from __future__ import annotations

import importlib
import time
from pathlib import Path
from typing import Any, cast

_PREFIX = "skill.scripts." if __package__ else ""
_DOMAIN_PREFIX = "skill.scripts." if __package__ else "scripts."
_CONTRACTS = importlib.import_module(f"{_PREFIX}pipeline_contracts")
_SCANNER_SERVICE = importlib.import_module(f"{_PREFIX}readme_showcase.scanner.service")
_RETRIEVAL_SERVICE = importlib.import_module(f"{_PREFIX}readme_showcase.retrieval.service")
_BUNDLE = importlib.import_module(f"{_DOMAIN_PREFIX}readme_showcase.validation.legacy")
_EVALUATION = importlib.import_module(f"{_DOMAIN_PREFIX}readme_showcase.evaluation.legacy")
_DELIVERY = importlib.import_module(f"{_DOMAIN_PREFIX}readme_showcase.delivery.legacy")

ContractError = _CONTRACTS.ContractError
canonical_sha256 = _CONTRACTS.canonical_sha256
MAX_FILES = _SCANNER_SERVICE.MAX_FILES
MAX_DIRECTORIES = _SCANNER_SERVICE.MAX_DIRECTORIES
MAX_FILE_BYTES = _SCANNER_SERVICE.MAX_FILE_BYTES
MAX_TOTAL_BYTES = _SCANNER_SERVICE.MAX_TOTAL_BYTES
MAX_DEPTH = _SCANNER_SERVICE.MAX_DEPTH
MAX_SECONDS = _SCANNER_SERVICE.MAX_SECONDS
MAX_ARTIFACT_BYTES = _BUNDLE.MAX_ARTIFACT_BYTES

# Literal inventory keeps legacy diagnostics discoverable from this public facade.
_COMPATIBILITY_ERROR_CODES = frozenset(
    {
        "E_APPROVAL_BASE",
        "E_APPROVAL_BRANCH",
        "E_APPROVAL_CANDIDATES",
        "E_APPROVAL_DECISION",
        "E_APPROVAL_EVALUATION",
        "E_APPROVAL_FINGERPRINT",
        "E_APPROVAL_REPOSITORY",
        "E_APPROVAL_TARGET",
        "E_BUNDLE_ASSET",
        "E_BUNDLE_CLAIM",
        "E_BUNDLE_HASH",
        "E_BUNDLE_MISSING",
        "E_BUNDLE_MODE",
        "E_BUNDLE_PLAN",
        "E_BUNDLE_SIZE",
        "E_BUNDLE_TARGET",
        "E_CANDIDATE_DRIFT",
        "E_CLAIM_COVERAGE",
        "E_CLAIM_DUPLICATE",
        "E_CLAIM_EVIDENCE",
        "E_CLAIM_LABEL",
        "E_CLAIM_LANGUAGE",
        "E_DATASET_COMMIT",
        "E_DATASET_DUPLICATE_ID",
        "E_DATASET_EMBEDDED_CONTENT",
        "E_DATASET_ID",
        "E_DATASET_LICENSE",
        "E_DATASET_LICENSE_CONFLICT",
        "E_DATASET_LICENSE_EVIDENCE",
        "E_DATASET_LICENSE_REVIEW",
        "E_DATASET_PURPOSE",
        "E_DATASET_RECORDS",
        "E_DATASET_RECORD_ID",
        "E_DATASET_REPOSITORY",
        "E_DATASET_REVISION",
        "E_DATASET_SHA256",
        "E_DATASET_SLUG_LIST",
        "E_DATASET_SOURCE_DUPLICATE",
        "E_DATASET_SPLIT",
        "E_DATASET_SPLIT_LEAK",
        "E_DATASET_TEXT",
        "E_ELK_SEMANTIC",
        "E_ENGINE_METADATA",
        "E_EVALUATION_DRIFT",
        "E_INPUT_ENCODING",
        "E_INPUT_JSON",
        "E_INPUT_NOT_FOUND",
        "E_OBSERVATION_BINDING",
        "E_PATH",
        "E_PR_BASE",
        "E_PR_BUNDLE",
        "E_PR_EVALUATION",
        "E_PR_EVIDENCE",
        "E_PR_FINGERPRINT",
        "E_PR_GIT",
        "E_PR_INDEX",
        "E_PR_NO_CHANGES",
        "E_PR_PATH",
        "E_PR_TARGET",
        "E_PR_WORKTREE",
        "E_PUBLISH_BRANCH",
        "E_PUBLISH_HASH",
        "E_PUBLISH_PATH",
        "E_README_ACCESSIBILITY",
        "E_README_AUDIT",
        "E_README_COMMAND",
        "E_README_LANGUAGE",
        "E_REMOTE_BASE",
        "E_REMOTE_BRANCH",
        "E_REMOTE_BRANCH_EXISTS",
        "E_REMOTE_PERMISSION",
        "E_REMOTE_REPOSITORY",
        "E_REMOTE_TARGET",
        "E_RETRIEVAL_EVIDENCE",
        "E_RETRIEVAL_MANIFEST",
        "E_RETRIEVAL_MODE",
        "E_RETRIEVAL_QUERY",
        "E_SCAN_DEPTH",
        "E_SCAN_DIRECTORY_COUNT",
        "E_SCAN_FILE_COUNT",
        "E_SCAN_FILE_SIZE",
        "E_SCAN_IO",
        "E_SCAN_RACE",
        "E_SCAN_ROOT",
        "E_SCAN_TIME",
        "E_SCAN_TOTAL_SIZE",
        "E_SCHEMA_MISSING_FIELD",
        "E_SCHEMA_TYPE",
        "E_SCHEMA_UNKNOWN_FIELD",
        "E_SCHEMA_VERSION",
        "E_VISUAL_MOTION_APPROVAL",
    }
)


