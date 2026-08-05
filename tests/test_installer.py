from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
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
_PACKAGED_SUFFIXES = {".py", ".json", ".md", ".mjs"}
_PACKAGED_ROOTS = (
    REPO_ROOT / "skill/scripts/readme_showcase/visual_kernel",
    REPO_ROOT / "skill/scripts/readme_showcase/contracts",
    REPO_ROOT / "skill/schemas",
    REPO_ROOT / "skill/references",
)


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


def expected_kernel_package_paths() -> set[str]:
    """Return the source paths that the npm package must preserve."""

    expected: set[str] = set()
    for root in _PACKAGED_ROOTS:
        for path in sorted(root.rglob("*")):
            if path.is_file() and path.suffix in _PACKAGED_SUFFIXES:
                expected.add(path.relative_to(REPO_ROOT).as_posix())
    return expected


def assert_kernel_package_paths(paths: set[str]) -> None:
    expected = expected_kernel_package_paths()
    missing = sorted(expected - paths)
    if missing:
        raise AssertionError(f"npm package omitted kernel contract files: {missing[:3]}")
    forbidden = sorted(
        path
        for path in paths
        if (
            "__pycache__" in path
            or path.endswith((".pyc", ".pyo", ".svg"))
            or "archscribe" in path.casefold()
        )
    )
    if forbidden:
        raise AssertionError(f"npm package contains forbidden kernel payloads: {forbidden[:3]}")


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

    def run_skills_cli(
        self,
        codex_home: Path,
        cwd: Path,
        action: str,
        *arguments: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(INSTALLER),
                "skills",
                action,
                "--codex-home",
                str(codex_home),
                *arguments,
            ],
            cwd=cwd,
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

    def test_skills_commands_install_check_and_update_project_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            codex_home = root / "codex"
            target = project / ".agents" / "skills" / "readme-showcase"

            installed = self.run_skills_cli(
                codex_home,
                project,
                "install",
                "--project",
                "--yes",
            )
            checked = self.run_skills_cli(codex_home, project, "check", "--project")

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(json.loads(installed.stdout)["status"], "installed")
            self.assertEqual(json.loads(installed.stdout)["scope"], "project")
            self.assertEqual(json.loads(checked.stdout)["status"], "current")
            self.assertTrue((target / "references" / "commands.md").is_file())

            skill = target / "SKILL.md"
            skill.write_text(skill.read_text(encoding="utf-8") + "\n# drift\n", encoding="utf-8")
            drift = self.run_skills_cli(codex_home, project, "check", "--project")
            updated = self.run_skills_cli(
                codex_home,
                project,
                "update",
                "--project",
                "--yes",
            )
            final = self.run_skills_cli(codex_home, project, "check", "--project")

            self.assertEqual(drift.returncode, 1, drift.stderr)
            self.assertEqual(json.loads(drift.stdout)["status"], "drift")
            self.assertEqual(updated.returncode, 0, updated.stderr)
            self.assertEqual(json.loads(updated.stdout)["status"], "updated")
            self.assertEqual(json.loads(final.stdout)["status"], "current")

    def test_skills_commands_user_scope_missing_update_and_legacy_compatibility(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            codex_home = root / "codex"

            missing = self.run_skills_cli(
                codex_home,
                project,
                "update",
                "--user",
                "--yes",
            )
            self.assertEqual(missing.returncode, 1, missing.stderr)
            self.assertEqual(json.loads(missing.stdout)["status"], "missing")
            self.assertFalse((codex_home / "skills" / "readme-showcase").exists())

            installed = self.run_skills_cli(
                codex_home,
                project,
                "install",
                "--user",
                "--yes",
            )
            checked = self.run_skills_cli(codex_home, project, "check", "--user")
            legacy_check = self.run_cli(codex_home, "--check")
            repeated = self.run_cli(codex_home)

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertEqual(json.loads(installed.stdout)["scope"], "user")
            self.assertEqual(json.loads(checked.stdout)["status"], "current")
            self.assertEqual(json.loads(legacy_check.stdout)["status"], "current")
            self.assertEqual(json.loads(repeated.stdout)["status"], "unchanged")

    def test_skills_commands_default_project_and_reject_ambiguous_scope(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            codex_home = root / "codex"

            default_install = self.run_skills_cli(
                codex_home,
                project,
                "install",
                "--yes",
            )
            user_install = self.run_skills_cli(
                codex_home,
                project,
                "install",
                "--user",
                "--yes",
            )
            ambiguous = self.run_skills_cli(codex_home, project, "check")

            self.assertEqual(default_install.returncode, 0, default_install.stderr)
            self.assertEqual(json.loads(default_install.stdout)["scope"], "project")
            self.assertEqual(user_install.returncode, 0, user_install.stderr)
            self.assertEqual(ambiguous.returncode, 2)
            self.assertIn("pass --project or --user", ambiguous.stderr)

    def test_project_scope_rejects_symlink_target_without_following_it(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            project = root / "project"
            (project / ".git").mkdir(parents=True)
            codex_home = root / "codex"
            real_target = root / "real-target"
            real_target.mkdir()
            (real_target / "SKILL.md").write_text(
                "---\nname: readme-showcase\n---\nkeep\n",
                encoding="utf-8",
            )
            before = file_map(real_target)
            link = project / ".agents" / "skills" / "readme-showcase"
            link.parent.mkdir(parents=True)
            link.symlink_to(real_target, target_is_directory=True)

            result = self.run_skills_cli(
                codex_home,
                project,
                "install",
                "--project",
                "--yes",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("unverified existing target", result.stderr)
            self.assertTrue(link.is_symlink())
            self.assertEqual(file_map(real_target), before)

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
                    "--offline",
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
            skills_check = subprocess.run(
                [
                    str(binary),
                    "skills",
                    "check",
                    "--user",
                    "--codex-home",
                    str(codex_home),
                ],
                cwd=project,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(skills_check.returncode, 0, skills_check.stderr)
            self.assertEqual(json.loads(skills_check.stdout)["status"], "current")
            target = codex_home / "skills" / "readme-showcase"
            self.assertEqual(schema_bytes(target), schema_bytes(REPO_ROOT / "skill"))

            compile_fixture = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    """
import hashlib
import json
import sys
from pathlib import Path

from scripts.readme_showcase import visual_kernel
from scripts.readme_showcase.contracts.evidence import validate_evidence_graph

fixture = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
evidence = validate_evidence_graph(fixture["evidence"])
case = next(item for item in fixture["cases"] if item["id"] == fixture["primary_case"])
compiled = visual_kernel.compile_visual(case["spec"], evidence)
assert compiled.artifacts["compiled/inventory.json"]
svg = compiled.artifacts["assets/readme-showcase/zh-Hans/desktop.svg"]
assert hashlib.sha256(svg).hexdigest()
print(f"inventory={compiled.inventory_sha256} svg_sha256={hashlib.sha256(svg).hexdigest()}")
""",
                    str(REPO_ROOT / "tests/fixtures/visual-kernel/qa-cases.v1.json"),
                ],
                cwd=target,
                env={
                    **os.environ,
                    "CODEX_HOME": str(codex_home),
                    "PYTHONPATH": os.pathsep.join((str(target), str(target / "scripts"))),
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(compile_fixture.returncode, 0, compile_fixture.stderr)
            self.assertIn("inventory=", compile_fixture.stdout)

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
            assert_kernel_package_paths(listed)

    def test_npm_package_allowlist_rejects_forbidden_temp_payloads(self) -> None:
        with tempfile.TemporaryDirectory(prefix="npm-package-clean-room-") as temporary:
            copied = Path(temporary) / "package"
            shutil.copytree(REPO_ROOT / "skill", copied / "skill")
            valid = {
                path.relative_to(copied).as_posix()
                for path in (copied / "skill").rglob("*")
                if path.is_file() and path.suffix in _PACKAGED_SUFFIXES
            }
            assert_kernel_package_paths(valid)
            for relative in (
                "skill/scripts/readme_showcase/visual_kernel/generated.svg",
                "skill/scripts/readme_showcase/visual_kernel/__pycache__/payload.pyc",
                "skill/scripts/readme_showcase/visual_kernel/archscribe-payload.json",
            ):
                candidate = copied.parent / relative
                candidate.parent.mkdir(parents=True, exist_ok=True)
                candidate.write_bytes(b"forbidden")
                valid.add(relative)
                with self.assertRaises(AssertionError):
                    assert_kernel_package_paths(valid)
                valid.remove(relative)
                candidate.unlink()

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
