from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping

from ...pipeline_contracts import (
    ContractError,
    MAX_JSON_BYTES,
    canonical_json_bytes,
    canonical_sha256,
    read_regular_bytes,
    write_canonical_json_atomic,
)
from ..contracts.assets import validate_asset_manifest
from ..contracts.claims import validate_claim_map
from ..contracts.common import normalize_posix_path
from ..contracts.evidence import validate_evidence_graph
from ..contracts.plan import validate_readme_plan_v2
from ..contracts.run import canonical_repository


GENERATED_BUNDLE_SCHEMA_VERSION = 2
MAX_CANDIDATES = 10_000
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BUNDLE_FIELDS = {"schema_version", "mode", "target", "candidate", "artifacts"}
_TARGET_FIELDS = {"repository", "base_sha"}
_CANDIDATE_FIELDS = {"readme", "assets", "candidate_sha256"}
_ARTIFACT_FIELDS = {"plan", "retrieval", "evidence", "claim_map", "asset_manifest", "evaluation"}
_REFERENCE_FIELDS = {"path", "sha256"}


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_float(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_float(child, f"{path}.{key}")


def _closed(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return value


def _sha(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError("E_BUNDLE_HASH", f"{context} must be lowercase SHA-256")
    return value


def _reference(value: Any, context: str) -> dict[str, str]:
    reference = _closed(value, _REFERENCE_FIELDS, context)
    try:
        path = normalize_posix_path(reference["path"])
    except ValueError as exc:
        raise ContractError("E_PATH", f"{context}.path must be safe relative POSIX path") from exc
    return {"path": path, "sha256": _sha(reference["sha256"], f"{context}.sha256")}


def _read_bytes(root: Path, reference: Mapping[str, str], context: str) -> bytes:
    try:
        raw = read_regular_bytes(
            root.joinpath(*Path(reference["path"]).parts),
            maximum=MAX_JSON_BYTES,
            path_code="E_PATH",
            size_code="E_INPUT_SIZE",
        )
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            raise ContractError("E_PATH", f"{context} is unavailable") from exc
        raise
    if hashlib.sha256(raw).hexdigest() != reference["sha256"]:
        raise ContractError("E_BUNDLE_HASH", f"{context} bytes differ from reference")
    return raw


def _read_json(root: Path, reference: Mapping[str, str], context: str) -> dict[str, Any]:
    raw = _read_bytes(root, reference, context)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_INPUT_JSON", f"{context} must be canonical UTF-8 JSON") from exc
    if not isinstance(payload, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must contain an object")
    if canonical_json_bytes(payload) != raw:
        raise ContractError("E_BUNDLE_HASH", f"{context} is not canonical JSON")
    return payload


def _candidate_projection(candidate: Mapping[str, Any]) -> dict[str, Any]:
    return {"readme": candidate["readme"], "assets": candidate["assets"]}


def validate_generated_bundle_v2(payload: Any, artifact_root: Path) -> dict[str, object]:
    _reject_float(payload)
    bundle = _closed(payload, _BUNDLE_FIELDS, "generated bundle")
    if type(bundle["schema_version"]) is not int or bundle["schema_version"] != GENERATED_BUNDLE_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle requires schema_version 2")
    mode = bundle["mode"]
    if mode not in {"readme", "asset-only", "audit-only"}:
        raise ContractError("E_BUNDLE_MODE", "generated bundle mode is unsupported")
    target = _closed(bundle["target"], _TARGET_FIELDS, "generated bundle.target")
    repository = canonical_repository(target["repository"])
    if target["repository"] != repository:
        raise ContractError("E_BUNDLE_TARGET", "generated bundle repository must be canonical owner/name")
    base_sha = target["base_sha"]
    if not isinstance(base_sha, str) or not _SHA1.fullmatch(base_sha):
        raise ContractError("E_BUNDLE_TARGET", "generated bundle base_sha must be immutable")

    raw_candidate = _closed(bundle["candidate"], _CANDIDATE_FIELDS, "generated bundle.candidate")
    readme = None if raw_candidate["readme"] is None else _reference(raw_candidate["readme"], "generated bundle.candidate.readme")
    raw_assets = raw_candidate["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_CANDIDATES:
        raise ContractError("E_SCHEMA_TYPE", "generated bundle.candidate.assets must be bounded array")
    assets = [_reference(value, f"generated bundle.candidate.assets[{index}]") for index, value in enumerate(raw_assets)]
    if assets != sorted(assets, key=lambda item: item["path"]) or len({item["path"] for item in assets}) != len(assets):
        raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate assets must be unique path order")
    candidate = {"readme": readme, "assets": assets}
    candidate_hash = _sha(raw_candidate["candidate_sha256"], "generated bundle.candidate.candidate_sha256")
    if canonical_sha256(candidate) != candidate_hash:
        raise ContractError("E_BUNDLE_HASH", "generated bundle candidate hash differs from references")

    raw_artifacts = _closed(bundle["artifacts"], _ARTIFACT_FIELDS, "generated bundle.artifacts")
    artifacts = {name: _reference(raw_artifacts[name], f"generated bundle.artifacts.{name}") for name in sorted(_ARTIFACT_FIELDS)}

    if mode == "readme" and readme is None:
        raise ContractError("E_BUNDLE_MODE", "readme mode requires candidate README")
    if mode != "readme" and readme is not None:
        raise ContractError("E_BUNDLE_MODE", f"{mode} mode cannot contain candidate README")
    if mode == "asset-only" and not assets:
        raise ContractError("E_BUNDLE_MODE", "asset-only mode requires candidate asset")
    if mode == "audit-only" and assets:
        raise ContractError("E_BUNDLE_MODE", "audit-only mode cannot contain candidate assets")

    if readme is not None:
        _read_bytes(artifact_root, readme, "generated bundle.candidate.readme")
    for index, reference in enumerate(assets):
        _read_bytes(artifact_root, reference, f"generated bundle.candidate.assets[{index}]")
    plan = validate_readme_plan_v2(_read_json(artifact_root, artifacts["plan"], "generated bundle.artifacts.plan"), mode=mode)
    retrieval = _read_json(artifact_root, artifacts["retrieval"], "generated bundle.artifacts.retrieval")
    if type(retrieval.get("schema_version")) is not int or retrieval["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "generated bundle retrieval artifact requires schema_version 1")
    evidence = validate_evidence_graph(_read_json(artifact_root, artifacts["evidence"], "generated bundle.artifacts.evidence"))
    graph_ids = {fact["fact_id"] for fact in evidence["facts"]}
    if not set(plan["evidence_ids"]).issubset(graph_ids):
        raise ContractError("E_CLAIM_EVIDENCE", "README plan references missing evidence")
    claims = validate_claim_map(
        _read_json(artifact_root, artifacts["claim_map"], "generated bundle.artifacts.claim_map"),
        evidence_graph=evidence,
    )
    claim_ids = {
        identifier
        for collection in (claims["markdown_blocks"], claims["diagram_labels"])
        for claim in collection
        for identifier in claim["evidence_ids"]
    }
    if not claim_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "claim map references evidence outside README plan")
    manifest = validate_asset_manifest(
        _read_json(artifact_root, artifacts["asset_manifest"], "generated bundle.artifacts.asset_manifest"),
        evidence_graph=evidence,
        artifact_root=artifact_root,
        candidate_assets=assets,
    )
    asset_ids = {identifier for asset in manifest["assets"] for identifier in asset["evidence_ids"]}
    if not asset_ids.issubset(set(plan["evidence_ids"])):
        raise ContractError("E_CLAIM_EVIDENCE", "asset manifest references evidence outside README plan")
    used_locales = {
        *(asset["locale"] for asset in manifest["assets"]),
        *(
            claim["claim_id"].split(":", 2)[1]
            for collection in (claims["markdown_blocks"], claims["diagram_labels"])
            for claim in collection
            if len(claim["claim_id"].split(":", 2)) == 3
            and claim["claim_id"].split(":", 2)[1] in {"en", "zh"}
        ),
    }
    if not used_locales.issubset(set(plan["languages"])):
        raise ContractError("E_CLAIM_LANGUAGE", "claim or asset locale differs from README plan")
    evaluation = _closed(
        _read_json(artifact_root, artifacts["evaluation"], "generated bundle.artifacts.evaluation"),
        {"schema_version", "status", "candidate_sha256"},
        "evaluation artifact",
    )
    if evaluation != {"schema_version": 2, "status": "pass", "candidate_sha256": candidate_hash}:
        raise ContractError("E_EVALUATION_DRIFT", "evaluation does not pass for exact candidate")
    return {
        "schema_version": 2,
        "status": "pass",
        "mode": mode,
        "bundle_sha256": canonical_sha256(bundle),
        "evidence_sha256": evidence["evidence_sha256"],
        "candidate_sha256": candidate_hash,
        "candidate_count": (1 if readme is not None else 0) + len(assets),
    }


def assemble_generated_bundle(
    artifact_root: Path,
    *,
    mode: str,
    target: Mapping[str, Any],
    candidate: Mapping[str, Any],
    artifacts: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_copy = copy.deepcopy(dict(candidate))
    candidate_copy["candidate_sha256"] = canonical_sha256(candidate_copy)
    bundle = {
        "schema_version": GENERATED_BUNDLE_SCHEMA_VERSION,
        "mode": mode,
        "target": {"repository": canonical_repository(target.get("repository")), "base_sha": target.get("base_sha")},
        "candidate": candidate_copy,
        "artifacts": copy.deepcopy(dict(artifacts)),
    }
    validate_generated_bundle_v2(bundle, artifact_root)
    return copy.deepcopy(bundle)


def write_generated_bundle_atomic(destination: Path, payload: Any, *, artifact_root: Path) -> None:
    validate_generated_bundle_v2(payload, artifact_root)
    write_canonical_json_atomic(destination, payload)
