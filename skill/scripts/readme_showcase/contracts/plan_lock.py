from __future__ import annotations

import hashlib
import importlib
import re
from pathlib import Path
from typing import Any, Mapping, NoReturn

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object_bytes,
)
from .plan import canonical_readme_plan_bytes


PLAN_LOCK_SCHEMA_VERSION = 1
PLAN_LOCK_REPORT_SCHEMA_VERSION = 1
PLAN_LOCK_PATH = "plan-lock.v1.json"
PLAN_LOCK_REPORT_PATH = "plan-lock-report.v1.json"
APPROVAL_ENVELOPE_PATH = "approval-envelope.json"

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")

_LOCK_FIELDS = {
    "schema_version",
    "plan_sha256",
    "locale_pairs",
    "asset_sha256",
    "claim_ids",
    "locked_after",
    "approval_sha256",
}
_REPORT_FIELDS = {"schema_version", "status", "lock_sha256", "plan_sha256", "findings"}

_CORE = importlib.import_module(
    "skill.scripts.pipeline_core" if (__package__ or "").startswith("skill.") else "pipeline_core"
)
_artifact_json = _CORE._artifact_json


def _fail(code: str, message: str) -> NoReturn:
    raise ContractError(code, message)


def _locale_pairs(plan: Mapping[str, Any]) -> list[list[str]]:
    """Explicit locale pairs from the plan, never inferred from filenames."""
    if plan.get("schema_version") == 1:
        tags = sorted(plan.get("languages", []))
    else:
        tags = sorted(item["tag"] for item in plan.get("locales", []))
    return [list(tags)] if tags else []


def _asset_hashes(bundle: Mapping[str, Any]) -> list[dict[str, str]]:
    candidate = bundle.get("candidate")
    if not isinstance(candidate, Mapping):
        return []
    assets = candidate.get("assets")
    if not isinstance(assets, list):
        return []
    result: list[dict[str, str]] = []
    for item in assets:
        if isinstance(item, Mapping):
            path = item.get("path")
            digest = item.get("sha256")
            if isinstance(path, str) and isinstance(digest, str):
                result.append({"path": path, "sha256": digest})
    return sorted(result, key=lambda item: item["path"])


def _claim_ids(artifact_root: Path, artifacts: Mapping[str, Any]) -> list[str]:
    claim_map_ref = artifacts.get("claim_map")
    if claim_map_ref is None:
        return []
    try:
        claim_map, _ = _artifact_json(artifact_root, claim_map_ref, "bundle artifacts.claim_map")
    except ContractError as exc:
        if exc.code == "E_BUNDLE_MISSING":
            _fail("E_PLAN_DRIFT", "locked bundle claim map artifact is missing")
        raise
    ids: list[str] = []
    for collection in ("markdown_blocks", "diagram_labels"):
        for claim in claim_map.get(collection, []):
            if isinstance(claim, Mapping):
                claim_id = claim.get("claim_id")
                if isinstance(claim_id, str):
                    ids.append(claim_id)
    return sorted(set(ids))


def _recompute(bundle: Mapping[str, Any], artifact_root: Path) -> dict[str, Any]:
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, Mapping) or "plan" not in artifacts:
        _fail("E_PLAN_DRIFT", "locked bundle is missing artifacts.plan")
    plan, _ = _artifact_json(
        artifact_root, artifacts["plan"], "bundle artifacts.plan"
    )
    version = plan.get("schema_version")
    if type(version) is not int:
        _fail("E_PLAN_DRIFT", "locked bundle plan has no schema_version")
    plan_sha256 = hashlib.sha256(
        canonical_readme_plan_bytes(plan, version=version)
    ).hexdigest()
    return {
        "plan_sha256": plan_sha256,
        "locale_pairs": _locale_pairs(plan),
        "asset_sha256": _asset_hashes(bundle),
        "claim_ids": _claim_ids(artifact_root, artifacts),
    }


def build_plan_lock(
    plan: Mapping[str, Any],
    *,
    asset_hashes: list[dict[str, str]],
    claim_ids: list[str],
    approval_sha256: str,
    locked_after: str,
) -> dict[str, Any]:
    version = plan.get("schema_version")
    if type(version) is not int:
        _fail("E_PLAN_LOCK", "plan lock requires a plan with schema_version")
    plan_sha256 = hashlib.sha256(
        canonical_readme_plan_bytes(plan, version=version)
    ).hexdigest()
    return {
        "schema_version": PLAN_LOCK_SCHEMA_VERSION,
        "plan_sha256": plan_sha256,
        "locale_pairs": _locale_pairs(plan),
        "asset_sha256": sorted(asset_hashes, key=lambda item: item["path"]),
        "claim_ids": sorted(set(claim_ids)),
        "locked_after": locked_after,
        "approval_sha256": approval_sha256,
    }


