from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Protocol, cast


REPO_ROOT = Path(__file__).resolve().parents[1]
INSTALLER = REPO_ROOT / "scripts" / "install_skill.py"


class InstallerModule(Protocol):
    InstallError: type[RuntimeError]

    def install(
        self,
        repo_root: Path,
        codex_home: Path,
        **kwargs: object,
    ) -> dict[str, object]: ...

    def check_install(
        self,
        repo_root: Path,
        codex_home: Path,
    ) -> dict[str, object]: ...


installer_spec = importlib.util.spec_from_file_location(
    "readme_showcase_install_skill",
    INSTALLER,
)
if installer_spec is None or installer_spec.loader is None:
    raise RuntimeError(f"cannot load installer: {INSTALLER}")
installer_module = importlib.util.module_from_spec(installer_spec)
installer_spec.loader.exec_module(installer_module)
install_skill = cast(InstallerModule, cast(object, installer_module))


def file_map(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def expected_files() -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for source, prefix in (
        (REPO_ROOT / "skill", Path()),
        (REPO_ROOT / "dataset", Path("dataset")),
    ):
        for path in sorted(source.rglob("*")):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and path.suffix != ".pyc"
                and path.name != ".DS_Store"
            ):
                result[(prefix / path.relative_to(source)).as_posix()] = path.read_bytes()
    return result


def schema_bytes(root: Path) -> dict[str, bytes]:
    return {
        path.name: path.read_bytes()
        for path in sorted((root / "schemas").glob("*.schema.json"))
    }


