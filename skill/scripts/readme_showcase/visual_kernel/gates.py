"""Semantic and accessibility gates for one compiled visual variant.

Security validation runs before content checks.  Once the inputs have crossed
that boundary, independent semantic failures are represented in one
canonical :class:`VisualGateReport` instead of stopping at the first finding.
"""

from __future__ import annotations

import hashlib
import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Mapping, Sequence
from typing import Any

from ...pipeline_contracts import ContractError
from .diagnostics import VISUAL_DIAGNOSTIC_CODES, VisualDiagnostic, VisualGateReport
from .geometry import validate_visual_geometry
from .interaction import InteractionGraph
from .model import VisualSpec, validate_visual_spec
from .scene import Scene, validate_visual_scene
from .security import validate_visual_security
from .svg import serialize_svg
from .theme import Theme
from .timeline import Timeline


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SCENE_INTENT = "__scene_intent__"
_REPORT_FIELDS = frozenset(
    {"schema_version", "status", "spec_sha256", "scene_sha256", "svg_sha256", "diagnostics"}
)
_DIAGNOSTIC_FIELDS = frozenset({"code", "severity", "path", "element_ids", "message"})


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _json_value(value: Any, context: str) -> Any:
    if type(value) is not bytes:
        return value
    try:
        return json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _fail("E_VISUAL_RESOURCE", f"{context} is not canonical JSON") from None


