"""Deterministic, data-only interaction projection for normalized Plans."""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.locale import parse_locale
from .model import VISUAL_EDGE_KINDS, VISUAL_INTENT_KINDS, VISUAL_NODE_KINDS
from .normalize import (
    Plan,
    PlanConstraint,
    PlanEdge,
    PlanGroup,
    PlanIntent,
    PlanLane,
    PlanNode,
)


INTERACTION_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(
    r"(?:file|snippet|config|package|symbol|cli|test|command|git|documentation):[0-9a-f]{64}\Z"
)
def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id_key(identifier: str) -> bytes:
    return identifier.encode("utf-8")


def _validate_id(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must be a non-empty ID")
    if unicodedata.normalize("NFC", value) != value:
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must be NFC-normalized")
    if any(ord(character) < 0x20 for character in value):
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must not contain control characters")
    return value


def _validate_text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise _fail("E_SCHEMA_TYPE", f"{context} must be a non-empty string")
    if unicodedata.normalize("NFC", value) != value:
        raise _fail("E_SCHEMA_VALUE", f"{context} must be NFC-normalized")
    return value


def _validate_evidence_ids(value: Any, context: str, *, required: bool) -> tuple[str, ...]:
    if not isinstance(value, tuple):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an immutable tuple")
    if required and not value:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} requires one or more Evidence IDs")
    if any(not isinstance(item, str) or _EVIDENCE_ID.fullmatch(item) is None for item in value):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} must contain Evidence v2 IDs")
    if len(value) != len(set(value)) or value != tuple(sorted(value, key=_id_key)):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} must be byte-sorted and unique")
    return value


def _validate_label_and_evidence(
    label: Any,
    evidence_ids: Any,
    context: str,
    *,
    required: bool,
) -> tuple[str | None, tuple[str, ...]]:
    if label is None:
        if required:
            raise _fail("E_SCHEMA_TYPE", f"{context}.label must be a string")
        identifiers = _validate_evidence_ids(evidence_ids, f"{context}.evidence_ids", required=False)
        if identifiers:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context}.evidence_ids requires a visible label")
        return None, ()
    text = _validate_text(label, f"{context}.label")
    return text, _validate_evidence_ids(evidence_ids, f"{context}.evidence_ids", required=True)


