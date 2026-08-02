from __future__ import annotations

import copy
import re
from typing import Any, Mapping

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from .common import normalize_text
from .evidence import validate_evidence_graph


CLAIM_MAP_SCHEMA_VERSION = 2
MAX_CLAIMS = 100_000
CLAIM_KINDS = frozenset({"factual", "instruction", "decorative"})
SUPPORT_LEVELS = frozenset({"direct", "composed", "documented-only"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")
_CLAIM_FIELDS = {
    "claim_id", "content_sha256", "claim_kind", "evidence_ids",
    "language_pair_id", "support_level",
}
_V1_CLAIM_FIELDS = {
    "claim_id", "content_sha256", "claim_kind", "truth_id",
    "evidence_sha256", "language_pair_id",
}


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


def _evidence_ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} requires one or more evidence IDs")
    identifiers = [normalize_text(item, f"{context}[]", maximum=512) for item in value]
    if any(not _EVIDENCE_ID.fullmatch(item) for item in identifiers):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} must contain normative Evidence v2 IDs")
    if len(identifiers) != len(set(identifiers)):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} contains duplicate evidence IDs")
    return identifiers


def _locale(claim_id: str) -> str | None:
    parts = claim_id.split(":", 2)
    return parts[1] if len(parts) == 3 and parts[1] in {"en", "zh"} else None


def validate_claim_map(
    payload: Any,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _reject_float(payload)
    claim_map = _closed(
        payload,
        {"schema_version", "markdown_blocks", "diagram_labels"},
        "claim map",
    )
    if type(claim_map["schema_version"]) is not int or claim_map["schema_version"] != CLAIM_MAP_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "claim map requires schema_version 2")
    known_ids: set[str] | None = None
    if evidence_graph is not None:
        graph = validate_evidence_graph(dict(evidence_graph))
        known_ids = {fact["fact_id"] for fact in graph["facts"]}

    normalized: dict[str, Any] = {"schema_version": CLAIM_MAP_SCHEMA_VERSION}
    seen_claims: set[str] = set()
    seen_content: set[str] = set()
    pairs: dict[str, list[dict[str, Any]]] = {}
    total = 0
    for collection_name in ("markdown_blocks", "diagram_labels"):
        raw_collection = claim_map[collection_name]
        if not isinstance(raw_collection, list):
            raise ContractError("E_SCHEMA_TYPE", f"claim map.{collection_name} must be an array")
        total += len(raw_collection)
        if total > MAX_CLAIMS:
            raise ContractError("E_SCHEMA_TYPE", f"claim map may contain at most {MAX_CLAIMS} claims")
        collection: list[dict[str, Any]] = []
        for index, raw in enumerate(raw_collection):
            context = f"claim map.{collection_name}[{index}]"
            claim = _closed(raw, _CLAIM_FIELDS, context)
            claim_id = normalize_text(claim["claim_id"], f"{context}.claim_id", maximum=512)
            content_hash = claim["content_sha256"]
            if not isinstance(content_hash, str) or not _SHA256.fullmatch(content_hash):
                raise ContractError("E_BUNDLE_CLAIM", f"{context}.content_sha256 must be lowercase SHA-256")
            if claim_id in seen_claims or content_hash in seen_content:
                raise ContractError("E_CLAIM_DUPLICATE", f"{context} duplicates claim identity or content")
            seen_claims.add(claim_id)
            seen_content.add(content_hash)
            claim_kind = claim["claim_kind"]
            support = claim["support_level"]
            if claim_kind not in CLAIM_KINDS:
                raise ContractError("E_BUNDLE_CLAIM", f"{context}.claim_kind is unsupported")
            if support not in SUPPORT_LEVELS:
                raise ContractError("E_BUNDLE_CLAIM", f"{context}.support_level is unsupported")
            identifiers = _evidence_ids(claim["evidence_ids"], f"{context}.evidence_ids")
            if support == "composed" and len(identifiers) < 2:
                raise ContractError("E_CLAIM_EVIDENCE", f"{context} composed support requires at least two evidence IDs")
            if known_ids is not None and not set(identifiers).issubset(known_ids):
                raise ContractError("E_CLAIM_EVIDENCE", f"{context} references missing or stale evidence")
            pair_id = claim["language_pair_id"]
            if pair_id is not None:
                pair_id = normalize_text(pair_id, f"{context}.language_pair_id", maximum=512)
                if _locale(claim_id) is None:
                    raise ContractError("E_CLAIM_LANGUAGE", f"{context} paired claim ID must declare en or zh locale")
            item = {
                "claim_id": claim_id,
                "content_sha256": content_hash,
                "claim_kind": claim_kind,
                "evidence_ids": identifiers,
                "language_pair_id": pair_id,
                "support_level": support,
            }
            collection.append(item)
            if pair_id is not None:
                pairs.setdefault(pair_id, []).append(item)
        if [item["claim_id"] for item in collection] != sorted(item["claim_id"] for item in collection):
            raise ContractError("E_CLAIM_COVERAGE", f"claim map.{collection_name} must be claim-id sorted")
        normalized[collection_name] = collection

    for pair_id, pair in pairs.items():
        locales = [_locale(item["claim_id"]) for item in pair]
        if sorted(locales) != ["en", "zh"]:
            raise ContractError("E_CLAIM_LANGUAGE", f"language pair {pair_id} requires exactly en and zh")
        first, second = pair
        if (
            first["evidence_ids"] != second["evidence_ids"]
            or first["support_level"] != second["support_level"]
            or first["claim_kind"] != second["claim_kind"]
        ):
            raise ContractError("E_CLAIM_LANGUAGE", f"language pair {pair_id} changed evidence or support semantics")
    return copy.deepcopy(normalized)


