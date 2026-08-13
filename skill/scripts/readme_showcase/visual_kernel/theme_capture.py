"""Optional playwright theme track: HTML-layer verification of GitHub theme fragments.

Deliberately named theme_capture, NOT theme: visual_kernel/theme.py already
holds the closed Theme token dataclass used by the compiled route.
"""

from __future__ import annotations

import subprocess
from pathlib import Path


def capture_theme(readme_path: str, out_dir: str, theme: str, *, node_path: str = "node") -> list[str]:
    """Render README HTML with GitHub theme CSS and capture a screenshot.

    Returns plain findings; a SKIPPED: note when Node/playwright is missing.
    An image hidden by its own #gh-*-only fragment in the OTHER theme is
    CORRECT behavior (GitHub's theme mechanism) and is never flagged; only
    fragment/theme mismatches and broken images are findings.
    """
    script = Path(__file__).resolve().parents[3] / "scripts" / "capture_readme_theme.mjs"
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            [node_path, str(script), readme_path, str(out), theme],
            capture_output=True, text=True, timeout=180,
        )
    except FileNotFoundError:
        return ["SKIPPED: node not installed; theme capture skipped"]
    except subprocess.TimeoutExpired:
        return ["SKIPPED: theme capture timed out"]
    if result.returncode != 0:
        return [f"SKIPPED: {result.stderr.strip()[:200]}"]
    import json
    payload = json.loads(result.stdout)
    findings: list[str] = []
    for image in payload["images"]:
        if image["broken"]:
            findings.append(f"image {image['src']!r} failed to load")
            continue
        fragment = image["fragment"]
        expected_hidden = fragment != "both" and fragment != f"{theme}-only"
        actually_hidden = image["displayed"] == "hidden"
        if expected_hidden and not actually_hidden:
            findings.append(
                f"image {image['src']!r} ({fragment}) is visible in the {theme} theme "
                "though its theme fragment should hide it"
            )
        elif not expected_hidden and actually_hidden:
            findings.append(
                f"image {image['src']!r} ({fragment}) is hidden in its own {theme} theme"
            )
    return findings
