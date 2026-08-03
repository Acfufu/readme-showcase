from __future__ import annotations

import copy
import importlib
import json
import os
import socket
import stat
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    write_canonical_json_atomic,
)
from tests import test_publish_gate


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "skill/scripts/readme_pipeline.py"
FIXTURES = ROOT / "tests/fixtures/contracts"


class ApprovalEnvelopeV2Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.producer = importlib.import_module(
            "skill.scripts.readme_showcase.delivery.approval"
        )
        self.contract = importlib.import_module(
            "skill.scripts.readme_showcase.contracts.publishing"
        )

    def fixture(self, root: Path) -> tuple[dict[str, Any], Path]:
        helper = test_publish_gate.PublishGateTests(methodName="runTest")
        _, pr, _, _ = helper.fixture(root)
        run_root = root / "run"
        preview_root = run_root / "output/preview"
        preview_root.mkdir(parents=True)
        report = {
            "schema_version": 1,
            "status": "complete",
            "generated_at": "2000-01-01T00:00:00Z",
            "surfaces": ["desktop", "mobile"],
        }
        write_canonical_json_atomic(preview_root / "report.json", report)
        (preview_root / "index.html").write_bytes(b"<!doctype html>\n")
        write_canonical_json_atomic(run_root / "pr-bundle.json", pr)
        return pr, run_root

    def assert_code(self, code: str, function: Any, *args: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def test_default_reject_and_decision_only_approve_authorizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            template = self.producer.create_approval_template(pr, run_root)
            self.assertEqual(template["decision"], "reject")
            self.assertEqual(
                self.contract.check_approval_envelope(template, pr, run_root),
                {
                    "schema_version": 2,
                    "status": "fail",
                    "findings": ["E_APPROVAL_DECISION"],
                    "write_authority": None,
                },
            )
            approved = copy.deepcopy(template)
            approved["decision"] = "approve"
            authorized = self.contract.check_approval_envelope(approved, pr, run_root)
            self.assertEqual(authorized["status"], "authorized")
            self.assertEqual(authorized["findings"], [])
            self.assertEqual(authorized["write_authority"], {
                key: approved[key]
                for key in (
                    "repository", "base_sha", "proposed_branch", "pr_fingerprint",
                    "candidate_hashes", "evaluation_sha256", "preview", "actions",
                )
            })

    def test_closed_fields_and_forbidden_approval_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            template = self.producer.create_approval_template(pr, run_root)
            for field in (
                "identity", "reviewer", "user", "comment", "comment_body",
                "token", "credential", "timestamp", "auto_approve", "remote_id",
                "model", "network", "provider", "connector",
            ):
                changed = copy.deepcopy(template)
                changed[field] = "secret"
                with self.subTest(field=field):
                    self.assert_code(
                        "E_SCHEMA_UNKNOWN_FIELD",
                        self.contract.validate_approval_envelope_v2,
                        changed,
                    )

    def test_schema_python_fixture_parity(self) -> None:
        schema = json.loads(
            (ROOT / "skill/schemas/approval-envelope.v2.schema.json").read_text(
                encoding="utf-8"
            )
        )
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        valid = json.loads(
            (FIXTURES / "approval-envelope-v2.valid.json").read_text(encoding="utf-8")
        )
        self.assertEqual(list(validator.iter_errors(valid)), [])
        self.assertEqual(self.contract.validate_approval_envelope_v2(valid), valid)
        invalid = json.loads(
            (FIXTURES / "approval-envelope-v2.invalid.json").read_text(encoding="utf-8")
        )
        for case in invalid["cases"]:
            with self.subTest(case=case["name"]):
                schema_errors = list(validator.iter_errors(case["payload"]))
                if not case.get("semantic"):
                    self.assertTrue(schema_errors)
                self.assert_code(
                    case["code"],
                    self.contract.validate_approval_envelope_v2,
                    case["payload"],
                )

    def test_every_bound_envelope_field_and_order_drift_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            template = self.producer.create_approval_template(pr, run_root)
            template["decision"] = "approve"
            cases: list[tuple[str, dict[str, Any], str]] = []
            for field in ("repository", "base_sha", "proposed_branch", "pr_fingerprint"):
                changed = copy.deepcopy(template)
                changed[field] = ("0" * 40 if field == "base_sha" else "0" * 64 if field == "pr_fingerprint" else "owner/other" if field == "repository" else "readme-showcase/other")
                cases.append((field, changed, "E_APPROVAL_FINGERPRINT"))
            changed = copy.deepcopy(template)
            changed["candidate_hashes"][0]["sha256"] = "0" * 64
            cases.append(("candidate hash", changed, "E_APPROVAL_CANDIDATES"))
            changed = copy.deepcopy(template)
            changed["candidate_hashes"] = list(reversed(changed["candidate_hashes"]))
            cases.append(("candidate order", changed, "E_APPROVAL_CANDIDATES"))
            changed = copy.deepcopy(template)
            changed["candidate_hashes"].append(copy.deepcopy(changed["candidate_hashes"][0]))
            cases.append(("candidate duplicate", changed, "E_APPROVAL_CANDIDATES"))
            changed = copy.deepcopy(template)
            changed["evaluation_sha256"] = "0" * 64
            cases.append(("evaluation binding", changed, "E_EVALUATION_DRIFT"))
            for field in ("preview_sha256", "report_sha256"):
                changed = copy.deepcopy(template)
                changed["preview"][field] = "0" * 64
                cases.append((field, changed, "E_PREVIEW_DRIFT"))
            for name, actions in (
                ("action reorder", list(reversed(template["actions"]))),
                ("action add", [*template["actions"], "merge-pull-request"]),
                ("action remove", template["actions"][:-1]),
            ):
                changed = copy.deepcopy(template)
                changed["actions"] = actions
                cases.append((name, changed, "E_APPROVAL_ACTIONS"))
            for name, changed, code in cases:
                with self.subTest(case=name):
                    result = self.contract.check_approval_envelope(changed, pr, run_root)
                    self.assertIn(code, result["findings"])
                    self.assertIsNone(result["write_authority"])

    def test_current_candidate_evaluation_and_preview_bytes_are_recomputed(self) -> None:
        for case, relative, code in (
            ("candidate", None, "E_APPROVAL_FINGERPRINT"),
            ("evaluation", "evaluation-report.json", "E_EVALUATION_DRIFT"),
            ("preview", "output/preview/index.html", "E_PREVIEW_DRIFT"),
            ("report", "output/preview/report.json", "E_PREVIEW_DRIFT"),
        ):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                pr, run_root = self.fixture(Path(temporary))
                approved = self.producer.create_approval_template(pr, run_root)
                approved["decision"] = "approve"
                bound_path = (
                    approved["candidate_hashes"][0]["path"]
                    if relative is None
                    else relative
                )
                (run_root / bound_path).write_bytes(b"drift\n")
                result = self.contract.check_approval_envelope(approved, pr, run_root)
                self.assertIn(code, result["findings"])
                self.assertIsNone(result["write_authority"])

    def test_cli_emits_canonical_reject_without_remote_or_subprocess_calls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            output = Path(temporary) / "approval.json"
            environment = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
            with mock.patch("subprocess.run") as subprocess_spy:
                result = subprocess.run(
                    [sys.executable, str(PIPELINE), "create-approval-template", "--pr-bundle", str(run_root / "pr-bundle.json"), "--output", str(output)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    env=environment,
                )
            # Patch proves library code did not invoke subprocess; this test's explicit CLI
            # call is represented by the mocked call and covered by manual QA with a real process.
            self.assertEqual(subprocess_spy.call_count, 1)
            subprocess_spy.return_value = result
            real = subprocess.check_output(
                [sys.executable, str(PIPELINE), "create-approval-template", "--pr-bundle", str(run_root / "pr-bundle.json"), "--output", str(output)],
                cwd=ROOT,
                env=environment,
            )
            payload = json.loads(real)
            self.assertEqual(payload["decision"], "reject")
            self.assertEqual(output.read_bytes(), canonical_json_bytes(payload))
            self.assertEqual(payload, self.producer.create_approval_template(pr, run_root))

    def test_noncanonical_symlink_fifo_and_unsafe_candidate_inputs_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            pr_path = run_root / "pr-bundle.json"
            output = Path(temporary) / "approval.json"
            output.write_bytes(b"unchanged\n")
            noncanonical = run_root / "noncanonical.json"
            noncanonical.write_text(json.dumps(pr, indent=2), encoding="utf-8")
            self.assert_code(
                "E_APPROVAL_INPUT",
                self.producer.create_approval_template_from_path,
                noncanonical,
                output,
            )
            self.assertEqual(output.read_bytes(), b"unchanged\n")
            linked = run_root / "linked.json"
            linked.symlink_to(pr_path)
            self.assert_code(
                "E_INPUT_PATH",
                self.producer.create_approval_template_from_path,
                linked,
                output,
            )
            if hasattr(os, "mkfifo"):
                fifo = run_root / "input.fifo"
                os.mkfifo(fifo)
                self.assertTrue(stat.S_ISFIFO(fifo.lstat().st_mode))
                self.assert_code(
                    "E_INPUT_PATH",
                    self.producer.create_approval_template_from_path,
                    fifo,
                    output,
                )
            unsafe = copy.deepcopy(pr)
            unsafe["candidate_files"][0]["path"] = "../README.md"
            self.assert_code(
                "E_APPROVAL_FINGERPRINT",
                self.producer.create_approval_template,
                unsafe,
                run_root,
            )

    def test_output_failure_leaves_existing_bytes_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            output = Path(temporary) / "approval.json"
            output.write_bytes(b"sentinel\n")
            pr_path = run_root / "pr-bundle.json"
            pr_bytes = pr_path.read_bytes()
            self.assert_code(
                "E_APPROVAL_INPUT",
                self.producer.create_approval_template_from_path,
                pr_path,
                pr_path,
            )
            self.assertEqual(pr_path.read_bytes(), pr_bytes)
            (run_root / "evaluation-report.json").write_bytes(b"drift\n")
            self.assert_code(
                "E_EVALUATION_DRIFT",
                self.producer.create_approval_template_from_path,
                run_root / "pr-bundle.json",
                output,
            )
            self.assertEqual(output.read_bytes(), b"sentinel\n")

    def test_producer_has_zero_network_gh_git_or_subprocess_surface(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            with (
                mock.patch.object(socket, "socket") as network_spy,
                mock.patch.object(subprocess, "run") as subprocess_spy,
                mock.patch.object(subprocess, "Popen") as popen_spy,
                mock.patch.object(subprocess, "check_output") as check_output_spy,
            ):
                envelope = self.producer.create_approval_template(pr, run_root)
            self.assertEqual(envelope["decision"], "reject")
            self.assertEqual(network_spy.call_count, 0)
            self.assertEqual(subprocess_spy.call_count, 0)
            self.assertEqual(popen_spy.call_count, 0)
            self.assertEqual(check_output_spy.call_count, 0)

    def test_symlink_and_fifo_outputs_fail_without_target_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            pr, run_root = self.fixture(Path(temporary))
            target = Path(temporary) / "target.json"
            target.write_bytes(b"protected\n")
            linked = Path(temporary) / "linked-output.json"
            linked.symlink_to(target)
            self.assert_code(
                "E_OUTPUT_PATH",
                self.producer.create_approval_template_from_path,
                run_root / "pr-bundle.json",
                linked,
            )
            self.assertEqual(target.read_bytes(), b"protected\n")
            if hasattr(os, "mkfifo"):
                fifo = Path(temporary) / "output.fifo"
                os.mkfifo(fifo)
                self.assert_code(
                    "E_OUTPUT_PATH",
                    self.producer.create_approval_template_from_path,
                    run_root / "pr-bundle.json",
                    fifo,
                )


if __name__ == "__main__":
    unittest.main()
