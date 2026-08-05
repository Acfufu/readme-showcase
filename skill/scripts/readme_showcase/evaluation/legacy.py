from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ...pipeline_contracts import canonical_json_bytes
from ..contracts.claims import validate_claim_map
from ..contracts.evaluation import validate_evaluation_report_v3
from ..contracts.evidence import validate_evidence_graph
from ..contracts.plan import validate_readme_plan
from ..evaluation.contract import metric
from ..visual_kernel.gates import validate_visual_gate_report
from ..visual_kernel.model import validate_visual_spec
from ..visual_kernel.reader import load_compiled_visual
from ..validation import legacy as _BUNDLE

ContractError = _BUNDLE.ContractError
canonical_sha256 = _BUNDLE.canonical_sha256
validate_contract = _BUNDLE.validate_contract
cast = _BUNDLE.cast
_EVALUATION = _BUNDLE._EVALUATION
_BEHAVIOR = _BUNDLE._BEHAVIOR
_EVALUATION_REPORT = _BUNDLE._EVALUATION_REPORT
_EVALUATION_REPORT_FIELDS = _BUNDLE._EVALUATION_REPORT_FIELDS
_ADVISORY_METRICS = _BUNDLE._ADVISORY_METRICS
_fail = _BUNDLE._fail
_object = _BUNDLE._object
_reference = _BUNDLE._reference
_artifact_json = _BUNDLE._artifact_json
validate_generated_bundle = _BUNDLE.validate_generated_bundle


_V3_METRIC_NAMES = (
    "gate_pass",
    "element_evidence_coverage",
    "variant_completeness",
    "determinism",
    "resource_budgets",
)
_V3_DECISION_BASIS = "hard-compiled-gates-with-configured-behavior"
_V3_VARIANTS = frozenset({"desktop", "mobile"})


def _v3_digest(value: Any) -> str:
    return value if (
        isinstance(value, str)
        and len(value) == 64
        and all(char in "0123456789abcdef" for char in value)
    ) else "0" * 64


def _v3_behavior_not_observed(
    commands: list[str],
) -> dict[str, object]:
    return cast(dict[str, object], _BEHAVIOR.evaluate_behavior(
        commands,
        [],
        base_sha="0" * 40,
        input_hashes={},
    ))


def _v3_empty_compiled_metrics(code: str) -> dict[str, dict[str, object]]:
    reason = code if code else "E_EVALUATION_METRIC"
    return {
        name: metric(0, 1, [reason])
        for name in _V3_METRIC_NAMES
    }


def _v3_report(
    *,
    payload: Any,
    compiled_fingerprint: str,
    compiled: Mapping[str, Mapping[str, object]],
    findings: list[dict[str, str]],
    advisory: Mapping[str, Any],
    behavior: Mapping[str, Any],
    behavior_required: bool,
) -> dict[str, object]:
    ordered_findings = sorted(
        (dict(item) for item in findings),
        key=lambda item: (item.get("code", ""), item.get("message", "")),
    )
    hard_status = "pass" if not ordered_findings else "fail"
    report = {
        "schema_version": 3,
        "status": "pass" if hard_status == "pass" and all(
            value.get("basis_points") == 10_000 for value in compiled.values()
        ) and (not behavior_required or behavior.get("status") == "pass") else "fail",
        "decision_basis": _V3_DECISION_BASIS,
        "bundle_sha256": canonical_sha256(payload),
        "compiled_fingerprint": _v3_digest(compiled_fingerprint),
        "hard_gate": {"status": hard_status, "findings": ordered_findings},
        "compiled": {name: dict(compiled[name]) for name in _V3_METRIC_NAMES},
        "advisory": dict(advisory),
        "behavior": dict(behavior),
        "behavior_required": behavior_required,
    }
    return validate_evaluation_report_v3(report)


def _v3_json(raw: bytes, context: str) -> dict[str, Any]:
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("E_VISUAL_DETERMINISM", f"{context} must be a JSON object") from exc
    if not isinstance(value, dict):
        raise ContractError("E_VISUAL_DETERMINISM", f"{context} must be a JSON object")
    if canonical_json_bytes(value) != raw:
        raise ContractError("E_VISUAL_DETERMINISM", f"{context} must use canonical JSON bytes")
    return value


def _v3_variant_keys(spec: Any) -> set[tuple[str, str]]:
    variants = getattr(spec, "variants", ())
    locale = getattr(spec, "locale", None)
    if not isinstance(locale, str) or not isinstance(variants, tuple) or not variants:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled Visual Spec has no declared variants")
    if any(item not in _V3_VARIANTS for item in variants):
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled Visual Spec declares an unsupported variant")
    return {(locale, variant) for variant in variants}