def _strict_mapping(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    raw = dict(value)
    if any(not isinstance(key, str) for key in raw):
        raise _fail("E_SCHEMA_KEY_TYPE", f"{context} contains a non-string field name")
    unknown = sorted(set(raw) - fields)
    missing = sorted(fields - set(raw))
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return raw


def _hash(value: Any, context: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context} must be a lowercase SHA-256 digest")
    return value


def _diagnostic_from_error(
    error: ContractError,
    *,
    path: str | None,
    element_ids: Sequence[str] = (),
    fallback_code: str = "E_VISUAL_DETERMINISM",
) -> VisualDiagnostic:
    code = error.code if error.code in VISUAL_DIAGNOSTIC_CODES else fallback_code
    identifiers = tuple(sorted(set(element_ids), key=lambda item: item.encode("utf-8")))
    return VisualDiagnostic(code, "error", path, identifiers, str(error))


def _normalize_theme(value: Any) -> Theme:
    raw = _json_value(value, "theme")
    if isinstance(raw, Theme):
        return raw
    if not isinstance(raw, Mapping):
        raise _fail("E_SCHEMA_TYPE", "theme must be a Theme or JSON object")
    return Theme(raw["schema_version"], raw["colors"], raw["spacing"], raw["strokes"], raw["text"], raw["variants"])


def _normalize_timeline(value: Any) -> Timeline:
    raw = _json_value(value, "timeline")
    if isinstance(raw, Timeline):
        return raw
    if not isinstance(raw, Mapping):
        raise _fail("E_SCHEMA_TYPE", "timeline must be a Timeline or JSON object")
    return Timeline(raw["targets"], raw["duration_ms"], raw["operations"], raw["reduced_motion"])


def _tuple_map(value: Any, context: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for key, values in value.items():
        if not isinstance(key, str) or not isinstance(values, (list, tuple)):
            raise _fail("E_SCHEMA_TYPE", f"{context} must map IDs to arrays")
        result[key] = tuple(values)
    return result


def _normalize_interaction(value: Any) -> InteractionGraph:
    raw = _json_value(value, "interaction")
    if isinstance(raw, InteractionGraph):
        return raw
    if not isinstance(raw, Mapping):
        raise _fail("E_SCHEMA_TYPE", "interaction must be an InteractionGraph or JSON object")
    return InteractionGraph(
        tuple(raw["focus_order"]),
        _tuple_map(raw["evidence_links"], "interaction.evidence_links"),
        _tuple_map(raw["adjacency"], "interaction.adjacency"),
        _tuple_map(raw["group_navigation"], "interaction.group_navigation"),
        _tuple_map(raw["lane_navigation"], "interaction.lane_navigation"),
    )


def _normalizers(value: Any) -> tuple[VisualSpec, Scene, Theme, Timeline, InteractionGraph]:
    # The security gate has already checked structure.  These projections are
    # intentionally repeated so the semantic checks operate on typed values.
    return (
        validate_visual_spec(_json_value(value[0], "visual spec")),
        validate_visual_scene(_json_value(value[1], "scene")),
        _normalize_theme(value[2]),
        _normalize_timeline(value[3]),
        _normalize_interaction(value[4]),
    )


def _local_name(tag: Any) -> str:
    if not isinstance(tag, str):
        return ""
    return tag.rsplit("}", 1)[-1].lower()


def _text_content(node: ET.Element) -> str:
    return " ".join("".join(node.itertext()).split())


def _expected_evidence(spec: VisualSpec) -> dict[str, tuple[str, ...]]:
    values: dict[str, tuple[str, ...]] = {_SCENE_INTENT: spec.intent.evidence_ids}
    for item in (*spec.groups, *spec.lanes, *spec.nodes, *spec.edges):
        if item.label is not None:
            values[item.id] = item.evidence_ids
    return values


def _check_evidence(
    spec: VisualSpec,
    scene: Scene,
    interaction: InteractionGraph,
    evidence_graph: Mapping[str, Any] | None,
) -> VisualDiagnostic | None:
    try:
        if evidence_graph is None:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", "visual gate requires an Evidence v2 graph")
        validate_visual_spec(spec, evidence_graph=evidence_graph)
        expected = _expected_evidence(spec)
        scene_text = {item.source_id: item for item in scene.primitives if item.kind == "text"}
        missing = tuple(identifier for identifier in expected if identifier not in scene_text)
        if missing:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", "Scene is missing Evidence-bound visible claims")
        for identifier, evidence_ids in expected.items():
            if scene_text[identifier].evidence_ids != evidence_ids:
                raise _fail("E_VISUAL_SPEC_EVIDENCE", f"Scene claim Evidence drifted for {identifier}")
        for identifier in (*spec.groups, *spec.lanes, *spec.nodes):
            if identifier.id not in interaction.evidence_links or not interaction.evidence_links[identifier.id]:
                raise _fail("E_VISUAL_SPEC_EVIDENCE", f"focus target {identifier.id} has no Evidence link")
    except ContractError as error:
        return _diagnostic_from_error(
            error,
            path="$.evidence",
            element_ids=tuple(_expected_evidence(spec)),
            fallback_code="E_VISUAL_SPEC_EVIDENCE",
        )
    return None


def _check_focus_order(spec: VisualSpec, interaction: InteractionGraph) -> VisualDiagnostic | None:
    expected = tuple(
        item.id
        for item in (*spec.lanes, *spec.groups, *spec.nodes)
    )
    if interaction.focus_order != expected:
        return VisualDiagnostic(
            "E_VISUAL_DETERMINISM",
            "error",
            "$.interaction.focus_order",
            tuple(sorted(set(expected) | set(interaction.focus_order), key=lambda item: item.encode("utf-8"))),
            "interaction focus order does not match the canonical container-first order",
        )
    return None


def _check_svg_accessibility(svg: bytes, scene: Scene) -> list[VisualDiagnostic]:
    try:
        root = ET.fromstring(svg)
    except (ET.ParseError, UnicodeDecodeError):
        # Security has already rejected malformed XML; keep this defensive
        # branch as a content diagnostic if a custom validator is substituted.
        return [VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg", (), "SVG cannot be parsed")]
    titles = [node for node in root.iter() if _local_name(node.tag) == "title"]
    descriptions = [node for node in root.iter() if _local_name(node.tag) in {"desc", "description"}]
    diagnostics: list[VisualDiagnostic] = []
    if len(titles) != 1 or not _text_content(titles[0]):
        diagnostics.append(VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.title", (), "SVG requires one non-empty accessible title"))
    else:
        intent = next((item.text for item in scene.primitives if item.kind == "text" and item.source_id == _SCENE_INTENT), None)
        if intent is not None and _text_content(titles[0]) != intent:
            diagnostics.append(VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.title", (_SCENE_INTENT,), "SVG title differs from the Scene intent label"))
    if len(descriptions) != 1 or not _text_content(descriptions[0]):
        diagnostics.append(VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.description", (), "SVG requires one non-empty accessible description"))
    labelled = set(str(root.attrib.get("aria-labelledby", "")).split())
    described = set(str(root.attrib.get("aria-describedby", "")).split())
    if len(titles) == 1 and titles[0].attrib.get("id") not in labelled:
        diagnostics.append(VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.aria-labelledby", (), "SVG title is not referenced by aria-labelledby"))
    if len(descriptions) == 1 and descriptions[0].attrib.get("id") not in described:
        diagnostics.append(VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.aria-describedby", (), "SVG description is not referenced by aria-describedby"))
    return diagnostics


def _check_variant(spec: VisualSpec, scene: Scene, theme: Theme, spec_sha256: str) -> VisualDiagnostic | None:
    reasons: list[str] = []
    if scene.locale != spec.locale:
        reasons.append("Scene locale differs from the Visual Spec")
    if scene.variant not in spec.variants:
        reasons.append("Scene variant is not declared by the Visual Spec")
    if scene.source_spec_sha256 != spec_sha256:
        reasons.append("Scene source_spec_sha256 does not match the Visual Spec")
    if scene.theme_sha256 != theme.sha256():
        reasons.append("Scene theme_sha256 does not match the resolved Theme")
    if scene.variant not in theme.variants:
        reasons.append("Scene variant is missing from the Theme policy")
    if not reasons:
        return None
    return VisualDiagnostic(
        "E_VISUAL_FINGERPRINT",
        "error",
        "$.scene.variant",
        (scene.variant,),
        "; ".join(reasons),
    )


def _check_determinism(
    artifacts: Mapping[str, bytes],
    normalized: tuple[VisualSpec, Scene, Theme, Timeline, InteractionGraph],
) -> VisualDiagnostic | None:
    names = ("spec", "scene", "theme", "timeline", "interaction")
    for name, value in zip(names, normalized):
        canonical = value.canonical_bytes()
        if canonical != artifacts[name]:
            return VisualDiagnostic(
                "E_VISUAL_DETERMINISM",
                "error",
                f"$.{name}",
                (),
                f"{name} bytes are not the current canonical projection",
            )
    try:
        deterministic_svg = serialize_svg(normalized[1], normalized[2])
    except ContractError as error:
        return _diagnostic_from_error(error, path="$.svg", fallback_code="E_VISUAL_TEXT_FIT")
    if deterministic_svg != artifacts["svg"]:
        return VisualDiagnostic(
            "E_VISUAL_DETERMINISM",
            "error",
            "$.svg",
            (),
            "SVG bytes are not the deterministic projection of the Scene and Theme",
        )
    return None


def validate_visual_gate_report(value: Any) -> VisualGateReport:
    """Validate a closed Gate Report v1 value without silently reordering it."""

    if isinstance(value, VisualGateReport):
        report = VisualGateReport(
            value.status,
            value.spec_sha256,
            value.scene_sha256,
            value.svg_sha256,
            value.diagnostics,
        )
    else:
        raw = _strict_mapping(value, _REPORT_FIELDS, "visual gate report")
        if type(raw["schema_version"]) is not int or raw["schema_version"] != 1:
            raise _fail("E_SCHEMA_VERSION", "visual gate report requires schema_version 1")
        for name in ("spec_sha256", "scene_sha256", "svg_sha256"):
            _hash(raw[name], f"visual gate report.{name}")
        diagnostics_raw = raw["diagnostics"]
        if not isinstance(diagnostics_raw, list):
            raise _fail("E_SCHEMA_TYPE", "visual gate report.diagnostics must be an array")
        diagnostics: list[VisualDiagnostic] = []
        for index, item in enumerate(diagnostics_raw):
            if isinstance(item, VisualDiagnostic):
                diagnostics.append(item)
                continue
            diagnostic = _strict_mapping(item, _DIAGNOSTIC_FIELDS, f"visual gate report.diagnostics[{index}]")
            if not isinstance(diagnostic["element_ids"], list):
                raise _fail("E_SCHEMA_TYPE", "visual gate diagnostic element_ids must be an array")
            diagnostics.append(
                VisualDiagnostic(
                    diagnostic["code"],
                    diagnostic["severity"],
                    diagnostic["path"],
                    tuple(diagnostic["element_ids"]),
                    diagnostic["message"],
                )
            )
        ordered = tuple(sorted(diagnostics, key=VisualDiagnostic.sort_key))
        if tuple(diagnostics) != ordered:
            raise _fail("E_SCHEMA_VALUE", "visual gate report diagnostics must be canonically sorted")
        report = VisualGateReport(raw["status"], raw["spec_sha256"], raw["scene_sha256"], raw["svg_sha256"], tuple(diagnostics))
    for name in ("spec_sha256", "scene_sha256", "svg_sha256"):
        _hash(getattr(report, name), f"visual gate report.{name}")
    return report


def run_visual_gates(
    spec: Any,
    scene: Any,
    theme: Any,
    timeline: Any,
    interaction: Any,
    svg: Any,
    *,
    evidence_graph: Mapping[str, Any],
) -> VisualGateReport:
    """Run trust, geometry, semantic, and accessibility checks for one variant."""

    # Do not catch this call: malformed paths, unsafe SVG, special files, and
    # resource exhaustion are trust-boundary failures and must abort promotion.
    artifacts = validate_visual_security(
        spec=spec,
        scene=scene,
        theme=theme,
        timeline=timeline,
        interaction=interaction,
        svg=svg,
    )
    # Normalize the immutable bytes returned by the security gate, not the
    # caller's original mutable mappings.
    normalized = _normalizers(
        (
            artifacts["spec"],
            artifacts["scene"],
            artifacts["theme"],
            artifacts["timeline"],
            artifacts["interaction"],
        )
    )
    normalized_spec, normalized_scene, normalized_theme, normalized_timeline, normalized_interaction = normalized
    diagnostics: list[VisualDiagnostic] = []

    evidence_diagnostic = _check_evidence(
        normalized_spec,
        normalized_scene,
        normalized_interaction,
        evidence_graph,
    )
    if evidence_diagnostic is not None:
        diagnostics.append(evidence_diagnostic)

    focus_diagnostic = _check_focus_order(normalized_spec, normalized_interaction)
    if focus_diagnostic is not None:
        diagnostics.append(focus_diagnostic)

    try:
        validate_visual_geometry(normalized_scene)
    except ContractError as error:
        diagnostics.append(_diagnostic_from_error(error, path="$.scene", fallback_code="E_VISUAL_GEOMETRY"))

    diagnostics.extend(_check_svg_accessibility(artifacts["svg"], normalized_scene))

    spec_sha256 = hashlib.sha256(artifacts["spec"]).hexdigest()
    scene_sha256 = hashlib.sha256(artifacts["scene"]).hexdigest()
    svg_sha256 = hashlib.sha256(artifacts["svg"]).hexdigest()
    variant_diagnostic = _check_variant(normalized_spec, normalized_scene, normalized_theme, spec_sha256)
    if variant_diagnostic is not None:
        diagnostics.append(variant_diagnostic)

    determinism_diagnostic = _check_determinism(artifacts, normalized)
    if determinism_diagnostic is not None:
        diagnostics.append(determinism_diagnostic)

    report = VisualGateReport.build(spec_sha256, scene_sha256, svg_sha256, diagnostics)
    return validate_visual_gate_report(report)


__all__ = ["run_visual_gates", "validate_visual_gate_report"]
