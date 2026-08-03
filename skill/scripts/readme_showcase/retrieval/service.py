"""Retrieval manifest validation plus v1/v2 producer services."""

from __future__ import annotations

import copy
import importlib
import re
import unicodedata
from typing import Any, Mapping, NoReturn
from urllib.parse import urlparse

from ..contracts.retrieval import validate_retrieval_packet_v2, validate_retrieval_query
from .classifier import ALL_PROJECT_TYPES
from .ranker import rank_records


_DATASET_FIELDS = {"schema_version", "dataset_id", "dataset_revision", "purpose", "records"}
_RECORD_FIELDS = {"record_id", "project_types", "section_intents", "tags", "pattern", "source", "split"}
_PATTERN_FIELDS = {"summary", "structure", "proof"}
_SOURCE_FIELDS = {
    "repository_url", "commit", "material_sha256", "license_spdx", "license_evidence_spdx",
    "license_evidence_url", "license_evidence_sha256", "human_reviewed",
}
_contracts = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _contracts.ContractError
canonical_sha256 = _contracts.canonical_sha256
validate_contract = _contracts.validate_contract

_EVIDENCE_FIELDS = {"schema_version", "status", "target", "scan_limits", "files", "facts", "warnings"}
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z")
_EMBEDDED = ("\n", "\r", "```", "<img", "<svg", "![")
_V1_PROJECT_TYPES = {"developer-tool", "library", "runtime-toolchain", "web-framework"}


def _fail(code: str, message: str) -> NoReturn:
    raise ContractError(code, message)


def _reject_floats(value: Any) -> None:
    if isinstance(value, float):
        _fail("E_SCHEMA_FLOAT", "retrieval input must not contain floats")
    if isinstance(value, list):
        for child in value:
            _reject_floats(child)
    elif isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("E_SCHEMA_KEY_TYPE", "retrieval input contains a non-string key")
            _reject_floats(child)


def _object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be a JSON object")
    missing, unknown = sorted(fields - set(value)), sorted(set(value) - fields)
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    return value


