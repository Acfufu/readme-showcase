from __future__ import annotations

import hashlib
import importlib
import os
import re
import stat
import subprocess
import tempfile
from pathlib import Path, PurePosixPath

_CONTRACTS = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
read_regular_bytes = _CONTRACTS.read_regular_bytes


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_MAX_GIT_OUTPUT_BYTES = 1024 * 1024
_MAX_INDEX_BYTES = 16 * 1024 * 1024
_STATE_ATTEMPTS = 2


def _fail(message: str) -> None:
    raise ContractError("E_SCAN_IO", message)


def git_directory(root: Path) -> Path | None:
    marker = root / ".git"
    try:
        info = marker.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot inspect .git: {exc}") from exc
    if stat.S_ISLNK(info.st_mode):
        _fail(".git must not be a symlink")
    if stat.S_ISDIR(info.st_mode):
        return marker.resolve(strict=True)
    if not stat.S_ISREG(info.st_mode):
        _fail(".git must be a directory or regular gitfile")
    try:
        value = read_regular_bytes(marker, maximum=4096, path_code="E_SCAN_IO").decode("utf-8").strip()
    except UnicodeDecodeError as exc:
        raise ContractError("E_SCAN_IO", ".git file must be UTF-8") from exc
    if not value.startswith("gitdir: ") or "\0" in value or "\n" in value or "\r" in value:
        _fail(".git file is invalid")
    target = Path(value[8:])
    if not target.is_absolute():
        target = marker.parent / target
    try:
        target_info = target.lstat()
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot inspect git directory: {exc}") from exc
    if stat.S_ISLNK(target_info.st_mode) or not stat.S_ISDIR(target_info.st_mode):
        _fail("git directory must be a real directory")
    return target.resolve(strict=True)


def _git_output(root: Path, *arguments: str) -> bytes:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", LC_ALL="C")
    try:
        with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
            result = subprocess.run(
                [
                    "git",
                    "--no-optional-locks",
                    "-c",
                    "core.fsmonitor=false",
                    "-c",
                    "core.hooksPath=/dev/null",
                    "-c",
                    "credential.helper=",
                    "-C",
                    str(root),
                    *arguments,
                ],
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                check=False,
                timeout=10,
                env=environment,
            )
            output_size = os.fstat(stdout.fileno()).st_size
            error_size = os.fstat(stderr.fileno()).st_size
            if output_size > _MAX_GIT_OUTPUT_BYTES or error_size > _MAX_GIT_OUTPUT_BYTES:
                _fail("local Git inspection output exceeds limit")
            stdout.seek(0)
            output = stdout.read()
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ContractError("E_SCAN_IO", "local Git inspection is unavailable") from exc
    if result.returncode:
        _fail(f"local Git inspection failed: {arguments[0]}")
    return output


def _base_sha(root: Path) -> str:
    value = _git_output(root, "rev-parse", "--verify", "HEAD").decode("ascii", "strict").strip()
    if not _COMMIT.fullmatch(value):
        _fail("Git HEAD is not a commit")
    return value


def base_sha(root: Path) -> str | None:
    return None if git_directory(root) is None else _base_sha(root)


def _paths(output: bytes) -> tuple[str, ...]:
    if output and not output.endswith(b"\0"):
        _fail("Git tracked-file output is not NUL terminated")
    paths: list[tuple[bytes, str]] = []
    for raw in output.split(b"\0")[:-1]:
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("E_SCAN_IO", "Git index path must be UTF-8") from exc
        path = PurePosixPath(value)
        if (
            not value
            or "\\" in value
            or path.is_absolute()
            or "." in path.parts
            or ".." in path.parts
            or path.as_posix() != value
        ):
            _fail("Git index contains an unsafe path")
        paths.append((raw, value))
    paths.sort(key=lambda item: item[0])
    return tuple(value for _, value in paths)


def tracked_paths(root: Path) -> tuple[str, ...] | None:
    if git_directory(root) is None:
        return None
    return _paths(_git_output(root, "ls-files", "-z", "--cached"))


def _index_snapshot(git_dir: Path) -> bytes:
    try:
        raw = read_regular_bytes(
            git_dir / "index",
            maximum=_MAX_INDEX_BYTES,
            path_code="E_SCAN_IO",
            size_code="E_SCAN_IO",
        )
    except ContractError as exc:
        raise ContractError("E_SCAN_IO", "Git index must be a bounded regular file") from exc
    return hashlib.sha256(raw).digest()


def tracked_state(root: Path) -> tuple[str, tuple[str, ...]] | None:
    git_dir = git_directory(root)
    if git_dir is None:
        return None
    for _ in range(_STATE_ATTEMPTS):
        base_before = _base_sha(root)
        index_before = _index_snapshot(git_dir)
        paths = _paths(_git_output(root, "ls-files", "-z", "--cached"))
        index_after = _index_snapshot(git_dir)
        base_after = _base_sha(root)
        if base_before == base_after and index_before == index_after:
            return base_before, paths
    raise ContractError("E_SCAN_RACE", "Git HEAD or index changed during tracked-file scan")
