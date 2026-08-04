"""Deterministic, renderer-neutral timeline semantics for a normalized Plan.

The kernel emits data only.  It does not know about frames, clocks, easing,
SVG transforms, browsers, or codecs; those concerns belong to later adapters.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, ClassVar, Literal

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from .normalize import Plan


_TimelineKind = Literal["reveal", "emphasis"]
_SCHEMA_VERSION = 1
_MAX_DURATION_MS = 30_000
_MAX_TIMELINE_BYTES = 512 * 1024
_REVEAL_MS = 180
_EMPHASIS_MS = 120
_GAP_MS = 40
_KINDS = frozenset({"reveal", "emphasis"})


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id_key(identifier: str) -> bytes:
    return identifier.encode("utf-8")


def _checked_id(value: Any, path: str) -> str:
    if type(value) is not str or not value or value != value.strip() or "\x00" in value:
        raise _fail("E_VISUAL_SPEC_ID", f"{path} must be a non-empty ID")
    return value


def _checked_int(value: Any, path: str, *, minimum: int = 0) -> int:
    if type(value) is not int:
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an integer")
    if value < minimum:
        raise _fail("E_VISUAL_DETERMINISM", f"{path} must be at least {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class _Operation:
    id: str
    kind: _TimelineKind
    target: str
    start_ms: int
    end_ms: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "target": self.target,
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
        }


def _operation(value: Any, index: int) -> _Operation:
    path = f"timeline.operations[{index}]"
    if isinstance(value, _Operation):
        raw: Mapping[str, Any] = {
            "id": value.id,
            "kind": value.kind,
            "target": value.target,
            "start_ms": value.start_ms,
            "end_ms": value.end_ms,
        }
    elif isinstance(value, Mapping):
        raw = value
    else:
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an object")
    allowed = {"id", "kind", "target", "start_ms", "end_ms"}
    unknown = sorted(set(raw) - allowed)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{path} contains unknown field: {unknown[0]}")
    missing = sorted(allowed - set(raw))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{path} is missing field: {missing[0]}")
    identifier = _checked_id(raw["id"], f"{path}.id")
    kind = raw["kind"]
    if type(kind) is not str:
        raise _fail("E_SCHEMA_TYPE", f"{path}.kind must be a string")
    if kind not in _KINDS:
        raise _fail("E_SCHEMA_VALUE", f"{path}.kind is unsupported")
    target = _checked_id(raw["target"], f"{path}.target")
    start = _checked_int(raw["start_ms"], f"{path}.start_ms")
    end = _checked_int(raw["end_ms"], f"{path}.end_ms", minimum=1)
    if end <= start:
        raise _fail("E_VISUAL_DETERMINISM", f"{path} must have a positive interval")
    return _Operation(identifier, kind, target, start, end)


def _targets(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        raise _fail("E_SCHEMA_TYPE", "timeline.targets must be an array")
    result = tuple(_checked_id(item, "timeline.targets[]") for item in value)
    if len(result) != len(set(result)):
        raise _fail("E_VISUAL_SPEC_ID", "timeline.targets must be unique")
    if result != tuple(sorted(result, key=_id_key)):
        raise _fail("E_VISUAL_SPEC_ID", "timeline.targets must be byte-sorted")
    return result


def _reduced_motion(value: Any, targets: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, Mapping):
        allowed = {"mode", "visible"}
        unknown = sorted(set(value) - allowed)
        if unknown:
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"timeline.reduced_motion contains unknown field: {unknown[0]}")
        if value.get("mode") != "static":
            raise _fail("E_SCHEMA_VALUE", "timeline.reduced_motion.mode must be static")
        value = value.get("visible")
    visible = _targets(value)
    if visible != targets:
        raise _fail("E_VISUAL_DETERMINISM", "reduced-motion static state must expose every declared target")
    return visible


@dataclass(frozen=True, slots=True)
class Timeline:
    """Immutable Timeline v1 with a static reduced-motion projection."""

    targets: tuple[str, ...]
    duration_ms: int
    operations: tuple[_Operation, ...]
    reduced_motion: tuple[str, ...]

    schema_version: ClassVar[int] = _SCHEMA_VERSION

    def __post_init__(self) -> None:
        targets = _targets(self.targets)
        duration = _checked_int(self.duration_ms, "timeline.duration_ms")
        if duration > _MAX_DURATION_MS:
            raise _fail("E_VISUAL_DETERMINISM", f"timeline duration exceeds {_MAX_DURATION_MS}ms")

        raw_operations = self.operations
        if not isinstance(raw_operations, Sequence) or isinstance(raw_operations, (str, bytes)):
            raise _fail("E_SCHEMA_TYPE", "timeline.operations must be an array")
        operations = tuple(_operation(item, index) for index, item in enumerate(raw_operations))
        operation_ids: set[str] = set()
        target_set = set(targets)
        operation_targets: set[str] = set()
        for item in operations:
            if item.id in operation_ids:
                raise _fail("E_VISUAL_SPEC_ID", f"duplicate timeline operation ID: {item.id}")
            operation_ids.add(item.id)
            if item.target not in target_set:
                raise _fail("E_VISUAL_SPEC_EDGE", f"timeline operation targets undeclared ID: {item.target}")
            if item.target in operation_targets:
                raise _fail("E_VISUAL_SPEC_ID", f"timeline target has multiple operations: {item.target}")
            operation_targets.add(item.target)
            if item.end_ms > duration:
                raise _fail("E_VISUAL_DETERMINISM", f"timeline operation exceeds duration: {item.id}")
        if operation_targets != target_set:
            missing = sorted(target_set - operation_targets, key=_id_key)
            raise _fail("E_VISUAL_DETERMINISM", f"timeline is missing operation target: {missing[0]}")
        ordered = tuple(sorted(operations, key=lambda item: (item.start_ms, item.end_ms, _id_key(item.id))))
        previous_end = 0
        for item in ordered:
            if item.start_ms < previous_end:
                raise _fail("E_VISUAL_DETERMINISM", f"timeline operations overlap at {item.id}")
            previous_end = item.end_ms
        if ordered and ordered[-1].end_ms != duration:
            raise _fail("E_VISUAL_DETERMINISM", "timeline duration must end at the final operation")
        if not ordered and duration != 0:
            raise _fail("E_VISUAL_DETERMINISM", "an empty timeline must have zero duration")
        reduced = _reduced_motion(self.reduced_motion, targets)
        object.__setattr__(self, "targets", targets)
        object.__setattr__(self, "duration_ms", duration)
        object.__setattr__(self, "operations", ordered)
        object.__setattr__(self, "reduced_motion", reduced)
        if len(self.canonical_bytes()) > _MAX_TIMELINE_BYTES:
            raise _fail("E_VISUAL_SPEC_SIZE", f"timeline exceeds {_MAX_TIMELINE_BYTES} canonical bytes")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "targets": list(self.targets),
            "duration_ms": self.duration_ms,
            "operations": [item.as_dict() for item in self.operations],
            "reduced_motion": {"mode": "static", "visible": list(self.reduced_motion)},
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def _validate_plan(plan: Plan) -> tuple[tuple[str, ...], dict[str, int], tuple[Any, ...]]:
    if not isinstance(plan, Plan):
        raise _fail("E_SCHEMA_TYPE", "timeline derivation requires a normalized Plan")
    if plan.schema_version != 1:
        raise _fail("E_SCHEMA_VERSION", "timeline derivation requires Plan schema_version 1")

    collections = (("nodes", plan.nodes), ("edges", plan.edges), ("groups", plan.groups), ("lanes", plan.lanes))
    ids: list[str] = []
    by_id: dict[str, Any] = {}
    for name, values in collections:
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)):
            raise _fail("E_SCHEMA_TYPE", f"plan.{name} must be an array")
        for index, value in enumerate(values):
            identifier = _checked_id(getattr(value, "id", None), f"plan.{name}[{index}].id")
            if identifier in by_id:
                raise _fail("E_VISUAL_SPEC_ID", f"duplicate Plan element ID: {identifier}")
            by_id[identifier] = value
            ids.append(identifier)
    node_ids = {item.id for item in plan.nodes}
    group_ids = {item.id for item in plan.groups}
    lane_ids = {item.id for item in plan.lanes}
    for node in plan.nodes:
        if node.group is not None and node.group.id not in group_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared group")
        if node.lane is not None and node.lane.id not in lane_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared lane")
    for edge in plan.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} references an undeclared node")
        if edge.source == edge.target:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} is a self-edge")
        if type(edge.kind) is not str:
            raise _fail("E_SCHEMA_TYPE", f"edge {edge.id}.kind must be a string")
        if type(edge.is_back_edge) is not bool:
            raise _fail("E_SCHEMA_TYPE", f"edge {edge.id}.is_back_edge must be boolean")

    if not isinstance(plan.constraints, Sequence) or isinstance(plan.constraints, (str, bytes)):
        raise _fail("E_SCHEMA_TYPE", "plan.constraints must be an array")
    constraints: dict[str, dict[str, int | None]] = {}
    for index, constraint in enumerate(plan.constraints):
        target = _checked_id(getattr(constraint, "target", None), f"plan.constraints[{index}].target")
        if target not in by_id:
            raise _fail("E_VISUAL_SPEC_EDGE", f"constraint target is undeclared: {target}")
        current = constraints.setdefault(target, {"order": None, "rank": None, "pin": None})
        for name in ("order", "rank", "pin"):
            value = getattr(constraint, name)
            if value is None:
                continue
            checked = _checked_int(value, f"plan.constraints[{index}].{name}")
            if current[name] is not None and current[name] != checked:
                raise _fail("E_VISUAL_DETERMINISM", f"constraint {target}.{name} is contradictory")
            current[name] = checked

    adjacency: dict[str, list[str]] = {identifier: [] for identifier in node_ids}
    forward_edges: list[Any] = []
    for edge in sorted(plan.edges, key=lambda item: _id_key(item.id)):
        if edge.kind == "back" or edge.is_back_edge:
            continue
        adjacency[edge.source].append(edge.target)
        forward_edges.append(edge)
    indegree = {identifier: 0 for identifier in node_ids}
    for edge in forward_edges:
        indegree[edge.target] += 1
    ready = sorted((identifier for identifier, value in indegree.items() if value == 0), key=_id_key)
    topological: list[str] = []
    while ready:
        current = ready.pop(0)
        topological.append(current)
        for target in sorted(adjacency[current], key=_id_key):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=_id_key)
    if len(topological) != len(node_ids):
        raise _fail("E_VISUAL_DETERMINISM", "forward Plan graph is cyclic")
    ranks = {identifier: 0 for identifier in node_ids}
    predecessors: dict[str, list[str]] = defaultdict(list)
    for edge in forward_edges:
        predecessors[edge.target].append(edge.source)
    for identifier in topological:
        natural = max((ranks[source] + 1 for source in predecessors[identifier]), default=0)
        hint = constraints.get(identifier, {}).get("rank")
        if hint is not None and hint < natural:
            raise _fail("E_VISUAL_DETERMINISM", f"rank hint for {identifier} violates a forward edge")
        ranks[identifier] = hint if hint is not None else natural
    return tuple(sorted(ids, key=_id_key)), ranks, tuple(sorted(plan.edges, key=lambda item: _id_key(item.id)))


def _order_key(item: Any, constraints: Mapping[str, Mapping[str, int | None]]) -> tuple[Any, ...]:
    hints = constraints.get(item.id, {})
    order = hints.get("order")
    return (order is None, order if order is not None else 0, _id_key(item.id))


def _build_operations(plan: Plan, ranks: Mapping[str, int], edges: tuple[Any, ...], constraints: Mapping[str, Mapping[str, int | None]]) -> tuple[_Operation, ...]:
    nodes = tuple(sorted(plan.nodes, key=lambda item: (ranks[item.id], *_order_key(item, constraints))))
    lanes = tuple(sorted(plan.lanes, key=lambda item: _order_key(item, constraints)))
    groups = tuple(sorted(plan.groups, key=lambda item: _order_key(item, constraints)))
    ordered: list[tuple[str, _TimelineKind, str]] = []
    ordered.extend((f"reveal:{item.id}", "reveal", item.id) for item in lanes)
    ordered.extend((f"reveal:{item.id}", "reveal", item.id) for item in groups)
    ordered.extend((f"reveal:{item.id}", "reveal", item.id) for item in nodes)
    ordered.extend(
        (f"emphasis:{item.id}", "emphasis", item.id)
        for item in sorted(
            edges,
            key=lambda item: (
                max(ranks[item.source], ranks[item.target]),
                item.is_back_edge or item.kind == "back",
                *_order_key(item, constraints),
            ),
        )
    )
    if len(ordered) > _MAX_DURATION_MS:
        raise _fail("E_VISUAL_DETERMINISM", "timeline has too many operations for its bounded duration")
    ideal = [_REVEAL_MS if kind == "reveal" else _EMPHASIS_MS for _, kind, _ in ordered]
    gaps = _GAP_MS if len(ordered) > 1 else 0
    available = _MAX_DURATION_MS - gaps * max(0, len(ordered) - 1)
    if available < len(ordered):
        gaps = 1
        available = _MAX_DURATION_MS - gaps * max(0, len(ordered) - 1)
    if ordered and available < len(ordered):
        raise _fail("E_VISUAL_DETERMINISM", "timeline operation count exceeds bounded duration")
    ideal_total = sum(ideal)
    durations = ideal if ideal_total <= available else [max(1, (value * available) // ideal_total) for value in ideal]
    operations: list[_Operation] = []
    cursor = 0
    for index, ((identifier, kind, target), duration) in enumerate(zip(ordered, durations)):
        end = cursor + duration
        operations.append(_Operation(identifier, kind, target, cursor, end))
        cursor = end
        if index + 1 < len(ordered):
            cursor += gaps
    if operations and cursor > _MAX_DURATION_MS:
        raise _fail("E_VISUAL_DETERMINISM", "timeline duration exceeds bounded maximum")
    return tuple(operations)


def derive_timeline(plan: Plan) -> Timeline:
    """Derive canonical reveal/emphasis operations from a normalized Plan."""

    if not isinstance(plan, Plan):
        raise _fail("E_SCHEMA_TYPE", "timeline derivation requires a normalized Plan")
    targets, ranks, edges = _validate_plan(plan)
    constraints: dict[str, dict[str, int | None]] = {}
    for constraint in plan.constraints:
        current = constraints.setdefault(constraint.target, {"order": None, "rank": None, "pin": None})
        for name in ("order", "rank", "pin"):
            value = getattr(constraint, name)
            if value is not None:
                current[name] = value
    operations = _build_operations(plan, ranks, edges, constraints)
    duration = operations[-1].end_ms if operations else 0
    return Timeline(targets, duration, operations, targets)


__all__ = ["Timeline", "derive_timeline"]
