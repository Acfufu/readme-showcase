from __future__ import annotations

import hashlib
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_sha256, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph


class DeliveryWorktreeIntegrationTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str, check: bool = True) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )
        if check:
            self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout.strip()

    def repository(self, root: Path) -> tuple[Path, str]:
        repository = root / "repository"
        repository.mkdir()
        self.git(repository, "init", "-b", "main")
        self.git(repository, "config", "user.name", "Delivery Test")
        self.git(repository, "config", "user.email", "delivery@example.invalid")
        self.git(repository, "remote", "add", "origin", "https://github.com/owner/repo.git")
        (repository / "README.md").write_bytes(b"# Base\n")
        self.git(repository, "add", "README.md")
        self.git(repository, "commit", "-m", "base")
        return repository, self.git(repository, "rev-parse", "HEAD")

    def bundle(
        self,
        artifacts: Path,
        base_sha: str,
        *,
        readme_path: str = "README.md",
        raw: bytes = b"# Candidate\n",
        assets: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        path = artifacts.joinpath(*Path(readme_path).parts)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        candidate = {
            "readme": {"path": readme_path, "sha256": hashlib.sha256(raw).hexdigest()},
            "assets": assets or [],
        }
        return {
            "schema_version": 2,
            "mode": "readme",
            "target": {"repository": "owner/repo", "base_sha": base_sha},
            "candidate": {**candidate, "candidate_sha256": canonical_sha256(candidate)},
            "artifacts": {},
        }

    def assert_code(self, code: str, function: object, *args: object, **kwargs: object) -> ContractError:
        with self.assertRaises(ContractError) as raised:
            function(*args, **kwargs)  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)
        return raised.exception

    def test_dirty_main_is_unchanged_and_candidate_result_is_deterministic(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha)
            (repository / "README.md").write_bytes(b"# Dirty main\n")
            (repository / "staged.txt").write_bytes(b"staged\n")
            self.git(repository, "add", "staged.txt")
            (repository / "untracked.txt").write_bytes(b"untracked\n")
            index_before = (repository / ".git/index").read_bytes()
            readme_before = (repository / "README.md").read_bytes()
            status_before = self.git(repository, "status", "--porcelain=v1", "-z")
            refs_before = self.git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)")
            worktrees_before = self.git(repository, "worktree", "list", "--porcelain")

            first = prepare_delivery_worktree(payload, artifacts, repository, {"README.md"})
            second = prepare_delivery_worktree(payload, artifacts, repository, {"README.md"})

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "prepared")
            self.assertEqual(first["target"]["base_sha"], base_sha)
            self.assertEqual([item["path"] for item in first["candidate_files"]], ["README.md"])
            self.assertIn(b"# Candidate", bytes.fromhex(first["diff_hex"]))
            self.assertEqual((repository / ".git/index").read_bytes(), index_before)
            self.assertEqual((repository / "README.md").read_bytes(), readme_before)
            self.assertEqual(self.git(repository, "status", "--porcelain=v1", "-z"), status_before)
            self.assertEqual(self.git(repository, "for-each-ref", "--format=%(refname)%00%(objectname)"), refs_before)
            self.assertEqual(self.git(repository, "worktree", "list", "--porcelain"), worktrees_before)

    def test_missing_and_mismatched_base_fail_closed(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree
        from skill.scripts.readme_showcase.delivery import worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            missing = self.bundle(artifacts, "0" * 40)
            self.assert_code("E_PR_BASE", prepare_delivery_worktree, missing, artifacts, repository, {"README.md"})

            payload = self.bundle(artifacts, base_sha)
            original = worktree._add_worktree

            def wrong_base(repo: Path, destination: Path, requested: str, configurations: object = ()) -> None:
                (repo / "other.txt").write_text("other\n", encoding="utf-8")
                self.git(repo, "add", "other.txt")
                self.git(repo, "commit", "-m", "other")
                original(repo, destination, self.git(repo, "rev-parse", "HEAD"), configurations)

            with mock.patch.object(worktree, "_add_worktree", side_effect=wrong_base):
                self.assert_code("E_PR_BASE", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})
            self.assertEqual(len(self.git(repository, "worktree", "list", "--porcelain").split("worktree ")), 2)

    def test_allowlist_duplicate_and_unsafe_paths_fail_before_worktree(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            for index, path in enumerate(("../README.md", "/tmp/README.md", ".git", "assets/.git/config")):
                artifacts = root / f"artifacts-{index}"
                artifacts.mkdir()
                payload = self.bundle(artifacts, base_sha)
                payload["candidate"]["readme"]["path"] = path
                self.assert_code("E_PR_PATH", prepare_delivery_worktree, payload, artifacts, repository, {path})

            artifacts = root / "extra"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha)
            self.assert_code("E_PR_PATH", prepare_delivery_worktree, payload, artifacts, repository, {"README.md", "EXTRA.md"})
            self.assert_code("E_PR_PATH", prepare_delivery_worktree, payload, artifacts, repository, ["README.md", "README.md"])
            duplicate = dict(payload)
            duplicate["candidate"] = dict(payload["candidate"])
            duplicate["candidate"]["assets"] = [dict(payload["candidate"]["readme"])]
            self.assert_code("E_PR_PATH", prepare_delivery_worktree, duplicate, artifacts, repository, {"README.md"})

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO requires POSIX")
    def test_symlink_fifo_and_git_payloads_are_rejected(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            for kind in ("symlink", "fifo"):
                artifacts = root / kind
                artifacts.mkdir()
                candidate = artifacts / "README.md"
                if kind == "symlink":
                    candidate.symlink_to(repository / "README.md")
                else:
                    os.mkfifo(candidate)
                reference = {"path": "README.md", "sha256": "0" * 64}
                body = {"readme": reference, "assets": []}
                payload = {
                    "schema_version": 2,
                    "mode": "readme",
                    "target": {"repository": "owner/repo", "base_sha": base_sha},
                    "candidate": {**body, "candidate_sha256": canonical_sha256(body)},
                    "artifacts": {},
                }
                self.assert_code("E_PR_PATH", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})

    def test_evidence_is_bound_to_immutable_base_not_dirty_main(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha)
            dirty = b"# Dirty main\n"
            (repository / "README.md").write_bytes(dirty)
            graph = EvidenceGraph(
                [
                    build_fact(
                        kind="file-presence",
                        path="README.md",
                        locator=None,
                        semantic_key="presence",
                        value=True,
                        source_bytes=dirty,
                    )
                ]
            ).to_dict()
            evidence_path = artifacts / "repository-evidence.json"
            write_canonical_json_atomic(evidence_path, graph)
            payload["artifacts"] = {
                "evidence": {
                    "path": evidence_path.name,
                    "sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
                }
            }

            self.assert_code("E_PR_EVIDENCE", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})

    def test_detached_checkout_disables_repository_filter_processes(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, _ = self.repository(root)
            marker = root / "filter-executed"
            filter_script = root / "filter.sh"
            filter_script.write_text(f"#!/bin/sh\n: > '{marker}'\ncat\n", encoding="utf-8")
            filter_script.chmod(0o700)
            (repository / ".gitattributes").write_text(
                "README.md filter=smudgeevil\nNEW.md filter=cleanevil\n",
                encoding="utf-8",
            )
            (repository / "subdir").mkdir()
            (repository / "subdir/.gitattributes").write_text("file.txt filter=nestedevil\n", encoding="utf-8")
            (repository / "subdir/file.txt").write_text("nested\n", encoding="utf-8")
            for name in ("smudgeevil", "cleanevil", "nestedevil"):
                self.git(repository, "config", f"filter.{name}.clean", "cat")
                self.git(repository, "config", f"filter.{name}.smudge", "cat")
            self.git(repository, "add", ".gitattributes", "subdir/.gitattributes", "subdir/file.txt")
            self.git(repository, "commit", "-m", "attributes")
            self.git(repository, "config", "filter.smudgeevil.smudge", str(filter_script))
            self.git(repository, "config", "filter.smudgeevil.required", "true")
            self.git(repository, "config", "filter.cleanevil.clean", str(filter_script))
            self.git(repository, "config", "filter.cleanevil.required", "true")
            self.git(repository, "config", "filter.nestedevil.smudge", str(filter_script))
            self.git(repository, "config", "filter.nestedevil.required", "true")
            base_sha = self.git(repository, "rev-parse", "HEAD")
            artifacts = root / "artifacts"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha, readme_path="NEW.md")

            result = prepare_delivery_worktree(payload, artifacts, repository, {"NEW.md"})

            self.assertEqual(result["status"], "prepared")
            self.assertFalse(marker.exists())

    def test_extra_worktree_path_interruption_cleanup_and_concurrency(self) -> None:
        from skill.scripts.readme_showcase.delivery import prepare_delivery_worktree
        from skill.scripts.readme_showcase.delivery import worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha)
            baseline = self.git(repository, "worktree", "list", "--porcelain")
            original_apply = worktree._apply_candidates

            def inject_extra(destination: Path, candidates: object) -> None:
                original_apply(destination, candidates)
                (destination / "EXTRA.md").write_text("extra\n", encoding="utf-8")

            with mock.patch.object(worktree, "_apply_candidates", side_effect=inject_extra):
                self.assert_code("E_PR_PATH", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})
            with mock.patch.object(worktree, "_apply_candidates", side_effect=KeyboardInterrupt):
                self.assert_code("E_PR_GIT", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})
            with mock.patch.object(worktree.tempfile, "mkdtemp", side_effect=FileExistsError):
                self.assert_code("E_PR_GIT", prepare_delivery_worktree, payload, artifacts, repository, {"README.md"})
            self.assertEqual(self.git(repository, "worktree", "list", "--porcelain"), baseline)

            results: list[dict[str, object]] = []
            errors: list[BaseException] = []
            barrier = threading.Barrier(2)

            def synchronized(destination: Path, candidates: object) -> None:
                barrier.wait(timeout=5)
                original_apply(destination, candidates)

            def run() -> None:
                try:
                    results.append(prepare_delivery_worktree(payload, artifacts, repository, {"README.md"}))
                except BaseException as exc:
                    errors.append(exc)

            with mock.patch.object(worktree, "_apply_candidates", side_effect=synchronized):
                threads = [threading.Thread(target=run) for _ in range(2)]
                for thread in threads:
                    thread.start()
                for thread in threads:
                    thread.join(timeout=10)
            self.assertEqual(errors, [])
            self.assertEqual(len(results), 2)
            self.assertEqual(self.git(repository, "worktree", "list", "--porcelain"), baseline)

    def test_audited_failure_retention_is_explicit_and_cleanup_is_idempotent(self) -> None:
        from skill.scripts.readme_showcase.delivery import cleanup_delivery_worktree, prepare_delivery_worktree
        from skill.scripts.readme_showcase.delivery import worktree

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository, base_sha = self.repository(root)
            artifacts = root / "artifacts"
            artifacts.mkdir()
            payload = self.bundle(artifacts, base_sha)
            with mock.patch.object(worktree, "_apply_candidates", side_effect=ContractError("E_PR_PATH", "injected")):
                error = self.assert_code(
                    "E_PR_PATH",
                    prepare_delivery_worktree,
                    payload,
                    artifacts,
                    repository,
                    {"README.md"},
                    audit_retain_failure=True,
                    retention_reason="security review",
                )
            retained = Path(error.retained_path)  # type: ignore[attr-defined]
            self.assertTrue(retained.is_dir())
            self.assertEqual(error.retention_reason, "security review")  # type: ignore[attr-defined]
            self.assertEqual(error.failure_result["retained_path"], str(retained))  # type: ignore[attr-defined]
            self.assertEqual(error.failure_result["retention_reason"], "security review")  # type: ignore[attr-defined]
            cleanup_delivery_worktree(repository, retained)
            cleanup_delivery_worktree(repository, retained)
            self.assertFalse(retained.exists())


if __name__ == "__main__":
    unittest.main()
