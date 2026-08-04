"""Closed, deterministic Scene v1 values.

The scene boundary is deliberately boring: Plan semantics and text-fit values
provide the labels and provenance, while the already validated ELK snapshot
provides every coordinate.  No renderer or layout process belongs here.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, ClassVar

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.locale import parse_locale
from .elk_backend import (
    ElkGeometryResult,
    _ELK_VERSION,
    _MODULE_SHA256,
    _NODE_VERSION,
    _PACKAGE_SHA256,
)
from .normalize import Plan
from .text import TextFitResult
from .theme import Theme


SCENE_SCHEMA_VERSION = 1
SCENE_LAYERS = ("containers", "edges", "nodes", "labels")
_INTENT_KEY = "__scene_intent__"
_LAYER_INDEX = {name: index for index, name in enumerate(SCENE_LAYERS)}
_VARIANTS = frozenset({"desktop", "mobile"})
_PRIMITIVE_KINDS = frozenset({"group", "rect", "line", "path", "text"})
_TEXT_ROLES = frozenset({"core", "title", "node", "label", "edge", "group", "lane", "caption", "description"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")
_MAX_COORDINATE = 20_000

_SCENE_FIELDS = frozenset(
    {
        "schema_version",
        "locale",
        "variant",
        "view_box",
        "source_spec_sha256",
        "theme_sha256",
        "backend",
        "layers",
        "primitives",
    }
)
_COMMON_PRIMITIVE_FIELDS = frozenset({"kind", "id", "source_id", "evidence_ids", "layer", "z"})
_PRIMITIVE_FIELDS = {
    "group": _COMMON_PRIMITIVE_FIELDS | {"x", "y", "width", "height", "children"},
    "rect": _COMMON_PRIMITIVE_FIELDS | {"x", "y", "width", "height"},
    "line": _COMMON_PRIMITIVE_FIELDS | {"x1", "y1", "x2", "y2"},
    "path": _COMMON_PRIMITIVE_FIELDS | {"points"},
    "text": _COMMON_PRIMITIVE_FIELDS | {"x", "y", "text", "lines", "widths", "font_size", "role"},
}
_GEOMETRY_FIELDS = frozenset({"schema_version", "engine", "canvas", "groups", "nodes", "ports", "edges"})
_RECT_FIELDS = frozenset({"id", "parent_id", "x", "y", "width", "height"})
_EDGE_FIELDS = frozenset({"id", "sections"})
_SECTION_FIELDS = frozenset({"start", "bends", "end"})
_POINT_FIELDS = frozenset({"x", "y"})
_CANVAS_FIELDS = frozenset({"width", "height"})
_ENGINE_REQUIRED = frozenset(
    {
        "engine_kind",
        "package_name",
        "package_version",
        "package_sha256",
        "module_sha256",
        "node_version",
        "renderer_sha256",
    }
)
_PINNED_ENGINE = {
    "engine_kind": "elk",
    "package_name": "elkjs",
    "package_version": _ELK_VERSION,
    "package_sha256": _PACKAGE_SHA256,
    "module_sha256": _MODULE_SHA256,
    "node_version": _NODE_VERSION,
}


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must be a non-empty ID")
    if unicodedata.normalize("NFC", value) != value:
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must be NFC-normalized")
    if any(ord(item) < 0x20 for item in value):
        raise _fail("E_VISUAL_SPEC_ID", f"{context} must not contain control characters")
    return value


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _integer(value: Any, context: str, *, maximum: int = _MAX_COORDINATE) -> int:
    if type(value) is not int:
        raise _fail("E_VISUAL_GEOMETRY", f"{context} must be an integer")
    if value < 0 or value > maximum:
        raise _fail("E_VISUAL_GEOMETRY", f"{context} must be between 0 and {maximum}")
    return value


def _hash(value: Any, context: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context} must be a lowercase SHA-256 digest")
    return value


def _evidence(value: Any, context: str, *, required: bool = False) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)) or isinstance(value, (str, bytes)):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} must be an array")
    result = tuple(value)
    if required and not result:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} requires one or more Evidence IDs")
    if any(not isinstance(item, str) or _EVIDENCE_ID.fullmatch(item) is None for item in result):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} must contain Evidence v2 IDs")
    if result != tuple(sorted(set(result), key=_id_key)):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{context} must be byte-sorted and unique")
    return result


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _closed(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    missing = sorted(fields - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return dict(value)


def _geometry_closed(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    try:
        return _closed(value, fields, context)
    except ContractError as exc:
        if exc.code in {"E_SCHEMA_TYPE", "E_SCHEMA_UNKNOWN_FIELD", "E_SCHEMA_MISSING_FIELD"}:
            raise _fail("E_VISUAL_GEOMETRY", str(exc)) from None
        raise


def _view_box(value: Any, context: str = "view_box") -> tuple[int, int, int, int]:
    if isinstance(value, Mapping):
        raw = _closed(value, frozenset({"x", "y", "width", "height"}), context)
        return tuple(_integer(raw[name], f"{context}.{name}") for name in ("x", "y", "width", "height"))  # type: ignore[return-value]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)) and len(value) == 4:
        return tuple(_integer(item, f"{context}[{index}]") for index, item in enumerate(value))  # type: ignore[return-value]
    raise _fail("E_VISUAL_GEOMETRY", f"{context} must contain x, y, width, and height")


def _primitive_projection(value: "ScenePrimitive") -> dict[str, Any]:
    kind = value.kind
    if kind not in _PRIMITIVE_KINDS:
        raise _fail("E_SCHEMA_VALUE", f"unsupported Scene primitive kind: {kind}")
    identifier = _id(value.id, "primitive.id")
    source_id = _id(value.source_id, f"primitive {identifier}.source_id")
    evidence_ids = _evidence(value.evidence_ids, f"primitive {identifier}.evidence_ids")
    if not isinstance(value.layer, str) or value.layer not in _LAYER_INDEX:
        raise _fail("E_SCHEMA_VALUE", f"primitive {identifier}.layer is unsupported")
    z = _integer(value.z, f"primitive {identifier}.z")
    inactive_defaults = {
        "x": None,
        "y": None,
        "width": None,
        "height": None,
        "children": (),
        "x1": None,
        "y1": None,
        "x2": None,
        "y2": None,
        "points": (),
        "text": None,
        "lines": (),
        "widths": (),
        "font_size": None,
        "role": None,
    }
    active = {
        "group": {"x", "y", "width", "height", "children"},
        "rect": {"x", "y", "width", "height"},
        "line": {"x1", "y1", "x2", "y2"},
        "path": {"points"},
        "text": {"x", "y", "text", "lines", "widths", "font_size", "role"},
    }[kind]
    for name, default in inactive_defaults.items():
        if name not in active and getattr(value, name) != default:
            raise _fail("E_SCHEMA_VALUE", f"primitive {identifier}.{name} is not valid for {kind}")
    result: dict[str, Any] = {
        "kind": kind,
        "id": identifier,
        "source_id": source_id,
        "evidence_ids": list(evidence_ids),
        "layer": value.layer,
        "z": z,
    }
    if kind in {"group", "rect"}:
        for name in ("x", "y", "width", "height"):
            result[name] = _integer(getattr(value, name), f"primitive {identifier}.{name}")
        if kind == "group":
            children = value.children
            if not isinstance(children, tuple):
                children = tuple(children) if isinstance(children, Sequence) and not isinstance(children, (str, bytes)) else ()
            if any(not isinstance(item, str) for item in children):
                raise _fail("E_VISUAL_SPEC_ID", f"primitive {identifier}.children must contain IDs")
            checked = tuple(_id(item, f"primitive {identifier}.children[]") for item in children)
            if checked != tuple(sorted(set(checked), key=_id_key)):
                raise _fail("E_VISUAL_SPEC_ID", f"primitive {identifier}.children must be byte-sorted and unique")
            result["children"] = list(checked)
    elif kind == "line":
        for name in ("x1", "y1", "x2", "y2"):
            result[name] = _integer(getattr(value, name), f"primitive {identifier}.{name}")
    elif kind == "path":
        raw_points = value.points
        if not isinstance(raw_points, tuple) or len(raw_points) < 2:
            raise _fail("E_VISUAL_GEOMETRY", f"primitive {identifier}.points must contain at least two points")
        points: list[dict[str, int]] = []
        for index, point in enumerate(raw_points):
            if not isinstance(point, tuple) or len(point) != 2:
                raise _fail("E_VISUAL_GEOMETRY", f"primitive {identifier}.points[{index}] is invalid")
            points.append({"x": _integer(point[0], f"primitive {identifier}.points[{index}].x"), "y": _integer(point[1], f"primitive {identifier}.points[{index}].y")})
        result["points"] = points
    else:
        if not isinstance(value.text, str) or not value.text or "\x00" in value.text:
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.text must be visible text")
        if unicodedata.normalize("NFC", value.text) != value.text:
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.text must be NFC-normalized")
        result["x"] = _integer(value.x, f"primitive {identifier}.x")
        result["y"] = _integer(value.y, f"primitive {identifier}.y")
        lines = value.lines if isinstance(value.lines, tuple) else tuple(value.lines)
        widths = value.widths if isinstance(value.widths, tuple) else tuple(value.widths)
        if not lines or len(lines) != len(widths) or any(not isinstance(item, str) or not item for item in lines):
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier} has invalid text lines")
        if any(unicodedata.normalize("NFC", item) != item for item in lines):
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.lines must be NFC-normalized")
        if any(type(item) is not int or item < 0 or item > _MAX_COORDINATE for item in widths):
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.widths must be non-negative integers")
        if not isinstance(value.role, str) or value.role not in _TEXT_ROLES:
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.role is unsupported")
        font_size = _integer(value.font_size, f"primitive {identifier}.font_size", maximum=_MAX_COORDINATE)
        if font_size < 1:
            raise _fail("E_VISUAL_TEXT_FIT", f"primitive {identifier}.font_size must be positive")
        result.update(
            {
                "text": value.text,
                "lines": list(lines),
                "widths": list(widths),
                "font_size": font_size,
                "role": value.role,
            }
        )
        _evidence(evidence_ids, f"primitive {identifier}.evidence_ids", required=True)
    return result


@dataclass(frozen=True, slots=True)
class ScenePrimitive:
    """One closed Scene primitive with semantic provenance."""

    kind: str
    id: str
    source_id: str
    evidence_ids: tuple[str, ...]
    layer: str
    z: int
    x: int | None = None
    y: int | None = None
    width: int | None = None
    height: int | None = None
    children: tuple[str, ...] = ()
    x1: int | None = None
    y1: int | None = None
    x2: int | None = None
    y2: int | None = None
    points: tuple[tuple[int, int], ...] = ()
    text: str | None = None
    lines: tuple[str, ...] = ()
    widths: tuple[int, ...] = ()
    font_size: int | None = None
    role: str | None = None

    def __post_init__(self) -> None:
        _primitive_projection(self)
        object.__setattr__(self, "evidence_ids", tuple(self.evidence_ids))
        object.__setattr__(self, "children", tuple(self.children))
        object.__setattr__(self, "points", tuple(tuple(point) for point in self.points))
        object.__setattr__(self, "lines", tuple(self.lines))
        object.__setattr__(self, "widths", tuple(self.widths))

    def as_dict(self) -> dict[str, Any]:
        return _primitive_projection(self)


def _primitive_from_mapping(value: Any, context: str) -> ScenePrimitive:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    kind = value.get("kind")
    if not isinstance(kind, str) or kind not in _PRIMITIVE_KINDS:
        raise _fail("E_SCHEMA_VALUE", f"{context}.kind is unsupported")
    raw = _closed(value, _PRIMITIVE_FIELDS[kind], context)
    kwargs: dict[str, Any] = {name: raw[name] for name in _COMMON_PRIMITIVE_FIELDS}
    if kind in {"group", "rect"}:
        kwargs.update({name: raw[name] for name in ("x", "y", "width", "height")})
        if kind == "group":
            kwargs["children"] = tuple(raw["children"])
    elif kind == "line":
        kwargs.update({name: raw[name] for name in ("x1", "y1", "x2", "y2")})
    elif kind == "path":
        points = raw["points"]
        if not isinstance(points, list):
            raise _fail("E_VISUAL_GEOMETRY", f"{context}.points must be an array")
        parsed_points: list[tuple[int, int]] = []
        for index, point in enumerate(points):
            point_raw = _geometry_closed(point, _POINT_FIELDS, f"{context}.points[{index}]")
            parsed_points.append((point_raw["x"], point_raw["y"]))
        kwargs["points"] = tuple(parsed_points)
    else:
        kwargs.update({name: raw[name] for name in ("x", "y", "text", "lines", "widths", "font_size", "role")})
    return ScenePrimitive(**kwargs)


def _backend(value: Any) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "scene.backend must be an object")
    raw = dict(value)
    if set(raw) != _ENGINE_REQUIRED:
        raise _fail("E_VISUAL_FINGERPRINT", "scene.backend engine identity fields are not closed")
    for name, expected in _PINNED_ENGINE.items():
        if raw.get(name) != expected:
            raise _fail("E_VISUAL_FINGERPRINT", f"scene.backend.{name} is not the pinned ELK identity")
    for name in _ENGINE_REQUIRED:
        item = raw[name]
        if not isinstance(item, str) or not item:
            raise _fail("E_VISUAL_FINGERPRINT", f"scene.backend.{name} must be non-empty text")
    for name in ("package_sha256", "module_sha256", "renderer_sha256"):
        _hash(raw[name], f"scene.backend.{name}")
    return _freeze(raw)


def _validate_scene_parts(
    schema_version: Any,
    locale: Any,
    variant: Any,
    view_box: Any,
    source_spec_sha256: Any,
    theme_sha256: Any,
    backend: Any,
    layers: Any,
    primitives: Any,
) -> tuple[int, str, str, tuple[int, int, int, int], str, str, MappingProxyType, tuple[str, ...], tuple[ScenePrimitive, ...]]:
    if type(schema_version) is not int or schema_version != SCENE_SCHEMA_VERSION:
        raise _fail("E_SCHEMA_VERSION", "scene requires schema_version 1")
    if not isinstance(locale, str):
        raise _fail("E_SCHEMA_TYPE", "scene.locale must be text")
    parse_locale(locale, "scene.locale")
    if not isinstance(variant, str) or variant not in _VARIANTS:
        raise _fail("E_SCHEMA_VALUE", "scene.variant must be desktop or mobile")
    box = _view_box(view_box)
    if box[0] != 0 or box[1] != 0:
        raise _fail("E_VISUAL_GEOMETRY", "scene.view_box origin must be zero")
    if (variant == "desktop" and box[2] != 1200) or (variant == "mobile" and not 1 <= box[2] <= 720):
        raise _fail("E_VISUAL_FINGERPRINT", f"scene.view_box width is invalid for {variant}")
    if box[3] <= 0:
        raise _fail("E_VISUAL_GEOMETRY", "scene.view_box height must be positive")
    source_hash = _hash(source_spec_sha256, "scene.source_spec_sha256")
    theme_hash = _hash(theme_sha256, "scene.theme_sha256")
    backend_value = _backend(backend)
    if not isinstance(layers, (tuple, list)) or tuple(layers) != SCENE_LAYERS:
        raise _fail("E_SCHEMA_VALUE", "scene.layers must equal the closed layer order")
    if not isinstance(primitives, (tuple, list)) or isinstance(primitives, (str, bytes)):
        raise _fail("E_SCHEMA_TYPE", "scene.primitives must be an array")
    values = tuple(
        item if isinstance(item, ScenePrimitive) else _primitive_from_mapping(item, f"scene.primitives[{index}]")
        for index, item in enumerate(primitives)
    )
    _validate_primitive_order(values, box)
    minimum_font = 16 if variant == "desktop" else 24
    if any(item.kind == "text" and (item.font_size is None or item.font_size < minimum_font) for item in values):
        raise _fail("E_VISUAL_FINGERPRINT", f"scene text is bound to the wrong {variant} font policy")
    return SCENE_SCHEMA_VERSION, locale, variant, box, source_hash, theme_hash, backend_value, SCENE_LAYERS, values


def _validate_primitive_order(values: tuple[ScenePrimitive, ...], box: tuple[int, int, int, int]) -> None:
    identifiers = [item.id for item in values]
    if len(identifiers) != len(set(identifiers)):
        raise _fail("E_VISUAL_SPEC_ID", "scene primitive IDs must be unique")
    expected_order = tuple(sorted(values, key=lambda item: (_LAYER_INDEX[item.layer], item.z, _id_key(item.id))))
    if values != expected_order:
        raise _fail("E_VISUAL_DETERMINISM", "scene primitives must be ordered by layer, z, and UTF-8 ID")
    primitive_by_id = {item.id: item for item in values}
    rects: dict[str, tuple[int, int, int, int]] = {}
    source_primitives: dict[str, ScenePrimitive] = {}
    text_sources: set[str] = set()
    for item in values:
        if item.kind != "text":
            if item.id != item.source_id:
                raise _fail("E_VISUAL_SPEC_ID", f"primitive {item.id} must use its semantic source ID")
            if item.source_id in source_primitives:
                raise _fail("E_VISUAL_SPEC_ID", f"duplicate primitive source ID: {item.source_id}")
            source_primitives[item.source_id] = item
            if item.kind in {"group", "rect"}:
                _evidence(item.evidence_ids, f"primitive {item.id}.evidence_ids", required=True)
        else:
            if item.source_id in text_sources:
                raise _fail("E_VISUAL_SPEC_ID", f"duplicate text source ID: {item.source_id}")
            text_sources.add(item.source_id)
        if item.kind in {"group", "rect"}:
            rect = (item.x or 0, item.y or 0, item.width or 0, item.height or 0)
            rects[item.source_id] = rect
            if rect[0] + rect[2] > box[2] or rect[1] + rect[3] > box[3]:
                raise _fail("E_VISUAL_GEOMETRY", f"primitive {item.id} is outside the scene view_box")
        elif item.kind == "line":
            points = ((item.x1, item.y1), (item.x2, item.y2))
            if any(point is None or point[0] > box[2] or point[1] > box[3] for point in points):
                raise _fail("E_VISUAL_GEOMETRY", f"primitive {item.id} is outside the scene view_box")
        elif item.kind == "path":
            if any(point[0] > box[2] or point[1] > box[3] for point in item.points):
                raise _fail("E_VISUAL_GEOMETRY", f"primitive {item.id} is outside the scene view_box")
        elif item.x is not None and (item.x > box[2] or item.y is None or item.y > box[3]):
            raise _fail("E_VISUAL_GEOMETRY", f"primitive {item.id} is outside the scene view_box")
    for item in values:
        if item.kind != "text":
            continue
        if item.source_id != _INTENT_KEY and item.source_id not in source_primitives:
            raise _fail("E_VISUAL_SPEC_ID", f"text {item.id} references an undeclared source")
        if item.source_id in source_primitives and item.evidence_ids != source_primitives[item.source_id].evidence_ids:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"text {item.id} changed its source Evidence binding")
    for item in values:
        if item.kind != "group":
            continue
        parent_rect = rects.get(item.source_id)
        if parent_rect is None:
            raise _fail("E_VISUAL_GEOMETRY", f"group {item.id} has no geometry")
        for child_id in item.children:
            child = primitive_by_id.get(child_id)
            if child is None or child.kind not in {"group", "rect"}:
                raise _fail("E_VISUAL_GEOMETRY", f"group {item.id} references an unknown child")
            child_rect = rects.get(child.source_id)
            if child_rect is None:
                raise _fail("E_VISUAL_GEOMETRY", f"group {item.id} child has no geometry")
            if (
                child_rect[0] < parent_rect[0]
                or child_rect[1] < parent_rect[1]
                or child_rect[0] + child_rect[2] > parent_rect[0] + parent_rect[2]
                or child_rect[1] + child_rect[3] > parent_rect[1] + parent_rect[3]
            ):
                raise _fail("E_VISUAL_GEOMETRY", f"group {item.id} child escapes its bounds")


@dataclass(frozen=True, slots=True)
class Scene:
    """Immutable Scene v1 projection."""

    schema_version: int
    locale: str
    variant: str
    view_box: tuple[int, int, int, int]
    source_spec_sha256: str
    theme_sha256: str
    backend: Mapping[str, Any]
    layers: tuple[str, ...]
    primitives: tuple[ScenePrimitive, ...]

    schema: ClassVar[int] = SCENE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        values = _validate_scene_parts(
            self.schema_version,
            self.locale,
            self.variant,
            self.view_box,
            self.source_spec_sha256,
            self.theme_sha256,
            self.backend,
            self.layers,
            self.primitives,
        )
        for name, value in zip(
            ("schema_version", "locale", "variant", "view_box", "source_spec_sha256", "theme_sha256", "backend", "layers", "primitives"),
            values,
        ):
            object.__setattr__(self, name, value)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "locale": self.locale,
            "variant": self.variant,
            "view_box": {"x": self.view_box[0], "y": self.view_box[1], "width": self.view_box[2], "height": self.view_box[3]},
            "source_spec_sha256": self.source_spec_sha256,
            "theme_sha256": self.theme_sha256,
            "backend": _thaw(self.backend),
            "layers": list(self.layers),
            "primitives": [item.as_dict() for item in self.primitives],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def validate_visual_scene(value: Any) -> Scene:
    """Validate a Scene object or closed JSON projection and return Scene."""

    if isinstance(value, Scene):
        _validate_scene_parts(
            value.schema_version,
            value.locale,
            value.variant,
            value.view_box,
            value.source_spec_sha256,
            value.theme_sha256,
            value.backend,
            value.layers,
            value.primitives,
        )
        return value
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "visual scene must be an object")
    raw = _closed(value, _SCENE_FIELDS, "scene")
    return Scene(
        raw["schema_version"],
        raw["locale"],
        raw["variant"],
        _view_box(raw["view_box"]),
        raw["source_spec_sha256"],
        raw["theme_sha256"],
        raw["backend"],
        tuple(raw["layers"]),
        tuple(raw["primitives"]),
    )


def _geometry_payload(value: Any) -> Mapping[str, Any]:
    if isinstance(value, ElkGeometryResult):
        return value.geometry
    if not isinstance(value, Mapping):
        raise _fail("E_VISUAL_GEOMETRY", "scene geometry must be an ELK geometry result")
    if "geometry" in value:
        if set(value) - {"geometry", "metadata"}:
            raise _fail("E_VISUAL_GEOMETRY", "scene geometry wrapper is not closed")
        value = value["geometry"]
    if not isinstance(value, Mapping):
        raise _fail("E_VISUAL_GEOMETRY", "scene geometry payload must be an object")
    return value


def _geometry(value: Any) -> tuple[MappingProxyType, tuple[int, int], dict[str, dict[str, Any]], dict[str, dict[str, Any]], list[dict[str, Any]]]:
    raw = _geometry_closed(value, _GEOMETRY_FIELDS, "geometry")
    if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
        raise _fail("E_VISUAL_GEOMETRY", "geometry.schema_version must be 1")
    backend = _backend(raw["engine"])
    canvas = _geometry_closed(raw["canvas"], _CANVAS_FIELDS, "geometry.canvas")
    dimensions = (_integer(canvas["width"], "geometry.canvas.width"), _integer(canvas["height"], "geometry.canvas.height"))
    groups = _geometry_rects(raw["groups"], "geometry.groups")
    nodes = _geometry_rects(raw["nodes"], "geometry.nodes")
    ports = raw["ports"]
    if not isinstance(ports, list):
        raise _fail("E_VISUAL_GEOMETRY", "geometry.ports must be an array")
    if ports:
        raise _fail("E_VISUAL_GEOMETRY", "geometry ports cannot be represented by Scene v1")
    edges = raw["edges"]
    if not isinstance(edges, list):
        raise _fail("E_VISUAL_GEOMETRY", "geometry.edges must be an array")
    for index, edge in enumerate(edges):
        item = _geometry_closed(edge, _EDGE_FIELDS, f"geometry.edges[{index}]")
        if item["id"] != f"edge-{index}":
            raise _fail("E_VISUAL_GEOMETRY", "geometry edge IDs must be contiguous edge-N values")
        sections = item["sections"]
        if not isinstance(sections, list) or not sections:
            raise _fail("E_VISUAL_GEOMETRY", f"geometry.edges[{index}].sections is incomplete")
        for section_index, section in enumerate(sections):
            section_raw = _geometry_closed(section, _SECTION_FIELDS, f"geometry.edges[{index}].sections[{section_index}]")
            if not isinstance(section_raw["bends"], list):
                raise _fail("E_VISUAL_GEOMETRY", "geometry edge bends must be an array")
            for point_name, point in (("start", section_raw["start"]), ("end", section_raw["end"]), *[("bend", bend) for bend in section_raw["bends"]]):
                point_raw = _geometry_closed(point, _POINT_FIELDS, f"geometry.edges[{index}].{point_name}")
                _integer(point_raw["x"], f"geometry.edges[{index}].{point_name}.x")
                _integer(point_raw["y"], f"geometry.edges[{index}].{point_name}.y")
    return backend, dimensions, groups, nodes, edges


def _geometry_rects(value: Any, context: str) -> dict[str, dict[str, Any]]:
    if not isinstance(value, list):
        raise _fail("E_VISUAL_GEOMETRY", f"{context} must be an array")
    result: dict[str, dict[str, Any]] = {}
    ordered: list[str] = []
    for index, item in enumerate(value):
        raw = _geometry_closed(item, _RECT_FIELDS, f"{context}[{index}]")
        identifier = _id(raw["id"], f"{context}[{index}].id")
        if identifier in result:
            raise _fail("E_VISUAL_SPEC_ID", f"duplicate geometry ID: {identifier}")
        if raw["parent_id"] is not None:
            _id(raw["parent_id"], f"{context}[{index}].parent_id")
        for name in ("x", "y", "width", "height"):
            _integer(raw[name], f"{context}[{index}].{name}")
        result[identifier] = raw
        ordered.append(identifier)
    if ordered != sorted(ordered, key=_id_key):
        raise _fail("E_VISUAL_GEOMETRY", f"{context} IDs must be UTF-8 sorted")
    return result


def _plan_values(plan: Any) -> tuple[tuple[Any, ...], tuple[Any, ...], tuple[Any, ...], tuple[Any, ...], tuple[Any, ...]]:
    if not isinstance(plan, Plan) or plan.schema_version != 1:
        raise _fail("E_SCHEMA_TYPE", "scene builder requires a normalized Plan")
    if plan.intent.kind not in {"architecture", "flow", "swimlane", "sequence"}:
        raise _fail("E_SCHEMA_VALUE", "Plan intent kind is unsupported")
    all_items = (*plan.groups, *plan.lanes, *plan.nodes, *plan.edges)
    ids = [item.id for item in all_items]
    if len(ids) != len(set(ids)):
        raise _fail("E_VISUAL_SPEC_ID", "Plan semantic IDs must be globally unique")
    for name, values in (("groups", plan.groups), ("lanes", plan.lanes), ("nodes", plan.nodes), ("edges", plan.edges)):
        identifiers = tuple(item.id for item in values)
        if identifiers != tuple(sorted(identifiers, key=_id_key)):
            raise _fail("E_VISUAL_DETERMINISM", f"Plan.{name} must be UTF-8 sorted")
    if _INTENT_KEY in ids:
        raise _fail("E_VISUAL_SPEC_ID", f"Plan ID is reserved: {_INTENT_KEY}")
    if any(item.label is None or not item.evidence_ids for item in (*plan.groups, *plan.lanes, *plan.nodes)):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", "Scene containers and nodes require visible Evidence-bound labels")
    if any(item.label is not None and not item.evidence_ids for item in plan.edges):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", "visible Plan labels require Evidence IDs")
    node_ids = {item.id for item in plan.nodes}
    for edge in plan.edges:
        if edge.source not in node_ids or edge.target not in node_ids:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} references an undeclared node")
        if edge.label is None and edge.evidence_ids:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"edge {edge.id} carries Evidence without a visible label")
    if plan.intent.label is None or not plan.intent.evidence_ids:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", "Plan intent requires a visible Evidence-bound label")
    return plan.groups, plan.lanes, plan.nodes, plan.edges, (plan.intent,)


def _text_map(value: Any, variant: str, labels: Mapping[str, str]) -> dict[str, TextFitResult]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "scene text_fits must be an object keyed by source ID")
    result: dict[str, TextFitResult] = {}
    for identifier, fit in value.items():
        _id(identifier, "text_fits key")
        if identifier not in labels:
            raise _fail("E_VISUAL_SPEC_ID", f"text_fits contains undeclared ID: {identifier}")
        if not isinstance(fit, TextFitResult) or fit.status != "fit":
            raise _fail("E_VISUAL_TEXT_FIT", f"text fit is not a successful result: {identifier}")
        if fit.variant != variant or fit.text != labels[identifier]:
            raise _fail("E_VISUAL_FINGERPRINT", f"text fit is stale or bound to the wrong variant: {identifier}")
        result[identifier] = fit
    missing = sorted(set(labels) - set(result), key=_id_key)
    if missing:
        raise _fail("E_VISUAL_TEXT_FIT", f"text fit is missing for visible label: {missing[0]}")
    return result


def _edge_points(item: Mapping[str, Any]) -> tuple[tuple[int, int], ...]:
    points: list[tuple[int, int]] = []
    for section in item["sections"]:
        start = section["start"]
        bends = section["bends"]
        end = section["end"]
        points.extend([(start["x"], start["y"]), *((bend["x"], bend["y"]) for bend in bends), (end["x"], end["y"])])
    if len(points) < 2:
        raise _fail("E_VISUAL_GEOMETRY", "edge geometry must contain two or more points")
    return tuple(points)


def _point_in_rect(point: tuple[int, int], rect: Mapping[str, Any]) -> bool:
    return rect["x"] <= point[0] <= rect["x"] + rect["width"] and rect["y"] <= point[1] <= rect["y"] + rect["height"]


def build_scene(
    plan: Plan,
    theme: Theme,
    text_fits: Mapping[str, TextFitResult],
    geometry: ElkGeometryResult | Mapping[str, Any],
    variant: str,
) -> Scene:
    """Merge one independently laid-out variant into a closed Scene."""

    groups, lanes, nodes, edges, intents = _plan_values(plan)
    if not isinstance(theme, Theme):
        raise _fail("E_SCHEMA_TYPE", "scene builder requires a resolved Theme")
    if not isinstance(variant, str) or variant not in _VARIANTS or variant not in plan.variants:
        raise _fail("E_VISUAL_FINGERPRINT", "scene variant is not declared by the Plan")
    if variant not in theme.variants:
        raise _fail("E_VISUAL_FINGERPRINT", "scene variant is not declared by the Theme")
    backend, canvas, geometry_groups, geometry_nodes, geometry_edges = _geometry(_geometry_payload(geometry))
    view_width = theme.variants[variant]["width"]
    if variant == "mobile" and canvas[0] > 720:
        raise _fail("E_VISUAL_FINGERPRINT", "mobile geometry is bound to a desktop canvas")
    if canvas[0] > view_width:
        raise _fail("E_VISUAL_GEOMETRY", f"geometry canvas exceeds {variant} view width")

    semantic_containers = (*groups, *lanes)
    container_ids = {item.id for item in semantic_containers}
    node_ids = {item.id for item in nodes}
    if set(geometry_groups) != container_ids or set(geometry_nodes) != node_ids:
        raise _fail("E_VISUAL_GEOMETRY", "geometry must preserve exactly every Plan container and node")
    if len(geometry_edges) != len(edges):
        raise _fail("E_VISUAL_GEOMETRY", "geometry edge count does not match the Plan")

    lane_for_node = {node.id: node.lane.id if node.lane is not None else None for node in nodes}
    group_lane: dict[str, str | None] = {}
    for group in groups:
        member_lanes = {lane_for_node[node.id] for node in nodes if node.group is not None and node.group.id == group.id and lane_for_node[node.id] is not None}
        if len(member_lanes) > 1:
            raise _fail("E_VISUAL_SPEC_EDGE", f"group {group.id} spans multiple lanes")
        group_lane[group.id] = next(iter(member_lanes), None)
    expected_parent: dict[str, str | None] = {lane.id: None for lane in lanes}
    expected_parent.update({group.id: group_lane[group.id] for group in groups})
    for container in semantic_containers:
        actual = geometry_groups[container.id]["parent_id"]
        if actual != expected_parent[container.id]:
            raise _fail("E_VISUAL_GEOMETRY", f"geometry parent drift for container {container.id}")
    expected_node_parent: dict[str, str | None] = {}
    for node in nodes:
        expected_node_parent[node.id] = node.group.id if node.group is not None else (node.lane.id if node.lane is not None else None)
        if geometry_nodes[node.id]["parent_id"] != expected_node_parent[node.id]:
            raise _fail("E_VISUAL_GEOMETRY", f"geometry parent drift for node {node.id}")

    labels: dict[str, str] = {}
    if intents[0].label is not None:
        labels[_INTENT_KEY] = intents[0].label
    for item in semantic_containers:
        if item.label is not None:
            labels[item.id] = item.label
    for item in nodes:
        if item.label is not None:
            labels[item.id] = item.label
    for item in edges:
        if item.label is not None:
            labels[item.id] = item.label
    fits = _text_map(text_fits, variant, labels)

    primitive_values: list[ScenePrimitive] = []
    for item in sorted(semantic_containers, key=lambda value: _id_key(value.id)):
        raw_rect = geometry_groups[item.id]
        children = [
            *[child.id for child in semantic_containers if expected_parent[child.id] == item.id],
            *[node.id for node in nodes if expected_node_parent[node.id] == item.id],
        ]
        primitive_values.append(ScenePrimitive("group", item.id, item.id, item.evidence_ids, "containers", 0, raw_rect["x"], raw_rect["y"], raw_rect["width"], raw_rect["height"], tuple(sorted(children, key=_id_key))))

    for index, edge in enumerate(edges):
        edge_geometry = geometry_edges[index]
        points = _edge_points(edge_geometry)
        if not _point_in_rect(points[0], geometry_nodes[edge.source]) or not _point_in_rect(points[-1], geometry_nodes[edge.target]):
            raise _fail("E_VISUAL_GEOMETRY", f"geometry edge {edge.id} endpoint drifted from Plan endpoints")
        if len(points) == 2:
            primitive_values.append(ScenePrimitive("line", edge.id, edge.id, edge.evidence_ids, "edges", 1, x1=points[0][0], y1=points[0][1], x2=points[1][0], y2=points[1][1]))
        else:
            primitive_values.append(ScenePrimitive("path", edge.id, edge.id, edge.evidence_ids, "edges", 1, points=points))

    for node in sorted(nodes, key=lambda value: _id_key(value.id)):
        raw_rect = geometry_nodes[node.id]
        primitive_values.append(ScenePrimitive("rect", node.id, node.id, node.evidence_ids, "nodes", 2, raw_rect["x"], raw_rect["y"], raw_rect["width"], raw_rect["height"]))

    minimum_font = theme.variants[variant]["min_font_size"]
    spacing = theme.spacing["label"]
    for source_id in sorted(labels, key=_id_key):
        fit = fits[source_id]
        if fit.font_size < minimum_font:
            raise _fail("E_VISUAL_TEXT_FIT", f"text fit for {source_id} is below the {variant} minimum font size")
        if source_id == _INTENT_KEY:
            x, y = 0, min(fit.font_size, max(0, canvas[1]))
        elif source_id in geometry_groups:
            raw_rect = geometry_groups[source_id]
            x = raw_rect["x"] + min(spacing, raw_rect["width"])
            y = raw_rect["y"] + min(spacing + fit.font_size, raw_rect["height"])
        elif source_id in geometry_nodes:
            raw_rect = geometry_nodes[source_id]
            x = raw_rect["x"] + min(spacing, raw_rect["width"])
            y = raw_rect["y"] + min(spacing + fit.font_size, raw_rect["height"])
        else:
            edge_index = next(index for index, edge in enumerate(edges) if edge.id == source_id)
            edge_points = _edge_points(geometry_edges[edge_index])
            midpoint = edge_points[len(edge_points) // 2]
            x, y = midpoint
        evidence_ids = intents[0].evidence_ids if source_id == _INTENT_KEY else next(
            item.evidence_ids for item in (*semantic_containers, *nodes, *edges) if item.id == source_id
        )
        primitive_values.append(ScenePrimitive("text", f"text:{source_id}", source_id, evidence_ids, "labels", 3, x, y, text=fit.text, lines=fit.lines, widths=fit.widths, font_size=fit.font_size, role=fit.role))

    primitive_values.sort(key=lambda item: (_LAYER_INDEX[item.layer], item.z, _id_key(item.id)))
    return Scene(
        SCENE_SCHEMA_VERSION,
        plan.locale,
        variant,
        (0, 0, view_width, canvas[1]),
        plan.source_spec_sha256,
        theme.sha256(),
        backend,
        SCENE_LAYERS,
        tuple(primitive_values),
    )


__all__ = ["SCENE_LAYERS", "SCENE_SCHEMA_VERSION", "Scene", "ScenePrimitive", "build_scene", "validate_visual_scene"]
