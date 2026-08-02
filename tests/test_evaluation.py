from __future__ import annotations

import copy
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
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.generation.assembler import assemble_generated_bundle
from tests import test_claim_coverage as claim_coverage


REPO_ROOT = Path(__file__).resolve().parents[1]
evaluate_generated_bundle = importlib.import_module(
    "skill.scripts.pipeline_core"
).evaluate_generated_bundle
build_pr_bundle = importlib.import_module(
    "skill.scripts.pipeline_core"
).build_pr_bundle


def reversed_objects(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: reversed_objects(value[key])
            for key in reversed(list(value))
        }
    if isinstance(value, list):
        return [reversed_objects(item) for item in value]
    return value


def _walk(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value, *[child for item in value.values() for child in _walk(item)]]
    if isinstance(value, list):
        return [value, *[child for item in value for child in _walk(item)]]
    return [value]


class EvaluationTests(unittest.TestCase):
    def helper(self) -> claim_coverage.ClaimCoverageTests:
        return claim_coverage.ClaimCoverageTests(methodName="runTest")

    def v2_bundle(
        self,
        root: Path,
        quality: str,
        *,
        repository: str = "owner/repo",
        base_sha: str = "a" * 40,
        readme_name: str = "README.generated.md",
        asset_name: str = "assets/hero.png",
    ) -> dict[str, Any]:
        source = b"source evidence\n"
        asset_raw = b"asset bytes\n"
        readme_raw = b"# Overview\n"
        (root / "source").mkdir()
        (root / "source" / "README.md").write_bytes(source)
        (root / asset_name).parent.mkdir(parents=True)
        (root / asset_name).write_bytes(asset_raw)
        (root / readme_name).write_bytes(readme_raw)
        fact = build_fact(
            kind="file-presence", path="source/README.md", locator=None,
            semantic_key="presence", value=True, source_bytes=source,
        )
        graph = EvidenceGraph([fact]).to_dict()
        fact_id = fact["fact_id"]
        plan = {
            "schema_version": 2, "mode": "readme", "languages": ["en"],
            "sections": ["overview"], "visual_intent": "hero",
            "diagram_route": "static", "commands": ["python -m demo"],
            "evidence_ids": [fact_id],
        }
        claims = {
            "schema_version": 2,
            "markdown_blocks": [{
                "claim_id": "markdown:en:overview", "content_sha256": "0" * 64,
                "claim_kind": "factual", "evidence_ids": [fact_id],
                "language_pair_id": None,
                "support_level": "documented-only" if quality == "low" else "direct",
            }],
            "diagram_labels": [],
        }
        if quality == "high":
            claims["diagram_labels"] = [{
                **copy.deepcopy(claims["markdown_blocks"][0]),
                "claim_id": "diagram:en:hero", "content_sha256": "f" * 64,
            }]
        assets = {
            "schema_version": 2,
            "assets": [{
                "asset_id": "hero", "path": asset_name, "locale": "en",
                "provenance": {
                    "kind": "derived", "path": "source/README.md",
                    "sha256": hashlib.sha256(source).hexdigest(),
                },
                "artifact_sha256": hashlib.sha256(asset_raw).hexdigest(),
                "candidate_sha256": hashlib.sha256(asset_raw).hexdigest(),
                "evidence_ids": [fact_id],
            }],
        }
        retrieval = {
            "schema_version": 1,
            "status": "available" if quality == "high" else "unavailable",
            "records": ([{"section_intents": ["overview"]}] if quality == "high" else []),
        }
        candidate = {
            "readme": {"path": readme_name, "sha256": hashlib.sha256(readme_raw).hexdigest()},
            "assets": [{"path": asset_name, "sha256": hashlib.sha256(asset_raw).hexdigest()}],
        }
        evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": canonical_sha256(candidate)}
        values = {
            "plan": plan, "retrieval": retrieval, "evidence": graph,
            "claim_map": claims, "asset_manifest": assets, "evaluation": evaluation,
        }
        paths = {
            "plan": "readme-plan.json", "retrieval": "retrieval-packet.json",
            "evidence": "repository-evidence.json", "claim_map": "claim-map.json",
            "asset_manifest": "asset-manifest.json", "evaluation": "evaluation.json",
        }
        artifacts = {}
        for name, value in values.items():
            write_canonical_json_atomic(root / paths[name], value)
            artifacts[name] = {"path": paths[name], "sha256": canonical_sha256(value)}
        return assemble_generated_bundle(
            root, mode="readme",
            target={"repository": repository, "base_sha": base_sha},
            candidate=candidate, artifacts=artifacts,
        )

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

    def test_v2_low_and_high_metrics_are_exact_distinct_and_advisory(self) -> None:
        reports = {}
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for quality in ("low", "high"):
                root = base / quality
                root.mkdir()
                bundle = self.v2_bundle(root, quality)
                reports[quality] = evaluate_generated_bundle(bundle, root)
            low, high = reports["low"], reports["high"]
            self.assertEqual(low["status"], "pass")
            self.assertEqual(low["hard_gate"], {"status": "pass", "findings": []})
            self.assertEqual(low["advisory"]["claim_coverage"], {
                "basis_points": 0, "covered": 0,
                "reasons": ["claim-unverified:markdown:en:overview"],
                "status": "measured", "total": 1,
            })
            self.assertEqual(low["advisory"]["visual_provenance"], {
                "basis_points": 0, "covered": 0,
                "reasons": ["visual-missing-claim-binding:hero"],
                "status": "measured", "total": 1,
            })
            self.assertEqual(high["advisory"]["claim_coverage"]["covered"], 2)
            self.assertEqual(high["advisory"]["diagram_label_coverage"]["covered"], 1)
            self.assertEqual(high["advisory"]["visual_provenance"]["covered"], 1)
            self.assertEqual(high["advisory"]["observable_commands"]["covered"], 0)
            self.assertNotEqual(canonical_sha256(low), canonical_sha256(high))
            self.assertFalse(any(isinstance(value, float) for report in reports.values() for value in _walk(report)))

    def test_v1_snapshot_bytes_remain_pinned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.helper().monolingual_bundle(root)
            report = evaluate_generated_bundle(bundle, root)
            self.assertEqual(
                hashlib.sha256(canonical_json_bytes(report)).hexdigest(),
                "323de83bbb43fb68822a08ffb247406f226af01a040c58a9c6c92e1529c73052",
            )

    def test_v2_evaluate_to_pr_bundle_uses_hard_gate_not_advisory_score(self) -> None:
        from tests.test_pr_bundle import PrBundleTests

        helper = PrBundleTests(methodName="runTest")
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, _ = helper.target(base)
            (target / "source").mkdir()
            (target / "source" / "README.md").write_bytes(b"source evidence\n")
            helper.git(target, "add", "source/README.md")
            helper.git(target, "commit", "-m", "add evidence source")
            base_sha = helper.git(target, "rev-parse", "HEAD")
            ready = {}
            reports = {}
            bundles = {}
            for quality in ("low", "high"):
                run_root = base / f"run-{quality}"
                run_root.mkdir()
                bundle = self.v2_bundle(
                    run_root,
                    quality,
                    repository="owner/target",
                    base_sha=base_sha,
                    readme_name="README.md",
                    asset_name="assets/readme/hero.png",
                )
                report = evaluate_generated_bundle(bundle, run_root)
                bundles[quality] = (bundle, run_root)
                reports[quality] = report
                ready[quality] = build_pr_bundle(bundle, report, run_root, target)
            self.assertEqual(ready["low"]["status"], "ready")
            self.assertEqual(ready["high"]["status"], "ready")
            self.assertNotIn("write_authority", ready["low"])
            self.assertNotIn("write_authority", ready["high"])
            self.assertNotEqual(
                reports["low"]["advisory"],
                reports["high"]["advisory"],
            )

            malformed = copy.deepcopy(reports["low"])
            malformed["advisory"]["claim_coverage"]["covered"] = 2
            bundle, run_root = bundles["low"]
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, malformed, run_root, target)
            self.assertEqual(raised.exception.code, "E_EVALUATION_METRIC")

            crafted = copy.deepcopy(reports["low"])
            crafted["advisory"]["claim_coverage"] = {
                "basis_points": 10000,
                "covered": 1,
                "reasons": [],
                "status": "measured",
                "total": 1,
            }
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, crafted, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_EVALUATION")

            hard_fail = copy.deepcopy(reports["low"])
            hard_fail["status"] = "fail"
            hard_fail["hard_gate"] = {
                "status": "fail",
                "findings": [{"code": "E_README_AUDIT", "message": "failed"}],
            }
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, hard_fail, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_EVALUATION")

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
