"""Deterministic stage-6 visual compiler composition.

The compiler is intentionally a thin composition layer.  Semantic validation,
layout policy, geometry validation, SVG serialization, derived state, and
artifact inventory remain owned by their existing modules; this module only
binds them into one fail-closed operation.
"""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, read_regular_bytes
from .artifacts import _preflight_files, build_compiled_artifacts
from .elk_backend import ElkGeometryResult, render_elk_geometry
from .gates import run_visual_gates
from .graph import CompiledGraph, compile_graph
from .interaction import derive_interaction
from .normalize import Plan, normalize_visual_spec
from .scene import build_scene
from .svg import serialize_svg
from .swimlane import plan_swimlanes
from .text import TextFitResult, fit_text
from .theme import Theme, resolve_theme
from .timeline import derive_timeline


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EDGE_ID = re.compile(r"edge-(0|[1-9][0-9]*)\Z")
_INTENT_ADAPTER_TYPES = {
    "architecture": "architecture",
    "flow": "flowchart",
    "swimlane": "flowchart",
    "sequence": "flowchart",
}
_NODE_ADAPTER_KINDS = {
    "actor": "person",
    "service": "service",
    "process": "component",
    "store": "database",
    "decision": "container",
    "note": "external",
}
_DIRECTIONS = {"desktop": "LR", "mobile": "TB"}
_SCENE_INTENT = "__scene_intent__"


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _kernel_identity() -> str:
    """Hash the bounded source projection that defines this kernel package."""

    root = Path(__file__).parent
    try:
        paths = sorted(
            (path for path in root.iterdir() if path.name.endswith(".py")),
            key=lambda path: path.name.encode("utf-8"),
        )
    except OSError:
        raise _fail("E_ENGINE_IDENTITY", "visual kernel source package is unavailable") from None
    records: list[dict[str, str]] = []
    total = 0
    for path in paths:
        try:
            raw = read_regular_bytes(
                path,
                maximum=256 * 1024,
                path_code="E_ENGINE_IDENTITY",
                size_code="E_ENGINE_IDENTITY",
            )
        except ContractError:
            raise _fail("E_ENGINE_IDENTITY", "visual kernel source package is unavailable") from None
        total += len(raw)
        if total > 2 * 1024 * 1024:
            raise _fail("E_ENGINE_IDENTITY", "visual kernel source package exceeds its identity bound")
        records.append({"name": path.name, "sha256": hashlib.sha256(raw).hexdigest()})
    if not records:
        raise _fail("E_ENGINE_IDENTITY", "visual kernel source package is empty")
    return hashlib.sha256(canonical_json_bytes({"schema_version": 1, "files": records})).hexdigest()


@dataclass(frozen=True, slots=True)
class CompiledVisual:
    """Immutable compiled artifact map and its inventory identity."""

    artifacts: Mapping[str, bytes]
    inventory_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.artifacts, Mapping) or not self.artifacts:
            raise _fail("E_SCHEMA_TYPE", "compiled visual artifacts must be a non-empty mapping")
        values: dict[str, bytes] = {}
        for path, value in self.artifacts.items():
            if not isinstance(path, str) or type(value) is not bytes:
                raise _fail("E_SCHEMA_TYPE", "compiled visual artifacts must map paths to bytes")
            values[path] = value
        ordered = {path: values[path] for path in sorted(values, key=lambda item: item.encode("utf-8"))}
        try:
            validated = _preflight_files(ordered, require_inventory=True)
        except ContractError:
            raise
        object.__setattr__(self, "artifacts", MappingProxyType(validated))
        if not isinstance(self.inventory_sha256, str) or _SHA256.fullmatch(self.inventory_sha256) is None:
            raise _fail("E_VISUAL_FINGERPRINT", "compiled visual inventory identity is invalid")
        try:
            inventory_bytes = validated["compiled/inventory.json"]
            inventory = json.loads(inventory_bytes.decode("utf-8"))
        except (KeyError, UnicodeDecodeError, json.JSONDecodeError, RecursionError):
            raise _fail("E_VISUAL_FINGERPRINT", "compiled visual inventory is unavailable") from None
        inventory_hash = inventory.get("inventory_sha256") if isinstance(inventory, Mapping) else None
        if (
            not isinstance(inventory, Mapping)
            or set(inventory) != {"schema_version", "layers", "inventory_sha256"}
            or inventory.get("schema_version") != 1
            or not isinstance(inventory_hash, str)
            or _SHA256.fullmatch(inventory_hash) is None
        ):
            raise _fail("E_VISUAL_FINGERPRINT", "compiled visual inventory is not a closed canonical projection")
        try:
            canonical_inventory = canonical_json_bytes(inventory)
        except ContractError:
            raise _fail("E_VISUAL_DETERMINISM", "compiled visual inventory is not canonical") from None
        if canonical_inventory != inventory_bytes:
            raise _fail("E_VISUAL_DETERMINISM", "compiled visual inventory is not canonical")
        if inventory_hash != self.inventory_sha256:
            raise _fail("E_VISUAL_FINGERPRINT", "compiled visual inventory identity does not match artifacts")


