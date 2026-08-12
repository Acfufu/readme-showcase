from __future__ import annotations

import importlib
import os
import re
from pathlib import Path
from typing import Any

_CONTRACTS = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
read_regular_bytes = _CONTRACTS.read_regular_bytes

from ..contracts.common import normalize_posix_path
from ..contracts.evidence import build_fact


VISUAL_KIND = "visual"
MAX_PALETTE_ITEMS = 32
MAX_TYPOGRAPHY_ITEMS = 32
MAX_TOKEN_LENGTH = 64
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_FILES_WALKED = 1024
_MAX_SVG_FILES = 32
_MAX_TOKEN_FILES = 16
# Directories whose contents are third-party, generated, or vendored material;
# their colors and fonts describe dependencies, not project identity.
_SKIP_DIRS = frozenset({
    ".cache", ".codex", ".git", ".mypy_cache", ".pytest_cache", ".ruff_cache",
    ".venv", "__pycache__", "build", "coverage", "dist", "node_modules",
    "vendor",
})
_DESIGN_TOKEN_NAMES = frozenset({
    "design-tokens.json", "theme.css", "theme.json", "tokens.css",
    "tokens.json", "variables.css",
})
_HEX_COLOR = re.compile(r"#[0-9a-fA-F]{6}\b")
# SVG attributes and CSS/JSON declarations alike: `font-family="..."`,
# `font-family: ...;`, or `"font-family": "..."`.
_FONT_STACK = re.compile(r"""font-family\s*(?::\s*|=)\s*((?:"[^"]*"(?:\s*,\s*"[^"]*")*)|(?:'[^']*'(?:\s*,\s*'[^']*')*)|(?:[^;}\n]+))""")
_NEUTRAL_HEX = re.compile(r"#([0-9a-fA-F]{2})\1\1\Z")


def _normalize_color(raw: str) -> str:
    return raw.lower()


def is_neutral_color(color: str) -> bool:
    return bool(_NEUTRAL_HEX.fullmatch(color))


def _bounded_unique(values: list[str], maximum: int) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        normalized = value.strip().strip("'\"")
        if not normalized:
            continue
        normalized = normalized[:MAX_TOKEN_LENGTH]
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append(normalized)
        if len(result) >= maximum:
            break
    return sorted(result)


def svg_tokens(text: str) -> dict[str, list[str]]:
    """Extract bounded palette and typography tokens from SVG/design-token text.

    Colors are every lowercase `#rrggbb` value in the text (fills, strokes,
    gradient stops); typography is every `font-family` stack split into its
    individual families.  Results are deduplicated, bounded, and sorted so a
    visual fact stays within the evidence contract.
    """
    colors = sorted({_normalize_color(raw) for raw in _HEX_COLOR.findall(text)})
    families: list[str] = []
    for stack in _FONT_STACK.findall(text):
        families.extend(part.strip().strip("'\"") for part in stack.split(","))
    return {
        "palette": _bounded_unique(colors, MAX_PALETTE_ITEMS),
        "typography": _bounded_unique(families, MAX_TYPOGRAPHY_ITEMS),
    }


def _design_tokens(text: str) -> dict[str, list[str]]:
    """Palette/typography tokens from a design-token JSON or CSS file."""
    return svg_tokens(text)


def _visual_material_files(root: Path) -> list[Path]:
    """Bounded walk collecting SVG assets and design-token files."""
    candidates: list[Path] = []
    svg_count = 0
    token_count = 0
    walked = 0
    for directory, names, files in os.walk(root):
        names[:] = sorted(name for name in names if name not in _SKIP_DIRS and not name.startswith("."))
        for name in sorted(files):
            walked += 1
            if walked > _MAX_FILES_WALKED:
                return candidates
            path = Path(directory) / name
            if name.endswith(".svg") and svg_count < _MAX_SVG_FILES:
                candidates.append(path)
                svg_count += 1
            elif name in _DESIGN_TOKEN_NAMES and token_count < _MAX_TOKEN_FILES:
                candidates.append(path)
                token_count += 1
    return candidates


def _read_bytes(path: Path) -> bytes | None:
    """Read one bounded, symlink-safe snapshot of a material file.

    ``read_regular_bytes`` opens with O_NOFOLLOW and verifies the regular-file
    identity (dev/ino/size/mtime) before and after the read, so the returned
    bytes are a single consistent snapshot.  None when missing or unsafe.
    """
    try:
        return read_regular_bytes(
            path,
            maximum=_MAX_SOURCE_BYTES,
            path_code="E_SCAN_IO",
            size_code="E_SCAN_IO",
        )
    except ContractError:
        return None


def _visual_fact(*, path: str, tokens: dict[str, list[str]], raw: bytes) -> dict[str, Any]:
    line_count = max(raw.count(b"\n") + 1, 1)
    return build_fact(
        kind=VISUAL_KIND,
        path=normalize_posix_path(path),
        locator={"line_start": 1, "line_end": line_count},
        semantic_key=f"visual:{normalize_posix_path(path)}",
        value={
            "palette": tokens["palette"],
            "typography": tokens["typography"],
        },
        source_bytes=raw,
        confidence="derived",
        derivation="palette and typography tokens derived from SVG assets and design-token files",
    )


def extract_visual_tokens(root: Path) -> list[dict[str, Any]]:
    """Extract visual identity facts at scan time from the target repository.

    The evaluator never touches the repository: it consumes these facts from
    the repository-evidence graph.  Sources are SVG assets (logos, favicons,
    diagrams) and design-token files; each source becomes one derived visual
    fact carrying its bounded palette and typography token lists.
    """
    facts: list[dict[str, Any]] = []
    for path in _visual_material_files(root):
        # One bounded snapshot feeds both tokenization and the fact's source
        # hash: tokens and source bytes can never disagree about file content.
        raw = _read_bytes(path)
        if raw is None:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        tokens = svg_tokens(text) if path.name.endswith(".svg") else _design_tokens(text)
        if not tokens["palette"] and not tokens["typography"]:
            continue
        relative = path.relative_to(root).as_posix()
        facts.append(_visual_fact(path=relative, tokens=tokens, raw=raw))
    return facts


__all__ = [
    "MAX_PALETTE_ITEMS",
    "MAX_TYPOGRAPHY_ITEMS",
    "extract_visual_tokens",
    "is_neutral_color",
    "svg_tokens",
]