def validate_dataset_manifest(payload: Any) -> dict[str, object]:
    return cast(dict[str, object], _RETRIEVAL_SERVICE.validate_dataset_manifest(payload))


def retrieve_patterns(
    evidence: Any,
    manifest: Any | None,
    *,
    project_type: str,
    sections: list[str],
    tags: list[str],
    mode: str,
) -> dict[str, object]:
    return cast(dict[str, object], _RETRIEVAL_SERVICE.retrieve_patterns_v1(
        evidence, manifest, project_type=project_type, sections=sections, tags=tags, mode=mode,
    ))


def scan_repository(root: Path) -> dict[str, object]:
    return _SCANNER_SERVICE.scan_repository_v1(
        root,
        _SCANNER_SERVICE.ScanLimits(
            files=MAX_FILES,
            directories=MAX_DIRECTORIES,
            file_bytes=MAX_FILE_BYTES,
            total_bytes=MAX_TOTAL_BYTES,
            depth=MAX_DEPTH,
            seconds=MAX_SECONDS,
        ),
    )


def segment_markdown_blocks(text: str) -> list[str]:
    return cast(list[str], _BUNDLE.segment_markdown_blocks(text))


def validate_generated_bundle(payload: Any, artifact_root: Path) -> dict[str, object]:
    _BUNDLE.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    return cast(dict[str, object], _BUNDLE.validate_generated_bundle(payload, artifact_root))


def evaluate_generated_bundle(
    payload: Any,
    artifact_root: Path,
    *,
    observation: dict[str, object] | None = None,
    trusted_observation_sha256s: frozenset[str] = frozenset(),
) -> dict[str, object]:
    _BUNDLE.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    return cast(dict[str, object], _EVALUATION.evaluate_generated_bundle(
        payload,
        artifact_root,
        observation=observation,
        trusted_observation_sha256s=trusted_observation_sha256s,
    ))


def build_pr_bundle(
    payload: Any,
    evaluation: Any,
    artifact_root: Path,
    target_root: Path,
) -> dict[str, object]:
    _BUNDLE.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    _DELIVERY.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    return cast(dict[str, object], _DELIVERY.build_pr_bundle(
        payload, evaluation, artifact_root, target_root,
    ))


def check_publish_gate(
    pr_payload: Any,
    remote_payload: Any,
    approval_payload: Any,
    candidate_root: Path,
) -> dict[str, object]:
    _BUNDLE.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    _DELIVERY.MAX_ARTIFACT_BYTES = MAX_ARTIFACT_BYTES
    return cast(dict[str, object], _DELIVERY.check_publish_gate(
        pr_payload, remote_payload, approval_payload, candidate_root,
    ))


# Private aliases retained for existing in-package v1 evaluation adapters.
_artifact_json = _BUNDLE._artifact_json
_reference = _BUNDLE._reference
_artifact_bytes = _BUNDLE._artifact_bytes
_diagram_claim_inputs = _BUNDLE._diagram_claim_inputs