def _adapter_id(prefix: str, identifier: str) -> str:
    return f"{prefix}-{hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:24]}"


def _claim_id(kind: str, identifier: str) -> str:
    return _adapter_id("claim", f"{kind}\x00{identifier}")


def _adapter_label(value: str, context: str) -> str:
    if not isinstance(value, str) or not value or "\n" in value or "\r" in value or len(value) > 120:
        raise _fail("E_VISUAL_TEXT_FIT", f"{context} cannot be represented by the bounded geometry adapter")
    return value


def _walk_projection(value: Any) -> tuple[tuple[str, str | None], tuple[str, ...]]:
    """Return adapter hierarchy parent bindings and stable traversal order."""

    if not isinstance(value, Mapping) or not isinstance(value.get("children"), list):
        raise _fail("E_VISUAL_SPEC_EDGE", "layout projection does not expose a child hierarchy")
    parents: list[tuple[str, str | None]] = []
    order: list[str] = []

    def walk(children: list[Any], parent: str | None) -> None:
        for child in children:
            if not isinstance(child, Mapping) or not isinstance(child.get("id"), str):
                raise _fail("E_VISUAL_SPEC_EDGE", "layout projection contains an invalid element")
            identifier = child["id"]
            parents.append((identifier, parent))
            order.append(identifier)
            nested = child.get("children")
            if nested is not None:
                if not isinstance(nested, list):
                    raise _fail("E_VISUAL_SPEC_EDGE", "layout projection children are not an array")
                walk(nested, identifier)

    walk(value["children"], None)
    return tuple(parents), tuple(order)


def _metrics(theme: Theme, variant: str) -> dict[str, int]:
    policy = theme.variants[variant]
    return {
        "canvas": theme.spacing["canvas"],
        "section": theme.spacing["section"],
        "node": theme.spacing["node"],
        "lane": theme.spacing["lane"],
        "label": theme.spacing["label"],
        "width": policy["width"],
        "min_font_size": policy["min_font_size"],
    }


def _hierarchy(plan: Plan, graph: CompiledGraph, variant: str, theme: Theme) -> tuple[tuple[str, str | None], tuple[str, ...]]:
    if plan.intent.kind == "swimlane":
        swimlane = plan_swimlanes(plan, graph, _metrics(theme, variant), variant)
        # This projection is the source for both the adapter hierarchy and its
        # traversal order; the planner is never called merely for validation.
        return _walk_projection(swimlane.as_dict())
    return _walk_projection(graph.as_dict())


