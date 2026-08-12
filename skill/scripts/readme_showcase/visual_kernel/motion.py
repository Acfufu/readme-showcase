"""Opt-in projections from Timeline v1/v2 plus motion-spec v2 semantic validation."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ...pipeline_contracts import ContractError
from .timeline import (
    Timeline,
    TimelineV2,
    _SMOOTH_INTERPOLATIONS,
    _checked_id,
    typewriter_char_width_factor,
)


_MAX_DURATION_MS = 30_000
_MAX_DURATION_MS_V2 = 12_000
_MAX_SCENES = 3
_TYPEWRITER_MODES = frozenset({"per-char", "per-word", "per-line"})
_LOCALES = frozenset({"en", "zh-Hans", "zh-Hant", "ja", "ko", "fr", "de"})
_DITHERS = frozenset({"none", "bayer", "heckbert", "floyd_steinberg", "sierra2", "sierra2_4a"})
_DEFAULT_TYPEWRITER = ("per-char", "en")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _seconds(value: Any, context: str) -> float:
    if type(value) is not int:
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an integer millisecond offset")
    if value < 0:
        raise _fail("E_VISUAL_DETERMINISM", f"{context} must be non-negative")
    return value / 1000.0


def _interval(start_ms: int, end_ms: int, context: str) -> dict[str, float]:
    start = _seconds(start_ms, f"{context}.start_ms")
    end = _seconds(end_ms, f"{context}.end_ms")
    if end <= start:
        raise _fail("E_VISUAL_DETERMINISM", f"{context} must have a positive interval")
    return {"start": start, "end": end}


def project_motion_spec(timeline: Timeline) -> Mapping[str, Any]:
    """Project a validated Timeline into the existing renderer's JSON shape."""

    if not isinstance(timeline, Timeline):
        raise _fail("E_SCHEMA_TYPE", "motion projection requires a Timeline v1 value")
    normalized = Timeline(
        timeline.targets,
        timeline.duration_ms,
        timeline.operations,
        timeline.reduced_motion,
    )
    if normalized.duration_ms <= 0 or normalized.duration_ms > _MAX_DURATION_MS:
        raise _fail("E_VISUAL_DETERMINISM", "Timeline duration is outside the motion-spec range")

    reveals: list[dict[str, Any]] = []
    layers: list[dict[str, Any]] = []
    for operation in normalized.operations:
        interval = _interval(operation.start_ms, operation.end_ms, f"timeline operation {operation.id}")
        if operation.kind == "reveal":
            reveals.append({"id": operation.target, "axis": "x", **interval})
        elif operation.kind == "emphasis":
            layers.append(
                {
                    "id": operation.target,
                    "enter": {**interval, "from": [0, 0]},
                    "exit": {**interval, "to": [0, 0]},
                }
            )
        else:  # Timeline v1 currently rejects this; keep the adapter closed if it grows.
            raise _fail("E_SCHEMA_VALUE", f"timeline operation {operation.id} kind is unsupported")

    projection: dict[str, Any] = {
        "schema_version": 1,
        "width": 1200,
        "fps": 30,
        "duration": normalized.duration_ms / 1000.0,
        "colors": 192,
        "dither": "none",
        "transparent_color": "#ff00ff",
        "alpha_threshold": 128,
        "clip_to_base_alpha": False,
        "max_size_mb": 2.0,
        "reveals": reveals,
        "layers": layers,
        "reduced_motion": {"mode": "static", "visible": list(normalized.reduced_motion)},
    }
    return projection


def _scene_projection(scene: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": scene.id,
        "interpolation": scene.interpolation,
        "enter": _interval(scene.enter.start_ms, scene.enter.end_ms, f"scene {scene.id}"),
    }
    if scene.hold is not None:
        result["hold"] = _interval(scene.hold.start_ms, scene.hold.end_ms, f"scene {scene.id} hold")
    if scene.exit is not None:
        result["exit"] = _interval(scene.exit.start_ms, scene.exit.end_ms, f"scene {scene.id} exit")
    return result


