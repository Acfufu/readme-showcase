from __future__ import annotations

import copy
import json
import subprocess
import unittest
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.motion import project_motion_spec
from skill.scripts.readme_showcase.visual_kernel.timeline import Timeline, derive_timeline
from tests.unit.visual_kernel.test_timeline import EVIDENCE, spec
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec


class MotionProjectionTests(unittest.TestCase):
    def test_public_surface_and_legacy_shape_preserve_targets_and_static_state(self) -> None:
        timeline = derive_timeline(normalize_visual_spec(spec(), EVIDENCE))
        projection = project_motion_spec(timeline)

        self.assertEqual(project_motion_spec.__module__, "skill.scripts.readme_showcase.visual_kernel.motion")
        self.assertEqual(project_motion_spec.__name__, "project_motion_spec")
        self.assertEqual(
            set(projection),
            {
                "schema_version",
                "width",
                "fps",
                "duration",
                "colors",
                "dither",
                "transparent_color",
                "alpha_threshold",
                "clip_to_base_alpha",
                "max_size_mb",
                "reveals",
                "layers",
                "reduced_motion",
            },
        )
        self.assertEqual(
            [item["id"] for item in projection["reveals"] + projection["layers"]],
            [operation.target for operation in timeline.operations],
        )
        self.assertEqual(projection["reduced_motion"], {"mode": "static", "visible": list(timeline.reduced_motion)})
        self.assertEqual(json.loads(json.dumps(projection)), projection)
        self.assertGreater(projection["duration"], 0)
        self.assertLessEqual(projection["duration"], 30.0)

    def test_reveal_and_emphasis_map_to_renderer_descriptors_without_subprocess(self) -> None:
        timeline = Timeline(
            ("edge", "node"),
            1_000,
            (
                {"id": "reveal:node", "kind": "reveal", "target": "node", "start_ms": 0, "end_ms": 400},
                {"id": "emphasis:edge", "kind": "emphasis", "target": "edge", "start_ms": 400, "end_ms": 1_000},
            ),
            ("edge", "node"),
        )
        with mock.patch.object(subprocess, "run") as run:
            projection = project_motion_spec(timeline)
        run.assert_not_called()
        self.assertEqual(projection["reveals"], [{"id": "node", "axis": "x", "start": 0.0, "end": 0.4}])
        self.assertEqual(
            projection["layers"],
            [
                {
                    "id": "edge",
                    "enter": {"start": 0.4, "end": 1.0, "from": [0, 0]},
                    "exit": {"start": 0.4, "end": 1.0, "to": [0, 0]},
                }
            ],
        )

    def test_output_is_byte_stable_and_legacy_validator_is_used_when_available(self) -> None:
        timeline = derive_timeline(normalize_visual_spec(spec(), EVIDENCE))
        first = project_motion_spec(timeline)
        second = project_motion_spec(timeline)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        try:
            from skill.scripts import render_motion_gif
        except (ModuleNotFoundError, SystemExit):
            self.skipTest("Pillow is unavailable for the legacy renderer validator")
        render_motion_gif.validate_spec(copy.deepcopy(first))

    def test_invalid_timeline_fails_before_renderer(self) -> None:
        cases = (
            ("non-timeline", lambda: {"targets": ["a"]}, "E_SCHEMA_TYPE"),
            (
                "unknown-target",
                lambda: Timeline(
                    ("a",),
                    10,
                    ({"id": "reveal:a", "kind": "reveal", "target": "missing", "start_ms": 0, "end_ms": 10},),
                    ("a",),
                ),
                "E_VISUAL_SPEC_EDGE",
            ),
            ("empty-duration", lambda: Timeline((), 0, (), ()), "E_VISUAL_DETERMINISM"),
        )
        for name, value, code in cases:
            with self.subTest(name=name), mock.patch.object(subprocess, "run") as run:
                with self.assertRaises(ContractError) as raised:
                    candidate = value()
                    project_motion_spec(candidate)  # type: ignore[arg-type]
                self.assertEqual(raised.exception.code, code)
                run.assert_not_called()

        with self.assertRaises(ContractError) as raised:
            Timeline(
                ("a",),
                10,
                ({"id": "reveal:a", "kind": "reveal", "target": "a", "start_ms": 0.5, "end_ms": 10},),
                ("a",),
            )
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

        with self.assertRaises(ContractError) as raised:
            Timeline(
                ("a",),
                10,
                (
                    {"id": "duplicate", "kind": "reveal", "target": "a", "start_ms": 0, "end_ms": 5},
                    {"id": "duplicate", "kind": "emphasis", "target": "a", "start_ms": 5, "end_ms": 10},
                ),
                ("a",),
            )
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")


if __name__ == "__main__":
    unittest.main()
