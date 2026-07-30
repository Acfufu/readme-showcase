from __future__ import annotations

import importlib
import hashlib
import json
import os
import re
import stat
import subprocess
import time
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn, cast
from urllib.parse import urlparse

_CONTRACTS = importlib.import_module(
    "pipeline_contracts"
    if __package__ in (None, "")
    else "skill.scripts.pipeline_contracts"
)
_AUDIT = importlib.import_module(
    "audit_readme"
    if __package__ in (None, "")
    else "skill.scripts.audit_readme"
)
ContractError = _CONTRACTS.ContractError
canonical_sha256 = _CONTRACTS.canonical_sha256
validate_contract = _CONTRACTS.validate_contract
audit_readme = _AUDIT.audit_readme
audit_svg_bytes = _AUDIT.audit_svg_bytes
image_references = _AUDIT.image_references
visible_svg_text = _AUDIT.visible_svg_text


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
    "evaluation-only",
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
_GLYPHIC_SEMANTIC_FIELDS = {
    "schema_version",
    "diagram_type",
    "accessibility_title",
    "accessibility_claim_id",
    "direction",
    "palette",
    "groups",
    "nodes",
    "edges",
    "claim_ids",
}
_GLYPHIC_PALETTE_FIELDS = {
    "background",
    "node_background",
    "node_border",
    "node_text",
    "edge_color",
    "edge_label_color",
}
_GLYPHIC_GROUP_FIELDS = {"id", "label", "parent_id", "claim_id"}
_GLYPHIC_NODE_FIELDS = {"id", "label", "group_id", "kind", "claim_id"}
_GLYPHIC_EDGE_FIELDS = {"source", "target", "label", "claim_id"}
_GLYPHIC_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_HEX_COLOR = re.compile(r"#[0-9A-Fa-f]{6}\Z")
_GLYPHIC_SOURCE_COMMIT = "ed79edb1624e2de78041611971a963efaea5e080"
_GLYPHIC_VERSION = "1.3.1"
_GLYPHIC_LICENSE = "FSL-1.1-ALv2"
_RETRIEVAL_EVIDENCE_FIELDS = {
    "schema_version",
    "status",
    "target",
    "scan_limits",
    "files",
    "facts",
    "warnings",
}
_EVIDENCE_TARGET_FIELDS = {"name", "base_sha"}
_EVIDENCE_FILE_FIELDS = {"path", "bytes", "lines", "sha256", "content"}
_EVIDENCE_FACT_FIELDS = {"fact_id", "kind", "path", "evidence_sha256"}
_ADVISORY_METRICS = (
    "claim_coverage",
    "diagram_label_coverage",
    "evidence_sources",
    "language_truth_pairs",
    "observable_commands",
    "section_intents",
    "visual_provenance",
)
_EVALUATION_REPORT_FIELDS = {
    "schema_version",
    "status",
    "decision_basis",
    "bundle_sha256",
    "hard_gate",
    "advisory",
}
_PR_EXCLUDED_PARTS = {
    ".git",
    ".omo",
    "evaluation-only",
    "node_modules",
    "previews",
    "run-artifacts",
}
_PR_EXCLUDED_NAMES = {
    "approval-envelope.json",
    "asset-manifest.json",
    "claim-map.json",
    "evaluation-report.json",
    "generated-readme-bundle.json",
    "pr-bundle.json",
    "readme-plan.json",
    "remote-state.json",
    "repository-evidence.json",
    "retrieval-packet.json",
}
_GITHUB_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z"
)
_RETRIEVAL_PROJECT_TYPES = {
    "developer-tool",
    "library",
    "runtime-toolchain",
    "web-framework",
}


def _fail(code: str, message: str) -> NoReturn:
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


def _retrieval_query(
    evidence: Any,
    *,
    project_type: str,
    sections: list[str],
    tags: list[str],
    mode: str,
) -> dict[str, object]:
    packet = _object(evidence, _RETRIEVAL_EVIDENCE_FIELDS, "repository evidence")
    if packet["schema_version"] != 1 or packet["status"] != "complete":
        _fail("E_RETRIEVAL_EVIDENCE", "retrieval requires complete schema-v1 evidence")
    for field in ("files", "facts", "warnings"):
        if not isinstance(packet[field], list):
            _fail("E_RETRIEVAL_EVIDENCE", f"repository evidence.{field} must be a list")
    if project_type not in _RETRIEVAL_PROJECT_TYPES:
        _fail("E_RETRIEVAL_QUERY", "project_type is unsupported")
    if mode not in {"production", "benchmark"}:
        _fail("E_RETRIEVAL_MODE", "mode must be production or benchmark")

    def normalized(values: list[str], context: str) -> list[str]:
        if not isinstance(values, list) or any(
            not isinstance(value, str) or not _SLUG.fullmatch(value)
            for value in values
        ):
            _fail("E_RETRIEVAL_QUERY", f"{context} must contain lowercase slugs")
        return sorted(set(values))

    return {
        "project_type": project_type,
        "sections": normalized(sections, "sections"),
        "tags": normalized(tags, "tags"),
        "evidence_sha256": canonical_sha256(packet),
    }


