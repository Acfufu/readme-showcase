from __future__ import annotations

import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import (
    canonical_json_bytes,
    canonical_sha256,
    write_canonical_json_atomic,
)
from tests import test_claim_coverage as claim_coverage


REPO_ROOT = Path(__file__).resolve().parents[1]
evaluate_generated_bundle = importlib.import_module(
    "skill.scripts.pipeline_core"
).evaluate_generated_bundle


def reversed_objects(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: reversed_objects(value[key])
            for key in reversed(list(value))
        }
    if isinstance(value, list):
        return [reversed_objects(item) for item in value]
    return value


class EvaluationTests(unittest.TestCase):
    def helper(self) -> claim_coverage.ClaimCoverageTests:
        return claim_coverage.ClaimCoverageTests(methodName="runTest")

    def test_integer_report_snapshot_and_object_order_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.helper().monolingual_bundle(root)

            first = evaluate_generated_bundle(bundle, root)
            second = evaluate_generated_bundle(reversed_objects(bundle), root)

            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first["status"], "pass")
            self.assertEqual(first["decision_basis"], "hard-gates-only")
            self.assertEqual(
                first["advisory"],
                {
                    "claim_coverage": {"covered": 3, "total": 3},
                    "diagram_label_coverage": {"covered": 0, "total": 0},
                    "evidence_sources": {"covered": 3, "total": 3},
                    "language_truth_pairs": {"covered": 0, "total": 0},
                    "observable_commands": {"covered": 0, "total": 0},
                    "section_intents": {"covered": 0, "total": 1},
                    "visual_provenance": {"covered": 1, "total": 1},
                },
            )
            for metric in first["advisory"].values():
                self.assertEqual(set(metric), {"covered", "total"})
                self.assertIs(type(metric["covered"]), int)
                self.assertIs(type(metric["total"]), int)

    def test_lower_optional_coverage_cannot_flip_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.helper().monolingual_bundle(root)
            plan_path = root / bundle["artifacts"]["plan"]["path"]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["sections"] = ["architecture", "overview"]
            write_canonical_json_atomic(plan_path, plan)
            bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)

            report = evaluate_generated_bundle(bundle, root)

            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                report["advisory"]["section_intents"],
                {"covered": 0, "total": 2},
            )

    def test_hard_failure_cannot_be_masked_by_advisory_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.helper().monolingual_bundle(root)
            readme_path = root / bundle["candidate"]["readme"]["path"]
            readme_path.write_text(
                readme_path.read_text(encoding="utf-8")
                + "\n[Broken](#missing)\n",
                encoding="utf-8",
            )
            bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(
                readme_path.read_bytes()
            ).hexdigest()

            report = evaluate_generated_bundle(bundle, root)

            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["hard_gate"]["status"], "fail")
            self.assertEqual(
                report["hard_gate"]["findings"][0]["code"],
                "E_README_AUDIT",
            )
            self.assertTrue(all(
                metric == {"covered": 0, "total": 0}
                for metric in report["advisory"].values()
            ))

    def test_cli_writes_equal_reports_and_returns_one_for_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.helper().monolingual_bundle(root)
            bundle_path = root / "generated-readme-bundle.json"
            write_canonical_json_atomic(bundle_path, bundle)
            outputs = [root / "evaluation-1.json", root / "evaluation-2.json"]
            for output in outputs:
                result = subprocess.run(
                    [
                        sys.executable,
                        "skill/scripts/readme_pipeline.py",
                        "evaluate",
                        "--bundle",
                        str(bundle_path),
                        "--output",
                        str(output),
                    ],
                    cwd=REPO_ROOT,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())

            readme_path = root / bundle["candidate"]["readme"]["path"]
            readme_path.write_text("# Broken\n\n[Bad](#missing)\n", encoding="utf-8")
            bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(
                readme_path.read_bytes()
            ).hexdigest()
            write_canonical_json_atomic(bundle_path, bundle)
            failed = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "evaluate",
                    "--bundle",
                    str(bundle_path),
                    "--output",
                    str(root / "evaluation-fail.json"),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(failed.returncode, 1, failed.stderr)
            self.assertEqual(json.loads(failed.stdout)["status"], "fail")


if __name__ == "__main__":
    unittest.main()
