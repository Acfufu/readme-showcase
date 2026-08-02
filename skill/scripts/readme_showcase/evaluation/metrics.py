from __future__ import annotations

import hashlib
import importlib
import json
from pathlib import Path
from typing import Any, Mapping, Sequence, cast

from ...pipeline_contracts import (
    ContractError,
    MAX_JSON_BYTES,
    canonical_json_bytes,
    read_regular_bytes,
)
from ..contracts.assets import validate_asset_manifest
from ..contracts.claims import validate_claim_map
from ..contracts.evidence import validate_evidence_graph
from ..contracts.plan import validate_readme_plan_v2
from .contract import ADVISORY_METRIC_NAMES, metric, validate_advisory_metrics


def _identifier(value: Any, context: str) -> str:
    if not isinstance(value, str) or ":" not in value or len(value) > 512:
        raise ContractError("E_EVALUATION_METRIC", f"{context} is not a normative evidence ID")
    prefix, digest = value.split(":", 1)
    if not prefix.isalpha() or not prefix.islower() or len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ContractError("E_EVALUATION_METRIC", f"{context} is not a normative evidence ID")
    return value


def _unique_ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list):
        raise ContractError("E_EVALUATION_METRIC", f"{context} must be an evidence ID list")
    identifiers = [_identifier(item, f"{context}[]") for item in value]
    if len(identifiers) != len(set(identifiers)):
        raise ContractError("E_EVALUATION_METRIC", f"{context} contains duplicate evidence IDs")
    return identifiers