def validate_plan_lock_v1(
    payload: Any,
    *,
    bundle: Mapping[str, Any] | None = None,
    artifact_root: Path | None = None,
    approval_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("E_PLAN_LOCK", "plan lock must be a JSON object")
    if payload.get("schema_version") != PLAN_LOCK_SCHEMA_VERSION:
        _fail("E_PLAN_LOCK", "plan lock requires schema_version 1")
    missing = sorted(_LOCK_FIELDS - set(payload))
    if missing:
        _fail("E_PLAN_LOCK", f"plan lock is missing required field: {missing[0]}")
    unknown = sorted(set(payload) - _LOCK_FIELDS)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"plan lock contains unknown field: {unknown[0]}")

    plan_sha256 = payload["plan_sha256"]
    if not isinstance(plan_sha256, str) or not _SHA256.fullmatch(plan_sha256):
        _fail("E_PLAN_LOCK", "plan lock.plan_sha256 must be a SHA-256 hex digest")
    locale_pairs = payload["locale_pairs"]
    if not isinstance(locale_pairs, list) or not locale_pairs:
        _fail("E_PLAN_LOCK", "plan lock.locale_pairs must be a non-empty array")
    for pair in locale_pairs:
        if (
            not isinstance(pair, list)
            or not pair
            or any(not isinstance(tag, str) or not tag for tag in pair)
        ):
            _fail("E_PLAN_LOCK", "plan lock.locale_pairs entries must be non-empty string arrays")
    asset_hashes = payload["asset_sha256"]
    if not isinstance(asset_hashes, list):
        _fail("E_PLAN_LOCK", "plan lock.asset_sha256 must be an array")
    for item in asset_hashes:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            _fail("E_PLAN_LOCK", "plan lock.asset_sha256 entries require path and sha256")
        if not isinstance(item["path"], str) or not item["path"]:
            _fail("E_PLAN_LOCK", "plan lock.asset_sha256.path must be a non-empty string")
        if not isinstance(item["sha256"], str) or not _SHA256.fullmatch(item["sha256"]):
            _fail("E_PLAN_LOCK", "plan lock.asset_sha256.sha256 must be a SHA-256 hex digest")
    claim_ids = payload["claim_ids"]
    if not isinstance(claim_ids, list) or any(
        not isinstance(item, str) or not item for item in claim_ids
    ):
        _fail("E_PLAN_LOCK", "plan lock.claim_ids must be a string array")
    locked_after = payload["locked_after"]
    if not isinstance(locked_after, str) or not _TIMESTAMP.fullmatch(locked_after):
        _fail("E_PLAN_LOCK", "plan lock.locked_after must be an RFC 3339 UTC timestamp")
    approval_sha256 = payload["approval_sha256"]
    if not isinstance(approval_sha256, str) or not _SHA256.fullmatch(approval_sha256):
        _fail("E_PLAN_LOCK", "plan lock.approval_sha256 must be a SHA-256 hex digest")

    if bundle is None:
        return dict(payload)
    if artifact_root is None:
        _fail("E_PLAN_LOCK", "plan lock drift check requires an artifact root")

    expected = _recompute(bundle, artifact_root)
    findings: list[dict[str, str]] = []
    if expected["plan_sha256"] != plan_sha256:
        findings.append({"code": "E_PLAN_DRIFT", "message": "bundle plan bytes drift from the locked plan"})
    if expected["locale_pairs"] != locale_pairs:
        findings.append({"code": "E_PLAN_DRIFT", "message": "bundle locale pairs drift from the locked plan"})
    if expected["asset_sha256"] != asset_hashes:
        findings.append({"code": "E_PLAN_DRIFT", "message": "bundle candidate assets drift from the locked plan"})
    if expected["claim_ids"] != claim_ids:
        findings.append({"code": "E_PLAN_DRIFT", "message": "bundle claim map drifts from the locked plan"})
    if approval_envelope is None:
        findings.append({"code": "E_PLAN_DRIFT", "message": "plan lock has no bound approval envelope"})
    else:
        if approval_envelope.get("decision") != "approve":
            findings.append({"code": "E_PLAN_DRIFT", "message": "plan lock approval decision is not approve"})
        if canonical_sha256(approval_envelope) != approval_sha256:
            findings.append({"code": "E_PLAN_DRIFT", "message": "plan lock approval binding differs from the approval envelope"})
    if findings:
        _fail("E_PLAN_DRIFT", findings[0]["message"])
    return {
        "schema_version": PLAN_LOCK_REPORT_SCHEMA_VERSION,
        "status": "pass",
        "lock_sha256": canonical_sha256(payload),
        "plan_sha256": plan_sha256,
        "findings": [],
    }


