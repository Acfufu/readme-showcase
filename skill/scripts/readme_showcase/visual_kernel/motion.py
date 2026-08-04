"""Pure opt-in projection from Timeline v1 to the legacy motion-spec shape."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from ...pipeline_contracts import ContractError
from .timeline import Timeline


_MAX_DURATION_MS = 30_000


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


__all__ = ["project_motion_spec"]
