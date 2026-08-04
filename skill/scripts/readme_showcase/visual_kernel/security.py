"""Fail-closed trust boundaries for compiled visual-kernel artifacts.

This module deliberately delegates semantic validation to the current Spec,
Scene, Theme, Timeline, Interaction, and SVG implementations.  It only binds
their canonical bytes to resource limits and a single immutable result.
"""

from __future__ import annotations

import json
import os
import re
import stat
import unicodedata
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Callable

from ...audit_readme import MAX_SVG_BYTES as _AUDIT_SVG_BYTES, audit_svg_bytes
from ...pipeline_contracts import ContractError, canonical_json_bytes, read_regular_bytes
from .diagnostics import VisualDiagnostic, VisualGateReport
from .interaction import InteractionGraph
from .model import validate_visual_spec
from .scene import validate_visual_scene
from .theme import Theme
from .timeline import Timeline


MAX_VISUAL_SPEC_BYTES = 256 * 1024
MAX_SCENE_BYTES = 2 * 1024 * 1024
MAX_SVG_BYTES = _AUDIT_SVG_BYTES
MAX_GATE_BYTES = 512 * 1024
MAX_TIMELINE_BYTES = 512 * 1024
MAX_INTERACTION_BYTES = 512 * 1024
MAX_COMPILED_BYTES = 16 * 1024 * 1024

_SCHEMA_FIELDS = {
    "theme": frozenset({"schema_version", "colors", "spacing", "strokes", "text", "variants"}),
    "timeline": frozenset({"schema_version", "targets", "duration_ms", "operations", "reduced_motion"}),
    "interaction": frozenset(
        {
            "schema_version",
            "focus_order",
            "evidence_links",
            "adjacency",
            "group_navigation",
            "lane_navigation",
        }
    ),
    "gate": frozenset(
        {"schema_version", "status", "spec_sha256", "scene_sha256", "svg_sha256", "diagnostics"}
    ),
}
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _bounded_maximum(value: Any) -> int:
    """Keep a caller-provided lower cap below the reader's hard ceiling."""

    if value is None:
        return MAX_SCENE_BYTES
    if type(value) is not int or not 1 <= value <= MAX_SCENE_BYTES:
        raise _fail("E_VISUAL_RESOURCE", "visual byte limit must be a positive integer")
    return value


def _json_from_bytes(raw: bytes, context: str) -> Any:
    if type(raw) is not bytes:
        raise _fail("E_SCHEMA_TYPE", f"{context} must be canonical UTF-8 JSON bytes")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _fail("E_VISUAL_RESOURCE", f"{context} must be canonical UTF-8 JSON") from None


def _closed_top(value: Mapping[str, Any], name: str) -> dict[str, Any]:
    fields = _SCHEMA_FIELDS[name]
    raw = dict(value)
    if any(not isinstance(key, str) for key in raw):
        raise _fail("E_SCHEMA_KEY_TYPE", f"{name} contains a non-string field name")
    unknown = sorted(set(raw) - fields)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{name} contains unknown field")
    missing = sorted(fields - set(raw))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{name} is missing a required field")
    if raw["schema_version"] != 1:
        raise _fail("E_SCHEMA_VERSION", f"{name} requires schema_version 1")
    return raw


def _normalize_theme(value: Any) -> Theme:
    if isinstance(value, Theme):
        return Theme(value.schema_version, value.colors, value.spacing, value.strokes, value.text, value.variants)
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "theme must be a Theme or JSON object")
    raw = _closed_top(value, "theme")
    return Theme(raw["schema_version"], raw["colors"], raw["spacing"], raw["strokes"], raw["text"], raw["variants"])


def _normalize_timeline(value: Any) -> Timeline:
    if isinstance(value, Timeline):
        return Timeline(value.targets, value.duration_ms, value.operations, value.reduced_motion)
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "timeline must be a Timeline or JSON object")
    raw = _closed_top(value, "timeline")
    return Timeline(raw["targets"], raw["duration_ms"], raw["operations"], raw["reduced_motion"])