def retrieve_patterns(
    evidence: Any,
    manifest: Any | None,
    *,
    project_type: str,
    sections: list[str],
    tags: list[str],
    mode: str,
) -> dict[str, object]:
    query = _retrieval_query(
        evidence,
        project_type=project_type,
        sections=sections,
        tags=tags,
        mode=mode,
    )
    if manifest is None:
        if mode == "benchmark":
            _fail("E_RETRIEVAL_MANIFEST", "benchmark retrieval requires a valid manifest")
        return {
            "schema_version": 1,
            "status": "unavailable",
            "mode": mode,
            "query": query,
            "dataset": None,
            "records": [],
            "reason": "manifest-unavailable",
        }

    validate_dataset_manifest(manifest)
    records = sorted(manifest["records"], key=lambda item: item["record_id"])
    normalized_manifest = {**manifest, "records": records}
    section_set = set(cast(list[str], query["sections"]))
    tag_set = set(cast(list[str], query["tags"]))
    ranked: list[dict[str, object]] = []
    for record in records:
        if record["split"] != "train":
            continue
        components = {
            "project_type_match": int(project_type in record["project_types"]),
            "section_overlap_count": len(section_set.intersection(record["section_intents"])),
            "tag_overlap_count": len(tag_set.intersection(record["tags"])),
        }
        score = (
            100 * components["project_type_match"]
            + 30 * components["section_overlap_count"]
            + 10 * components["tag_overlap_count"]
        )
        if score == 0:
            continue
        ranked.append(
            {
                "record_id": record["record_id"],
                "score": score,
                "components": components,
                "project_types": record["project_types"],
                "section_intents": record["section_intents"],
                "tags": record["tags"],
                "pattern": record["pattern"],
                "source": record["source"],
            }
        )
    ranked.sort(
        key=lambda item: (
            -cast(int, item["score"]),
            cast(str, item["record_id"]),
        )
    )
    return {
        "schema_version": 1,
        "status": "available",
        "mode": mode,
        "query": query,
        "dataset": {
            "dataset_id": manifest["dataset_id"],
            "dataset_revision": manifest["dataset_revision"],
            "manifest_sha256": canonical_sha256(normalized_manifest),
        },
        "records": ranked[:5],
        "reason": None,
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


def _validate_plan(payload: Any, mode: str) -> dict[str, Any]:
    plan = validate_contract(
        payload,
        required=_PLAN_FIELDS,
        optional=set(),
        context="README plan",
    )
    if plan["mode"] != mode:
        _fail("E_BUNDLE_MODE", "README plan mode differs from bundle mode")
    languages = _string_list(plan["languages"], "README plan.languages", allow_empty=False)
    if not set(languages).issubset({"en", "zh"}):
        _fail("E_README_LANGUAGE", "README plan.languages must contain en and/or zh")
    _string_list(plan["sections"], "README plan.sections")
    commands = _string_list(plan["commands"], "README plan.commands")
    for index, command in enumerate(commands):
        _text(command, f"README plan.commands[{index}]")
    _string_list(plan["evidence_ids"], "README plan.evidence_ids")
    _text(plan["visual_intent"], "README plan.visual_intent")
    if plan["diagram_route"] not in {"none", "static", "glyphic"}:
        _fail("E_BUNDLE_PLAN", "README plan.diagram_route is unsupported")
    return plan


def _validate_svg(
    raw: bytes,
    context: str,
    *,
    expected_title: str | None = None,
    expected_labels: list[str] | None = None,
) -> None:
    issues = audit_svg_bytes(
        raw,
        expected_title=expected_title,
        expected_labels=expected_labels,
    )
    if issues:
        code, message = issues[0]
        _fail(code, f"{context}: {message}")


def _validate_asset_bytes(raw: bytes, asset_type: str, path: str, context: str) -> None:
    suffixes = {
        "svg": {".svg"},
        "png": {".png"},
        "webp": {".webp"},
        "gif": {".gif"},
    }
    suffix = PurePosixPath(path).suffix.lower()
    if suffix not in suffixes.get(asset_type, set()):
        _fail("E_BUNDLE_ASSET", f"{context}.type does not match path suffix")
    if asset_type == "svg":
        _validate_svg(raw, context)
    elif asset_type == "png" and not raw.startswith(b"\x89PNG\r\n\x1a\n"):
        _fail("E_BUNDLE_ASSET", f"{context} is not PNG bytes")
    elif asset_type == "gif" and not raw.startswith((b"GIF87a", b"GIF89a")):
        _fail("E_BUNDLE_ASSET", f"{context} is not GIF bytes")
    elif asset_type == "webp" and not (
        len(raw) >= 12 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP"
    ):
        _fail("E_BUNDLE_ASSET", f"{context} is not WebP bytes")


def segment_markdown_blocks(text: str) -> list[str]:
    blocks: list[str] = []
    current: list[str] = []
    fence: str | None = None
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        marker = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})", line)
        if marker and fence is None:
            fence = marker.group(1)[0]
        elif marker and fence == marker.group(1)[0]:
            fence = None
        if not line.strip() and fence is None:
            if current:
                blocks.append("\n".join(current))
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _content_sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _load_evidence_facts(
    root: Path,
    *,
    base_sha: str,
    evidence_ids: list[str],
) -> dict[str, str]:
    path = root / "repository-evidence.json"
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise ContractError(
            "E_CLAIM_EVIDENCE",
            "repository-evidence.json is required for claim validation",
        ) from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("E_CLAIM_EVIDENCE", "repository evidence must be a regular file")
    if info.st_size > MAX_TOTAL_BYTES:
        _fail("E_CLAIM_EVIDENCE", "repository evidence exceeds scan byte limit")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(
            "E_CLAIM_EVIDENCE",
            "repository evidence must be UTF-8 JSON",
        ) from exc
    evidence = _object(payload, _RETRIEVAL_EVIDENCE_FIELDS, "repository evidence")
    if evidence["schema_version"] != 1 or evidence["status"] != "complete":
        _fail("E_CLAIM_EVIDENCE", "claims require complete schema-v1 repository evidence")
    target = _object(
        evidence["target"],
        _EVIDENCE_TARGET_FIELDS,
        "repository evidence.target",
    )
    if target["base_sha"] != base_sha:
        _fail("E_CLAIM_EVIDENCE", "repository evidence base SHA differs from bundle")

    files = evidence["files"]
    if not isinstance(files, list):
        _fail("E_CLAIM_EVIDENCE", "repository evidence.files must be a list")
    file_hashes: dict[str, str] = {}
    for index, value in enumerate(files):
        item = _object(
            value,
            _EVIDENCE_FILE_FIELDS,
            f"repository evidence.files[{index}]",
        )
        path_value = _relative_path(item["path"], f"repository evidence.files[{index}].path")
        content = item["content"]
        digest = item["sha256"]
        if (
            not isinstance(content, str)
            or not isinstance(digest, str)
            or not _SHA256.fullmatch(digest)
            or _content_sha256(content) != digest
            or item["bytes"] != len(content.encode("utf-8"))
            or item["lines"] != len(content.splitlines())
        ):
            _fail("E_CLAIM_EVIDENCE", "repository evidence file hash/counts are inconsistent")
        if path_value.as_posix() in file_hashes:
            _fail("E_CLAIM_EVIDENCE", "repository evidence contains duplicate file path")
        file_hashes[path_value.as_posix()] = digest

    facts = evidence["facts"]
    if not isinstance(facts, list):
        _fail("E_CLAIM_EVIDENCE", "repository evidence.facts must be a list")
    fact_hashes: dict[str, str] = {}
    for index, value in enumerate(facts):
        item = _object(
            value,
            _EVIDENCE_FACT_FIELDS,
            f"repository evidence.facts[{index}]",
        )
        fact_id = item["fact_id"]
        path_value = item["path"]
        digest = item["evidence_sha256"]
        if (
            not isinstance(fact_id, str)
            or not fact_id
            or fact_id.startswith(("gold:", "retrieval:"))
            or item["kind"] != "repository-file"
            or not isinstance(path_value, str)
            or file_hashes.get(path_value) != digest
        ):
            _fail("E_CLAIM_EVIDENCE", "repository evidence fact is not target-file evidence")
        if fact_id in fact_hashes:
            _fail("E_CLAIM_EVIDENCE", f"duplicate repository fact: {fact_id}")
        fact_hashes[fact_id] = digest
    if not set(evidence_ids).issubset(fact_hashes):
        _fail("E_CLAIM_EVIDENCE", "README plan references unknown target evidence")
    return fact_hashes


