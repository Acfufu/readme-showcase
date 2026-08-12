from __future__ import annotations

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
from skill.scripts.readme_showcase.evaluation.incident_log import (
    append_failure_entry,
    record_identity_override,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
_CORE = importlib.import_module("skill.scripts.pipeline_core")

OVERRIDE = {"reason": "brand refresh approved by design lead", "approved_by": "alice@example.com"}

FAILING_REPORT: dict[str, object] = {
    "schema_version": 3,
    "status": "fail",
    "hard_gate": {
        "status": "fail",
        "findings": [
            {"code": "E_SELF_REVIEW", "message": "self-review: 1 claim(s) without evidence correspondence: markdown:en:alpha"},
            {"code": "E_VOICE_MATCH", "message": "voice mismatch: candidate wording diverges from repository evidence"},
        ],
    },
}


def _entry(gate: str, root_cause: str, fix_hint: str) -> dict[str, str]:
    return {
        "kind": "failure",
        "gate": gate,
        "root_cause": root_cause,
        "fix_hint": fix_hint,
        "report_sha256": canonical_sha256(FAILING_REPORT),
    }


class AppendFailureEntryTests(unittest.TestCase):
    """A failed evaluation appends a lessons-pending entry with gate, root
    cause, and a fix hint into the attempt directory."""

    def _write(self, root: Path) -> None:
        append_failure_entry(FAILING_REPORT, root)

    def _read(self, root: Path) -> dict[str, Any]:
        return json.loads((root / "lessons-pending.json").read_text(encoding="utf-8"))

    def test_failing_report_writes_lesson_pending_with_gate_cause_and_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            append_failure_entry(FAILING_REPORT, root)
            payload = self._read(root)
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(
                payload["entries"],
                [
                    _entry(
                        "E_SELF_REVIEW",
                        "self-review: 1 claim(s) without evidence correspondence: markdown:en:alpha",
                        "bind every candidate claim to a repository-evidence fact, then re-run evaluation",
                    ),
                    _entry(
                        "E_VOICE_MATCH",
                        "voice mismatch: candidate wording diverges from repository evidence",
                        "align candidate wording with repository evidence, then re-run evaluation",
                    ),
                ],
            )

    def test_unknown_gate_gets_generic_fix_hint(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = {
                "schema_version": 3,
                "status": "fail",
                "hard_gate": {
                    "status": "fail",
                    "findings": [{"code": "E_UNKNOWN_GATE", "message": "mystery failure"}],
                },
            }
            append_failure_entry(report, root)
            entries = self._read(root)["entries"]
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["gate"], "E_UNKNOWN_GATE")
            self.assertEqual(
                entries[0]["fix_hint"],
                "address the finding, then re-run evaluation",
            )

    def test_passing_report_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            append_failure_entry({"schema_version": 3, "status": "pass", "hard_gate": {"status": "pass", "findings": []}}, root)
            self.assertFalse((root / "lessons-pending.json").exists())

    def test_failing_report_without_findings_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            append_failure_entry({"schema_version": 3, "status": "fail", "hard_gate": {"status": "fail", "findings": []}}, root)
            self.assertFalse((root / "lessons-pending.json").exists())

    def test_append_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            append_failure_entry(FAILING_REPORT, root)
            append_failure_entry(FAILING_REPORT, root)
            self.assertEqual(len(self._read(root)["entries"]), 2)

    def test_append_preserves_existing_entries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            seed = {
                "schema_version": 1,
                "entries": [
                    {
                        "kind": "override",
                        "gate": "E_IDENTITY_MATCH",
                        "reason": "legacy override",
                        "approved_by": "carol@example.com",
                        "report_sha256": "0" * 64,
                    }
                ],
            }
            write_canonical_json_atomic(root / "lessons-pending.json", seed)
            append_failure_entry(FAILING_REPORT, root)
            payload = self._read(root)
            self.assertEqual(len(payload["entries"]), 3)
            self.assertEqual(payload["entries"][0], seed["entries"][0])

    def test_malformed_existing_log_raises_contract_error(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "lessons-pending.json").write_text("{not json", encoding="utf-8")
            with self.assertRaises(ContractError) as caught:
                append_failure_entry(FAILING_REPORT, root)
            self.assertEqual(caught.exception.code, "E_INCIDENT_LOG")


class RecordIdentityOverrideTests(unittest.TestCase):
    """An identity override in the evaluation report is recorded as an
    independent non-failure lessons-pending entry."""

    def test_override_is_recorded_as_independent_entry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = {"schema_version": 3, "status": "pass", "identity_override": OVERRIDE}
            record_identity_override(report, root)
            payload = json.loads((root / "lessons-pending.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(
                payload["entries"],
                [
                    {
                        "kind": "override",
                        "gate": "E_IDENTITY_MATCH",
                        "reason": OVERRIDE["reason"],
                        "approved_by": OVERRIDE["approved_by"],
                        "report_sha256": canonical_sha256(report),
                    }
                ],
            )

    def test_null_override_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_identity_override({"schema_version": 3, "status": "pass", "identity_override": None}, root)
            self.assertFalse((root / "lessons-pending.json").exists())

    def test_non_override_report_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record_identity_override({"schema_version": 1, "status": "pass"}, root)
            self.assertFalse((root / "lessons-pending.json").exists())


class IncidentLogCliTests(unittest.TestCase):
    """CLI wiring: evaluate failures and publish-gate overrides land in
    lessons-pending.json next to the evaluation report."""

    def run_cli(self, *arguments: str, cwd: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_cli_evaluate_failure_writes_lesson_pending(self) -> None:
        from tests import test_claim_coverage as claim_coverage

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = claim_coverage.ClaimCoverageTests(methodName="runTest")
            bundle = helper.monolingual_bundle(root)
            readme_path = root / bundle["candidate"]["readme"]["path"]
            readme_path.write_text("# Broken\n\n[Bad](#missing)\n", encoding="utf-8")
            import hashlib

            bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(
                readme_path.read_bytes()
            ).hexdigest()
            bundle_path = root / "generated-readme-bundle.json"
            write_canonical_json_atomic(bundle_path, bundle)
            output = root / "evaluation-report.json"

            result = self.run_cli(
                "evaluate",
                "--bundle",
                str(bundle_path),
                "--output",
                str(output),
                cwd=REPO_ROOT,
            )

            self.assertEqual(result.returncode, 1, result.stderr)
            payload = json.loads((root / "lessons-pending.json").read_text(encoding="utf-8"))
            self.assertEqual(payload["schema_version"], 1)
            self.assertEqual(len(payload["entries"]), 1)
            entry = payload["entries"][0]
            self.assertEqual(entry["kind"], "failure")
            self.assertEqual(entry["gate"], "E_README_AUDIT")
            self.assertTrue(entry["root_cause"])
            self.assertEqual(
                entry["fix_hint"],
                "fix the README audit findings, then re-run evaluation",
            )
            self.assertEqual(entry["report_sha256"], canonical_sha256(json.loads(output.read_text(encoding="utf-8"))))

    def test_cli_check_publish_gate_records_identity_override(self) -> None:
        from tests.test_pr_bundle import PrBundleTests

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = PrBundleTests(methodName="runTest")
            target, base_sha, run_root, bundle, evaluation = helper.run_compiled_bundle(root)
            evaluation["identity_override"] = OVERRIDE
            write_canonical_json_atomic(run_root / "evaluation-report.json", evaluation)
            pr = _CORE.build_pr_bundle(bundle, evaluation, run_root, target)
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
                "candidate_hashes": [
                    {
                        "path": item["path"],
                        "sha256": item["after_sha256"],
                    }
                    for item in [*pr["candidate_files"], *pr["semantic_sources"]]
                ],
            }
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

            result = self.run_cli(
                "check-publish-gate",
                "--pr-bundle",
                str(paths["pr"]),
                "--remote-state",
                str(paths["remote"]),
                "--approval",
                str(paths["approval"]),
                "--output",
                str(output),
                cwd=target,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "authorized")
            payload = json.loads((run_root / "lessons-pending.json").read_text(encoding="utf-8"))
            self.assertEqual(
                payload["entries"],
                [
                    {
                        "kind": "override",
                        "gate": "E_IDENTITY_MATCH",
                        "reason": OVERRIDE["reason"],
                        "approved_by": OVERRIDE["approved_by"],
                        "report_sha256": canonical_sha256(evaluation),
                    }
                ],
            )

    def test_cli_check_publish_gate_without_override_writes_nothing(self) -> None:
        from tests.test_pr_bundle import PrBundleTests

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            helper = PrBundleTests(methodName="runTest")
            target, base_sha, run_root, bundle, evaluation = helper.run_compiled_bundle(root)
            write_canonical_json_atomic(run_root / "evaluation-report.json", evaluation)
            pr = _CORE.build_pr_bundle(bundle, evaluation, run_root, target)
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
                "candidate_hashes": [
                    {
                        "path": item["path"],
                        "sha256": item["after_sha256"],
                    }
                    for item in [*pr["candidate_files"], *pr["semantic_sources"]]
                ],
            }
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

            result = self.run_cli(
                "check-publish-gate",
                "--pr-bundle",
                str(paths["pr"]),
                "--remote-state",
                str(paths["remote"]),
                "--approval",
                str(paths["approval"]),
                "--output",
                str(output),
                cwd=target,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((run_root / "lessons-pending.json").exists())


if __name__ == "__main__":
    unittest.main()