def _slugs(value: Any, field: str, *, allow_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (not allow_empty and not value) or any(not isinstance(item, str) or not _SLUG.fullmatch(item) for item in value) or value != sorted(set(value)):
        _fail("E_DATASET_SLUG_LIST", f"{field} must be a sorted unique slug list")
    return value


def _source(value: Any, context: str) -> tuple[str, str, str]:
    source = _object(value, _SOURCE_FIELDS, context)
    repository_url = source["repository_url"]
    parsed = urlparse(repository_url) if isinstance(repository_url, str) else None
    if parsed is None or parsed.scheme != "https" or parsed.netloc.lower() != "github.com" or len([part for part in parsed.path.split("/") if part]) != 2 or parsed.query or parsed.fragment:
        _fail("E_DATASET_REPOSITORY", f"{context}.repository_url must name a GitHub repository")
    commit = source["commit"]
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        _fail("E_DATASET_COMMIT", f"{context}.commit must be a lowercase 40-character SHA")
    for field in ("material_sha256", "license_evidence_sha256"):
        if not isinstance(source[field], str) or not _SHA256.fullmatch(source[field]):
            _fail("E_DATASET_SHA256", f"{context}.{field} must be a lowercase SHA-256")
    license_spdx, evidence_spdx = source["license_spdx"], source["license_evidence_spdx"]
    if not isinstance(license_spdx, str) or license_spdx.upper() in {"UNKNOWN", "NOASSERTION"} or not _SPDX.fullmatch(license_spdx):
        _fail("E_DATASET_LICENSE", f"{context}.license_spdx must be reviewed SPDX")
    if evidence_spdx != license_spdx:
        _fail("E_DATASET_LICENSE_CONFLICT", f"{context} license evidence conflicts")
    expected = f"{repository_url.rstrip('/')}/blob/{commit}/"
    if not isinstance(source["license_evidence_url"], str) or not source["license_evidence_url"].startswith(expected):
        _fail("E_DATASET_LICENSE_EVIDENCE", f"{context}.license_evidence_url must pin commit")
    if source["human_reviewed"] is not True:
        _fail("E_DATASET_LICENSE_REVIEW", f"{context}.human_reviewed must be true")
    return repository_url, commit, source["material_sha256"]


def validate_dataset_manifest(payload: Any) -> dict[str, object]:
    _reject_floats(payload)
    manifest = validate_contract(payload, required=_DATASET_FIELDS, optional=set(), context="retrieval dataset manifest")
    if manifest["dataset_id"] != "readme-showcase-retrieval":
        _fail("E_DATASET_ID", "dataset_id must be readme-showcase-retrieval")
    if type(manifest["dataset_revision"]) is not int or manifest["dataset_revision"] < 1:
        _fail("E_DATASET_REVISION", "dataset_revision must be a positive integer")
    if manifest["purpose"] != "retrieval-only":
        _fail("E_DATASET_PURPOSE", "purpose must be retrieval-only")
    records = manifest["records"]
    if not isinstance(records, list) or len(records) > 1000:
        _fail("E_DATASET_RECORDS", "records must be a list with at most 1000 items")
    identifiers: set[str] = set()
    sources: dict[tuple[str, str, str], str] = {}
    materials: dict[str, str] = {}
    split_counts = {"test": 0, "train": 0}
    for index, raw in enumerate(records):
        context = f"retrieval dataset manifest.records[{index}]"
        record = _object(raw, _RECORD_FIELDS, context)
        record_id = record["record_id"]
        if not isinstance(record_id, str) or not _SLUG.fullmatch(record_id):
            _fail("E_DATASET_RECORD_ID", f"{context}.record_id must be a lowercase slug")
        if record_id in identifiers:
            _fail("E_DATASET_DUPLICATE_ID", f"duplicate record_id: {record_id}")
        identifiers.add(record_id)
        for field in ("project_types", "section_intents", "tags"):
            _slugs(record[field], f"{context}.{field}")
        pattern = _object(record["pattern"], _PATTERN_FIELDS, f"{context}.pattern")
        for field, text in pattern.items():
            if not isinstance(text, str) or not text or len(text) > 240:
                _fail("E_DATASET_TEXT", f"{context}.pattern.{field} must be nonempty text within 240 characters")
            if any(marker in text.casefold() for marker in _EMBEDDED):
                _fail("E_DATASET_EMBEDDED_CONTENT", f"{context}.pattern.{field} contains embedded content")
        split = record["split"]
        if split not in split_counts:
            _fail("E_DATASET_SPLIT", f"{context}.split must be train or test")
        identity = _source(record["source"], f"{context}.source")
        if identity in sources:
            code = "E_DATASET_SPLIT_LEAK" if sources[identity] != split else "E_DATASET_SOURCE_DUPLICATE"
            _fail(code, f"source identity reused by {record_id}")
        sources[identity] = split
        material = identity[2]
        if material in materials:
            code = "E_DATASET_SPLIT_LEAK" if materials[material] != split else "E_DATASET_SOURCE_DUPLICATE"
            _fail(code, f"source material reused by {record_id}")
        materials[material] = split
        split_counts[split] += 1
    return {
        "schema_version": 1, "status": "pass", "record_count": len(records),
        "split_counts": split_counts, "manifest_sha256": canonical_sha256(manifest),
    }


def _normalized_query(query: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(query, Mapping):
        _fail("E_RETRIEVAL_QUERY", "retrieval query must be an object")
    _reject_floats(query)
    raw = _object(dict(query), {"project_type", "sections", "tags", "manifest_features", "evidence_sha256"}, "retrieval query")
    if raw["project_type"] not in ALL_PROJECT_TYPES:
        _fail("E_RETRIEVAL_QUERY", "project_type is unsupported")
    for field in ("sections", "tags", "manifest_features"):
        if not isinstance(raw[field], list) or any(not isinstance(value, str) for value in raw[field]):
            _fail("E_RETRIEVAL_QUERY", f"{field} must contain strings")
    normalized = {
        "project_type": raw["project_type"],
        "sections": sorted(set(raw["sections"])),
        "tags": sorted(set(raw["tags"])),
        "manifest_features": sorted(set(unicodedata.normalize("NFKC", value).casefold() for value in raw["manifest_features"])),
        "evidence_sha256": raw["evidence_sha256"],
    }
    return validate_retrieval_query(normalized)


def retrieve_patterns_v2(manifest: Any | None, query: Mapping[str, Any], *, mode: str = "production", benchmark: bool = False) -> dict[str, Any]:
    normalized_query = _normalized_query(query)
    if mode not in {"production", "benchmark"}:
        _fail("E_RETRIEVAL_MODE", "mode must be production or benchmark")
    if mode == "benchmark" and not benchmark:
        _fail("E_RETRIEVAL_BENCHMARK", "benchmark retrieval requires explicit benchmark=True")
    if manifest is None:
        if mode == "benchmark":
            _fail("E_RETRIEVAL_MANIFEST", "benchmark retrieval requires a valid manifest")
        return validate_retrieval_packet_v2({
            "schema_version": 2, "status": "unavailable", "mode": mode, "query": normalized_query,
            "dataset": None, "records": [], "reason": "manifest-unavailable",
        })
    validate_dataset_manifest(manifest)
    ordered = sorted(copy.deepcopy(manifest["records"]), key=lambda item: item["record_id"])
    eligible = ordered if mode == "benchmark" else [record for record in ordered if record["split"] == "train"]
    packet = {
        "schema_version": 2, "status": "available", "mode": mode, "query": normalized_query,
        "dataset": {
            "dataset_id": manifest["dataset_id"], "dataset_revision": manifest["dataset_revision"],
            "manifest_sha256": canonical_sha256({**manifest, "records": ordered}),
        },
        "records": rank_records(eligible, normalized_query), "reason": None,
    }
    return validate_retrieval_packet_v2(packet)


def _v1_query(evidence: Any, project_type: str, sections: list[str], tags: list[str], mode: str) -> dict[str, object]:
    packet = _object(evidence, _EVIDENCE_FIELDS, "repository evidence")
    if packet["schema_version"] != 1 or packet["status"] != "complete" or any(not isinstance(packet[field], list) for field in ("files", "facts", "warnings")):
        _fail("E_RETRIEVAL_EVIDENCE", "retrieval requires complete schema-v1 evidence")
    if project_type not in _V1_PROJECT_TYPES:
        _fail("E_RETRIEVAL_QUERY", "project_type is unsupported")
    if mode not in {"production", "benchmark"}:
        _fail("E_RETRIEVAL_MODE", "mode must be production or benchmark")
    for values, context in ((sections, "sections"), (tags, "tags")):
        if not isinstance(values, list) or any(not isinstance(value, str) or not _SLUG.fullmatch(value) for value in values):
            _fail("E_RETRIEVAL_QUERY", f"{context} must contain lowercase slugs")
    return {
        "project_type": project_type, "sections": sorted(set(sections)), "tags": sorted(set(tags)),
        "evidence_sha256": canonical_sha256(packet),
    }


def retrieve_patterns_v1(evidence: Any, manifest: Any | None, *, project_type: str, sections: list[str], tags: list[str], mode: str) -> dict[str, object]:
    query = _v1_query(evidence, project_type, sections, tags, mode)
    if manifest is None:
        if mode == "benchmark":
            _fail("E_RETRIEVAL_MANIFEST", "benchmark retrieval requires a valid manifest")
        return {"schema_version": 1, "status": "unavailable", "mode": mode, "query": query, "dataset": None, "records": [], "reason": "manifest-unavailable"}
    validate_dataset_manifest(manifest)
    records = sorted(manifest["records"], key=lambda item: item["record_id"])
    section_set, tag_set = set(query["sections"]), set(query["tags"])
    ranked = []
    for record in records:
        if record["split"] != "train":
            continue
        components = {
            "project_type_match": int(project_type in record["project_types"]),
            "section_overlap_count": len(section_set & set(record["section_intents"])),
            "tag_overlap_count": len(tag_set & set(record["tags"])),
        }
        score = 100 * components["project_type_match"] + 30 * components["section_overlap_count"] + 10 * components["tag_overlap_count"]
        if score:
            ranked.append({
                "record_id": record["record_id"], "score": score, "components": components,
                "project_types": record["project_types"], "section_intents": record["section_intents"], "tags": record["tags"],
                "pattern": record["pattern"], "source": record["source"],
            })
    ranked.sort(key=lambda item: (-item["score"], item["record_id"]))
    return {
        "schema_version": 1, "status": "available", "mode": mode, "query": query,
        "dataset": {"dataset_id": manifest["dataset_id"], "dataset_revision": manifest["dataset_revision"], "manifest_sha256": canonical_sha256({**manifest, "records": records})},
        "records": ranked[:5], "reason": None,
    }


__all__ = ["retrieve_patterns_v1", "retrieve_patterns_v2", "validate_dataset_manifest"]
