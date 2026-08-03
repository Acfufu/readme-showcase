from __future__ import annotations

import hashlib
import heapq
import importlib
import os
import stat
import time
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

ContractError = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
).ContractError
from .git import base_sha, tracked_paths, tracked_state
from .index import _regular_file, build_file_index
from .policies import (
    FIXED_EXCLUDED_DIRECTORIES,
    SECRET_NAMES,
    SECRET_SUFFIXES,
    assert_scanner_policy_unchanged,
    is_secret_path,
    scanner_policy_snapshot,
)

MAX_FILES = 2000
MAX_DIRECTORIES = 500
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 12
MAX_SECONDS = 5
EXCLUDED_DIRECTORIES = FIXED_EXCLUDED_DIRECTORIES
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
    state = tracked_state(canonical_root)
    if state is None:
        raise ContractError("E_SCAN_ROOT", "tracked file index requires a Git repository")
    base, paths = state
    return {"base_sha": base, "files": build_file_index(canonical_root, paths)}


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
    policy_snapshot = scanner_policy_snapshot(canonical_root)
    policy = policy_snapshot.policy
    limits = (
        ScanLimits(
            files=policy.limits.indexed_files,
            total_bytes=policy.limits.total_bytes,
            seconds=policy.limits.seconds,
        )
        if policy is not None
        else limits or ScanLimits()
    )
    tracked = tracked_paths(canonical_root)
    assert_scanner_policy_unchanged(canonical_root, policy_snapshot)
    if policy is not None:
        allowed = set(tracked or ()) if policy.tracked_only else None
    else:
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

    def finish(packet: dict[str, object]) -> dict[str, object]:
        assert_scanner_policy_unchanged(canonical_root, policy_snapshot)
        return packet

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
                return finish(_incomplete(canonical_root, limits, "E_SCAN_TIME", relative))
            if len(entry.relative_to(canonical_root).parts) - 1 > limits.depth:
                return finish(_incomplete(canonical_root, limits, "E_SCAN_DEPTH", relative))
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
                    return finish(_incomplete(canonical_root, limits, "E_SCAN_DIRECTORY_COUNT", relative))
                children.append(entry)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                warnings.append({"code": "W_SCAN_SPECIAL", "path": relative})
                continue
            seen_files += 1
            if seen_files > limits.files:
                return finish(_incomplete(canonical_root, limits, "E_SCAN_FILE_COUNT", relative))
            if policy is not None and not policy.selects(relative):
                continue
            if is_secret_path(relative):
                warnings.append({"code": "W_SCAN_SECRET", "path": relative})
                continue
            if entry.suffix.lower() in BINARY_SUFFIXES:
                warnings.append({"code": "W_SCAN_BINARY", "path": relative})
                continue
            if policy is not None and len(files) >= policy.limits.content_files:
                return finish(_incomplete(canonical_root, limits, "E_SCAN_FILE_COUNT", relative))
            if entry_stat.st_size > limits.file_bytes:
                return finish(_incomplete(canonical_root, limits, "E_SCAN_FILE_SIZE", relative))
            total_bytes += entry_stat.st_size
            if total_bytes > limits.total_bytes:
                return finish(_incomplete(canonical_root, limits, "E_SCAN_TOTAL_SIZE", relative))
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
    return finish({
        "schema_version": 1,
        "status": "complete",
        "target": {"name": canonical_root.name, "base_sha": base_sha(canonical_root)},
        "scan_limits": limits.as_dict(),
        "files": files,
        "facts": facts,
        "warnings": warnings,
    })


def _v2_limits(limits: ScanLimits | None, policy: object | None) -> tuple[ScanLimits, int]:
    if limits is None and policy is not None:
        profile_limits = policy.limits
        limits = ScanLimits(
            files=profile_limits.content_files,
            total_bytes=profile_limits.total_bytes,
            seconds=profile_limits.seconds,
        )
        indexed_files = profile_limits.indexed_files
    else:
        limits = limits or ScanLimits()
        indexed_files = limits.files
    values = limits.as_dict()
    maxima = ScanLimits(files=100_000, total_bytes=64 * 1024 * 1024, seconds=60).as_dict()
    for key, value in values.items():
        if type(value) is not int or value < 1 or value > maxima[key]:
            raise ContractError("E_SCANNER_CONFIG", f"{key} must be a positive integer within the hard maximum")
    return limits, indexed_files


