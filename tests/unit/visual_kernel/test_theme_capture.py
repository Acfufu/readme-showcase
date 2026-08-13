import json
import shutil
import subprocess
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.visual_kernel.theme_capture import capture_theme

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "visual"


@unittest.skipUnless(shutil.which("node"), "node not installed")
class ThemeCaptureTest(unittest.TestCase):
    def test_dark_capture_finds_no_defects_on_correct_theme_readme(self):
        # The light-only fragment is correctly hidden in dark (its own
        # #gh-dark-mode-only counterpart is visible); the file:// image
        # resolves and is not broken.
        readme = FIXTURES / "theme-readme.html"
        out = Path(__file__).resolve().parent / "theme-out"
        out.mkdir(exist_ok=True)
        try:
            findings = capture_theme(str(readme), str(out), "dark")
            if any("SKIPPED" in f for f in findings):
                self.skipTest("playwright not installed")
            self.assertEqual(findings, [])
            self.assertTrue((out / "theme-readme-dark.png").exists())
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            for html in out.glob("*.html"):
                html.unlink()
            out.rmdir()

    def test_node_script_contract(self):
        script = Path(__file__).resolve().parents[3] / "skill" / "scripts" / "capture_readme_theme.mjs"
        out = Path(__file__).resolve().parent / "theme-out-js"
        out.mkdir(exist_ok=True)
        try:
            result = subprocess.run(
                ["node", str(script), str(FIXTURES / "theme-readme.html"), str(out), "light"],
                capture_output=True, text=True, timeout=120,
            )
            if result.returncode != 0:
                self.skipTest(f"playwright not installed: {result.stderr[:200]}")
            payload = json.loads(result.stdout)
            self.assertTrue(payload["ok"])
            self.assertEqual(payload["theme"], "light")
            self.assertTrue(payload["images"])
            light_only = next(
                (image for image in payload["images"] if image["fragment"] == "light-only"),
                None,
            )
            self.assertIsNotNone(light_only, payload["images"])
            self.assertEqual(light_only["displayed"], "visible")
            self.assertFalse(light_only["broken"], "file:// image must resolve")
        finally:
            for png in out.glob("*.png"):
                png.unlink()
            for html in out.glob("*.html"):
                html.unlink()
            out.rmdir()
