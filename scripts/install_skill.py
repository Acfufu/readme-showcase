#!/usr/bin/env python3

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator, cast


class InstallError(RuntimeError):
    pass


_THREAD_LOCK = threading.Lock()


def _absolute(path: Path) -> Path:
    """Return an absolute path without resolving symlinks."""

    return Path(os.path.abspath(path.expanduser()))


@contextmanager
def _install_lock(target: Path) -> Iterator[None]:
    parent = target.parent
    parent.mkdir(parents=True, exist_ok=True)
    lock_path = parent / ".readme-showcase.install.lock"
    descriptor = os.open(
        lock_path,
        os.O_RDWR
        | os.O_CREAT
        | getattr(os, "O_NOFOLLOW", 0),
        0o600,
    )
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise InstallError(f"invalid install lock: {lock_path}")
        with _THREAD_LOCK:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(descriptor, fcntl.LOCK_UN)
    finally:
        os.close(descriptor)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest_sha256(manifest: dict[str, str]) -> str:
    raw = json.dumps(
        manifest,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _skip_source(path: Path) -> bool:
    return (
        "__pycache__" in path.parts
        or path.suffix == ".pyc"
        or path.name == ".DS_Store"
    )


def _source_files(repo_root: Path) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for source, prefix in (
        (repo_root / "skill", Path()),
        (repo_root / "dataset", Path("dataset")),
    ):
        if source.is_symlink() or not source.is_dir():
            raise InstallError(f"invalid source directory: {source}")
        for path in sorted(source.rglob("*")):
            if path.is_symlink():
                raise InstallError(f"source symlink is not allowed: {path}")
            if path.is_file() and not _skip_source(path):
                result[prefix / path.relative_to(source)] = path

    required = {
        Path(".nvmrc"),
        Path("SKILL.md"),
        Path("agents/openai.yaml"),
        Path("scripts/readme_pipeline.py"),
        Path("scripts/render_elk.mjs"),
        Path("vendor/elkjs/lib/elk.bundled.js"),
        Path("vendor/elkjs/package.json"),
        Path("vendor/elkjs/LICENSE.md"),
        Path("dataset/retrieval/manifest.json"),
    }
    missing = sorted(required - set(result))
    if missing:
        raise InstallError(f"source package is incomplete: {missing[0]}")

    skill_text = result[Path("SKILL.md")].read_text(encoding="utf-8")
    if re.search(r"(?m)^name:\s*readme-showcase\s*$", skill_text) is None:
        raise InstallError("source SKILL.md has unexpected identity")
    return result


def _expected_manifest(files: dict[Path, Path]) -> dict[str, str]:
    return {
        relative.as_posix(): _file_sha256(source)
        for relative, source in sorted(files.items())
    }


def tree_manifest(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            raise InstallError(f"installed symlink is not allowed: {path}")
        if path.is_file() and not _skip_source(path):
            result[path.relative_to(root).as_posix()] = _file_sha256(path)
    return result


def _validate_existing_target(target: Path) -> None:
    if target.is_symlink() or not target.is_dir():
        raise InstallError(f"unverified existing target: {target}")
    skill_path = target / "SKILL.md"
    try:
        skill_text = skill_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise InstallError(f"unverified existing target: {target}") from exc
    if re.search(r"(?m)^name:\s*readme-showcase\s*$", skill_text) is None:
        raise InstallError(f"unverified existing target: {target}")
    _ = tree_manifest(target)


def _copy_source(files: dict[Path, Path], stage: Path) -> None:
    for relative, source in sorted(files.items()):
        destination = stage / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        _ = shutil.copy2(source, destination)


def _result(
    status: str,
    target: Path,
    manifest: dict[str, str],
    backup: Path | None = None,
) -> dict[str, object]:
    return {
        "backup": None if backup is None else str(backup),
        "files": len(manifest),
        "sha256": _manifest_sha256(manifest),
        "status": status,
        "target": str(target),
    }


def _install_unlocked(
    repo_root: Path,
    target: Path,
    *,
    after_stage: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    target = _absolute(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    files = _source_files(repo_root)
    expected = _expected_manifest(files)

    if target.exists() or target.is_symlink():
        _validate_existing_target(target)

    stage = Path(
        tempfile.mkdtemp(
            prefix=".readme-showcase.stage.",
            dir=target.parent,
        )
    )
    backup: Path | None = None
    installed = False
    try:
        _copy_source(files, stage)
        if after_stage is not None:
            after_stage(stage)
        if tree_manifest(stage) != expected:
            raise InstallError("staged package hash mismatch")

        if target.exists():
            current = tree_manifest(target)
            if current == expected:
                return _result("unchanged", target, expected)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            backup = target.with_name(
                f"readme-showcase.backup.{stamp}.{_manifest_sha256(current)[:12]}"
            )
            if backup.exists() or backup.is_symlink():
                raise InstallError(f"backup already exists: {backup}")
            os.replace(target, backup)
            if after_backup is not None:
                after_backup(backup)

        os.replace(stage, target)
        installed = True
        if tree_manifest(target) != expected:
            raise InstallError("installed package hash mismatch")
        return _result("installed", target, expected, backup)
    except BaseException as exc:
        try:
            if backup is not None and backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                os.replace(backup, target)
            elif installed and target.exists():
                shutil.rmtree(target)
        except Exception as rollback_exc:
            raise InstallError(
                f"install failed and rollback failed; backup={backup}: {rollback_exc}"
            ) from exc
        if isinstance(exc, InstallError):
            raise
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise InstallError(str(exc)) from exc
    finally:
        if stage.exists():
            shutil.rmtree(stage)


def install_target(
    repo_root: Path,
    target: Path,
    *,
    after_stage: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    target = _absolute(target)
    with _install_lock(target):
        return _install_unlocked(
            repo_root,
            target,
            after_stage=after_stage,
            after_backup=after_backup,
        )


def install(
    repo_root: Path,
    codex_home: Path,
    *,
    after_stage: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    codex_home = codex_home.expanduser().resolve()
    return install_target(
        repo_root,
        codex_home / "skills" / "readme-showcase",
        after_stage=after_stage,
        after_backup=after_backup,
    )


def check_target(repo_root: Path, target: Path) -> dict[str, object]:
    repo_root = repo_root.resolve()
    target = _absolute(target)
    files = _source_files(repo_root)
    expected = _expected_manifest(files)
    if not target.exists() and not target.is_symlink():
        return _result("missing", target, expected)
    _validate_existing_target(target)
    status = "current" if tree_manifest(target) == expected else "drift"
    return _result(status, target, expected)


def check_install(repo_root: Path, codex_home: Path) -> dict[str, object]:
    codex_home = codex_home.expanduser().resolve()
    return check_target(repo_root, codex_home / "skills" / "readme-showcase")


def _project_root(start: Path) -> Path | None:
    current = start.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return None


def _scope_target(
    scope: str,
    *,
    project_root: Path | None,
    codex_home: Path,
) -> Path:
    if scope == "project":
        if project_root is None:
            raise InstallError("project scope requires a Git repository")
        return project_root / ".agents" / "skills" / "readme-showcase"
    return codex_home / "skills" / "readme-showcase"


def _select_scope(
    action: str,
    *,
    requested: str | None,
    yes: bool,
    project_root: Path | None,
    codex_home: Path,
) -> str:
    if requested is not None:
        return requested

    project_target = (
        None
        if project_root is None
        else _scope_target("project", project_root=project_root, codex_home=codex_home)
    )
    user_target = _scope_target("user", project_root=project_root, codex_home=codex_home)
    project_exists = project_target is not None and project_target.exists()
    user_exists = user_target.exists()
    if project_exists and user_exists:
        if yes or not sys.stdin.isatty():
            raise InstallError("readme-showcase exists in project and user scopes; pass --project or --user")
        answer = input("Install scope [project/user] (project): ").strip().lower()
        return "user" if answer in {"u", "user", "global", "home"} else "project"
    if project_exists:
        return "project"
    if user_exists:
        return "user"

    if action == "install" and not yes and sys.stdin.isatty():
        default = "project" if project_root is not None else "user"
        answer = input(f"Install scope [project/user] ({default}): ").strip().lower()
        if answer in {"u", "user", "global", "home"}:
            return "user"
        if answer in {"p", "project", "local", "repo"}:
            return "project"
        return default
    return "project" if project_root is not None else "user"


def _run_skills(arguments: list[str], repo_root: Path) -> int:
    parser = argparse.ArgumentParser(
        prog="readme-showcase skills",
        description="Install, update, or check the readme-showcase Skill.",
    )
    subcommands = parser.add_subparsers(dest="action", required=True)
    for action in ("install", "update", "check"):
        command = subcommands.add_parser(action)
        scope = command.add_mutually_exclusive_group()
        _ = scope.add_argument("--project", action="store_true")
        _ = scope.add_argument("--user", action="store_true")
        _ = command.add_argument("--codex-home", type=Path)
        if action != "check":
            _ = command.add_argument("--yes", "-y", action="store_true")
    parsed = parser.parse_args(arguments)
    action = cast(str, parsed.action)
    requested = "project" if parsed.project else "user" if parsed.user else None
    yes = cast(bool, getattr(parsed, "yes", False))
    codex_home = cast(
        Path,
        parsed.codex_home
        or Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")),
    ).expanduser().resolve()
    project_root = _project_root(Path.cwd())
    scope = _select_scope(
        action,
        requested=requested,
        yes=yes,
        project_root=project_root,
        codex_home=codex_home,
    )
    target = _scope_target(scope, project_root=project_root, codex_home=codex_home)

    if action == "check":
        result = check_target(repo_root, target)
    elif action == "update":
        current = check_target(repo_root, target)
        if current["status"] == "missing":
            result = current
        else:
            result = install_target(repo_root, target)
            result["status"] = "updated" if result["status"] == "installed" else "current"
    else:
        result = install_target(repo_root, target)
        if result["status"] == "unchanged":
            result["status"] = "current"

    output = {"command": action, "scope": scope, **result}
    print(json.dumps(output, sort_keys=True, separators=(",", ":")))
    return 1 if output["status"] in {"missing", "drift"} else 0


def _run_legacy(arguments: list[str], repo_root: Path) -> int:
    default_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser = argparse.ArgumentParser(
        description="Atomically install the readme-showcase Skill.",
    )
    _ = parser.add_argument("--codex-home", type=Path, default=default_home)
    _ = parser.add_argument("--check", action="store_true")
    parsed = parser.parse_args(arguments)
    codex_home = cast(Path, parsed.codex_home)
    check = cast(bool, parsed.check)
    result = (
        check_install(repo_root, codex_home)
        if check
        else install(repo_root, codex_home)
    )
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] not in {"missing", "drift"} else 1


def main() -> int:
    arguments = sys.argv[1:]
    repo_root = Path(__file__).resolve().parents[1]
    try:
        return (
            _run_skills(arguments[1:], repo_root)
            if arguments[:1] == ["skills"]
            else _run_legacy(arguments, repo_root)
        )
    except InstallError as exc:
        print(f"install_skill: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