def _tuple_map(value: Any, context: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for key, targets in value.items():
        if not isinstance(key, str) or not isinstance(targets, (tuple, list)):
            raise _fail("E_SCHEMA_TYPE", f"{context} must map IDs to arrays")
        result[key] = tuple(targets)
    return result


def _normalize_interaction(value: Any) -> InteractionGraph:
    if isinstance(value, InteractionGraph):
        return InteractionGraph(
            value.focus_order,
            value.evidence_links,
            value.adjacency,
            value.group_navigation,
            value.lane_navigation,
        )
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "interaction must be an InteractionGraph or JSON object")
    raw = _closed_top(value, "interaction")
    if not isinstance(raw["focus_order"], (tuple, list)):
        raise _fail("E_SCHEMA_TYPE", "interaction.focus_order must be an array")
    return InteractionGraph(
        tuple(raw["focus_order"]),
        _tuple_map(raw["evidence_links"], "interaction.evidence_links"),
        _tuple_map(raw["adjacency"], "interaction.adjacency"),
        _tuple_map(raw["group_navigation"], "interaction.group_navigation"),
        _tuple_map(raw["lane_navigation"], "interaction.lane_navigation"),
    )


def _normalize_gate(value: Any) -> VisualGateReport:
    if isinstance(value, VisualGateReport):
        return VisualGateReport(
            value.status,
            value.spec_sha256,
            value.scene_sha256,
            value.svg_sha256,
            value.diagnostics,
        )
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "gate must be a VisualGateReport or JSON object")
    raw = _closed_top(value, "gate")
    diagnostics: list[VisualDiagnostic] = []
    for item in raw["diagnostics"]:
        if isinstance(item, VisualDiagnostic):
            diagnostics.append(item)
            continue
        if not isinstance(item, Mapping):
            raise _fail("E_SCHEMA_TYPE", "gate diagnostics must contain objects")
        if not {"code", "severity"}.issubset(item):
            raise _fail("E_SCHEMA_MISSING_FIELD", "gate diagnostic is missing a required field")
        if set(item) - {"code", "severity", "path", "element_ids", "message"}:
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", "gate diagnostic contains an unknown field")
        diagnostics.append(
            VisualDiagnostic(
                item["code"],
                item["severity"],
                item.get("path"),
                tuple(item.get("element_ids", ())),
                item.get("message", ""),
            )
        )
    return VisualGateReport(
        raw["status"],
        raw["spec_sha256"],
        raw["scene_sha256"],
        raw["svg_sha256"],
        tuple(diagnostics),
    )


def _canonical_artifact(
    value: Any,
    *,
    name: str,
    maximum: int,
    normalizer: Callable[[Any], Any],
    size_code: str = "E_VISUAL_RESOURCE",
) -> bytes:
    """Revalidate a value and require the byte representation to be canonical."""

    if isinstance(value, bytes):
        if len(value) > maximum:
            raise _fail(size_code, f"{name} exceeds its byte limit")
        payload = _json_from_bytes(value, name)
        try:
            if canonical_json_bytes(payload) != value:
                raise _fail("E_VISUAL_DETERMINISM", f"{name} is not canonical")
            normalized = normalizer(payload)
            canonical = normalized.canonical_bytes()
        except ContractError:
            raise
        except (AttributeError, TypeError, ValueError, RecursionError):
            raise _fail("E_SCHEMA_TYPE", f"{name} cannot be canonicalized") from None
        if canonical != value:
            raise _fail("E_VISUAL_DETERMINISM", f"{name} is not the current canonical projection")
    else:
        try:
            normalized = normalizer(value)
            canonical = normalized.canonical_bytes()
        except ContractError:
            raise
        except (AttributeError, TypeError, ValueError, RecursionError):
            raise _fail("E_SCHEMA_TYPE", f"{name} cannot be canonicalized") from None
    if len(canonical) > maximum:
        raise _fail(size_code, f"{name} exceeds its byte limit")
    return canonical


def validate_visual_svg_bytes(value: Any) -> bytes:
    """Validate static SVG bytes through the repository's authoritative audit."""

    if type(value) is not bytes:
        raise _fail("E_SCHEMA_TYPE", "SVG must be bytes")
    if len(value) > MAX_SVG_BYTES:
        raise _fail("E_VISUAL_RESOURCE", "SVG exceeds its byte limit")
    issues = audit_svg_bytes(value)
    if issues:
        if any(code == "E_SVG_LIMIT" for code, _ in issues):
            raise _fail("E_VISUAL_RESOURCE", "SVG exceeds a structural or dimension limit")
        raise _fail("E_VISUAL_SVG_SECURITY", "SVG violates the static SVG security policy")
    return value


