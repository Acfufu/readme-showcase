"""Independent candidate-claim self-review against repository evidence.

The self-review is the evaluation-side critic: candidate claims are checked
for a correspondence entry inside ``repository-evidence.json`` ``facts[]``,
never against the candidate README itself.  Comparing the product against
itself is a self-referential loop, and because critic and generator are the
same model, more than one auto-revision invites sycophantic loops.  A failed
self-review therefore allows exactly ``MAX_SELF_REVIEW_REVISIONS``
auto-revisions before the final verdict stands.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

MAX_SELF_REVIEW_REVISIONS = 1


def collect_evidence_facts(evidence: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Index repository-evidence ``facts[]`` by ``fact_id``.

    Entries that are not objects or carry no string ``fact_id`` are ignored;
    the review never fails on malformed evidence entries it cannot bind.
    """
    indexed: dict[str, dict[str, Any]] = {}
    raw_facts = evidence.get("facts")
    if not isinstance(raw_facts, list):
        return indexed
    for raw in raw_facts:
        if not isinstance(raw, Mapping):
            continue
        fact_id = raw.get("fact_id")
        if isinstance(fact_id, str) and fact_id not in indexed:
            indexed[fact_id] = dict(raw)
    return indexed


def self_review_check(
    claims: Mapping[str, Any],
    evidence_facts: Mapping[str, Mapping[str, Any]],
) -> dict[str, object]:
    """Review every candidate claim against the evidence fact index.

    Each non-decorative claim (factual and instruction alike) must reference
    at least one evidence fact that exists in the repository-evidence graph;
    a claim without any such correspondence entry is uncovered and fails the
    review.  Decorative claims are exempt: they carry no truth claim.

    Returns ``{"pass": bool, "uncovered": [...], "evidence": str}`` with
    uncovered claim IDs sorted for deterministic reports.
    """
    uncovered: list[str] = []
    for collection_name in ("markdown_blocks", "diagram_labels"):
        collection = claims.get(collection_name)
        if not isinstance(collection, list):
            continue
        for raw in collection:
            if not isinstance(raw, Mapping) or raw.get("claim_kind") == "decorative":
                continue
            identifiers = raw.get("evidence_ids")
            if (
                not isinstance(identifiers, list)
                or not identifiers
                or not any(
                    isinstance(identifier, str) and identifier in evidence_facts
                    for identifier in identifiers
                )
            ):
                claim_id = raw.get("claim_id")
                if isinstance(claim_id, str):
                    uncovered.append(claim_id)
    ordered = sorted(set(uncovered))
    if not ordered:
        return {"pass": True, "uncovered": [], "evidence": "self-review: all candidate claims correspond to repository-evidence facts"}
    return {
        "pass": False,
        "uncovered": ordered,
        "evidence": f"self-review: {len(ordered)} claim(s) without evidence correspondence: {', '.join(ordered)}",
    }


__all__ = [
    "MAX_SELF_REVIEW_REVISIONS",
    "collect_evidence_facts",
    "self_review_check",
]
