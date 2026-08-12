"""Incident log: evaluation failures and identity overrides.

Every failed evaluation appends one ``lessons-pending.json`` entry per hard
finding into the attempt directory that also holds the evaluation report;
``check-publish-gate`` records a separately approved identity override there
as an independent non-failure entry.  ``lessons-pending.json`` is a candidate
inbox, never the ledger: only human-confirmed entries migrate into
``skill/references/lessons.md``.  Entries are canonical, deduplicated, and
purely derived from the report, so re-running an evaluation never duplicates
a lesson.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    canonical_sha256,
    read_json_object,
    write_canonical_json_atomic,
)

LESSONS_PENDING_SCHEMA_VERSION = 1
LESSONS_PENDING_NAME = "lessons-pending.json"
INCIDENT_LOG_ERROR_CODE = "E_INCIDENT_LOG"

# Fix hints map a gate code to the durable corrective action a future run
# should follow; unknown codes receive the generic fallback.
_FIX_HINTS: Mapping[str, str] = {
    "E_VOICE_MATCH": "align candidate wording with repository evidence, then re-run evaluation",
    "E_IDENTITY_MATCH": "use repository identity tokens or record an approved identity override, then re-run evaluation",
    "E_SELF_REVIEW": "bind every candidate claim to a repository-evidence fact, then re-run evaluation",
    "E_README_AUDIT": "fix the README audit findings, then re-run evaluation",
    "E_VISUAL_DETERMINISM": "regenerate the compiled visual projections deterministically, then re-run evaluation",
    "E_VISUAL_FINGERPRINT": "regenerate the compiled visual projections and re-run evaluation",
    "E_CLAIM_COVERAGE": "cover every claim with repository-evidence facts, then re-run evaluation",
}
_FALLBACK_FIX_HINT = "address the finding, then re-run evaluation"


def _fix_hint(gate: str) -> str:
    return _FIX_HINTS.get(gate, _FALLBACK_FIX_HINT)


def _load_entries(attempt_dir: Path) -> list[dict[str, Any]]:
    path = attempt_dir / LESSONS_PENDING_NAME
    if not path.exists():
        return []
    try:
        payload = read_json_object(path)
    except ContractError as exc:
        raise ContractError(
            INCIDENT_LOG_ERROR_CODE,
            f"{LESSONS_PENDING_NAME} is malformed: {exc}",
        ) from exc
    if (
        not isinstance(payload, Mapping)
        or payload.get("schema_version") != LESSONS_PENDING_SCHEMA_VERSION
    ):
        raise ContractError(
            INCIDENT_LOG_ERROR_CODE,
            f"{LESSONS_PENDING_NAME} must be a v{LESSONS_PENDING_SCHEMA_VERSION} incident log",
        )
    entries = payload.get("entries")
    if not isinstance(entries, list):
        raise ContractError(
            INCIDENT_LOG_ERROR_CODE,
            f"{LESSONS_PENDING_NAME} entries must be a list",
        )
    return [dict(entry) for entry in entries if isinstance(entry, Mapping)]


def _append_entry(attempt_dir: Path, entry: Mapping[str, object]) -> None:
    entries = _load_entries(attempt_dir)
    if entry not in entries:
        entries.append(dict(entry))
        write_canonical_json_atomic(
            attempt_dir / LESSONS_PENDING_NAME,
            {
                "schema_version": LESSONS_PENDING_SCHEMA_VERSION,
                "entries": entries,
            },
        )


def append_failure_entry(
    report: Mapping[str, Any],
    attempt_dir: Path | str,
) -> None:
    """Append one lessons-pending failure entry per hard-gate finding.

    Passing reports and failing reports without findings write nothing; the
    entries carry the gate code, the finding's root cause, a deterministic
    fix hint, and the report hash for provenance.
    """
    root = Path(attempt_dir)
    if report.get("status") != "fail":
        return
    hard_gate = report.get("hard_gate")
    findings = hard_gate.get("findings") if isinstance(hard_gate, Mapping) else None
    if not isinstance(findings, list):
        return
    report_sha256 = canonical_sha256(report)
    for finding in findings:
        if not isinstance(finding, Mapping):
            continue
        gate = finding.get("code")
        root_cause = finding.get("message")
        if (
            not isinstance(gate, str)
            or not gate
            or not isinstance(root_cause, str)
            or not root_cause
        ):
            continue
        _append_entry(
            root,
            {
                "kind": "failure",
                "gate": gate,
                "root_cause": root_cause,
                "fix_hint": _fix_hint(gate),
                "report_sha256": report_sha256,
            },
        )


def record_identity_override(
    report: Mapping[str, Any],
    attempt_dir: Path | str,
) -> None:
    """Record a written identity override as an independent pending lesson.

    Reads ``report["identity_override"]`` (evaluation-report.v3) and appends
    one ``kind: "override"`` entry carrying the written reason and approver.
    A missing or null override writes nothing.
    """
    root = Path(attempt_dir)
    override = report.get("identity_override")
    if not isinstance(override, Mapping):
        return
    reason = override.get("reason")
    if not isinstance(reason, str) or not reason:
        return
    approved_by = override.get("approved_by")
    _append_entry(
        root,
        {
            "kind": "override",
            "gate": "E_IDENTITY_MATCH",
            "reason": reason,
            "approved_by": approved_by if isinstance(approved_by, str) else "",
            "report_sha256": canonical_sha256(report),
        },
    )


__all__ = [
    "INCIDENT_LOG_ERROR_CODE",
    "LESSONS_PENDING_NAME",
    "LESSONS_PENDING_SCHEMA_VERSION",
    "append_failure_entry",
    "record_identity_override",
]
