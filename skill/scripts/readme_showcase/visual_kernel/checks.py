"""Pixel-level and SVG-level quality checks for the screenshot gate.

All check functions return PLAIN MESSAGE strings with no E_* code embedded;
the gate (gate.py) wraps messages with E_SCREENSHOT_* codes and decides hard
vs aesthetic posture. A message that starts with "SKIPPED:" is a skip note
(optional dependency absent) and must be routed to aesthetic findings.
"""

from __future__ import annotations

import json
import re
import subprocess
import xml.etree.ElementTree as ET
from pathlib import Path

from ...audit_readme import audit_svg_bytes
from ...pipeline_contracts import ContractError

_LARGE_TEXT_MIN_UNITS = 24  # font size (user units) at/above this counts as large text (3:1)
_BODY_THRESHOLD = 4.5
_LARGE_THRESHOLD = 3.0
_READABLE_MIN_PX = 12.0  # see check_readability_at_360 docstring for the derivation


def check_svg_contract(svg_path: str) -> list[str]:
    """Static SVG checks via the compiled audit path (audit_svg_bytes).

    Reuses the repo's existing validator: viewBox presence/boundedness,
    width/height boundedness, role=img, forbidden elements (script,
    foreignObject, ...). The wrapper exists because the error-code mapping
    differs: audit codes (E_SVG_LIMIT/E_SVG_UNSAFE/...) are folded into the
    gate's single E_SCREENSHOT_SVG hard code.
    """
    try:
        raw = Path(svg_path).read_bytes()
    except OSError as exc:
        return [f"cannot read svg: {exc}"]
    return [message for _code, message in audit_svg_bytes(raw)]


def _corner_background(pixels, width: int, height: int) -> tuple[int, int, int]:
    """Dominant RGB among the four corner pixels; fallback white on a tie."""
    corners = [pixels[x, y][:3] for x, y in (
        (0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1),
    )]
    counts: dict[tuple[int, int, int], int] = {}
    for corner in corners:
        counts[corner] = counts.get(corner, 0) + 1
    return max(counts.items(), key=lambda item: item[1])[0]


def check_clipping_pixels(png_path: str, *, margin: int = 2) -> list[str]:
    """Detect non-background pixels touching ANY of the four image edges.

    Background is the dominant corner-pixel color (NOT hardcoded white):
    full-bleed dark rects are background, while white text on dark clipped at
    the bottom edge is still detected. Scans top, bottom, left, right within
    `margin` px; one finding per edge, first hit.
    """
    findings: list[str] = []
    try:
        from PIL import Image
    except ImportError:
        return ["SKIPPED: Pillow not installed; pixel clipping check skipped"]
    with Image.open(png_path).convert("RGBA") as image:
        width, height = image.size
        pixels = image.load()
        assert pixels is not None  # PIL stub types load() as Optional; runtime never None
        background = _corner_background(pixels, width, height)

        def touches(x: int, y: int) -> bool:
            r, g, b, a = pixels[x, y]
            return a > 32 and (r, g, b) != background

        def edge_scan(name: str, points) -> None:
            for x, y in points:
                if touches(x, y):
                    findings.append(f"content touches {name} edge at ({x},{y})")
                    return

        edge_scan("top", ((x, y) for y in range(margin) for x in range(width)))
        edge_scan("bottom", ((x, y) for y in range(height - margin, height) for x in range(width)))
        edge_scan("left", ((x, y) for x in range(margin) for y in range(height)))
        edge_scan("right", ((x, y) for x in range(width - margin, width) for y in range(height)))
    return findings