def _readme_claim_inputs(
    root: Path,
    *,
    readme_path: str | None,
    readme_text: str | None,
    languages: list[str],
) -> list[dict[str, str]]:
    if readme_path is None or readme_text is None:
        return []
    if len(languages) == 1:
        return [
            {
                "content": block,
                "content_sha256": _content_sha256(block),
                "language": languages[0],
            }
            for block in segment_markdown_blocks(readme_text)
        ]
    if languages != ["en", "zh"]:
        _fail("E_CLAIM_LANGUAGE", "bilingual claim coverage requires en and zh")
    primary = PurePosixPath(readme_path)
    if primary.name == "README.md":
        primary_language, companion_name = "en", "README_zh.md"
    elif primary.name == "README_zh.md":
        primary_language, companion_name = "zh", "README.md"
    else:
        _fail("E_CLAIM_LANGUAGE", "bilingual bundle README must use README.md or README_zh.md")
    companion_path = root.joinpath(*primary.parent.parts, companion_name)
    try:
        info = companion_path.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_CLAIM_LANGUAGE", "bilingual companion README is missing") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        _fail("E_CLAIM_LANGUAGE", "bilingual companion README must be a regular file")
    try:
        companion_text = companion_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("E_CLAIM_LANGUAGE", "bilingual companion README must be UTF-8") from exc
    issues, _, _ = audit_readme(companion_path, root=root)
    if issues:
        _fail("E_README_AUDIT", issues[0])
    sources = {
        primary_language: readme_text,
        "zh" if primary_language == "en" else "en": companion_text,
    }
    return [
        {
            "content": block,
            "content_sha256": _content_sha256(block),
            "language": language,
        }
        for language in languages
        for block in segment_markdown_blocks(sources[language])
    ]


def _diagram_claim_inputs(
    payload: Any,
    *,
    root: Path,
    default_language: str,
) -> list[dict[str, str | None]]:
    manifest = validate_contract(
        payload,
        required=_ASSET_MANIFEST_FIELDS,
        optional=set(),
        context="asset manifest",
    )
    assets = manifest["assets"]
    if not isinstance(assets, list):
        _fail("E_SCHEMA_TYPE", "asset manifest.assets must be a list")
    expected: list[dict[str, str | None]] = []
    for index, value in enumerate(assets):
        if not isinstance(value, dict):
            _fail("E_SCHEMA_TYPE", f"asset manifest.assets[{index}] must be an object")
        path_value = value.get("path")
        language = (
            "zh"
            if isinstance(path_value, str)
            and (
                "/zh/" in f"/{path_value.lower()}/"
                or "-zh." in path_value.lower()
                or "_zh." in path_value.lower()
            )
            else default_language
        )
        if value.get("engine_kind") == "glyphic":
            semantic, _ = _artifact_json(
                root,
                value.get("semantic"),
                f"asset manifest.assets[{index}].semantic",
            )
            _validate_glyphic_semantic(semantic)
            labels = [
                (semantic["accessibility_claim_id"], semantic["accessibility_title"]),
                *[(item["claim_id"], item["label"]) for item in semantic["groups"]],
                *[(item["claim_id"], item["label"]) for item in semantic["nodes"]],
                *[
                    (item["claim_id"], item["label"])
                    for item in semantic["edges"]
                    if item["label"] is not None
                ],
            ]
            expected.extend(
                {
                    "claim_id": claim_id,
                    "content": text,
                    "content_sha256": _content_sha256(text),
                    "language": language,
                }
                for claim_id, text in labels
            )
        elif value.get("type") == "svg":
            reference = _reference(
                {"path": value.get("path"), "sha256": value.get("sha256")},
                f"asset manifest.assets[{index}]",
            )
            raw = _artifact_bytes(root, reference, f"asset manifest.assets[{index}]")
            expected.extend(
                {
                    "claim_id": None,
                    "content": text,
                    "content_sha256": _content_sha256(text),
                    "language": language,
                }
                for text in visible_svg_text(raw)
            )
    return expected