def _semantic_envelope(plan: Plan, graph: CompiledGraph, theme: Theme, variant: str) -> tuple[dict[str, Any], dict[str, str]]:
    parents, traversal = _hierarchy(plan, graph, variant, theme)
    parent_by_id = dict(parents)
    groups = {item.id: item for item in (*plan.groups, *plan.lanes)}
    nodes = {item.id: item for item in plan.nodes}
    if set(parent_by_id) != set(groups) | set(nodes):
        raise _fail("E_VISUAL_SPEC_EDGE", "layout hierarchy does not preserve every Plan element")

    semantic_to_adapter: dict[str, str] = {}
    for identifier in (*groups, *nodes):
        prefix = "group" if identifier in groups else "node"
        semantic_to_adapter[identifier] = _adapter_id(prefix, identifier)

    ordered_groups = [identifier for identifier in traversal if identifier in groups]
    ordered_nodes = [identifier for identifier in traversal if identifier in nodes]
    # A projection with no child hierarchy (or a custom graph projection) must
    # still obey the CompiledGraph rank/order contract for node input order.
    rank_by_id = dict(graph.ranks)
    position_by_id = dict(graph.orders)
    ordered_nodes = sorted(
        ordered_nodes,
        key=lambda identifier: (
            rank_by_id.get(identifier, 0),
            position_by_id.get(identifier, 0),
            identifier.encode("utf-8"),
        ),
    )

    claim_by_id = {identifier: _claim_id("label", identifier) for identifier in (*groups, *nodes)}
    accessibility_claim_id = _claim_id("intent", _SCENE_INTENT)
    claim_ids = [accessibility_claim_id, *claim_by_id.values()]
    for item in plan.edges:
        if item.label is not None:
            claim_by_id[item.id] = _claim_id("label", item.id)
            claim_ids.append(claim_by_id[item.id])
    claim_ids = sorted(set(claim_ids))

    envelope_groups: list[dict[str, Any]] = []
    for identifier in ordered_groups:
        item = groups[identifier]
        if item.label is None:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{identifier} requires a visible Evidence-bound label")
        parent = parent_by_id.get(identifier)
        envelope_groups.append(
            {
                "id": semantic_to_adapter[identifier],
                "label": _adapter_label(item.label, f"{identifier}.label"),
                "parent_id": semantic_to_adapter.get(parent) if parent is not None else None,
                "claim_id": claim_by_id[identifier],
            }
        )

    envelope_nodes: list[dict[str, Any]] = []
    for identifier in ordered_nodes:
        item = nodes[identifier]
        if item.label is None:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{identifier} requires a visible Evidence-bound label")
        parent = parent_by_id.get(identifier)
        if parent is not None and parent not in groups:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {identifier} has an invalid hierarchy parent")
        envelope_nodes.append(
            {
                "id": semantic_to_adapter[identifier],
                "label": _adapter_label(item.label, f"{identifier}.label"),
                "group_id": semantic_to_adapter.get(parent) if parent is not None else None,
                "kind": _NODE_ADAPTER_KINDS[item.kind],
                "claim_id": claim_by_id[identifier],
            }
        )

    envelope_edges: list[dict[str, Any]] = []
    node_map = {item.id: item for item in plan.nodes}
    for item in plan.edges:
        edge_label = None if item.label is None else _adapter_label(item.label, f"{item.id}.label")
        envelope_edges.append(
            {
                "source": semantic_to_adapter[node_map[item.source].id],
                "target": semantic_to_adapter[node_map[item.target].id],
                "label": edge_label,
                "claim_id": None if edge_label is None else claim_by_id[item.id],
            }
        )

    if plan.intent.label is None:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", "visual intent requires a visible Evidence-bound label")
    palette = {
        "background": theme.colors["background"],
        "node_background": theme.colors["surface"],
        "node_border": theme.colors["accent"],
        "node_text": theme.colors["text"],
        "edge_color": theme.colors["line"],
        "edge_label_color": theme.colors["muted"],
    }
    envelope = {
        "schema_version": 1,
        "diagram_type": _INTENT_ADAPTER_TYPES[plan.intent.kind],
        "accessibility_title": _adapter_label(plan.intent.label, "intent.label"),
        "accessibility_claim_id": accessibility_claim_id,
        "direction": _DIRECTIONS[variant],
        "palette": palette,
        "groups": envelope_groups,
        "nodes": envelope_nodes,
        "edges": envelope_edges,
        "claim_ids": claim_ids,
    }
    return envelope, semantic_to_adapter


