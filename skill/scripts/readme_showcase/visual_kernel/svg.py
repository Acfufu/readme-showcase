"""Deterministic, static SVG serialization for validated visual Scenes.

The serializer is intentionally small and renderer-neutral.  Scene validation
owns semantic and geometry invariants; this module only projects those values
to an XML tree with a fixed, local-only vocabulary.
"""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from typing import Any

from ...pipeline_contracts import ContractError
from .scene import Scene, ScenePrimitive, validate_visual_scene
from .theme import Theme


_SVG_NAMESPACE = "http://www.w3.org/2000/svg"
_INTENT_SOURCE_ID = "__scene_intent__"
_XML_ID_PREFIX = "scene-p-"
_TITLE_ID = "scene-title"
_DESCRIPTION_ID = "scene-description"
_ARROW_ID = "scene-arrow"
_FONT_FAMILY = "system-ui, sans-serif"


def _element(name: str, attributes: Iterable[tuple[str, Any]] = ()) -> ET.Element:
    element = ET.Element(name)
    for key, value in attributes:
        element.set(key, str(value))
    return element


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _xml_safe_text(value: str, context: str) -> str:
    """Reject XML 1.0 code points ET cannot represent safely."""

    if re.search(
        r"(?i)(?:url\s*\(\s*(?!#)|\b(?:https?|ftp|file|data|javascript|mailto):|\bon[a-z]+\s*=)",
        value,
    ):
        raise _fail("E_VISUAL_SVG_SECURITY", f"{context} contains an external reference or active attribute")
    for character in value:
        codepoint = ord(character)
        if (
            codepoint in {0xFFFE, 0xFFFF}
            or 0xD800 <= codepoint <= 0xDFFF
            or codepoint < 0x20 and codepoint not in {0x09, 0x0A, 0x0D}
        ):
            raise _fail("E_VISUAL_TEXT_FIT", f"{context} contains an XML-incompatible character")
    return value


def _dom_id(identifier: str) -> str:
    digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
    return f"{_XML_ID_PREFIX}{digest}"


def _primitive_attributes(value: ScenePrimitive) -> list[tuple[str, Any]]:
    return [
        ("id", _dom_id(value.id)),
        ("data-scene-id", value.id),
        ("data-source-id", value.source_id),
        ("data-kind", value.kind),
        ("data-layer", value.layer),
        ("data-z", value.z),
    ]


