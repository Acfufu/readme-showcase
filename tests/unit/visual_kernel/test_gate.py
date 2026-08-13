import json
import shutil
import unittest
import unittest.mock
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.gate import run_screenshot_gate

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "visual"


class GateTest(unittest.TestCase):
    @unittest.skipUnless(shutil.which("resvg") or shutil.which("rsvg-convert"), "no rasterizer")
    def test_ok_svg_passes_and_writes_screenshots(self):
        out = Path(__file__).resolve().parent / "gate-out"
        out.mkdir(exist_ok=True)
        try:
            report = run_screenshot_gate([str(FIXTURES / "hero-ok.svg")], str(out))
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["hard_gate"]["status"], "pass")
            self.assertEqual(len(report["screenshots"]), 1)
            for shot in report["screenshots"][0].values():
                if isinstance(shot, str) and shot.endswith(".png"):
                    self.assertTrue((out / shot).exists())
            with open(out / "screenshot-gate-report.v1.json", encoding="utf-8") as handle:
                json.load(handle)
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    def test_missing_rasterizer_reports_aesthetic_skip_note_not_hard_fail(self):
        # B7 posture: a missing rasterizer must NEVER produce a hard
        # E_SCREENSHOT_RASTER/E_RASTER_DEPENDENCY finding; it degrades to an
        # aesthetic skip note and the gate still passes when nothing else
        # fails.
        out = Path(__file__).resolve().parent / "gate-out-noraster"
        out.mkdir(exist_ok=True)
        try:
            with unittest.mock.patch(
                "skill.scripts.readme_showcase.visual_kernel.gate.render_svg",
                side_effect=ContractError("E_RASTER_DEPENDENCY", "no rasterizer"),
            ):
                report = run_screenshot_gate([str(FIXTURES / "hero-ok.svg")], str(out))
            self.assertEqual(report["status"], "pass")
            codes = {f["code"] for f in report["hard_gate"]["findings"]}
            self.assertNotIn("E_SCREENSHOT_RASTER", codes)
            self.assertNotIn("E_RASTER_DEPENDENCY", codes)
            self.assertTrue(any(
                f["code"] == "E_SCREENSHOT_RASTER_SKIP" for f in report["aesthetic_findings"]
            ))
        finally:
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    @unittest.skipUnless(shutil.which("resvg") or shutil.which("rsvg-convert"), "no rasterizer")
    def test_clipped_svg_fails_hard_gate(self):
        out = Path(__file__).resolve().parent / "gate-out-fail"
        out.mkdir(exist_ok=True)
        try:
            report = run_screenshot_gate([str(FIXTURES / "hero-clipped.svg")], str(out))
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["hard_gate"]["status"], "fail")
            self.assertTrue(any(
                f["code"] == "E_SCREENSHOT_CLIP" for f in report["hard_gate"]["findings"]
            ))
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            (out / "screenshot-gate-report.v1.json").unlink(missing_ok=True)
            out.rmdir()
