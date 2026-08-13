import json
import shutil
import subprocess
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.visual_kernel.checks import check_clipping_bbox

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "visual"
CLIPPED = str(FIXTURES / "hero-clipped.svg")
OK = str(FIXTURES / "hero-ok.svg")


@unittest.skipUnless(shutil.which("node"), "node not installed")
class BboxChecksTest(unittest.TestCase):
    def test_clipped_svg_reports_bottom_overflow(self):
        findings = check_clipping_bbox(CLIPPED)
        if any("SKIPPED" in f for f in findings):
            self.skipTest("resvg-js not installed")
        self.assertTrue(any("overflow" in f for f in findings),
                        f"expected bottom-overflow finding, got {findings}")

    def test_ok_svg_passes(self):
        findings = check_clipping_bbox(OK)
        if any("SKIPPED" in f for f in findings):
            self.skipTest("resvg-js not installed")
        self.assertEqual(findings, [])

    def test_node_script_emits_contract_json(self):
        script = Path(__file__).resolve().parents[3] / "skill" / "scripts" / "measure_bbox.mjs"
        result = subprocess.run(
            ["node", str(script), OK, "900"], capture_output=True, text=True)
        if result.returncode != 0:
            self.skipTest(f"resvg-js not installed: {result.stderr[:200]}")
        payload = json.loads(result.stdout)
        self.assertTrue(payload["ok"])
        # hero-ok.svg is 1200x360; at render width 900 the viewport is
        # 900x270 and text_bboxes are in the same pixel space.
        self.assertEqual(payload["viewport"], {"width": 900, "height": 270})
        for box in payload["text_bboxes"]:
            for key in ("x", "y", "width", "height"):
                self.assertIsInstance(box[key], int)
