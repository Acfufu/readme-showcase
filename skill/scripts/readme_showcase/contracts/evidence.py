from __future__ import annotations

import copy
import hashlib
import re
import unicodedata
from typing import Any, Mapping

from .common import (
    ContractError,
    canonical_sha256,
    normalize_posix_path,
    normalize_text,
    validate_bounded_json,
)


EVIDENCE_SCHEMA_VERSION = 2
EVIDENCE_KINDS = frozenset(
    {
        "file-presence",
        "file-snippet",
        "config-value",
        "package-metadata",
        "code-symbol",
        "cli-entrypoint",
        "test-observation",
        "command-observation",
        "git-metadata",
        "documentation-statement",
    }
)
CONFIDENCE_LEVELS = frozenset({"observed", "derived", "documented"})
MAX_FACTS = 10_000
_PREFIXES = {
    "file-presence": "file",
    "file-snippet": "snippet",
    "config-value": "config",
    "package-metadata": "package",
    "code-symbol": "symbol",
    "cli-entrypoint": "cli",
    "test-observation": "test",
    "command-observation": "command",
    "git-metadata": "git",
    "documentation-statement": "documentation",
}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SYMBOL = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:(?:::|\.)[A-Za-z_][A-Za-z0-9_]*)*\Z")
_JSON_POINTER = re.compile(r"(?:/(?:[^~/]|~[01])*)*\Z")
_FACT_FIELDS = frozenset(
    {"fact_id", "kind", "source", "semantic_key", "value", "source_sha256", "evidence_sha256", "confidence"}
)


def _kind(value: Any) -> str:
    if value not in EVIDENCE_KINDS:
        raise ContractError("E_EVIDENCE_KIND", "evidence kind is unsupported")
    return value


def normalize_locator(locator: Any) -> dict[str, Any]:
    if not isinstance(locator, Mapping):
        raise ContractError("E_EVIDENCE_LOCATOR", "locator must be one line, symbol, or JSON Pointer object")
    keys = set(locator)
    if keys == {"line_start", "line_end"}:
        start, end = locator["line_start"], locator["line_end"]
        if type(start) is not int or type(end) is not int or start < 1 or end < start:
            raise ContractError("E_EVIDENCE_LOCATOR", "line locator must be a closed 1-based range")
        return {"line_start": start, "line_end": end}
    if keys == {"symbol"}:
        symbol = locator["symbol"]
        if not isinstance(symbol, str):
            raise ContractError("E_EVIDENCE_LOCATOR", "symbol locator must be a qualified name")
        symbol = unicodedata.normalize("NFC", symbol)
        if not _SYMBOL.fullmatch(symbol) or len(symbol.encode("utf-8")) > 512:
            raise ContractError("E_EVIDENCE_LOCATOR", "symbol locator must be a bounded qualified name")
        return {"symbol": symbol}
    if keys == {"json_pointer"}:
        pointer = locator["json_pointer"]
        if not isinstance(pointer, str) or not _JSON_POINTER.fullmatch(pointer) or len(pointer.encode("utf-8")) > 2048:
            raise ContractError("E_EVIDENCE_LOCATOR", "config locator must be an RFC 6901 JSON Pointer")
        return {"json_pointer": unicodedata.normalize("NFC", pointer)}
    raise ContractError("E_EVIDENCE_LOCATOR", "locator variants cannot be omitted, mixed, or ambiguous")


def normalize_source(kind: str, source: Any) -> dict[str, Any]:
    if not isinstance(source, Mapping) or "path" not in source:
        raise ContractError("E_EVIDENCE_LOCATOR", "source must contain path")
    path = normalize_posix_path(source["path"])
    locator = {key: value for key, value in source.items() if key != "path"}
    if kind == "file-presence":
        if locator:
            raise ContractError("E_EVIDENCE_LOCATOR", "file-presence source cannot have a fine locator")
        return {"path": path}
    if not locator:
        raise ContractError("E_EVIDENCE_LOCATOR", "only file-presence may omit a fine locator")
    return {"path": path, **normalize_locator(locator)}


def compute_fact_id(kind: str, path: str, locator: Mapping[str, Any] | None, semantic_key: str) -> str:
    normalized_kind = _kind(kind)
    source = normalize_source(normalized_kind, {"path": path, **(dict(locator) if locator is not None else {})})
    normalized_key = normalize_text(semantic_key, "semantic_key", maximum=512)
    projection = {
        "kind": normalized_kind,
        "locator": {key: value for key, value in source.items() if key != "path"},
        "path": source["path"],
        "semantic_key": normalized_key,
    }
    return f"{_PREFIXES[normalized_kind]}:{canonical_sha256(projection)}"


def compute_evidence_sha256(fact: Mapping[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in fact.items() if key != "evidence_sha256"})


