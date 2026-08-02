from __future__ import annotations

from typing import Any

from ...pipeline_contracts import ContractError


ADVISORY_METRIC_NAMES = (
    "claim_coverage",
    "diagram_label_coverage",
    "evidence_sources",
    "language_truth_pairs",
    "observable_commands",
    "section_intents",
    "visual_provenance",
)
MAX_METRIC_COUNT = (1 << 63) - 1
_BASE_FIELDS = {"covered", "total", "status", "reasons"}
_MEASURED_FIELDS = _BASE_FIELDS | {"basis_points"}


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_float(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_float(child, f"{path}.{key}")


def validate_metric(payload: Any, name: str) -> dict[str, object]:
    _reject_float(payload, f"advisory.{name}")
    if not isinstance(payload, dict):
        raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} must be an object")
    status = payload.get("status")
    expected_fields = _MEASURED_FIELDS if status == "measured" else _BASE_FIELDS
    if set(payload) != expected_fields:
        raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} fields do not match status")
    covered, total = payload["covered"], payload["total"]
    if (
        type(covered) is not int
        or type(total) is not int
        or covered < 0
        or total < 0
        or covered > MAX_METRIC_COUNT
        or total > MAX_METRIC_COUNT
        or covered > total
    ):
        raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} counts are invalid")
    reasons = payload["reasons"]
    if (
        not isinstance(reasons, list)
        or any(not isinstance(reason, str) or not reason or "\x00" in reason for reason in reasons)
        or reasons != sorted(set(reasons))
    ):
        raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} reasons are invalid")
    if total == 0:
        if status != "not-applicable" or covered != 0 or reasons:
            raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} zero total must be not-applicable")
    else:
        if status != "measured":
            raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} nonzero total must be measured")
        basis_points = payload["basis_points"]
        if (
            type(basis_points) is not int
            or basis_points < 0
            or basis_points > 10_000
            or basis_points != covered * 10_000 // total
            or (covered < total and not reasons)
            or (covered == total and reasons)
        ):
            raise ContractError("E_EVALUATION_METRIC", f"evaluation metric {name} measurement is inconsistent")
    return dict(payload)


def validate_advisory_metrics(payload: Any) -> dict[str, dict[str, object]]:
    _reject_float(payload, "advisory")
    if not isinstance(payload, dict) or set(payload) != set(ADVISORY_METRIC_NAMES):
        raise ContractError("E_EVALUATION_METRIC", "evaluation advisory metric names are invalid")
    return {
        name: validate_metric(payload[name], name)
        for name in ADVISORY_METRIC_NAMES
    }


def metric(covered: int, total: int, reasons: list[str]) -> dict[str, object]:
    normalized_reasons = sorted(set(reasons))
    if total == 0:
        value: dict[str, object] = {
            "covered": covered,
            "reasons": normalized_reasons,
            "status": "not-applicable",
            "total": total,
        }
    else:
        value = {
            "basis_points": covered * 10_000 // total,
            "covered": covered,
            "reasons": normalized_reasons,
            "status": "measured",
            "total": total,
        }
    return validate_metric(value, "computed")
