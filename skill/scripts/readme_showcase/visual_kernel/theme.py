"""Closed, deterministic Theme v1 values and desktop/mobile policy.

Repository tokens can change only the small, project-owned palette and metric
surface below.  Theme resolution never reads files or resources and never
accepts coordinates: layout remains an independent decision for each variant.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.common import validate_bounded_json


THEME_SCHEMA_VERSION = 1

_COLOR_DEFAULTS = {
    "background": "#0b1020",
    "surface": "#121a2e",
    "text": "#f8fafc",
    "muted": "#b6c2d9",
    "accent": "#4fd1c5",
    "line": "#47617f",
}
_SPACING_DEFAULTS = {
    "canvas": 32,
    "section": 24,
    "node": 16,
    "lane": 24,
    "label": 8,
}
_STROKE_DEFAULTS = {
    "hairline": 1,
    "normal": 2,
    "emphasis": 3,
}
_TEXT_DEFAULTS = {
    "core": 16,
    "label": 14,
    "caption": 12,
}
_VARIANT_DEFAULTS = {
    "desktop": {"width": 1200, "render_width": 900, "min_font_size": 16},
    "mobile": {"width": 720, "render_width": 360, "min_font_size": 24},
}

_GROUP_DEFAULTS = {
    "colors": _COLOR_DEFAULTS,
    "spacing": _SPACING_DEFAULTS,
    "strokes": _STROKE_DEFAULTS,
    "text": _TEXT_DEFAULTS,
}
_COLOR_RE = re.compile(r"#[0-9a-fA-F]{6}\Z")
_SCHEME_RE = re.compile(r"(?i)(?:^|[^a-z0-9_])(?:https?|ftp|file|data|javascript|mailto):")
_PATH_FIELD_NAMES = frozenset({"file", "files", "href", "path", "src", "url", "urls"})
_RESOURCE_FIELD_NAMES = frozenset(
    {"asset", "assets", "css", "font", "fonts", "icon", "icons",
     "import", "script", "scripts"}
)
_GEOMETRY_FIELD_NAMES = frozenset(
    {"coordinate", "coordinates", "height", "view_box", "viewbox", "width", "x", "y"}
)


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _field_kind(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    normalized = name.casefold().replace("-", "_")
    if normalized in _PATH_FIELD_NAMES:
        return "path"
    if normalized in _RESOURCE_FIELD_NAMES or any(item in normalized for item in ("font", "icon", "script")):
        return "resource"
    if normalized in _GEOMETRY_FIELD_NAMES or "coordinate" in normalized:
        return "geometry"
    return None


def _reject_unsafe_value(value: Any, context: str) -> None:
    if not isinstance(value, str):
        return
    if _SCHEME_RE.search(value) or value.startswith(("/", "~/", "\\\\")) or "\\" in value:
        raise _fail("E_VISUAL_PATH", f"{context} must not contain a URL or path")
    if ".." in value.split("/"):
        raise _fail("E_VISUAL_PATH", f"{context} must not contain traversal")


def _validate_closed_group(
    value: Any,
    defaults: Mapping[str, Any],
    context: str,
    *,
    require_all: bool = True,
) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise _fail("E_SCHEMA_KEY_TYPE", f"{context} contains a non-string token name")
    unknown = sorted(set(value) - set(defaults))
    if unknown:
        kind = _field_kind(unknown[0])
        if kind == "path":
            raise _fail("E_VISUAL_PATH", f"{context} contains an unsupported path token: {unknown[0]}")
        if kind == "resource":
            raise _fail("E_VISUAL_RESOURCE", f"{context} contains an unsupported resource token: {unknown[0]}")
        if kind == "geometry":
            raise _fail("E_VISUAL_GEOMETRY", f"{context} contains an unsupported coordinate token: {unknown[0]}")
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown token: {unknown[0]}")
    if require_all:
        missing = sorted(set(defaults) - set(value))
        if missing:
            raise _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing token: {missing[0]}")
    return {key: value[key] for key in defaults if key in value}


def _parse_color(value: Any, context: str) -> str:
    _reject_unsafe_value(value, context)
    if not isinstance(value, str):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be a hexadecimal color string")
    if _COLOR_RE.fullmatch(value) is None:
        raise _fail("E_SCHEMA_VALUE", f"{context} must be a six-digit hexadecimal color")
    return value.lower()


def _parse_metric(value: Any, context: str, *, minimum: int = 0, maximum: int = 20000) -> int:
    _reject_unsafe_value(value, context)
    if type(value) is not int:
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an integer")
    if not minimum <= value <= maximum:
        raise _fail("E_SCHEMA_VALUE", f"{context} must be between {minimum} and {maximum}")
    return value


def _channel(value: str, offset: int) -> float:
    return int(value[offset : offset + 2], 16) / 255


def _luminance(value: str) -> float:
    channels = []
    for offset in (1, 3, 5):
        channel = _channel(value, offset)
        channels.append(channel / 12.92 if channel <= 0.04045 else ((channel + 0.055) / 1.055) ** 2.4)
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast_ratio(first: str, second: str) -> float:
    light = max(_luminance(first), _luminance(second))
    dark = min(_luminance(first), _luminance(second))
    return (light + 0.05) / (dark + 0.05)


def _check_contrast(colors: Mapping[str, str]) -> None:
    requirements = (("text", "background", 4.5), ("muted", "background", 4.5), ("accent", "background", 3.0))
    for foreground, background, minimum in requirements:
        ratio = _contrast_ratio(colors[foreground], colors[background])
        if ratio < minimum:
            raise _fail(
                "E_SCHEMA_VALUE",
                f"colors.{foreground} and colors.{background} contrast {ratio:.3f}; requires {minimum:.1f}",
            )


def _freeze_variants(value: Any) -> MappingProxyType:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "variants must be an object")
    expected = set(_VARIANT_DEFAULTS)
    if set(value) != expected:
        unknown = sorted(set(value) - expected)
        if unknown:
            kind = _field_kind(unknown[0])
            if kind == "geometry":
                raise _fail("E_VISUAL_GEOMETRY", f"variants contains unsupported coordinate token: {unknown[0]}")
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"variants contains unknown policy: {unknown[0]}")
        raise _fail("E_SCHEMA_MISSING_FIELD", f"variants is missing policy: {sorted(expected - set(value))[0]}")
    frozen: dict[str, MappingProxyType] = {}
    for variant, defaults in _VARIANT_DEFAULTS.items():
        group = _validate_closed_group(value[variant], defaults, f"variants.{variant}")
        parsed = {key: _parse_metric(item, f"variants.{variant}.{key}", minimum=1) for key, item in group.items()}
        if variant == "desktop":
            if parsed["width"] != 1200 or parsed["min_font_size"] < 16 or parsed["render_width"] != 900:
                raise _fail("E_VISUAL_GEOMETRY", "desktop policy requires width 1200, render width 900, and minimum text 16+")
        elif parsed["width"] > 720 or parsed["min_font_size"] < 24 or parsed["render_width"] != 360:
            raise _fail("E_VISUAL_GEOMETRY", "mobile policy requires width at most 720, render width 360, and minimum text 24+")
        if parsed["render_width"] > parsed["width"]:
            raise _fail("E_VISUAL_GEOMETRY", f"{variant} render width must not exceed its view width")
        frozen[variant] = MappingProxyType(parsed)
    return MappingProxyType(frozen)


def _validate_theme_maps(
    colors: Any,
    spacing: Any,
    strokes: Any,
    text: Any,
    variants: Any,
) -> tuple[MappingProxyType, MappingProxyType, MappingProxyType, MappingProxyType, MappingProxyType]:
    parsed_colors_raw = _validate_closed_group(colors, _COLOR_DEFAULTS, "colors")
    parsed_colors = {key: _parse_color(value, f"colors.{key}") for key, value in parsed_colors_raw.items()}
    _check_contrast(parsed_colors)

    parsed_spacing_raw = _validate_closed_group(spacing, _SPACING_DEFAULTS, "spacing")
    parsed_spacing = {key: _parse_metric(value, f"spacing.{key}") for key, value in parsed_spacing_raw.items()}
    parsed_strokes_raw = _validate_closed_group(strokes, _STROKE_DEFAULTS, "strokes")
    parsed_strokes = {key: _parse_metric(value, f"strokes.{key}", minimum=1) for key, value in parsed_strokes_raw.items()}
    parsed_text_raw = _validate_closed_group(text, _TEXT_DEFAULTS, "text")
    parsed_text = {key: _parse_metric(value, f"text.{key}", minimum=1) for key, value in parsed_text_raw.items()}
    if parsed_text["core"] < 16:
        raise _fail("E_VISUAL_TEXT_FIT", "text.core must be at least 16 for desktop")
    return (
        MappingProxyType(parsed_colors),
        MappingProxyType(parsed_spacing),
        MappingProxyType(parsed_strokes),
        MappingProxyType(parsed_text),
        _freeze_variants(variants),
    )


@dataclass(frozen=True, slots=True)
class Theme:
    """Immutable Theme v1 token map and independent variant policy."""

    schema_version: int
    colors: Mapping[str, str]
    spacing: Mapping[str, int]
    strokes: Mapping[str, int]
    text: Mapping[str, int]
    variants: Mapping[str, Mapping[str, int]]

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != THEME_SCHEMA_VERSION:
            raise _fail("E_SCHEMA_VERSION", "theme requires schema_version 1")
        colors, spacing, strokes, text, variants = _validate_theme_maps(
            self.colors, self.spacing, self.strokes, self.text, self.variants
        )
        object.__setattr__(self, "colors", colors)
        object.__setattr__(self, "spacing", spacing)
        object.__setattr__(self, "strokes", strokes)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "variants", variants)

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "colors": dict(self.colors),
            "spacing": dict(self.spacing),
            "strokes": dict(self.strokes),
            "text": dict(self.text),
            "variants": {variant: dict(policy) for variant, policy in self.variants.items()},
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def resolve_theme(repository_tokens: Mapping[str, Any] | None = None) -> Theme:
    """Resolve repository-safe token overrides into a canonical Theme v1."""

    colors = dict(_COLOR_DEFAULTS)
    spacing = dict(_SPACING_DEFAULTS)
    strokes = dict(_STROKE_DEFAULTS)
    text = dict(_TEXT_DEFAULTS)
    if repository_tokens is not None:
        if not isinstance(repository_tokens, Mapping):
            raise _fail("E_SCHEMA_TYPE", "repository tokens must be an object")
        payload = dict(repository_tokens)
        validate_bounded_json(payload)
        unknown = sorted(set(payload) - set(_GROUP_DEFAULTS))
        if unknown:
            kind = _field_kind(unknown[0])
            if kind == "path":
                raise _fail("E_VISUAL_PATH", f"repository tokens contain an unsupported path token: {unknown[0]}")
            if kind == "resource":
                raise _fail("E_VISUAL_RESOURCE", f"repository tokens contain an unsupported resource token: {unknown[0]}")
            if kind == "geometry":
                raise _fail("E_VISUAL_GEOMETRY", f"repository tokens contain an unsupported coordinate token: {unknown[0]}")
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"repository tokens contain unknown group: {unknown[0]}")
        for group_name, target in (("colors", colors), ("spacing", spacing), ("strokes", strokes), ("text", text)):
            if group_name not in payload:
                continue
            group = payload[group_name]
            if not isinstance(group, Mapping):
                raise _fail("E_SCHEMA_TYPE", f"repository tokens.{group_name} must be an object")
            parsed = _validate_closed_group(
                group,
                _GROUP_DEFAULTS[group_name],
                f"repository tokens.{group_name}",
                require_all=False,
            )
            target.update(parsed)
    return Theme(THEME_SCHEMA_VERSION, colors, spacing, strokes, text, _VARIANT_DEFAULTS)


__all__ = ["Theme", "resolve_theme"]