def _validate_claim_map(
    payload: Any,
    *,
    markdown_expected: list[dict[str, str]],
    diagram_expected: list[dict[str, str | None]],
    fact_hashes: dict[str, str],
    evidence_ids: list[str],
    languages: list[str],
) -> set[str]:
    claim_map = validate_contract(
        payload,
        required=_CLAIM_MAP_FIELDS,
        optional=set(),
        context="claim map",
    )
    if claim_map["schema_version"] != 1:
        _fail("E_SCHEMA_VERSION", "claim map requires schema_version 1")
    truth_ids: set[str] = set()
    claim_ids: set[str] = set()
    content_hashes: set[str] = set()
    expected_all = [*markdown_expected, *diagram_expected]
    expected_hashes = [str(item["content_sha256"]) for item in expected_all]
    if len(expected_hashes) != len(set(expected_hashes)):
        _fail("E_CLAIM_DUPLICATE", "generated content contains duplicate block or label hash")
    expected_by_collection = {
        "markdown_blocks": {
            str(item["content_sha256"]): item for item in markdown_expected
        },
        "diagram_labels": {
            str(item["content_sha256"]): item for item in diagram_expected
        },
    }
    paired: dict[tuple[str, str], list[tuple[dict[str, Any], str]]] = {}
    for collection_name in ("markdown_blocks", "diagram_labels"):
        collection = claim_map[collection_name]
        if not isinstance(collection, list):
            _fail("E_SCHEMA_TYPE", f"claim map.{collection_name} must be a list")
        ordered_ids: list[str] = []
        for index, value in enumerate(collection):
            context = f"claim map.{collection_name}[{index}]"
            claim = _object(value, _CLAIM_FIELDS, context)
            claim_id = _text(claim["claim_id"], f"{context}.claim_id")
            truth_id = _text(claim["truth_id"], f"{context}.truth_id")
            for field in ("content_sha256", "evidence_sha256"):
                digest = claim[field]
                if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                    _fail("E_BUNDLE_CLAIM", f"{context}.{field} must be a SHA-256")
            content_hash = cast(str, claim["content_sha256"])
            if claim_id in claim_ids or content_hash in content_hashes:
                _fail("E_CLAIM_DUPLICATE", f"{context} duplicates claim id or content hash")
            claim_ids.add(claim_id)
            content_hashes.add(content_hash)
            ordered_ids.append(claim_id)
            if claim["claim_kind"] not in {"factual", "instruction", "decorative"}:
                _fail("E_BUNDLE_CLAIM", f"{context}.claim_kind is unsupported")
            language_pair = claim["language_pair_id"]
            if language_pair is not None and (not isinstance(language_pair, str) or not language_pair):
                _fail("E_BUNDLE_CLAIM", f"{context}.language_pair_id is invalid")
            truth_ids.add(truth_id)
            if (
                truth_id.startswith(("gold:", "retrieval:"))
                or truth_id not in evidence_ids
                or fact_hashes.get(truth_id) != claim["evidence_sha256"]
            ):
                _fail("E_CLAIM_EVIDENCE", f"{context} lacks matching target evidence")
            expected = expected_by_collection[collection_name].get(content_hash)
            if expected is None:
                _fail("E_CLAIM_COVERAGE", f"{context} is orphan or stale")
            expected_claim_id = expected.get("claim_id")
            if expected_claim_id is not None and claim_id != expected_claim_id:
                _fail("E_CLAIM_LABEL", f"{context}.claim_id differs from semantic source")
            language = str(expected["language"])
            if collection_name == "markdown_blocks" and not claim_id.startswith(
                f"markdown:{language}:"
            ):
                _fail("E_CLAIM_LANGUAGE", f"{context}.claim_id has wrong language")
            if len(languages) == 1:
                if language_pair is not None:
                    _fail("E_CLAIM_LANGUAGE", f"{context} has stale language pair")
            else:
                if language_pair is None:
                    _fail("E_CLAIM_LANGUAGE", f"{context} is missing bilingual pair")
                paired.setdefault((collection_name, language_pair), []).append(
                    (claim, language)
                )
        if ordered_ids != sorted(ordered_ids):
            _fail("E_CLAIM_COVERAGE", f"claim map.{collection_name} must be claim-id sorted")
        if set(expected_by_collection[collection_name]) != {
            claim["content_sha256"] for claim in collection
        }:
            _fail("E_CLAIM_COVERAGE", f"claim map.{collection_name} is incomplete")
    if len(languages) > 1:
        for (collection_name, pair_id), entries in paired.items():
            if (
                len(entries) != 2
                or {language for _, language in entries} != set(languages)
                or len({claim["truth_id"] for claim, _ in entries}) != 1
                or len({claim["evidence_sha256"] for claim, _ in entries}) != 1
                or len({claim["claim_kind"] for claim, _ in entries}) != 1
            ):
                _fail(
                    "E_CLAIM_LANGUAGE",
                    f"{collection_name} language pair {pair_id} is incomplete or inconsistent",
                )
    return truth_ids