def _v2_paths(root: Path, tracked: tuple[str, ...] | None, tracked_only: bool) -> Iterator[str]:
    if tracked_only and tracked is not None:
        yield from tracked
        return
    pending: list[tuple[bytes, Path]] = []
    try:
        entries = root.iterdir()
        for entry in entries:
            heapq.heappush(pending, (os.fsencode(entry.relative_to(root).as_posix()), entry))
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot list .: {exc}") from exc
    while pending:
        _, entry = heapq.heappop(pending)
        relative = entry.relative_to(root).as_posix()
        try:
            info = entry.lstat()
        except OSError as exc:
            raise ContractError("E_SCAN_IO", f"cannot inspect {relative}: {exc}") from exc
        if stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode):
            if entry.name in EXCLUDED_DIRECTORIES:
                continue
            try:
                marker = (entry / ".git").lstat()
            except FileNotFoundError:
                try:
                    children = entry.iterdir()
                    for child in children:
                        child_relative = child.relative_to(root).as_posix()
                        heapq.heappush(pending, (os.fsencode(child_relative), child))
                except OSError as exc:
                    raise ContractError("E_SCAN_IO", f"cannot list {relative}: {exc}") from exc
            except OSError as exc:
                raise ContractError("E_SCAN_IO", f"cannot inspect {relative}/.git: {exc}") from exc
            else:
                if stat.S_ISDIR(marker.st_mode) or stat.S_ISREG(marker.st_mode):
                    yield relative
            continue
        yield relative


def _v2_skip(path: str, reason: str) -> dict[str, object]:
    return {"path": path, "reason": reason, "required_for_generation": False}


def _scan_contract() -> object:
    return importlib.import_module(
        "skill.scripts.readme_showcase.contracts.scan"
        if __package__.startswith("skill.")
        else "readme_showcase.contracts.scan"
    )