def validate_plan_lock_report_v1(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        _fail("E_PLAN_LOCK", "plan lock report must be a JSON object")
    if payload.get("schema_version") != PLAN_LOCK_REPORT_SCHEMA_VERSION:
        _fail("E_PLAN_LOCK", "plan lock report requires schema_version 1")
    missing = sorted(_REPORT_FIELDS - set(payload))
    if missing:
        _fail("E_PLAN_LOCK", f"plan lock report is missing required field: {missing[0]}")
    unknown = sorted(set(payload) - _REPORT_FIELDS)
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"plan lock report contains unknown field: {unknown[0]}")
    status = payload["status"]
    if status not in {"pass", "fail"}:
        _fail("E_PLAN_LOCK", "plan lock report.status must be pass or fail")
    lock_sha256 = payload["lock_sha256"]
    if not isinstance(lock_sha256, str) or not _SHA256.fullmatch(lock_sha256):
        _fail("E_PLAN_LOCK", "plan lock report.lock_sha256 must be a SHA-256 hex digest")
    plan_sha256 = payload["plan_sha256"]
    if not isinstance(plan_sha256, str) or not _SHA256.fullmatch(plan_sha256):
        _fail("E_PLAN_LOCK", "plan lock report.plan_sha256 must be a SHA-256 hex digest")
    findings = payload["findings"]
    if not isinstance(findings, list):
        _fail("E_PLAN_LOCK", "plan lock report.findings must be an array")
    for finding in findings:
        if not isinstance(finding, dict) or set(finding) != {"code", "message"}:
            _fail("E_PLAN_LOCK", "plan lock report.findings entries require code and message")
        if not isinstance(finding["code"], str) or not finding["code"]:
            _fail("E_PLAN_LOCK", "plan lock report.findings.code must be a non-empty string")
        if not isinstance(finding["message"], str) or not finding["message"]:
            _fail("E_PLAN_LOCK", "plan lock report.findings.message must be a non-empty string")
    return dict(payload)


def check_plan_lock(
    *,
    lock_path: Path,
    approval_path: Path,
    manifest: Mapping[str, Any],
    bundle: Mapping[str, Any],
    artifact_root: Path,
) -> dict[str, Any] | None:
    """EvaluateStage gate entry.

    Fail-closed: a run that requires a lock but has none fails with
    E_PLAN_LOCK.  A lock that exists is always validated (belt), even when
    the manifest does not require one, so deleting the lock cannot bypass
    the gate.  Returns the pass report, or raises E_PLAN_LOCK / E_PLAN_DRIFT.
    """
    requires = bool(manifest.get("requires_plan_lock", False))
    lock_exists = lock_path.is_file()
    if not lock_exists and not requires:
        return None
    if not lock_exists:
        _fail("E_PLAN_LOCK", "run requires a plan lock but plan-lock.v1.json is missing")
    raw, lock = read_json_object_bytes(lock_path)
    if raw != canonical_json_bytes(lock):
        _fail("E_PLAN_LOCK", "plan-lock.v1.json must use canonical JSON bytes")
    approval_envelope: Mapping[str, Any] | None = None
    if approval_path.is_file():
        _, approval_envelope = read_json_object_bytes(approval_path)
    return validate_plan_lock_v1(
        lock,
        bundle=bundle,
        artifact_root=artifact_root,
        approval_envelope=approval_envelope,
    )


__all__ = [
    "APPROVAL_ENVELOPE_PATH",
    "PLAN_LOCK_PATH",
    "PLAN_LOCK_REPORT_PATH",
    "PLAN_LOCK_REPORT_SCHEMA_VERSION",
    "PLAN_LOCK_SCHEMA_VERSION",
    "build_plan_lock",
    "check_plan_lock",
    "validate_plan_lock_report_v1",
    "validate_plan_lock_v1",
]