def _validate_glyphic_semantic(payload: Any) -> None:
    semantic = _object(payload, _GLYPHIC_SEMANTIC_FIELDS, "Glyphic semantic source")
    if semantic["schema_version"] != 1:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic semantic schema_version must be 1")
    if semantic["diagram_type"] not in {"architecture", "flowchart", "c4"}:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic diagram type is unsupported")
    if semantic["direction"] not in {"TB", "BT", "LR", "RL"}:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic direction is unsupported")

    def glyphic_id(value: Any, context: str, *, nullable: bool = False) -> str | None:
        if nullable and value is None:
            return None
        if not isinstance(value, str) or not _GLYPHIC_ID.fullmatch(value):
            _fail("E_GLYPHIC_SEMANTIC", f"{context} must be a bounded identifier")
        return value

    def glyphic_text(value: Any, context: str, *, nullable: bool = False) -> str | None:
        if nullable and value is None:
            return None
        if (
            not isinstance(value, str)
            or not value
            or len(value) > 120
            or "\n" in value
            or "\r" in value
        ):
            _fail("E_GLYPHIC_SEMANTIC", f"{context} must be single-line text within 120 characters")
        return value

    glyphic_text(semantic["accessibility_title"], "Glyphic accessibility_title")
    glyphic_id(semantic["accessibility_claim_id"], "Glyphic accessibility_claim_id")
    palette = _object(semantic["palette"], _GLYPHIC_PALETTE_FIELDS, "Glyphic palette")
    for field in sorted(_GLYPHIC_PALETTE_FIELDS):
        if not isinstance(palette[field], str) or not _HEX_COLOR.fullmatch(palette[field]):
            _fail("E_GLYPHIC_SEMANTIC", f"Glyphic palette.{field} must be a six-digit hex color")

    groups_raw = semantic["groups"]
    nodes_raw = semantic["nodes"]
    edges_raw = semantic["edges"]
    if not isinstance(groups_raw, list) or len(groups_raw) > 50:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic groups must contain at most 50 entries")
    if not isinstance(nodes_raw, list) or not nodes_raw or len(nodes_raw) > 100:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic nodes must contain 1-100 entries")
    if not isinstance(edges_raw, list) or len(edges_raw) > 200:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic edges must contain at most 200 entries")

    groups: list[dict[str, Any]] = []
    for index, raw in enumerate(groups_raw):
        context = f"Glyphic groups[{index}]"
        item = _object(raw, _GLYPHIC_GROUP_FIELDS, context)
        glyphic_id(item["id"], f"{context}.id")
        glyphic_text(item["label"], f"{context}.label")
        glyphic_id(item["parent_id"], f"{context}.parent_id", nullable=True)
        glyphic_id(item["claim_id"], f"{context}.claim_id")
        groups.append(item)

    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(nodes_raw):
        context = f"Glyphic nodes[{index}]"
        item = _object(raw, _GLYPHIC_NODE_FIELDS, context)
        glyphic_id(item["id"], f"{context}.id")
        glyphic_text(item["label"], f"{context}.label")
        glyphic_id(item["group_id"], f"{context}.group_id", nullable=True)
        glyphic_id(item["claim_id"], f"{context}.claim_id")
        if item["kind"] not in {
            "component",
            "service",
            "database",
            "person",
            "system",
            "external",
            "container",
        }:
            _fail("E_GLYPHIC_SEMANTIC", f"{context}.kind is unsupported")
        nodes.append(item)

    edges: list[dict[str, Any]] = []
    for index, raw in enumerate(edges_raw):
        context = f"Glyphic edges[{index}]"
        item = _object(raw, _GLYPHIC_EDGE_FIELDS, context)
        glyphic_id(item["source"], f"{context}.source")
        glyphic_id(item["target"], f"{context}.target")
        glyphic_text(item["label"], f"{context}.label", nullable=True)
        glyphic_id(item["claim_id"], f"{context}.claim_id", nullable=True)
        if (item["label"] is None) != (item["claim_id"] is None):
            _fail("E_GLYPHIC_SEMANTIC", f"{context} label and claim_id must both be null or text")
        edges.append(item)

    group_ids = {item["id"] for item in groups}
    node_ids = {item["id"] for item in nodes}
    all_ids = [item["id"] for item in groups] + [item["id"] for item in nodes]
    if len(set(all_ids)) != len(all_ids):
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic group and node ids must be unique")
    parent_by_group = {item["id"]: item["parent_id"] for item in groups}
    for group in groups:
        parent_id = group["parent_id"]
        if parent_id is not None and parent_id not in group_ids:
            _fail("E_GLYPHIC_SEMANTIC", f"Glyphic group {group['id']} references unknown parent")
        seen = {group["id"]}
        while parent_id is not None:
            if parent_id in seen:
                _fail("E_GLYPHIC_SEMANTIC", "Glyphic group hierarchy contains a cycle")
            seen.add(parent_id)
            parent_id = parent_by_group[parent_id]
    for node in nodes:
        if node["group_id"] is not None and node["group_id"] not in group_ids:
            _fail("E_GLYPHIC_SEMANTIC", f"Glyphic node {node['id']} references unknown group")
    for edge in edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            _fail("E_GLYPHIC_SEMANTIC", "Glyphic edge references unknown node")

    claim_ids = semantic["claim_ids"]
    if (
        not isinstance(claim_ids, list)
        or any(not isinstance(item, str) or not _GLYPHIC_ID.fullmatch(item) for item in claim_ids)
        or claim_ids != sorted(set(claim_ids))
    ):
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic claim_ids must be a sorted unique identifier list")
    used_claim_ids = sorted(
        [semantic["accessibility_claim_id"]]
        + [item["claim_id"] for item in groups]
        + [item["claim_id"] for item in nodes]
        + [item["claim_id"] for item in edges if item["claim_id"] is not None]
    )
    if claim_ids != used_claim_ids:
        _fail("E_GLYPHIC_SEMANTIC", "Glyphic claim_ids must exactly match semantic claims")


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
    if metadata["source_commit"] != _GLYPHIC_SOURCE_COMMIT:
        _fail("E_ENGINE_METADATA", "engine source_commit must match pinned Glyphic source")
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
    if (
        metadata["package_version"] != _GLYPHIC_VERSION
        or metadata["core_version"] != _GLYPHIC_VERSION
        or metadata["engine_schema_version"] != "1"
        or metadata["license_spdx"] != _GLYPHIC_LICENSE
        or not isinstance(metadata["node_version"], str)
        or not metadata["node_version"].startswith("22.")
        or not isinstance(metadata["sri"], str)
        or not re.fullmatch(r"sha512-[A-Za-z0-9+/]+={0,2}", metadata["sri"])
    ):
        _fail("E_ENGINE_METADATA", "engine version, runtime, SRI, or license is not pinned")
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
    readme_text: str | None,
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
        raw_asset = _artifact_bytes(root, reference, context)
        manifest_refs.append(reference)
        if asset["type"] not in {"svg", "png", "webp", "gif"}:
            _fail("E_BUNDLE_ASSET", f"{context}.type is unsupported")
        if engine_kind not in {"hand-authored", "glyphic"}:
            _fail("E_BUNDLE_ASSET", f"{context}.engine_kind is unsupported")
        if production_kind not in {"static", "hybrid", "motion"}:
            _fail("E_BUNDLE_ASSET", f"{context}.production_kind is unsupported")
        if not isinstance(asset["alt"], str) or not asset["alt"].strip():
            _fail("E_README_ACCESSIBILITY", f"{context}.alt must be useful text")
        if not isinstance(asset["caption"], str) or not asset["caption"].strip():
            _fail("E_README_ACCESSIBILITY", f"{context}.caption must be useful text")
        _validate_asset_bytes(raw_asset, asset["type"], reference["path"], context)
        bound_truth_ids = _string_list(asset["truth_ids"], f"{context}.truth_ids", allow_empty=False)
        if not set(bound_truth_ids).issubset(truth_ids):
            _fail("E_BUNDLE_CLAIM", f"{context} references unknown truth_id")
        if engine_kind == "glyphic":
            if production_kind != "static":
                _fail("E_BUNDLE_ASSET", "Glyphic output must use static production")
            if asset["type"] != "svg" or not reference["path"].endswith(".svg"):
                _fail("E_BUNDLE_ASSET", "Glyphic output must be standalone SVG")
            semantic_payload, semantic_ref = _artifact_json(root, asset["semantic"], f"{context}.semantic")
            _validate_glyphic_semantic(semantic_payload)
            metadata_payload, _ = _artifact_json(
                root,
                asset["engine_metadata"],
                f"{context}.engine_metadata",
            )
            fallback_ref = _reference(asset["fallback"], f"{context}.fallback")
            fallback_raw = _artifact_bytes(root, fallback_ref, f"{context}.fallback")
            if not fallback_ref["path"].endswith(".svg"):
                _fail("E_BUNDLE_ASSET", "Glyphic fallback must be static SVG")
            if not semantic_ref["path"].endswith(".glyphic.json"):
                _fail("E_BUNDLE_ASSET", "Glyphic semantic source must use .glyphic.json")
            metadata_ref = _reference(asset["engine_metadata"], f"{context}.engine_metadata")
            if not metadata_ref["path"].endswith(".engine.json"):
                _fail("E_BUNDLE_ASSET", "Glyphic metadata must use .engine.json")
            _validate_svg(fallback_raw, f"{context}.fallback")
            _validate_engine_metadata(
                metadata_payload,
                asset_sha256=reference["sha256"],
                semantic_sha256=semantic_ref["sha256"],
            )
            labels = (
                [item["label"] for item in semantic_payload["groups"]]
                + [item["label"] for item in semantic_payload["nodes"]]
                + [
                    item["label"]
                    for item in semantic_payload["edges"]
                    if item["label"] is not None
                ]
            )
            _validate_svg(
                raw_asset,
                context,
                expected_title=semantic_payload["accessibility_title"],
                expected_labels=labels,
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
                source_raw = _artifact_bytes(root, source_ref, f"{context}.{name}")
                if PurePosixPath(source_ref["path"]).suffix not in expected_suffixes[name]:
                    _fail("E_BUNDLE_ASSET", f"{context}.{name} has unsupported file type")
                if source_ref["path"].endswith(".svg"):
                    _validate_svg(source_raw, f"{context}.{name}")
            if len({item["path"] for item in hybrid_refs.values()}) != 4:
                _fail("E_BUNDLE_ASSET", "hybrid editable sources and fallback must be distinct")
        elif production_kind == "motion":
            if asset["type"] != "gif":
                _fail("E_BUNDLE_ASSET", "motion output must publish GIF")
            if asset["motion_approved"] is not True:
                _fail("E_VISUAL_MOTION_APPROVAL", "motion requires explicit approval")
            for name in ("source", "fallback"):
                source_ref = _reference(asset[name], f"{context}.{name}")
                source_raw = _artifact_bytes(root, source_ref, f"{context}.{name}")
                if not source_ref["path"].endswith(".svg"):
                    _fail("E_BUNDLE_ASSET", f"{context}.{name} must be static SVG")
                _validate_svg(source_raw, f"{context}.{name}")
            motion_spec, _ = _artifact_json(
                root,
                asset["motion_spec"],
                f"{context}.motion_spec",
            )
            if motion_spec.get("schema_version") != 1:
                _fail("E_SCHEMA_VERSION", "motion spec requires schema_version 1")
        if readme_text is not None:
            matching = [
                (alt, line)
                for source, alt, line in image_references(readme_text)
                if source.removeprefix("./").split("#", 1)[0].split("?", 1)[0]
                == reference["path"]
            ]
            if [alt for alt, _ in matching] != [asset["alt"]]:
                _fail(
                    "E_README_ACCESSIBILITY",
                    f"{context} requires one README image with matching alt",
                )
            lines = readme_text.splitlines()
            image_line = matching[0][1]
            if not any(
                asset["caption"] in line
                for line in lines[image_line : image_line + 3]
            ):
                _fail(
                    "E_README_ACCESSIBILITY",
                    f"{context}.caption must immediately follow README image",
                )
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

    readme_path_value: str | None = None
    readme_text: str | None = None
    if mode == "readme":
        readme_ref = _reference(readme, "bundle candidate.readme")
        readme_path_value = readme_ref["path"]
        readme_raw = _artifact_bytes(artifact_root, readme_ref, "bundle candidate.readme")
        try:
            readme_text = readme_raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ContractError("E_INPUT_ENCODING", "candidate README must be UTF-8") from exc
        readme_path = artifact_root.joinpath(*PurePosixPath(readme_ref["path"]).parts)
        readme_issues, _, _ = audit_readme(readme_path, root=artifact_root)
        if readme_issues:
            _fail("E_README_AUDIT", readme_issues[0])
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
    validated_plan = _validate_plan(plan, mode)
    if readme_text is not None:
        for command in validated_plan["commands"]:
            if command not in readme_text:
                _fail("E_README_COMMAND", f"README omits planned command: {command}")
        if set(validated_plan["languages"]) == {"en", "zh"}:
            links = re.findall(
                r"(?:href=[\"']|\]\()(\.?/?README(?:_zh)?\.md)(?:[\"')])",
                readme_text,
            )
            if not links:
                _fail("E_README_LANGUAGE", "bilingual README must link its language pair")
    if retrieval.get("schema_version") != 1:
        _fail("E_SCHEMA_VERSION", "retrieval packet requires schema_version 1")
    fact_hashes = _load_evidence_facts(
        artifact_root,
        base_sha=target["base_sha"],
        evidence_ids=validated_plan["evidence_ids"],
    )
    _validate_asset_manifest(
        asset_manifest,
        root=artifact_root,
        candidate_assets=candidate_assets,
        truth_ids=set(fact_hashes),
        readme_text=readme_text,
    )
    _validate_claim_map(
        claims,
        markdown_expected=_readme_claim_inputs(
            artifact_root,
            readme_path=readme_path_value,
            readme_text=readme_text,
            languages=validated_plan["languages"],
        ),
        diagram_expected=_diagram_claim_inputs(
            asset_manifest,
            root=artifact_root,
            default_language=validated_plan["languages"][0],
        ),
        fact_hashes=fact_hashes,
        evidence_ids=validated_plan["evidence_ids"],
        languages=validated_plan["languages"],
    )
    return {
        "schema_version": 1,
        "status": "pass",
        "mode": mode,
        "bundle_sha256": canonical_sha256(bundle),
        "candidate_count": (1 if readme is not None else 0) + len(candidate_assets),
    }


def _empty_advisory_metrics() -> dict[str, dict[str, int]]:
    return {
        name: {"covered": 0, "total": 0}
        for name in _ADVISORY_METRICS
    }


def evaluate_generated_bundle(payload: Any, artifact_root: Path) -> dict[str, object]:
    bundle_sha256 = canonical_sha256(payload)
    try:
        validate_generated_bundle(payload, artifact_root)
    except ContractError as exc:
        return {
            "schema_version": 1,
            "status": "fail",
            "decision_basis": "hard-gates-only",
            "bundle_sha256": bundle_sha256,
            "hard_gate": {
                "status": "fail",
                "findings": [{"code": exc.code, "message": str(exc)}],
            },
            "advisory": _empty_advisory_metrics(),
        }

    bundle = cast(dict[str, Any], payload)
    candidate = cast(dict[str, Any], bundle["candidate"])
    artifacts = cast(dict[str, Any], bundle["artifacts"])
    plan, _ = _artifact_json(
        artifact_root,
        artifacts["plan"],
        "bundle artifacts.plan",
    )
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

    readme_text = ""
    if candidate["readme"] is not None:
        readme_ref = _reference(candidate["readme"], "bundle candidate.readme")
        readme_text = _artifact_bytes(
            artifact_root,
            readme_ref,
            "bundle candidate.readme",
        ).decode("utf-8")

    markdown_claims = cast(list[dict[str, Any]], claims["markdown_blocks"])
    diagram_claims = cast(list[dict[str, Any]], claims["diagram_labels"])
    claim_entries = [*markdown_claims, *diagram_claims]
    assets = cast(list[dict[str, Any]], asset_manifest["assets"])
    expected_diagram_labels = _diagram_claim_inputs(
        asset_manifest,
        root=artifact_root,
        default_language=cast(list[str], plan["languages"])[0],
    )

    planned_evidence = set(cast(list[str], plan["evidence_ids"]))
    used_evidence = {
        cast(str, claim["truth_id"])
        for claim in claim_entries
    }
    for asset in assets:
        used_evidence.update(cast(list[str], asset["truth_ids"]))

    pair_counts: dict[str, int] = {}
    for claim in claim_entries:
        pair_id = claim["language_pair_id"]
        if isinstance(pair_id, str):
            pair_counts[pair_id] = pair_counts.get(pair_id, 0) + 1
    language_count = len(cast(list[str], plan["languages"]))

    retrieved_sections: set[str] = set()
    records = retrieval.get("records", [])
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("section_intents"), list):
                retrieved_sections.update(
                    item
                    for item in record["section_intents"]
                    if isinstance(item, str)
                )
    planned_sections = set(cast(list[str], plan["sections"]))
    commands = cast(list[str], plan["commands"])

    advisory = {
        "claim_coverage": {
            "covered": len(claim_entries),
            "total": (
                len(segment_markdown_blocks(readme_text))
                + len(expected_diagram_labels)
            ),
        },
        "diagram_label_coverage": {
            "covered": len(diagram_claims),
            "total": len(expected_diagram_labels),
        },
        "evidence_sources": {
            "covered": len(planned_evidence.intersection(used_evidence)),
            "total": len(planned_evidence),
        },
        "language_truth_pairs": {
            "covered": sum(
                count == language_count
                for count in pair_counts.values()
            ),
            "total": len(pair_counts),
        },
        "observable_commands": {
            "covered": sum(command in readme_text for command in commands),
            "total": len(commands),
        },
        "section_intents": {
            "covered": len(planned_sections.intersection(retrieved_sections)),
            "total": len(planned_sections),
        },
        "visual_provenance": {
            "covered": len(assets),
            "total": len(assets),
        },
    }
    return {
        "schema_version": 1,
        "status": "pass",
        "decision_basis": "hard-gates-only",
        "bundle_sha256": bundle_sha256,
        "hard_gate": {"status": "pass", "findings": []},
        "advisory": advisory,
    }


