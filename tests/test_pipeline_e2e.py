from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.readme_showcase.orchestration import runner as runner_module
from skill.scripts.readme_showcase.orchestration import stages as stages_module
from skill.scripts.readme_showcase.orchestration.logging import StageLogger
from tests import test_bundle_contracts as bundle_contracts
from tests import test_elk_adapter as elk_adapter
from tests import test_pr_bundle as pr_bundle
from tests import test_pipeline_contracts as pipeline_contracts


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
DATASET = REPO_ROOT / "dataset/retrieval/manifest.json"


class OfflinePipelineE2ETests(unittest.TestCase):
    def cli(
        self,
        *arguments: str,
        cwd: Path = REPO_ROOT,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=cwd,
            capture_output=True,
            text=True,
            check=False,
        )

    def target(self, root: Path) -> tuple[Path, str]:
        return pr_bundle.PrBundleTests(methodName="runTest").target(root)

    def prepare(
        self,
        run_root: Path,
        target: Path,
        base_sha: str,
        *,
        mode: str,
        elk: bool = False,
        diagram_route: str | None = None,
        engine_artifacts: tuple[Path, Path, Path] | None = None,
    ) -> tuple[Path, Path, dict[str, Any]]:
        helper = bundle_contracts.BundleContractTests(methodName="runTest")
        bundle, _ = helper.make_bundle(
            run_root,
            mode,
            elk=elk,
            diagram_route=diagram_route,
        )
        if bundle["candidate"]["readme"] is not None:
            source = run_root / bundle["candidate"]["readme"]["path"]
            destination = run_root / "README.md"
            source.rename(destination)
            bundle["candidate"]["readme"] = {
                "path": "README.md",
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }

        evidence_path = run_root / "repository-evidence.json"
        scan = self.cli(
            "scan",
            "--root",
            str(target),
            "--output",
            str(evidence_path),
        )
        self.assertEqual(scan.returncode, 0, scan.stderr)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        self.assertEqual(evidence["target"]["base_sha"], base_sha)
        fact = next(
            item
            for item in evidence["facts"]
            if item["fact_id"] == "file:README.md"
        )

        retrieval_path = run_root / "retrieval-packet.json"
        retrieval = self.cli(
            "retrieve",
            "--evidence",
            str(evidence_path),
            "--manifest",
            str(DATASET),
            "--project-type",
            "developer-tool",
            "--section",
            "overview",
            "--tag",
            "workflow",
            "--mode",
            "production",
            "--output",
            str(retrieval_path),
        )
        self.assertEqual(retrieval.returncode, 0, retrieval.stderr)
        retrieval_payload = json.loads(retrieval_path.read_text(encoding="utf-8"))
        bundle["artifacts"]["retrieval"] = {
            "path": "retrieval-packet.json",
            "sha256": canonical_sha256(retrieval_payload),
        }

        plan_path = run_root / bundle["artifacts"]["plan"]["path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["evidence_ids"] = ["file:README.md"]
        plan["sections"] = ["overview"]
        write_canonical_json_atomic(plan_path, plan)
        bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)

        claims_path = run_root / bundle["artifacts"]["claim_map"]["path"]
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        for collection in ("markdown_blocks", "diagram_labels"):
            for claim in claims[collection]:
                claim["truth_id"] = "file:README.md"
                claim["evidence_sha256"] = fact["evidence_sha256"]
        write_canonical_json_atomic(claims_path, claims)
        bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)

        manifest_path = run_root / bundle["artifacts"]["asset_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset in manifest["assets"]:
            asset["truth_ids"] = ["file:README.md"]
        if engine_artifacts is not None:
            semantic_source, raw_source, metadata_source = engine_artifacts
            semantic_destination = run_root / "assets/readme/diagram.diagram.json"
            raw_destination = run_root / "assets/readme/diagram.svg"
            metadata_destination = run_root / "assets/readme/diagram.engine.json"
            semantic_destination.write_bytes(semantic_source.read_bytes())
            raw_destination.write_bytes(raw_source.read_bytes())
            metadata_destination.write_bytes(metadata_source.read_bytes())
            semantic_sha256 = hashlib.sha256(
                semantic_destination.read_bytes()
            ).hexdigest()
            raw_sha256 = hashlib.sha256(raw_destination.read_bytes()).hexdigest()
            metadata_sha256 = hashlib.sha256(
                metadata_destination.read_bytes()
            ).hexdigest()
            manifest["assets"][0]["sha256"] = raw_sha256
            manifest["assets"][0]["semantic"]["sha256"] = semantic_sha256
            manifest["assets"][0]["engine_metadata"]["sha256"] = metadata_sha256
            bundle["candidate"]["assets"][0]["sha256"] = raw_sha256
        write_canonical_json_atomic(manifest_path, manifest)
        bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)

        bundle["target"] = {
            "repository": "owner/target",
            "base_sha": base_sha,
        }
        bundle_path = run_root / "generated-readme-bundle.json"
        write_canonical_json_atomic(bundle_path, bundle)
        validated = self.cli("validate-bundle", "--bundle", str(bundle_path))
        self.assertEqual(validated.returncode, 0, validated.stderr)

        evaluation_path = run_root / "evaluation-report.json"
        evaluated = self.cli(
            "evaluate",
            "--bundle",
            str(bundle_path),
            "--output",
            str(evaluation_path),
        )
        self.assertEqual(evaluated.returncode, 0, evaluated.stderr)
        return bundle_path, evaluation_path, bundle

    def build(
        self,
        target: Path,
        bundle: Path,
        evaluation: Path,
        output: Path,
    ) -> subprocess.CompletedProcess[str]:
        return self.cli(
            "build-pr-bundle",
            "--bundle",
            str(bundle),
            "--evaluation",
            str(evaluation),
            "--output",
            str(output),
            cwd=target,
        )

    def test_legacy_none_static_and_elk_routes_remain_operational(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            for route in ("none", "static", "elk"):
                with self.subTest(route=route):
                    run_root = base / f"route-{route}"
                    run_root.mkdir()
                    bundle_path, evaluation_path, bundle = self.prepare(
                        run_root,
                        target,
                        base_sha,
                        mode="asset-only",
                        elk=route == "elk",
                        diagram_route=route,
                    )
                    plan_path = run_root / bundle["artifacts"]["plan"]["path"]
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                    self.assertEqual(plan["schema_version"], 1)
                    self.assertEqual(plan["diagram_route"], route)
                    self.assertEqual(bundle["schema_version"], 1)
                    result = self.build(
                        target,
                        bundle_path,
                        evaluation_path,
                        run_root / "pr.json",
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)

    def test_static_scan_retrieve_evaluate_and_pr_bundle_repeat(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, _ = self.target(base)
            (target / ".gitignore").write_text(
                "evaluation-only/\n",
                encoding="utf-8",
            )
            git = pr_bundle.PrBundleTests(methodName="runTest")
            git.git(target, "add", ".gitignore")
            git.git(target, "commit", "-m", "ignore evaluation corpus")
            base_sha = git.git(target, "rev-parse", "HEAD")
            evaluation_only = target / "evaluation-only"
            evaluation_only.mkdir()
            (evaluation_only / "gold.txt").write_text(
                "GOLD-SENTINEL\n",
                encoding="utf-8",
            )
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation, _ = self.prepare(
                run_root,
                target,
                base_sha,
                mode="readme",
            )
            evidence_text = (run_root / "repository-evidence.json").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("GOLD-SENTINEL", evidence_text)
            index_before = (target / ".git/index").read_bytes()
            outputs = [run_root / "pr-1.json", run_root / "pr-2.json"]

            results = [
                self.build(target, bundle, evaluation, output)
                for output in outputs
            ]

            self.assertEqual([result.returncode for result in results], [0, 0])
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            pr_payload = json.loads(outputs[0].read_text(encoding="utf-8"))
            self.assertEqual(pr_payload["status"], "ready")
            self.assertEqual(
                [item["path"] for item in pr_payload["candidate_files"]],
                ["README.md", "assets/readme/diagram.svg"],
            )
            self.assertEqual((target / ".git/index").read_bytes(), index_before)

    @unittest.skipIf(
        os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
        "ELK flow runs in isolated Node 22 lane",
    )
    def test_elk_readme_preserves_raw_bytes_and_semantic_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            engine_root = base / "engine"
            engine_root.mkdir()
            engine = elk_adapter.ELKAdapterTests(methodName="runTest")
            rendered, raw, metadata = engine.run_adapter(
                engine_root,
                "architecture.json",
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            semantic = engine_root / "run/diagram.diagram.json"
            raw_before = raw.read_bytes()
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation, _ = self.prepare(
                run_root,
                target,
                base_sha,
                mode="readme",
                elk=True,
                engine_artifacts=(semantic, raw, metadata),
            )
            output = run_root / "pr.json"

            result = self.build(target, bundle, evaluation, output)

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                (run_root / "assets/readme/diagram.svg").read_bytes(),
                raw_before,
            )
            pr_payload = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["path"] for item in pr_payload["semantic_sources"]],
                ["assets/readme/diagram.diagram.json"],
            )

    @unittest.skipIf(
        os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
        "ELK flow runs in isolated Node 22 lane",
    )
    def test_invalid_engine_input_falls_back_to_static_asset_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            engine = elk_adapter.ELKAdapterTests(methodName="runTest")
            invalid_root = base / "invalid"
            invalid_root.mkdir()
            invalid, invalid_output, invalid_metadata = engine.run_adapter(
                invalid_root,
                "invalid-coordinate.json",
            )
            self.assertEqual(invalid.returncode, 2)
            self.assertFalse(invalid_output.exists())
            self.assertFalse(invalid_metadata.exists())

            target, base_sha = self.target(base)
            readme_before = hashlib.sha256(
                (target / "README.md").read_bytes()
            ).hexdigest()
            run_root = base / "fallback-run"
            run_root.mkdir()
            bundle, evaluation, _ = self.prepare(
                run_root,
                target,
                base_sha,
                mode="asset-only",
            )
            result = self.build(
                target,
                bundle,
                evaluation,
                run_root / "pr.json",
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                hashlib.sha256((target / "README.md").read_bytes()).hexdigest(),
                readme_before,
            )

    def test_audit_only_stops_before_pr_and_unrelated_edit_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "audit-run"
            run_root.mkdir()
            bundle, evaluation, _ = self.prepare(
                run_root,
                target,
                base_sha,
                mode="audit-only",
            )
            no_pr = self.build(
                target,
                bundle,
                evaluation,
                run_root / "audit-pr.json",
            )
            self.assertEqual(no_pr.returncode, 2)
            self.assertIn("E_PR_NO_CHANGES", no_pr.stderr)

            static_root = base / "static-run"
            static_root.mkdir()
            static_bundle, static_evaluation, _ = self.prepare(
                static_root,
                target,
                base_sha,
                mode="readme",
            )
            (target / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
            before = (target / "README.md").read_bytes()
            index_before = (target / ".git/index").read_bytes()
            rejected_output = static_root / "rejected-pr.json"
            rejected = self.build(
                target,
                static_bundle,
                static_evaluation,
                rejected_output,
            )
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("E_PR_WORKTREE", rejected.stderr)
            self.assertFalse(rejected_output.exists())
            self.assertEqual((target / "README.md").read_bytes(), before)
            self.assertEqual((target / ".git/index").read_bytes(), index_before)

    @unittest.skipIf(
        os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
        "compiled pipeline runs in isolated Node 22 lane",
    )
    def test_compiled_v3_runner_lifecycle_and_failed_compile_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, _ = self.target(base)
            (target / "README.md").write_text("# Demo\n", encoding="utf-8")
            git = pr_bundle.PrBundleTests(methodName="runTest")
            git.git(target, "add", "README.md")
            git.git(target, "commit", "-m", "compiled fixture")
            workspace = base / "workspace"
            plan, candidate, _, _ = pipeline_contracts.BundleAssembleStageTests._compiled_inputs_with_v1_evidence()
            plan_path = base / "readme-plan-v3.json"
            write_canonical_json_atomic(plan_path, plan)

            started = self.cli(
                "run", "--root", str(target), "--workspace", str(workspace),
                "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
                "--plan", str(plan_path),
            )
            self.assertEqual(started.returncode, 0, started.stderr)
            self.assertEqual(json.loads(started.stdout)["status"], "waiting-for-candidate")
            candidate_root = workspace / "stages/05-candidate"
            for relative, raw in candidate.items():
                destination = candidate_root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)

            completed = self.cli("resume", "--workspace", str(workspace))
            self.assertEqual(completed.returncode, 0, completed.stderr)
            manifest = json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], "complete")
            self.assertEqual(tuple(stage["name"] for stage in manifest["stages"]), pipeline_contracts.STAGE_NAMES)
            self.assertEqual(len(manifest["stages"]), 8)
            self.assertEqual([stage["attempt"] for stage in manifest["stages"]], [1, 1, 1, 1, 0, 1, 1, 1])
            stage6 = workspace / "stages/06-bundle-assemble"
            self.assertTrue((stage6 / "attempts/1/compiled/visual-spec.json").is_file())
            self.assertTrue((stage6 / "attempts/1/assets/readme-showcase/en/desktop.svg").is_file())
            validation = json.loads((workspace / "stages/07-validation/attempts/1/validation-report.json").read_text(encoding="utf-8"))
            evaluation = json.loads((workspace / "stages/08-evaluation/attempts/1/evaluation-report.json").read_text(encoding="utf-8"))
            self.assertEqual(validation["status"], "pass")
            self.assertEqual(evaluation["schema_version"], 3)
            self.assertEqual(evaluation["status"], "pass")

            unchanged = self.cli("resume", "--workspace", str(workspace), "--log-format", "json")
            self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
            self.assertEqual(manifest["stages"], json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))["stages"])
            skipped = [json.loads(line) for line in unchanged.stderr.splitlines() if line]
            self.assertEqual([record["event"] for record in skipped], ["stage.skipped"] * 8)
            self.assertEqual([record["stage"] for record in skipped], list(pipeline_contracts.STAGE_NAMES))

            stage6_before = {
                path.relative_to(stage6).as_posix(): path.read_bytes()
                for path in stage6.rglob("*") if path.is_file()
            }
            # Test setup marks downstream stages stale to force one public retry.
            manifest["stages"][5]["status"] = "stale"
            manifest["stages"][6]["status"] = "stale"
            manifest["stages"][7]["status"] = "stale"
            write_canonical_json_atomic(workspace / "run-manifest.json", manifest)
            with mock.patch.object(
                stages_module,
                "compile_visual",
                side_effect=ContractError("E_OUTPUT_GEOMETRY", "forced compiler failure"),
            ):
                with self.assertRaises(ContractError):
                    runner_module.resume_run(
                        workspace_path=workspace,
                        plan=None,
                        stop_after=None,
                        logger=StageLogger(verbosity="quiet"),
                    )
            failed_manifest = json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(failed_manifest["stages"][5]["attempt"], 1)
            self.assertEqual((stage6 / "current.json").read_bytes(), stage6_before["current.json"])
            self.assertFalse((stage6 / "attempts/2").exists())
            self.assertEqual(stage6_before, {
                path.relative_to(stage6).as_posix(): path.read_bytes()
                for path in stage6.rglob("*") if path.is_file()
            })

            retried = self.cli("resume", "--workspace", str(workspace))
            self.assertEqual(retried.returncode, 0, retried.stderr)
            self.assertEqual(
                json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))["status"],
                "complete",
                retried.stdout + retried.stderr,
            )


if __name__ == "__main__":
    unittest.main()
