"""Pure, bounded aggregation for canonical local feedback events."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes
from ..contracts.feedback import validate_feedback_event


MIN_SAMPLES = 2
METRIC_NAMES = (
    "pattern_acceptance",
    "section_removal",
    "asset_rejection",
    "manual_edit_distance",
    "pr_merge",
)


def _reason(code: str) -> dict[str, str]:
    return {"code": code}


def _rate_bps(numerator: int, denominator: int) -> int:
    """Return an exact, integer, half-up basis-point rate."""
    return (2 * numerator * 10_000 + denominator) // (2 * denominator)


def _metric(numerator: int, denominator: int, samples: int) -> dict[str, object]:
    if denominator == 0:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "status": "not_applicable",
            "reasons": [_reason("zero_total")],
        }
    if samples < MIN_SAMPLES:
        return {
            "numerator": numerator,
            "denominator": denominator,
            "status": "not_applicable",
            "reasons": [_reason("insufficient_sample")],
        }
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate_bps": _rate_bps(numerator, denominator),
        "status": "ok",
        "reasons": [],
    }


def _binding_split(event: Mapping[str, Any], bindings: Mapping[tuple[str, str], Mapping[str, Any]]) -> str | None:
    binding = bindings.get((event["run_id"], event["fingerprint"]))
    if not isinstance(binding, Mapping):
        return None
    split = binding.get("split")
    return split if isinstance(split, str) else None


def _event_bytes(event: Mapping[str, Any]) -> bytes | None:
    try:
        return canonical_json_bytes(dict(event))
    except (ContractError, TypeError, ValueError):
        return None


def aggregate_feedback(
    events: Iterable[Mapping[str, Any]],
    *,
    bindings: Mapping[tuple[str, str], Mapping[str, Any]],
) -> dict[str, object]:
    """Aggregate only valid, bound, production feedback into count pairs.

    The binding map is deliberately explicit: ``(run_id, fingerprint)`` keys
    resolve to a local mapping containing ``split``.  Aggregation neither reads
    nor writes any workspace state.
    """
    ignored: Counter[str] = Counter()
    seen: dict[str, tuple[bytes, dict[str, Any]]] = {}
    accepted: dict[str, tuple[bytes, dict[str, Any]]] = {}
    for raw_event in events:
        if not isinstance(raw_event, Mapping):
            ignored["invalid_event"] += 1
            continue
        event = dict(raw_event)
        event_id = event.get("event_id")
        encoded = _event_bytes(event)
        if not isinstance(event_id, str) or encoded is None:
            ignored["invalid_event"] += 1
            continue
        previous = seen.get(event_id)
        if previous is not None:
            # Exact repeat bytes are deliberately silent: they are idempotent,
            # so a repeated JSONL line cannot alter aggregate output bytes.
            if previous[0] != encoded:
                ignored["duplicate_collision"] += 1
            continue
        try:
            validate_feedback_event(event)
        except (ContractError, TypeError, ValueError):
            ignored["invalid_event"] += 1
            continue
        seen[event_id] = (encoded, event)
        split = _binding_split(event, bindings)
        if split is None:
            ignored["unbound_event"] += 1
            continue
        if split == "test":
            ignored["test_split_event"] += 1
            continue
        if split != "train":
            ignored["unbound_event"] += 1
            continue
        accepted[event_id] = (encoded, event)

    pairs = {name: [0, 0] for name in METRIC_NAMES}
    samples = {name: set() for name in METRIC_NAMES}
    record_pairs: dict[str, list[int]] = {}
    record_samples: dict[str, set[str]] = {}
    for _, event in sorted(accepted.values(), key=lambda item: (item[0], item[1]["event_id"])):
        details = event["details"]
        accepted = set(details.get("accepted_ids", []))
        rejected = set(details.get("rejected_ids", []))
        if accepted & rejected:
            ignored["overlapping_feedback_ids"] += 1
            continue
        patterns = set(details.get("pattern_ids", []))
        sections = set(details.get("section_ids", []))
        assets = set(details.get("asset_ids", []))
        accepted_patterns, rejected_patterns = patterns & accepted, patterns & rejected
        if accepted_patterns or rejected_patterns:
            samples["pattern_acceptance"].add(event["event_id"])
        for record_id in accepted_patterns:
            record_pairs.setdefault(record_id, [0, 0])[0] += 1
            record_pairs[record_id][1] += 1
            record_samples.setdefault(record_id, set()).add(event["event_id"])
        for record_id in rejected_patterns:
            record_pairs.setdefault(record_id, [0, 0])[1] += 1
            record_samples.setdefault(record_id, set()).add(event["event_id"])
        pairs["pattern_acceptance"][0] += len(accepted_patterns)
        pairs["pattern_acceptance"][1] += len(accepted_patterns) + len(rejected_patterns)
        removed_sections, retained_sections = sections & rejected, sections & accepted
        if removed_sections or retained_sections:
            samples["section_removal"].add(event["event_id"])
        pairs["section_removal"][0] += len(removed_sections)
        pairs["section_removal"][1] += len(removed_sections) + len(retained_sections)
        if event["event"] == "asset-rejected":
            rejected_assets = assets & rejected
            pairs["asset_rejection"][0] += len(rejected_assets)
            pairs["asset_rejection"][1] += len(assets)
            if assets:
                samples["asset_rejection"].add(event["event_id"])
        distance = details.get("manual_edit_distance")
        if event["event"] == "candidate-edited" and distance is not None:
            pairs["manual_edit_distance"][0] += distance["changed"]
            pairs["manual_edit_distance"][1] += distance["total"]
            samples["manual_edit_distance"].add(event["event_id"])
        outcome = details.get("pr_outcome")
        if outcome in {"closed", "merged"}:
            pairs["pr_merge"][0] += int(outcome == "merged")
            pairs["pr_merge"][1] += 1
            samples["pr_merge"].add(event["event_id"])

    return {
        "metrics": {name: _metric(*pairs[name], len(samples[name])) for name in METRIC_NAMES},
        "record_metrics": {record_id: _metric(*pair, len(record_samples[record_id])) for record_id, pair in sorted(record_pairs.items())},
        "ignored": {code: ignored[code] for code in sorted(ignored)},
    }


__all__ = ["METRIC_NAMES", "MIN_SAMPLES", "aggregate_feedback"]
