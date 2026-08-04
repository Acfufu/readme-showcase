"""Deterministic, font-free measurement and fitting for visual labels.

Widths use a deliberately conservative local calibration table.  Values in
``CHARACTER_CLASS_WIDTHS`` are quarter-em units; a cluster's CSS width is
``ceil(units * font_size / 4)``.  Unicode categories and East Asian width are
the only inputs, so no font, platform resource, or process-global state can
change a fit decision.

Wrapping prefers normalized word boundaries and falls back to complete
Unicode grapheme-like clusters.  The small cluster scanner handles combining
marks, variation selectors, emoji modifiers, regional-indicator pairs, and
zero-width-joiner sequences without a third-party regex package.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256


TEXT_SCHEMA_VERSION = 1
_WIDTH_SCALE = 4
_MAX_TEXT_BYTES = 4096

# Quarter-em units.  These intentionally err on the wide side for text that
# has no installed font: a later renderer may fit more, never less, than this
# gate predicted.
_CHARACTER_CLASS_WIDTHS: Mapping[str, int] = MappingProxyType(
    {
        "control": 0,
        "mark": 0,
        "space": 2,
        "latin": 5,
        "digit": 5,
        "punctuation": 4,
        "wide": 8,
        "emoji": 8,
        "symbol": 6,
        "other": 6,
    }
)

_TEXT_VARIANTS = frozenset({"desktop", "mobile"})
_MIN_FONT_SIZE: Mapping[str, int] = MappingProxyType({"desktop": 16, "mobile": 24})

# These semantic label roles carry bounded line budgets, not typography tokens.
_ROLE_LINE_BUDGETS: Mapping[str, int] = MappingProxyType(
    {
        "core": 3,
        "title": 2,
        "node": 3,
        "label": 3,
        "edge": 1,
        "group": 2,
        "lane": 2,
        "caption": 4,
        "description": 4,
    }
)
_TEXT_ROLES = frozenset(_ROLE_LINE_BUDGETS)

TextStatus = Literal["fit", "fail"]


def _error(message: str, code: str = "E_VISUAL_TEXT_FIT") -> ContractError:
    return ContractError(code, message)


def _normalize_label(value: Any) -> str:
    """Return NFC text with all Unicode whitespace collapsed to one space.

    Whitespace normalization is the only intentional text transformation.  A
    label must remain non-empty and bounded; no truncation or ellipsis is
    performed here or by :func:`fit_text`.
    """

    if not isinstance(value, str) or "\x00" in value:
        raise _error("label must be a string without NUL")
    normalized = unicodedata.normalize("NFC", value)
    normalized = " ".join(normalized.split())
    if not normalized:
        raise _error("label must contain visible text")
    if len(normalized.encode("utf-8")) > _MAX_TEXT_BYTES:
        raise _error(f"label exceeds {_MAX_TEXT_BYTES} UTF-8 bytes")
    return normalized


# Unicode variation selectors and emoji modifiers are combining-like for the
# purpose of a renderer-neutral cluster.  Tags make a single flag sequence.
def _is_extend(char: str) -> bool:
    codepoint = ord(char)
    return (
        unicodedata.combining(char) != 0
        or unicodedata.category(char) in {"Mn", "Mc", "Me"}
        or 0xFE00 <= codepoint <= 0xFE0F
        or 0xE0100 <= codepoint <= 0xE01EF
        or 0x1F3FB <= codepoint <= 0x1F3FF
        or 0xE0020 <= codepoint <= 0xE007F
    )


def _is_regional_indicator(char: str) -> bool:
    return 0x1F1E6 <= ord(char) <= 0x1F1FF


def _grapheme_clusters(value: str) -> tuple[str, ...]:
    """Split text at safe cluster boundaries without external dependencies."""

    if not isinstance(value, str):
        raise _error("grapheme input must be text")
    clusters: list[str] = []
    current = ""
    regional_count = 0
    for char in value:
        if not current:
            current = char
            regional_count = 1 if _is_regional_indicator(char) else 0
            continue
        if (
            _is_extend(char)
            or char == "\u200d"
            or current.endswith("\u200d")
            or (all(_is_extend(item) for item in current) and not _is_extend(char))
            or (_is_regional_indicator(char) and regional_count == 1)
        ):
            current += char
            if _is_regional_indicator(char):
                regional_count += 1
            continue
        clusters.append(current)
        current = char
        regional_count = 1 if _is_regional_indicator(char) else 0
    if current:
        clusters.append(current)
    return tuple(clusters)


def _emoji_cluster(cluster: str) -> bool:
    return any(
        (
            0x1F000 <= ord(char) <= 0x1FAFF
            or 0x2300 <= ord(char) <= 0x23FF
            or 0x2600 <= ord(char) <= 0x27BF
            or _is_regional_indicator(char)
        )
        for char in cluster
    )


def _character_class(cluster: str) -> str:
    """Classify one already-segmented cluster using the local calibration."""

    if not cluster:
        raise _error("character cluster must not be empty")
    base = tuple(char for char in cluster if not _is_extend(char) and char != "\u200d")
    if not base:
        return "mark"
    if all(char.isspace() for char in base):
        return "space"
    if _emoji_cluster(cluster):
        return "emoji"
    if any(unicodedata.east_asian_width(char) in {"W", "F"} for char in base):
        return "wide"
    first = base[0]
    category = unicodedata.category(first)
    if category.startswith("C"):
        return "control"
    if category.startswith("P"):
        return "punctuation"
    if category.startswith("S"):
        return "symbol"
    if first.isdigit():
        return "digit"
    if first.isalpha():
        return "latin"
    return "other"


def _cluster_width(cluster: str, font_size: int) -> int:
    units = _CHARACTER_CLASS_WIDTHS[_character_class(cluster)]
    return (units * font_size + _WIDTH_SCALE - 1) // _WIDTH_SCALE


def _validate_font_size(font_size: Any, *, variant: str | None = None) -> int:
    if font_size is None:
        return _MIN_FONT_SIZE[variant or "desktop"]
    if type(font_size) is not int or font_size <= 0:
        raise _error("font_size must be a positive integer")
    if variant is not None and font_size < _MIN_FONT_SIZE[variant]:
        raise _error(f"font_size must be at least {_MIN_FONT_SIZE[variant]} for {variant}")
    return font_size


def _validate_variant(variant: Any) -> str:
    if not isinstance(variant, str):
        raise _error("variant must be a string", "E_SCHEMA_TYPE")
    if variant not in _TEXT_VARIANTS:
        raise _error("variant must be desktop or mobile", "E_SCHEMA_VALUE")
    return variant


def _validate_role(role: Any) -> str:
    if not isinstance(role, str):
        raise _error("role must be a string", "E_SCHEMA_TYPE")
    if role not in _TEXT_ROLES:
        raise _error(f"role must be one of {', '.join(sorted(_TEXT_ROLES))}", "E_SCHEMA_VALUE")
    return role


def _validate_width(width: Any) -> int:
    if type(width) is not int or width < 0:
        raise _error("width must be a non-negative integer")
    return width


def _measure_text(
    text: Any,
    font_size: int | None = None,
    *,
    variant: str | None = None,
) -> int:
    """Measure normalized text in deterministic integer CSS pixels."""

    if variant is not None:
        variant = _validate_variant(variant)
    size = _validate_font_size(font_size, variant=variant)
    normalized = _normalize_label(text)
    return sum(_cluster_width(cluster, size) for cluster in _grapheme_clusters(normalized))


def _split_word(word: str, width: int, font_size: int) -> tuple[tuple[str, ...], tuple[int, ...]] | None:
    clusters = _grapheme_clusters(word)
    chunks: list[str] = []
    widths: list[int] = []
    current: list[str] = []
    current_width = 0
    for cluster in clusters:
        cluster_width = _cluster_width(cluster, font_size)
        if cluster_width > width:
            return None
        if current and current_width + cluster_width > width:
            chunks.append("".join(current))
            widths.append(current_width)
            current = []
            current_width = 0
        current.append(cluster)
        current_width += cluster_width
    if current:
        chunks.append("".join(current))
        widths.append(current_width)
    return tuple(chunks), tuple(widths)


def _wrap(normalized: str, width: int, font_size: int) -> tuple[tuple[str, ...], tuple[int, ...]] | None:
    """Greedily wrap words, splitting only at complete grapheme clusters."""

    lines: list[str] = []
    widths: list[int] = []
    current = ""
    current_width = 0
    space_width = _cluster_width(" ", font_size)
    for word in normalized.split(" "):
        word_width = _measure_text(word, font_size)
        if word_width <= width:
            if current and current_width + space_width + word_width <= width:
                current += " " + word
                current_width += space_width + word_width
            else:
                if current:
                    lines.append(current)
                    widths.append(current_width)
                current = word
                current_width = word_width
            continue

        if current:
            lines.append(current)
            widths.append(current_width)
            current = ""
            current_width = 0
        split = _split_word(word, width, font_size)
        if split is None:
            return None
        chunks, chunk_widths = split
        if len(chunks) > 1:
            lines.extend(chunks[:-1])
            widths.extend(chunk_widths[:-1])
        current = chunks[-1]
        current_width = chunk_widths[-1]
    if current:
        lines.append(current)
        widths.append(current_width)
    return tuple(lines), tuple(widths)


@dataclass(frozen=True, slots=True)
class TextFitResult:
    """Immutable fit/fail projection consumed by later geometry gates."""

    status: TextStatus
    text: str
    lines: tuple[str, ...]
    widths: tuple[int, ...]
    max_width: int
    role: str
    variant: str
    font_size: int
    line_budget: int
    error_code: str | None = None
    message: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"fit", "fail"}:
            raise ValueError("text status must be fit or fail")
        if not isinstance(self.lines, tuple) or not isinstance(self.widths, tuple):
            raise TypeError("text lines and widths must be tuples")
        if len(self.lines) != len(self.widths):
            raise ValueError("text lines and widths must have equal length")
        if any(type(width) is not int or width < 0 for width in self.widths):
            raise ValueError("text widths must be non-negative integers")
        if self.status == "fit" and self.error_code is not None:
            raise ValueError("fit result must not carry an error")
        if self.status == "fail" and not self.error_code:
            raise ValueError("fail result must carry an error code")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": TEXT_SCHEMA_VERSION,
            "status": self.status,
            "text": self.text,
            "lines": list(self.lines),
            "widths": list(self.widths),
            "max_width": self.max_width,
            "role": self.role,
            "variant": self.variant,
            "font_size": self.font_size,
            "line_budget": self.line_budget,
            "error_code": self.error_code,
            "message": self.message,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def _failure(
    *,
    text: str = "",
    width: int = -1,
    role: str = "",
    variant: str = "",
    font_size: int = 0,
    line_budget: int = 0,
    lines: tuple[str, ...] = (),
    widths: tuple[int, ...] = (),
    message: str,
) -> TextFitResult:
    return TextFitResult(
        "fail",
        text,
        lines,
        widths,
        width,
        role,
        variant,
        font_size,
        line_budget,
        "E_VISUAL_TEXT_FIT",
        message,
    )


def fit_text(
    text: Any,
    width: int | None = None,
    role: str = "core",
    variant: str = "desktop",
    font_size: int | None = None,
) -> TextFitResult:
    """Fit a label and return an immutable success or ``E_VISUAL_TEXT_FIT``."""

    checked_variant = _validate_variant(variant)
    checked_role = _validate_role(role)
    checked_width = _validate_width(width)
    checked_font_size = _validate_font_size(font_size, variant=checked_variant)
    normalized = _normalize_label(text)

    wrapped = _wrap(normalized, checked_width, checked_font_size)
    if wrapped is None:
        return _failure(
            text=normalized,
            width=checked_width,
            role=checked_role,
            variant=checked_variant,
            font_size=checked_font_size,
            line_budget=_ROLE_LINE_BUDGETS[checked_role],
            message="one or more grapheme clusters exceed the available width",
        )
    lines, widths = wrapped
    budget = _ROLE_LINE_BUDGETS[checked_role]
    if len(lines) > budget:
        return _failure(
            text=normalized,
            width=checked_width,
            role=checked_role,
            variant=checked_variant,
            font_size=checked_font_size,
            line_budget=budget,
            lines=lines,
            widths=widths,
            message=f"label requires {len(lines)} lines; {checked_role} allows {budget}",
        )
    return TextFitResult(
        "fit",
        normalized,
        lines,
        widths,
        checked_width,
        checked_role,
        checked_variant,
        checked_font_size,
        budget,
    )


__all__ = [
    "TextFitResult",
    "fit_text",
]
