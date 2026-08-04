"""Plan declared lanes as bounded ELK hierarchy and channel metadata.

The planner owns only semantic ELK constraints.  It never assigns coordinates
or dimensions; ELK remains responsible for geometry after this projection.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from .graph import CompiledGraph
from .normalize import Plan


_SCHEMA_VERSION = 1
_VARIANTS = frozenset({"desktop", "mobile"})
_METRIC_KEYS = frozenset(
    {"canvas", "section", "node", "lane", "label", "width", "min_font_size"}
)
_MIN_FONT_SIZE = {"desktop": 16, "mobile": 24}


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id_key(identifier: str) -> bytes:
    return identifier.encode("utf-8")


def _padding(values: tuple[int, int, int, int]) -> str:
    top, left, bottom, right = values
    return f"[top={top},left={left},bottom={bottom},right={right}]"


def _clone(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clone(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clone(item) for item in value]
    return value


def _walk_children(items: Any) -> tuple[dict[str, Any], ...]:
    if not isinstance(items, list):
        return ()
    result: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _fail("E_VISUAL_SPEC_EDGE", "ELK graph contains an invalid child")
        result.append(item)
        result.extend(_walk_children(item.get("children")))
    return tuple(result)


def _validate_metrics(metrics: Mapping[str, int], variant: str) -> tuple[tuple[str, int], ...]:
    if not isinstance(metrics, Mapping):
        raise _fail("E_SCHEMA_TYPE", "swimlane metrics must be an object")
    keys = set(metrics)
    unknown = sorted(keys - _METRIC_KEYS)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"swimlane metrics contains unknown field: {unknown[0]}")
    missing = sorted(_METRIC_KEYS - keys)
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"swimlane metrics is missing field: {missing[0]}")
    values: dict[str, int] = {}
    for key in sorted(_METRIC_KEYS, key=_id_key):
        value = metrics[key]
        if type(value) is not int or value <= 0:
            raise _fail("E_VISUAL_DETERMINISM", f"swimlane metrics.{key} must be a positive integer")
        values[key] = value
    if variant == "desktop" and values["width"] != 1200:
        raise _fail("E_VISUAL_DETERMINISM", "desktop swimlane width must be 1200")
    if variant == "mobile" and values["width"] > 720:
        raise _fail("E_VISUAL_DETERMINISM", "mobile swimlane width must be at most 720")
    if values["min_font_size"] < _MIN_FONT_SIZE[variant]:
        raise _fail(
            "E_VISUAL_DETERMINISM",
            f"{variant} swimlane min_font_size is below its minimum",
        )
    return tuple((key, values[key]) for key in sorted(values, key=_id_key))


def _validate_plan(plan: Plan) -> tuple[dict[str, Any], dict[str, Any], tuple[Any, ...]]:
    if not isinstance(plan, Plan):
        raise _fail("E_SCHEMA_TYPE", "swimlane planning requires a normalized Plan")
    if plan.schema_version != 1:
        raise _fail("E_SCHEMA_VERSION", "swimlane planning requires Plan schema_version 1")
    lanes: dict[str, Any] = {}
    groups: dict[str, Any] = {}
    nodes: dict[str, Any] = {}
    for lane in plan.lanes:
        if lane.id in lanes or lane.id in groups or lane.id in nodes:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate swimlane element ID: {lane.id}")
        lanes[lane.id] = lane
    for group in plan.groups:
        if group.id in lanes or group.id in groups or group.id in nodes:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate swimlane element ID: {group.id}")
        groups[group.id] = group
    for node in plan.nodes:
        if node.id in lanes or node.id in groups or node.id in nodes:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate swimlane element ID: {node.id}")
        nodes[node.id] = node
        if node.lane is not None and node.lane.id not in lanes:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared lane")
        if node.group is not None and node.group.id not in groups:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared group")

    element_ids = set(nodes) | set(groups) | set(lanes)
    edges: list[Any] = []
    edge_ids: set[str] = set()
    for edge in sorted(plan.edges, key=lambda item: _id_key(item.id)):
        if (
            edge.id in element_ids
            or edge.id in edge_ids
            or edge.source not in nodes
            or edge.target not in nodes
        ):
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} has an undeclared or colliding endpoint")
        edge_ids.add(edge.id)
        edges.append(edge)
    return lanes, groups, tuple(edges)


def _group_node_ids(item: dict[str, Any], node_ids: set[str]) -> tuple[str, ...]:
    found = [child["id"] for child in _walk_children(item.get("children")) if child["id"] in node_ids]
    return tuple(found)


@dataclass(frozen=True, slots=True)
class SwimlanePlan:
    """Immutable swimlane constraints and their ELK projection."""

    variant: str
    metrics: tuple[tuple[str, int], ...]
    graph: CompiledGraph
    lanes: tuple[tuple[str, tuple[str, ...], tuple[int, int, int, int], tuple[int, int, int, int]], ...]
    channels: tuple[tuple[str, str, int], ...]

    def as_dict(self) -> dict[str, Any]:
        """Return a fresh nested ELK graph with channel metadata."""

        metrics = dict(self.metrics)
        base = self.graph.as_dict()
        if base.get("id") != "root" or not isinstance(base.get("children"), list):
            raise _fail("E_VISUAL_SPEC_EDGE", "compiled graph must expose a root children array")
        children = [_clone(item) for item in base["children"]]
        node_ids = {identifier for identifier, _ in self.graph.ranks}
        lane_nodes = {lane_id: set(node_ids_for_lane) for lane_id, node_ids_for_lane, *_ in self.lanes}
        lane_by_node = {
            node_id: lane_id for lane_id, node_ids_for_lane in lane_nodes.items() for node_id in node_ids_for_lane
        }

        group_items = {
            item["id"]: item
            for item in _walk_children(children)
            if item["id"] not in node_ids
        }
        group_lane: dict[str, str | None] = {}
        for group_id, item in group_items.items():
            members = _group_node_ids(item, node_ids)
            member_lanes = {lane_by_node[node_id] for node_id in members if node_id in lane_by_node}
            if member_lanes and any(node_id not in lane_by_node for node_id in members):
                raise _fail(
                    "E_VISUAL_SPEC_EDGE",
                    f"group {group_id} mixes assigned and unassigned swimlane nodes",
                )
            if len(member_lanes) > 1:
                raise _fail(
                    "E_VISUAL_SPEC_EDGE",
                    f"group {group_id} spans multiple swimlanes",
                )
            group_lane[group_id] = next(iter(member_lanes), None)

        child_ids = {item["id"] for item in _walk_children(children)}
        expected_ids = node_ids | set(group_items)
        if child_ids != expected_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", "compiled graph dropped or invented an element")

        channel_by_edge = {edge_id: (kind, offset) for kind, edge_id, offset in self.channels}
        lane_groups: list[dict[str, Any]] = []
        assigned: set[str] = set()
        for lane_index, (lane_id, node_ids_for_lane, header, body) in enumerate(self.lanes):
            lane_children: list[dict[str, Any]] = []
            for item in children:
                item_id = item["id"]
                if item_id in group_lane and group_lane[item_id] == lane_id:
                    lane_children.append(item)
                    assigned.add(item_id)
                elif item_id in lane_by_node and lane_by_node[item_id] == lane_id:
                    node = item
                    if item_id not in {
                        member
                        for group in group_items.values()
                        for member in _group_node_ids(group, node_ids)
                    }:
                        lane_children.append(node)
                        assigned.add(item_id)
            # Channel offsets are represented in body padding below; use the
            # lane record's already-derived body values without recomputing
            # coordinates or mutating graph children.
            lane_group = {
                "id": lane_id,
                "children": lane_children,
                "layoutOptions": {
                    "elk.padding": _padding(
                        (
                            header[0] + body[0],
                            header[1] + body[1],
                            header[2] + body[2],
                            header[3] + body[3],
                        )
                    ),
                    "elk.spacing.nodeNode": str(metrics["node"]),
                    "elk.layered.spacing.nodeNodeBetweenLayers": str(metrics["section"]),
                    "elk.layered.crossingMinimization.positionId": str(lane_index),
                },
                "properties": {
                    "header_padding": _padding(header),
                    "body_padding": _padding(body),
                    "channel_gap": str(body[2] - metrics["node"] * max(1, len(node_ids_for_lane))),
                },
            }
            lane_groups.append(lane_group)

        root_children = [*lane_groups, *[item for item in children if item["id"] not in assigned]]
        result = _clone(base)
        result["children"] = root_children
        root_options = result.get("layoutOptions")
        if not isinstance(root_options, dict):
            root_options = {}
        root_options["elk.spacing.nodeNode"] = str(metrics["canvas"])
        result["layoutOptions"] = root_options
        result_edges: list[dict[str, Any]] = []
        for edge in base.get("edges", []):
            if not isinstance(edge, dict) or not isinstance(edge.get("id"), str):
                raise _fail("E_VISUAL_SPEC_EDGE", "compiled graph contains an invalid edge")
            value = _clone(edge)
            channel = channel_by_edge.get(edge["id"])
            if channel is not None:
                properties = value.get("properties")
                if not isinstance(properties, dict):
                    properties = {}
                properties["channel"] = channel[0]
                properties["channel_offset"] = str(channel[1])
                value["properties"] = properties
            result_edges.append(value)
        result["edges"] = result_edges
        properties = result.get("properties")
        if not isinstance(properties, dict):
            properties = {}
        properties.update(
            {
                "variant": self.variant,
                "canvas_metric": str(metrics["canvas"]),
                "canvas_width": str(metrics["width"]),
                "min_font_size": str(metrics["min_font_size"]),
                "schema_version": str(_SCHEMA_VERSION),
                "channel_ids": ",".join(edge_id for _, edge_id, _ in self.channels),
            }
        )
        result["properties"] = properties
        return result

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def plan_swimlanes(
    plan: Plan,
    graph: CompiledGraph,
    metrics: Mapping[str, int],
    variant: str,
) -> SwimlanePlan:
    """Build deterministic lane groups and loop/skip channel constraints."""

    if not isinstance(variant, str) or variant not in _VARIANTS:
        raise _fail("E_SCHEMA_VALUE", "swimlane variant must be desktop or mobile")
    lane_values, _, edges = _validate_plan(plan)
    if not isinstance(graph, CompiledGraph):
        raise _fail("E_SCHEMA_TYPE", "swimlane planning requires a CompiledGraph")
    metric_values = _validate_metrics(metrics, variant)
    metric_map = dict(metric_values)
    node_ids = {node.id for node in plan.nodes}
    if {identifier for identifier, _ in graph.ranks} != node_ids:
        raise _fail("E_VISUAL_SPEC_EDGE", "compiled graph does not preserve every Plan node")
    graph_edge_by_id = {edge_id: (source, target, is_back) for edge_id, source, target, is_back in graph.edges}
    if set(graph_edge_by_id) != {edge.id for edge in edges}:
        raise _fail("E_VISUAL_SPEC_EDGE", "compiled graph does not preserve every Plan edge")
    for edge in edges:
        compiled = graph_edge_by_id.get(edge.id)
        if compiled is None or compiled[0] != edge.source or compiled[1] != edge.target:
            raise _fail("E_VISUAL_SPEC_EDGE", f"compiled edge {edge.id} has an invalid endpoint")

    ranks = dict(graph.ranks)
    lane_for_node = {
        node.id: node.lane.id if node.lane is not None else None
        for node in plan.nodes
    }
    channels: list[tuple[str, str, int]] = []
    for ordinal, edge in enumerate(edges):
        source_lane = lane_for_node[edge.source]
        target_lane = lane_for_node[edge.target]
        is_loop = edge.kind == "back" or edge.is_back_edge or graph_edge_by_id[edge.id][2]
        is_skip = not is_loop and (
            source_lane != target_lane
            or ranks.get(edge.target, 0) - ranks.get(edge.source, 0) > 1
        )
        if is_loop or is_skip:
            channels.append(("loop" if is_loop else "skip", edge.id, metric_map["section"] * (ordinal + 1)))

    lane_records: list[tuple[str, tuple[str, ...], tuple[int, int, int, int], tuple[int, int, int, int]]] = []
    channels_by_lane: dict[str, list[int]] = {lane_id: [] for lane_id in lane_values}
    for kind, edge_id, offset in channels:
        edge = next(item for item in edges if item.id == edge_id)
        for lane_id in (lane_for_node[edge.source], lane_for_node[edge.target]):
            if lane_id is not None:
                channels_by_lane[lane_id].append(offset)

    for lane in sorted(lane_values.values(), key=lambda item: _id_key(item.id)):
        members = tuple(
            node.id
            for node in sorted(plan.nodes, key=lambda item: _id_key(item.id))
            if node.lane is not None and node.lane.id == lane.id
        )
        channel_gap = max(channels_by_lane[lane.id], default=0)
        header_pad = metric_map["label"] * max(1, len(lane.label.encode("utf-8")))
        header = (
            header_pad,
            metric_map["lane"],
            header_pad,
            metric_map["lane"],
        )
        body = (
            metric_map["node"] * max(1, len(members)),
            metric_map["node"],
            metric_map["node"] * max(1, len(members)) + channel_gap,
            metric_map["node"],
        )
        lane_records.append((lane.id, members, header, body))

    result = SwimlanePlan(variant, metric_values, graph, tuple(lane_records), tuple(channels))
    # Verify that no plan edge or node was silently omitted before returning a
    # serializable result.  The projection repeats this check defensively.
    projection = result.as_dict()
    projected_edges = {edge["id"] for edge in projection.get("edges", [])}
    if projected_edges != {edge.id for edge in edges}:
        raise _fail("E_VISUAL_SPEC_EDGE", "swimlane projection dropped an edge")
    projected_nodes = {
        item["id"]
        for item in _walk_children(projection.get("children"))
        if item["id"] in node_ids
    }
    if projected_nodes != node_ids:
        raise _fail("E_VISUAL_SPEC_EDGE", "swimlane projection dropped a node")
    return result


__all__ = ["SwimlanePlan", "plan_swimlanes"]
