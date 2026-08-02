from __future__ import annotations

import hashlib
import importlib
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

ContractError = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
).ContractError
from .git import base_sha, tracked_paths
from .index import build_file_index


MAX_FILES = 2000
MAX_DIRECTORIES = 500
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 12
MAX_SECONDS = 5
EXCLUDED_DIRECTORIES = frozenset(
    {
        ".agents", ".claude", ".codex", ".cursor", ".git", ".hg", ".omo",
        ".svn", ".trellis", ".venv", "__pycache__", "build", "dist",
        "evaluation-only", "node_modules", "vendor", "venv",
    }
)
SECRET_NAMES = frozenset(
    {".env", ".env.local", "credentials", "credentials.json", "id_dsa", "id_ed25519", "id_rsa"}
)
SECRET_SUFFIXES = frozenset({".key", ".p12", ".pem"})
BINARY_SUFFIXES = frozenset(
    {".avi", ".gif", ".gz", ".ico", ".jpeg", ".jpg", ".mov", ".mp4", ".pdf", ".png", ".tar", ".webm", ".webp", ".woff", ".woff2", ".zip"}
)


@dataclass(frozen=True)
class ScanLimits:
    files: int = MAX_FILES
    directories: int = MAX_DIRECTORIES
    file_bytes: int = MAX_FILE_BYTES
    total_bytes: int = MAX_TOTAL_BYTES
    depth: int = MAX_DEPTH
    seconds: int = MAX_SECONDS

    def as_dict(self) -> dict[str, int]:
        return {
            "max_depth": self.depth,
            "max_directories": self.directories,
            "max_file_bytes": self.file_bytes,
            "max_files": self.files,
            "max_seconds": self.seconds,
            "max_total_bytes": self.total_bytes,
        }


def tracked_file_index(root: Path) -> dict[str, object]:
    canonical_root = _root(root)
    paths = tracked_paths(canonical_root)
    if paths is None:
        raise ContractError("E_SCAN_ROOT", "tracked file index requires a Git repository")
    return {"base_sha": base_sha(canonical_root), "files": build_file_index(canonical_root, paths)}


def _root(root: Path) -> Path:
    try:
        initial = root.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_SCAN_ROOT", f"scan root not found: {root}") from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISDIR(initial.st_mode):
        raise ContractError("E_SCAN_ROOT", "scan root must be a real directory")
    return root.resolve(strict=True)


def _incomplete(root: Path, limits: ScanLimits, code: str, path: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "incomplete",
        "target": {"name": root.name, "base_sha": base_sha(root)},
        "scan_limits": limits.as_dict(),
        "files": [],
        "facts": [],
        "warnings": [{"code": code, "path": path}],
    }


def _read(entry: Path, expected: os.stat_result, relative: str, maximum: int) -> bytes:
    try:
        descriptor = os.open(
            entry,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot open {relative}: {exc}") from exc
    try:
        actual = os.fstat(descriptor)
        expected_values = (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)
        actual_values = (actual.st_dev, actual.st_ino, actual.st_size, actual.st_mtime_ns)
        if not stat.S_ISREG(actual.st_mode) or actual_values != expected_values:
            raise ContractError("E_SCAN_RACE", f"file changed during scan: {relative}")
        chunks, remaining = [], maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            len(raw) > maximum
            or len(raw) != actual.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != actual_values
        ):
            raise ContractError("E_SCAN_RACE", f"file changed during scan: {relative}")
        return raw
    finally:
        os.close(descriptor)


