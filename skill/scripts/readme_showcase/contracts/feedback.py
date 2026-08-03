from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256


EVENTS = (
    "preview-approved",
    "preview-rejected",
    "pr-opened",
    "pr-closed",
    "pr-merged",
    "candidate-edited",
    "asset-rejected",
)
DETAILS_CODE = "E_FEEDBACK_DETAILS"
MAX_IDS = 1024
MAX_COUNT = 1_000_000_000
_TOP_LEVEL = {
    "schema_version",
    "event_id",
    "run_id",
    "fingerprint",
    "event",
    "recorded_at",
    "details",
}
_DETAIL_FIELDS = {
    "pattern_ids",
    "section_ids",
    "asset_ids",
    "accepted_ids",
    "rejected_ids",
    "manual_edit_distance",
    "pr_number",
    "pr_outcome",
}
_ID = re.compile(r"[a-z0-9][a-z0-9._:/-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TIME = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z\Z")
_PRIVATE_ID_TERMS = (
    "account",
    "author",
    "comment",
    "email",
    "identity",
    "secret",
    "source",
    "token",
    "user",
)


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _closed(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    return value


def _sha256(value: Any, context: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail("E_FEEDBACK_BINDING", f"{context} must be a lowercase SHA-256")
    return value


def _ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_IDS:
        _fail(DETAILS_CODE, f"{context} must be a bounded ID array")
    result: list[str] = []
    for item in value:
        if not isinstance(item, str) or _ID.fullmatch(item) is None:
            _fail("E_FEEDBACK_ID", f"{context} contains a noncanonical ID")
        folded = item.casefold()
        if any(term in folded for term in _PRIVATE_ID_TERMS):
            _fail("E_FEEDBACK_PRIVACY", f"{context} contains a forbidden private ID")
        result.append(item)
    if result != sorted(set(result)):
        _fail("E_FEEDBACK_ID", f"{context} IDs must be unique and sorted")
    return result


def _distance(value: Any) -> dict[str, int]:
    item = _closed(value, {"changed", "total"}, "manual edit distance")
    if set(item) != {"changed", "total"}:
        _fail("E_SCHEMA_MISSING_FIELD", "manual edit distance requires changed and total")
    changed = item["changed"]
    total = item["total"]
    if (
        type(changed) is not int
        or type(total) is not int
        or changed < 0
        or total < 0
        or changed > total
        or total > MAX_COUNT
    ):
        _fail(DETAILS_CODE, "manual edit distance must be bounded and changed <= total")
    return {"changed": changed, "total": total}


def _timestamp(value: Any) -> str:
    if not isinstance(value, str) or _TIME.fullmatch(value) is None:
        _fail("E_FEEDBACK_TIME", "recorded_at must be an RFC 3339 UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractError("E_FEEDBACK_TIME", "recorded_at must be an RFC 3339 UTC timestamp") from exc
    if parsed.utcoffset() is None or parsed.utcoffset().total_seconds() != 0:
        _fail("E_FEEDBACK_TIME", "recorded_at must use UTC")
    return value


def validate_feedback_details(event: str, value: Any) -> dict[str, Any]:
    details = _closed(value, _DETAIL_FIELDS, "feedback details")
    if not details:
        _fail(DETAILS_CODE, "feedback details cannot be empty")
    for field in ("pattern_ids", "section_ids", "asset_ids", "accepted_ids", "rejected_ids"):
        if field in details:
            _ids(details[field], field)
    if "manual_edit_distance" in details:
        _distance(details["manual_edit_distance"])
    if "pr_number" in details:
        number = details["pr_number"]
        if type(number) is not int or number < 1 or number > MAX_COUNT:
            _fail(DETAILS_CODE, "pr_number must be a bounded positive integer")
    if "pr_outcome" in details and details["pr_outcome"] not in {"opened", "closed", "merged"}:
        _fail(DETAILS_CODE, "pr_outcome is unsupported")

    required: dict[str, set[str]] = {
        "preview-approved": {"accepted_ids"},
        "preview-rejected": {"rejected_ids"},
        "candidate-edited": {"manual_edit_distance"},
        "asset-rejected": {"asset_ids", "rejected_ids"},
        "pr-opened": {"pr_number", "pr_outcome"},
        "pr-closed": {"pr_number", "pr_outcome"},
        "pr-merged": {"pr_number", "pr_outcome"},
    }
    missing = sorted(required[event] - set(details))
    if missing:
        _fail(DETAILS_CODE, f"{event} requires {missing[0]}")
    if event.startswith("pr-"):
        expected = event.removeprefix("pr-")
        if details["pr_outcome"] != expected:
            _fail(DETAILS_CODE, f"{event} requires pr_outcome={expected}")
    elif "pr_number" in details or "pr_outcome" in details:
        _fail(DETAILS_CODE, "PR fields are allowed only for PR events")
    canonical_json_bytes(details)
    return details


def feedback_event_id(payload_without_id: dict[str, Any]) -> str:
    return canonical_sha256(payload_without_id)


def build_feedback_event(
    *,
    run_id: str,
    fingerprint: str,
    event: str,
    recorded_at: str,
    details: Any,
) -> dict[str, Any]:
    if event not in EVENTS:
        _fail("E_FEEDBACK_EVENT", "feedback event is unsupported")
    payload: dict[str, Any] = {
        "schema_version": 1,
        "run_id": _sha256(run_id, "run_id"),
        "fingerprint": _sha256(fingerprint, "fingerprint"),
        "event": event,
        "recorded_at": _timestamp(recorded_at),
        "details": validate_feedback_details(event, details),
    }
    return {**payload, "event_id": feedback_event_id(payload)}


def validate_feedback_event(value: Any) -> dict[str, Any]:
    event = _closed(value, _TOP_LEVEL, "feedback event")
    missing = sorted(_TOP_LEVEL - set(event))
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"feedback event is missing required field: {missing[0]}")
    if type(event["schema_version"]) is not int or event["schema_version"] != 1:
        _fail("E_SCHEMA_VERSION", "feedback event requires schema_version 1")
    if event["event"] not in EVENTS:
        _fail("E_FEEDBACK_EVENT", "feedback event is unsupported")
    _sha256(event["run_id"], "run_id")
    _sha256(event["fingerprint"], "fingerprint")
    _timestamp(event["recorded_at"])
    validate_feedback_details(event["event"], event["details"])
    supplied = event["event_id"]
    if not isinstance(supplied, str) or _SHA256.fullmatch(supplied) is None:
        _fail("E_FEEDBACK_ID", "event_id must be a lowercase SHA-256")
    projection = {key: value for key, value in event.items() if key != "event_id"}
    if supplied != feedback_event_id(projection):
        _fail("E_FEEDBACK_ID", "event_id does not match canonical event bytes")
    canonical_json_bytes(event)
    return event


__all__ = [
    "EVENTS",
    "DETAILS_CODE",
    "MAX_COUNT",
    "MAX_IDS",
    "build_feedback_event",
    "feedback_event_id",
    "validate_feedback_details",
    "validate_feedback_event",
]