def project_motion_spec_v2(timeline: Timeline | TimelineV2) -> dict[str, Any]:
    """Project a validated Timeline v1 or v2 into the motion-spec v2 JSON shape.

    Timeline v1 values stay supported: timelines inside the v2 contract (at
    most three operations and a twelve-second duration) project one scene per
    operation with the default latin per-char typewriter.
    """
    if isinstance(timeline, Timeline):
        normalized: Timeline | TimelineV2 = Timeline(
            timeline.targets,
            timeline.duration_ms,
            timeline.operations,
            timeline.reduced_motion,
        )
        if normalized.duration_ms <= 0 or normalized.duration_ms > _MAX_DURATION_MS_V2:
            raise _fail("E_VISUAL_DETERMINISM", "Timeline duration is outside the motion-spec v2 range")
        if len(normalized.operations) > _MAX_SCENES:
            raise _fail("E_VISUAL_DETERMINISM", f"motion-spec v2 supports at most {_MAX_SCENES} scenes")
        scenes = [
            {
                "id": operation.target,
                "interpolation": "linear",
                "enter": _interval(
                    operation.start_ms,
                    operation.end_ms,
                    f"timeline operation {operation.id}",
                ),
            }
            for operation in normalized.operations
        ]
        typewriter = dict(zip(("mode", "locale"), _DEFAULT_TYPEWRITER, strict=True))
        reduced = normalized.reduced_motion
    elif isinstance(timeline, TimelineV2):
        normalized = TimelineV2(
            timeline.targets,
            timeline.duration_ms,
            timeline.scenes,
            timeline.typewriter,
            timeline.reduced_motion,
        )
        scenes = [_scene_projection(item) for item in normalized.scenes]
        typewriter = {"mode": normalized.typewriter.mode, "locale": normalized.typewriter.locale}
        reduced = normalized.reduced_motion
    else:
        raise _fail("E_SCHEMA_TYPE", "motion projection requires a Timeline v1 or v2 value")

    return {
        "schema_version": 2,
        "width": 1200,
        "fps": 30,
        "duration": normalized.duration_ms / 1000.0,
        "colors": 192,
        "dither": "none",
        "transparent_color": "#ff00ff",
        "alpha_threshold": 128,
        "clip_to_base_alpha": False,
        "max_size_mb": 2.0,
        "scenes": scenes,
        "typewriter": {
            **typewriter,
            "char_width_factor": typewriter_char_width_factor(typewriter["mode"], typewriter["locale"]),
        },
        "reduced_motion": {"mode": "static", "visible": list(reduced)},
    }


def _spec_number(value: Any, path: str) -> float:
    if type(value) is not int and type(value) is not float:
        raise _fail("E_SCHEMA_TYPE", f"{path} must be a number")
    return float(value)