def _restore_geometry(result: ElkGeometryResult, semantic_to_adapter: Mapping[str, str]) -> dict[str, Any]:
    raw = result.as_dict()["geometry"]
    inverse = {value: key for key, value in semantic_to_adapter.items()}
    for name in ("groups", "nodes"):
        values = raw.get(name)
        if not isinstance(values, list):
            raise _fail("E_OUTPUT_GEOMETRY", f"geometry.{name} is unavailable")
        restored: list[dict[str, Any]] = []
        for item in values:
            if not isinstance(item, Mapping):
                raise _fail("E_OUTPUT_GEOMETRY", f"geometry.{name} contains an invalid element")
            value = dict(item)
            identifier = inverse.get(value.get("id"))
            if identifier is None:
                raise _fail("E_OUTPUT_GEOMETRY", f"geometry.{name} contains an unknown semantic ID")
            value["id"] = identifier
            parent = value.get("parent_id")
            value["parent_id"] = inverse.get(parent) if parent is not None else None
            if parent is not None and value["parent_id"] is None:
                raise _fail("E_OUTPUT_GEOMETRY", "geometry parent ID is unknown")
            restored.append(value)
        raw[name] = sorted(restored, key=lambda item: item["id"].encode("utf-8"))
    edges = raw.get("edges")
    if not isinstance(edges, list):
        raise _fail("E_OUTPUT_GEOMETRY", "geometry.edges is unavailable")
    numbered: list[tuple[int, dict[str, Any]]] = []
    for item in edges:
        if not isinstance(item, Mapping):
            raise _fail("E_OUTPUT_GEOMETRY", "geometry.edges contains an invalid element")
        identifier = item.get("id")
        match = _EDGE_ID.fullmatch(identifier) if isinstance(identifier, str) else None
        if match is None:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry edge IDs must be contiguous edge-N values")
        numbered.append((int(match.group(1)), dict(item)))
    expected = set(range(len(numbered)))
    if {index for index, _ in numbered} != expected:
        raise _fail("E_OUTPUT_GEOMETRY", "geometry edge IDs must be contiguous edge-N values")
    raw["edges"] = [item for _, item in sorted(numbered, key=lambda entry: entry[0])]
    return raw