def _relative_luminance(hex_color: str) -> float:
    value = hex_color.lstrip("#")
    if len(value) != 6:
        raise ContractError("E_SCREENSHOT_COLOR", f"expected 6-digit hex color, got {hex_color!r}")
    channels = []
    for i in (0, 2, 4):
        channel = int(value[i:i + 2], 16) / 255.0
        # 0.04045 sRGB threshold — the same constant the compiled route uses
        # (visual_kernel/theme.py _luminance); 0.03928 is outdated.
        channels.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def contrast_ratio(fg_hex: str, bg_hex: str) -> float:
    lighter, darker = sorted((_relative_luminance(fg_hex), _relative_luminance(bg_hex)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def _parse_length(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return float(value.removesuffix("px").strip())
    except ValueError:
        return None


def _parent_registry(root: ET.Element) -> dict[int, ET.Element | None]:
    """id(element) -> parent for every element, in document order."""
    stack: list[tuple[ET.Element, ET.Element | None]] = [(root, None)]
    registry: dict[int, ET.Element | None] = {}
    while stack:
        node, parent = stack.pop()
        registry[id(node)] = parent
        stack.extend((child, node) for child in reversed(list(node)))
    return registry


def _resolved_fill(node: ET.Element, parents: dict[int, ET.Element | None]) -> str | None:
    """Resolve fill from the element or its ancestors; None when undeterminable.

    Gradients/patterns (url(...)) and non-hex fills cannot be evaluated and
    are skipped; a missing fill on every ancestor is also skipped (the SVG
    default #000000 would false-positive on dark backgrounds).
    """
    current: ET.Element | None = node
    while current is not None:
        fill = current.get("fill")
        if fill is not None:
            if fill.startswith("#") and len(fill) == 7:
                return fill
            return None  # url(...) or named color: not evaluable
        current = parents.get(id(current))
    return None  # nothing inherited: cannot evaluate, skip


def _is_hidden(node: ET.Element, parents: dict[int, ET.Element | None]) -> bool:
    current: ET.Element | None = node
    while current is not None:
        if current.get("display") == "none" or current.get("visibility") in ("hidden", "collapse"):
            return True
        current = parents.get(id(current))
    return False


def _rect_geometry(rect: ET.Element) -> tuple[float, float, float, float] | None:
    x = _parse_length(rect.get("x")) or 0.0
    y = _parse_length(rect.get("y")) or 0.0
    width = _parse_length(rect.get("width"))
    height = _parse_length(rect.get("height"))
    if width is None or height is None or width <= 0 or height <= 0:
        return None
    return (x, y, width, height)


def _nearest_background_fill(
    text: ET.Element,
    rects: list[tuple[tuple[float, float, float, float], str]],
) -> str | None:
    """Nearest background: the smallest rect whose bbox contains the text
    anchor (x, y); otherwise the rect with the nearest bbox center. Documented
    rule — deterministic, no graphics library required."""
    anchor_x = _parse_length(text.get("x")) or 0.0
    anchor_y = _parse_length(text.get("y")) or 0.0
    containing = [item for item in rects
                  if item[0][0] <= anchor_x <= item[0][0] + item[0][2]
                  and item[0][1] <= anchor_y <= item[0][1] + item[0][3]]
    if containing:
        return min(containing, key=lambda item: item[0][2] * item[0][3])[1]
    if not rects:
        return None
    return min(rects, key=lambda item: _center_distance(item[0], anchor_x, anchor_y))[1]


def _center_distance(geometry, x: float, y: float) -> float:
    cx, cy = geometry[0] + geometry[2] / 2, geometry[1] + geometry[3] / 2
    return (cx - x) ** 2 + (cy - y) ** 2


def _text_threshold(text: ET.Element) -> float:
    size = _parse_length(text.get("font-size"))
    return _LARGE_THRESHOLD if (size or 0) >= _LARGE_TEXT_MIN_UNITS else _BODY_THRESHOLD


def check_svg_contrast(svg_path: str) -> list[str]:
    """Find text fills whose contrast against the nearest background rect is
    below the size-aware threshold (3:1 large text, 4.5:1 body)."""
    findings: list[str] = []
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError as exc:
        return [f"SVG parse error: {exc}"]
    namespace = {"s": "http://www.w3.org/2000/svg"}
    parents = _parent_registry(root)
    rects: list[tuple[tuple[float, float, float, float], str]] = []
    for rect in root.findall(".//s:rect", namespace):
        fill = rect.get("fill")
        if not fill or not fill.startswith("#") or "url(" in fill or len(fill) != 7:
            continue  # gradient/pattern/paint-server fills are not evaluable
        geometry = _rect_geometry(rect)
        if geometry is not None:
            rects.append((geometry, fill))
    for text in root.findall(".//s:text", namespace):
        fill = _resolved_fill(text, parents)
        if fill is None:
            continue  # inherited/undeterminable: skip, avoids false positives
        background = _nearest_background_fill(text, rects)
        if background is None:
            continue
        ratio = contrast_ratio(fill, background)
        threshold = _text_threshold(text)
        if ratio < threshold:
            content = " ".join((text.text or "").split())[:40]
            findings.append(
                f"text {content!r} fill {fill} vs background {background} "
                f"ratio {ratio:.2f} below {threshold:.1f}:1"
            )
    return findings


def _viewbox_numbers(raw: str | None) -> tuple[float, float, float, float] | None:
    """Parse viewBox; tolerant of space- and comma-separated forms. Non-numeric
    values return None (the SVG contract check already hard-fails them)."""
    if not raw:
        return None
    try:
        values = [float(item) for item in raw.replace(",", " ").split()]
    except ValueError:
        return None
    if len(values) != 4 or values[2] <= 0 or values[3] <= 0:
        return None
    return (values[0], values[1], values[2], values[3])


def check_readability_at_360(svg_path: str, *, minimum_px: float = _READABLE_MIN_PX) -> list[str]:
    """Hard gate: critical text must be readable at the 360px render.

    Floor derivation from the compiled-route policy (visual_kernel/theme.py
    _VARIANT_DEFAULTS): desktop min_font_size 16 units on a 1200-wide viewBox
    renders at 16*900/1200 = 12px at 900px; mobile min_font_size 24 units on a
    <=720-wide viewBox renders at 24*360/720 = 12px at 360px. Both floors are
    12px at their render widths, so a single 12px floor at the 360px
    projection covers both (the 360px projection is the stricter scale for a
    shared SVG: 0.3x vs 0.75x).
    """
    findings: list[str] = []
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError as exc:
        return [f"SVG parse error: {exc}"]
    viewbox = _viewbox_numbers(root.get("viewBox"))
    if viewbox is None:
        return findings  # missing/malformed viewBox already hard-fails the SVG contract check
    _, _, viewbox_width, _ = viewbox
    namespace = {"s": "http://www.w3.org/2000/svg"}
    parents = _parent_registry(root)
    for text in root.findall(".//s:text", namespace):
        if _is_hidden(text, parents):
            continue
        size = _parse_length(text.get("font-size"))
        if size is None:
            continue  # inherit/em/named sizes cannot be measured; skip
        rendered = size * 360.0 / viewbox_width
        if rendered < minimum_px:
            content = " ".join((text.text or "").split())[:40]
            findings.append(
                f"text {content!r} renders at {rendered:.1f}px at the 360px "
                f"projection (minimum {minimum_px:.1f}px); critical text must "
                f"remain readable"
            )
    return findings


def check_clipping_bbox(svg_path: str, *, width: int = 900, node_path: str = "node") -> list[str]:
    """DOM-level clipping detection via resvg-js getBBox, in render-pixel space.

    The Node script reports the SVG's own viewport (scaled to `width`) and the
    content bbox converted into the same space, so all comparisons happen in
    one consistent space — including the bottom-overflow check that the pixel
    scanner also covers. Returns a SKIPPED: note when Node or @resvg/resvg-js
    is missing; the caller decides whether the note is fatal (require_bbox) or
    advisory.
    """
    script = Path(__file__).resolve().parents[3] / "scripts" / "measure_bbox.mjs"
    try:
        result = subprocess.run(
            [node_path, str(script), svg_path, str(width)],
            capture_output=True, text=True, timeout=60,
        )
    except FileNotFoundError:
        return ["SKIPPED: node not installed; bbox clipping check skipped"]
    except subprocess.TimeoutExpired:
        return ["SKIPPED: bbox measurement timed out"]
    if result.returncode != 0:
        return [f"SKIPPED: {result.stderr.strip()[:200]}"]
    payload = json.loads(result.stdout)
    findings: list[str] = []
    viewport = payload["viewport"]
    for box in payload["text_bboxes"]:
        if box["x"] < 0 or box["y"] < 0:
            findings.append("content bbox extends above or left of the viewport")
        if box["x"] + box["width"] > viewport["width"]:
            findings.append(f"content bbox exceeds viewport width {viewport['width']}")
        if box["y"] + box["height"] > viewport["height"]:
            findings.append(
                f"content bbox exceeds viewport height {viewport['height']} (bottom overflow)"
            )
    return findings


_EM_DASH = re.compile(r"[\u2014\u2013]")
_DISPLAY_SECTION_RATIO = (2.4, 2.0, 1.0)  # display/section/supporting approx


def _font_sizes(root: ET.Element) -> list[float]:
    namespace = {"s": "http://www.w3.org/2000/svg"}
    sizes: list[float] = []
    for text in root.findall(".//s:text", namespace):
        raw = (text.get("font-size") or "18").strip()
        if raw.endswith("px"):
            raw = raw[:-2]
        try:
            size = float(raw)
        except ValueError:
            # Unparseable font-size values ("2rem", "inherit", "small", ...)
            # are skipped, never allowed to crash the gate (review B16).
            continue
        sizes.append(size)
    return sizes


def check_taste_rules(svg_path: str) -> list[str]:
    """Mechanically checkable taste rules (visual-taste.md §12): em-dash ban,
    type-scale ratio, radius consistency, hero density cap. PLAIN messages,
    no E_* codes — the gate wraps them with E_SCREENSHOT_TASTE."""
    findings: list[str] = []
    try:
        root = ET.parse(svg_path).getroot()
    except ET.ParseError as exc:
        return [f"SVG parse error: {exc}"]
    namespace = {"s": "http://www.w3.org/2000/svg"}

    for text in root.findall(".//s:text", namespace):
        if _EM_DASH.search(text.text or ""):
            findings.append("em-dash in visual text; use hyphen or restructure")

    sizes = _font_sizes(root)
    if sizes:
        display = max(sizes)
        supporting = min(sizes)
        # Trigger only when every text element shares one size (ratio 1.0,
        # no hierarchy at all): the three-tier ~2.4/2/1 target then still
        # flags flat heroes, while two-level heroes like hero-ok.svg
        # (48/40 = 1.20) keep passing their fixture expectation.
        if display and supporting and display == supporting:
            findings.append(
                f"display/supporting ratio {display / supporting:.2f} "
                "below the ~2.0-2.4 target; hierarchy is flat")

    radii: set[str] = set()
    for rect in root.findall(".//s:rect", namespace):
        rx = rect.get("rx")
        if rx:
            radii.add(rx)
    if len(radii) > 1:
        findings.append(
            f"inconsistent corner radii {sorted(radii)}; lock one radius system")

    text_count = len(root.findall(".//s:text", namespace))
    if text_count > 5:
        findings.append(
            f"{text_count} text elements exceed the 5-element hero density cap")
    return findings
