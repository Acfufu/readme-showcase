from __future__ import annotations

import copy
import hashlib
import importlib
import json
import shutil
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
from skill.scripts.readme_showcase.delivery.legacy import validate_pr_bundle
from tests import test_claim_coverage as claim_coverage
from tests.contract import test_bundle_v3 as _bundle_v3


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
        elk: bool = False,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        helper = claim_coverage.ClaimCoverageTests(methodName="runTest")
        bundle = helper.monolingual_bundle(root, elk=elk)
        bundle["target"] = {
            "repository": "owner/target",
            "base_sha": base_sha,
        }
        evidence_path = root / "repository-evidence.json"
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        target_readme = root.parent / "target" / "README.md"
        target_content = target_readme.read_text(encoding="utf-8")
        target_digest = hashlib.sha256(target_content.encode()).hexdigest()
        evidence = {
            **evidence,
            "target": {"name": "target", "base_sha": base_sha},
            "files": [
                {
                    "path": "README.md",
                    "bytes": len(target_content.encode()),
                    "lines": len(target_content.splitlines()),
                    "sha256": target_digest,
                    "content": target_content,
                }
            ],
            "facts": [
                {
                    "fact_id": "file:README.md",
                    "kind": "repository-file",
                    "path": "README.md",
                    "evidence_sha256": target_digest,
                }
            ],
        }
        write_canonical_json_atomic(evidence_path, evidence)
        plan_path = root / bundle["artifacts"]["plan"]["path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["evidence_ids"] = ["file:README.md"]
        write_canonical_json_atomic(plan_path, plan)
        bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)
        claims_path = root / bundle["artifacts"]["claim_map"]["path"]
        claims = json.loads(claims_path.read_text(encoding="utf-8"))
        for collection in ("markdown_blocks", "diagram_labels"):
            for claim in claims[collection]:
                claim["truth_id"] = "file:README.md"
                claim["evidence_sha256"] = target_digest
        write_canonical_json_atomic(claims_path, claims)
        bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
        manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for asset in manifest["assets"]:
            asset["truth_ids"] = ["file:README.md"]
        write_canonical_json_atomic(manifest_path, manifest)
        bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(
            manifest
        )
        if not elk:
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

    def run_compiled_bundle(
        self,
        root: Path,
    ) -> tuple[Path, str, Path, dict[str, Any], dict[str, Any]]:
        root.mkdir(parents=True, exist_ok=True)
        target, _ = self.target(root)
        for index in range(1, 8):
            (target / f"scene-evidence-{index}.md").write_bytes(
                f"scene-evidence-{index}".encode()
            )
        self.git(target, "add", ".")
        self.git(target, "commit", "-m", "compiled evidence")
        base_sha = self.git(target, "rev-parse", "HEAD")
        run_root = root / "compiled-run"
        run_root.mkdir()
        bundle = _bundle_v3.BundleV3ContractTests(methodName="runTest").make_bundle(run_root)
        bundle["target"] = {
            "repository": "owner/target",
            "base_sha": base_sha,
        }
        evaluation = evaluate_generated_bundle(bundle, run_root)
        self.assertEqual(evaluation["status"], "pass")
        return target, base_sha, run_root, bundle, evaluation

    def test_compiled_bundle_builds_deterministic_v2_with_only_readme_and_svg_candidates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root)
            index_before = hashlib.sha256((target / ".git/index").read_bytes()).hexdigest()

            first = build_pr_bundle(bundle, evaluation, run_root, target)
            second = build_pr_bundle(bundle, evaluation, run_root, target)

            self.assertEqual(first, second)
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first["schema_version"], 2)
            self.assertEqual(first["target"]["base_sha"], base_sha)
            self.assertEqual(first["semantic_sources"], [])
            self.assertEqual(
                [item["path"] for item in first["candidate_files"]],
                [
                    "README.md",
                    "README_zh.md",
                    "assets/readme-showcase/en/desktop.svg",
                    "assets/readme-showcase/en/mobile.svg",
                ],
            )
            self.assertEqual(
                first["compiled"]["fingerprint"],
                bundle["compiled"]["fingerprint"],
            )
            self.assertEqual(validate_pr_bundle(first), first)
            self.assertEqual(
                first["fingerprint"],
                canonical_sha256({
                    key: value
                    for key, value in first.items()
                    if key not in {"fingerprint", "status"}
                }),
            )
            self.assertEqual(
                hashlib.sha256((target / ".git/index").read_bytes()).hexdigest(),
                index_before,
            )

    def test_compiled_bundle_rejects_drift_internal_publish_paths_and_target_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root)
            index_before = (target / ".git/index").read_bytes()

            (run_root / "compiled/inventory.json").write_bytes(
                (run_root / "compiled/inventory.json").read_bytes() + b" "
            )
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_BUNDLE_HASH")
            self.assertEqual((target / ".git/index").read_bytes(), index_before)

            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root / "internal")
            internal = run_root / "compiled/scenes/en/desktop.json"
            bundle["candidate"]["assets"][0] = {
                "path": "compiled/scenes/en/desktop.json",
                "sha256": hashlib.sha256(internal.read_bytes()).hexdigest(),
            }
            bundle["candidate"]["candidate_sha256"] = canonical_sha256({
                "readmes": bundle["candidate"]["readmes"],
                "assets": bundle["candidate"]["assets"],
            })
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_BUNDLE_ASSET")

            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root / "state")
            state_index_before = (target / ".git/index").read_bytes()
            (target / "dirty.txt").write_text("dirty\n", encoding="utf-8")
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_WORKTREE")
            (target / "dirty.txt").unlink()

            bundle["target"]["base_sha"] = "0" * 40
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_BASE")
            self.assertEqual((target / ".git/index").read_bytes(), state_index_before)

            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root / "origin")
            self.git(target, "remote", "set-url", "origin", "https://github.com/owner/other.git")
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_TARGET")

            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root / "evaluation")
            stale_fingerprint = dict(evaluation)
            stale_fingerprint["compiled_fingerprint"] = "0" * 64
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, stale_fingerprint, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_EVALUATION")
            stale_bundle_hash = dict(evaluation)
            stale_bundle_hash["bundle_sha256"] = "0" * 64
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, stale_bundle_hash, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_EVALUATION")

    def test_compiled_bundle_rejects_excluded_path_and_no_change_without_index_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, _, run_root, bundle, evaluation = self.run_compiled_bundle(root)
            source = run_root / "README.md"
            excluded = run_root / ".omo/README.md"
            excluded.parent.mkdir()
            shutil.copyfile(source, excluded)
            bundle["candidate"]["readmes"][0] = {
                "path": ".omo/README.md",
                "sha256": hashlib.sha256(excluded.read_bytes()).hexdigest(),
            }
            with self.assertRaises(ContractError):
                build_pr_bundle(bundle, evaluation, run_root, target)

            target, _, run_root, bundle, evaluation = self.run_compiled_bundle(root / "pr-path")
            valid_pr = build_pr_bundle(bundle, evaluation, run_root, target)
            path_index_before = (target / ".git/index").read_bytes()
            invalid_pr = copy.deepcopy(valid_pr)
            invalid_pr["candidate_files"][0]["path"] = ".omo/README.md"
            projection = {
                key: value
                for key, value in invalid_pr.items()
                if key not in {"fingerprint", "status"}
            }
            invalid_pr["fingerprint"] = canonical_sha256(projection)
            with self.assertRaises(ContractError) as raised:
                validate_pr_bundle(invalid_pr)
            self.assertEqual(raised.exception.code, "E_PR_PATH")
            self.assertEqual((target / ".git/index").read_bytes(), path_index_before)

            target, base_sha, run_root, bundle, evaluation = self.run_compiled_bundle(root / "unchanged")
            for reference in [*bundle["candidate"]["readmes"], *bundle["candidate"]["assets"]]:
                destination = target / reference["path"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(run_root / reference["path"], destination)
            self.git(target, "add", ".")
            self.git(target, "commit", "-m", "candidate baseline")
            bundle["target"]["base_sha"] = self.git(target, "rev-parse", "HEAD")
            evaluation = evaluate_generated_bundle(bundle, run_root)
            unchanged_index_before = (target / ".git/index").read_bytes()
            with self.assertRaises(ContractError) as raised:
                build_pr_bundle(bundle, evaluation, run_root, target)
            self.assertEqual(raised.exception.code, "E_PR_NO_CHANGES")
            self.assertEqual((target / ".git/index").read_bytes(), unchanged_index_before)

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

            self.assertEqual(first["schema_version"], 1)
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

            elk_root = base / "elk-run"
            elk_root.mkdir()
            elk_bundle, elk_evaluation = self.run_bundle(
                elk_root,
                base_sha,
                elk=True,
            )
            elk_pr = build_pr_bundle(
                elk_bundle,
                elk_evaluation,
                elk_root,
                target,
            )
            self.assertEqual(
                [item["path"] for item in elk_pr["candidate_files"]],
                ["assets/readme/diagram.svg"],
            )
            self.assertEqual(
                [item["path"] for item in elk_pr["semantic_sources"]],
                ["assets/readme/diagram.diagram.json"],
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
                elk=True,
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

    def test_git_inspection_disables_repository_fsmonitor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "run"
            run_root.mkdir()
            bundle, evaluation = self.run_bundle(run_root, base_sha)
            marker = base / "fsmonitor-ran"
            hook = base / "fsmonitor.sh"
            hook.write_text(
                f"#!/bin/sh\nprintf called > '{marker}'\nprintf '\\n'\n",
                encoding="utf-8",
            )
            hook.chmod(0o700)
            self.git(target, "config", "core.fsmonitor", str(hook))

            _ = build_pr_bundle(bundle, evaluation, run_root, target)

            self.assertFalse(marker.exists())

    def test_evidence_bytes_must_match_clean_target_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            target, base_sha = self.target(base)
            run_root = base / "run"
            run_root.mkdir()
            bundle, _ = self.run_bundle(run_root, base_sha)
            evidence_path = run_root / "repository-evidence.json"
            evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
            fabricated = "fabricated target evidence\n"
            digest = hashlib.sha256(fabricated.encode()).hexdigest()
            evidence["files"][0].update(
                {
                    "bytes": len(fabricated.encode()),
                    "lines": 1,
                    "sha256": digest,
                    "content": fabricated,
                }
            )
            evidence["facts"][0]["evidence_sha256"] = digest
            write_canonical_json_atomic(evidence_path, evidence)
            claims_path = run_root / bundle["artifacts"]["claim_map"]["path"]
            claims = json.loads(claims_path.read_text(encoding="utf-8"))
            for collection in ("markdown_blocks", "diagram_labels"):
                for claim in claims[collection]:
                    claim["evidence_sha256"] = digest
            write_canonical_json_atomic(claims_path, claims)
            bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(
                claims
            )
            evaluation = evaluate_generated_bundle(bundle, run_root)

            self.assert_code(
                "E_PR_EVIDENCE",
                bundle,
                evaluation,
                run_root,
                target,
            )

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
