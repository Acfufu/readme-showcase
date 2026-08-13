import shutil
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.visual_kernel.checks import (
    check_clipping_pixels,
    check_readability_at_360,
    check_svg_contrast,
    check_svg_contract,
    contrast_ratio,
)
from skill.scripts.readme_showcase.visual_kernel.raster import render_svg

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "visual"
OK = str(FIXTURES / "hero-ok.svg")
CLIPPED = str(FIXTURES / "hero-clipped.svg")


class ChecksTest(unittest.TestCase):
    def test_contrast_ratio_black_on_white(self):
        self.assertGreater(contrast_ratio("#000000", "#ffffff"), 15)

    def test_contrast_ratio_weak_pair(self):
        self.assertLess(contrast_ratio("#888888", "#ffffff"), 4.5)

    def test_svg_contract_ok_svg_passes(self):
        self.assertEqual(check_svg_contract(OK), [])

    def test_svg_contract_missing_viewbox_fails(self):
        findings = check_svg_contract(str(FIXTURES / "hero-no-viewbox.svg"))
        self.assertTrue(any("viewBox" in f for f in findings),
                        f"expected a viewBox finding, got {findings}")

    def test_clipping_detects_bottom_overflow(self):
        if not (shutil.which("resvg") or shutil.which("rsvg-convert")):
            self.skipTest("no rasterizer")
        png = str(FIXTURES / "clipped-render.png")
        try:
            render_svg(CLIPPED, png, width=900)
            findings = check_clipping_pixels(png)
            self.assertTrue(findings, "clipped SVG must touch the image edge")
        finally:
            Path(png).unlink(missing_ok=True)

    def test_clipping_dark_bleed_rect_is_background_not_clipping(self):
        # hero-ok.svg has a full-bleed dark rect; the dominant corner color is
        # the background, so the pixel check must NOT report clipping.
        if not (shutil.which("resvg") or shutil.which("rsvg-convert")):
            self.skipTest("no rasterizer")
        png = str(FIXTURES / "ok-render.png")
        try:
            render_svg(OK, png, width=900)
            findings = check_clipping_pixels(png)
            self.assertEqual(findings, [], f"full-bleed bg must not clip, got {findings}")
        finally:
            Path(png).unlink(missing_ok=True)

    def test_svg_contrast_weak_text_found(self):
        findings = check_svg_contrast(str(FIXTURES / "hero-weak-contrast.svg"))
        self.assertTrue(findings)

    def test_readability_ok_svg_passes(self):
        self.assertEqual(check_readability_at_360(OK), [])

    def test_readability_flags_small_text(self):
        findings = check_readability_at_360(str(FIXTURES / "hero-small-text.svg"))
        self.assertTrue(any("readable" in f for f in findings))
