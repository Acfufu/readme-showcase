from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from argparse import Namespace
from pathlib import Path
from unittest import mock

try:
    from PIL import Image
    from skill.scripts import render_motion_gif
except (ImportError, SystemExit):  # The legacy-all CI job does not install Pillow.
    Image = None  # type: ignore[assignment]
    render_motion_gif = None  # type: ignore[assignment]

from skill.scripts.pipeline_contracts import ContractError

try:
    from skill.scripts.render_static_frame import render_static_frame
except (ImportError, SystemExit):
    render_static_frame = None  # type: ignore[assignment]


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skill/scripts/render_motion_gif.py"
HERO_SVG = REPO_ROOT / "assets/readme/hero.svg"
HERO_SPEC = REPO_ROOT / "assets/readme/hero-motion.json"
HERO_SHA256 = "e54ed1893de5f37d1b315f10d33a1be08dbbb789785bb389db5c75485cacbd55"


MOTION_PRODUCTION = REPO_ROOT / "skill/references/motion-production.md"


class MotionProductionContractTests(unittest.TestCase):
    """Doc contracts for motion-production.md (no Pillow required)."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.text = MOTION_PRODUCTION.read_text(encoding="utf-8")

    def test_doc_asserts_dual_engine_playback_matrix(self) -> None:
        self.assertIn("SMIL", self.text)
        self.assertIn("@keyframes", self.text)
        self.assertIn("Chrome 151", self.text)
        self.assertIn("Firefox 153", self.text)

    def test_doc_contains_static_frame_contract(self) -> None:
        lowered = self.text.lower()
        self.assertIn("frozen frame", lowered)
        self.assertIn("reduced-motion", lowered)

    def test_doc_describes_static_frame_generator(self) -> None:
        self.assertIn("render_static_frame", self.text)


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

    def _write_spec(self, **updates: object) -> Path:
        path = self.root / "motion.json"
        payload = json.loads(HERO_SPEC.read_text(encoding="utf-8"))
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_v2_spec(self, **updates: object) -> Path:
        path = self.root / "motion-v2.json"
        payload: dict[str, object] = {
            "schema_version": 2,
            "width": 1200,
            "fps": 30,
            "duration": 8.0,
            "colors": 192,
            "dither": "none",
            "transparent_color": "#ff00ff",
            "alpha_threshold": 128,
            "clip_to_base_alpha": False,
            "max_size_mb": 2.0,
            "scenes": [
                {
                    "id": "moving",
                    "interpolation": "linear",
                    "enter": {"start": 0.2, "end": 0.9},
                    "hold": {"start": 0.9, "end": 7.2},
                }
            ],
            "typewriter": {"mode": "per-char", "locale": "en", "char_width_factor": 0.6},
            "reduced_motion": {"mode": "static", "visible": ["moving"]},
        }
        payload.update(updates)
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def _write_timeline_v2(self) -> Path:
        path = self.root / "timeline-v2.json"
        path.write_text(
            json.dumps(
                {
                    "schema_version": 2,
                    "targets": ["moving"],
                    "duration_ms": 1_200,
                    "scenes": [
                        {
                            "id": "moving",
                            "interpolation": "linear",
                            "enter": {"start_ms": 0, "end_ms": 600},
                            "hold": {"start_ms": 600, "end_ms": 1_200},
                        }
                    ],
                    "typewriter": {"mode": "per-char", "locale": "en"},
                    "reduced_motion": {"mode": "static", "visible": ["moving"]},
                }
            ),
            encoding="utf-8",
        )
        return path

    def test_v2_spec_loads_validates_and_renders(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        spec_path = self._write_v2_spec()
        spec = render_motion_gif.load_spec(spec_path)
        render_motion_gif.validate_spec(copy.deepcopy(spec))
        self.assertEqual(spec["schema_version"], 2)
        self.assertEqual(spec["duration"], 8.0)
        self.assertEqual(len(spec["scenes"]), 1)
        self.assertEqual(spec["typewriter"]["char_width_factor"], 0.6)

        self._require_external_renderer()
        svg = self._write_svg()
        output = self.root / "v2.gif"
        result = self._run(str(svg), str(output), "--spec", str(spec_path))
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(output) as image:
            self.assertEqual(image.format, "GIF")
            self.assertGreaterEqual(getattr(image, "n_frames", 1), 1)

    def test_v2_timeline_projects_and_renders(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        timeline = self._write_timeline_v2()
        projection = render_motion_gif.load_timeline(timeline)
        self.assertEqual(projection["schema_version"], 2)
        self.assertEqual([scene["id"] for scene in projection["scenes"]], ["moving"])
        self.assertEqual(projection["duration"], 1.2)
        self.assertEqual(projection["typewriter"], {"mode": "per-char", "locale": "en", "char_width_factor": 0.6})

        self._require_external_renderer()
        svg = self._write_svg()
        output = self.root / "timeline-v2.gif"
        result = self._run(str(svg), str(output), "--timeline", str(timeline))
        self.assertEqual(result.returncode, 0, result.stderr)
        with Image.open(output) as image:
            self.assertEqual(image.format, "GIF")
            self.assertGreaterEqual(getattr(image, "n_frames", 1), 1)

    def test_v2_spec_contract_rejections_are_bounded(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        four_scenes = [
            {
                "id": f"scene-{index}",
                "interpolation": "linear",
                "enter": {"start": index * 2.0, "end": index * 2.0 + 1.0},
            }
            for index in range(4)
        ]
        cases = (
            ("too-many-scenes", {"scenes": four_scenes}, "scenes must be at most 3"),
            ("duration-over-cap", {"duration": 13.0}, "duration must be at most 12 seconds"),
            (
                "discrete-scene",
                {
                    "scenes": [
                        {
                            "id": "moving",
                            "interpolation": "discrete",
                            "enter": {"start": 0.2, "end": 0.9},
                        }
                    ]
                },
                "must be linear",
            ),
            (
                "factor-mismatch",
                {"typewriter": {"mode": "per-char", "locale": "en", "char_width_factor": 1.0}},
                "char_width_factor must match",
            ),
        )
        for name, updates, message in cases:
            with self.subTest(name=name):
                spec = render_motion_gif.load_spec(self._write_v2_spec(**updates))
                with self.assertRaises(SystemExit) as raised:
                    render_motion_gif.validate_spec(spec)
                self.assertIn(message, str(raised.exception))

    def test_locale_char_width_table_is_exposed_and_matches_the_kernel(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        self.assertEqual(render_motion_gif.typewriter_char_width_factor("per-char", "en"), 0.6)
        self.assertEqual(render_motion_gif.typewriter_char_width_factor("per-line", "zh-Hans"), 1.0)
        self.assertEqual(render_motion_gif.typewriter_char_width_factor("per-word", "ja"), 1.0)
        from skill.scripts.pipeline_contracts import ContractError

        with self.assertRaises(ContractError):
            render_motion_gif.typewriter_char_width_factor("per-char", "zh-Hans")

    def test_v2_degradation_reduces_params_and_writes_back_motion_json(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        spec_path = self._write_v2_spec()
        svg = self._write_svg()
        output = self.root / "degraded.gif"
        motion_json = self.root / "degraded-motion.json"
        frames_work = self.root / "frames-work"
        args = Namespace(
            input_svg=svg,
            output_gif=output,
            spec=spec_path,
            timeline=None,
            keep_frames=None,
            motion_json=motion_json,
        )
        with (
            mock.patch.object(render_motion_gif, "command_path", return_value="ffmpeg"),
            mock.patch.object(render_motion_gif, "choose_renderer", return_value=("rsvg-convert", "renderer")),
            mock.patch.object(
                render_motion_gif,
                "build_frames",
                return_value=(frames_work, 240, 1200, 600, False),
            ) as build,
            mock.patch.object(render_motion_gif, "encode_gif", side_effect=[3 * 1024 * 1024, 1024 * 1024]) as encode,
        ):
            render_motion_gif.run(args)

        self.assertEqual(build.call_count, 2)
        self.assertEqual(encode.call_count, 2)
        written = json.loads(motion_json.read_text(encoding="utf-8"))
        self.assertEqual(written["duration"], 7.0)
        self.assertEqual(written["fps"], 30)
        for scene in written["scenes"]:
            for name in ("enter", "hold", "exit"):
                interval = scene.get(name)
                if interval is not None:
                    self.assertLessEqual(interval["end"], 7.0)

    def test_v2_degradation_floor_falls_back_to_static_frame(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        spec_path = self._write_v2_spec()
        svg = self._write_svg()
        output = self.root / "fallback.gif"
        motion_json = self.root / "fallback-motion.json"
        motion_json.write_text("previous", encoding="utf-8")
        frames_work = self.root / "frames-work"

        def oversized(*_args, **_kwargs) -> int:
            return 3 * 1024 * 1024

        def fake_render(_renderer: tuple[str, str], _svg_path: Path, png_path: Path) -> None:
            Image.new("RGBA", (160, 80), (255, 255, 255, 255)).save(png_path)

        args = Namespace(
            input_svg=svg,
            output_gif=output,
            spec=spec_path,
            timeline=None,
            keep_frames=None,
            motion_json=motion_json,
        )
        with (
            mock.patch.object(render_motion_gif, "command_path", return_value="ffmpeg"),
            mock.patch.object(render_motion_gif, "choose_renderer", return_value=("rsvg-convert", "renderer")),
            mock.patch.object(
                render_motion_gif,
                "build_frames",
                return_value=(frames_work, 240, 1200, 600, False),
            ) as build,
            mock.patch.object(render_motion_gif, "encode_gif", side_effect=oversized) as encode,
            mock.patch.object(render_motion_gif, "render_svg", side_effect=fake_render),
        ):
            render_motion_gif.run(args)

        self.assertGreaterEqual(build.call_count, 2)
        self.assertGreaterEqual(encode.call_count, 2)
        with Image.open(output) as image:
            self.assertEqual(image.format, "GIF")
            self.assertEqual(getattr(image, "n_frames", 1), 1)
        self.assertEqual(motion_json.read_text(encoding="utf-8"), "previous")

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

    def test_hostile_spec_budget_rejects_before_workspace_or_output_replacement(self) -> None:
        cases = (
            ("duration", {"duration": 1_000_000_000}, "duration must be at most"),
            ("width", {"width": 1_000_000}, "width must be at most"),
        )
        for name, updates, message in cases:
            with self.subTest(name=name):
                spec = self._write_spec(**updates)
                output = self.root / f"{name}.gif"
                output.write_bytes(b"previous-output")
                frames_root = self.root / f"{name}-frames"
                frames_root.mkdir()

                result = self._run(
                    str(HERO_SVG),
                    str(output),
                    "--spec",
                    str(spec),
                    "--keep-frames",
                    str(frames_root),
                )

                self.assertNotEqual(result.returncode, 0)
                self.assertIn(message, result.stderr)
                self.assertEqual(output.read_bytes(), b"previous-output")
                self.assertFalse((frames_root / "frames").exists())

    def test_raster_budget_rejects_large_frame_work(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        spec = render_motion_gif.load_spec(HERO_SPEC)
        with self.assertRaises(SystemExit) as raised:
            render_motion_gif.validate_frame_budget(spec, (200, 3_333))
        self.assertIn("per-frame pixel budget exceeded", str(raised.exception))

    def test_hostile_inputs_and_processes_are_bounded_before_rendering(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        oversized_spec = self.root / "oversized.json"
        oversized_spec.write_text(
            json.dumps({"padding": "x" * (256 * 1024)}),
            encoding="utf-8",
        )
        with self.assertRaises(SystemExit) as spec_error:
            render_motion_gif.load_spec(oversized_spec)
        self.assertIn("input exceeds", str(spec_error.exception))

        oversized_svg = self.root / "oversized.svg"
        oversized_svg.write_bytes(
            b'<svg xmlns="http://www.w3.org/2000/svg" width="1" height="1">'
            + b"<!--"
            + b"x" * (2 * 1024 * 1024)
            + b"--></svg>"
        )
        args = Namespace(
            input_svg=oversized_svg,
            output_gif=self.root / "oversized.gif",
            spec=HERO_SPEC,
            timeline=None,
            keep_frames=None,
        )
        with (
            mock.patch.object(render_motion_gif, "command_path", return_value="ffmpeg"),
            mock.patch.object(render_motion_gif, "choose_renderer", return_value=("rsvg-convert", "renderer")),
            mock.patch.object(render_motion_gif, "build_frames", side_effect=AssertionError("render reached")),
            self.assertRaises(SystemExit) as svg_error,
        ):
            render_motion_gif.run(args)
        self.assertIn("input exceeds", str(svg_error.exception))

        root = ET.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="3333" '
            'viewBox="0 0 200 3333"><rect width="1" height="1"/></svg>'
        )
        spec = render_motion_gif.load_spec(HERO_SPEC)
        with (
            mock.patch.object(render_motion_gif, "render_svg") as renderer,
            self.assertRaises(SystemExit) as budget_error,
        ):
            render_motion_gif.build_frames(root, spec, ("fake", "fake"), self.root / "frames-work")
        self.assertIn("per-frame pixel budget exceeded", str(budget_error.exception))
        renderer.assert_not_called()

        with (
            mock.patch.object(
                render_motion_gif.subprocess,
                "run",
                side_effect=subprocess.TimeoutExpired(["rsvg-convert"], 60),
            ) as process,
            self.assertRaises(SystemExit) as timeout_error,
        ):
            render_motion_gif.render_svg(
                ("rsvg-convert", "renderer"),
                self.root / "input.svg",
                self.root / "output.png",
            )
        self.assertIsNotNone(process.call_args.kwargs.get("timeout"))
        self.assertIn("timed out", str(timeout_error.exception))

    def test_motion_json_structure_units_and_work_are_bounded(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        non_object = self.root / "non-object.json"
        non_object.write_text("[]\n", encoding="utf-8")
        with self.assertRaises(SystemExit) as object_error:
            render_motion_gif.load_spec(non_object)
        self.assertIn("JSON object", str(object_error.exception))

        deep = self.root / "deep.json"
        deep.write_bytes(b'{"nested":' + (b"[" * 2_000) + b"0" + (b"]" * 2_000) + b"}\n")
        with self.assertRaises(SystemExit) as deep_error:
            render_motion_gif.load_spec(deep)
        self.assertIn("structural", str(deep_error.exception))

        spec = render_motion_gif.load_spec(HERO_SPEC)
        spec["reveals"] = [
            {"id": f"item-{index}", "start": 0, "end": 1}
            for index in range(65)
        ]
        with self.assertRaises(SystemExit) as element_error:
            render_motion_gif.validate_spec(spec)
        self.assertIn("motion elements", str(element_error.exception))

        spec = render_motion_gif.load_spec(HERO_SPEC)
        spec.update(
            fps=60,
            duration=3,
            reveals=[
                {"id": f"item-{index}", "start": 0, "end": 1}
                for index in range(64)
            ],
            layers=[],
        )
        with self.assertRaises(SystemExit) as work_error:
            render_motion_gif.validate_spec(spec)
        self.assertIn("composite work", str(work_error.exception))

        root = ET.fromstring(
            '<svg xmlns="http://www.w3.org/2000/svg" width="10cm" height="10cm" '
            'viewBox="0 0 1 1"/>'
        )
        with self.assertRaises(SystemExit) as unit_error:
            render_motion_gif._svg_dimensions(root)
        self.assertIn("unitless or px", str(unit_error.exception))

    def test_deep_svg_fails_before_copy_or_render(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        root = ET.Element(
            "{http://www.w3.org/2000/svg}svg",
            {"width": "160", "height": "80", "viewBox": "0 0 160 80"},
        )
        cursor = root
        for _ in range(2_000):
            cursor = ET.SubElement(cursor, "{http://www.w3.org/2000/svg}g")
        spec = render_motion_gif.load_spec(HERO_SPEC)
        spec.update(reveals=[], layers=[])
        workspace = self.root / "deep-svg"
        workspace.mkdir()

        with (
            mock.patch.object(render_motion_gif, "render_svg") as renderer,
            self.assertRaises(SystemExit) as raised,
        ):
            render_motion_gif.build_frames(
                root,
                spec,
                ("fake", "fake"),
                workspace,
            )

        self.assertIn("SVG structure", str(raised.exception))
        renderer.assert_not_called()
        self.assertEqual(list(workspace.iterdir()), [])

    def test_cli_rejects_symlinked_svg_spec_and_timeline_inputs(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        svg = self._write_svg()
        spec = self._write_spec()
        timeline = self._write_timeline()
        cases = (
            ("svg", svg, {"input_svg": None, "spec": spec, "timeline": None}),
            ("spec", spec, {"input_svg": svg, "spec": None, "timeline": None}),
            ("timeline", timeline, {"input_svg": svg, "spec": None, "timeline": None}),
        )
        for name, source, values in cases:
            with self.subTest(name=name):
                linked = self.root / f"linked-{name}{source.suffix}"
                linked.symlink_to(source)
                if name == "svg":
                    values["input_svg"] = linked
                elif name == "spec":
                    values["spec"] = linked
                else:
                    values["timeline"] = linked
                args = Namespace(
                    output_gif=self.root / f"{name}.gif",
                    keep_frames=None,
                    **values,
                )
                with (
                    mock.patch.object(render_motion_gif, "command_path", return_value="ffmpeg"),
                    mock.patch.object(render_motion_gif, "choose_renderer", return_value=("rsvg-convert", "renderer")),
                    mock.patch.object(
                        render_motion_gif,
                        "build_frames",
                        side_effect=AssertionError("render reached"),
                    ) as build,
                    self.assertRaises(SystemExit) as raised,
                ):
                    render_motion_gif.run(args)
                self.assertIn("regular file", str(raised.exception))
                build.assert_not_called()

    def test_failed_encoder_preserves_existing_output(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        frames = self.root / "frames"
        frames.mkdir()
        output = self.root / "existing.gif"
        output.write_bytes(b"last-known-good")
        spec = render_motion_gif.load_spec(HERO_SPEC)

        def process(command: list[str], label: str) -> None:
            destination = Path(command[-1])
            if label == "ffmpeg palette generation":
                palette = Image.new("P", (1, 1))
                palette.putpalette([255, 0, 255] + [0, 0, 0] * 255)
                palette.save(destination)
                return
            destination.write_bytes(b"partial-output")
            raise SystemExit("encoder failed")

        with (
            mock.patch.object(render_motion_gif, "_run_process", side_effect=process),
            self.assertRaises(SystemExit),
        ):
            render_motion_gif.encode_gif(frames, output, spec, "ffmpeg", 1, False)

        self.assertEqual(output.read_bytes(), b"last-known-good")

    def test_oversized_encoder_output_fails_before_transparency_postprocess(self) -> None:
        self.assertIsNotNone(render_motion_gif)
        frames = self.root / "frames-oversized"
        frames.mkdir()
        output = self.root / "existing-oversized.gif"
        output.write_bytes(b"last-known-good")
        spec = render_motion_gif.load_spec(HERO_SPEC)

        def process(command: list[str], label: str) -> None:
            destination = Path(command[-1])
            if label == "ffmpeg palette generation":
                palette = Image.new("P", (1, 1))
                palette.putpalette([255, 0, 255] + [0, 0, 0] * 255)
                palette.save(destination)
                return
            destination.write_bytes(b"oversized")

        with (
            mock.patch.object(render_motion_gif, "MAX_MOTION_OUTPUT_BYTES", 4),
            mock.patch.object(render_motion_gif, "_run_process", side_effect=process),
            mock.patch.object(render_motion_gif, "mark_key_color_transparent") as mark,
            self.assertRaises(SystemExit) as raised,
        ):
            render_motion_gif.encode_gif(frames, output, spec, "ffmpeg", 1, True)

        self.assertIn("E_OUTPUT_SIZE", str(raised.exception))
        mark.assert_not_called()
        self.assertEqual(output.read_bytes(), b"last-known-good")

    def test_timeline_duration_budget_rejects_before_workspace_or_output_replacement(self) -> None:
        timeline = self._write_timeline()
        payload = json.loads(timeline.read_text(encoding="utf-8"))
        payload["duration_ms"] = 30_001
        payload["operations"][0]["end_ms"] = 30_001
        timeline.write_text(json.dumps(payload), encoding="utf-8")
        output = self.root / "timeline-existing.gif"
        output.write_bytes(b"previous-output")
        frames_root = self.root / "timeline-frames"
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
        self.assertIn("E_VISUAL_DETERMINISM", result.stderr)
        self.assertEqual(output.read_bytes(), b"previous-output")
        self.assertFalse((frames_root / "frames").exists())

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


@unittest.skipUnless(render_static_frame is not None, "render_static_frame is required")
class StaticFrameRendererTests(unittest.TestCase):
    """Settled static-frame derivation from an animated SVG source (Task 2.3)."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="readme-static-test-")
        self.root = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _svg(self, body: str) -> Path:
        path = self.root / "animated.svg"
        path.write_text(
            f'''<svg xmlns="http://www.w3.org/2000/svg" width="160" height="80" viewBox="0 0 160 80">
  <rect width="160" height="80" fill="#ffffff"/>
{body}
</svg>
''',
            encoding="utf-8",
        )
        return path

    def _v2_spec(self, *, scene_id: str = "moving") -> dict[str, object]:
        return {
            "schema_version": 2,
            "width": 1200,
            "fps": 30,
            "duration": 8.0,
            "colors": 192,
            "dither": "none",
            "transparent_color": "#ff00ff",
            "alpha_threshold": 128,
            "clip_to_base_alpha": False,
            "max_size_mb": 2.0,
            "scenes": [
                {
                    "id": scene_id,
                    "interpolation": "linear",
                    "enter": {"start": 0.2, "end": 0.9},
                    "hold": {"start": 0.9, "end": 7.2},
                }
            ],
            "typewriter": {"mode": "per-char", "locale": "en", "char_width_factor": 0.6},
            "reduced_motion": {"mode": "static", "visible": [scene_id]},
        }

    def test_settled_frame_freezes_width_bar_at_full_value(self) -> None:
        """The settled frame shows the animation end, never the t=0 width=0 bar."""
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="0" height="32" fill="#111111">\n'
            '    <animate attributeName="width" from="0" to="112" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        data = render_static_frame(svg.read_bytes(), self._v2_spec())
        root = ET.fromstring(data)
        rect = root.find(".//{http://www.w3.org/2000/svg}rect[@id='moving']")
        self.assertIsNotNone(rect)
        assert rect is not None
        self.assertEqual(rect.get("width"), "112")
        smil_tags = [
            element.tag.rsplit("}", 1)[-1]
            for element in root.iter()
            if element.tag.rsplit("}", 1)[-1]
            in {"animate", "set", "animateTransform", "animateMotion"}
        ]
        self.assertEqual(smil_tags, [])

    def test_settled_frame_uses_last_values_entry(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="0" height="32" fill="#111111">\n'
            '    <animate attributeName="width" values="0; 56; 112" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        data = render_static_frame(svg.read_bytes(), self._v2_spec())
        root = ET.fromstring(data)
        rect = root.find(".//{http://www.w3.org/2000/svg}rect[@id='moving']")
        assert rect is not None
        self.assertEqual(rect.get("width"), "112")

    def test_settled_frame_freezes_animate_transform(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="112" height="32" fill="#111111">\n'
            '    <animateTransform attributeName="transform" type="translate" '
            'from="0 0" to="24 24" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        data = render_static_frame(svg.read_bytes(), self._v2_spec())
        root = ET.fromstring(data)
        rect = root.find(".//{http://www.w3.org/2000/svg}rect[@id='moving']")
        assert rect is not None
        self.assertEqual(rect.get("transform"), "translate(24 24)")

    def test_settled_frame_strips_css_keyframes_and_animation(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            "  <style>\n"
            "    @keyframes grow { from { width: 0 } to { width: 112px } }\n"
            "    .frame { fill: #111111 }\n"
            "  </style>\n"
            '  <rect id="moving" class="frame" x="24" y="24" width="112" height="32" '
            'style="animation: grow 1s forwards"/>\n'
        )
        data = render_static_frame(svg.read_bytes(), self._v2_spec())
        text = data.decode("utf-8")
        self.assertNotIn("@keyframes", text)
        self.assertNotIn("animation", text)
        self.assertIn(".frame { fill: #111111 }", text)

    def test_settled_frame_preserves_defs_and_static_geometry(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            "  <defs>\n"
            '    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">\n'
            '      <stop offset="0" stop-color="#eeeeee"/>\n'
            '      <stop offset="1" stop-color="#cccccc"/>\n'
            "    </linearGradient>\n"
            "  </defs>\n"
            '  <rect id="background" x="0" y="0" width="160" height="80" fill="url(#bg)"/>\n'
            '  <rect id="moving" x="24" y="24" width="0" height="32" fill="#111111">\n'
            '    <animate attributeName="width" from="0" to="112" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        data = render_static_frame(svg.read_bytes(), self._v2_spec())
        root = ET.fromstring(data)
        self.assertIsNotNone(
            root.find(".//{http://www.w3.org/2000/svg}linearGradient[@id='bg']")
        )
        background = root.find(".//{http://www.w3.org/2000/svg}rect[@id='background']")
        assert background is not None
        self.assertEqual(background.get("fill"), "url(#bg)")
        self.assertEqual(background.get("width"), "160")

    def test_settled_frame_rejects_unknown_variant(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="112" height="32" fill="#111111"/>'
        )
        with self.assertRaises(ContractError) as raised:
            render_static_frame(svg.read_bytes(), self._v2_spec(), variant="t0")
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

    def test_settled_frame_rejects_scene_id_missing_from_svg(self) -> None:
        """Stale motion specs fail loudly instead of silently producing a frame."""
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="other" x="24" y="24" width="112" height="32" fill="#111111"/>'
        )
        with self.assertRaises(ContractError) as raised:
            render_static_frame(svg.read_bytes(), self._v2_spec(scene_id="missing"))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")
        self.assertIn("SVG element id not found: missing", str(raised.exception))

    def test_settled_frame_rejects_invalid_motion_spec(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="112" height="32" fill="#111111"/>'
        )
        spec = self._v2_spec()
        spec["intruder"] = 1
        with self.assertRaises(ContractError) as raised:
            render_static_frame(svg.read_bytes(), spec)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

        with self.assertRaises(ContractError) as raised:
            render_static_frame(svg.read_bytes(), "not-a-spec")  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

    def test_settled_frame_accepts_v1_spec_scene_ids(self) -> None:
        self.assertIsNotNone(render_static_frame)
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="0" height="32" fill="#111111">\n'
            '    <animate attributeName="width" from="0" to="112" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        spec = {
            "schema_version": 1,
            "width": 1200,
            "fps": 30,
            "duration": 5.0,
            "reveals": [{"id": "moving", "axis": "x", "start": 0, "end": 1}],
            "layers": [],
        }
        data = render_static_frame(svg.read_bytes(), spec)
        root = ET.fromstring(data)
        rect = root.find(".//{http://www.w3.org/2000/svg}rect[@id='moving']")
        assert rect is not None
        self.assertEqual(rect.get("width"), "112")

    def test_settled_frame_cli_writes_static_svg(self) -> None:
        svg = self._svg(
            '  <rect id="moving" x="24" y="24" width="0" height="32" fill="#111111">\n'
            '    <animate attributeName="width" from="0" to="112" dur="1s" fill="freeze"/>\n'
            "  </rect>"
        )
        spec = self.root / "motion-v2.json"
        spec.write_text(json.dumps(self._v2_spec()), encoding="utf-8")
        output = self.root / "hero-static.svg"
        result = subprocess.run(
            [
                sys.executable,
                str(REPO_ROOT / "skill/scripts/render_static_frame.py"),
                str(svg),
                str(output),
                "--spec",
                str(spec),
            ],
            cwd=REPO_ROOT,
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("STATIC-FRAME", result.stdout)
        rect = ET.fromstring(output.read_bytes()).find(
            ".//{http://www.w3.org/2000/svg}rect[@id='moving']"
        )
        assert rect is not None
        self.assertEqual(rect.get("width"), "112")


if __name__ == "__main__":
    unittest.main()
