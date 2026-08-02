from __future__ import annotations

import copy
from typing import Any, Mapping

from ...pipeline_contracts import ContractError
from ..contracts.evaluation import validate_behavior_result, validate_evaluation_report_v2
from .contract import metric, validate_advisory_metrics


def adapt_v1_evaluation_report(report: Mapping[str, Any]) -> dict[str, Any]:
    """Preserve legacy report shape and bytes; v2 is an additive contract."""
    if not isinstance(report, Mapping) or report.get("schema_version") != 1:
        raise ContractError("E_EVALUATION_REPORT", "v1 evaluation report is invalid")
    return copy.deepcopy(dict(report))


def build_evaluation_report_v2(
    *,
    bundle_sha256: str,
    hard_gate: Mapping[str, Any],
    advisory: Mapping[str, Any],
    behavior: Mapping[str, Any],
    behavior_required: bool,
) -> dict[str, Any]:
    normalized_behavior = validate_behavior_result(behavior)
    normalized_advisory = validate_advisory_metrics(advisory)
    command_metric = normalized_advisory["observable_commands"]
    if command_metric["total"] != normalized_behavior["total_commands"]:
        raise ContractError("E_OBSERVATION_BINDING", "behavior command count differs from evaluation plan")
    normalized_advisory["observable_commands"] = metric(
        int(normalized_behavior["observable_commands"]),
        int(normalized_behavior["total_commands"]),
        list(normalized_behavior["reasons"]),
    )
    hard_gate_copy = copy.deepcopy(dict(hard_gate))
    if isinstance(hard_gate_copy.get("findings"), list):
        hard_gate_copy["findings"] = sorted(
            hard_gate_copy["findings"],
            key=lambda item: (item.get("code", ""), item.get("message", "")) if isinstance(item, dict) else ("", ""),
        )
    hard_pass = hard_gate_copy.get("status") == "pass"
    behavior_pass = normalized_behavior["status"] == "pass"
    report = {
        "schema_version": 2,
        "status": "pass" if hard_pass and (not behavior_required or behavior_pass) else "fail",
        "decision_basis": "hard-gates-with-configured-behavior",
        "bundle_sha256": bundle_sha256,
        "hard_gate": hard_gate_copy,
        "advisory": normalized_advisory,
        "behavior": normalized_behavior,
        "behavior_required": behavior_required,
    }
    return validate_evaluation_report_v2(report)