def _validate_evaluation_report(
    payload: Any,
    *,
    bundle_sha256: str,
) -> None:
    report = validate_contract(
        payload,
        required=_EVALUATION_REPORT_FIELDS,
        optional=set(),
        context="evaluation report",
    )
    if (
        report["status"] != "pass"
        or report["decision_basis"] != "hard-gates-only"
        or report["bundle_sha256"] != bundle_sha256
    ):
        _fail("E_PR_EVALUATION", "evaluation does not pass for this exact bundle")
    hard_gate = _object(
        report["hard_gate"],
        {"status", "findings"},
        "evaluation report.hard_gate",
    )
    if hard_gate["status"] != "pass" or hard_gate["findings"] != []:
        _fail("E_PR_EVALUATION", "evaluation hard gate must pass without findings")
    advisory = _object(
        report["advisory"],
        set(_ADVISORY_METRICS),
        "evaluation report.advisory",
    )
    for name in _ADVISORY_METRICS:
        pair = _object(
            advisory[name],
            {"covered", "total"},
            f"evaluation report.advisory.{name}",
        )
        covered = pair["covered"]
        total = pair["total"]
        if (
            type(covered) is not int
            or type(total) is not int
            or covered < 0
            or total < 0
            or covered > total
        ):
            _fail("E_PR_EVALUATION", f"evaluation metric {name} is invalid")


