"""Strict retrieval-packet v2 contract and v1 read adapter."""

from __future__ import annotations

import copy
import importlib
import re
import unicodedata
from typing import Any

_contracts = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _contracts.ContractError


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_PACKET_FIELDS = {"schema_version", "status", "mode", "query", "dataset", "records", "reason"}
_QUERY_FIELDS = {"project_type", "sections", "tags", "manifest_features", "evidence_sha256"}
_DATASET_FIELDS = {"dataset_id", "dataset_revision", "manifest_sha256"}
_RESULT_FIELDS = {
    "record_id", "score_basis_points", "signals", "reasons", "project_types",
    "section_intents", "tags", "pattern", "source", "source_split",
}
_SIGNAL_FIELDS = {
    "project_type_basis_points", "section_overlap_basis_points", "tag_overlap_basis_points",
    "manifest_feature_overlap_basis_points", "bm25_basis_points", "diversity_penalty_basis_points",
}
_REASON_FIELDS = {"code", "signal", "matched_values"}
_PATTERN_FIELDS = {"summary", "structure", "proof"}
_SOURCE_FIELDS = {
    "repository_url", "commit", "material_sha256", "license_spdx", "license_evidence_spdx",
    "license_evidence_url", "license_evidence_sha256", "human_reviewed",
}


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_floats(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                raise ContractError("E_SCHEMA_KEY_TYPE", f"{path} contains a non-string key")
            _reject_floats(child, f"{path}.{key}")


def _object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown, missing = sorted(set(value) - fields), sorted(fields - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    return value


def _slugs(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not _SLUG.fullmatch(item) for item in value):
        raise ContractError("E_RETRIEVAL_PACKET", f"{context} must contain slugs")
    if value != sorted(set(value)):
        raise ContractError("E_RETRIEVAL_PACKET", f"{context} must be sorted and unique")
    return value


def _text_list(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item or len(item) > 240 for item in value):
        raise ContractError("E_RETRIEVAL_PACKET", f"{context} must contain bounded text")
    normalized = [unicodedata.normalize("NFKC", item).casefold() for item in value]
    if value != sorted(set(normalized)):
        raise ContractError("E_RETRIEVAL_PACKET", f"{context} must be normalized, sorted, and unique")
    return value


def validate_retrieval_query(value: Any) -> dict[str, Any]:
    query = _object(value, _QUERY_FIELDS, "retrieval query")
    if not isinstance(query["project_type"], str) or not _SLUG.fullmatch(query["project_type"]):
        raise ContractError("E_RETRIEVAL_QUERY", "project_type must be a slug")
    _slugs(query["sections"], "retrieval query.sections")
    _slugs(query["tags"], "retrieval query.tags")
    _text_list(query["manifest_features"], "retrieval query.manifest_features")
    if not isinstance(query["evidence_sha256"], str) or not _SHA256.fullmatch(query["evidence_sha256"]):
        raise ContractError("E_RETRIEVAL_QUERY", "evidence_sha256 must be lowercase SHA-256")
    return copy.deepcopy(query)


def _validate_source(value: Any, context: str) -> dict[str, Any]:
    source = _object(value, _SOURCE_FIELDS, context)
    for field in ("repository_url", "commit", "license_spdx", "license_evidence_spdx", "license_evidence_url"):
        if not isinstance(source[field], str) or not source[field]:
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.{field} is invalid")
    for field in ("material_sha256", "license_evidence_sha256"):
        if not isinstance(source[field], str) or not _SHA256.fullmatch(source[field]):
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.{field} is invalid")
    if type(source["human_reviewed"]) is not bool or not source["human_reviewed"]:
        raise ContractError("E_RETRIEVAL_PACKET", f"{context}.human_reviewed must be true")
    return source


def validate_retrieval_packet_v2(payload: Any) -> dict[str, Any]:
    _reject_floats(payload)
    packet = _object(payload, _PACKET_FIELDS, "retrieval packet v2")
    if type(packet["schema_version"]) is not int or packet["schema_version"] != 2:
        raise ContractError("E_SCHEMA_VERSION", "retrieval packet requires schema_version 2")
    if packet["status"] not in {"available", "unavailable"} or packet["mode"] not in {"production", "benchmark"}:
        raise ContractError("E_RETRIEVAL_PACKET", "retrieval status or mode is invalid")
    validate_retrieval_query(packet["query"])
    if packet["status"] == "unavailable":
        if packet["dataset"] is not None or packet["records"] != [] or not isinstance(packet["reason"], str) or not packet["reason"]:
            raise ContractError("E_RETRIEVAL_PACKET", "unavailable retrieval packet fields disagree")
        return copy.deepcopy(packet)
    if packet["reason"] is not None:
        raise ContractError("E_RETRIEVAL_PACKET", "available retrieval packet reason must be null")
    dataset = _object(packet["dataset"], _DATASET_FIELDS, "retrieval packet dataset")
    if dataset["dataset_id"] != "readme-showcase-retrieval" or type(dataset["dataset_revision"]) is not int or dataset["dataset_revision"] < 1:
        raise ContractError("E_RETRIEVAL_PACKET", "retrieval packet dataset identity is invalid")
    if not isinstance(dataset["manifest_sha256"], str) or not _SHA256.fullmatch(dataset["manifest_sha256"]):
        raise ContractError("E_RETRIEVAL_PACKET", "retrieval packet manifest hash is invalid")
    records = packet["records"]
    if not isinstance(records, list) or len(records) > 5:
        raise ContractError("E_RETRIEVAL_PACKET", "retrieval records must contain at most five results")
    identifiers: set[str] = set()
    identities: set[tuple[str, str, str]] = set()
    for index, raw in enumerate(records):
        context = f"retrieval packet records[{index}]"
        result = _object(raw, _RESULT_FIELDS, context)
        record_id = result["record_id"]
        if not isinstance(record_id, str) or not _SLUG.fullmatch(record_id) or record_id in identifiers:
            raise ContractError("E_DATASET_DUPLICATE_ID", f"{context}.record_id is invalid or duplicate")
        identifiers.add(record_id)
        score = result["score_basis_points"]
        if type(score) is not int or not 0 <= score <= 10_000:
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.score_basis_points is invalid")
        signals = _object(result["signals"], _SIGNAL_FIELDS, f"{context}.signals")
        if any(type(value) is not int or not 0 <= value <= 10_000 for value in signals.values()):
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.signals contain an invalid score")
        reasons = result["reasons"]
        if not isinstance(reasons, list):
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.reasons must be a list")
        reason_codes: set[str] = set()
        for reason in reasons:
            item = _object(reason, _REASON_FIELDS, f"{context}.reason")
            if not isinstance(item["code"], str) or not item["code"] or item["code"] in reason_codes:
                raise ContractError("E_RETRIEVAL_PACKET", f"{context}.reason code is invalid or duplicate")
            reason_codes.add(item["code"])
            if item["signal"] not in _SIGNAL_FIELDS or item["signal"] == "diversity_penalty_basis_points" or signals[item["signal"]] <= 0:
                raise ContractError("E_RETRIEVAL_REASON_DANGLING", f"{context}.reason does not bind a positive signal")
            if not isinstance(item["matched_values"], list) or not item["matched_values"] or item["matched_values"] != sorted(set(item["matched_values"])) or any(not isinstance(value, str) or not value for value in item["matched_values"]):
                raise ContractError("E_RETRIEVAL_PACKET", f"{context}.reason matched_values are invalid")
        _slugs(result["project_types"], f"{context}.project_types")
        _slugs(result["section_intents"], f"{context}.section_intents")
        _slugs(result["tags"], f"{context}.tags")
        pattern = _object(result["pattern"], _PATTERN_FIELDS, f"{context}.pattern")
        if any(not isinstance(value, str) or not value for value in pattern.values()):
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.pattern is invalid")
        source = _validate_source(result["source"], f"{context}.source")
        identity = (source["repository_url"], source["commit"], source["material_sha256"])
        if identity in identities:
            raise ContractError("E_DATASET_SOURCE_DUPLICATE", f"{context}.source is duplicate")
        identities.add(identity)
        if result["source_split"] not in {"train", "test"}:
            raise ContractError("E_RETRIEVAL_PACKET", f"{context}.source_split is invalid")
        if packet["mode"] == "production" and result["source_split"] != "train":
            raise ContractError("E_DATASET_SPLIT_LEAK", "production retrieval packet contains test split")
    return copy.deepcopy(packet)


def adapt_v2_to_v1(payload: Any) -> dict[str, object]:
    packet = validate_retrieval_packet_v2(payload)
    query = packet["query"]
    records = []
    for result in packet["records"]:
        components = {
            "project_type_match": int(query["project_type"] in result["project_types"]),
            "section_overlap_count": len(set(query["sections"]) & set(result["section_intents"])),
            "tag_overlap_count": len(set(query["tags"]) & set(result["tags"])),
        }
        records.append({
            "record_id": result["record_id"],
            "score": 100 * components["project_type_match"] + 30 * components["section_overlap_count"] + 10 * components["tag_overlap_count"],
            "components": components,
            "project_types": result["project_types"], "section_intents": result["section_intents"], "tags": result["tags"],
            "pattern": result["pattern"], "source": result["source"],
        })
    records.sort(key=lambda item: (-item["score"], item["record_id"]))
    return {
        "schema_version": 1, "status": packet["status"], "mode": packet["mode"],
        "query": {key: query[key] for key in ("project_type", "sections", "tags", "evidence_sha256")},
        "dataset": packet["dataset"], "records": records[:5], "reason": packet["reason"],
    }


__all__ = ["adapt_v2_to_v1", "validate_retrieval_packet_v2", "validate_retrieval_query"]
