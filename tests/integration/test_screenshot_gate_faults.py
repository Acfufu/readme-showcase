import shutil
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.visual_kernel.gate import run_screenshot_gate

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "visual"


class ScreenshotGateFaultsTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("resvg") or shutil.which("rsvg-convert"), "no rasterizer")
    def test_clipped_svg_fails(self):
        out = Path(__file__).resolve().parent / "faults-out"
        out.mkdir(exist_ok=True)
        try:
            report = run_screenshot_gate([str(FIXTURES / "hero-clipped.svg")], str(out))
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(
                f["code"] == "E_SCREENSHOT_CLIP" for f in report["hard_gate"]["findings"]
            ))
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    def test_missing_viewbox_fails_without_rasterizer(self):
        # Static SVG contract checks (audit_svg_bytes reuse) need no rasterizer,
        # so this test runs even when resvg/rsvg-convert is absent.
        out = Path(__file__).resolve().parent / "faults-out-vb"
        out.mkdir(exist_ok=True)
        try:
            report = run_screenshot_gate([str(FIXTURES / "hero-no-viewbox.svg")], str(out))
            self.assertEqual(report["status"], "fail")
            self.assertTrue(any(
                f["code"] == "E_SCREENSHOT_SVG" for f in report["hard_gate"]["findings"]
            ))
            self.assertTrue(any(
                "viewBox" in f["message"] for f in report["hard_gate"]["findings"]
            ))
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    def test_weak_contrast_reports_aesthetic_finding(self):
        out = Path(__file__).resolve().parent / "faults-out-contrast"
        out.mkdir(exist_ok=True)
        try:
            report = run_screenshot_gate([str(FIXTURES / "hero-weak-contrast.svg")], str(out))
            self.assertTrue(any(
                f["code"] == "E_SCREENSHOT_CONTRAST" for f in report["aesthetic_findings"]
            ))
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()