def _v3_variant_path(kind: str, locale: str, variant: str) -> str:
    directory = {
        "scene": "scenes",
        "gate": "gates",
        "timeline": "timeline",
        "interaction": "interaction",
        "svg": None,
    }[kind]
    if directory is None:
        return f"assets/readme-showcase/{locale}/{variant}.svg"
    return f"compiled/{directory}/{locale}/{variant}.json"


def _v3_element_metric(
    claims: Mapping[str, Any],
    spec: Any,
    evidence: Mapping[str, Any],
) -> dict[str, object]:
    expected: dict[str, tuple[str, ...]] = {}
    for collection in (spec.nodes, spec.edges, spec.groups, spec.lanes):
        for element in collection:
            if element.label is not None:
                expected[element.id] = tuple(element.evidence_ids)
    known_ids = {fact["fact_id"] for fact in evidence.get("facts", []) if isinstance(fact, Mapping)}
    by_element = {
        item.get("element_id"): item
        for item in claims.get("diagram_labels", [])
        if isinstance(item, Mapping) and isinstance(item.get("element_id"), str)
    }
    covered = 0
    reasons: list[str] = []
    for identifier in sorted(expected, key=lambda value: value.encode("utf-8")):
        claim = by_element.get(identifier)
        if (
            isinstance(claim, Mapping)
            and claim.get("claim_kind") != "decorative"
            and tuple(claim.get("evidence_ids", ())) == expected[identifier]
            and set(expected[identifier]).issubset(known_ids)
        ):
            covered += 1
        else:
            reasons.append(f"element-evidence-uncovered:{identifier}")
    # A spec with no labeled element is vacuously covered, but still has a
    # concrete denominator so the hard measure cannot become N/A.
    if not expected:
        return metric(1, 1, [])
    return metric(covered, len(expected), reasons)


