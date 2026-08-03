from __future__ import annotations

from pathlib import Path
from typing import Any

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

def evaluate_generated_bundle(
    payload: Any,
    artifact_root: Path,
    *,
    observation: dict[str, object] | None = None,
    trusted_observation_sha256s: frozenset[str] = frozenset(),
) -> dict[str, object]:
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
