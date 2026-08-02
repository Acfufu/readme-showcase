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


@contextmanager
def _install_lock(codex_home: Path) -> Iterator[None]:
    parent = codex_home / "skills"
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

    builder = repo_root / "scripts" / "build_glyphic_engine_lock.py"
    if builder.is_symlink() or not builder.is_file():
        raise InstallError(f"invalid source file: {builder}")
    result[Path("scripts/build_glyphic_engine_lock.py")] = builder

    required = {
        Path("SKILL.md"),
        Path("agents/openai.yaml"),
        Path("scripts/readme_pipeline.py"),
        Path("scripts/render_glyphic.mjs"),
        Path("dataset/retrieval/manifest.json"),
        Path("scripts/build_glyphic_engine_lock.py"),
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
    codex_home: Path,
    *,
    after_stage: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    repo_root = repo_root.resolve()
    codex_home = codex_home.expanduser().resolve()
    target = codex_home / "skills" / "readme-showcase"
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


def install(
    repo_root: Path,
    codex_home: Path,
    *,
    after_stage: Callable[[Path], None] | None = None,
    after_backup: Callable[[Path], None] | None = None,
) -> dict[str, object]:
    codex_home = codex_home.expanduser().resolve()
    with _install_lock(codex_home):
        return _install_unlocked(
            repo_root,
            codex_home,
            after_stage=after_stage,
            after_backup=after_backup,
        )


def check_install(repo_root: Path, codex_home: Path) -> dict[str, object]:
    repo_root = repo_root.resolve()
    codex_home = codex_home.expanduser().resolve()
    target = codex_home / "skills" / "readme-showcase"
    files = _source_files(repo_root)
    expected = _expected_manifest(files)
    if not target.exists() and not target.is_symlink():
        return _result("missing", target, expected)
    _validate_existing_target(target)
    status = "current" if tree_manifest(target) == expected else "drift"
    return _result(status, target, expected)


def main() -> int:
    default_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser = argparse.ArgumentParser(
        description="Atomically install the readme-showcase Skill.",
    )
    _ = parser.add_argument("--codex-home", type=Path, default=default_home)
    _ = parser.add_argument("--check", action="store_true")
    arguments = parser.parse_args()
    codex_home = cast(Path, arguments.codex_home)
    check = cast(bool, arguments.check)
    repo_root = Path(__file__).resolve().parents[1]
    try:
        result = (
            check_install(repo_root, codex_home)
            if check
            else install(repo_root, codex_home)
        )
    except InstallError as exc:
        print(f"install_skill: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 0 if result["status"] not in {"missing", "drift"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
