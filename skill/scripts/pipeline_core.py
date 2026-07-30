from __future__ import annotations

import importlib
import hashlib
import os
import re
import stat
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

_CONTRACTS = importlib.import_module(
    "pipeline_contracts"
    if __package__ in (None, "")
    else "skill.scripts.pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
canonical_sha256 = _CONTRACTS.canonical_sha256
validate_contract = _CONTRACTS.validate_contract


_DATASET_FIELDS = {
    "schema_version",
    "dataset_id",
    "dataset_revision",
    "purpose",
    "records",
}
_RECORD_FIELDS = {
    "record_id",
    "project_types",
    "section_intents",
    "tags",
    "pattern",
    "source",
    "split",
}
_PATTERN_FIELDS = {"summary", "structure", "proof"}
_SOURCE_FIELDS = {
    "repository_url",
    "commit",
    "material_sha256",
    "license_spdx",
    "license_evidence_spdx",
    "license_evidence_url",
    "license_evidence_sha256",
    "human_reviewed",
}
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z")
_EMBEDDED_MARKERS = ("\n", "\r", "```", "<img", "<svg", "![")
MAX_FILES = 2000
MAX_DIRECTORIES = 500
MAX_FILE_BYTES = 512 * 1024
MAX_TOTAL_BYTES = 4 * 1024 * 1024
MAX_DEPTH = 12
MAX_SECONDS = 5
_EXCLUDED_DIRECTORIES = {
    ".agents",
    ".claude",
    ".codex",
    ".cursor",
    ".git",
    ".hg",
    ".omo",
    ".svn",
    ".trellis",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "vendor",
    "venv",
}
_SECRET_NAMES = {
    ".env",
    ".env.local",
    "credentials",
    "credentials.json",
    "id_dsa",
    "id_ed25519",
    "id_rsa",
}
_SECRET_SUFFIXES = {".key", ".p12", ".pem"}
_BINARY_SUFFIXES = {
    ".avi",
    ".gif",
    ".gz",
    ".ico",
    ".jpeg",
    ".jpg",
    ".mov",
    ".mp4",
    ".pdf",
    ".png",
    ".tar",
    ".webm",
    ".webp",
    ".woff",
    ".woff2",
    ".zip",
}


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be a JSON object")
    missing = sorted(fields - set(value))
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    return value


def _text(value: Any, field: str, *, limit: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        _fail("E_DATASET_TEXT", f"{field} must be nonempty text within {limit} characters")
    if any(marker in value.lower() for marker in _EMBEDDED_MARKERS):
        _fail("E_DATASET_EMBEDDED_CONTENT", f"{field} contains embedded content")
    return value


def _slugs(value: Any, field: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not _SLUG.fullmatch(item) for item in value)
        or value != sorted(set(value))
    ):
        _fail("E_DATASET_SLUG_LIST", f"{field} must be a sorted unique slug list")
    return value


def _validate_pattern(value: Any, context: str) -> None:
    pattern = _object(value, _PATTERN_FIELDS, context)
    for field in sorted(_PATTERN_FIELDS):
        _text(pattern[field], f"{context}.{field}")


def _validate_source(value: Any, context: str) -> tuple[str, str, str]:
    source = _object(value, _SOURCE_FIELDS, context)
    repository_url = source["repository_url"]
    parsed = urlparse(repository_url) if isinstance(repository_url, str) else None
    if (
        parsed is None
        or parsed.scheme != "https"
        or parsed.netloc.lower() != "github.com"
        or len([part for part in parsed.path.split("/") if part]) != 2
        or parsed.query
        or parsed.fragment
    ):
        _fail("E_DATASET_REPOSITORY", f"{context}.repository_url must name a GitHub repository")

    commit = source["commit"]
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        _fail("E_DATASET_COMMIT", f"{context}.commit must be a lowercase 40-character SHA")

    for field in ("material_sha256", "license_evidence_sha256"):
        digest = source[field]
        if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            _fail("E_DATASET_SHA256", f"{context}.{field} must be a lowercase SHA-256")

    license_spdx = source["license_spdx"]
    evidence_spdx = source["license_evidence_spdx"]
    if (
        not isinstance(license_spdx, str)
        or license_spdx.upper() in {"UNKNOWN", "NOASSERTION"}
        or not _SPDX.fullmatch(license_spdx)
    ):
        _fail("E_DATASET_LICENSE", f"{context}.license_spdx must be reviewed SPDX")
    if evidence_spdx != license_spdx:
        _fail("E_DATASET_LICENSE_CONFLICT", f"{context} license evidence conflicts")

    evidence_url = source["license_evidence_url"]
    expected_prefix = f"{repository_url.rstrip('/')}/blob/{commit}/"
    if not isinstance(evidence_url, str) or not evidence_url.startswith(expected_prefix):
        _fail("E_DATASET_LICENSE_EVIDENCE", f"{context}.license_evidence_url must pin commit")
    if source["human_reviewed"] is not True:
        _fail("E_DATASET_LICENSE_REVIEW", f"{context}.human_reviewed must be true")

    return repository_url, commit, source["material_sha256"]


def validate_dataset_manifest(payload: Any) -> dict[str, object]:
    manifest = validate_contract(
        payload,
        required=_DATASET_FIELDS,
        optional=set(),
        context="retrieval dataset manifest",
    )
    if manifest["dataset_id"] != "readme-showcase-retrieval":
        _fail("E_DATASET_ID", "dataset_id must be readme-showcase-retrieval")
    revision = manifest["dataset_revision"]
    if type(revision) is not int or revision < 1:
        _fail("E_DATASET_REVISION", "dataset_revision must be a positive integer")
    if manifest["purpose"] != "retrieval-only":
        _fail("E_DATASET_PURPOSE", "purpose must be retrieval-only")

    records = manifest["records"]
    if not isinstance(records, list) or len(records) > 1000:
        _fail("E_DATASET_RECORDS", "records must be a list with at most 1000 items")

    record_ids: set[str] = set()
    source_splits: dict[tuple[str, str, str], str] = {}
    split_counts = {"test": 0, "train": 0}
    for index, value in enumerate(records):
        context = f"retrieval dataset manifest.records[{index}]"
        record = _object(value, _RECORD_FIELDS, context)
        record_id = record["record_id"]
        if not isinstance(record_id, str) or not _SLUG.fullmatch(record_id):
            _fail("E_DATASET_RECORD_ID", f"{context}.record_id must be a lowercase slug")
        if record_id in record_ids:
            _fail("E_DATASET_DUPLICATE_ID", f"duplicate record_id: {record_id}")
        record_ids.add(record_id)

        _slugs(record["project_types"], f"{context}.project_types")
        _slugs(record["section_intents"], f"{context}.section_intents")
        _slugs(record["tags"], f"{context}.tags")
        _validate_pattern(record["pattern"], f"{context}.pattern")

        split = record["split"]
        if split not in split_counts:
            _fail("E_DATASET_SPLIT", f"{context}.split must be train or test")
        identity = _validate_source(record["source"], f"{context}.source")
        prior_split = source_splits.get(identity)
        if prior_split is not None:
            code = "E_DATASET_SPLIT_LEAK" if prior_split != split else "E_DATASET_SOURCE_DUPLICATE"
            _fail(code, f"source identity reused by {record_id}")
        source_splits[identity] = split
        split_counts[split] += 1

    return {
        "schema_version": 1,
        "status": "pass",
        "record_count": len(records),
        "split_counts": split_counts,
        "manifest_sha256": canonical_sha256(manifest),
    }


def _scan_limits() -> dict[str, int]:
    return {
        "max_depth": MAX_DEPTH,
        "max_directories": MAX_DIRECTORIES,
        "max_file_bytes": MAX_FILE_BYTES,
        "max_files": MAX_FILES,
        "max_seconds": MAX_SECONDS,
        "max_total_bytes": MAX_TOTAL_BYTES,
    }


def _git_base_sha(root: Path) -> str | None:
    git = root / ".git"
    try:
        git_stat = git.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISDIR(git_stat.st_mode):
        return None
    head = git / "HEAD"
    try:
        if not stat.S_ISREG(head.lstat().st_mode):
            return None
        value = head.read_text(encoding="ascii").strip()
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return None
    if _COMMIT.fullmatch(value):
        return value
    if not value.startswith("ref: "):
        return None
    reference = value[5:]
    if reference.startswith("/") or ".." in reference.split("/"):
        return None
    ref_path = git.joinpath(*reference.split("/"))
    try:
        if not stat.S_ISREG(ref_path.lstat().st_mode):
            return None
        resolved = ref_path.read_text(encoding="ascii").strip()
    except (FileNotFoundError, UnicodeDecodeError, OSError):
        return None
    return resolved if _COMMIT.fullmatch(resolved) else None


def _incomplete_scan(root: Path, code: str, path: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "status": "incomplete",
        "target": {"name": root.name, "base_sha": _git_base_sha(root)},
        "scan_limits": _scan_limits(),
        "files": [],
        "facts": [],
        "warnings": [{"code": code, "path": path}],
    }


def _read_scanned_file(entry: Path, expected: os.stat_result, relative: str) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(entry, flags)
    except OSError as exc:
        raise ContractError("E_SCAN_IO", f"cannot open {relative}: {exc}") from exc
    try:
        actual = os.fstat(descriptor)
        if (
            not stat.S_ISREG(actual.st_mode)
            or actual.st_dev != expected.st_dev
            or actual.st_ino != expected.st_ino
            or actual.st_size != expected.st_size
        ):
            raise ContractError("E_SCAN_RACE", f"file changed during scan: {relative}")
        with os.fdopen(descriptor, "rb") as source:
            descriptor = -1
            return source.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def scan_repository(root: Path) -> dict[str, object]:
    try:
        initial = root.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_SCAN_ROOT", f"scan root not found: {root}") from exc
    if stat.S_ISLNK(initial.st_mode) or not stat.S_ISDIR(initial.st_mode):
        raise ContractError("E_SCAN_ROOT", "scan root must be a real directory")
    canonical_root = root.resolve(strict=True)
    started = time.monotonic()
    files: list[dict[str, object]] = []
    warnings: list[dict[str, str]] = []
    seen_files = 0
    seen_directories = 0
    total_bytes = 0
    pending = [canonical_root]

    while pending:
        directory = pending.pop()
        try:
            entries = sorted(directory.iterdir(), key=lambda item: item.name)
        except OSError as exc:
            relative = directory.relative_to(canonical_root).as_posix() or "."
            raise ContractError("E_SCAN_IO", f"cannot list {relative}: {exc}") from exc
        children: list[Path] = []
        for entry in entries:
            relative = entry.relative_to(canonical_root).as_posix()
            if time.monotonic() - started > MAX_SECONDS:
                return _incomplete_scan(canonical_root, "E_SCAN_TIME", relative)
            if len(entry.relative_to(canonical_root).parts) - 1 > MAX_DEPTH:
                return _incomplete_scan(canonical_root, "E_SCAN_DEPTH", relative)
            try:
                entry_stat = entry.lstat()
            except OSError as exc:
                raise ContractError("E_SCAN_IO", f"cannot inspect {relative}: {exc}") from exc
            if stat.S_ISLNK(entry_stat.st_mode):
                warnings.append({"code": "W_SCAN_SYMLINK", "path": relative})
                continue
            if stat.S_ISDIR(entry_stat.st_mode):
                if entry.name in _EXCLUDED_DIRECTORIES:
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
                if seen_directories > MAX_DIRECTORIES:
                    return _incomplete_scan(canonical_root, "E_SCAN_DIRECTORY_COUNT", relative)
                children.append(entry)
                continue
            if not stat.S_ISREG(entry_stat.st_mode):
                warnings.append({"code": "W_SCAN_SPECIAL", "path": relative})
                continue

            seen_files += 1
            if seen_files > MAX_FILES:
                return _incomplete_scan(canonical_root, "E_SCAN_FILE_COUNT", relative)
            lower_name = entry.name.lower()
            if lower_name in _SECRET_NAMES or entry.suffix.lower() in _SECRET_SUFFIXES:
                warnings.append({"code": "W_SCAN_SECRET", "path": relative})
                continue
            if entry.suffix.lower() in _BINARY_SUFFIXES:
                warnings.append({"code": "W_SCAN_BINARY", "path": relative})
                continue
            if entry_stat.st_size > MAX_FILE_BYTES:
                return _incomplete_scan(canonical_root, "E_SCAN_FILE_SIZE", relative)
            total_bytes += entry_stat.st_size
            if total_bytes > MAX_TOTAL_BYTES:
                return _incomplete_scan(canonical_root, "E_SCAN_TOTAL_SIZE", relative)
            raw = _read_scanned_file(entry, entry_stat, relative)
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

    files.sort(key=lambda item: str(item["path"]))
    warnings.sort(key=lambda item: (item["path"], item["code"]))
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
        "target": {"name": canonical_root.name, "base_sha": _git_base_sha(canonical_root)},
        "scan_limits": _scan_limits(),
        "files": files,
        "facts": facts,
        "warnings": warnings,
    }
