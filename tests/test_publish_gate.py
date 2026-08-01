from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from tests import test_pr_bundle as pr_bundle


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
_CORE = importlib.import_module("skill.scripts.pipeline_core")
check_publish_gate = _CORE.check_publish_gate


class PublishGateTests(unittest.TestCase):
    def fixture(
        self,
        root: Path,
    ) -> tuple[Path, dict[str, Any], dict[str, Any], dict[str, Any]]:
        helper = pr_bundle.PrBundleTests(methodName="runTest")
        target, base_sha = helper.target(root)
        run_root = root / "run"
        run_root.mkdir()
        bundle, evaluation = helper.run_bundle(run_root, base_sha)
        write_canonical_json_atomic(
            run_root / "evaluation-report.json",
            evaluation,
        )
        pr = _CORE.build_pr_bundle(bundle, evaluation, run_root, target)
        candidate_hashes = [
            {
                "path": item["path"],
                "sha256": item["after_sha256"],
            }
            for item in [*pr["candidate_files"], *pr["semantic_sources"]]
        ]
        remote = {
            "schema_version": 1,
            "repository": pr["target"]["repository"],
            "base_sha": pr["target"]["base_sha"],
            "default_branch": "main",
            "proposed_branch": pr["target"]["branch"],
            "branch_exists": False,
            "branch_head_sha": None,
            "permissions": {
                "contents_write": True,
                "pull_requests_write": True,
            },
        }
        approval = {
            "schema_version": 1,
            "decision": "approve",
            "repository": pr["target"]["repository"],
            "base_sha": pr["target"]["base_sha"],
            "branch": pr["target"]["branch"],
            "fingerprint": pr["fingerprint"],
            "evaluation_sha256": pr["evaluation"]["report_sha256"],
            "candidate_hashes": candidate_hashes,
        }
        return target, pr, remote, approval

    def test_matching_preflight_and_approval_authorize_exact_connector_actions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, pr, remote, approval = self.fixture(root)
            branches_before = subprocess.check_output(
                ["git", "-C", str(target), "branch", "--format=%(refname)"],
                text=True,
            )

            first = check_publish_gate(pr, remote, approval, root / "run")
            second = check_publish_gate(pr, remote, approval, root / "run")

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "authorized")
            self.assertEqual(first["findings"], [])
            self.assertEqual(
                first["write_authority"]["connector_actions"],
                [
                    "create-branch",
                    "commit-files",
                    "push-branch",
                    "open-pull-request",
                ],
            )
            self.assertEqual(
                subprocess.check_output(
                    ["git", "-C", str(target), "branch", "--format=%(refname)"],
                    text=True,
                ),
                branches_before,
            )

    def test_every_bound_field_drift_removes_write_authority(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, pr, remote, approval = self.fixture(root)
            cases: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any]]] = {}
            for name in (
                "repository",
                "base_sha",
                "branch",
                "candidate",
                "evaluation",
                "fingerprint",
                "decision",
                "permission",
                "branch_exists",
            ):
                changed_pr = copy.deepcopy(pr)
                changed_remote = copy.deepcopy(remote)
                changed_approval = copy.deepcopy(approval)
                if name == "repository":
                    changed_remote["repository"] = "owner/other"
                elif name == "base_sha":
                    changed_remote["base_sha"] = "0" * 40
                elif name == "branch":
                    changed_remote["proposed_branch"] = "readme-showcase/other"
                elif name == "candidate":
                    changed_approval["candidate_hashes"][0]["sha256"] = "0" * 64
                elif name == "evaluation":
                    changed_approval["evaluation_sha256"] = "0" * 64
                elif name == "fingerprint":
                    changed_approval["fingerprint"] = "0" * 64
                elif name == "decision":
                    changed_approval["decision"] = "reject"
                elif name == "permission":
                    changed_remote["permissions"]["contents_write"] = False
                else:
                    changed_remote["branch_exists"] = True
                    changed_remote["branch_head_sha"] = "1" * 40
                cases[name] = (changed_pr, changed_remote, changed_approval)

            for name, values in cases.items():
                with self.subTest(name=name):
                    result = check_publish_gate(*values, root / "run")
                    self.assertEqual(result["status"], "fail")
                    self.assertIsNone(result["write_authority"])
                    self.assertTrue(result["findings"])

            changed_pr = copy.deepcopy(pr)
            changed_pr["candidate_files"][0]["after_sha256"] = "2" * 64
            projection = {
                key: value
                for key, value in changed_pr.items()
                if key not in {"fingerprint", "status"}
            }
            changed_pr["fingerprint"] = canonical_sha256(projection)
            byte_drift = check_publish_gate(
                changed_pr,
                remote,
                approval,
                root / "run",
            )
            self.assertEqual(byte_drift["status"], "fail")
            self.assertIsNone(byte_drift["write_authority"])
            self.assertIn("E_APPROVAL_CANDIDATES", byte_drift["findings"])

            candidate_path = root / "run" / pr["candidate_files"][0]["path"]
            candidate_before = candidate_path.read_bytes()
            candidate_path.write_bytes(candidate_before + b"drift")
            candidate_drift = check_publish_gate(
                pr,
                remote,
                approval,
                root / "run",
            )
            self.assertIn("E_CANDIDATE_DRIFT", candidate_drift["findings"])
            self.assertIsNone(candidate_drift["write_authority"])
            candidate_path.write_bytes(candidate_before)

            evaluation_path = root / "run/evaluation-report.json"
            evaluation_before = evaluation_path.read_bytes()
            evaluation_path.write_bytes(evaluation_before + b" ")
            evaluation_drift = check_publish_gate(
                pr,
                remote,
                approval,
                root / "run",
            )
            self.assertIn("E_EVALUATION_DRIFT", evaluation_drift["findings"])
            self.assertIsNone(evaluation_drift["write_authority"])
            evaluation_path.write_bytes(evaluation_before)

    def test_self_consistent_excluded_candidate_never_authorizes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, pr, remote, approval = self.fixture(root)
            candidate = pr["candidate_files"][0]
            original_path = candidate["path"]
            unsafe_path = ".github/workflows/release.yml"
            candidate["path"] = unsafe_path
            source = root / "run" / original_path
            destination = root / "run" / unsafe_path
            destination.parent.mkdir(parents=True)
            destination.write_bytes(source.read_bytes())
            projection = {
                key: value
                for key, value in pr.items()
                if key not in {"fingerprint", "status"}
            }
            pr["fingerprint"] = canonical_sha256(projection)
            approval["fingerprint"] = pr["fingerprint"]
            approval["candidate_hashes"][0]["path"] = unsafe_path

            with self.assertRaises(ContractError) as raised:
                check_publish_gate(pr, remote, approval, root / "run")

            self.assertEqual(raised.exception.code, "E_PR_PATH")

    def test_cli_authorizes_matching_input_and_rejects_secret_field_atomically(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, pr, remote, approval = self.fixture(root)
            run_root = root / "run"
            paths = {
                "pr": run_root / "pr-bundle.json",
                "remote": run_root / "remote-state.json",
                "approval": run_root / "approval-envelope.json",
            }
            for name, value in (
                ("pr", pr),
                ("remote", remote),
                ("approval", approval),
            ):
                write_canonical_json_atomic(paths[name], value)
            output = run_root / "publish-gate.json"
            result = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE),
                    "check-publish-gate",
                    "--pr-bundle",
                    str(paths["pr"]),
                    "--remote-state",
                    str(paths["remote"]),
                    "--approval",
                    str(paths["approval"]),
                    "--output",
                    str(output),
                ],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "authorized")

            approval["decision"] = "reject"
            write_canonical_json_atomic(paths["approval"], approval)
            drift_output = run_root / "drift-gate.json"
            drift = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE),
                    "check-publish-gate",
                    "--pr-bundle",
                    str(paths["pr"]),
                    "--remote-state",
                    str(paths["remote"]),
                    "--approval",
                    str(paths["approval"]),
                    "--output",
                    str(drift_output),
                ],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(drift.returncode, 1, drift.stderr)
            drift_report = json.loads(drift_output.read_text(encoding="utf-8"))
            self.assertEqual(drift_report["status"], "fail")
            self.assertIsNone(drift_report["write_authority"])
            approval["decision"] = "approve"
            write_canonical_json_atomic(paths["approval"], approval)

            remote["token"] = "must-never-be-read"
            write_canonical_json_atomic(paths["remote"], remote)
            rejected_output = run_root / "rejected-gate.json"
            rejected = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE),
                    "check-publish-gate",
                    "--pr-bundle",
                    str(paths["pr"]),
                    "--remote-state",
                    str(paths["remote"]),
                    "--approval",
                    str(paths["approval"]),
                    "--output",
                    str(rejected_output),
                ],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("E_SCHEMA_UNKNOWN_FIELD", rejected.stderr)
            self.assertNotIn("must-never-be-read", rejected.stderr)
            self.assertFalse(rejected_output.exists())


if __name__ == "__main__":
    unittest.main()
