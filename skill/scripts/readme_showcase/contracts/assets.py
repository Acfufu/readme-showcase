from __future__ import annotations

import copy
import hashlib
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...pipeline_contracts import ContractError, canonical_json_bytes
from .common import MAX_SOURCE_BYTES, normalize_posix_path, normalize_text, read_source_bytes
from .evidence import validate_evidence_graph


ASSET_MANIFEST_SCHEMA_VERSION = 2
MAX_ASSETS = 10_000
ASSET_LOCALES = frozenset({"en", "zh"})
PROVENANCE_KINDS = frozenset({"hand-authored", "derived", "generated"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")
_ASSET_FIELDS = {
    "asset_id", "path", "locale", "provenance", "artifact_sha256",
    "candidate_sha256", "evidence_ids",
}
_PROVENANCE_FIELDS = {"kind", "path", "sha256"}


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_float(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_float(child, f"{path}.{key}")


def _closed(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return value


def _sha(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError("E_BUNDLE_HASH", f"{context} must be lowercase SHA-256")
    return value


def _path(value: Any, context: str) -> str:
    try:
        return normalize_posix_path(value)
    except ValueError as exc:
        raise ContractError("E_PATH", f"{context} must be safe relative POSIX path") from exc


def _ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} requires one or more evidence IDs")
    result = [normalize_text(item, f"{context}[]", maximum=512) for item in value]
    if any(not _EVIDENCE_ID.fullmatch(item) for item in result):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} must contain normative Evidence v2 IDs")
    if len(result) != len(set(result)):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} contains duplicate evidence IDs")
    return result


def _safe_read(root: Path, path: str, context: str) -> bytes:
    try:
        return read_source_bytes(root, path, maximum=MAX_SOURCE_BYTES)
    except ValueError as exc:
        if getattr(exc, "code", None) in {"E_EVIDENCE_PATH", "E_INPUT_PATH", "E_INPUT_NOT_FOUND"}:
            raise ContractError("E_PATH", f"{context} must be a regular file below artifact root") from exc
        raise


def validate_asset_manifest(
    payload: Any,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
    artifact_root: Path | None = None,
    candidate_assets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    _reject_float(payload)
    manifest = _closed(payload, {"schema_version", "assets"}, "asset manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != ASSET_MANIFEST_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "asset manifest requires schema_version 2")
    raw_assets = manifest["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_ASSETS:
        raise ContractError("E_SCHEMA_TYPE", f"asset manifest.assets must contain at most {MAX_ASSETS} entries")

    facts: dict[str, dict[str, Any]] | None = None
    if evidence_graph is not None:
        graph = validate_evidence_graph(dict(evidence_graph))
        facts = {fact["fact_id"]: fact for fact in graph["facts"]}
    candidates: dict[str, str] | None = None
    if candidate_assets is not None:
        candidates = {}
        for index, reference in enumerate(candidate_assets):
            ref = _closed(dict(reference), {"path", "sha256"}, f"candidate.assets[{index}]")
            path = _path(ref["path"], f"candidate.assets[{index}].path")
            if path in candidates:
                raise ContractError("E_BUNDLE_ASSET", "candidate assets contain duplicate path")
            candidates[path] = _sha(ref["sha256"], f"candidate.assets[{index}].sha256")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, raw in enumerate(raw_assets):
        context = f"asset manifest.assets[{index}]"
        asset = _closed(raw, _ASSET_FIELDS, context)
        asset_id = normalize_text(asset["asset_id"], f"{context}.asset_id", maximum=512)
        path = _path(asset["path"], f"{context}.path")
        if asset_id in seen_ids or path in seen_paths:
            raise ContractError("E_BUNDLE_ASSET", f"{context} duplicates asset identity or path")
        seen_ids.add(asset_id)
        seen_paths.add(path)
        locale = asset["locale"]
        if locale not in ASSET_LOCALES:
            raise ContractError("E_README_LANGUAGE", f"{context}.locale is unsupported")
        provenance = _closed(asset["provenance"], _PROVENANCE_FIELDS, f"{context}.provenance")
        kind = provenance["kind"]
        if kind not in PROVENANCE_KINDS:
            raise ContractError("E_BUNDLE_ASSET", f"{context}.provenance.kind is unsupported")
        source_path = _path(provenance["path"], f"{context}.provenance.path")
        if source_path == path:
            raise ContractError("E_BUNDLE_HASH", f"{context} cannot use candidate bytes as provenance")
        source_hash = _sha(provenance["sha256"], f"{context}.provenance.sha256")
        artifact_hash = _sha(asset["artifact_sha256"], f"{context}.artifact_sha256")
        candidate_hash = _sha(asset["candidate_sha256"], f"{context}.candidate_sha256")
        if artifact_hash != candidate_hash:
            raise ContractError("E_BUNDLE_HASH", f"{context} candidate and artifact hashes differ")
        identifiers = _ids(asset["evidence_ids"], f"{context}.evidence_ids")
        if facts is not None:
            if not set(identifiers).issubset(facts):
                raise ContractError("E_CLAIM_EVIDENCE", f"{context} references missing evidence")
            source_facts = [
                facts[identifier]
                for identifier in identifiers
                if facts[identifier]["source"]["path"] == source_path
                and facts[identifier]["source_sha256"] == source_hash
            ]
            if not source_facts:
                raise ContractError("E_BUNDLE_HASH", f"{context} provenance is not bound to normative evidence")
        if candidates is not None and candidates.get(path) != candidate_hash:
            raise ContractError("E_BUNDLE_HASH", f"{context} differs from candidate reference")
        if artifact_root is not None:
            if hashlib.sha256(_safe_read(artifact_root, source_path, f"{context}.provenance")).hexdigest() != source_hash:
                raise ContractError("E_BUNDLE_HASH", f"{context} provenance bytes changed")
            if hashlib.sha256(_safe_read(artifact_root, path, context)).hexdigest() != artifact_hash:
                raise ContractError("E_BUNDLE_HASH", f"{context} artifact bytes changed")
        normalized.append({
            "asset_id": asset_id,
            "path": path,
            "locale": locale,
            "provenance": {"kind": kind, "path": source_path, "sha256": source_hash},
            "artifact_sha256": artifact_hash,
            "candidate_sha256": candidate_hash,
            "evidence_ids": identifiers,
        })
    if [item["path"] for item in normalized] != sorted(item["path"] for item in normalized):
        raise ContractError("E_BUNDLE_ASSET", "asset manifest must use path order")
    if candidates is not None and set(candidates) != seen_paths:
        raise ContractError("E_BUNDLE_ASSET", "candidate assets and asset manifest differ")
    return copy.deepcopy({"schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": normalized})


def canonical_asset_manifest_bytes(payload: Any, **kwargs: Any) -> bytes:
    return canonical_json_bytes(validate_asset_manifest(payload, **kwargs))


def read_asset_manifest(payload: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    if payload.get("schema_version") == 1:
        legacy = copy.deepcopy(dict(payload))
        if set(legacy) != {"schema_version", "assets"} or not isinstance(legacy["assets"], list):
            raise ContractError("E_SCHEMA_FIELDS", "v1 asset manifest fields are invalid")
        return legacy
    return validate_asset_manifest(payload, **kwargs)