def _claims(claims: Mapping[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for collection in ("markdown_blocks", "diagram_labels"):
        values = claims.get(collection)
        if not isinstance(values, list):
            raise ContractError("E_EVALUATION_METRIC", f"claim map {collection} must be a list")
        for value in values:
            if not isinstance(value, dict):
                raise ContractError("E_EVALUATION_METRIC", f"claim map {collection} contains a non-object")
            result.append(value)
    return result


def _claim_verified(claim: Mapping[str, Any], known_ids: set[str]) -> bool:
    identifiers = _unique_ids(claim.get("evidence_ids"), f"claim {claim.get('claim_id', '<unknown>')}.evidence_ids")
    if not set(identifiers).issubset(known_ids):
        raise ContractError("E_EVALUATION_METRIC", f"claim {claim.get('claim_id', '<unknown>')} references dangling evidence")
    support = claim.get("support_level")
    return support in {"direct", "composed"} and (support != "composed" or len(identifiers) >= 2)


def _asset_claims(asset_id: str, diagram_claims: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        claim
        for claim in diagram_claims
        if isinstance(claim.get("claim_id"), str)
        and claim["claim_id"].rsplit(":", 1)[-1] == asset_id
    ]


def compute_advisory_metrics(
    *,
    plan: Mapping[str, Any],
    retrieval: Mapping[str, Any],
    evidence: Mapping[str, Any],
    claims: Mapping[str, Any],
    asset_manifest: Mapping[str, Any],
) -> dict[str, dict[str, object]]:
    facts = evidence.get("facts")
    if not isinstance(facts, list):
        raise ContractError("E_EVALUATION_METRIC", "evidence facts must be a list")
    fact_ids: list[str] = []
    facts_by_id: dict[str, Mapping[str, Any]] = {}
    for fact in facts:
        if not isinstance(fact, Mapping):
            raise ContractError("E_EVALUATION_METRIC", "evidence facts must contain objects")
        identifier = _identifier(fact.get("fact_id"), "evidence fact_id")
        fact_ids.append(identifier)
        facts_by_id[identifier] = fact
    if len(fact_ids) != len(set(fact_ids)):
        raise ContractError("E_EVALUATION_METRIC", "evidence contains duplicate fact IDs")
    known_ids = set(fact_ids)

    planned_evidence = _unique_ids(plan.get("evidence_ids"), "README plan.evidence_ids")
    if not set(planned_evidence).issubset(known_ids):
        raise ContractError("E_EVALUATION_METRIC", "README plan references dangling evidence")
    claim_entries = _claims(claims)
    diagram_claims = list(claims.get("diagram_labels", []))
    claim_verified = {
        str(claim.get("claim_id")): _claim_verified(claim, known_ids)
        for claim in claim_entries
    }

    factual_claims = [claim for claim in claim_entries if claim.get("claim_kind") == "factual"]
    uncovered_claims = [
        str(claim.get("claim_id"))
        for claim in factual_claims
        if not claim_verified[str(claim.get("claim_id"))]
    ]
    visible_diagram_claims = [claim for claim in diagram_claims if claim.get("claim_kind") != "decorative"]
    uncovered_labels = [
        str(claim.get("claim_id"))
        for claim in visible_diagram_claims
        if not claim_verified[str(claim.get("claim_id"))]
    ]

    source_claims = [claim for claim in claim_entries if claim.get("claim_kind") != "decorative"]
    claim_used_ids = {
        identifier
        for claim in source_claims
        for identifier in _unique_ids(claim.get("evidence_ids"), f"claim {claim.get('claim_id')}.evidence_ids")
    }
    covered_sources = set(planned_evidence).intersection(claim_used_ids)
    unused_sources = sorted(set(planned_evidence) - covered_sources)

    languages = plan.get("languages")
    if not isinstance(languages, list) or any(not isinstance(value, str) for value in languages):
        raise ContractError("E_EVALUATION_METRIC", "README plan.languages must be a string list")
    pair_claims: dict[str, list[dict[str, Any]]] = {}
    for claim in claim_entries:
        pair_id = claim.get("language_pair_id")
        if isinstance(pair_id, str):
            pair_claims.setdefault(pair_id, []).append(claim)
    complete_pairs = {
        pair_id
        for pair_id, values in pair_claims.items()
        if len(values) == len(languages) and all(claim_verified[str(value.get("claim_id"))] for value in values)
    }

    commands = plan.get("commands")
    if not isinstance(commands, list) or any(not isinstance(value, str) for value in commands) or len(commands) != len(set(commands)):
        raise ContractError("E_EVALUATION_METRIC", "README plan.commands must be a unique string list")

    sections = plan.get("sections")
    if not isinstance(sections, list) or any(not isinstance(value, str) for value in sections) or len(sections) != len(set(sections)):
        raise ContractError("E_EVALUATION_METRIC", "README plan.sections must be a unique string list")
    retrieved_sections: set[str] = set()
    records = retrieval.get("records", [])
    if isinstance(records, list):
        for record in records:
            if isinstance(record, Mapping) and isinstance(record.get("section_intents"), list):
                retrieved_sections.update(value for value in record["section_intents"] if isinstance(value, str))
    missing_sections = sorted(set(sections) - retrieved_sections)

    raw_assets = asset_manifest.get("assets")
    if not isinstance(raw_assets, list):
        raise ContractError("E_EVALUATION_METRIC", "asset manifest.assets must be a list")
    nondecorative_assets: list[Mapping[str, Any]] = []
    covered_assets: set[str] = set()
    visual_reasons: list[str] = []
    for asset in raw_assets:
        if not isinstance(asset, Mapping) or not isinstance(asset.get("asset_id"), str):
            raise ContractError("E_EVALUATION_METRIC", "asset manifest contains an invalid asset")
        asset_id = asset["asset_id"]
        identifiers = _unique_ids(asset.get("evidence_ids"), f"asset {asset_id}.evidence_ids")
        if not set(identifiers).issubset(known_ids):
            raise ContractError("E_EVALUATION_METRIC", f"asset {asset_id} references dangling evidence")
        bindings = _asset_claims(asset_id, diagram_claims)
        if bindings and all(binding.get("claim_kind") == "decorative" for binding in bindings):
            continue
        nondecorative_assets.append(asset)
        provenance = asset.get("provenance")
        provenance_bound = False
        if isinstance(provenance, Mapping):
            provenance_bound = any(
                isinstance(facts_by_id.get(identifier), Mapping)
                and isinstance(facts_by_id[identifier].get("source"), Mapping)
                and facts_by_id[identifier]["source"].get("path") == provenance.get("path")
                and facts_by_id[identifier].get("source_sha256") == provenance.get("sha256")
                for identifier in identifiers
            )
        claim_bound = any(
            claim_verified[str(binding.get("claim_id"))]
            and bool(set(identifiers).intersection(_unique_ids(binding.get("evidence_ids"), f"claim {binding.get('claim_id')}.evidence_ids")))
            for binding in bindings
            if binding.get("claim_kind") != "decorative"
        )
        if provenance_bound and claim_bound:
            covered_assets.add(asset_id)
        else:
            if not provenance_bound:
                visual_reasons.append(f"visual-provenance-unverified:{asset_id}")
            if not claim_bound:
                visual_reasons.append(f"visual-missing-claim-binding:{asset_id}")

    advisory = {
        "claim_coverage": metric(
            len(factual_claims) - len(uncovered_claims),
            len(factual_claims),
            [f"claim-unverified:{claim_id}" for claim_id in uncovered_claims],
        ),
        "diagram_label_coverage": metric(
            len(visible_diagram_claims) - len(uncovered_labels),
            len(visible_diagram_claims),
            [f"diagram-label-unverified:{claim_id}" for claim_id in uncovered_labels],
        ),
        "evidence_sources": metric(
            len(covered_sources),
            len(planned_evidence),
            [f"unused-evidence:{identifier}" for identifier in unused_sources],
        ),
        "language_truth_pairs": metric(
            len(complete_pairs),
            len(pair_claims),
            [f"language-pair-incomplete:{pair_id}" for pair_id in sorted(set(pair_claims) - complete_pairs)],
        ),
        "observable_commands": metric(
            0,
            len(commands),
            [f"command-not-observed:{command}" for command in sorted(commands)],
        ),
        "section_intents": metric(
            len(set(sections).intersection(retrieved_sections)),
            len(sections),
            [f"section-not-retrieved:{section}" for section in missing_sections],
        ),
        "visual_provenance": metric(
            len(covered_assets),
            len(nondecorative_assets),
            visual_reasons,
        ),
    }
    return validate_advisory_metrics(advisory)


def _read_json(root: Path, reference: Any, context: str) -> dict[str, Any]:
    if not isinstance(reference, Mapping) or set(reference) != {"path", "sha256"}:
        raise ContractError("E_EVALUATION_METRIC", f"{context} reference is invalid")
    path, digest = reference["path"], reference["sha256"]
    if not isinstance(path, str) or not isinstance(digest, str):
        raise ContractError("E_EVALUATION_METRIC", f"{context} reference is invalid")
    raw = read_regular_bytes(root / path, maximum=MAX_JSON_BYTES, path_code="E_PATH", size_code="E_INPUT_SIZE")
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ContractError("E_BUNDLE_HASH", f"{context} bytes differ from reference")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_INPUT_JSON", f"{context} must be canonical JSON") from exc
    if not isinstance(value, dict) or canonical_json_bytes(value) != raw:
        raise ContractError("E_BUNDLE_HASH", f"{context} must be canonical JSON")
    return value


def empty_advisory_metrics() -> dict[str, dict[str, object]]:
    return {name: metric(0, 0, []) for name in ADVISORY_METRIC_NAMES}


def _legacy_empty_advisory_metrics() -> dict[str, dict[str, int]]:
    return {name: {"covered": 0, "total": 0} for name in ADVISORY_METRIC_NAMES}


def evaluate_v1_legacy(payload: Any, artifact_root: Path) -> dict[str, object]:
    """Preserve byte-identical v1 report behavior behind pipeline_core wrapper."""
    core = importlib.import_module(
        "skill.scripts.pipeline_core"
        if __package__.startswith("skill.")
        else "pipeline_core"
    )
    bundle_sha256 = core.canonical_sha256(payload)
    try:
        core.validate_generated_bundle(payload, artifact_root)
    except core.ContractError as exc:
        return {
            "schema_version": 1,
            "status": "fail",
            "decision_basis": "hard-gates-only",
            "bundle_sha256": bundle_sha256,
            "hard_gate": {
                "status": "fail",
                "findings": [{"code": exc.code, "message": str(exc)}],
            },
            "advisory": _legacy_empty_advisory_metrics(),
        }
    bundle = cast(dict[str, Any], payload)
    candidate = cast(dict[str, Any], bundle["candidate"])
    artifacts = cast(dict[str, Any], bundle["artifacts"])
    plan, _ = core._artifact_json(artifact_root, artifacts["plan"], "bundle artifacts.plan")
    retrieval, _ = core._artifact_json(artifact_root, artifacts["retrieval"], "bundle artifacts.retrieval")
    claims, _ = core._artifact_json(artifact_root, artifacts["claim_map"], "bundle artifacts.claim_map")
    asset_manifest, _ = core._artifact_json(artifact_root, artifacts["asset_manifest"], "bundle artifacts.asset_manifest")

    readme_text = ""
    if candidate["readme"] is not None:
        readme_ref = core._reference(candidate["readme"], "bundle candidate.readme")
        readme_text = core._artifact_bytes(artifact_root, readme_ref, "bundle candidate.readme").decode("utf-8")

    markdown_claims = cast(list[dict[str, Any]], claims["markdown_blocks"])
    diagram_claims = cast(list[dict[str, Any]], claims["diagram_labels"])
    claim_entries = [*markdown_claims, *diagram_claims]
    assets = cast(list[dict[str, Any]], asset_manifest["assets"])
    expected_diagram_labels = core._diagram_claim_inputs(
        asset_manifest,
        root=artifact_root,
        default_language=cast(list[str], plan["languages"])[0],
    )
    planned_evidence = set(cast(list[str], plan["evidence_ids"]))
    used_evidence = {cast(str, claim["truth_id"]) for claim in claim_entries}
    for asset in assets:
        used_evidence.update(cast(list[str], asset["truth_ids"]))
    pair_counts: dict[str, int] = {}
    for claim in claim_entries:
        pair_id = claim["language_pair_id"]
        if isinstance(pair_id, str):
            pair_counts[pair_id] = pair_counts.get(pair_id, 0) + 1
    language_count = len(cast(list[str], plan["languages"]))
    retrieved_sections: set[str] = set()
    records = retrieval.get("records", [])
    if isinstance(records, list):
        for record in records:
            if isinstance(record, dict) and isinstance(record.get("section_intents"), list):
                retrieved_sections.update(item for item in record["section_intents"] if isinstance(item, str))
    planned_sections = set(cast(list[str], plan["sections"]))
    commands = cast(list[str], plan["commands"])
    advisory = {
        "claim_coverage": {
            "covered": len(claim_entries),
            "total": len(core.segment_markdown_blocks(readme_text)) + len(expected_diagram_labels),
        },
        "diagram_label_coverage": {"covered": len(diagram_claims), "total": len(expected_diagram_labels)},
        "evidence_sources": {"covered": len(planned_evidence.intersection(used_evidence)), "total": len(planned_evidence)},
        "language_truth_pairs": {
            "covered": sum(count == language_count for count in pair_counts.values()),
            "total": len(pair_counts),
        },
        "observable_commands": {"covered": sum(command in readme_text for command in commands), "total": len(commands)},
        "section_intents": {"covered": len(planned_sections.intersection(retrieved_sections)), "total": len(planned_sections)},
        "visual_provenance": {"covered": len(assets), "total": len(assets)},
    }
    return {
        "schema_version": 1,
        "status": "pass",
        "decision_basis": "hard-gates-only",
        "bundle_sha256": bundle_sha256,
        "hard_gate": {"status": "pass", "findings": []},
        "advisory": advisory,
    }


def evaluate_v2_advisory(payload: Mapping[str, Any], artifact_root: Path) -> dict[str, dict[str, object]]:
    artifacts = payload.get("artifacts")
    mode = payload.get("mode")
    if not isinstance(artifacts, Mapping) or not isinstance(mode, str):
        raise ContractError("E_EVALUATION_METRIC", "generated bundle evaluation inputs are invalid")
    plan = validate_readme_plan_v2(_read_json(artifact_root, artifacts.get("plan"), "plan"), mode=mode)
    retrieval = _read_json(artifact_root, artifacts.get("retrieval"), "retrieval")
    evidence = validate_evidence_graph(_read_json(artifact_root, artifacts.get("evidence"), "evidence"))
    claims = validate_claim_map(_read_json(artifact_root, artifacts.get("claim_map"), "claim map"), evidence_graph=evidence)
    asset_manifest = validate_asset_manifest(_read_json(artifact_root, artifacts.get("asset_manifest"), "asset manifest"), evidence_graph=evidence)
    return compute_advisory_metrics(
        plan=plan,
        retrieval=retrieval,
        evidence=evidence,
        claims=claims,
        asset_manifest=asset_manifest,
    )
