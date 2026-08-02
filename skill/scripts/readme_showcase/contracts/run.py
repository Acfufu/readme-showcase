from __future__ import annotations

import re
from typing import Any, Mapping

from ...pipeline_contracts import ContractError, canonical_sha256, validate_contract


RUN_SCHEMA_VERSION = 1
RUN_STATES = frozenset(
    {
        "created",
        "running",
        "waiting-for-plan",
        "waiting-for-candidate",
        "failed",
        "manual-review-required",
        "complete",
    }
)
STAGE_STATES = frozenset(
    {
        "pending",
        "running",
        "pass",
        "failed",
        "stale",
        "waiting-for-plan",
        "waiting-for-candidate",
    }
)
STAGE_NAMES = (
    "scan",
    "retrieve",
    "plan-import",
    "generation-request",
    "candidate",
    "bundle-assemble",
    "validation",
    "evaluation",
)
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_TIMESTAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_CONFIGURATION_FIELDS = frozenset({"mode", "project_type", "locales", "scanner_profile"})


def _string(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be a non-empty string")
    return value


def canonical_repository(repository: str) -> str:
    value = _string(repository, "target.repository").strip()
    for prefix in ("https://github.com/", "http://github.com/", "ssh://git@github.com/", "git@github.com:"):
        if value.lower().startswith(prefix):
            value = value[len(prefix) :]
            break
    value = value.rstrip("/")
    if value.lower().endswith(".git"):
        value = value[:-4]
    parts = value.split("/")
    if len(parts) != 2 or any(not part or part in {".", ".."} for part in parts):
        raise ContractError("E_RUN_TARGET", "target.repository must be owner/name")
    return "/".join(parts).lower()


def normalize_configuration(configuration: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(configuration, Mapping):
        raise ContractError("E_SCHEMA_TYPE", "configuration must be an object")
    keys = set(configuration)
    if any(not isinstance(key, str) for key in keys):
        raise ContractError("E_SCHEMA_KEY_TYPE", "configuration contains a non-string object key")
    unknown = sorted(keys - _CONFIGURATION_FIELDS)
    missing = sorted(_CONFIGURATION_FIELDS - keys)
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"configuration contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"configuration is missing required field: {missing[0]}")
    locales = configuration["locales"]
    if not isinstance(locales, (list, tuple)) or not locales:
        raise ContractError("E_SCHEMA_TYPE", "configuration.locales must be a non-empty array")
    normalized_locales = sorted({_string(locale, "configuration.locales[]") for locale in locales})
    return {
        "locales": normalized_locales,
        "mode": _string(configuration["mode"], "configuration.mode"),
        "project_type": _string(configuration["project_type"], "configuration.project_type"),
        "scanner_profile": _string(configuration["scanner_profile"], "configuration.scanner_profile"),
    }


def compute_run_id(*, repository: str, base_sha: str, configuration: Mapping[str, Any]) -> str:
    if not isinstance(base_sha, str) or not _SHA1.fullmatch(base_sha.lower()):
        raise ContractError("E_RUN_BASE", "target.base_sha must be a 40-character hexadecimal SHA")
    return canonical_sha256(
        {
            "base_sha": base_sha.lower(),
            "configuration": normalize_configuration(configuration),
            "repository": canonical_repository(repository),
            "schema_version": RUN_SCHEMA_VERSION,
        }
    )


def _optional_sha(value: Any, path: str) -> None:
    if value is not None and (not isinstance(value, str) or not _SHA256.fullmatch(value)):
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be null or a SHA-256 hex digest")


def _timestamp(value: Any, path: str, *, nullable: bool = False) -> None:
    if nullable and value is None:
        return
    if not isinstance(value, str) or not _TIMESTAMP.fullmatch(value):
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be an RFC 3339 UTC timestamp")


def validate_run_manifest(payload: Any) -> dict[str, Any]:
    manifest = validate_contract(
        payload,
        required={
            "schema_version",
            "run_id",
            "created_at",
            "updated_at",
            "status",
            "target",
            "configuration",
            "current_stage",
            "stages",
        },
        optional=set(),
        context="run manifest",
    )
    if not isinstance(manifest["run_id"], str) or not _SHA256.fullmatch(manifest["run_id"]):
        raise ContractError("E_SCHEMA_TYPE", "run manifest.run_id must be a SHA-256 hex digest")
    _timestamp(manifest["created_at"], "run manifest.created_at")
    _timestamp(manifest["updated_at"], "run manifest.updated_at")
    if manifest["status"] not in RUN_STATES:
        raise ContractError("E_SCHEMA_VALUE", "run manifest.status is unsupported")

    target = manifest["target"]
    if not isinstance(target, dict) or set(target) != {"root", "repository", "base_sha"}:
        raise ContractError("E_SCHEMA_FIELDS", "run manifest.target fields are invalid")
    root = _string(target["root"], "run manifest.target.root")
    if not root.startswith("/"):
        raise ContractError("E_SCHEMA_VALUE", "run manifest.target.root must be absolute")
    repository = canonical_repository(target["repository"])
    base_sha = _string(target["base_sha"], "run manifest.target.base_sha").lower()
    if not _SHA1.fullmatch(base_sha):
        raise ContractError("E_RUN_BASE", "target.base_sha must be a 40-character hexadecimal SHA")
    configuration = normalize_configuration(manifest["configuration"])
    if target["repository"] != repository or target["base_sha"] != base_sha:
        raise ContractError("E_SCHEMA_VALUE", "run manifest.target must use canonical values")
    if manifest["configuration"] != configuration:
        raise ContractError("E_SCHEMA_VALUE", "run manifest.configuration must use normalized values")

    if manifest["current_stage"] is not None and manifest["current_stage"] not in STAGE_NAMES:
        raise ContractError("E_SCHEMA_VALUE", "run manifest.current_stage is unsupported")
    stages = manifest["stages"]
    if not isinstance(stages, list) or len(stages) != len(STAGE_NAMES):
        raise ContractError("E_SCHEMA_TYPE", "run manifest.stages must contain the eight ordered stages")
    for index, (stage, expected_name) in enumerate(zip(stages, STAGE_NAMES, strict=True)):
        if not isinstance(stage, dict) or set(stage) != {
            "name",
            "status",
            "input_sha256",
            "output_sha256",
            "attempt",
            "started_at",
            "completed_at",
        }:
            raise ContractError("E_SCHEMA_FIELDS", f"run manifest.stages[{index}] fields are invalid")
        if stage["name"] != expected_name or stage["status"] not in STAGE_STATES:
            raise ContractError("E_SCHEMA_VALUE", f"run manifest.stages[{index}] name or status is invalid")
        if type(stage["attempt"]) is not int or stage["attempt"] < 0:
            raise ContractError("E_SCHEMA_TYPE", f"run manifest.stages[{index}].attempt must be a non-negative integer")
        _optional_sha(stage["input_sha256"], f"run manifest.stages[{index}].input_sha256")
        _optional_sha(stage["output_sha256"], f"run manifest.stages[{index}].output_sha256")
        _timestamp(stage["started_at"], f"run manifest.stages[{index}].started_at", nullable=True)
        _timestamp(stage["completed_at"], f"run manifest.stages[{index}].completed_at", nullable=True)

    expected_run_id = compute_run_id(
        repository=repository,
        base_sha=base_sha,
        configuration=configuration,
    )
    if manifest["run_id"] != expected_run_id:
        raise ContractError("E_RUN_ID", "run manifest.run_id does not match canonical inputs")
    return manifest