def validate_visual_security(
    spec: Any | None = None,
    scene: Any | None = None,
    theme: Any | None = None,
    timeline: Any | None = None,
    interaction: Any | None = None,
    svg: Any | None = None,
    gate: Any | None = None,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
) -> Mapping[str, bytes]:
    """Validate any produced visual artifacts and return canonical bytes.

    All requested artifacts are validated into a local mapping first.  A
    failing trust boundary therefore cannot expose a partial promotion set.
    """

    if all(value is None for value in (spec, scene, theme, timeline, interaction, svg, gate)):
        raise _fail("E_SCHEMA_TYPE", "visual security gate requires at least one artifact")
    artifacts: dict[str, bytes] = {}
    if spec is not None:
        artifacts["spec"] = _canonical_artifact(
            spec,
            name="visual spec",
            maximum=MAX_VISUAL_SPEC_BYTES,
            normalizer=lambda value: validate_visual_spec(value, evidence_graph=evidence_graph),
            size_code="E_VISUAL_SPEC_SIZE",
        )
    if scene is not None:
        artifacts["scene"] = _canonical_artifact(
            scene,
            name="scene",
            maximum=MAX_SCENE_BYTES,
            normalizer=validate_visual_scene,
        )
    if theme is not None:
        artifacts["theme"] = _canonical_artifact(
            theme,
            name="theme",
            maximum=MAX_COMPILED_BYTES,
            normalizer=_normalize_theme,
        )
    if timeline is not None:
        artifacts["timeline"] = _canonical_artifact(
            timeline,
            name="timeline",
            maximum=MAX_TIMELINE_BYTES,
            normalizer=_normalize_timeline,
        )
    if interaction is not None:
        artifacts["interaction"] = _canonical_artifact(
            interaction,
            name="interaction",
            maximum=MAX_INTERACTION_BYTES,
            normalizer=_normalize_interaction,
        )
    if gate is not None:
        artifacts["gate"] = _canonical_artifact(
            gate,
            name="gate",
            maximum=MAX_GATE_BYTES,
            normalizer=_normalize_gate,
        )
    if svg is not None:
        artifacts["svg"] = validate_visual_svg_bytes(svg)
    if sum(len(value) for value in artifacts.values()) > MAX_COMPILED_BYTES:
        raise _fail("E_VISUAL_RESOURCE", "compiled visual artifacts exceed the run byte limit")
    return MappingProxyType(dict(sorted(artifacts.items())))


def _safe_relative_path(value: Any) -> str:
    try:
        raw = os.fspath(value)
    except TypeError:
        raise _fail("E_VISUAL_PATH", "visual artifact path is invalid") from None
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _fail("E_VISUAL_PATH", "visual artifact path is invalid")
    normalized = unicodedata.normalize("NFC", raw)
    if (
        "\\" in normalized
        or normalized.startswith(("/", "~/", "//"))
        or _SCHEME.match(normalized)
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
    ):
        raise _fail("E_VISUAL_PATH", "visual artifact path must be a safe relative POSIX path")
    path = PurePosixPath(normalized)
    if path.is_absolute() or len(path.parts) == 0:
        raise _fail("E_VISUAL_PATH", "visual artifact path must be a safe relative POSIX path")
    return path.as_posix()


def read_visual_bytes(
    root: os.PathLike[str] | str,
    relative: os.PathLike[str] | str,
    *,
    maximum: int | None = None,
) -> bytes:
    """Read one regular artifact under a real root without following links."""

    safe_relative = _safe_relative_path(relative)
    limit = _bounded_maximum(maximum)
    try:
        root_path = Path(root)
        root_info = root_path.lstat()
        if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
            raise OSError
    except (OSError, TypeError, ValueError):
        raise _fail("E_VISUAL_PATH", "visual artifact root is unavailable or unsafe") from None
    destination = root_path.joinpath(*PurePosixPath(safe_relative).parts)
    try:
        return read_regular_bytes(
            destination,
            maximum=limit,
            path_code="E_VISUAL_PATH",
            size_code="E_VISUAL_RESOURCE",
        )
    except ContractError as exc:
        if exc.code == "E_VISUAL_RESOURCE":
            raise _fail("E_VISUAL_RESOURCE", "visual artifact exceeds its byte limit") from None
        raise _fail("E_VISUAL_PATH", "visual artifact is unavailable or unsafe") from None
    except OSError:
        raise _fail("E_VISUAL_PATH", "visual artifact is unavailable or unsafe") from None


__all__ = [
    "MAX_COMPILED_BYTES",
    "MAX_GATE_BYTES",
    "MAX_INTERACTION_BYTES",
    "MAX_SCENE_BYTES",
    "MAX_SVG_BYTES",
    "MAX_TIMELINE_BYTES",
    "MAX_VISUAL_SPEC_BYTES",
    "read_visual_bytes",
    "validate_visual_security",
    "validate_visual_svg_bytes",
]
