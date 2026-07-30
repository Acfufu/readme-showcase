from __future__ import annotations

import importlib
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path, PurePosixPath
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
_REF_FIELDS = {"path", "sha256"}
_BUNDLE_FIELDS = {"schema_version", "mode", "target", "candidate", "artifacts"}
_TARGET_FIELDS = {"repository", "base_sha"}
_CANDIDATE_FIELDS = {"readme", "assets"}
_ARTIFACT_FIELDS = {"plan", "retrieval", "claim_map", "asset_manifest"}
_PLAN_FIELDS = {
    "schema_version",
    "mode",
    "languages",
    "sections",
    "visual_intent",
    "diagram_route",
    "commands",
    "evidence_ids",
}
_CLAIM_MAP_FIELDS = {"schema_version", "markdown_blocks", "diagram_labels"}
_CLAIM_FIELDS = {
    "claim_id",
    "content_sha256",
    "claim_kind",
    "evidence_sha256",
    "truth_id",
    "language_pair_id",
}
_ASSET_MANIFEST_FIELDS = {"schema_version", "assets"}
_ASSET_FIELDS = {
    "path",
    "sha256",
    "type",
    "engine_kind",
    "production_kind",
    "alt",
    "caption",
    "truth_ids",
}
_GLYPHIC_ASSET_FIELDS = _ASSET_FIELDS | {"semantic", "engine_metadata", "fallback"}
_HYBRID_ASSET_FIELDS = _ASSET_FIELDS | {"layout", "subject", "prompt", "fallback"}
_MOTION_ASSET_FIELDS = _ASSET_FIELDS | {
    "source",
    "motion_spec",
    "fallback",
    "motion_approved",
}
_ENGINE_METADATA_FIELDS = {
    "schema_version",
    "engine_kind",
    "source_commit",
    "package_version",
    "core_version",
    "engine_schema_version",
    "package_sha256",
    "tree_sha256",
    "sri",
    "license_spdx",
    "license_sha256",
    "lock_sha256",
    "node_version",
    "platform",
    "architecture",
    "input_sha256",
    "theme_sha256",
    "output_sha256",
    "run_hashes",
    "validation",
    "fallback_state",
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


def _relative_path(value: Any, context: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\\" in value:
        _fail("E_PATH", f"{context} must be a nonempty POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or "." in path.parts or ".." in path.parts or path.as_posix() != value:
        _fail("E_PATH", f"{context} must be a normalized relative path")
    return path


def _reference(value: Any, context: str) -> dict[str, str]:
    reference = _object(value, _REF_FIELDS, context)
    path = _relative_path(reference["path"], f"{context}.path")
    digest = reference["sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _fail("E_BUNDLE_HASH", f"{context}.sha256 must be a lowercase SHA-256")
    return {"path": path.as_posix(), "sha256": digest}


def _artifact_bytes(root: Path, reference: dict[str, str], context: str) -> bytes:
    root = root.resolve(strict=True)
    destination = root.joinpath(*PurePosixPath(reference["path"]).parts)
    current = root
    try:
        for part in PurePosixPath(reference["path"]).parts:
            current = current / part
            if stat.S_ISLNK(current.lstat().st_mode):
                _fail("E_PATH", f"{context} cannot reference a symlink")
    except FileNotFoundError as exc:
        raise ContractError("E_BUNDLE_MISSING", f"{context} not found: {reference['path']}") from exc
    try:
        destination.resolve(strict=True).relative_to(root)
    except (OSError, ValueError) as exc:
        raise ContractError("E_PATH", f"{context} escapes artifact root") from exc
    file_stat = destination.lstat()
    if not stat.S_ISREG(file_stat.st_mode):
        _fail("E_PATH", f"{context} must reference a regular file")
    raw = _read_scanned_file(destination, file_stat, reference["path"])
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        _fail("E_BUNDLE_HASH", f"{context} hash does not match {reference['path']}")
    return raw


def _artifact_json(root: Path, value: Any, context: str) -> tuple[dict[str, Any], dict[str, str]]:
    reference = _reference(value, context)
    raw = _artifact_bytes(root, reference, context)
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_INPUT_JSON", f"{context} must be valid UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must contain a JSON object")
    return payload, reference


def _string_list(value: Any, context: str, *, allow_empty: bool = True) -> list[str]:
    if (
        not isinstance(value, list)
        or (not allow_empty and not value)
        or any(not isinstance(item, str) or not item for item in value)
        or value != sorted(set(value))
    ):
        _fail("E_SCHEMA_TYPE", f"{context} must be a sorted unique string list")
    return value


def _validate_plan(payload: Any, mode: str) -> None:
    plan = validate_contract(
        payload,
        required=_PLAN_FIELDS,
        optional=set(),
        context="README plan",
    )
    if plan["mode"] != mode:
        _fail("E_BUNDLE_MODE", "README plan mode differs from bundle mode")
    _string_list(plan["languages"], "README plan.languages", allow_empty=False)
    _string_list(plan["sections"], "README plan.sections")
    _string_list(plan["commands"], "README plan.commands")
    _string_list(plan["evidence_ids"], "README plan.evidence_ids")
    _text(plan["visual_intent"], "README plan.visual_intent")
    if plan["diagram_route"] not in {"none", "static", "glyphic"}:
        _fail("E_BUNDLE_PLAN", "README plan.diagram_route is unsupported")


def _validate_claim_map(payload: Any) -> set[str]:
    claim_map = validate_contract(
        payload,
        required=_CLAIM_MAP_FIELDS,
        optional=set(),
        context="claim map",
    )
    truth_ids: set[str] = set()
    for collection_name in ("markdown_blocks", "diagram_labels"):
        collection = claim_map[collection_name]
        if not isinstance(collection, list):
            _fail("E_SCHEMA_TYPE", f"claim map.{collection_name} must be a list")
        for index, value in enumerate(collection):
            context = f"claim map.{collection_name}[{index}]"
            claim = _object(value, _CLAIM_FIELDS, context)
            for field in ("claim_id", "truth_id"):
                _text(claim[field], f"{context}.{field}")
            for field in ("content_sha256", "evidence_sha256"):
                digest = claim[field]
                if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    _fail("E_BUNDLE_CLAIM", f"{context}.{field} must be a SHA-256")
            if claim["claim_kind"] not in {"factual", "instruction", "decorative"}:
                _fail("E_BUNDLE_CLAIM", f"{context}.claim_kind is unsupported")
            language_pair = claim["language_pair_id"]
            if language_pair is not None and (not isinstance(language_pair, str) or not language_pair):
                _fail("E_BUNDLE_CLAIM", f"{context}.language_pair_id is invalid")
            truth_id = claim["truth_id"]
            if truth_id in truth_ids:
                _fail("E_BUNDLE_CLAIM", f"duplicate truth_id: {truth_id}")
            truth_ids.add(truth_id)
    return truth_ids


def _validate_engine_metadata(
    payload: Any,
    *,
    asset_sha256: str,
    semantic_sha256: str,
) -> None:
    metadata = validate_contract(
        payload,
        required=_ENGINE_METADATA_FIELDS,
        optional=set(),
        context="Glyphic engine metadata",
    )
    if metadata["engine_kind"] != "glyphic":
        _fail("E_ENGINE_METADATA", "engine metadata must declare glyphic")
    if not isinstance(metadata["source_commit"], str) or not _COMMIT.fullmatch(metadata["source_commit"]):
        _fail("E_ENGINE_METADATA", "engine source_commit must be immutable")
    for field in (
        "package_sha256",
        "tree_sha256",
        "license_sha256",
        "lock_sha256",
        "input_sha256",
        "theme_sha256",
        "output_sha256",
    ):
        value = metadata[field]
        if not isinstance(value, str) or not _SHA256.fullmatch(value):
            _fail("E_ENGINE_METADATA", f"engine metadata {field} must be a SHA-256")
    if metadata["input_sha256"] != semantic_sha256 or metadata["output_sha256"] != asset_sha256:
        _fail("E_ENGINE_METADATA", "engine input/output hashes do not bind asset")
    if metadata["run_hashes"] != [asset_sha256, asset_sha256]:
        _fail("E_ENGINE_METADATA", "engine run hashes must prove equal raw bytes")
    if metadata["validation"] != "pass" or metadata["fallback_state"] != "preserved":
        _fail("E_ENGINE_METADATA", "engine validation and fallback state must pass")
    for field in (
        "package_version",
        "core_version",
        "engine_schema_version",
        "sri",
        "license_spdx",
        "node_version",
        "platform",
        "architecture",
    ):
        _text(metadata[field], f"engine metadata.{field}")


def _validate_asset_manifest(
    payload: Any,
    *,
    root: Path,
    candidate_assets: list[dict[str, str]],
    truth_ids: set[str],
) -> None:
    manifest = validate_contract(
        payload,
        required=_ASSET_MANIFEST_FIELDS,
        optional=set(),
        context="asset manifest",
    )
    assets = manifest["assets"]
    if not isinstance(assets, list):
        _fail("E_SCHEMA_TYPE", "asset manifest.assets must be a list")
    manifest_refs: list[dict[str, str]] = []
    for index, value in enumerate(assets):
        context = f"asset manifest.assets[{index}]"
        if not isinstance(value, dict):
            _fail("E_SCHEMA_TYPE", f"{context} must be an object")
        engine_kind = value.get("engine_kind")
        production_kind = value.get("production_kind")
        if engine_kind == "glyphic":
            fields = _GLYPHIC_ASSET_FIELDS
        elif production_kind == "hybrid":
            fields = _HYBRID_ASSET_FIELDS
        elif production_kind == "motion":
            fields = _MOTION_ASSET_FIELDS
        else:
            fields = _ASSET_FIELDS
        asset = _object(value, fields, context)
        reference = _reference(
            {"path": asset["path"], "sha256": asset["sha256"]},
            context,
        )
        _artifact_bytes(root, reference, context)
        manifest_refs.append(reference)
        if asset["type"] not in {"svg", "png", "webp", "gif"}:
            _fail("E_BUNDLE_ASSET", f"{context}.type is unsupported")
        if engine_kind not in {"hand-authored", "glyphic"}:
            _fail("E_BUNDLE_ASSET", f"{context}.engine_kind is unsupported")
        if production_kind not in {"static", "hybrid", "motion"}:
            _fail("E_BUNDLE_ASSET", f"{context}.production_kind is unsupported")
        _text(asset["alt"], f"{context}.alt")
        _text(asset["caption"], f"{context}.caption")
        bound_truth_ids = _string_list(asset["truth_ids"], f"{context}.truth_ids", allow_empty=False)
        if not set(bound_truth_ids).issubset(truth_ids):
            _fail("E_BUNDLE_CLAIM", f"{context} references unknown truth_id")
        if engine_kind == "glyphic":
            if production_kind != "static":
                _fail("E_BUNDLE_ASSET", "Glyphic output must use static production")
            if asset["type"] != "svg" or not reference["path"].endswith(".svg"):
                _fail("E_BUNDLE_ASSET", "Glyphic output must be standalone SVG")
            semantic_payload, semantic_ref = _artifact_json(root, asset["semantic"], f"{context}.semantic")
            validate_contract(
                semantic_payload,
                required={"schema_version", "diagram_type"},
                optional=set(),
                context="Glyphic semantic source",
            )
            if semantic_payload["diagram_type"] not in {"architecture", "flowchart", "c4"}:
                _fail("E_BUNDLE_ASSET", "Glyphic diagram type is unsupported")
            metadata_payload, _ = _artifact_json(
                root,
                asset["engine_metadata"],
                f"{context}.engine_metadata",
            )
            _artifact_bytes(root, _reference(asset["fallback"], f"{context}.fallback"), f"{context}.fallback")
            _validate_engine_metadata(
                metadata_payload,
                asset_sha256=reference["sha256"],
                semantic_sha256=semantic_ref["sha256"],
            )
        elif production_kind == "hybrid":
            if asset["type"] not in {"png", "webp"}:
                _fail("E_BUNDLE_ASSET", "hybrid output must publish PNG or WebP")
            hybrid_refs = {
                name: _reference(asset[name], f"{context}.{name}")
                for name in ("layout", "subject", "prompt", "fallback")
            }
            expected_suffixes = {
                "layout": {".svg"},
                "subject": {".png", ".webp"},
                "prompt": {".txt"},
                "fallback": {".svg"},
            }
            for name, source_ref in hybrid_refs.items():
                _artifact_bytes(root, source_ref, f"{context}.{name}")
                if PurePosixPath(source_ref["path"]).suffix not in expected_suffixes[name]:
                    _fail("E_BUNDLE_ASSET", f"{context}.{name} has unsupported file type")
            if len({item["path"] for item in hybrid_refs.values()}) != 4:
                _fail("E_BUNDLE_ASSET", "hybrid editable sources and fallback must be distinct")
        elif production_kind == "motion":
            if asset["type"] != "gif":
                _fail("E_BUNDLE_ASSET", "motion output must publish GIF")
            if asset["motion_approved"] is not True:
                _fail("E_VISUAL_MOTION_APPROVAL", "motion requires explicit approval")
            for name in ("source", "fallback"):
                source_ref = _reference(asset[name], f"{context}.{name}")
                _artifact_bytes(root, source_ref, f"{context}.{name}")
                if not source_ref["path"].endswith(".svg"):
                    _fail("E_BUNDLE_ASSET", f"{context}.{name} must be static SVG")
            motion_spec, _ = _artifact_json(
                root,
                asset["motion_spec"],
                f"{context}.motion_spec",
            )
            if motion_spec.get("schema_version") != 1:
                _fail("E_SCHEMA_VERSION", "motion spec requires schema_version 1")
    if manifest_refs != candidate_assets:
        _fail("E_BUNDLE_ASSET", "candidate assets and asset manifest differ")


def validate_generated_bundle(payload: Any, artifact_root: Path) -> dict[str, object]:
    bundle = validate_contract(
        payload,
        required=_BUNDLE_FIELDS,
        optional=set(),
        context="generated README bundle",
    )
    mode = bundle["mode"]
    if mode not in {"readme", "asset-only", "audit-only"}:
        _fail("E_BUNDLE_MODE", "bundle mode is unsupported")
    target = _object(bundle["target"], _TARGET_FIELDS, "bundle target")
    _text(target["repository"], "bundle target.repository")
    if not isinstance(target["base_sha"], str) or not _COMMIT.fullmatch(target["base_sha"]):
        _fail("E_BUNDLE_TARGET", "bundle target.base_sha must be immutable")

    candidate = _object(bundle["candidate"], _CANDIDATE_FIELDS, "bundle candidate")
    readme = candidate["readme"]
    assets = candidate["assets"]
    if not isinstance(assets, list):
        _fail("E_SCHEMA_TYPE", "bundle candidate.assets must be a list")
    candidate_assets = [
        _reference(value, f"bundle candidate.assets[{index}]")
        for index, value in enumerate(assets)
    ]
    if candidate_assets != sorted(candidate_assets, key=lambda item: item["path"]):
        _fail("E_BUNDLE_ASSET", "bundle candidate.assets must be path-sorted")
    for index, reference in enumerate(candidate_assets):
        _artifact_bytes(artifact_root, reference, f"bundle candidate.assets[{index}]")

    if mode == "readme":
        readme_ref = _reference(readme, "bundle candidate.readme")
        _artifact_bytes(artifact_root, readme_ref, "bundle candidate.readme")
    elif readme is not None:
        _fail("E_BUNDLE_MODE", f"{mode} mode cannot contain candidate README")
    if mode == "asset-only" and not candidate_assets:
        _fail("E_BUNDLE_MODE", "asset-only mode requires at least one asset")
    if mode == "audit-only" and candidate_assets:
        _fail("E_BUNDLE_MODE", "audit-only mode cannot contain candidate assets")

    artifacts = _object(bundle["artifacts"], _ARTIFACT_FIELDS, "bundle artifacts")
    plan, _ = _artifact_json(artifact_root, artifacts["plan"], "bundle artifacts.plan")
    retrieval, _ = _artifact_json(
        artifact_root,
        artifacts["retrieval"],
        "bundle artifacts.retrieval",
    )
    claims, _ = _artifact_json(
        artifact_root,
        artifacts["claim_map"],
        "bundle artifacts.claim_map",
    )
    asset_manifest, _ = _artifact_json(
        artifact_root,
        artifacts["asset_manifest"],
        "bundle artifacts.asset_manifest",
    )
    _validate_plan(plan, mode)
    if retrieval.get("schema_version") != 1:
        _fail("E_SCHEMA_VERSION", "retrieval packet requires schema_version 1")
    truth_ids = _validate_claim_map(claims)
    _validate_asset_manifest(
        asset_manifest,
        root=artifact_root,
        candidate_assets=candidate_assets,
        truth_ids=truth_ids,
    )
    return {
        "schema_version": 1,
        "status": "pass",
        "mode": mode,
        "bundle_sha256": canonical_sha256(bundle),
        "candidate_count": (1 if readme is not None else 0) + len(candidate_assets),
    }
