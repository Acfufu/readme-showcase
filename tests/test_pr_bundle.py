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
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from tests import test_claim_coverage as claim_coverage


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
_CORE = importlib.import_module("skill.scripts.pipeline_core")
build_pr_bundle = _CORE.build_pr_bundle
evaluate_generated_bundle = _CORE.evaluate_generated_bundle


class PrBundleTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def target(self, root: Path) -> tuple[Path, str]:
        target = root / "target"
        target.mkdir()
        self.git(target, "init", "-b", "main")
        self.git(target, "config", "user.name", "README Test")
        self.git(target, "config", "user.email", "readme@example.invalid")
        self.git(
            target,
            "remote",
            "add",
            "origin",
            "https://github.com/owner/target.git",
        )
        (target / "README.md").write_text("# Existing\n", encoding="utf-8")
        self.git(target, "add", "README.md")
        self.git(target, "commit", "-m", "initial")
        return target, self.git(target, "rev-parse", "HEAD")

    def run_bundle(
        self,
        root: Path,
        base_sha: str,
        *,
        glyphic: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        helper = claim_coverage.ClaimCoverageTests(methodName="runTest")
        bundle = helper.monolingual_bundle(root, glyphic=glyphic)
        bundle["target"] = {
            "repository": "owner/target",
            "base_sha": base_sha,
        }
        evidence_path = root / "repository-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        evidence["target"]["base_sha"] = base_sha
        write_canonical_json_atomic(evidence_path, evidence)
        if not glyphic:
            source = root / bundle["candidate"]["readme"]["path"]
            destination = root / "README.md"
            source.rename(destination)
            bundle["candidate"]["readme"] = {
                "path": "README.md",
                "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            }
        evaluation = evaluate_generated_bundle(bundle, root)
        self.assertEqual(evaluation["status"], "pass")
        return bundle, evaluation

    def assert_code(
        self,
        code: str,
        bundle: dict[str, Any],
        evaluation: dict[str, Any],
        run_root: Path,
        target_root: Path,
    ) -> None:
        with self.assertRaises(ContractError) as raised:
            build_pr_bundle(bundle, evaluation, run_root, target_root)
        self.assertEqual(raised.exception.code, code)

    def test_deterministic_fingerprint_binds_candidate_and_semantic_source(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation = self.run_bundle(run_root, base_sha)
            index_before = hashlib.sha256((target / ".git/index").read_bytes()).hexdigest()

            first = build_pr_bundle(bundle, evaluation, run_root, target)
            second = build_pr_bundle(bundle, evaluation, run_root, target)

            self.assertEqual(first, second)
            self.assertEqual(
                first["fingerprint"],
                canonical_sha256({
                    key: value
                    for key, value in first.items()
                    if key not in {"fingerprint", "status"}
                }),
            )
            self.assertEqual(
                [item["path"] for item in first["candidate_files"]],
                ["README.md", "assets/readme/diagram.svg"],
            )
            self.assertEqual(first["semantic_sources"], [])
            self.assertEqual(first["target"]["base_sha"], base_sha)
            self.assertEqual(
                first["target"]["branch"],
                f"readme-showcase/{canonical_sha256(bundle)[:12]}",
            )
            self.assertEqual(
                hashlib.sha256((target / ".git/index").read_bytes()).hexdigest(),
                index_before,
            )

            glyphic_root = base / "glyphic-run"
            glyphic_root.mkdir()
            glyphic_bundle, glyphic_evaluation = self.run_bundle(
                glyphic_root,
                base_sha,
                glyphic=True,
            )
            glyphic_pr = build_pr_bundle(
                glyphic_bundle,
                glyphic_evaluation,
                glyphic_root,
                target,
            )
            self.assertEqual(
                [item["path"] for item in glyphic_pr["candidate_files"]],
                ["assets/readme/diagram.svg"],
            )
            self.assertEqual(
                [item["path"] for item in glyphic_pr["semantic_sources"]],
                ["assets/readme/diagram.glyphic.json"],
            )

    def test_base_evaluation_dirty_and_excluded_paths_fail_without_index_change(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation = self.run_bundle(run_root, base_sha)
            index_before = (target / ".git/index").read_bytes()

            stale = dict(evaluation)
            stale["bundle_sha256"] = "0" * 64
            self.assert_code(
                "E_PR_EVALUATION",
                bundle,
                stale,
                run_root,
                target,
            )

            bundle["target"]["base_sha"] = "0" * 40
            self.assert_code("E_PR_BASE", bundle, evaluation, run_root, target)
            bundle["target"]["base_sha"] = base_sha

            (target / "unrelated.txt").write_text("dirty\n", encoding="utf-8")
            self.assert_code("E_PR_WORKTREE", bundle, evaluation, run_root, target)
            (target / "unrelated.txt").unlink()

            excluded_root = base / "excluded-run"
            excluded_root.mkdir()
            excluded_bundle, _ = self.run_bundle(
                excluded_root,
                base_sha,
                glyphic=True,
            )
            source = excluded_root / "assets/readme/diagram.svg"
            excluded = excluded_root / ".omo/diagram.svg"
            excluded.parent.mkdir()
            source.rename(excluded)
            excluded_bundle["candidate"]["assets"][0]["path"] = ".omo/diagram.svg"
            manifest_path = (
                excluded_root
                / excluded_bundle["artifacts"]["asset_manifest"]["path"]
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["path"] = ".omo/diagram.svg"
            write_canonical_json_atomic(manifest_path, manifest)
            excluded_bundle["artifacts"]["asset_manifest"]["sha256"] = (
                canonical_sha256(manifest)
            )
            excluded_evaluation = evaluate_generated_bundle(
                excluded_bundle,
                excluded_root,
            )
            self.assertEqual(excluded_evaluation["status"], "pass")
            self.assert_code(
                "E_PR_PATH",
                excluded_bundle,
                excluded_evaluation,
                excluded_root,
                target,
            )

            self.assertEqual((target / ".git/index").read_bytes(), index_before)

    def test_cli_outputs_repeat_without_staging(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation = self.run_bundle(run_root, base_sha)
            bundle_path = run_root / "generated-readme-bundle.json"
            evaluation_path = run_root / "evaluation-report.json"
            write_canonical_json_atomic(bundle_path, bundle)
            write_canonical_json_atomic(evaluation_path, evaluation)
            cached_before = self.git(target, "diff", "--cached", "--binary")
            outputs = [run_root / "pr-1.json", run_root / "pr-2.json"]

            for output in outputs:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(PIPELINE),
                        "build-pr-bundle",
                        "--bundle",
                        str(bundle_path),
                        "--evaluation",
                        str(evaluation_path),
                        "--output",
                        str(output),
                    ],
                    cwd=target,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(outputs[0].read_bytes(), outputs[1].read_bytes())
            self.assertEqual(self.git(target, "diff", "--cached", "--binary"), cached_before)


if __name__ == "__main__":
    unittest.main()
