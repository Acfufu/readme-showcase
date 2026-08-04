"""Compile normalized graph semantics into a deterministic ELK graph.

This module owns only graph semantics.  It computes ranks and sibling order so
the geometry backend can make the final coordinate decision; it never writes
coordinates or dimensions.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from .normalize import Plan


_EDGE_KINDS = frozenset({"flow", "dependency", "data", "back"})

_ROOT_LAYOUT_OPTIONS = {
    "elk.algorithm": "layered",
    "elk.direction": "RIGHT",
    "elk.hierarchyHandling": "INCLUDE_CHILDREN",
    "elk.layered.considerModelOrder.strategy": "NODES_AND_EDGES",
    "elk.layered.crossingMinimization.strategy": "LAYER_SWEEP",
    "elk.layered.crossingMinimization.forceNodeModelOrder": "true",
    "elk.layered.nodePlacement.strategy": "NETWORK_SIMPLEX",
}


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id_key(identifier: str) -> bytes:
    return identifier.encode("utf-8")


def _check_elements(plan: Plan) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, Any]]:
    node_ids = tuple(sorted((node.id for node in plan.nodes), key=_id_key))
    group_ids = tuple(sorted((group.id for group in plan.groups), key=_id_key))
    all_ids = (*node_ids, *group_ids, *(lane.id for lane in plan.lanes))
    if len(set(all_ids)) != len(all_ids):
        raise _fail("E_VISUAL_SPEC_ID", "graph elements must have globally unique IDs")
    node_set = set(node_ids)
    element_set = set(all_ids)
    group_set = set(group_ids)
    lane_set = set(all_ids) - node_set - group_set
    for node in plan.nodes:
        if node.group is not None and node.group.id not in group_set:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared group")
        if node.lane is not None and node.lane.id not in lane_set:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared lane")
    edge_ids: set[str] = set()
    for edge in sorted(plan.edges, key=lambda item: _id_key(item.id)):
        if edge.id in element_set or edge.id in edge_ids or edge.source not in node_set or edge.target not in node_set:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} has an undeclared or colliding endpoint")
        edge_ids.add(edge.id)
        if edge.kind not in _EDGE_KINDS or edge.source == edge.target:
            reason = "unsupported kind" if edge.kind not in _EDGE_KINDS else "self-edge"
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} has {reason}")
    constraints: dict[str, Any] = {}
    for item in plan.constraints:
        if item.target not in element_set | edge_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"constraint target is undeclared: {item.target}")
        if item.target in lane_set or item.target in edge_ids or item.target in group_set and item.pin is not None:
            raise _fail("E_VISUAL_DETERMINISM", f"constraint target is unsupported: {item.target}")
        if item.target not in constraints:
            constraints[item.target] = {"order": None, "rank": None, "pin": None}
        current = constraints[item.target]
        for name in ("order", "rank", "pin"):
            value = getattr(item, name)
            if value is None:
                continue
            if type(value) is not int or value < 0:
                raise _fail("E_VISUAL_DETERMINISM", f"constraint {item.target}.{name} must be non-negative integer")
            previous = current[name]
            if previous is not None and previous != value:
                raise _fail("E_VISUAL_DETERMINISM", f"constraint {item.target}.{name} is contradictory")
            current[name] = value
    return node_ids, group_ids, constraints


def _reaches(adjacency: dict[str, list[str]], start: str, target: str) -> bool:
    pending = [start]
    visited: set[str] = set()
    while pending:
        current = pending.pop()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        pending.extend(adjacency[current])
    return False


def _forward_edges(plan: Plan, node_ids: tuple[str, ...]) -> tuple[tuple[str, str, str, bool], ...]:
    adjacency = {identifier: [] for identifier in node_ids}
    result: list[tuple[str, str, str, bool]] = []
    for edge in sorted(plan.edges, key=lambda item: _id_key(item.id)):
        is_back = edge.kind == "back" or edge.is_back_edge or _reaches(adjacency, edge.target, edge.source)
        if not is_back:
            adjacency[edge.source].append(edge.target)
        result.append((edge.id, edge.source, edge.target, is_back))
    return tuple(result)


def _topological(node_ids: tuple[str, ...], edges: Iterable[tuple[str, str, str, bool]]) -> tuple[str, ...]:
    adjacency: dict[str, list[str]] = {identifier: [] for identifier in node_ids}
    indegree = {identifier: 0 for identifier in node_ids}
    for _, source, target, is_back in edges:
        if is_back:
            continue
        adjacency[source].append(target)
        indegree[target] += 1
    ready = sorted((identifier for identifier, count in indegree.items() if count == 0), key=_id_key)
    ordered: list[str] = []
    while ready:
        current = ready.pop(0)
        ordered.append(current)
        for target in sorted(adjacency[current], key=_id_key):
            indegree[target] -= 1
            if indegree[target] == 0:
                ready.append(target)
                ready.sort(key=_id_key)
    if len(ordered) != len(node_ids):
        raise _fail("E_VISUAL_DETERMINISM", "forward graph is cyclic after back-edge classification")
    return tuple(ordered)


def _ranks(node_ids: tuple[str, ...], edges: tuple[tuple[str, str, str, bool], ...], constraints: dict[str, Any]) -> dict[str, int]:
    ordered = _topological(node_ids, edges)
    predecessors: dict[str, list[str]] = defaultdict(list)
    for _, source, target, is_back in edges:
        if not is_back:
            predecessors[target].append(source)
    ranks = {identifier: 0 for identifier in node_ids}
    for current in ordered:
        natural = max((ranks[source] + 1 for source in predecessors[current]), default=0)
        hint = constraints.get(current, {}).get("rank")
        if hint is not None and hint < natural:
            raise _fail("E_VISUAL_DETERMINISM", f"rank hint for {current} violates a forward edge")
        ranks[current] = hint if hint is not None else natural
    for current in ordered:
        for source in predecessors[current]:
            if ranks[current] <= ranks[source]:
                raise _fail("E_VISUAL_DETERMINISM", f"rank hints leave edge {source}->{current} unsatisfied")
    return ranks


def _initial_levels(node_ids: tuple[str, ...], ranks: dict[str, int], constraints: dict[str, Any]) -> dict[int, list[str]]:
    levels: dict[int, list[str]] = defaultdict(list)
    for identifier in node_ids:
        levels[ranks[identifier]].append(identifier)
    for rank, values in levels.items():
        values.sort(
            key=lambda identifier: (
                constraints.get(identifier, {}).get("order") is None,
                constraints.get(identifier, {}).get("order")
                if constraints.get(identifier, {}).get("order") is not None
                else 0,
                _id_key(identifier),
            )
        )
        pins = [constraints.get(identifier, {}).get("pin") for identifier in values]
        occupied = [pin for pin in pins if pin is not None]
        if len(set(occupied)) != len(occupied) or any(pin >= len(values) for pin in occupied):
            raise _fail("E_VISUAL_DETERMINISM", f"pin hints for rank {rank} cannot be satisfied")
        placed: list[str | None] = [None] * len(values)
        for identifier in values:
            pin = constraints.get(identifier, {}).get("pin")
            if pin is not None:
                placed[pin] = identifier
        remaining = [identifier for identifier in values if identifier not in placed]
        iterator = iter(remaining)
        levels[rank] = [next(iterator) if item is None else item for item in placed]
    return levels


def _positions(levels: dict[int, list[str]]) -> dict[str, int]:
    return {identifier: position for values in levels.values() for position, identifier in enumerate(values)}


def _sweep(levels: dict[int, list[str]], edges: tuple[tuple[str, str, str, bool], ...], constraints: dict[str, Any], *, forward: bool) -> None:
    positions = _positions(levels)
    neighbors: dict[str, list[str]] = defaultdict(list)
    for _, source, target, is_back in edges:
        if is_back:
            continue
        if forward:
            neighbors[target].append(source)
        else:
            neighbors[source].append(target)
    ranks = sorted(levels)
    if not forward:
        ranks.reverse()
    for rank in ranks:
        values = levels[rank]
        movable = [identifier for identifier in values if constraints.get(identifier, {}).get("pin") is None]
        if len(movable) < 2:
            continue
        def key(identifier: str) -> tuple[Any, ...]:
            values_for_neighbors = [positions[item] for item in neighbors[identifier] if item in positions]
            order = constraints.get(identifier, {}).get("order")
            if order is not None:
                return (0, order, _id_key(identifier))
            if values_for_neighbors:
                return (1, sum(values_for_neighbors) / len(values_for_neighbors), _id_key(identifier))
            return (2, positions[identifier], _id_key(identifier))

        movable.sort(key=key)
        iterator = iter(movable)
        levels[rank] = [next(iterator) if constraints.get(identifier, {}).get("pin") is None else identifier for identifier in values]
        positions = _positions(levels)


def _layout_options(identifier: str, ranks: dict[str, int], positions: dict[str, int]) -> dict[str, str]:
    return {
        "elk.layered.layering.layerId": str(ranks[identifier]),
        "elk.layered.crossingMinimization.positionId": str(positions[identifier]),
    }


@dataclass(frozen=True, slots=True)
class CompiledGraph:
    """Immutable graph constraints with a fresh ELK JSON projection."""

    ranks: tuple[tuple[str, int], ...]
    orders: tuple[tuple[str, int], ...]
    back_edges: tuple[str, ...]
    node_groups: tuple[tuple[str, str | None], ...]
    groups: tuple[tuple[str, tuple[str, ...], int | None, int | None], ...]
    edges: tuple[tuple[str, str, str, bool], ...]

    def as_dict(self) -> dict[str, Any]:
        """Build the supported ELK hierarchy without coordinates."""

        rank_by_id = dict(self.ranks)
        order_by_id = dict(self.orders)
        groups_by_node = dict(self.node_groups)
        nodes: dict[str, dict[str, Any]] = {
            identifier: {
                "id": identifier,
                "layoutOptions": _layout_options(identifier, rank_by_id, order_by_id),
            }
            for identifier, _ in self.ranks
        }
        grouped = {
            identifier: [nodes[node_id] for node_id in child_ids]
            for identifier, child_ids, _, _ in self.groups
        }
        root_nodes: list[dict[str, Any]] = []
        ordered_ids = sorted(rank_by_id, key=lambda item: (rank_by_id[item], order_by_id[item], _id_key(item)))
        for identifier in ordered_ids:
            group_id = groups_by_node[identifier]
            if group_id is None:
                root_nodes.append(nodes[identifier])
        groups: list[dict[str, Any]] = []
        for identifier, _, rank, order in self.groups:
            value: dict[str, Any] = {"id": identifier, "children": grouped[identifier]}
            options: dict[str, str] = {}
            if rank is not None:
                options["elk.layered.layering.layerId"] = str(rank)
            if order is not None:
                options["elk.layered.crossingMinimization.positionId"] = str(order)
            if options:
                value["layoutOptions"] = options
            groups.append(value)
        result_edges: list[dict[str, Any]] = []
        for identifier, source, target, is_back in self.edges:
            value: dict[str, Any] = {"id": identifier, "sources": [source], "targets": [target]}
            if is_back:
                value["layoutOptions"] = {"elk.layered.feedbackEdges": "true"}
            result_edges.append(value)
        return {
            "id": "root",
            "layoutOptions": dict(_ROOT_LAYOUT_OPTIONS),
            "children": [*groups, *root_nodes],
            "edges": result_edges,
        }

    def canonical_bytes(self) -> bytes:
        """Return canonical bytes for this graph projection."""

        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        """Return the SHA-256 digest for this graph projection."""

        return canonical_sha256(self.as_dict())


def compile_graph(plan: Plan) -> CompiledGraph:
    """Compile a normalized :class:`Plan` into immutable ELK constraints."""

    if not isinstance(plan, Plan):
        raise _fail("E_SCHEMA_TYPE", "graph compilation requires a normalized Plan")
    if plan.schema_version != 1:
        raise _fail("E_SCHEMA_VERSION", "graph compilation requires Plan schema_version 1")
    node_ids, group_ids, constraints = _check_elements(plan)
    edges = _forward_edges(plan, node_ids)
    ranks = _ranks(node_ids, edges, constraints)
    levels = _initial_levels(node_ids, ranks, constraints)
    _sweep(levels, edges, constraints, forward=True)
    _sweep(levels, edges, constraints, forward=False)
    positions = _positions(levels)
    group_values = tuple(
        (
            group.id,
            tuple(
                node.id
                for node in sorted(plan.nodes, key=lambda item: (ranks[item.id], positions[item.id], _id_key(item.id)))
                if node.group is not None and node.group.id == group.id
            ),
            constraints.get(group.id, {}).get("rank"),
            constraints.get(group.id, {}).get("order"),
        )
        for group in sorted(plan.groups, key=lambda item: _id_key(item.id))
    )
    node_group_values = tuple(
        (node.id, node.group.id if node.group is not None else None)
        for node in sorted(plan.nodes, key=lambda item: _id_key(item.id))
    )
    return CompiledGraph(
        tuple(sorted(ranks.items(), key=lambda item: _id_key(item[0]))),
        tuple(sorted(positions.items(), key=lambda item: _id_key(item[0]))),
        tuple(identifier for identifier, _, _, is_back in edges if is_back),
        node_group_values,
        group_values,
        edges,
    )


__all__ = [
    "CompiledGraph",
    "compile_graph",
]