def _radius(width: int, height: int, maximum: int) -> int:
    return min(maximum, width // 2, height // 2)


def _text_color(value: ScenePrimitive, theme: Theme) -> str:
    if value.role in {"title", "core"}:
        return theme.colors["accent"]
    if value.role in {"label", "edge", "group", "lane", "caption", "description"}:
        return theme.colors["muted"]
    return theme.colors["text"]


def _text_token(value: ScenePrimitive) -> str:
    if value.role in {"title", "core", "node"}:
        return "core"
    if value.role in {"caption", "description"}:
        return "caption"
    return "label"


def _group_element(value: ScenePrimitive, theme: Theme) -> ET.Element:
    element = _element("g", _primitive_attributes(value))
    frame = _element(
        "rect",
        (
            ("x", value.x),
            ("y", value.y),
            ("width", value.width),
            ("height", value.height),
            ("fill", theme.colors["surface"]),
            ("stroke", theme.colors["line"]),
            ("stroke-width", theme.strokes["hairline"]),
            ("rx", _radius(value.width or 0, value.height or 0, theme.spacing["section"])),
            ("data-role", "group-frame"),
        ),
    )
    element.append(frame)
    return element


def _rect_element(value: ScenePrimitive, theme: Theme) -> ET.Element:
    return _element(
        "rect",
        (
            *_primitive_attributes(value),
            ("x", value.x),
            ("y", value.y),
            ("width", value.width),
            ("height", value.height),
            ("fill", theme.colors["surface"]),
            ("stroke", theme.colors["accent"]),
            ("stroke-width", theme.strokes["normal"]),
            ("rx", _radius(value.width or 0, value.height or 0, theme.spacing["node"])),
        ),
    )


def _edge_presentation(theme: Theme) -> list[tuple[str, Any]]:
    return [
        ("fill", "none"),
        ("stroke", theme.colors["line"]),
        ("stroke-width", theme.strokes["normal"]),
        ("stroke-linecap", "round"),
        ("stroke-linejoin", "round"),
        ("marker-end", f"url(#{_ARROW_ID})"),
    ]


def _edge_attributes(value: ScenePrimitive, theme: Theme) -> list[tuple[str, Any]]:
    return [*_primitive_attributes(value), *_edge_presentation(theme)]


def _line_element(value: ScenePrimitive, theme: Theme) -> ET.Element:
    attributes = _primitive_attributes(value)
    attributes.extend(
        [
            ("x1", value.x1),
            ("y1", value.y1),
            ("x2", value.x2),
            ("y2", value.y2),
            *_edge_presentation(theme),
        ]
    )
    return _element("line", attributes)


def _path_element(value: ScenePrimitive, theme: Theme) -> ET.Element:
    points = value.points
    path_data = " ".join(
        f"{'M' if index == 0 else 'L'}{point[0]} {point[1]}"
        for index, point in enumerate(points)
    )
    return _element("path", (*_edge_attributes(value, theme), ("d", path_data)))


def _text_element(value: ScenePrimitive, theme: Theme) -> ET.Element:
    token = _text_token(value)
    font_size = max(value.font_size or 1, theme.text[token])
    element = _element(
        "text",
        (
            *_primitive_attributes(value),
            ("x", value.x),
            ("y", value.y),
            ("fill", _text_color(value, theme)),
            ("font-family", _FONT_FAMILY),
            ("font-size", font_size),
            ("font-weight", 700 if value.role in {"title", "core"} else 500),
            ("text-anchor", "start"),
        ),
    )
    lines = tuple(value.lines)
    if not lines:
        raise _fail("E_VISUAL_TEXT_FIT", f"text primitive {value.id} has no lines")
    element.text = _xml_safe_text(lines[0], f"text primitive {value.id}")
    for line in lines[1:]:
        span = _element("tspan", (("x", value.x), ("dy", font_size)))
        span.text = _xml_safe_text(line, f"text primitive {value.id}")
        element.append(span)
    return element


def _marker(theme: Theme) -> ET.Element:
    marker = _element(
        "marker",
        (
            ("id", _ARROW_ID),
            ("markerWidth", 8),
            ("markerHeight", 6),
            ("refX", 7),
            ("refY", 3),
            ("orient", "auto"),
        ),
    )
    marker.append(
        _element(
            "path",
            (
                ("d", "M0 0 L8 3 L0 6 Z"),
                ("fill", theme.colors["line"]),
            ),
        )
    )
    return marker


def _description(scene: Scene) -> str:
    return f"Static {scene.variant} visual scene for locale {scene.locale}."


def _render_dimensions(scene: Scene, theme: Theme) -> tuple[int, int]:
    _, _, width, height = scene.view_box
    render_width = theme.variants[scene.variant]["render_width"]
    render_height = max(1, (height * render_width + width - 1) // width)
    return render_width, render_height


def serialize_svg(scene: Any, theme: Any) -> bytes:
    """Serialize one validated Scene to deterministic, standalone SVG bytes."""

    validated_scene = validate_visual_scene(scene)
    if type(theme) is not Theme:
        raise _fail("E_SCHEMA_TYPE", "SVG serializer requires a resolved Theme")
    if theme.sha256() != validated_scene.theme_sha256:
        raise _fail("E_VISUAL_FINGERPRINT", "scene theme hash does not match the resolved Theme")

    for primitive in validated_scene.primitives:
        _xml_safe_text(primitive.id, f"primitive {primitive.id}.id")
        _xml_safe_text(primitive.source_id, f"primitive {primitive.id}.source_id")
        if primitive.kind == "text":
            _xml_safe_text(primitive.text or "", f"text primitive {primitive.id}")
            for line in primitive.lines:
                _xml_safe_text(line, f"text primitive {primitive.id}")

    intent_titles = tuple(
        primitive
        for primitive in validated_scene.primitives
        if primitive.kind == "text" and primitive.source_id == _INTENT_SOURCE_ID
    )
    if len(intent_titles) != 1 or intent_titles[0].role != "title":
        raise _fail("E_VISUAL_TEXT_FIT", "scene must contain exactly one reserved intent title")
    title = intent_titles[0].text or ""
    title = _xml_safe_text(title, "scene title")
    description = _xml_safe_text(_description(validated_scene), "scene description")
    x, y, view_width, view_height = validated_scene.view_box
    render_width, render_height = _render_dimensions(validated_scene, theme)
    root = _element(
        "svg",
        (
            ("xmlns", _SVG_NAMESPACE),
            ("width", render_width),
            ("height", render_height),
            ("viewBox", f"{x} {y} {view_width} {view_height}"),
            ("role", "img"),
            ("aria-labelledby", _TITLE_ID),
            ("aria-describedby", _DESCRIPTION_ID),
            ("data-scene-locale", validated_scene.locale),
            ("data-scene-variant", validated_scene.variant),
        ),
    )
    title_element = _element("title", (("id", _TITLE_ID),))
    title_element.text = title
    description_element = _element("desc", (("id", _DESCRIPTION_ID),))
    description_element.text = description
    root.extend((title_element, description_element))

    definitions = _element("defs")
    definitions.append(_marker(theme))
    root.append(definitions)

    root.append(
        _element(
            "rect",
            (
                ("x", x),
                ("y", y),
                ("width", view_width),
                ("height", view_height),
                ("fill", theme.colors["background"]),
                ("data-role", "background"),
            ),
        )
    )

    for primitive in validated_scene.primitives:
        if primitive.kind == "group":
            element = _group_element(primitive, theme)
        elif primitive.kind == "rect":
            element = _rect_element(primitive, theme)
        elif primitive.kind == "line":
            element = _line_element(primitive, theme)
        elif primitive.kind == "path":
            element = _path_element(primitive, theme)
        else:
            element = _text_element(primitive, theme)
        root.append(element)

    return ET.tostring(root, encoding="utf-8", xml_declaration=False, short_empty_elements=True) + b"\n"


__all__ = ["serialize_svg"]
