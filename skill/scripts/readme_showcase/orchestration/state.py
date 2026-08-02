from __future__ import annotations

from copy import deepcopy
from enum import Enum
from typing import Any, Mapping

from ..contracts.run import STAGE_NAMES, canonical_repository, compute_run_id, normalize_configuration, validate_run_manifest


class RunState(str, Enum):
    CREATED = "created"
    RUNNING = "running"
    WAITING_FOR_PLAN = "waiting-for-plan"
    WAITING_FOR_CANDIDATE = "waiting-for-candidate"
    FAILED = "failed"
    MANUAL_REVIEW_REQUIRED = "manual-review-required"
    COMPLETE = "complete"


class StageState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    PASS = "pass"
    FAILED = "failed"
    STALE = "stale"
    WAITING_FOR_PLAN = "waiting-for-plan"
    WAITING_FOR_CANDIDATE = "waiting-for-candidate"


def initial_stages() -> list[dict[str, Any]]:
    return [
        {
            "name": name,
            "status": StageState.PENDING.value,
            "input_sha256": None,
            "output_sha256": None,
            "attempt": 0,
            "started_at": None,
            "completed_at": None,
        }
        for name in STAGE_NAMES
    ]


def stale_from(stages: list[dict[str, Any]], index: int) -> list[dict[str, Any]]:
    updated = deepcopy(stages)
    for stage in updated[index:]:
        stage["status"] = StageState.STALE.value
    return updated


def reconcile_inputs(
    manifest: Mapping[str, Any],
    *,
    repository: str,
    base_sha: str,
    configuration: Mapping[str, Any],
    timestamp: str,
) -> dict[str, Any]:
    current = deepcopy(validate_run_manifest(dict(manifest)))
    normalized_repository = canonical_repository(repository)
    normalized_configuration = normalize_configuration(configuration)
    run_id = compute_run_id(
        repository=normalized_repository,
        base_sha=base_sha,
        configuration=normalized_configuration,
    )
    if run_id != current["run_id"]:
        current["run_id"] = run_id
        current["target"]["repository"] = normalized_repository
        current["target"]["base_sha"] = base_sha.lower()
        current["configuration"] = normalized_configuration
        current["current_stage"] = STAGE_NAMES[0]
        current["stages"] = stale_from(current["stages"], 0)
    current["updated_at"] = timestamp
    validate_run_manifest(current)
    return current