class InstallerTests(unittest.TestCase):
    def run_cli(
        self,
        codex_home: Path,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "--codex-home",
                str(codex_home),
                *arguments,
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_fresh_repeat_check_and_vendored_engine_payload(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex"
            target = codex_home / "skills" / "readme-showcase"

            first = self.run_cli(codex_home)
            second = self.run_cli(codex_home)
            check = self.run_cli(codex_home, "--check")

            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(check.returncode, 0, check.stderr)
            self.assertEqual(json.loads(first.stdout)["status"], "installed")
            self.assertEqual(json.loads(second.stdout)["status"], "unchanged")
            self.assertEqual(json.loads(check.stdout)["status"], "current")
            expected = expected_files()
            self.assertEqual(set(expected), set(file_map(target)))
            for relative, content in expected.items():
                self.assertEqual((target / relative).read_bytes(), content)
            self.assertFalse(any(path.name == "node_modules" for path in target.rglob("*")))
            self.assertEqual(
                hashlib.sha256((target / "vendor/elkjs/lib/elk.bundled.js").read_bytes()).hexdigest(),
                "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4",
            )
            self.assertFalse(
                any(
                    path.name
                    in {
                        "engine-lock.json",
                        "package-lock.json",
                        "pnpm-lock.yaml",
                        "yarn.lock",
                    }
                    for path in target.rglob("*")
                )
            )
            self.assertEqual(
                list((codex_home / "skills").glob("readme-showcase.backup.*")),
                [],
            )

            help_result = subprocess.run(
                [
                    sys.executable,
                    str(target / "scripts" / "readme_pipeline.py"),
                    "--help",
                ],
                cwd=target,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(help_result.returncode, 0, help_result.stderr)
            self.assertIn("build-pr-bundle", help_result.stdout)
            post_run_check = self.run_cli(codex_home, "--check")
            self.assertEqual(post_run_check.returncode, 0, post_run_check.stderr)
            self.assertEqual(json.loads(post_run_check.stdout)["status"], "current")
            self.assertEqual(schema_bytes(target), schema_bytes(REPO_ROOT / "skill"))

    @unittest.skipIf(
        os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
        "npm package test runs in isolated Node lane",
    )
    def test_packed_npm_binary_installs_and_checks_skill(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = subprocess.run(
                [
                    "npm",
                    "pack",
                    "--pack-destination",
                    str(root),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(package.returncode, 0, package.stderr)
            tarballs = list(root.glob("*.tgz"))
            self.assertEqual(len(tarballs), 1)
            tarball = tarballs[0]
            project = root / "project"
            project.mkdir()
            installed = subprocess.run(
                [
                    "npm",
                    "install",
                    "--ignore-scripts",
                    "--no-audit",
                    "--no-fund",
                    str(tarball),
                ],
                cwd=project,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(installed.returncode, 0, installed.stderr)
            binary = project / "node_modules" / ".bin" / "readme-showcase"
            codex_home = root / "codex"
            environment = os.environ | {"CODEX_HOME": str(codex_home)}

            results = [
                subprocess.run(
                    [str(binary), *arguments],
                    cwd=project,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                for arguments in ((), (), ("--check",))
            ]
            for result in results:
                self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(
                [json.loads(result.stdout)["status"] for result in results],
                ["installed", "unchanged", "current"],
            )
            target = codex_home / "skills" / "readme-showcase"
            self.assertEqual(schema_bytes(target), schema_bytes(REPO_ROOT / "skill"))

            dry_run = subprocess.run(
                ["npm", "pack", "--dry-run", "--json"],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
            packed = json.loads(dry_run.stdout)
            package_record = next(iter(packed.values())) if isinstance(packed, dict) else packed[0]
            listed = {item["path"] for item in package_record["files"]}
            expected = {
                f"skill/schemas/{path.name}"
                for path in (REPO_ROOT / "skill" / "schemas").glob("*.schema.json")
            }
            self.assertEqual(listed & expected, expected)

    def test_stage_hash_mismatch_and_backup_failure_restore_old_target(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex"
            target = codex_home / "skills" / "readme-showcase"
            target.mkdir(parents=True)
            _ = (target / "SKILL.md").write_text(
                "---\nname: readme-showcase\n---\nold\n",
                encoding="utf-8",
            )
            _ = (target / "old.txt").write_text("keep\n", encoding="utf-8")
            before = file_map(target)

            def corrupt_stage(stage: Path) -> None:
                _ = (stage / "SKILL.md").write_text(
                    "corrupt\n",
                    encoding="utf-8",
                )

            with self.assertRaises(install_skill.InstallError):
                _ = install_skill.install(
                    REPO_ROOT,
                    codex_home,
                    after_stage=corrupt_stage,
                )
            self.assertEqual(file_map(target), before)
            self.assertEqual(
                list((codex_home / "skills").glob("readme-showcase.backup.*")),
                [],
            )

            def fail_after_backup(_: Path) -> None:
                raise RuntimeError("injected after backup")

            with self.assertRaises(install_skill.InstallError):
                _ = install_skill.install(
                    REPO_ROOT,
                    codex_home,
                    after_backup=fail_after_backup,
                )
            self.assertEqual(file_map(target), before)

            result = install_skill.install(REPO_ROOT, codex_home)
            backup = Path(cast(str, result["backup"]))
            self.assertEqual(result["status"], "installed")
            self.assertTrue(backup.is_dir())
            self.assertEqual(file_map(backup), before)
            self.assertEqual(
                install_skill.check_install(REPO_ROOT, codex_home)["status"],
                "current",
            )

    def test_unverified_existing_target_is_rejected_unchanged(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex"
            target = codex_home / "skills" / "readme-showcase"
            target.mkdir(parents=True)
            _ = (target / "SKILL.md").write_text(
                "name: another-skill\n",
                encoding="utf-8",
            )
            before = file_map(target)

            result = self.run_cli(codex_home)

            self.assertEqual(result.returncode, 2)
            self.assertIn("unverified existing target", result.stderr)
            self.assertEqual(file_map(target), before)

    def test_concurrent_installs_are_serialized(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            codex_home = Path(temporary) / "codex"
            target = codex_home / "skills" / "readme-showcase"
            target.mkdir(parents=True)
            _ = (target / "SKILL.md").write_text(
                "---\nname: readme-showcase\n---\nold\n",
                encoding="utf-8",
            )
            _ = (target / "old.txt").write_text("keep\n", encoding="utf-8")
            backup_ready = threading.Event()
            second_entered = threading.Event()
            results: list[dict[str, object]] = []
            errors: list[BaseException] = []

            def pause_after_backup(_: Path) -> None:
                backup_ready.set()
                self.assertTrue(second_entered.wait(2))
                time.sleep(0.1)

            def first() -> None:
                try:
                    results.append(
                        install_skill.install(
                            REPO_ROOT,
                            codex_home,
                            after_backup=pause_after_backup,
                        )
                    )
                except BaseException as exc:
                    errors.append(exc)

            def second() -> None:
                self.assertTrue(backup_ready.wait(2))
                second_entered.set()
                try:
                    results.append(install_skill.install(REPO_ROOT, codex_home))
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=first), threading.Thread(target=second)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(5)

            self.assertFalse(any(thread.is_alive() for thread in threads))
            self.assertEqual(errors, [])
            self.assertEqual(
                sorted(str(result["status"]) for result in results),
                ["installed", "unchanged"],
            )
            self.assertEqual(
                install_skill.check_install(REPO_ROOT, codex_home)["status"],
                "current",
            )


if __name__ == "__main__":
    _ = unittest.main()
