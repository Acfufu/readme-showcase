"""Bounded geometry and text-fit gates for a validated Scene.

This gate deliberately operates on the renderer-neutral Scene values.  It does
not repair coordinates, infer semantic metadata, or measure text with a font.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...pipeline_contracts import ContractError
from .scene import Scene, ScenePrimitive, validate_visual_scene
from .text import _ROLE_LINE_BUDGETS


_MAX_COORDINATE = 20_000
_MAX_PRIMITIVES = 5_000


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _checked_integer(value: Any, context: str) -> int:
    if type(value) is not int or value < 0 or value > _MAX_COORDINATE:
        raise _fail("E_VISUAL_GEOMETRY", f"{context} must be an integer between 0 and {_MAX_COORDINATE}")
    return value


def _validated_scene(value: Any) -> Scene:
    try:
        return validate_visual_scene(value)
    except ContractError as exc:
        # A direct geometry payload can omit a coordinate before it reaches
        # ScenePrimitive; keep the geometry gate's public failure code stable.
        if exc.code == "E_SCHEMA_MISSING_FIELD":
            raise _fail("E_VISUAL_GEOMETRY", str(exc)) from None
        raise


def _rect(value: ScenePrimitive, context: str) -> tuple[int, int, int, int]:
    if value.x is None or value.y is None or value.width is None or value.height is None:
        raise _fail("E_VISUAL_GEOMETRY", f"{context} is missing rectangle coordinates")
    x = _checked_integer(value.x, f"{context}.x")
    y = _checked_integer(value.y, f"{context}.y")
    width = _checked_integer(value.width, f"{context}.width")
    height = _checked_integer(value.height, f"{context}.height")
    return x, y, x + width, y + height


def _point(value: tuple[int, int], context: str) -> tuple[int, int]:
    if not isinstance(value, tuple) or len(value) != 2:
        raise _fail("E_VISUAL_GEOMETRY", f"{context} must contain an x/y pair")
    return (
        _checked_integer(value[0], f"{context}.x"),
        _checked_integer(value[1], f"{context}.y"),
    )


def _point_in_rect(point: tuple[int, int], rectangle: tuple[int, int, int, int]) -> bool:
    return rectangle[0] <= point[0] <= rectangle[2] and rectangle[1] <= point[1] <= rectangle[3]


def _positive_area_overlap(first: tuple[int, int, int, int], second: tuple[int, int, int, int]) -> bool:
    return min(first[2], second[2]) > max(first[0], second[0]) and min(first[3], second[3]) > max(first[1], second[1])


def _orientation(
    first: tuple[int, int],
    second: tuple[int, int],
    third: tuple[int, int],
) -> int:
    value = (second[0] - first[0]) * (third[1] - first[1]) - (second[1] - first[1]) * (third[0] - first[0])
    return (value > 0) - (value < 0)


def _on_segment(
    first: tuple[int, int],
    point: tuple[int, int],
    second: tuple[int, int],
) -> bool:
    return (
        min(first[0], second[0]) <= point[0] <= max(first[0], second[0])
        and min(first[1], second[1]) <= point[1] <= max(first[1], second[1])
    )


def _segments_intersect(
    first_start: tuple[int, int],
    first_end: tuple[int, int],
    second_start: tuple[int, int],
    second_end: tuple[int, int],
) -> bool:
    orientations = (
        _orientation(first_start, first_end, second_start),
        _orientation(first_start, first_end, second_end),
        _orientation(second_start, second_end, first_start),
        _orientation(second_start, second_end, first_end),
    )
    if orientations[0] * orientations[1] < 0 and orientations[2] * orientations[3] < 0:
        return True
    return any(
        orientation == 0 and point_on_segment
        for orientation, point_on_segment in (
            (orientations[0], _on_segment(first_start, second_start, first_end)),
            (orientations[1], _on_segment(first_start, second_end, first_end)),
            (orientations[2], _on_segment(second_start, first_start, second_end)),
            (orientations[3], _on_segment(second_start, first_end, second_end)),
        )
    )


def _segment_touches_rect(
    start: tuple[int, int],
    end: tuple[int, int],
    rectangle: tuple[int, int, int, int],
) -> bool:
    if _point_in_rect(start, rectangle) or _point_in_rect(end, rectangle):
        return True
    left, top, right, bottom = rectangle
    corners = ((left, top), (right, top), (right, bottom), (left, bottom))
    return any(
        _segments_intersect(start, end, corners[index], corners[(index + 1) % 4])
        for index in range(4)
    )


def _edge_points(value: ScenePrimitive) -> tuple[tuple[int, int], ...]:
    if value.kind == "line":
        if value.x1 is None or value.y1 is None or value.x2 is None or value.y2 is None:
            raise _fail("E_VISUAL_GEOMETRY", f"edge {value.id} is missing line coordinates")
        return (
            _point((value.x1, value.y1), f"edge {value.id}.start"),
            _point((value.x2, value.y2), f"edge {value.id}.end"),
        )
    if value.kind != "path" or len(value.points) < 2:
        raise _fail("E_VISUAL_GEOMETRY", f"edge {value.id} must contain at least two points")
    return tuple(_point(point, f"edge {value.id}.points[{index}]") for index, point in enumerate(value.points))


def _check_containment(
    scene: Scene,
    rectangles: Mapping[str, tuple[int, int, int, int]],
) -> None:
    box = tuple(_checked_integer(item, f"scene.view_box[{index}]") for index, item in enumerate(scene.view_box))
    left, top, width, height = box
    right, bottom = left + width, top + height
    if left != 0 or top != 0 or width <= 0 or height <= 0:
        raise _fail("E_VISUAL_GEOMETRY", "scene.view_box must be a positive zero-origin rectangle")

    for primitive in scene.primitives:
        if primitive.kind in {"group", "rect"}:
            geometry = rectangles[primitive.id]
            if geometry[0] < left or geometry[1] < top or geometry[2] > right or geometry[3] > bottom:
                raise _fail("E_VISUAL_GEOMETRY", f"primitive {primitive.id} is outside the scene view_box")
        elif primitive.kind == "line":
            for index, point in enumerate(_edge_points(primitive)):
                if not (left <= point[0] <= right and top <= point[1] <= bottom):
                    raise _fail("E_VISUAL_GEOMETRY", f"edge {primitive.id} point {index} is outside the scene view_box")
        elif primitive.kind == "path":
            for index, point in enumerate(_edge_points(primitive)):
                if not (left <= point[0] <= right and top <= point[1] <= bottom):
                    raise _fail("E_VISUAL_GEOMETRY", f"edge {primitive.id} point {index} is outside the scene view_box")
        else:
            if primitive.x is None or primitive.y is None:
                raise _fail("E_VISUAL_GEOMETRY", f"text {primitive.id} is missing coordinates")
            x = _checked_integer(primitive.x, f"text {primitive.id}.x")
            y = _checked_integer(primitive.y, f"text {primitive.id}.y")
            if not (left <= x <= right and top <= y <= bottom):
                raise _fail("E_VISUAL_GEOMETRY", f"text {primitive.id} is outside the scene view_box")


def _check_group_children(
    scene: Scene,
    rectangles: Mapping[str, tuple[int, int, int, int]],
) -> None:
    by_id = {primitive.id: primitive for primitive in scene.primitives}
    for group in (item for item in scene.primitives if item.kind == "group"):
        parent = rectangles[group.id]
        for child_id in group.children:
            child = by_id.get(child_id)
            if child is None or child.kind not in {"group", "rect"}:
                raise _fail("E_VISUAL_GEOMETRY", f"group {group.id} references an unknown child")
            child_rect = rectangles[child.id]
            if child_rect[0] < parent[0] or child_rect[1] < parent[1] or child_rect[2] > parent[2] or child_rect[3] > parent[3]:
                raise _fail("E_VISUAL_GEOMETRY", f"group {group.id} child {child.id} escapes its bounds")


def _check_node_overlap(nodes: tuple[ScenePrimitive, ...], rectangles: Mapping[str, tuple[int, int, int, int]]) -> None:
    # ponytail: bounded O(n²) pairwise scan; use a sweep-line only if the 5,000-primitive ceiling grows.
    for index, first in enumerate(nodes):
        for second in nodes[index + 1 :]:
            if _positive_area_overlap(rectangles[first.id], rectangles[second.id]):
                raise _fail("E_VISUAL_OVERLAP", f"node rectangles {first.id} and {second.id} overlap")


def _check_edges(
    edges: tuple[ScenePrimitive, ...],
    nodes: tuple[ScenePrimitive, ...],
    rectangles: Mapping[str, tuple[int, int, int, int]],
) -> None:
    for edge in edges:
        points = _edge_points(edge)
        endpoint_ids: list[tuple[str, ...]] = []
        for endpoint_name, point in (("source", points[0]), ("target", points[-1])):
            matches = tuple(node.id for node in nodes if _point_in_rect(point, rectangles[node.id]))
            if len(matches) != 1:
                raise _fail(
                    "E_VISUAL_EDGE_INTERSECTION",
                    f"edge {edge.id} {endpoint_name} must lie in exactly one node rectangle",
                )
            endpoint_ids.append(matches)
        ignored = {endpoint_ids[0][0], endpoint_ids[1][0]}
        for segment_index, (start, end) in enumerate(zip(points, points[1:])):
            for node in nodes:
                if node.id in ignored:
                    continue
                if _segment_touches_rect(start, end, rectangles[node.id]):
                    raise _fail(
                        "E_VISUAL_EDGE_INTERSECTION",
                        f"edge {edge.id} segment {segment_index} contacts non-endpoint node {node.id}",
                    )


def _check_text(
    scene: Scene,
    text_primitives: tuple[ScenePrimitive, ...],
    rectangles: Mapping[str, tuple[int, int, int, int]],
) -> None:
    scene_right = scene.view_box[0] + scene.view_box[2]
    for text in text_primitives:
        budget = _ROLE_LINE_BUDGETS.get(text.role or "")
        if budget is None or len(text.lines) > budget or len(text.lines) != len(text.widths):
            raise _fail("E_VISUAL_TEXT_FIT", f"text {text.id} exceeds its role line budget")
        if any(type(width) is not int or width < 0 or width > _MAX_COORDINATE for width in text.widths):
            raise _fail("E_VISUAL_TEXT_FIT", f"text {text.id} has invalid measured widths")
        owner = rectangles.get(text.source_id)
        owner_left, owner_right = (owner[0], owner[2]) if owner is not None else (scene.view_box[0], scene_right)
        text_x = text.x
        if text_x is None:
            raise _fail("E_VISUAL_TEXT_FIT", f"text {text.id} is missing its x coordinate")
        maximum_width = max(text.widths, default=0)
        if text_x < owner_left or text_x + maximum_width > owner_right:
            raise _fail("E_VISUAL_TEXT_FIT", f"text {text.id} exceeds its owning content width")


def validate_visual_geometry(value: Any) -> Scene:
    """Validate geometry and text-fit invariants and return the immutable Scene."""

    scene = _validated_scene(value)
    if len(scene.primitives) > _MAX_PRIMITIVES:
        raise _fail("E_VISUAL_GEOMETRY", f"scene exceeds the {_MAX_PRIMITIVES}-primitive geometry ceiling")

    rectangle_values = tuple(item for item in scene.primitives if item.kind in {"group", "rect"})
    rectangles = {item.id: _rect(item, f"primitive {item.id}") for item in rectangle_values}
    _check_containment(scene, rectangles)
    _check_group_children(scene, rectangles)

    nodes = tuple(item for item in scene.primitives if item.kind == "rect")
    _check_node_overlap(nodes, rectangles)
    _check_edges(tuple(item for item in scene.primitives if item.kind in {"line", "path"}), nodes, rectangles)
    _check_text(scene, tuple(item for item in scene.primitives if item.kind == "text"), rectangles)
    return scene


__all__ = ["validate_visual_geometry"]