def _v3_compiled_metrics(
    loaded: Any,
    bundle: Mapping[str, Any],
    spec: Any,
    claims: Mapping[str, Any],
    evidence: Mapping[str, Any],
) -> tuple[dict[str, dict[str, object]], list[dict[str, str]]]:
    files = loaded.artifacts
    expected_keys = _v3_variant_keys(spec)
    findings: list[dict[str, str]] = []
    gate_covered = 0
    gate_failed_keys: set[tuple[str, str]] = set()
    variant_covered = 0
    variant_incomplete_keys: set[tuple[str, str]] = set()
    for locale, variant in sorted(expected_keys, key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8"))):
        paths = {
            name: _v3_variant_path(name, locale, variant)
            for name in ("scene", "gate", "timeline", "interaction", "svg")
        }
        missing = [name for name, path in paths.items() if path not in files]
        if missing:
            message = f"compiled variant {locale}/{variant} is missing {missing[0]}"
            findings.append({"code": "E_VISUAL_FINGERPRINT", "message": message})
            gate_failed_keys.add((locale, variant))
            variant_incomplete_keys.add((locale, variant))
            continue
        try:
            gate_raw = _v3_json(files[paths["gate"]], f"{paths['gate']}")
            gate = validate_visual_gate_report(gate_raw)
            if gate.status == "pass":
                gate_covered += 1
            else:
                gate_failed_keys.add((locale, variant))
                for diagnostic in gate.diagnostics:
                    if diagnostic.severity == "error":
                        findings.append({"code": diagnostic.code, "message": diagnostic.message})
                if not gate.diagnostics:
                    findings.append({"code": "E_VISUAL_DETERMINISM", "message": f"gate failed for {locale}/{variant}"})
        except (KeyError, TypeError, ValueError, ContractError) as exc:
            gate_failed_keys.add((locale, variant))
            variant_incomplete_keys.add((locale, variant))
            code = getattr(exc, "code", "E_VISUAL_DETERMINISM")
            message = str(exc)
            findings.append({"code": code, "message": message})
            continue
        variant_covered += 1

    gate_metric = metric(
        gate_covered,
        len(expected_keys),
        [f"gate-failed:{locale}/{variant}" for locale, variant in sorted(gate_failed_keys)],
    )
    variant_metric = metric(
        variant_covered,
        len(expected_keys),
        [f"variant-incomplete:{locale}/{variant}" for locale, variant in sorted(variant_incomplete_keys)],
    )
    try:
        claim_metric = _v3_element_metric(claims, spec, evidence)
    except (KeyError, TypeError, ContractError) as exc:
        claim_metric = metric(0, 1, [getattr(exc, "code", "E_CLAIM_COVERAGE")])
        findings.append({"code": getattr(exc, "code", "E_CLAIM_COVERAGE"), "message": str(exc)})

    inventory_identity = bundle.get("compiled", {}).get("fingerprint") if isinstance(bundle.get("compiled"), Mapping) else None
    inventory_ok = loaded.inventory_sha256 == inventory_identity
    if not inventory_ok:
        findings.append({"code": "E_VISUAL_FINGERPRINT", "message": "bundle compiled fingerprint differs from loaded inventory"})
    deterministic_metric = metric(1 if inventory_ok else 0, 1, [] if inventory_ok else ["E_VISUAL_FINGERPRINT"])
    resource_metric = metric(1, 1, [])

    return (
        {
            "gate_pass": gate_metric,
            "element_evidence_coverage": claim_metric,
            "variant_completeness": variant_metric,
            "determinism": deterministic_metric,
            "resource_budgets": resource_metric,
        },
        findings,
    )


def _evaluate_v3(
    payload: Mapping[str, Any],
    artifact_root: Path,
    *,
    observation: Mapping[str, object] | None = None,
    trusted_observation_sha256s: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Evaluate one Generated Bundle v3 through the compiled reader boundary."""

    compiled_value = payload.get("compiled")
    compiled_fingerprint = (
        compiled_value.get("fingerprint")
        if isinstance(compiled_value, Mapping)
        else "0" * 64
    )
    behavior: dict[str, object] = _v3_behavior_not_observed([])
    behavior_required = observation is not None

    try:
        validate_generated_bundle(payload, artifact_root)
        loaded = load_compiled_visual(artifact_root, payload)
        artifacts = cast(dict[str, Any], payload["artifacts"])
        mode = cast(str, payload["mode"])
        plan_raw, _ = _artifact_json(artifact_root, artifacts["plan"], "bundle artifacts.plan")
        plan = validate_readme_plan(plan_raw, mode=mode)
        if plan["schema_version"] != 3 or plan["diagram_route"] != "compiled":
            raise ContractError("E_BUNDLE_PLAN", "compiled bundle requires README Plan v3")
        retrieval, _ = _artifact_json(artifact_root, artifacts["retrieval"], "bundle artifacts.retrieval")
        evidence, _ = _artifact_json(artifact_root, artifacts["evidence"], "bundle artifacts.evidence")
        evidence = validate_evidence_graph(evidence)
        spec_payload, _ = _artifact_json(artifact_root, artifacts["visual_spec"], "bundle artifacts.visual_spec")
        spec = validate_visual_spec(spec_payload, evidence_graph=evidence)
        claims, _ = _artifact_json(artifact_root, artifacts["claim_map"], "bundle artifacts.claim_map")
        claims = validate_claim_map(claims, evidence_graph=evidence, visual_spec=spec_payload)
        manifest, _ = _artifact_json(artifact_root, artifacts["asset_manifest"], "bundle artifacts.asset_manifest")

        compiled_metrics, findings = _v3_compiled_metrics(
            loaded,
            payload,
            spec,
            claims,
            evidence,
        )

        advisory = _EVALUATION.compute_advisory_metrics(
            plan=plan,
            retrieval=retrieval,
            evidence=evidence,
            claims=claims,
            asset_manifest=manifest,
        )
        input_hashes = {
            name: _reference(reference, f"bundle artifacts.{name}")["sha256"]
            for name, reference in sorted(artifacts.items())
        }
        behavior = cast(dict[str, object], _BEHAVIOR.evaluate_behavior(
            cast(list[str], plan["commands"]),
            [] if observation is None else [dict(observation)],
            base_sha=cast(str, payload["target"]["base_sha"]),
            input_hashes=input_hashes,
            trusted_observation_sha256s=trusted_observation_sha256s,
        ))
        advisory["observable_commands"] = metric(
            int(behavior["observable_commands"]),
            int(behavior["total_commands"]),
            list(behavior["reasons"]),
        )

        return _v3_report(
            payload=payload,
            compiled_fingerprint=loaded.inventory_sha256,
            compiled=compiled_metrics,
            findings=findings,
            advisory=advisory,
            behavior=behavior,
            behavior_required=behavior_required,
        )
    except ContractError as exc:
        # Keep the evaluator's established fail-closed boundary: malformed or
        # stale input becomes a deterministic report, while observation
        # binding errors remain caller errors as in v2.
        if observation is not None and exc.code == "E_OBSERVATION_BINDING":
            raise
        return _v3_report(
            payload=payload,
            compiled_fingerprint=cast(str, compiled_fingerprint),
            compiled=_v3_empty_compiled_metrics(exc.code),
            findings=[{"code": exc.code, "message": str(exc)}],
            advisory=_EVALUATION.empty_advisory_metrics(),
            behavior=behavior,
            behavior_required=behavior_required,
        )


def evaluate_generated_bundle(
    payload: Any,
    artifact_root: Path,
    *,
    observation: dict[str, object] | None = None,
    trusted_observation_sha256s: frozenset[str] = frozenset(),
) -> dict[str, object]:
    if isinstance(payload, dict) and payload.get("schema_version") == 3:
        return _evaluate_v3(
            payload,
            artifact_root,
            observation=observation,
            trusted_observation_sha256s=trusted_observation_sha256s,
        )
    if observation is not None:
        if not isinstance(payload, dict) or payload.get("schema_version") != 2:
            raise ContractError("E_OBSERVATION_BINDING", "command observations require a v2 bundle")
        validate_generated_bundle(payload, artifact_root)
        artifacts = cast(dict[str, Any], payload["artifacts"])
        plan, _ = _artifact_json(artifact_root, artifacts["plan"], "bundle artifacts.plan")
        target = cast(dict[str, Any], payload["target"])
        input_hashes = {
            name: _reference(reference, f"bundle artifacts.{name}")["sha256"]
            for name, reference in sorted(artifacts.items())
        }
        behavior = _BEHAVIOR.evaluate_behavior(
            cast(list[str], plan["commands"]), [observation],
            base_sha=cast(str, target["base_sha"]), input_hashes=input_hashes,
            trusted_observation_sha256s=trusted_observation_sha256s,
        )
        return cast(dict[str, object], _EVALUATION_REPORT.build_evaluation_report_v2(
            bundle_sha256=canonical_sha256(payload),
            hard_gate={"status": "pass", "findings": []},
            advisory=_EVALUATION.evaluate_v2_advisory(payload, artifact_root),
            behavior=behavior,
            behavior_required=True,
        ))
    if not isinstance(payload, dict) or payload.get("schema_version") != 2:
        return cast(dict[str, object], _EVALUATION.evaluate_v1_legacy(payload, artifact_root))
    bundle_sha256 = canonical_sha256(payload)
    try:
        validate_generated_bundle(payload, artifact_root)
    except ContractError as exc:
        return {
            "schema_version": 1,
            "status": "fail",
            "decision_basis": "hard-gates-only",
            "bundle_sha256": bundle_sha256,
            "hard_gate": {
                "status": "fail",
                "findings": [{"code": exc.code, "message": str(exc)}],
            },
            "advisory": _EVALUATION.empty_advisory_metrics(),
        }
    return {
        "schema_version": 1,
        "status": "pass",
        "decision_basis": "hard-gates-only",
        "bundle_sha256": bundle_sha256,
        "hard_gate": {"status": "pass", "findings": []},
        "advisory": _EVALUATION.evaluate_v2_advisory(payload, artifact_root),
    }


def _validate_evaluation_report(
    payload: Any,
    *,
    bundle_sha256: str,
    bundle_schema_version: int,
    expected_advisory: dict[str, dict[str, object]] | None = None,
) -> None:
    report = validate_contract(
        payload,
        required=_EVALUATION_REPORT_FIELDS,
        optional=set(),
        context="evaluation report",
    )
    if (
        report["status"] != "pass"
        or report["decision_basis"] != "hard-gates-only"
        or report["bundle_sha256"] != bundle_sha256
    ):
        _fail("E_PR_EVALUATION", "evaluation does not pass for this exact bundle")
    hard_gate = _object(
        report["hard_gate"],
        {"status", "findings"},
        "evaluation report.hard_gate",
    )
    if hard_gate["status"] != "pass" or hard_gate["findings"] != []:
        _fail("E_PR_EVALUATION", "evaluation hard gate must pass without findings")
    advisory = _object(
        report["advisory"],
        set(_ADVISORY_METRICS),
        "evaluation report.advisory",
    )
    if bundle_schema_version == 2:
        validated_advisory = _EVALUATION.validate_advisory_metrics(advisory)
        if validated_advisory != expected_advisory:
            _fail(
                "E_PR_EVALUATION",
                "evaluation advisory metrics differ from bundle evidence",
            )
        return
    for name in _ADVISORY_METRICS:
        pair = _object(
            advisory[name],
            {"covered", "total"},
            f"evaluation report.advisory.{name}",
        )
        covered = pair["covered"]
        total = pair["total"]
        if (
            type(covered) is not int
            or type(total) is not int
            or covered < 0
            or total < 0
            or covered > total
        ):
            _fail("E_PR_EVALUATION", f"evaluation metric {name} is invalid")
