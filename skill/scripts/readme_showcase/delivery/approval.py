from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object_bytes,
    write_canonical_json_atomic,
)
from ..contracts.plan_lock import (
    APPROVAL_ENVELOPE_PATH,
    PLAN_LOCK_PATH,
    build_plan_lock,
)
from ..contracts.publishing import (
    APPROVAL_SCHEMA_VERSION,
    COMPILED_BOUND_PATHS,
    current_approval_bindings,
    validate_approval_envelope_v2,
)


INPUT_ERROR_CODE = "E_APPROVAL_INPUT"


def create_approval_template(pr_payload: Any, candidate_root: Path) -> dict[str, Any]:
    envelope = {
        "schema_version": APPROVAL_SCHEMA_VERSION,
        "decision": "reject",
        **current_approval_bindings(pr_payload, candidate_root),
    }
    return validate_approval_envelope_v2(envelope)


def create_approval_template_from_path(pr_bundle: Path, output: Path) -> dict[str, Any]:
    raw, payload = read_json_object_bytes(pr_bundle)
    if raw != canonical_json_bytes(payload):
        raise ContractError(INPUT_ERROR_CODE, "PR bundle must be canonical JSON")
    envelope = create_approval_template(payload, pr_bundle.parent)
    bound_paths = {
        pr_bundle,
        pr_bundle.parent / "evaluation-report.json",
        pr_bundle.parent / envelope["preview"]["path"],
        pr_bundle.parent / envelope["preview"]["report_path"],
        *(
            pr_bundle.parent / item["path"]
            for item in envelope["candidate_hashes"]
        ),
    }
    compiled_root = None
    if payload["schema_version"] == 2:
        bound_paths.update(
            pr_bundle.parent.joinpath(*Path(relative).parts)
            for relative in COMPILED_BOUND_PATHS
        )
        compiled_root = os.path.abspath(os.fspath(pr_bundle.parent / "compiled"))
    output_key = os.path.abspath(os.fspath(output))
    output_in_compiled = (
        compiled_root is not None
        and os.path.commonpath((output_key, compiled_root)) == compiled_root
    )
    if output_in_compiled or output_key in {
        os.path.abspath(os.fspath(path))
        for path in bound_paths
    }:
        raise ContractError(INPUT_ERROR_CODE, "approval output must not replace a bound input")
    write_canonical_json_atomic(output, envelope)
    return envelope


def write_plan_lock(
    workspace: Path,
    approval_payload: Any,
    *,
    locked_after: str,
) -> dict[str, Any] | None:
    """Write plan-lock.v1.json from a human-approved approval envelope.

    Only the approval handler calls this, and only after the envelope has been
    checked authorized.  The lock pins the workspace plan bytes, the candidate
    asset hashes, and the claim-map claim IDs; the gate recomputes every value
    from the evaluated bundle and rejects any drift.
    """
    plan_path = workspace / "inputs/readme-plan.json"
    if not (workspace / "run-manifest.json").is_file() or not plan_path.is_file():
        return None
    raw, plan = read_json_object_bytes(plan_path)
    if raw != canonical_json_bytes(plan):
        raise ContractError(INPUT_ERROR_CODE, "workspace plan must be canonical JSON")
    asset_hashes: list[dict[str, str]] = []
    asset_manifest_path = workspace / "stages/05-candidate/asset-manifest.json"
    if asset_manifest_path.is_file():
        _, asset_manifest = read_json_object_bytes(asset_manifest_path)
        for item in asset_manifest.get("assets", []):
            if (
                isinstance(item, dict)
                and isinstance(item.get("path"), str)
                and isinstance(item.get("sha256"), str)
            ):
                asset_hashes.append({"path": item["path"], "sha256": item["sha256"]})
    claim_ids: list[str] = []
    claim_map_path = workspace / "stages/05-candidate/claim-map.json"
    if claim_map_path.is_file():
        _, claim_map = read_json_object_bytes(claim_map_path)
        for collection in ("markdown_blocks", "diagram_labels"):
            for claim in claim_map.get(collection, []):
                if isinstance(claim, dict) and isinstance(claim.get("claim_id"), str):
                    claim_ids.append(claim["claim_id"])
    lock = build_plan_lock(
        plan,
        asset_hashes=asset_hashes,
        claim_ids=claim_ids,
        approval_sha256=canonical_sha256(approval_payload),
        locked_after=locked_after,
    )
    write_canonical_json_atomic(workspace / PLAN_LOCK_PATH, lock)
    write_canonical_json_atomic(workspace / APPROVAL_ENVELOPE_PATH, approval_payload)
    return lock


__all__ = [
    "create_approval_template",
    "create_approval_template_from_path",
    "write_plan_lock",
]