def build_fact(
    *,
    kind: str,
    path: str,
    locator: Mapping[str, Any] | None,
    semantic_key: str,
    value: Any,
    source_bytes: bytes | None = None,
    source_sha256: str | None = None,
    confidence: str = "observed",
    derivation: str | None = None,
) -> dict[str, Any]:
    normalized_kind = _kind(kind)
    source = normalize_source(normalized_kind, {"path": path, **(dict(locator) if locator is not None else {})})
    key = normalize_text(semantic_key, "semantic_key", maximum=512)
    validate_bounded_json(value)
    if not isinstance(source_bytes, (bytes, type(None))):
        raise ContractError("E_SOURCE_HASH", "source_bytes must be bytes")
    digest = hashlib.sha256(source_bytes).hexdigest() if source_bytes is not None else source_sha256
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ContractError("E_SOURCE_HASH", "source SHA-256 must bind exact source bytes")
    if source_bytes is not None and source_sha256 is not None and digest != source_sha256:
        raise ContractError("E_SOURCE_HASH", "source bytes do not match supplied SHA-256")
    if confidence not in CONFIDENCE_LEVELS:
        raise ContractError("E_EVIDENCE_CONFIDENCE", "confidence is unsupported")
    if confidence == "derived":
        if derivation is None:
            raise ContractError("E_EVIDENCE_DERIVATION", "derived evidence requires derivation")
        derivation = normalize_text(derivation, "derivation", maximum=2048)
    elif derivation is not None:
        raise ContractError("E_EVIDENCE_DERIVATION", "only derived evidence may include derivation")
    fact: dict[str, Any] = {
        "fact_id": compute_fact_id(normalized_kind, source["path"], {k: v for k, v in source.items() if k != "path"} or None, key),
        "kind": normalized_kind,
        "source": source,
        "semantic_key": key,
        "value": copy.deepcopy(value),
        "source_sha256": digest,
        "confidence": confidence,
    }
    if derivation is not None:
        fact["derivation"] = derivation
    fact["evidence_sha256"] = compute_evidence_sha256(fact)
    return fact


def validate_fact(fact: Any, *, source_bytes: bytes | None = None) -> dict[str, Any]:
    if not isinstance(fact, dict):
        raise ContractError("E_SCHEMA_TYPE", "evidence fact must be an object")
    optional = {"derivation"}
    unknown = sorted(set(fact) - _FACT_FIELDS - optional)
    missing = sorted(_FACT_FIELDS - set(fact))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"evidence fact contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"evidence fact is missing field: {missing[0]}")
    kind = _kind(fact["kind"])
    source = normalize_source(kind, fact["source"])
    key = normalize_text(fact["semantic_key"], "semantic_key", maximum=512)
    validate_bounded_json(fact["value"])
    digest = fact["source_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ContractError("E_SOURCE_HASH", "source_sha256 must be lowercase SHA-256")
    if source_bytes is not None and hashlib.sha256(source_bytes).hexdigest() != digest:
        raise ContractError("E_SOURCE_HASH", "source bytes changed after evidence creation")
    confidence = fact["confidence"]
    derivation = fact.get("derivation")
    if confidence not in CONFIDENCE_LEVELS:
        raise ContractError("E_EVIDENCE_CONFIDENCE", "confidence is unsupported")
    if confidence == "derived":
        if derivation is None:
            raise ContractError("E_EVIDENCE_DERIVATION", "derived evidence requires derivation")
        normalize_text(derivation, "derivation", maximum=2048)
    elif derivation is not None:
        raise ContractError("E_EVIDENCE_DERIVATION", "only derived evidence may include derivation")
    locator = {name: value for name, value in source.items() if name != "path"} or None
    expected_id = compute_fact_id(kind, source["path"], locator, key)
    if fact["fact_id"] != expected_id:
        raise ContractError("E_FACT_ID", "fact_id does not match normalized identity")
    if not isinstance(fact["evidence_sha256"], str) or not _SHA256.fullmatch(fact["evidence_sha256"]):
        raise ContractError("E_EVIDENCE_HASH", "evidence_sha256 must be lowercase SHA-256")
    if compute_evidence_sha256(fact) != fact["evidence_sha256"]:
        raise ContractError("E_EVIDENCE_HASH", "evidence semantics changed after hashing")
    if fact["source"] != source or fact["semantic_key"] != key:
        raise ContractError("E_SCHEMA_VALUE", "evidence fact must use normalized source and semantic key")
    return copy.deepcopy(fact)


def validate_claim_support(fact: Mapping[str, Any], *, observed_behavior: bool) -> None:
    validated = validate_fact(dict(fact))
    if observed_behavior and validated["confidence"] == "documented":
        raise ContractError("E_EVIDENCE_CONFIDENCE", "documented evidence cannot support observed behavior")


def compute_graph_sha256(payload: Mapping[str, Any]) -> str:
    return canonical_sha256({key: value for key, value in payload.items() if key != "evidence_sha256"})


def validate_evidence_graph(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractError("E_SCHEMA_TYPE", "repository evidence must be an object")
    required = {"schema_version", "facts", "evidence_sha256"}
    unknown = sorted(set(payload) - required)
    missing = sorted(required - set(payload))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"repository evidence contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"repository evidence is missing field: {missing[0]}")
    if payload["schema_version"] != EVIDENCE_SCHEMA_VERSION or type(payload["schema_version"]) is not int:
        raise ContractError("E_SCHEMA_VERSION", "repository evidence requires schema_version 2")
    facts = payload["facts"]
    if not isinstance(facts, list) or len(facts) > MAX_FACTS:
        raise ContractError("E_EVIDENCE_LIMIT", f"repository evidence may contain at most {MAX_FACTS} facts")
    seen_ids: set[str] = set()
    for fact in facts:
        raw_id = fact.get("fact_id") if isinstance(fact, dict) else None
        if isinstance(raw_id, str) and raw_id in seen_ids:
            raise ContractError("E_FACT_DUPLICATE", "repository evidence contains a duplicate or colliding fact_id")
        if isinstance(raw_id, str):
            seen_ids.add(raw_id)
    validated = [validate_fact(fact) for fact in facts]
    identifiers = [fact["fact_id"] for fact in validated]
    if identifiers != sorted(identifiers):
        raise ContractError("E_EVIDENCE_ORDER", "repository evidence facts must use canonical fact_id order")
    digest = payload["evidence_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest) or compute_graph_sha256(payload) != digest:
        raise ContractError("E_EVIDENCE_HASH", "repository evidence graph hash does not match semantics")
    return copy.deepcopy(payload)


assert_supports_claim = validate_claim_support
fact_id_for = compute_fact_id
evidence_sha256_for = compute_evidence_sha256
