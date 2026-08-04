"""Pure normalization of validated Visual Spec values into a backend-neutral Plan."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from .model import VisualSpec, validate_visual_spec


PLAN_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class PlanIntent:
    kind: str
    label: str
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class PlanGroup:
    id: str
    label: str
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class PlanLane:
    id: str
    label: str
    evidence_ids: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
        }


@dataclass(frozen=True, slots=True)
class PlanNode:
    id: str
    kind: str
    label: str
    evidence_ids: tuple[str, ...]
    group: PlanGroup | None = None
    lane: PlanLane | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "label": self.label,
            "evidence_ids": list(self.evidence_ids),
        }
        if self.group is not None:
            result["group_id"] = self.group.id
        if self.lane is not None:
            result["lane_id"] = self.lane.id
        return result


@dataclass(frozen=True, slots=True)
class PlanEdge:
    id: str
    kind: str
    source: str
    target: str
    label: str | None
    evidence_ids: tuple[str, ...]
    is_back_edge: bool

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
            "is_back_edge": self.is_back_edge,
        }
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        return result


@dataclass(frozen=True, slots=True)
class PlanConstraint:
    target: str
    order: int | None = None
    rank: int | None = None
    pin: int | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"target": self.target}
        if self.order is not None:
            result["order"] = self.order
        if self.rank is not None:
            result["rank"] = self.rank
        if self.pin is not None:
            result["pin"] = self.pin
        return result


@dataclass(frozen=True, slots=True)
class Plan:
    schema_version: int
    source_spec_sha256: str
    intent: PlanIntent
    locale: str
    variants: tuple[str, ...]
    nodes: tuple[PlanNode, ...]
    edges: tuple[PlanEdge, ...]
    groups: tuple[PlanGroup, ...]
    lanes: tuple[PlanLane, ...]
    constraints: tuple[PlanConstraint, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_spec_sha256": self.source_spec_sha256,
            "intent": self.intent.as_dict(),
            "locale": self.locale,
            "variants": list(self.variants),
            "nodes": [item.as_dict() for item in self.nodes],
            "edges": [item.as_dict() for item in self.edges],
            "groups": [item.as_dict() for item in self.groups],
            "lanes": [item.as_dict() for item in self.lanes],
            "constraints": [item.as_dict() for item in self.constraints],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def _reaches(adjacency: Mapping[str, tuple[str, ...]], start: str, target: str) -> bool:
    """Return whether ``target`` is reachable from ``start`` in the accepted DAG."""

    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency.get(current, ()))
    return False


def normalize_visual_spec(
    payload: Any,
    evidence_graph: Mapping[str, Any] | None = None,
) -> Plan:
    """Validate and copy a Visual Spec into a closed, backend-neutral Plan.

    Forward edges are accepted in stable byte-sorted ID order.  An explicit
    ``back`` edge, a self-edge, or an edge whose target can already reach its
    source is marked as a back edge and excluded from the forward adjacency.
    Every source element remains in the resulting immutable Plan.
    """

    if evidence_graph is None:
        raise ContractError(
            "E_VISUAL_SPEC_EVIDENCE",
            "normalization requires an Evidence v2 graph",
        )
    spec = validate_visual_spec(payload, evidence_graph=evidence_graph)
    groups = tuple(
        PlanGroup(item.id, item.label, item.evidence_ids) for item in spec.groups
    )
    lanes = tuple(
        PlanLane(item.id, item.label, item.evidence_ids) for item in spec.lanes
    )
    groups_by_id = {item.id: item for item in groups}
    lanes_by_id = {item.id: item for item in lanes}

    nodes = tuple(
        PlanNode(
            item.id,
            item.kind,
            item.label,
            item.evidence_ids,
            groups_by_id.get(item.group_id) if item.group_id is not None else None,
            lanes_by_id.get(item.lane_id) if item.lane_id is not None else None,
        )
        for item in spec.nodes
    )

    accepted: dict[str, list[str]] = {item.id: [] for item in nodes}
    edges: list[PlanEdge] = []
    for item in spec.edges:
        is_back_edge = (
            item.kind == "back"
            or item.source == item.target
            or _reaches(
                {key: tuple(value) for key, value in accepted.items()},
                item.target,
                item.source,
            )
        )
        if not is_back_edge:
            accepted[item.source].append(item.target)
        edges.append(
            PlanEdge(
                item.id,
                item.kind,
                item.source,
                item.target,
                item.label,
                item.evidence_ids,
                is_back_edge,
            )
        )

    constraints = tuple(
        PlanConstraint(item.target, item.order, item.rank, item.pin)
        for item in spec.constraints
    )
    return Plan(
        PLAN_SCHEMA_VERSION,
        spec.sha256(),
        PlanIntent(spec.intent.kind, spec.intent.label, spec.intent.evidence_ids),
        spec.locale,
        spec.variants,
        nodes,
        tuple(edges),
        groups,
        lanes,
        constraints,
    )


def canonical_plan_bytes(
    payload: Any,
    evidence_graph: Mapping[str, Any] | None = None,
) -> bytes:
    plan = payload if isinstance(payload, Plan) else normalize_visual_spec(payload, evidence_graph)
    return plan.canonical_bytes()


def canonical_plan_sha256(
    payload: Any,
    evidence_graph: Mapping[str, Any] | None = None,
) -> str:
    plan = payload if isinstance(payload, Plan) else normalize_visual_spec(payload, evidence_graph)
    return plan.sha256()


__all__ = [
    "PLAN_SCHEMA_VERSION",
    "Plan",
    "PlanConstraint",
    "PlanEdge",
    "PlanGroup",
    "PlanIntent",
    "PlanLane",
    "PlanNode",
    "canonical_plan_bytes",
    "canonical_plan_sha256",
    "normalize_visual_spec",
]
