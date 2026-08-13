#!/usr/bin/env python3
"""CLI entry for the screenshot gate.

Usage:
  python3.11 skill/scripts/visual_screenshot_gate.py \
      --svg path/to/hero.svg [--svg ...] --out stages/07-validation/attempts/1/screenshots
      [--require-bbox]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# parents[2] = repo root (scripts -> skill -> root); required for the
# `skill.scripts.*` import to resolve, matching the test suite's import style.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from skill.scripts.readme_showcase.visual_kernel.gate import run_screenshot_gate


def main() -> int:
    parser = argparse.ArgumentParser(description="Render and check README SVG visuals.")
    parser.add_argument("--svg", action="append", required=True, help="SVG path (repeatable)")
    parser.add_argument("--out", required=True, help="output directory for PNGs and report")
    parser.add_argument("--require-bbox", action="store_true", help="treat missing bbox dependency as hard failure")
    args = parser.parse_args()
    report = run_screenshot_gate(args.svg, args.out, require_bbox=args.require_bbox)
    print(f"screenshot gate: {report['status']} ({len(report['hard_gate']['findings'])} hard findings, "
          f"{len(report['aesthetic_findings'])} aesthetic findings)")
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