def _validate_plan(plan: Plan) -> tuple[
    tuple[PlanNode, ...],
    tuple[PlanEdge, ...],
    tuple[PlanGroup, ...],
    tuple[PlanLane, ...],
]:
    """Recheck the immutable Plan boundary before deriving interaction data."""

    if not isinstance(plan, Plan):
        raise _fail("E_SCHEMA_TYPE", "interaction derivation requires a normalized Plan")
    if type(plan.schema_version) is not int or plan.schema_version != 1:
        raise _fail("E_SCHEMA_VERSION", "interaction derivation requires Plan schema_version 1")
    if not isinstance(plan.source_spec_sha256, str) or _SHA256.fullmatch(plan.source_spec_sha256) is None:
        raise _fail("E_VISUAL_SPEC_ID", "Plan source_spec_sha256 must be a lowercase SHA-256 digest")
    if not isinstance(plan.intent, PlanIntent):
        raise _fail("E_SCHEMA_TYPE", "Plan intent must be a PlanIntent")
    if not isinstance(plan.intent.kind, str) or plan.intent.kind not in VISUAL_INTENT_KINDS:
        raise _fail("E_SCHEMA_VALUE", "Plan intent kind is unsupported")
    _validate_label_and_evidence(
        plan.intent.label,
        plan.intent.evidence_ids,
        "Plan.intent",
        required=True,
    )
    if not isinstance(plan.locale, str):
        raise _fail("E_SCHEMA_TYPE", "Plan locale must be a string")
    parse_locale(plan.locale, "Plan.locale")
    if not isinstance(plan.variants, tuple):
        raise _fail("E_SCHEMA_TYPE", "Plan variants must be an immutable tuple")
    if not plan.variants or any(not isinstance(item, str) for item in plan.variants):
        raise _fail("E_SCHEMA_VALUE", "Plan variants must be non-empty strings")
    if len(plan.variants) != len(set(plan.variants)):
        raise _fail("E_SCHEMA_VALUE", "Plan variants must be non-empty and unique")
    if any(item not in {"desktop", "mobile"} for item in plan.variants):
        raise _fail("E_SCHEMA_VALUE", "Plan variants must be desktop or mobile")
    if plan.variants != tuple(sorted(plan.variants, key=_id_key)):
        raise _fail("E_SCHEMA_VALUE", "Plan variants must be byte-sorted")
    for name, values in (
        ("nodes", plan.nodes),
        ("edges", plan.edges),
        ("groups", plan.groups),
        ("lanes", plan.lanes),
        ("constraints", plan.constraints),
    ):
        if not isinstance(values, tuple):
            raise _fail("E_SCHEMA_TYPE", f"Plan {name} must be an immutable tuple")

    groups: list[PlanGroup] = []
    lanes: list[PlanLane] = []
    nodes: list[PlanNode] = []
    edges: list[PlanEdge] = []
    element_ids: dict[str, str] = {}

    for index, group in enumerate(plan.groups):
        if not isinstance(group, PlanGroup):
            raise _fail("E_SCHEMA_TYPE", f"Plan.groups[{index}] must be a PlanGroup")
        identifier = _validate_id(group.id, f"Plan.groups[{index}].id")
        _validate_label_and_evidence(
            group.label,
            group.evidence_ids,
            f"Plan.groups[{index}]",
            required=True,
        )
        if identifier in element_ids:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate focus target: {identifier}")
        element_ids[identifier] = "group"
        groups.append(group)

    for index, lane in enumerate(plan.lanes):
        if not isinstance(lane, PlanLane):
            raise _fail("E_SCHEMA_TYPE", f"Plan.lanes[{index}] must be a PlanLane")
        identifier = _validate_id(lane.id, f"Plan.lanes[{index}].id")
        _validate_label_and_evidence(
            lane.label,
            lane.evidence_ids,
            f"Plan.lanes[{index}]",
            required=True,
        )
        if identifier in element_ids:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate focus target: {identifier}")
        element_ids[identifier] = "lane"
        lanes.append(lane)

    group_by_id = {group.id: group for group in groups}
    lane_by_id = {lane.id: lane for lane in lanes}
    for index, node in enumerate(plan.nodes):
        if not isinstance(node, PlanNode):
            raise _fail("E_SCHEMA_TYPE", f"Plan.nodes[{index}] must be a PlanNode")
        identifier = _validate_id(node.id, f"Plan.nodes[{index}].id")
        if not isinstance(node.kind, str) or node.kind not in VISUAL_NODE_KINDS:
            raise _fail("E_SCHEMA_VALUE", f"Plan.nodes[{index}].kind is unsupported")
        _validate_label_and_evidence(
            node.label,
            node.evidence_ids,
            f"Plan.nodes[{index}]",
            required=True,
        )
        if identifier in element_ids:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate focus target: {identifier}")
        if node.group is not None:
            if not isinstance(node.group, PlanGroup) or node.group.id not in group_by_id:
                raise _fail("E_VISUAL_SPEC_EDGE", f"node {identifier} references an undeclared group")
            if node.group != group_by_id[node.group.id]:
                raise _fail("E_VISUAL_SPEC_EDGE", f"node {identifier} carries a stale group reference")
        if node.lane is not None:
            if not isinstance(node.lane, PlanLane) or node.lane.id not in lane_by_id:
                raise _fail("E_VISUAL_SPEC_EDGE", f"node {identifier} references an undeclared lane")
            if node.lane != lane_by_id[node.lane.id]:
                raise _fail("E_VISUAL_SPEC_EDGE", f"node {identifier} carries a stale lane reference")
        element_ids[identifier] = "node"
        nodes.append(node)

    node_ids = {node.id for node in nodes}
    for index, edge in enumerate(plan.edges):
        if not isinstance(edge, PlanEdge):
            raise _fail("E_SCHEMA_TYPE", f"Plan.edges[{index}] must be a PlanEdge")
        identifier = _validate_id(edge.id, f"Plan.edges[{index}].id")
        if not isinstance(edge.kind, str) or edge.kind not in VISUAL_EDGE_KINDS:
            raise _fail("E_SCHEMA_VALUE", f"Plan.edges[{index}].kind is unsupported")
        source = _validate_id(edge.source, f"Plan.edges[{index}].source")
        target = _validate_id(edge.target, f"Plan.edges[{index}].target")
        if source not in node_ids or target not in node_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {identifier} references an undeclared node")
        if type(edge.is_back_edge) is not bool:
            raise _fail("E_SCHEMA_TYPE", f"Plan.edges[{index}].is_back_edge must be boolean")
        _validate_label_and_evidence(
            edge.label,
            edge.evidence_ids,
            f"Plan.edges[{index}]",
            required=False,
        )
        if identifier in element_ids:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate focus target: {identifier}")
        element_ids[identifier] = "edge"
        edges.append(edge)

    for index, constraint in enumerate(plan.constraints):
        if not isinstance(constraint, PlanConstraint):
            raise _fail("E_SCHEMA_TYPE", f"Plan.constraints[{index}] must be a PlanConstraint")
        target = _validate_id(constraint.target, f"Plan.constraints[{index}].target")
        if target not in element_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"constraint target is undeclared: {target}")
        hints = (constraint.order, constraint.rank, constraint.pin)
        if all(value is None for value in hints):
            raise _fail("E_SCHEMA_VALUE", f"Plan.constraints[{index}] must contain a layout hint")
        if any(type(value) is not int or value < 0 for value in hints if value is not None):
            raise _fail("E_SCHEMA_VALUE", f"Plan.constraints[{index}] hints must be non-negative integers")

    return (
        tuple(sorted(nodes, key=lambda item: _id_key(item.id))),
        tuple(sorted(edges, key=lambda item: _id_key(item.id))),
        tuple(sorted(groups, key=lambda item: _id_key(item.id))),
        tuple(sorted(lanes, key=lambda item: _id_key(item.id))),
    )


