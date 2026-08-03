"""Strict retrieval packet and candidate-ledger contracts."""
# noqa: SIZE_OK - one contract boundary; task ownership forbids a cross-file split.

from __future__ import annotations

import copy
import importlib
import re
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Final

_contracts = importlib.import_module(
    "skill.scripts.pipeline_contracts"
    if (__package__ or "").startswith("skill.")
    else "pipeline_contracts"
)
ContractError = _contracts.ContractError
canonical_json_bytes = _contracts.canonical_json_bytes
canonical_sha256 = _contracts.canonical_sha256
read_json_object_bytes = _contracts.read_json_object_bytes


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z")
_REPOSITORY = re.compile(r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_REVIEW_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
TRUSTED_REVIEW_PACKET_SHA256: Final = (
    "35272b8cdcfb4e01fdc155158594c7d1a13bceabd8afa6b12f16b2040844fc4a"
)
TRUSTED_APPROVAL_ARTIFACT_SHA256: Final = (
    "feef0396226cc3bd6a816ead3a253fb2ed14b043c856279cde96f9933d2d38da"
)
TRUSTED_SOURCE_COMMIT: Final = "e6e0a38e6ca8d0ce2996544c427312533561d5c2"
_TRUSTED_REVIEW_PACKET_CREATED_AT = datetime(
    2026, 8, 3, 8, 31, 48, tzinfo=timezone.utc
)
_PROJECT_TYPES = {"developer-tool", "library", "runtime-toolchain", "web-framework"}
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
_LEDGER_FIELDS = {"schema_version", "dataset_id", "source_manifest_sha256", "candidates"}
_CANDIDATE_FIELDS = {
    "record_id", "repository_url", "commit", "material", "license", "source_identity",
    "intended_split", "project_type", "section_intents", "tags", "metadata",
    "review_status", "approval_receipt",
}
_MATERIAL_FIELDS = {"path", "sha256", "url"}
_LICENSE_FIELDS = {"path", "spdx", "evidence_url", "sha256"}
_IDENTITY_FIELDS = {"repository_url", "commit", "material_sha256", "license_sha256"}
_METADATA_FIELDS = {
    "project_size", "user_role", "install_method", "has_ui", "multi_package",
    "primary_readme_goal",
}
_RECEIPT_FIELDS = {
    "candidate_id", "reviewer_identity", "reviewer_kind", "reviewed_at", "decision",
    "source_commit", "candidate_commit", "review_packet_sha256",
    "approval_artifact_sha256", "material_sha256", "license_sha256", "receipt_sha256",
}


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_floats(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
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


def _candidate_path(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise ContractError("E_DATASET_PROVENANCE", f"{context} must be a relative POSIX path")
    path = PurePosixPath(value)
    if path.is_absolute() or path.as_posix() != value or any(part in {"", ".", ".."} for part in path.parts):
        raise ContractError("E_DATASET_PROVENANCE", f"{context} must be a normalized relative path")
    return value


def _bounded_text(value: Any, context: str, maximum: int = 240) -> str:
    if not isinstance(value, str) or not value or len(value) > maximum:
        raise ContractError("E_DATASET_PROVENANCE", f"{context} must be bounded text")
    return value


def _parse_review_time(value: Any, context: str) -> datetime:
    if not isinstance(value, str) or not _REVIEW_TIME.fullmatch(value):
        raise ContractError("E_DATASET_REVIEW", f"{context} must be UTC RFC 3339 seconds")
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
    except ValueError as error:
        raise ContractError(
            "E_DATASET_REVIEW", f"{context} must be a real UTC calendar timestamp"
        ) from error


def _validate_review_clock(value: datetime | None, context: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() != timedelta(0)
    ):
        raise ContractError(
            "E_DATASET_REVIEW", f"{context} requires an explicit UTC review clock"
        )
    return value.astimezone(timezone.utc)


def _validate_candidate_receipt(
    value: Any,
    candidate: dict[str, Any],
    context: str,
    *,
    review_time_not_after: datetime | None,
) -> None:
    receipt = _object(value, _RECEIPT_FIELDS, context)
    reviewer = receipt["reviewer_identity"]
    if (
        receipt["reviewer_kind"] != "external-human"
        or reviewer != "human:acfufu"
    ):
        raise ContractError("E_DATASET_REVIEW", f"{context} requires an external human reviewer")
    reviewed_at = _parse_review_time(receipt["reviewed_at"], f"{context}.reviewed_at")
    expected = {
        "candidate_id": candidate["record_id"],
        "candidate_commit": candidate["commit"],
        "material_sha256": candidate["material"]["sha256"],
        "license_sha256": candidate["license"]["sha256"],
    }
    if any(receipt[field] != expected_value for field, expected_value in expected.items()):
        raise ContractError("E_DATASET_REVIEW", f"{context} does not bind candidate provenance")
    if receipt["decision"] not in {"approved", "rejected"}:
        raise ContractError("E_DATASET_REVIEW", f"{context}.decision is invalid")
    if receipt["review_packet_sha256"] != TRUSTED_REVIEW_PACKET_SHA256:
        raise ContractError("E_DATASET_REVIEW", f"{context}.review_packet_sha256 is not trusted")
    if (
        receipt["approval_artifact_sha256"] != TRUSTED_APPROVAL_ARTIFACT_SHA256
        or receipt["source_commit"] != TRUSTED_SOURCE_COMMIT
    ):
        raise ContractError("E_DATASET_REVIEW", f"{context} is not bound to the authorized source")
    clock = _validate_review_clock(review_time_not_after, context)
    if reviewed_at < _TRUSTED_REVIEW_PACKET_CREATED_AT or reviewed_at > clock:
        raise ContractError(
            "E_DATASET_REVIEW",
            f"{context}.reviewed_at must follow the trusted packet and not be in the future",
        )
    supplied_hash = receipt["receipt_sha256"]
    if not isinstance(supplied_hash, str) or supplied_hash != canonical_sha256({
        field: field_value for field, field_value in receipt.items() if field != "receipt_sha256"
    }):
        raise ContractError("E_DATASET_REVIEW", f"{context}.receipt_sha256 does not match canonical receipt bytes")


def _validate_candidate(
    value: Any,
    context: str,
    *,
    review_time_not_after: datetime | None,
) -> dict[str, Any]:
    candidate = _object(value, _CANDIDATE_FIELDS, context)
    if not isinstance(candidate["record_id"], str) or not _SLUG.fullmatch(candidate["record_id"]):
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.record_id must be a slug")
    repository = candidate["repository_url"]
    commit = candidate["commit"]
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.repository_url must be canonical GitHub HTTPS")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.commit must be a full pinned commit")
    material = _object(candidate["material"], _MATERIAL_FIELDS, f"{context}.material")
    license_value = _object(candidate["license"], _LICENSE_FIELDS, f"{context}.license")
    material_path = _candidate_path(material["path"], f"{context}.material.path")
    license_path = _candidate_path(license_value["path"], f"{context}.license.path")
    for field, source in (("material.sha256", material), ("license.sha256", license_value)):
        if not isinstance(source["sha256"], str) or not _SHA256.fullmatch(source["sha256"]):
            raise ContractError("E_DATASET_PROVENANCE", f"{context}.{field} is invalid")
    expected_material_url = f"{repository}/blob/{commit}/{material_path}"
    expected_license_url = f"{repository}/blob/{commit}/{license_path}"
    if material["url"] != expected_material_url or license_value["evidence_url"] != expected_license_url:
        raise ContractError("E_DATASET_PROVENANCE", f"{context} uses mutable or mismatched evidence URLs")
    if not isinstance(license_value["spdx"], str) or not _SPDX.fullmatch(license_value["spdx"]):
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.license.spdx is invalid")
    identity = _object(candidate["source_identity"], _IDENTITY_FIELDS, f"{context}.source_identity")
    expected_identity = {
        "repository_url": repository, "commit": commit,
        "material_sha256": material["sha256"], "license_sha256": license_value["sha256"],
    }
    if identity != expected_identity:
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.source_identity has hash or source drift")
    if candidate["intended_split"] != "train":
        raise ContractError("E_DATASET_SPLIT_LEAK", f"{context}.intended_split must be train")
    if candidate["project_type"] not in _PROJECT_TYPES:
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.project_type is invalid")
    _slugs(candidate["section_intents"], f"{context}.section_intents")
    _slugs(candidate["tags"], f"{context}.tags")
    metadata = _object(candidate["metadata"], _METADATA_FIELDS, f"{context}.metadata")
    if metadata["project_size"] not in {"small", "medium", "large"}:
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.metadata.project_size is invalid")
    for field in ("user_role", "install_method", "primary_readme_goal"):
        _bounded_text(metadata[field], f"{context}.metadata.{field}")
    if type(metadata["has_ui"]) is not bool or type(metadata["multi_package"]) is not bool:
        raise ContractError("E_DATASET_PROVENANCE", f"{context}.metadata boolean fields are invalid")
    status, receipt = candidate["review_status"], candidate["approval_receipt"]
    if status == "unverified":
        if receipt is not None:
            raise ContractError("E_DATASET_REVIEW", f"{context} unverified candidate must not carry a receipt")
    elif status in {"approved", "rejected"}:
        if receipt is None:
            raise ContractError("E_DATASET_REVIEW", f"{context} reviewed candidate requires a receipt")
        _validate_candidate_receipt(
            receipt,
            candidate,
            f"{context}.approval_receipt",
            review_time_not_after=review_time_not_after,
        )
        if receipt["decision"] != status:
            raise ContractError("E_DATASET_REVIEW", f"{context} review status and receipt disagree")
    else:
        raise ContractError("E_DATASET_REVIEW", f"{context}.review_status is invalid")
    return candidate


def validate_retrieval_candidate_ledger_v1(
    payload: Any,
    *,
    production_manifest: Any | None = None,
    production_manifest_sha256: str | None = None,
    review_time_not_after: datetime | None = None,
) -> dict[str, Any]:
    _reject_floats(payload)
    ledger = _object(payload, _LEDGER_FIELDS, "retrieval candidate ledger v1")
    if type(ledger["schema_version"]) is not int or ledger["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "retrieval candidate ledger requires schema_version 1")
    if ledger["dataset_id"] != "readme-showcase-retrieval-candidates":
        raise ContractError("E_DATASET_PROVENANCE", "candidate ledger dataset_id is invalid")
    manifest_hash = ledger["source_manifest_sha256"]
    if not isinstance(manifest_hash, str) or not _SHA256.fullmatch(manifest_hash):
        raise ContractError("E_DATASET_PROVENANCE", "source_manifest_sha256 is invalid")
    if production_manifest_sha256 is not None and manifest_hash != production_manifest_sha256:
        raise ContractError("E_DATASET_PROVENANCE", "source manifest bytes have drifted")
    candidates = ledger["candidates"]
    if not isinstance(candidates, list) or len(candidates) != 12:
        raise ContractError("E_DATASET_COVERAGE", "candidate ledger must contain exactly 12 candidates")
    validated = [
        _validate_candidate(
            raw,
            f"candidate[{index}]",
            review_time_not_after=review_time_not_after,
        )
        for index, raw in enumerate(candidates)
    ]
    record_ids = [candidate["record_id"] for candidate in validated]
    if record_ids != sorted(set(record_ids)):
        raise ContractError("E_DATASET_DUPLICATE_ID", "candidate record IDs must be sorted and unique")
    identities = [(candidate["repository_url"], candidate["commit"]) for candidate in validated]
    if len(identities) != len(set(identities)):
        raise ContractError("E_DATASET_SOURCE_DUPLICATE", "candidate source identities must be unique")
    if production_manifest is not None:
        manifest = _object(
            production_manifest,
            {"schema_version", "dataset_id", "dataset_revision", "purpose", "records"},
            "production manifest",
        )
        candidates_by_identity = {
            (candidate["repository_url"], candidate["commit"]): candidate
            for candidate in validated
        }
        for record in manifest["records"]:
            identity = (record["source"]["repository_url"], record["source"]["commit"])
            candidate = candidates_by_identity.get(identity)
            if candidate is None:
                continue
            source = record["source"]
            if (
                candidate["review_status"] != "approved"
                or record["record_id"] != candidate["record_id"]
                or record["split"] != "train"
                or record["project_types"] != [candidate["project_type"]]
                or record["section_intents"] != candidate["section_intents"]
                or record["tags"] != candidate["tags"]
                or source["material_sha256"] != candidate["material"]["sha256"]
                or source["license_spdx"] != candidate["license"]["spdx"]
                or source["license_evidence_spdx"] != candidate["license"]["spdx"]
                or source["license_evidence_url"] != candidate["license"]["evidence_url"]
                or source["license_evidence_sha256"] != candidate["license"]["sha256"]
                or source["human_reviewed"] is not True
            ):
                raise ContractError(
                    "E_DATASET_REVIEW",
                    "production candidate is not fully bound to an approved receipt",
                )
    return copy.deepcopy(ledger)


def load_retrieval_candidate_ledger_v1(
    path: Path,
    *,
    production_manifest: Any | None = None,
    production_manifest_sha256: str | None = None,
    review_time_not_after: datetime | None = None,
) -> dict[str, Any]:
    raw, payload = read_json_object_bytes(path)
    ledger = validate_retrieval_candidate_ledger_v1(
        payload,
        production_manifest=production_manifest,
        production_manifest_sha256=production_manifest_sha256,
        review_time_not_after=review_time_not_after,
    )
    if raw != canonical_json_bytes(ledger):
        raise ContractError("E_DATASET_PROVENANCE", "candidate ledger file must use canonical JSON bytes")
    return ledger


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


def adapt_v2_to_v1(payload: Any) -> dict[str, Any]:
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


__all__ = [
    "adapt_v2_to_v1",
    "load_retrieval_candidate_ledger_v1",
    "validate_retrieval_candidate_ledger_v1",
    "validate_retrieval_packet_v2",
    "validate_retrieval_query",
]
