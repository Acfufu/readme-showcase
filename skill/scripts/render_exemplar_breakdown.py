#!/usr/bin/env python3
"""Generate annotated structural breakdown SVGs for synthetic exemplars.

Breakdowns mark STRUCTURE only (composition zones, hierarchy levels,
negative patterns) — never style. They teach composition, not aesthetics.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from xml.sax.saxutils import escape

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from skill.scripts.pipeline_contracts import ContractError

_ZONE_LABELS = {"identity", "proof", "metadata", "context", "workflow"}
_ZONE_COLORS = {"identity": "#22c55e", "proof": "#3b82f6", "metadata": "#94a3b8",
                "context": "#a855f7", "workflow": "#f59e0b"}
_ZONE_X = {"identity": 60, "proof": 620, "metadata": 60, "context": 60, "workflow": 620}
_ZONE_Y = {"identity": 80, "proof": 80, "metadata": 260, "context": 60, "workflow": 200}
_ZONE_W = {"identity": 480, "proof": 520, "metadata": 1080, "context": 480, "workflow": 520}
_ZONE_H = {"identity": 160, "proof": 160, "metadata": 60, "context": 160, "workflow": 100}


def render_breakdown_svg(composition: dict[str, list[str]], output_svg: str) -> None:
    zones = composition.get("zones") or []
    hierarchy = composition.get("hierarchy") or []
    negative = composition.get("negative_patterns") or []
    if not zones:
        raise ContractError("E_BREAKDOWN_ZONES", "zones must not be empty")
    if not hierarchy:
        raise ContractError("E_BREAKDOWN_HIERARCHY", "hierarchy must not be empty")
    parts = [
        '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="360" '
        'viewBox="0 0 1200 360" role="img" aria-labelledby="title desc">',
        "<title id=\"title\">Structural breakdown</title>",
        "<desc id=\"desc\">Composition zones, hierarchy levels, and negative patterns.</desc>",
        '<rect width="1200" height="360" fill="#0f172a"/>',
        '<text x="60" y="40" font-size="20" fill="#e2e8f0">Composition zones (structure only)</text>',
    ]
    for zone in zones:
        if zone not in _ZONE_LABELS:
            raise ContractError("E_BREAKDOWN_ZONE", f"unknown zone {zone!r}")
        color = _ZONE_COLORS[zone]
        parts.append(
            f'<rect x="{_ZONE_X[zone]}" y="{_ZONE_Y[zone]}" width="{_ZONE_W[zone]}" '
            f'height="{_ZONE_H[zone]}" fill="none" stroke="{color}" stroke-width="3"/>'
        )
        parts.append(
            f'<text x="{_ZONE_X[zone] + 12}" y="{_ZONE_Y[zone] + 28}" font-size="18" '
            f'fill="{color}">{zone}</text>'
        )
    parts.append('<text x="60" y="180" font-size="16" fill="#94a3b8">Hierarchy: '
                 + " &gt; ".join(escape(level) for level in hierarchy) + "</text>")
    for index, pattern in enumerate(negative):
        y = 220 + index * 28
        parts.append(
            f'<text x="60" y="{y}" font-size="16" fill="#f87171" '
            f'text-decoration="line-through">{escape(pattern)}</text>'
        )
    parts.append('<text x="60" y="300" font-size="14" fill="#64748b">Negative patterns '
                 '(banned, shown crossed out)</text>')
    parts.append("</svg>")
    Path(output_svg).write_text("\n".join(parts), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate annotated breakdown SVG.")
    parser.add_argument("--zones", required=True)
    parser.add_argument("--hierarchy", required=True)
    parser.add_argument("--negative", default="")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    render_breakdown_svg(
        {"zones": args.zones.split(","),
         "hierarchy": args.hierarchy.split(","),
         "negative_patterns": [p for p in args.negative.split(",") if p]},
        args.out,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