def _spec_interval(value: Any, path: str, duration: float) -> dict[str, float]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an object")
    allowed = {"start", "end"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{path} contains unknown field: {unknown[0]}")
    missing = sorted(allowed - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{path} is missing field: {missing[0]}")
    start = _spec_number(value["start"], f"{path}.start")
    end = _spec_number(value["end"], f"{path}.end")
    if start < 0:
        raise _fail("E_VISUAL_DETERMINISM", f"{path}.start must be non-negative")
    if end <= start:
        raise _fail("E_VISUAL_DETERMINISM", f"{path} must have a positive interval")
    if end > duration:
        raise _fail("E_VISUAL_DETERMINISM", f"{path} exceeds the motion duration")
    return {"start": start, "end": end}


def _scene_spec(value: Any, index: int, duration: float) -> dict[str, Any]:
    path = f"scenes[{index}]"
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an object")
    allowed = {"id", "interpolation", "enter", "hold", "exit"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{path} contains unknown field: {unknown[0]}")
    missing = sorted({"id", "interpolation", "enter"} - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{path} is missing field: {missing[0]}")
    identifier = _checked_id(value["id"], f"{path}.id")
    interpolation = value["interpolation"]
    if type(interpolation) is not str:
        raise _fail("E_SCHEMA_TYPE", f"{path}.interpolation must be a string")
    if interpolation not in _SMOOTH_INTERPOLATIONS:
        raise _fail("E_SCHEMA_VALUE", f"{path}.interpolation must be linear for smooth motion")
    enter = _spec_interval(value["enter"], f"{path}.enter", duration)
    hold = _spec_interval(value["hold"], f"{path}.hold", duration) if "hold" in value else None
    exit = _spec_interval(value["exit"], f"{path}.exit", duration) if "exit" in value else None
    if hold is not None and hold["start"] != enter["end"]:
        raise _fail("E_VISUAL_DETERMINISM", f"{path}.hold must start where enter ends")
    if exit is not None:
        previous = hold or enter
        if exit["start"] != previous["end"]:
            raise _fail("E_VISUAL_DETERMINISM", f"{path}.exit must start where hold ends")
    last = exit or hold or enter
    return {"id": identifier, "interpolation": interpolation, "enter": enter, "end": last["end"]}


def validate_motion_spec_v2(value: Any) -> None:
    """Validate a motion-spec v2 payload: JSON shape plus the semantic contract.

    Smooth scene motion must stay linear; discrete stepping is reserved for the
    typewriter reveal, whose locale decides the allowed mode and char width.
    Scenes are sequential, non-overlapping, at most three, and the static
    reduced-motion state exposes every scene id.
    """
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "motion spec must be an object")
    allowed = {
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
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"motion spec contains unknown field: {unknown[0]}")
    required = {"schema_version", "scenes", "typewriter", "reduced_motion"}
    missing = sorted(required - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"motion spec is missing field: {missing[0]}")
    if value["schema_version"] != 2:
        raise _fail("E_SCHEMA_VERSION", "motion spec requires schema_version 2")

    duration = _spec_number(value.get("duration", 8.0), "duration")
    if duration <= 0:
        raise _fail("E_VISUAL_DETERMINISM", "duration must be positive")
    if duration > _MAX_DURATION_MS_V2 / 1000:
        raise _fail("E_VISUAL_DETERMINISM", f"duration must be at most {_MAX_DURATION_MS_V2 / 1000:g} seconds")

    fps = value.get("fps", 30)
    if type(fps) is not int or not 1 <= fps <= 60:
        raise _fail("E_SCHEMA_VALUE", "fps must be an integer between 1 and 60")
    width = value.get("width", 1200)
    if type(width) is not int or width <= 0:
        raise _fail("E_SCHEMA_VALUE", "width must be a positive integer")
    colors = value.get("colors", 192)
    if type(colors) is not int or not 2 <= colors <= 256:
        raise _fail("E_SCHEMA_VALUE", "colors must be between 2 and 256")
    alpha_threshold = value.get("alpha_threshold", 128)
    if type(alpha_threshold) is not int or not 0 <= alpha_threshold <= 255:
        raise _fail("E_SCHEMA_VALUE", "alpha_threshold must be between 0 and 255")
    transparent_color = value.get("transparent_color", "#ff00ff")
    if (
        type(transparent_color) is not str
        or len(transparent_color) != 7
        or not transparent_color.startswith("#")
        or any(char not in "0123456789abcdefABCDEF" for char in transparent_color[1:])
    ):
        raise _fail("E_SCHEMA_VALUE", "transparent_color must use #RRGGBB format")
    dither = value.get("dither", "none")
    if type(dither) is not str or dither not in _DITHERS:
        raise _fail("E_SCHEMA_VALUE", f"unsupported dither mode: {dither}")
    if type(value.get("clip_to_base_alpha", False)) is not bool:
        raise _fail("E_SCHEMA_VALUE", "clip_to_base_alpha must be true or false")
    if value.get("max_size_mb", 2.0) != 2.0:
        raise _fail("E_SCHEMA_VALUE", "max_size_mb must be 2.0")

    raw_scenes = value["scenes"]
    if not isinstance(raw_scenes, Sequence) or isinstance(raw_scenes, (str, bytes)):
        raise _fail("E_SCHEMA_TYPE", "scenes must be an array")
    if not raw_scenes:
        raise _fail("E_VISUAL_DETERMINISM", "scenes must not be empty")
    if len(raw_scenes) > _MAX_SCENES:
        raise _fail("E_VISUAL_DETERMINISM", f"scenes must be at most {_MAX_SCENES}")
    scenes = [_scene_spec(item, index, duration) for index, item in enumerate(raw_scenes)]
    scene_ids = [item["id"] for item in scenes]
    if len(scene_ids) != len(set(scene_ids)):
        raise _fail("E_VISUAL_SPEC_ID", "scene ids must be unique")
    ordered = sorted(scenes, key=lambda item: (item["enter"]["start"], item["id"]))
    previous_end = 0.0
    for item in ordered:
        if item["enter"]["start"] < previous_end:
            raise _fail("E_VISUAL_DETERMINISM", f"scenes overlap at {item['id']}")
        previous_end = item["end"]

    typewriter = value["typewriter"]
    if not isinstance(typewriter, Mapping):
        raise _fail("E_SCHEMA_TYPE", "typewriter must be an object")
    mode = typewriter.get("mode")
    locale = typewriter.get("locale")
    if type(mode) is not str or mode not in _TYPEWRITER_MODES:
        raise _fail("E_SCHEMA_VALUE", "typewriter mode is unsupported")
    if type(locale) is not str or locale not in _LOCALES:
        raise _fail("E_SCHEMA_VALUE", "typewriter locale is unsupported")
    expected_factor = typewriter_char_width_factor(mode, locale)
    factor = _spec_number(typewriter.get("char_width_factor"), "typewriter.char_width_factor")
    if factor != expected_factor:
        raise _fail("E_SCHEMA_VALUE", "typewriter char_width_factor must match the locale table")

    reduced = value["reduced_motion"]
    if not isinstance(reduced, Mapping):
        raise _fail("E_SCHEMA_TYPE", "reduced_motion must be an object")
    if reduced.get("mode") != "static":
        raise _fail("E_SCHEMA_VALUE", "reduced_motion.mode must be static")
    visible = reduced.get("visible")
    if not isinstance(visible, Sequence) or isinstance(visible, (str, bytes)):
        raise _fail("E_SCHEMA_TYPE", "reduced_motion.visible must be an array")
    checked_visible = tuple(_checked_id(item, "reduced_motion.visible[]") for item in visible)
    if checked_visible != tuple(sorted(scene_ids)):
        raise _fail("E_VISUAL_DETERMINISM", "reduced-motion static state must expose every scene id")


__all__ = ["project_motion_spec", "project_motion_spec_v2", "validate_motion_spec_v2"]
