"""Bounded post-evidence feedback adjustment for retrieval results."""

from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from typing import Any

from ...pipeline_contracts import ContractError
from ..evaluation.feedback_metrics import METRIC_NAMES


MAX_ADVISORY_BPS = 250


def _metric_rate(metric: object, name: str) -> int | None:
    if not isinstance(metric, Mapping) or metric.get("status") != "ok":
        return None
    numerator, denominator, rate = metric.get("numerator"), metric.get("denominator"), metric.get("rate_bps")
    if (
        type(numerator) is not int or type(denominator) is not int or type(rate) is not int
        or denominator < 1 or numerator < 0 or numerator > denominator or not 0 <= rate <= 10_000
    ):
        raise ContractError("E_FEEDBACK_METRIC", f"{name} is not an exact bounded metric")
    expected = (2 * numerator * 10_000 + denominator) // (2 * denominator)
    if rate != expected:
        raise ContractError("E_FEEDBACK_METRIC", f"{name} rate does not match its count pair")
    return rate


def advisory_delta(metrics: Mapping[str, object]) -> int | None:
    """Return the fixed capped aggregate delta, or ``None`` without signal."""
    rates: list[int] = []
    for name in METRIC_NAMES:
        rate = _metric_rate(metrics.get(name), name)
        if rate is not None:
            # Less manual editing is a positive outcome; all other rates are direct.
            rates.append(10_000 - rate if name == "manual_edit_distance" else rate)
    if not rates:
        return None
    centered = sum(rates) // len(rates) - 5_000
    return max(-MAX_ADVISORY_BPS, min(MAX_ADVISORY_BPS, centered // 20))


def _eligible_for_adjustment(result: Mapping[str, Any]) -> bool:
    """Never turn a test or explicitly ineligible record into a feedback target."""
    if result.get("source_split") != "train":
        return False
    return result.get("eligible") is not False and result.get("safety_eligible") is not False


def apply_feedback_signal(
    base_results: Sequence[Mapping[str, Any]],
    metrics: Mapping[str, object],
) -> Sequence[Mapping[str, Any]]:
    """Apply a fixed advisory delta after evidence ranking.

    No valid aggregate signal returns the original sequence unchanged, preserving
    the base rank object's byte representation and tie order exactly.
    """
    summary = metrics
    global_metrics = metrics.get("metrics", metrics) if isinstance(metrics, Mapping) else metrics
    delta = advisory_delta(global_metrics)
    if delta is None:
        return base_results
    adjusted: list[dict[str, Any]] = []
    for base in base_results:
        if not isinstance(base, Mapping) or type(base.get("score_basis_points")) is not int:
            raise ContractError("E_FEEDBACK_RANK", "base retrieval result has no integer evidence score")
        if base.get("source_split") != "train":
            continue
        result = copy.deepcopy(dict(base))
        record_metric = summary.get("record_metrics", {}).get(base["record_id"]) if isinstance(summary, Mapping) else None
        record_rate = _metric_rate(record_metric, base["record_id"]) if record_metric is not None else None
        record_delta = 0 if record_rate is None else max(-MAX_ADVISORY_BPS, min(MAX_ADVISORY_BPS, (record_rate - 5_000) // 20))
        applied = max(-MAX_ADVISORY_BPS, min(MAX_ADVISORY_BPS, delta + record_delta)) if _eligible_for_adjustment(base) else 0
        result["feedback_advisory_basis_points"] = applied
        result["adjusted_score_basis_points"] = result["score_basis_points"] + applied
        adjusted.append(result)
    adjusted.sort(key=lambda result: (-result["adjusted_score_basis_points"], result["record_id"]))
    return adjusted


__all__ = ["MAX_ADVISORY_BPS", "advisory_delta", "apply_feedback_signal"]
