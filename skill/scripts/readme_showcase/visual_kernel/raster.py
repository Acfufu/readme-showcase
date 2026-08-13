"""Deterministic SVG rasterization via resvg CLI with rsvg-convert fallback."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from ...pipeline_contracts import ContractError


def _run(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True)
    except FileNotFoundError as exc:
        # The chosen rasterizer binary does not exist: a dependency failure,
        # not an input failure. The caller decides skip-vs-hard posture.
        raise ContractError(
            "E_RASTER_DEPENDENCY",
            f"rasterizer not found: {command[0]} (install resvg or librsvg)",
        ) from exc
    if result.returncode != 0:
        raise ContractError(
            "E_RASTER_FAILED",
            f"rasterizer failed: {' '.join(command)} :: {result.stderr.strip()[:400]}",
        )


def render_svg(svg_path: str, output_png: str, width: int, *, rasterizer: str | None = None) -> None:
    """Render an SVG to PNG at a given pixel width.

    Prefers the resvg CLI (higher SVG-spec conformance than librsvg); falls
    back to rsvg-convert. Raises ContractError when no rasterizer is present.
    """
    svg = Path(svg_path)
    if not svg.exists():
        raise ContractError("E_RASTER_INPUT", f"svg not found: {svg_path}")
    chosen = rasterizer or (shutil.which("resvg") and "resvg") or (shutil.which("rsvg-convert") and "rsvg-convert")
    if not chosen:
        raise ContractError(
            "E_RASTER_DEPENDENCY",
            "no SVG rasterizer found; install resvg (brew install resvg) or librsvg (rsvg-convert)",
        )
    if chosen == "resvg":
        _run([chosen, "--width", str(width), "--background", "white", str(svg), str(output_png)])
    else:
        _run([chosen, "-w", str(width), "-b", "white", "-o", str(output_png), str(svg)])
