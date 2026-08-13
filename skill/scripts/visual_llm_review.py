#!/usr/bin/env python3
"""CLI entry for the vision-LLM aesthetic review track (optional advisory).

Default `--model auto` inherits the host session's LLM: resolves
VISION_REVIEW_MODEL / session probe with API key; otherwise writes a review
brief for the host agent to complete in-session (status "host").

Usage:
  python3.11 skill/scripts/visual_llm_review.py \
      --screenshots stages/07-validation/attempts/1/screenshots/hero-ok-900.png \
      --out stages/07-validation/attempts/1/ \
      [--model auto|gpt-4o|claude-...] [--reference assets/readme/reference-hero.png]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# parents[2] = repo root (scripts -> skill -> root); required for the
# `skill.scripts.*` import to resolve, matching the test suite's import style.
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from skill.scripts.readme_showcase.visual_kernel.review import review_screenshots


def main() -> int:
    parser = argparse.ArgumentParser(description="Vision-LLM aesthetic review of screenshots.")
    parser.add_argument("--screenshots", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--model", default="auto",
                        help="reviewer model; 'auto' inherits the host session's LLM")
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key-env", default="VISION_REVIEW_API_KEY")
    parser.add_argument("--reference", default=None, help="quality-bar screenshot for pairwise A/B")
    args = parser.parse_args()
    report = review_screenshots(
        args.screenshots, args.out, model=args.model, api_base=args.api_base,
        api_key_env=args.api_key_env, reference=args.reference,
    )
    print(f"vision review: {report['status']} via {report['reviewer_model']} "
          f"({len(report['criteria'])} criteria)")
    # host/skipped are advisory outcomes; "fail" (API verdicts) exits 1.
    return 0 if report["status"] in ("pass", "host", "skipped") else 1


if __name__ == "__main__":
    raise SystemExit(main())