def scan_repository_v2(
    root: Path,
    project_type: str = "unknown",
    *,
    limits: ScanLimits | None = None,
    clock: object = time.monotonic,
) -> dict[str, object]:
    contract = _scan_contract()
    if project_type not in contract.PROJECT_TYPES:
        raise ContractError("E_SCAN_PROJECT_TYPE", "project_type is unsupported")
    if not callable(clock):
        raise ContractError("E_SCANNER_CONFIG", "clock must be callable")
    canonical_root = _root(root)
    policy_snapshot = scanner_policy_snapshot(canonical_root)
    policy = policy_snapshot.policy
    limits, indexed_cap = _v2_limits(limits, policy)
    state = tracked_state(canonical_root)
    base, tracked = state if state is not None else (None, None)
    tracked_set = set(tracked or ())
    assert_scanner_policy_unchanged(canonical_root, policy_snapshot)
    tracked_only = policy.tracked_only if policy is not None else tracked is not None
    started = clock()
    if not isinstance(started, (int, float)):
        raise ContractError("E_SCANNER_CONFIG", "clock must return seconds")

    files: list[dict[str, object]] = []
    skipped: list[dict[str, object]] = []
    indexed_files = 0
    total_bytes = 0
    for relative in _v2_paths(canonical_root, tracked, tracked_only):
        indexed_files += 1
        if indexed_files > indexed_cap:
            skipped.append(_v2_skip(relative, "file-count-limit"))
            break
        if policy is not None and not policy.selects(relative):
            continue
        now = clock()
        if not isinstance(now, (int, float)):
            raise ContractError("E_SCANNER_CONFIG", "clock must return seconds")
        if now - started > limits.seconds:
            skipped.append(_v2_skip(relative, "time-limit"))
            break
        entry = canonical_root.joinpath(*relative.split("/"))
        try:
            entry_stat = entry.lstat()
        except OSError:
            skipped.append(_v2_skip(relative, "race"))
            continue
        if stat.S_ISLNK(entry_stat.st_mode):
            skipped.append(_v2_skip(relative, "symlink"))
            continue
        if stat.S_ISDIR(entry_stat.st_mode):
            skipped.append(_v2_skip(relative, "submodule"))
            continue
        if not stat.S_ISREG(entry_stat.st_mode):
            skipped.append(_v2_skip(relative, "special-file"))
            continue
        if is_secret_path(relative):
            skipped.append(_v2_skip(relative, "secret"))
            continue
        if entry.suffix.lower() in BINARY_SUFFIXES:
            skipped.append(_v2_skip(relative, "binary"))
            continue
        if relative in tracked_set:
            try:
                _regular_file(canonical_root, relative)
            except ContractError as exc:
                if exc.code not in {"E_SCAN_IO", "E_SCAN_RACE"}:
                    raise
                skipped.append(_v2_skip(relative, "race"))
                continue
        if len(files) >= limits.files:
            skipped.append(_v2_skip(relative, "file-count-limit"))
            break
        if entry_stat.st_size > limits.file_bytes:
            skipped.append(_v2_skip(relative, "file-size-limit"))
            break
        if total_bytes + entry_stat.st_size > limits.total_bytes:
            skipped.append(_v2_skip(relative, "total-size-limit"))
            break
        try:
            raw = _read(entry, entry_stat, relative, limits.file_bytes)
        except ContractError as exc:
            if exc.code not in {"E_SCAN_IO", "E_SCAN_RACE"}:
                raise
            skipped.append(_v2_skip(relative, "race"))
            continue
        if b"\0" in raw:
            skipped.append(_v2_skip(relative, "binary"))
            continue
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError:
            skipped.append(_v2_skip(relative, "invalid-utf8"))
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
        total_bytes += len(raw)

    files.sort(key=lambda item: os.fsencode(str(item["path"])))
    paths = [str(item["path"]) for item in files]
    required, satisfied, missing = contract.minimum_evidence(project_type, paths)
    missing_set = set(missing)
    for item in skipped:
        item["required_for_generation"] = bool(contract.evidence_categories(str(item["path"])) & missing_set)
    existing = {str(item["path"]) for item in skipped}
    for category in missing:
        path = f"_required/{category}"
        if path not in existing:
            skipped.append({"path": path, "reason": "required-evidence-missing", "required_for_generation": True})
    skipped.sort(key=lambda item: os.fsencode(str(item["path"])))
    status = "incomplete" if missing else "partial" if skipped else "complete"
    allowed_consumers = ["audit"] if project_type == "unknown" and status != "complete" else ["audit", "readme"]
    if status == "complete":
        allowed_consumers.append("publish")
    facts = [
        {
            "fact_id": f"file:{item['path']}",
            "kind": "repository-file",
            "path": item["path"],
            "evidence_sha256": item["sha256"],
        }
        for item in files
    ]
    if state is not None and tracked_state(canonical_root) != state:
        raise ContractError("E_SCAN_RACE", "Git HEAD or index changed during repository scan")
    assert_scanner_policy_unchanged(canonical_root, policy_snapshot)
    packet = {
        "schema_version": 2,
        "status": status,
        "target": {"name": canonical_root.name, "base_sha": base},
        "scan_limits": limits.as_dict(),
        "project_type": project_type,
        "coverage": {
            "tracked_files": len(tracked) if tracked is not None else indexed_files,
            "indexed_files": indexed_files,
            "selected_files": len(files) + len(skipped),
            "content_files": len(files),
            "skipped_files": len(skipped),
            "content_bytes": total_bytes,
        },
        "files": files,
        "facts": facts,
        "skipped": skipped,
        "warnings": [],
        "policy": {
            "required_evidence": required,
            "satisfied_evidence": satisfied,
            "missing_evidence": missing,
            "allowed_consumers": sorted(allowed_consumers),
            "publish_eligible": status == "complete",
        },
    }
    return contract.validate_repository_scan_v2(packet)
