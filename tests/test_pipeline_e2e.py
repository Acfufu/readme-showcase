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

from skill.scripts.pipeline_contracts import (
    canonical_sha256,
    write_canonical_json_atomic,
)
from tests import test_bundle_contracts as bundle_contracts
from tests import test_glyphic_adapter as glyphic_adapter
from tests import test_pr_bundle as pr_bundle


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
        glyphic: bool = False,
        engine_artifacts: tuple[Path, Path, Path] | None = None,
    ) -> tuple[Path, Path, dict[str, Any]]:
        helper = bundle_contracts.BundleContractTests(methodName="runTest")
        bundle, _ = helper.make_bundle(
            run_root,
            mode,
            glyphic=glyphic,
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
            semantic_destination = run_root / "assets/readme/diagram.glyphic.json"
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
        "fake Glyphic flow runs in isolated Node 22 lane",
    )
    def test_fake_glyphic_readme_preserves_raw_bytes_and_semantic_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            engine_root = base / "engine"
            engine_root.mkdir()
            engine = glyphic_adapter.GlyphicAdapterTests(methodName="runTest")
            module_root, lock = engine.build_engine(engine_root)
            rendered, raw, metadata = engine.run_adapter(
                engine_root,
                module_root,
                lock,
                "architecture.json",
            )
            self.assertEqual(rendered.returncode, 0, rendered.stderr)
            semantic = engine_root / "run/diagram.glyphic.json"
            raw_before = raw.read_bytes()
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation, _ = self.prepare(
                run_root,
                target,
                base_sha,
                mode="readme",
                glyphic=True,
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
                ["assets/readme/diagram.glyphic.json"],
            )

    @unittest.skipIf(
        os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
        "fake Glyphic flow runs in isolated Node 22 lane",
    )
    def test_missing_and_unsafe_engine_fall_back_to_static_asset_only(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            engine = glyphic_adapter.GlyphicAdapterTests(methodName="runTest")

            missing_root = base / "missing"
            missing_root.mkdir()
            _, lock = engine.build_engine(missing_root)
            missing, missing_output, _ = engine.run_adapter(
                missing_root,
                missing_root / "absent",
                lock,
                "architecture.json",
            )
            self.assertEqual(missing.returncode, 1)
            self.assertFalse(missing_output.exists())

            unsafe_root = base / "unsafe"
            unsafe_root.mkdir()
            module_root, unsafe_lock = engine.build_engine(unsafe_root, "unsafe")
            unsafe_run = unsafe_root / "run"
            unsafe_run.mkdir()
            unsafe_output = unsafe_run / "diagram.svg"
            unsafe_metadata = unsafe_run / "diagram.engine.json"
            unsafe_output.write_bytes(b"last-good-svg")
            unsafe_metadata.write_bytes(b"last-good-metadata")
            unsafe, _, _ = engine.run_adapter(
                unsafe_root,
                module_root,
                unsafe_lock,
                "architecture.json",
            )
            self.assertEqual(unsafe.returncode, 1)
            self.assertEqual(unsafe_output.read_bytes(), b"last-good-svg")
            self.assertEqual(unsafe_metadata.read_bytes(), b"last-good-metadata")

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


if __name__ == "__main__":
    unittest.main()