def scan_repository_v1(root: Path, limits: ScanLimits | None = None) -> dict[str, object]:
    canonical_root = _root(root)
    limits = limits or ScanLimits()
    tracked = tracked_paths(canonical_root)
    allowed = set(tracked) if tracked is not None else None
    allowed_directories = {
        "/".join(path.split("/")[:index])
        for path in (tracked or ())
        for index in range(1, len(path.split("/")))
    }
    started = time.monotonic()
    files: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    seen_files = seen_directories = total_bytes = 0
    pending = [canonical_root]
    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: os.fsencode(item.name))
        except OSError as exc:
            relative = directory.relative_to(canonical_root).as_posix() or "."
            raise ContractError("E_SCAN_IO", f"cannot list {relative}: {exc}") from exc
        children: list[Path] = []
        for entry in entries:
            relative = entry.relative_to(canonical_root).as_posix()
            if allowed is not None and relative not in allowed and relative not in allowed_directories:
                continue
            if time.monotonic() - started > limits.seconds:
                return _incomplete(canonical_root, limits, "E_SCAN_TIME", relative)
            if len(entry.relative_to(canonical_root).parts) - 1 > limits.depth:
                return _incomplete(canonical_root, limits, "E_SCAN_DEPTH", relative)
            try:
                entry_stat = entry.lstat()
            except OSError as exc:
                raise ContractError("E_SCAN_IO", f"cannot inspect {relative}: {exc}") from exc
            if stat.S_ISLNK(entry_stat.st_mode):
                warnings.append({"code": "W_SCAN_SYMLINK", "path": relative})
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if entry.name in EXCLUDED_DIRECTORIES:
                    continue
                marker = entry / ".git"
                try:
                    marker.lstat()
                except FileNotFoundError:
                    pass
                except OSError as exc:
                    raise ContractError("E_SCAN_IO", f"cannot inspect {relative}/.git: {exc}") from exc
                else:
                    warnings.append({"code": "W_SCAN_SUBMODULE", "path": relative})
                    continue
                seen_directories += 1
                if seen_directories > limits.directories:
                    return _incomplete(canonical_root, limits, "E_SCAN_DIRECTORY_COUNT", relative)
                children.append(entry)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                warnings.append({"code": "W_SCAN_SPECIAL", "path": relative})
                continue
            seen_files += 1
            if seen_files > limits.files:
                return _incomplete(canonical_root, limits, "E_SCAN_FILE_COUNT", relative)
            if entry.name.lower() in SECRET_NAMES or entry.suffix.lower() in SECRET_SUFFIXES:
                warnings.append({"code": "W_SCAN_SECRET", "path": relative})
                continue
            if entry.suffix.lower() in BINARY_SUFFIXES:
                warnings.append({"code": "W_SCAN_BINARY", "path": relative})
                continue
            if entry_stat.st_size > limits.file_bytes:
                return _incomplete(canonical_root, limits, "E_SCAN_FILE_SIZE", relative)
            total_bytes += entry_stat.st_size
            if total_bytes > limits.total_bytes:
                return _incomplete(canonical_root, limits, "E_SCAN_TOTAL_SIZE", relative)
            raw = _read(entry, entry_stat, relative, limits.file_bytes)
            if b"\0" in raw:
                warnings.append({"code": "W_SCAN_BINARY", "path": relative})
                continue
            try:
                content = raw.decode("utf-8")
            except UnicodeDecodeError:
                warnings.append({"code": "W_SCAN_INVALID_UTF8", "path": relative})
                continue
            files.append(
                {
                    "path": relative,
                    "bytes": len(raw),
                    "lines": len(content.splitlines()),
                    "sha256": hashlib.sha256(raw).hexdigest(),
                    "content": content,
                }
            )
        pending.extend(reversed(children))
    files.sort(key=lambda item: os.fsencode(str(item["path"])))
    warnings.sort(key=lambda item: (os.fsencode(item["path"]), item["code"]))
    facts = [
        {
            "fact_id": f"file:{item['path']}",
            "kind": "repository-file",
            "path": item["path"],
            "evidence_sha256": item["sha256"],
        }
        for item in files
    ]
    return {
        "schema_version": 1,
        "status": "complete",
        "target": {"name": canonical_root.name, "base_sha": base_sha(canonical_root)},
        "scan_limits": limits.as_dict(),
        "files": files,
        "facts": facts,
        "warnings": warnings,
    }
