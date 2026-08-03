from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    read_json_object_bytes,
    write_canonical_json_atomic,
)
from ..contracts.publishing import (
    APPROVAL_SCHEMA_VERSION,
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
    output_key = os.path.abspath(os.fspath(output))
    if output_key in {os.path.abspath(os.fspath(path)) for path in bound_paths}:
        raise ContractError(INPUT_ERROR_CODE, "approval output must not replace a bound input")
    write_canonical_json_atomic(output, envelope)
    return envelope


__all__ = ["create_approval_template", "create_approval_template_from_path"]
