#!/usr/bin/env python3
"""Derive the settled static frame from an animated SVG source and its motion spec.

The settled composition is the animation end state: every enter complete and
no exit applied.  Derivation keys off the SVG's own animation declarations
(SMIL final values are baked in, CSS ``@keyframes``/``animation`` declarations
are stripped so authored settled values show) — never off a motion JSON that
may sit stale beside a fallback GIF.  The motion spec is validated and
cross-checked instead: every scene id must exist in the SVG.
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
import xml.etree.ElementTree as ET
from io import BytesIO
from pathlib import Path
from typing import Any

if __package__ and __package__.startswith("skill."):
    from skill.scripts.audit_readme import MAX_SVG_DEPTH, MAX_SVG_ELEMENTS
    from skill.scripts.pipeline_contracts import (
        ContractError,
        read_json_object_bytes,
        read_regular_bytes,
        write_bytes_atomic,
    )
    from skill.scripts.readme_showcase.visual_kernel.motion import (
        validate_motion_spec_v2,
    )
else:  # The installed Skill runs this file directly from its scripts directory.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.audit_readme import MAX_SVG_DEPTH, MAX_SVG_ELEMENTS
    from scripts.pipeline_contracts import (
        ContractError,
        read_json_object_bytes,
        read_regular_bytes,
        write_bytes_atomic,
    )
    from scripts.readme_showcase.visual_kernel.motion import (
        validate_motion_spec_v2,
    )


SVG_NS = "http://www.w3.org/2000/svg"
ET.register_namespace("", SVG_NS)

MAX_STATIC_SVG_BYTES = 2 * 1024 * 1024
MAX_STATIC_SPEC_BYTES = 256 * 1024

_SMIL_TAGS = frozenset({"animate", "set", "animateTransform", "animateMotion"})
_FUNCTION_TRANSFORMS = frozenset({"translate", "scale", "rotate", "skewX", "skewY"})
_KEYFRAMES = re.compile(r"@(?:-\w+-)?keyframes\b")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _scene_ids(spec: dict[str, Any]) -> list[str]:
    if spec.get("schema_version") == 2:
        return [item["id"] for item in spec["scenes"]]
    return [
        item["id"] for item in [*spec.get("reveals", []), *spec.get("layers", [])]
    ]


def _validate_spec(spec: Any) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise _fail("E_SCHEMA_TYPE", "motion spec must be an object")
    version = spec.get("schema_version")
    if version == 2:
        validate_motion_spec_v2(spec)
    elif version == 1:
        for field in ("reveals", "layers"):
            items = spec.get(field)
            if not isinstance(items, list):
                raise _fail("E_SCHEMA_TYPE", f"motion spec {field} must be an array")
            for item in items:
                if not isinstance(item, dict) or not item.get("id"):
                    raise _fail("E_SCHEMA_TYPE", "every reveal and layer needs a non-empty id")
        ids = _scene_ids(spec)
        if len(ids) != len(set(ids)):
            raise _fail("E_VISUAL_SPEC_ID", "motion element ids must be unique")
    else:
        raise _fail("E_SCHEMA_VERSION", "motion spec requires schema_version 1 or 2")
    return spec


def _parse_svg(svg: Any) -> ET.Element:
    if isinstance(svg, ET.Element):
        return svg
    if isinstance(svg, Path):
        try:
            raw = read_regular_bytes(svg, maximum=MAX_STATIC_SVG_BYTES)
        except ContractError as exc:
            raise _fail(exc.code, str(exc)) from exc
    elif isinstance(svg, bytes):
        raw = svg
    elif isinstance(svg, str):
        raw = svg.encode("utf-8")
    else:
        raise _fail("E_SCHEMA_TYPE", "input SVG must be bytes, str, Path, or an Element")
    if len(raw) > MAX_STATIC_SVG_BYTES:
        raise _fail("E_INPUT_SIZE", f"input exceeds {MAX_STATIC_SVG_BYTES} bytes")
    try:
        return ET.fromstring(raw)
    except ET.ParseError as exc:
        raise _fail("E_SCHEMA_TYPE", f"invalid SVG XML: {exc}") from exc


def _validate_structure(root: ET.Element) -> None:
    elements = 0
    stack = [(root, 1)]
    while stack:
        node, depth = stack.pop()
        elements += 1
        if depth > MAX_SVG_DEPTH or elements > MAX_SVG_ELEMENTS:
            raise _fail("E_SCHEMA_VALUE", "SVG structure exceeds depth or element limits")
        stack.extend((child, depth + 1) for child in reversed(list(node)))


def _find_path(root: ET.Element, element_id: str) -> list[ET.Element] | None:
    if root.attrib.get("id") == element_id:
        return [root]
    for child in root:
        path = _find_path(child, element_id)
        if path:
            return [root, *path]
    return None


def _bake_smil(target: ET.Element, animation: ET.Element) -> None:
    tag = animation.tag.rsplit("}", 1)[-1]
    if tag == "animateMotion":
        # Path-based motion resolves to the authored position when frozen.
        return
    attribute = animation.get("attributeName")
    if not attribute:
        return
    final = animation.get("to")
    values = animation.get("values")
    if final is None and values is not None:
        final = values.split(";")[-1].strip()
    if final is None:
        # No explicit end state (e.g. from-only or by-based): the authored
        # attribute is the settled design and stays as-is.
        return
    if tag == "animateTransform":
        kind = animation.get("type", "translate")
        if kind in _FUNCTION_TRANSFORMS:
            target.set("transform", f"{kind}({final})")
        elif kind == "matrix":
            target.set("transform", f"matrix({final})")
        return
    if ":" in attribute:
        # Namespaced attribute names (e.g. xlink:href) keep the authored value.
        return
    target.set(attribute, final)


def _freeze_smil(root: ET.Element) -> None:
    for parent in list(root.iter()):
        for child in list(parent):
            if child.tag.rsplit("}", 1)[-1] in _SMIL_TAGS:
                _bake_smil(parent, child)
                parent.remove(child)


def _strip_keyframes(css: str) -> str:
    """Remove @keyframes blocks (including vendor prefixes), brace-balanced."""
    result: list[str] = []
    cursor = 0
    for match in _KEYFRAMES.finditer(css):
        start = match.start()
        if start > cursor:
            result.append(css[cursor:start])
        open_brace = css.find("{", start)
        if open_brace == -1:
            cursor = start
            break
        depth = 0
        end = -1
        for index in range(open_brace, len(css)):
            if css[index] == "{":
                depth += 1
            elif css[index] == "}":
                depth -= 1
                if depth == 0:
                    end = index + 1
                    break
        if end == -1:
            cursor = open_brace
            break
        cursor = end
    result.append(css[cursor:])
    return "".join(result)


def _unbalanced(declaration: str) -> bool:
    if declaration.count('"') % 2 or declaration.count("'") % 2:
        return True
    return declaration.count("(") > declaration.count(")")


def _strip_animation_declarations(css: str) -> str:
    """Drop animation/animation-* declarations without mangling other rules."""
    merged: list[str] = []
    for chunk in css.split(";"):
        if merged and _unbalanced(merged[-1]):
            merged[-1] = f"{merged[-1]};{chunk}"
        else:
            merged.append(chunk)
    kept = []
    for declaration in merged:
        if not declaration.strip():
            continue
        if ":" not in declaration:
            kept.append(declaration)
            continue
        prop = declaration.split(":", 1)[0].strip().lower()
        if prop == "animation" or prop.startswith("animation-"):
            continue
        kept.append(declaration)
    return ";".join(kept).strip()


def _strip_css_animation(root: ET.Element) -> None:
    for element in root.iter():
        style = element.get("style")
        if style is None:
            continue
        cleaned = _strip_animation_declarations(style)
        if cleaned:
            element.set("style", cleaned)
        else:
            del element.attrib["style"]
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1] != "style" or not element.text:
            continue
        cleaned = _strip_keyframes(element.text)
        element.text = _strip_animation_declarations(cleaned)


def _serialize(root: ET.Element) -> bytes:
    stream = BytesIO()
    ET.ElementTree(root).write(stream, encoding="utf-8", xml_declaration=True)
    return stream.getvalue()


def render_static_frame(svg: Any, motion_spec: Any, variant: str = "settled") -> bytes:
    """Return the settled static frame as SVG bytes.

    ``svg`` is the animated SVG source (bytes, str, Path, or parsed Element);
    ``motion_spec`` is a motion spec v1 or v2 payload.  The settled composition
    freezes SMIL animations at their final values, strips CSS animation
    machinery so authored settled values show, and fails loudly when a scene id
    does not exist in the SVG (stale-spec guard).
    """
    if variant != "settled":
        raise _fail("E_VISUAL_DETERMINISM", "static-frame variant must be settled")
    _validate_spec(motion_spec)
    root = _parse_svg(svg)
    _validate_structure(root)

    missing = sorted(
        element_id
        for element_id in _scene_ids(motion_spec)
        if _find_path(root, element_id) is None
    )
    if missing:
        raise _fail(
            "E_VISUAL_SPEC_ID",
            f"SVG element id not found: {missing[0]}",
        )

    settled = copy.deepcopy(root)
    _freeze_smil(settled)
    _strip_css_animation(settled)
    return _serialize(settled)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Derive the settled static frame (static SVG) from an animated "
            "SVG source and its motion spec."
        )
    )
    parser.add_argument("input_svg", type=Path)
    parser.add_argument("output_svg", type=Path)
    parser.add_argument(
        "--spec",
        type=Path,
        required=True,
        help="motion spec JSON (v1 or v2)",
    )
    parser.add_argument("--variant", default="settled", choices=["settled"])
    return parser.parse_args()


def fail(message: str) -> None:
    raise SystemExit(f"ERROR: {message}")


def run(args: argparse.Namespace) -> None:
    try:
        raw = read_regular_bytes(
            args.input_svg.expanduser(),
            maximum=MAX_STATIC_SVG_BYTES,
        )
        _, spec = read_json_object_bytes(
            args.spec.expanduser(),
            maximum=MAX_STATIC_SPEC_BYTES,
        )
        data = render_static_frame(raw, spec, variant=args.variant)
        write_bytes_atomic(args.output_svg.expanduser(), data)
    except ContractError as exc:
        fail(f"{exc.code}: {exc}")
    print(f"STATIC-FRAME: {args.output_svg}")
    print(f"Output: {len(data)} bytes, variant {args.variant}")


if __name__ == "__main__":
    run(parse_args())