def _git_output(root: Path, *arguments: str) -> bytes:
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["LC_ALL"] = "C"
    try:
        result = subprocess.run(
            [
                "git",
                "--no-optional-locks",
                "-C",
                str(root),
                *arguments,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
            timeout=10,
            env=environment,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise ContractError("E_PR_GIT", "local Git inspection is unavailable") from exc
    if result.returncode != 0:
        _fail("E_PR_GIT", f"local Git inspection failed: {arguments[0]}")
    return result.stdout


def _github_repository_from_origin(value: str) -> str:
    patterns = (
        r"https://github\.com/(?P<repo>[^?#]+?)(?:\.git)?\Z",
        r"git@github\.com:(?P<repo>.+?)(?:\.git)?\Z",
        r"ssh://git@github\.com/(?P<repo>.+?)(?:\.git)?\Z",
    )
    for pattern in patterns:
        match = re.fullmatch(pattern, value)
        if match and _GITHUB_REPOSITORY.fullmatch(match.group("repo")):
            return match.group("repo")
    _fail("E_PR_TARGET", "origin must identify the exact GitHub repository")


def _publish_path(value: str, kind: str) -> PurePosixPath:
    path = _relative_path(value, f"PR {kind} path")
    if (
        any(part in _PR_EXCLUDED_PARTS for part in path.parts)
        or path.name in _PR_EXCLUDED_NAMES
    ):
        _fail("E_PR_PATH", f"excluded path cannot enter PR bundle: {value}")
    if kind == "readme" and path.name not in {"README.md", "README_zh.md"}:
        _fail("E_PR_PATH", "README candidate must target README.md or README_zh.md")
    if kind in {"asset", "semantic"} and path.parts[:2] != ("assets", "readme"):
        _fail("E_PR_PATH", f"{kind} candidate must stay under assets/readme")
    if kind == "semantic" and not path.name.endswith(".glyphic.json"):
        _fail("E_PR_PATH", "Glyphic semantic source must use .glyphic.json")
    return path


def _target_file_sha256(root: Path, path: PurePosixPath) -> str | None:
    current = root
    for index, part in enumerate(path.parts):
        current = current / part
        try:
            info = current.lstat()
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(info.st_mode):
            _fail("E_PR_PATH", f"target path crosses symlink: {path.as_posix()}")
        if index < len(path.parts) - 1:
            if not stat.S_ISDIR(info.st_mode):
                _fail("E_PR_PATH", f"target parent is not a directory: {path.as_posix()}")
        elif not stat.S_ISREG(info.st_mode):
            _fail("E_PR_PATH", f"target candidate is not a regular file: {path.as_posix()}")
    return hashlib.sha256(
        _read_scanned_file(current, current.lstat(), path.as_posix())
    ).hexdigest()


def _candidate_change(
    *,
    artifact_root: Path,
    target_root: Path,
    reference: dict[str, str],
    kind: str,
) -> dict[str, object]:
    path = _publish_path(reference["path"], kind)
    _artifact_bytes(artifact_root, reference, f"PR {kind} candidate")
    before_sha256 = _target_file_sha256(target_root, path)
    after_sha256 = reference["sha256"]
    change = (
        "add"
        if before_sha256 is None
        else "unchanged"
        if before_sha256 == after_sha256
        else "modify"
    )
    return {
        "path": path.as_posix(),
        "before_sha256": before_sha256,
        "after_sha256": after_sha256,
        "change": change,
    }


def build_pr_bundle(
    payload: Any,
    evaluation: Any,
    artifact_root: Path,
    target_root: Path,
) -> dict[str, object]:
    artifact_root = artifact_root.resolve(strict=True)
    try:
        target_info = target_root.lstat()
    except FileNotFoundError as exc:
        raise ContractError("E_PR_TARGET", "target repository is missing") from exc
    if stat.S_ISLNK(target_info.st_mode) or not stat.S_ISDIR(target_info.st_mode):
        _fail("E_PR_TARGET", "target repository must be a real directory")
    target_root = target_root.resolve(strict=True)
    try:
        artifact_root.relative_to(target_root)
    except ValueError:
        pass
    else:
        _fail("E_PR_PATH", "pipeline run directory must stay outside target repository")

    bundle = validate_contract(
        payload,
        required=_BUNDLE_FIELDS,
        optional=set(),
        context="generated README bundle",
    )
    target = _object(bundle["target"], _TARGET_FIELDS, "bundle target")
    repository = _text(target["repository"], "bundle target.repository")
    base_sha = target["base_sha"]
    if not isinstance(base_sha, str) or not _COMMIT.fullmatch(base_sha):
        _fail("E_BUNDLE_TARGET", "bundle target.base_sha must be immutable")
    if not _GITHUB_REPOSITORY.fullmatch(repository):
        _fail("E_PR_TARGET", "bundle target.repository must be owner/name")

    head = _git_output(target_root, "rev-parse", "--verify", "HEAD").decode(
        "ascii",
        errors="strict",
    ).strip()
    if head != base_sha:
        _fail("E_PR_BASE", "target HEAD differs from bundle base SHA")
    origin = _git_output(target_root, "remote", "get-url", "origin").decode(
        "utf-8",
        errors="strict",
    ).strip()
    if _github_repository_from_origin(origin) != repository:
        _fail("E_PR_TARGET", "target origin differs from bundle repository")
    cached_before = _git_output(
        target_root,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
    )
    worktree = _git_output(
        target_root,
        "status",
        "--porcelain=v1",
        "-z",
        "--untracked-files=all",
    )
    if worktree:
        _fail("E_PR_WORKTREE", "target worktree and index must be clean")

    validate_generated_bundle(bundle, artifact_root)
    bundle_sha256 = canonical_sha256(bundle)
    _validate_evaluation_report(evaluation, bundle_sha256=bundle_sha256)
    candidate = cast(dict[str, Any], bundle["candidate"])
    references: list[tuple[dict[str, str], str]] = []
    if candidate["readme"] is not None:
        references.append(
            (_reference(candidate["readme"], "bundle candidate.readme"), "readme")
        )
    references.extend(
        (
            _reference(value, f"bundle candidate.assets[{index}]"),
            "asset",
        )
        for index, value in enumerate(cast(list[Any], candidate["assets"]))
    )
    candidate_files = sorted(
        (
            _candidate_change(
                artifact_root=artifact_root,
                target_root=target_root,
                reference=reference,
                kind=kind,
            )
            for reference, kind in references
        ),
        key=lambda item: cast(str, item["path"]),
    )

    artifacts = cast(dict[str, Any], bundle["artifacts"])
    asset_manifest, _ = _artifact_json(
        artifact_root,
        artifacts["asset_manifest"],
        "bundle artifacts.asset_manifest",
    )
    semantic_references = [
        _reference(asset["semantic"], f"asset manifest.assets[{index}].semantic")
        for index, asset in enumerate(cast(list[dict[str, Any]], asset_manifest["assets"]))
        if asset.get("engine_kind") == "glyphic"
    ]
    semantic_sources = sorted(
        (
            _candidate_change(
                artifact_root=artifact_root,
                target_root=target_root,
                reference=reference,
                kind="semantic",
            )
            for reference in semantic_references
        ),
        key=lambda item: cast(str, item["path"]),
    )
    paths = [
        cast(str, item["path"])
        for item in [*candidate_files, *semantic_sources]
    ]
    if len(paths) != len(set(paths)):
        _fail("E_PR_PATH", "PR candidate paths must be unique")
    if not any(
        item["change"] != "unchanged"
        for item in [*candidate_files, *semantic_sources]
    ):
        _fail("E_PR_NO_CHANGES", "candidate bytes equal target base")
    if _git_output(
        target_root,
        "diff",
        "--cached",
        "--binary",
        "--no-ext-diff",
    ) != cached_before:
        _fail("E_PR_INDEX", "cached diff changed during PR bundle inspection")

    mode = cast(str, bundle["mode"])
    metadata = {
        "commit_message": (
            "docs(readme): refresh project showcase"
            if mode == "readme"
            else "docs(readme): refresh showcase assets"
        ),
        "pull_request_title": (
            "docs: refresh README showcase"
            if mode == "readme"
            else "docs: refresh README showcase assets"
        ),
        "pull_request_body": (
            "## Summary\n\n"
            "- Refresh evidence-bound README showcase artifacts\n\n"
            "## Verification\n\n"
            "- Deterministic hard gates: pass\n"
        ),
    }
    projection: dict[str, object] = {
        "schema_version": 1,
        "mode": mode,
        "target": {
            "repository": repository,
            "base_sha": base_sha,
            "branch": f"readme-showcase/{bundle_sha256[:12]}",
        },
        "candidate_files": candidate_files,
        "semantic_sources": semantic_sources,
        "evaluation": {
            "status": "pass",
            "bundle_sha256": bundle_sha256,
            "report_sha256": canonical_sha256(evaluation),
        },
        "metadata": metadata,
        "exclusions": [
            ".omo/**",
            "evaluation-only/**",
            "node_modules/**",
            "previews/**",
            "run-artifacts/**",
        ],
    }
    return {
        **projection,
        "status": "ready",
        "fingerprint": canonical_sha256(projection),
    }