def _freeze_map(value: Mapping[str, tuple[str, ...]], context: str) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for key, targets in value.items():
        _validate_id(key, f"{context} key")
        if not isinstance(targets, tuple) or any(not isinstance(item, str) for item in targets):
            raise _fail("E_SCHEMA_TYPE", f"{context}.{key} must contain immutable string IDs")
        for item in targets:
            _validate_id(item, f"{context}.{key}[]")
        if targets != tuple(sorted(targets, key=_id_key)):
            raise _fail("E_VISUAL_SPEC_ID", f"{context}.{key} must be byte-sorted")
        result[key] = targets
    return MappingProxyType(result)


@dataclass(frozen=True, slots=True)
class InteractionGraph:
    """Immutable focus, provenance, adjacency, and membership data."""

    focus_order: tuple[str, ...]
    evidence_links: Mapping[str, tuple[str, ...]]
    adjacency: Mapping[str, tuple[str, ...]]
    group_navigation: Mapping[str, tuple[str, ...]]
    lane_navigation: Mapping[str, tuple[str, ...]]

    schema_version: ClassVar[int] = INTERACTION_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.focus_order, tuple) or any(not isinstance(item, str) for item in self.focus_order):
            raise _fail("E_SCHEMA_TYPE", "interaction focus_order must be an immutable string tuple")
        if len(self.focus_order) != len(set(self.focus_order)):
            raise _fail("E_VISUAL_SPEC_ID", "interaction focus_order contains a duplicate target")
        for target in self.focus_order:
            _validate_id(target, "interaction focus_order[]")
        for name in ("evidence_links", "adjacency", "group_navigation", "lane_navigation"):
            object.__setattr__(self, name, _freeze_map(getattr(self, name), f"interaction {name}"))
        for identifier, evidence_ids in self.evidence_links.items():
            _validate_evidence_ids(
                evidence_ids,
                f"interaction evidence_links.{identifier}",
                required=False,
            )
        focus_targets = set(self.focus_order)
        for target in self.focus_order:
            evidence_ids = self.evidence_links.get(target)
            if evidence_ids is None or not evidence_ids:
                raise _fail("E_VISUAL_SPEC_EVIDENCE", f"focus target {target} has no Evidence link")
        for name in ("adjacency", "group_navigation", "lane_navigation"):
            values = getattr(self, name)
            for identifier, targets in values.items():
                if identifier not in focus_targets:
                    raise _fail("E_VISUAL_SPEC_EDGE", f"{name} references undeclared target: {identifier}")
                if len(targets) != len(set(targets)):
                    raise _fail("E_VISUAL_SPEC_EDGE", f"{name}.{identifier} contains duplicate target")
                if any(target not in focus_targets for target in targets):
                    raise _fail("E_VISUAL_SPEC_EDGE", f"{name}.{identifier} references undeclared target")
        for source, targets in self.adjacency.items():
            for target in targets:
                if source not in self.adjacency.get(target, ()):
                    raise _fail("E_VISUAL_SPEC_EDGE", "interaction adjacency must be symmetric")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "focus_order": list(self.focus_order),
            "evidence_links": {
                identifier: list(values) for identifier, values in self.evidence_links.items()
            },
            "adjacency": {
                identifier: list(values) for identifier, values in self.adjacency.items()
            },
            "group_navigation": {
                identifier: list(values) for identifier, values in self.group_navigation.items()
            },
            "lane_navigation": {
                identifier: list(values) for identifier, values in self.lane_navigation.items()
            },
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def derive_interaction(plan: Plan) -> InteractionGraph:
    """Derive canonical interaction data without rendering or inferred links."""

    nodes, edges, groups, lanes = _validate_plan(plan)
    node_ids = tuple(node.id for node in nodes)
    group_ids = tuple(group.id for group in groups)
    lane_ids = tuple(lane.id for lane in lanes)

    # Outer containers precede their visible node members in keyboard order.
    focus_order = (*lane_ids, *group_ids, *node_ids)
    if len(focus_order) != len(set(focus_order)):
        raise _fail("E_VISUAL_SPEC_ID", "interaction focus_order contains a duplicate target")

    evidence_links: dict[str, tuple[str, ...]] = {}
    for element in (*groups, *lanes, *nodes, *edges):
        evidence_links[element.id] = element.evidence_ids

    adjacency: dict[str, set[str]] = {identifier: set() for identifier in node_ids}
    for edge in edges:
        adjacency[edge.source].add(edge.target)
        if edge.source != edge.target:
            adjacency[edge.target].add(edge.source)

    group_navigation = {
        group.id: tuple(sorted((node.id for node in nodes if node.group is not None and node.group.id == group.id), key=_id_key))
        for group in groups
    }
    lane_navigation = {
        lane.id: tuple(sorted((node.id for node in nodes if node.lane is not None and node.lane.id == lane.id), key=_id_key))
        for lane in lanes
    }
    return InteractionGraph(
        focus_order,
        evidence_links,
        {
            identifier: tuple(sorted(values, key=_id_key))
            for identifier, values in adjacency.items()
        },
        group_navigation,
        lane_navigation,
    )


__all__ = ["InteractionGraph", "derive_interaction"]