def canonical_claim_map_bytes(payload: Any, *, evidence_graph: Mapping[str, Any] | None = None) -> bytes:
    return canonical_json_bytes(validate_claim_map(payload, evidence_graph=evidence_graph))


def adapt_v1_claim_map(
    payload: Mapping[str, Any],
    *,
    evidence_id_map: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    legacy = _closed(copy.deepcopy(dict(payload)), {"schema_version", "markdown_blocks", "diagram_labels"}, "v1 claim map")
    if type(legacy["schema_version"]) is not int or legacy["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "v1 claim map requires schema_version 1")
    adapted: dict[str, Any] = {"schema_version": 2}
    for collection_name in ("markdown_blocks", "diagram_labels"):
        collection = legacy[collection_name]
        if not isinstance(collection, list):
            raise ContractError("E_SCHEMA_TYPE", f"v1 claim map.{collection_name} must be an array")
        rows: list[dict[str, Any]] = []
        for index, raw in enumerate(collection):
            claim = _closed(raw, _V1_CLAIM_FIELDS, f"v1 claim map.{collection_name}[{index}]")
            digest = claim["evidence_sha256"]
            if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
                raise ContractError("E_CLAIM_EVIDENCE", "v1 claim evidence hash is invalid")
            truth_id = normalize_text(claim["truth_id"], "v1 claim truth_id", maximum=512)
            rows.append({
                "claim_id": claim["claim_id"],
                "content_sha256": claim["content_sha256"],
                "claim_kind": claim["claim_kind"],
                "evidence_ids": [(evidence_id_map or {}).get(truth_id, f"legacy:{canonical_sha256({'evidence_sha256': digest, 'truth_id': truth_id})}")],
                "language_pair_id": claim["language_pair_id"],
                "support_level": "direct",
            })
        adapted[collection_name] = rows
    return validate_claim_map(adapted)


def read_claim_map(payload: Mapping[str, Any], *, evidence_graph: Mapping[str, Any] | None = None) -> dict[str, Any]:
    return adapt_v1_claim_map(payload) if payload.get("schema_version") == 1 else validate_claim_map(payload, evidence_graph=evidence_graph)