def _label_owner_width(geometry: Mapping[str, Any], source_id: str, variant: str, theme: Theme) -> int:
    if source_id == _SCENE_INTENT:
        return int(theme.variants[variant]["width"])
    for name in ("groups", "nodes"):
        for item in geometry[name]:
            if item["id"] == source_id:
                return max(1, int(item["width"]) - theme.spacing["label"])
    # Edge labels are positioned at their route midpoint by Scene v1.  Bound
    # fitting to the remaining right-hand canvas so the geometry gate cannot
    # observe an overrun.
    if source_id.startswith("__edge__:"):
        index = int(source_id.split(":", 1)[1])
        points: list[Mapping[str, int]] = []
        for section in geometry["edges"][index]["sections"]:
            points.extend((section["start"], *section["bends"], section["end"]))
        midpoint = points[len(points) // 2]
        return max(1, int(theme.variants[variant]["width"]) - int(midpoint["x"]))
    return max(1, int(theme.variants[variant]["width"]))


def _text_fits(plan: Plan, geometry: Mapping[str, Any], theme: Theme, variant: str) -> dict[str, TextFitResult]:
    minimum_font = max(theme.text["core"], theme.variants[variant]["min_font_size"])
    result: dict[str, TextFitResult] = {}
    if plan.intent.label is None:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", "visual intent requires a visible label")
    intent_fit = fit_text(
        plan.intent.label,
        width=_label_owner_width(geometry, _SCENE_INTENT, variant, theme),
        role="title",
        variant=variant,
        font_size=minimum_font,
    )
    if intent_fit.status != "fit":
        raise _fail("E_VISUAL_TEXT_FIT", intent_fit.message or "intent label does not fit")
    result[_SCENE_INTENT] = intent_fit
    for item in (*plan.groups, *plan.lanes, *plan.nodes):
        if item.label is None:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{item.id} requires a visible label")
        role = "group" if item in plan.groups else "lane" if item in plan.lanes else "node"
        fit = fit_text(
            item.label,
            width=_label_owner_width(geometry, item.id, variant, theme),
            role=role,
            variant=variant,
            font_size=minimum_font,
        )
        if fit.status != "fit":
            raise _fail("E_VISUAL_TEXT_FIT", fit.message or f"{item.id} does not fit")
        result[item.id] = fit
    for index, item in enumerate(plan.edges):
        if item.label is None:
            continue
        fit = fit_text(
            item.label,
            width=_label_owner_width(geometry, f"__edge__:{index}", variant, theme),
            role="edge",
            variant=variant,
            font_size=minimum_font,
        )
        if fit.status != "fit":
            raise _fail("E_VISUAL_TEXT_FIT", fit.message or f"{item.id} does not fit")
        result[item.id] = fit
    return result


def _identity_from_geometry(result: ElkGeometryResult) -> tuple[str, str]:
    engine = result.identity
    if not isinstance(engine, Mapping):
        raise _fail("E_ENGINE_IDENTITY", "ELK geometry result has no verified engine identity")
    package = engine.get("package_sha256")
    renderer = engine.get("renderer_sha256")
    if (
        not isinstance(package, str)
        or not isinstance(renderer, str)
        or _SHA256.fullmatch(package) is None
        or _SHA256.fullmatch(renderer) is None
    ):
        raise _fail("E_ENGINE_IDENTITY", "ELK geometry result identity is incomplete")
    return package, renderer


def _compile_once(spec: Any, evidence_graph: Mapping[str, Any], repository_tokens: Mapping[str, Any] | None) -> CompiledVisual:
    kernel_identity = _kernel_identity()
    plan = normalize_visual_spec(spec, evidence_graph)
    theme = resolve_theme(repository_tokens)
    graph = compile_graph(plan)
    timeline = derive_timeline(plan)
    interaction = derive_interaction(plan)
    variants: list[dict[str, Any]] = []
    identities: tuple[str, str] | None = None

    for variant in plan.variants:
        envelope, semantic_to_adapter = _semantic_envelope(plan, graph, theme, variant)
        with tempfile.TemporaryDirectory(prefix=".visual-kernel-") as attempt:
            geometry_result = render_elk_geometry(envelope, attempt)
        current_identity = _identity_from_geometry(geometry_result)
        if identities is None:
            identities = current_identity
        elif identities != current_identity:
            raise _fail("E_VISUAL_FINGERPRINT", "ELK package or renderer identity drifted across variants")
        geometry = _restore_geometry(geometry_result, semantic_to_adapter)
        fits = _text_fits(plan, geometry, theme, variant)
        scene = build_scene(plan, theme, fits, geometry, variant)
        svg = serialize_svg(scene, theme)
        gate = run_visual_gates(
            spec,
            scene,
            theme,
            timeline,
            interaction,
            svg,
            evidence_graph=evidence_graph,
        )
        if gate.status != "pass":
            if gate.diagnostics:
                diagnostic = gate.diagnostics[0]
                raise ContractError(diagnostic.code, diagnostic.message)
            raise _fail("E_VISUAL_DETERMINISM", "visual gate report contains hard failures")
        variants.append(
            {
                "locale": plan.locale,
                "variant": variant,
                "scene": scene,
                "svg": svg,
                "gate": gate,
                "timeline": timeline,
                "interaction": interaction,
            }
        )

    if identities is None:
        raise _fail("E_VISUAL_SPEC_EDGE", "Visual Spec declares no variants")
    artifacts = build_compiled_artifacts(
        spec,
        theme,
        variants,
        {"kernel": kernel_identity, "elk": identities[0], "renderer": identities[1]},
        evidence_graph=evidence_graph,
    )
    inventory_raw = artifacts["compiled/inventory.json"]
    try:
        inventory = json.loads(inventory_raw.decode("utf-8"))
        inventory_sha256 = inventory["inventory_sha256"]
    except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, RecursionError):
        raise _fail("E_VISUAL_FINGERPRINT", "compiled inventory cannot be read") from None
    return CompiledVisual(artifacts, inventory_sha256)


def compile_visual(
    spec: Any,
    evidence_graph: Mapping[str, Any],
    repository_tokens: Mapping[str, Any] | None = None,
) -> CompiledVisual:
    """Compile one Evidence-bound Visual Spec into immutable stage-6 bytes."""

    first = _compile_once(spec, evidence_graph, repository_tokens)
    second = _compile_once(spec, evidence_graph, repository_tokens)
    if first.artifacts != second.artifacts or first.inventory_sha256 != second.inventory_sha256:
        raise _fail("E_VISUAL_DETERMINISM", "visual compilation changed between identical runs")
    return first


__all__ = ["CompiledVisual", "compile_visual"]
