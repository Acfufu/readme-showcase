from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

try:
    from PIL import Image
    from skill.scripts import render_motion_gif
except (ImportError, SystemExit):  # The legacy-all CI job does not install Pillow.
    Image = None  # type: ignore[assignment]
    render_motion_gif = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skill/scripts/render_motion_gif.py"
HERO_SVG = REPO_ROOT / "assets/readme/hero.svg"
HERO_SPEC = REPO_ROOT / "assets/readme/hero-motion.json"
HERO_SHA256 = "f65a4f6888b29a32497cd61982d835882872daefbb22e1aa64f0f770f138315d"


@unittest.skipUnless(render_motion_gif is not None, "Pillow is required for motion renderer tests")
class MotionRendererTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="readme-motion-test-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _require_external_renderer(self) -> None:
        if not shutil.which("ffmpeg"):
            self.skipTest("ffmpeg is required for motion renderer tests")
        if not shutil.which("rsvg-convert") and not shutil.which("sips"):
            self.skipTest("rsvg-convert or sips is required for motion renderer tests")

    def _run(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )

    def _write_timeline(self, *, target: str = "moving") -> Path:
        path = self.root / "timeline.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "targets": [target],
                    "duration_ms": 120,
                    "operations": [
                        {
                            "id": f"reveal:{target}",
                            "kind": "reveal",
                            "target": target,
                            "start_ms": 0,
                            "end_ms": 120,
                        }
                    ],
                    "reduced_motion": {"mode": "static", "visible": [target]},
                }
            ),
            encoding="utf-8",
        )
        return path

    def _write_svg(self, *, target: str = "moving") -> Path:
        path = self.root / "fixture.svg"
        path.write_text(
            f'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80" viewBox="0 0 160 80">
  <rect width="160" height="80" fill="#ffffff"/>
  <rect id="{target}" x="24" y="24" width="112" height="32" fill="#111111"/>
</svg>
''',
            encoding="utf-8",
        )
        return path

    def test_explicit_timeline_renders_and_preserves_reduced_motion_projection(self) -> None:
        self._require_external_renderer()
        svg = self._write_svg()
        timeline = self._write_timeline()
        output = self.root / "timeline.gif"

        result = self._run(str(svg), str(output), "--timeline", str(timeline))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreater(output.stat().st_size, 0)
        self.assertIsNotNone(render_motion_gif)
        projection = render_motion_gif.load_timeline(timeline)
        self.assertEqual(
            projection["reduced_motion"],
            {"mode": "static", "visible": ["moving"]},
        )
        with Image.open(output) as image:
            self.assertEqual(image.format, "GIF")
            self.assertGreaterEqual(getattr(image, "n_frames", 1), 1)

    def test_spec_command_keeps_existing_hero_bytes(self) -> None:
        self._require_external_renderer()
        output = self.root / "hero.gif"
        result = self._run(str(HERO_SVG), str(output), "--spec", str(HERO_SPEC))

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            hashlib.sha256(output.read_bytes()).hexdigest(),
            HERO_SHA256,
        )

    def test_timeline_source_is_required_and_mutually_exclusive(self) -> None:
        neither = self._run(str(HERO_SVG), str(self.root / "none.gif"))
        self.assertEqual(neither.returncode, 2)
        self.assertIn("one of the arguments --spec --timeline is required", neither.stderr)

        both = self._run(
            str(HERO_SVG),
            str(self.root / "both.gif"),
            "--spec",
            str(HERO_SPEC),
            "--timeline",
            str(self._write_timeline()),
        )
        self.assertEqual(both.returncode, 2)
        self.assertIn("argument --timeline: not allowed with argument --spec", both.stderr)

    def test_installed_layout_direct_script_imports_and_reaches_timeline_validation(self) -> None:
        installed = self.root / "installed"
        installed.mkdir()
        shutil.copytree(
            REPO_ROOT / "skill/scripts",
            installed / "scripts",
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )
        help_result = subprocess.run(
            [sys.executable, str(installed / "scripts/render_motion_gif.py"), "--help"],
            cwd=installed,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn("--timeline TIMELINE", help_result.stdout)

        invalid = self.root / "installed-invalid.json"
        invalid.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "targets": ["moving"],
                    "duration_ms": 120,
                    "operations": [
                        {
                            "id": "reveal:missing",
                            "kind": "reveal",
                            "target": "missing",
                            "start_ms": 0,
                            "end_ms": 120,
                        }
                    ],
                    "reduced_motion": {"mode": "static", "visible": ["moving"]},
                }
            ),
            encoding="utf-8",
        )
        validation_result = subprocess.run(
            [
                sys.executable,
                str(installed / "scripts/render_motion_gif.py"),
                str(HERO_SVG),
                str(self.root / "installed.gif"),
                "--timeline",
                str(invalid),
            ],
            cwd=installed,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertNotEqual(validation_result.returncode, 0)
        self.assertIn("E_VISUAL_SPEC_EDGE", validation_result.stderr)
        self.assertNotIn("ModuleNotFoundError", validation_result.stderr)

    def test_invalid_timeline_fails_before_frame_directory_or_output_replacement(self) -> None:
        timeline = self.root / "invalid.json"
        timeline.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "targets": ["moving"],
                    "duration_ms": 120,
                    "operations": [
                        {
                            "id": "reveal:missing",
                            "kind": "reveal",
                            "target": "missing",
                            "start_ms": 0,
                            "end_ms": 120,
                        }
                    ],
                    "reduced_motion": {"mode": "static", "visible": ["moving"]},
                }
            ),
            encoding="utf-8",
        )
        output = self.root / "existing.gif"
        output.write_bytes(b"previous-output")
        frames_root = self.root / "kept-frames"
        frames_root.mkdir()

        result = self._run(
            str(HERO_SVG),
            str(output),
            "--timeline",
            str(timeline),
            "--keep-frames",
            str(frames_root),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("E_VISUAL_SPEC_EDGE", result.stderr)
        self.assertEqual(output.read_bytes(), b"previous-output")
        self.assertFalse((frames_root / "frames").exists())

    def test_stale_svg_target_fails_before_output_replacement(self) -> None:
        self._require_external_renderer()
        timeline = self._write_timeline(target="missing")
        svg = self._write_svg(target="moving")
        output = self.root / "existing.gif"
        output.write_bytes(b"previous-output")
        frames_root = self.root / "kept-frames"
        frames_root.mkdir()

        result = self._run(
            str(svg),
            str(output),
            "--timeline",
            str(timeline),
            "--keep-frames",
            str(frames_root),
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("SVG element id not found: missing", result.stderr)
        self.assertEqual(output.read_bytes(), b"previous-output")
        self.assertFalse((frames_root / "frames").exists())


if __name__ == "__main__":
    unittest.main()
