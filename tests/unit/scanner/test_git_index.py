from __future__ import annotations

import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.scanner import git as scanner_git
from skill.scripts.readme_showcase.scanner.service import tracked_file_index


class TrackedFileIndexTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(root), *arguments],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def make_repository(self, base: Path) -> tuple[Path, str]:
        root = base / "target"
        root.mkdir()
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "scanner@example.invalid")
        self.git(root, "config", "user.name", "Scanner Test")
        for name, content in (
            ("README.md", "# Demo\n"),
            ("src/hello world.py", "print('space')\n"),
            ("src/line\nbreak.js", "console.log('newline')\n"),
            ("文档/说明.md", "说明\n"),
        ):
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git(root, "add", "--all")
        self.git(root, "commit", "-qm", "fixture")
        return root, self.git(root, "rev-parse", "HEAD")

    def test_directory_git_packed_refs_paths_and_untracked_exclusion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, expected_sha = self.make_repository(Path(temporary))
            self.git(root, "pack-refs", "--all")
            (root / "untracked.txt").write_text("do not index\n", encoding="utf-8")

            first = tracked_file_index(root)
            second = tracked_file_index(root / ".." / "target")

            self.assertEqual(first, second)
            self.assertEqual(first["base_sha"], expected_sha)
            self.assertEqual(
                [entry["path"] for entry in first["files"]],
                ["README.md", "src/hello world.py", "src/line\nbreak.js", "文档/说明.md"],
            )
            self.assertNotIn("untracked.txt", {entry["path"] for entry in first["files"]})
            self.assertEqual(first["files"][0]["role"], "readme")
            self.assertEqual(first["files"][1]["language"], "python")
            self.assertTrue(all(entry["tracked"] for entry in first["files"]))
            self.assertTrue(all(not entry["selected_for_content"] for entry in first["files"]))
            self.assertTrue(all(entry["sha256"] is None for entry in first["files"]))

    def test_worktree_git_file_resolves_base(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, expected_sha = self.make_repository(base)
            worktree = base / "linked"
            self.git(root, "worktree", "add", "--detach", "-q", str(worktree), "HEAD")
            self.assertTrue((worktree / ".git").is_file())

            result = tracked_file_index(worktree)

            self.assertEqual(result["base_sha"], expected_sha)
            self.assertEqual(result["files"][0]["path"], "README.md")

    def test_fsmonitor_never_executes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, _ = self.make_repository(base)
            sentinel = base / "fsmonitor-ran"
            hook = base / "fsmonitor.sh"
            hook.write_text(f"#!/bin/sh\ntouch '{sentinel}'\n", encoding="utf-8")
            hook.chmod(hook.stat().st_mode | stat.S_IXUSR)
            self.git(root, "config", "core.fsmonitor", str(hook))

            tracked_file_index(root)

            self.assertFalse(sentinel.exists())

    def test_commit_between_git_observations_never_returns_mixed_state(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, _ = self.make_repository(Path(temporary))
            original = scanner_git._git_output
            mutated = False

            def mutate_after_paths(target: Path, *arguments: str) -> bytes:
                nonlocal mutated
                output = original(target, *arguments)
                if arguments == ("ls-files", "-z", "--cached") and not mutated:
                    mutated = True
                    (root / "new.py").write_text("print('new')\n", encoding="utf-8")
                    self.git(root, "add", "new.py")
                    self.git(root, "commit", "-qm", "race")
                return output

            with mock.patch.object(scanner_git, "_git_output", side_effect=mutate_after_paths):
                result = tracked_file_index(root)

            self.assertEqual(result["base_sha"], self.git(root, "rev-parse", "HEAD"))
            self.assertIn("new.py", {entry["path"] for entry in result["files"]})

    def test_index_only_mutation_forces_a_fresh_observation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, expected_sha = self.make_repository(Path(temporary))
            original = scanner_git._git_output
            path_observations = 0

            def mutate_index(target: Path, *arguments: str) -> bytes:
                nonlocal path_observations
                output = original(target, *arguments)
                if arguments == ("ls-files", "-z", "--cached"):
                    path_observations += 1
                    if path_observations == 1:
                        (root / "README.md").write_text("# staged mutation\n", encoding="utf-8")
                        self.git(root, "add", "README.md")
                return output

            with mock.patch.object(scanner_git, "_git_output", side_effect=mutate_index):
                result = tracked_file_index(root)

            self.assertEqual(result["base_sha"], expected_sha)
            self.assertGreaterEqual(path_observations, 2)
            readme = next(entry for entry in result["files"] if entry["path"] == "README.md")
            self.assertEqual(readme["bytes"], len("# staged mutation\n"))

    def test_tracked_symlink_and_fifo_fail_without_reading(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root, _ = self.make_repository(base)
            tracked = root / "README.md"
            tracked.unlink()
            tracked.symlink_to(base / "outside")
            with self.assertRaisesRegex(ContractError, "tracked path must be a regular file"):
                tracked_file_index(root)

            tracked.unlink()
            os.mkfifo(tracked)
            with self.assertRaisesRegex(ContractError, "tracked path must be a regular file"):
                tracked_file_index(root)


if __name__ == "__main__":
    unittest.main()
