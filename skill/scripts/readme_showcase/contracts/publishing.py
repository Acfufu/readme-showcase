from __future__ import annotations

import hashlib
import json
import re
import stat
from pathlib import Path, PurePosixPath
from typing import Any, cast

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    read_regular_bytes,
)
from ..delivery import legacy as _LEGACY
from ..visual_kernel.reader import load_compiled_visual


APPROVAL_SCHEMA_VERSION = 2
ALLOWED_ACTIONS = [
    "create-branch",
    "commit-files",
    "push-branch",
    "open-pull-request",
]
EVALUATION_PATH = "evaluation-report.json"
PREVIEW_PATH = "output/preview/index.html"
PREVIEW_REPORT_PATH = "output/preview/report.json"
COMPILED_MANIFEST_PATH = "asset-manifest.json"
MAX_BOUND_BYTES = 16 * 1024 * 1024
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})/"
    r"[A-Za-z0-9](?:[A-Za-z0-9_.-]{0,99})\Z"
)
_FIELDS = {
    "schema_version",
    "decision",
    "repository",
    "base_sha",
    "proposed_branch",
    "pr_fingerprint",
    "candidate_hashes",
    "evaluation_sha256",
    "preview",
    "actions",
}
_CANDIDATE_FIELDS = {"path", "sha256"}
_PREVIEW_FIELDS = {
    "path",
    "preview_sha256",
    "report_path",
    "report_sha256",
}
_REMOTE_V2_FIELDS = {"schema_version", "operation_id", "actions"}
_REMOTE_ACTION_FIELDS = {
    "action",
    "permission",
    "checked_repository",
    "checked_base_sha",
    "checked_branch",
    "checked_candidate_sha256",
    "checked_evaluation_sha256",
    "checked_approval_sha256",
    "observed",
}
_DELIVERY_RESULT_FIELDS = {
    "schema_version",
    "status",
    "operation_id",
    "branch",
    "commit_sha",
    "pr_url",
    "pr_number",
    "reason",
    "attempts",
    "idempotent",
}


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _closed_object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    missing = sorted(fields - set(value))
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    return value


