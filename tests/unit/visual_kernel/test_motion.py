from __future__ import annotations

import copy
import json
import subprocess
import unittest
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.motion import (
    project_motion_spec,
    project_motion_spec_v2,
    validate_motion_spec_v2,
)
from skill.scripts.readme_showcase.visual_kernel.timeline import Timeline, TimelineV2, derive_timeline
from tests.unit.visual_kernel.test_timeline import EVIDENCE, spec, v2_scene, v2_timeline
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


class MotionV2ProjectionTests(unittest.TestCase):
    V2_KEYS = {
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
        "scenes",
        "typewriter",
        "reduced_motion",
    }

    def test_v2_spec_shape_from_timeline_v2(self) -> None:
        timeline = v2_timeline(typewriter={"mode": "per-line", "locale": "zh-Hans"})
        projection = project_motion_spec_v2(timeline)

        self.assertEqual(project_motion_spec_v2.__module__, "skill.scripts.readme_showcase.visual_kernel.motion")
        self.assertEqual(projection["schema_version"], 2)
        self.assertEqual(set(projection), self.V2_KEYS)
        self.assertEqual(projection["duration"], 8.0)
        self.assertEqual(projection["max_size_mb"], 2.0)
        self.assertLessEqual(projection["duration"], 12.0)
        self.assertEqual(
            projection["scenes"],
            [
                {
                    "id": "card",
                    "interpolation": "linear",
                    "enter": {"start": 0.0, "end": 0.8},
                    "hold": {"start": 0.8, "end": 3.6},
                },
                {
                    "id": "title",
                    "interpolation": "linear",
                    "enter": {"start": 3.6, "end": 4.4},
                },
            ],
        )
        self.assertEqual(
            projection["typewriter"],
            {"mode": "per-line", "locale": "zh-Hans", "char_width_factor": 1.0},
        )
        self.assertEqual(projection["reduced_motion"], {"mode": "static", "visible": ["card", "title"]})
        self.assertEqual(json.loads(json.dumps(projection)), projection)

    def test_v2_projection_accepts_timeline_v1_within_the_v2_contract(self) -> None:
        timeline = Timeline(
            ("edge", "node"),
            8_000,
            (
                {"id": "reveal:node", "kind": "reveal", "target": "node", "start_ms": 0, "end_ms": 3_200},
                {"id": "emphasis:edge", "kind": "emphasis", "target": "edge", "start_ms": 3_240, "end_ms": 8_000},
            ),
            ("edge", "node"),
        )
        projection = project_motion_spec_v2(timeline)
        self.assertEqual(projection["schema_version"], 2)
        self.assertEqual(
            [(scene["id"], scene["enter"]) for scene in projection["scenes"]],
            [
                ("node", {"start": 0.0, "end": 3.2}),
                ("edge", {"start": 3.24, "end": 8.0}),
            ],
        )
        self.assertEqual(
            projection["typewriter"],
            {"mode": "per-char", "locale": "en", "char_width_factor": 0.6},
        )
        self.assertEqual(projection["reduced_motion"], {"mode": "static", "visible": ["edge", "node"]})

    def test_v2_projection_rejects_timeline_v1_outside_the_v2_contract(self) -> None:
        many = Timeline(
            ("a", "b", "c", "d"),
            8_000,
            (
                {"id": "reveal:a", "kind": "reveal", "target": "a", "start_ms": 0, "end_ms": 1_900},
                {"id": "reveal:b", "kind": "reveal", "target": "b", "start_ms": 1_940, "end_ms": 3_900},
                {"id": "reveal:c", "kind": "reveal", "target": "c", "start_ms": 3_940, "end_ms": 5_900},
                {"id": "reveal:d", "kind": "reveal", "target": "d", "start_ms": 5_940, "end_ms": 8_000},
            ),
            ("a", "b", "c", "d"),
        )
        with self.assertRaises(ContractError) as raised:
            project_motion_spec_v2(many)
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        long = Timeline(
            ("a",),
            13_000,
            ({"id": "reveal:a", "kind": "reveal", "target": "a", "start_ms": 0, "end_ms": 13_000},),
            ("a",),
        )
        with self.assertRaises(ContractError) as raised:
            project_motion_spec_v2(long)
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

    def test_v2_projection_rejects_non_timeline_and_v1_projection_is_unchanged(self) -> None:
        with self.assertRaises(ContractError) as raised:
            project_motion_spec_v2({"targets": ["a"]})  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

        timeline = v2_timeline()
        with self.assertRaises(ContractError) as raised:
            project_motion_spec(timeline)  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

    def test_v2_projection_is_byte_stable_and_validates_round_trip(self) -> None:
        timeline = v2_timeline(typewriter={"mode": "per-line", "locale": "zh-Hans"})
        first = project_motion_spec_v2(timeline)
        second = project_motion_spec_v2(timeline)
        self.assertEqual(
            json.dumps(first, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            json.dumps(second, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        validate_motion_spec_v2(copy.deepcopy(first))

    def test_validate_motion_spec_v2_rejects_contract_violations(self) -> None:
        projection = project_motion_spec_v2(v2_timeline())
        cases = (
            ("discrete-scene", "E_SCHEMA_VALUE", self._mutate(projection, lambda value: value["scenes"][0].__setitem__("interpolation", "discrete"))),
            ("too-many-scenes", "E_VISUAL_DETERMINISM", self._mutate(projection, lambda value: value["scenes"].append({"id": "extra", "interpolation": "linear", "enter": {"start": 6.0, "end": 7.0}}))),
            ("duration-over-cap", "E_VISUAL_DETERMINISM", self._mutate(projection, lambda value: value.__setitem__("duration", 13.0))),
            ("factor-mismatch", "E_SCHEMA_VALUE", self._mutate(projection, lambda value: value["typewriter"].__setitem__("char_width_factor", 1.0))),
            ("mode-locale-mismatch", "E_SCHEMA_VALUE", self._mutate(projection, lambda value: value["typewriter"].update(mode="per-char", locale="zh-Hans", char_width_factor=0.6))),
            ("unknown-field", "E_SCHEMA_UNKNOWN_FIELD", self._mutate(projection, lambda value: value.__setitem__("mystery", True))),
            ("schema-version", "E_SCHEMA_VERSION", self._mutate(projection, lambda value: value.__setitem__("schema_version", 1))),
        )
        for name, code, payload in cases:
            with self.subTest(name=name):
                with self.assertRaises(ContractError) as raised:
                    validate_motion_spec_v2(payload)
                self.assertEqual(raised.exception.code, code)

    @staticmethod
    def _mutate(projection: dict, mutate) -> dict:
        candidate = copy.deepcopy(projection)
        mutate(candidate)
        return candidate


if __name__ == "__main__":
    unittest.main()