def _hash(value: Any, code: str, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        _fail(code, f"{context} must be a lowercase SHA-256")
    return value


def _path(value: Any, code: str, context: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\0" in value:
        _fail(code, f"{context} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or value.startswith("~/")
        or any(part in {"", ".", ".."} for part in value.split("/"))
        or len(value.encode("utf-8")) > 4096
        or path.as_posix() != value
    ):
        _fail(code, f"{context} must be a safe relative POSIX path")
    return value


def _branch(value: Any) -> str:
    try:
        return _LEGACY._branch(value, "approval proposed_branch")
    except ContractError as exc:
        raise ContractError("E_APPROVAL_FINGERPRINT", str(exc)) from exc


def validate_approval_envelope_v2(payload: Any) -> dict[str, Any]:
    envelope = _closed_object(payload, _FIELDS, "approval envelope v2")
    if envelope["schema_version"] != APPROVAL_SCHEMA_VERSION or type(
        envelope["schema_version"]
    ) is not int:
        _fail("E_SCHEMA_VERSION", "approval envelope requires schema_version 2")
    if envelope["decision"] not in {"approve", "reject"}:
        _fail("E_APPROVAL_DECISION", "approval decision is unsupported")
    repository = envelope["repository"]
    if not isinstance(repository, str) or not _REPOSITORY.fullmatch(repository):
        _fail("E_APPROVAL_FINGERPRINT", "approval repository must be owner/name")
    if not isinstance(envelope["base_sha"], str) or not _COMMIT.fullmatch(
        envelope["base_sha"]
    ):
        _fail("E_APPROVAL_FINGERPRINT", "approval base_sha must be immutable")
    _branch(envelope["proposed_branch"])
    _hash(envelope["pr_fingerprint"], "E_APPROVAL_FINGERPRINT", "approval pr_fingerprint")
    _hash(envelope["evaluation_sha256"], "E_EVALUATION_DRIFT", "approval evaluation_sha256")

    raw_candidates = envelope["candidate_hashes"]
    if not isinstance(raw_candidates, list) or not raw_candidates or len(raw_candidates) > 256:
        _fail("E_APPROVAL_CANDIDATES", "approval candidate_hashes must be a bounded non-empty list")
    candidates: list[dict[str, str]] = []
    for index, raw in enumerate(raw_candidates):
        item = _closed_object(raw, _CANDIDATE_FIELDS, f"approval candidate_hashes[{index}]")
        candidates.append({
            "path": _path(item["path"], "E_APPROVAL_CANDIDATES", f"candidate_hashes[{index}].path"),
            "sha256": _hash(item["sha256"], "E_APPROVAL_CANDIDATES", f"candidate_hashes[{index}].sha256"),
        })
    paths = [item["path"] for item in candidates]
    if len(paths) != len(set(paths)):
        _fail("E_APPROVAL_CANDIDATES", "approval candidate paths must be unique")

    preview = _closed_object(envelope["preview"], _PREVIEW_FIELDS, "approval preview")
    if preview["path"] != PREVIEW_PATH or preview["report_path"] != PREVIEW_REPORT_PATH:
        _fail("E_PREVIEW_DRIFT", "approval preview paths differ from the concrete producer")
    _hash(preview["preview_sha256"], "E_PREVIEW_DRIFT", "approval preview.preview_sha256")
    _hash(preview["report_sha256"], "E_PREVIEW_DRIFT", "approval preview.report_sha256")
    if envelope["actions"] != ALLOWED_ACTIONS:
        _fail("E_APPROVAL_ACTIONS", "approval actions differ from the fixed ordered allowlist")
    canonical_json_bytes(envelope)
    return envelope


def validate_remote_state_v2(payload: Any) -> dict[str, Any]:
    state = _closed_object(payload, _REMOTE_V2_FIELDS, "remote state v2")
    if type(state["schema_version"]) is not int or state["schema_version"] != 2:
        _fail("E_SCHEMA_VERSION", "remote state requires schema_version 2")
    operation_id = _hash(
        state["operation_id"],
        "E_REMOTE_BINDING",
        "remote state operation_id",
    )
    raw_actions = state["actions"]
    if not isinstance(raw_actions, list) or len(raw_actions) != len(ALLOWED_ACTIONS):
        _fail("E_REMOTE_ACTIONS", "remote state requires one entry per fixed action")
    normalized: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_actions):
        entry = _closed_object(raw, _REMOTE_ACTION_FIELDS, f"remote state actions[{index}]")
        action = entry["action"]
        if action != ALLOWED_ACTIONS[index]:
            _fail("E_REMOTE_ACTIONS", "remote state actions differ from the fixed order")
        if type(entry["permission"]) is not bool:
            _fail("E_REMOTE_PERMISSION", f"{action} permission must be boolean")
        if not isinstance(entry["checked_repository"], str) or not _REPOSITORY.fullmatch(
            entry["checked_repository"]
        ):
            _fail("E_REMOTE_BINDING", f"{action} repository binding is invalid")
        if not isinstance(entry["checked_base_sha"], str) or not _COMMIT.fullmatch(
            entry["checked_base_sha"]
        ):
            _fail("E_REMOTE_BINDING", f"{action} base binding is invalid")
        _branch(entry["checked_branch"])
        for field in (
            "checked_candidate_sha256",
            "checked_evaluation_sha256",
            "checked_approval_sha256",
        ):
            _hash(entry[field], "E_REMOTE_BINDING", f"{action} {field}")
        if entry["checked_approval_sha256"] != operation_id:
            _fail("E_REMOTE_BINDING", f"{action} approval binding differs from operation ID")
        if entry["observed"] is not None and not isinstance(entry["observed"], dict):
            _fail("E_REMOTE_OBSERVATION", f"{action} observed state must be object or null")
        normalized.append(entry)
    canonical_json_bytes(state)
    return state


def validate_delivery_result_v1(payload: Any) -> dict[str, Any]:
    result = _closed_object(payload, _DELIVERY_RESULT_FIELDS, "delivery result v1")
    if type(result["schema_version"]) is not int or result["schema_version"] != 1:
        _fail("E_SCHEMA_VERSION", "delivery result requires schema_version 1")
    if result["status"] not in {"delivered", "failed"}:
        _fail("E_DELIVERY_RESULT", "delivery result status is unsupported")
    _hash(result["operation_id"], "E_DELIVERY_RESULT", "delivery result operation_id")
    _branch(result["branch"])
    if type(result["attempts"]) is not int or result["attempts"] < 0:
        _fail("E_DELIVERY_RESULT", "delivery result attempts must be nonnegative integer")
    if type(result["idempotent"]) is not bool:
        _fail("E_DELIVERY_RESULT", "delivery result idempotent must be boolean")
    if result["status"] == "delivered":
        if result["reason"] is not None:
            _fail("E_DELIVERY_RESULT", "delivered result cannot claim a failure reason")
        if not isinstance(result["commit_sha"], str) or not _COMMIT.fullmatch(result["commit_sha"]):
            _fail("E_DELIVERY_RESULT", "delivered result requires observed commit SHA")
        if (
            not isinstance(result["pr_url"], str)
            or not result["pr_url"].startswith("https://github.com/")
            or type(result["pr_number"]) is not int
            or result["pr_number"] < 1
        ):
            _fail("E_DELIVERY_RESULT", "delivered result requires observed pull request")
    else:
        if any(result[field] is not None for field in ("commit_sha", "pr_url", "pr_number")):
            _fail("E_DELIVERY_RESULT", "failed result cannot claim remote identifiers")
        if not isinstance(result["reason"], str) or not re.fullmatch(r"E_[A-Z0-9_]+", result["reason"]):
            _fail("E_DELIVERY_RESULT", "failed result requires a stable reason code")
    canonical_json_bytes(result)
    return result


def _root(root: Path) -> Path:
    try:
        info = root.lstat()
    except OSError as exc:
        raise ContractError("E_APPROVAL_INPUT", "approval input root is unavailable") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
        _fail("E_APPROVAL_INPUT", "approval input root must be a real directory")
    return root


def _read(root: Path, relative: str, code: str) -> bytes:
    try:
        return read_regular_bytes(
            root.joinpath(*PurePosixPath(relative).parts),
            maximum=MAX_BOUND_BYTES,
            path_code=code,
            size_code=code,
        )
    except ContractError as exc:
        if exc.code == code:
            raise
        raise ContractError(code, f"bound input is unavailable: {relative}") from exc


def _canonical_report(root: Path, relative: str, code: str) -> tuple[bytes, dict[str, Any]]:
    raw = _read(root, relative, code)
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError(code, f"bound report is malformed: {relative}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        _fail(code, f"bound report is not canonical JSON: {relative}")
    return raw, value


def _validate_compiled_binding(pr: dict[str, Any], root: Path) -> None:
    """Re-read the complete stage-6 set before deriving approval bindings."""

    compiled = cast(dict[str, Any], pr["compiled"])
    inventory = cast(dict[str, str], compiled["inventory"])
    manifest_raw = _read(root, COMPILED_MANIFEST_PATH, "E_APPROVAL_FINGERPRINT")
    bundle = {
        "schema_version": 3,
        "artifacts": {
            "asset_manifest": {
                "path": COMPILED_MANIFEST_PATH,
                "sha256": hashlib.sha256(manifest_raw).hexdigest(),
            }
        },
        "compiled": {
            "inventory": dict(inventory),
            "fingerprint": compiled["fingerprint"],
            "retention": "manual",
        },
    }
    try:
        load_compiled_visual(root, bundle)
    except ContractError as exc:
        raise ContractError(
            "E_APPROVAL_FINGERPRINT",
            "compiled artifact bytes differ from PR bundle",
        ) from exc


def current_approval_bindings(pr_payload: Any, candidate_root: Path) -> dict[str, Any]:
    try:
        pr = _LEGACY.validate_pr_bundle(pr_payload)
    except ContractError as exc:
        raise ContractError("E_APPROVAL_FINGERPRINT", str(exc)) from exc
    root = _root(candidate_root)
    if pr["schema_version"] == 2:
        _validate_compiled_binding(pr, root)
    target = cast(dict[str, Any], pr["target"])
    evaluation = cast(dict[str, Any], pr["evaluation"])
    candidates = [
        {
            "path": cast(str, item["path"]),
            "sha256": cast(str, item["after_sha256"]),
        }
        for item in [
            *cast(list[dict[str, Any]], pr["candidate_files"]),
            *cast(list[dict[str, Any]], pr["semantic_sources"]),
        ]
    ]
    for item in candidates:
        raw = _read(root, item["path"], "E_APPROVAL_FINGERPRINT")
        if hashlib.sha256(raw).hexdigest() != item["sha256"]:
            _fail("E_APPROVAL_FINGERPRINT", f"candidate bytes drifted: {item['path']}")

    evaluation_raw, _ = _canonical_report(root, EVALUATION_PATH, "E_EVALUATION_DRIFT")
    evaluation_sha256 = hashlib.sha256(evaluation_raw).hexdigest()
    if evaluation_sha256 != evaluation["report_sha256"]:
        _fail("E_EVALUATION_DRIFT", "evaluation report bytes differ from PR bundle")
    preview_raw = _read(root, PREVIEW_PATH, "E_PREVIEW_DRIFT")
    report_raw, _ = _canonical_report(root, PREVIEW_REPORT_PATH, "E_PREVIEW_DRIFT")
    return {
        "repository": target["repository"],
        "base_sha": target["base_sha"],
        "proposed_branch": target["branch"],
        "pr_fingerprint": pr["fingerprint"],
        "candidate_hashes": candidates,
        "evaluation_sha256": evaluation_sha256,
        "preview": {
            "path": PREVIEW_PATH,
            "preview_sha256": hashlib.sha256(preview_raw).hexdigest(),
            "report_path": PREVIEW_REPORT_PATH,
            "report_sha256": hashlib.sha256(report_raw).hexdigest(),
        },
        "actions": list(ALLOWED_ACTIONS),
    }


def check_approval_envelope(
    approval_payload: Any,
    pr_payload: Any,
    candidate_root: Path,
) -> dict[str, object]:
    try:
        approval = validate_approval_envelope_v2(approval_payload)
    except ContractError as exc:
        return {
            "schema_version": 2,
            "status": "fail",
            "findings": [exc.code],
            "write_authority": None,
        }
    findings: list[str] = []
    try:
        expected = current_approval_bindings(pr_payload, candidate_root)
    except ContractError as exc:
        findings.append(exc.code)
        expected = None
    if approval["decision"] != "approve":
        findings.append("E_APPROVAL_DECISION")
    if expected is not None:
        for field in ("repository", "base_sha", "proposed_branch", "pr_fingerprint"):
            if approval[field] != expected[field]:
                findings.append("E_APPROVAL_FINGERPRINT")
        if approval["candidate_hashes"] != expected["candidate_hashes"]:
            findings.append("E_APPROVAL_CANDIDATES")
        if approval["evaluation_sha256"] != expected["evaluation_sha256"]:
            findings.append("E_EVALUATION_DRIFT")
        if approval["preview"] != expected["preview"]:
            findings.append("E_PREVIEW_DRIFT")
        if approval["actions"] != expected["actions"]:
            findings.append("E_APPROVAL_ACTIONS")
    findings = sorted(set(findings))
    authority = None
    if not findings and expected is not None:
        authority = expected
    return {
        "schema_version": 2,
        "status": "authorized" if authority is not None else "fail",
        "findings": findings,
        "write_authority": authority,
    }


__all__ = [
    "ALLOWED_ACTIONS",
    "APPROVAL_SCHEMA_VERSION",
    "check_approval_envelope",
    "current_approval_bindings",
    "validate_approval_envelope_v2",
    "validate_delivery_result_v1",
    "validate_remote_state_v2",
]
