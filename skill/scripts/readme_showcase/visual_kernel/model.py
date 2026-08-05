"""Immutable, Evidence-bound Visual Spec v1 values.

The model deliberately owns only semantic input.  Layout coordinates, assets,
and renderer details belong to later compiler stages and are not accepted here.
"""

from __future__ import annotations

import copy
import re
import unicodedata
from dataclasses import dataclass
from typing import Any, Mapping

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.common import MAX_JSON_DEPTH, MAX_JSON_NODES, normalize_text
from ..contracts.evidence import validate_evidence_graph
from ..contracts.locale import parse_locale


VISUAL_SPEC_SCHEMA_VERSION = 1
MAX_VISUAL_SPEC_BYTES = 256 * 1024

VISUAL_INTENT_KINDS = frozenset({"architecture", "flow", "swimlane", "sequence"})
VISUAL_VARIANTS = frozenset({"desktop", "mobile"})
VISUAL_NODE_KINDS = frozenset({"actor", "service", "process", "store", "decision", "note"})
VISUAL_EDGE_KINDS = frozenset({"flow", "dependency", "data", "back"})

_SHA256_EVIDENCE = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")
_SCHEME = re.compile(r"(?i)\b(?:https?|ftp|file|data|javascript|mailto):(?:/{1,2}|[^\s])")
_ABSOLUTE_PATH = re.compile(
    r"(?:^|(?<![A-Za-z0-9_]))/(?![\s/])(?:[^\s/]+/)*[^\s/]+|~/|[A-Za-z]:[\\/]|\\\\[^\\/]+[\\/]"
)
_TRAVERSAL = re.compile(r"(?:^|[/\\])\.\.(?:[/\\]|$)")

_TOP_FIELDS = frozenset(
    {"schema_version", "intent", "locale", "variants", "nodes", "edges", "groups", "lanes", "constraints"}
)
_INTENT_FIELDS = frozenset({"kind", "label", "evidence_ids"})
_NODE_FIELDS = frozenset({"id", "kind", "label", "evidence_ids", "group_id", "lane_id"})
_EDGE_FIELDS = frozenset({"id", "kind", "source", "target", "label", "evidence_ids"})
_GROUP_FIELDS = frozenset({"id", "label", "evidence_ids"})
_LANE_FIELDS = frozenset({"id", "label", "evidence_ids"})
_CONSTRAINT_FIELDS = frozenset({"target", "order", "rank", "pin"})


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _reject_floats(value: Any, path: str = "$") -> None:
    stack = [(value, path, 0)]
    nodes = 1
    while stack:
        item, item_path, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            raise _fail("E_VISUAL_SPEC_SIZE", "visual spec exceeds structural limits")
        if isinstance(item, float):
            raise _fail("E_SCHEMA_FLOAT", f"{item_path} must not contain floats")
        if isinstance(item, list):
            nodes += len(item)
            if nodes > MAX_JSON_NODES:
                raise _fail("E_VISUAL_SPEC_SIZE", "visual spec exceeds structural limits")
            stack.extend(
                (item[index], f"{item_path}[{index}]", depth + 1)
                for index in range(len(item) - 1, -1, -1)
            )
        elif isinstance(item, dict):
            nodes += len(item)
            if nodes > MAX_JSON_NODES:
                raise _fail("E_VISUAL_SPEC_SIZE", "visual spec exceeds structural limits")
            for key, child in reversed(item.items()):
                if not isinstance(key, str):
                    raise _fail("E_SCHEMA_KEY_TYPE", f"{item_path} contains a non-string object key")
                stack.append((child, f"{item_path}.{key}", depth + 1))


def _closed(
    value: Any,
    fields: frozenset[str],
    path: str,
    *,
    required: frozenset[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an object")
    unknown = sorted(set(value) - fields)
    if unknown:
        # Resource-looking additions receive the resource-specific hard gate;
        # all other additions remain ordinary closed-schema failures.
        if unknown[0].casefold() in {"url", "path", "href", "src"}:
            raise _fail("E_VISUAL_PATH", f"{path} contains an unsupported path field: {unknown[0]}")
        if unknown[0].casefold() in {"asset", "assets", "bytes", "data", "icon", "font", "script"}:
            raise _fail("E_VISUAL_RESOURCE", f"{path} contains an unsupported resource field: {unknown[0]}")
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"{path} contains unknown field: {unknown[0]}")
    required_fields = fields if required is None else required
    missing = sorted(required_fields - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"{path} is missing required field: {missing[0]}")
    return value


def _text(value: Any, path: str, *, maximum: int = 4096) -> str:
    try:
        normalized = normalize_text(value, path, maximum=maximum)
    except ContractError as exc:
        raise exc
    if _SCHEME.search(normalized):
        raise _fail("E_VISUAL_PATH", f"{path} must not contain a URL or resource scheme")
    if _ABSOLUTE_PATH.search(normalized) or _TRAVERSAL.search(normalized):
        raise _fail("E_VISUAL_PATH", f"{path} must not contain a path")
    return normalized


def _id(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise _fail("E_VISUAL_SPEC_ID", f"{path} must be a non-empty ID")
    normalized = unicodedata.normalize("NFC", value)
    # IDs are identity, not display text: a caller must supply the canonical
    # NFC spelling instead of having the validator silently rewrite it.
    if normalized != value:
        raise _fail("E_VISUAL_SPEC_ID", f"{path} must already be NFC-normalized")
    if (_SCHEME.search(value) and not _SHA256_EVIDENCE.fullmatch(value)) or _ABSOLUTE_PATH.search(value) or _TRAVERSAL.search(value):
        raise _fail("E_VISUAL_PATH", f"{path} must not contain a path or URL")
    return value


def _sorted_unique(values: Any, path: str, *, code: str = "E_VISUAL_SPEC_ID") -> tuple[str, ...]:
    if not isinstance(values, list) or not values:
        raise _fail(code, f"{path} must contain one or more IDs")
    result = tuple(_id(value, f"{path}[]") for value in values)
    if len(result) != len(set(result)):
        raise _fail(code, f"{path} must not contain duplicate IDs")
    if result != tuple(sorted(result, key=lambda item: item.encode("utf-8"))):
        raise _fail(code, f"{path} must be byte-sorted")
    return result


def _evidence_ids(value: Any, path: str, *, required: bool = True) -> tuple[str, ...]:
    if value is None and not required:
        return ()
    identifiers = _sorted_unique(value, path, code="E_VISUAL_SPEC_EVIDENCE")
    if any(not _SHA256_EVIDENCE.fullmatch(identifier) for identifier in identifiers):
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{path} must contain Evidence v2 IDs")
    return identifiers


def _label_and_evidence(
    raw: dict[str, Any],
    path: str,
    *,
    required: bool = False,
) -> tuple[str | None, tuple[str, ...]]:
    if "label" not in raw:
        if required:
            raise _fail("E_SCHEMA_MISSING_FIELD", f"{path} is missing required field: label")
        # A non-visible relation/container may omit its label and therefore
        # has no user-facing evidence binding.
        if "evidence_ids" in raw:
            raise _fail("E_VISUAL_SPEC_EVIDENCE", f"{path}.evidence_ids requires a visible label")
        return None, ()
    label = raw["label"]
    if label is None:
        raise _fail("E_SCHEMA_TYPE", f"{path}.label must be a string when present")
    return _text(label, f"{path}.label"), _evidence_ids(raw.get("evidence_ids"), f"{path}.evidence_ids")


def _assert_collection_order(items: tuple[Any, ...], path: str) -> None:
    identifiers = tuple(item.id for item in items)
    if identifiers != tuple(sorted(identifiers, key=lambda item: item.encode("utf-8"))):
        raise _fail("E_VISUAL_SPEC_ID", f"{path} must be sorted by byte-sorted ID")


@dataclass(frozen=True, slots=True)
class VisualIntent:
    kind: str
    label: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"kind": self.kind}
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        return result


@dataclass(frozen=True, slots=True)
class VisualNode:
    id: str
    kind: str
    label: str | None = None
    evidence_ids: tuple[str, ...] = ()
    group_id: str | None = None
    lane_id: str | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id, "kind": self.kind}
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        if self.group_id is not None:
            result["group_id"] = self.group_id
        if self.lane_id is not None:
            result["lane_id"] = self.lane_id
        return result


@dataclass(frozen=True, slots=True)
class VisualEdge:
    id: str
    kind: str
    source: str
    target: str
    label: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "source": self.source,
            "target": self.target,
        }
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        return result


@dataclass(frozen=True, slots=True)
class VisualGroup:
    id: str
    label: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id}
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        return result


@dataclass(frozen=True, slots=True)
class VisualLane:
    id: str
    label: str | None = None
    evidence_ids: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"id": self.id}
        if self.label is not None:
            result["label"] = self.label
            result["evidence_ids"] = list(self.evidence_ids)
        return result


@dataclass(frozen=True, slots=True)
class VisualConstraint:
    target: str
    order: int | None = None
    rank: int | None = None
    pin: int | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {"target": self.target}
        if self.order is not None:
            result["order"] = self.order
        if self.rank is not None:
            result["rank"] = self.rank
        if self.pin is not None:
            result["pin"] = self.pin
        return result


@dataclass(frozen=True, slots=True)
class VisualSpec:
    schema_version: int
    intent: VisualIntent
    locale: str
    variants: tuple[str, ...]
    nodes: tuple[VisualNode, ...]
    edges: tuple[VisualEdge, ...]
    groups: tuple[VisualGroup, ...]
    lanes: tuple[VisualLane, ...]
    constraints: tuple[VisualConstraint, ...]

    def as_dict(self) -> dict[str, Any]:
        # Construct fresh containers on every call; callers cannot mutate the
        # immutable value graph through a serialized projection.
        return {
            "schema_version": self.schema_version,
            "intent": self.intent.as_dict(),
            "locale": self.locale,
            "variants": list(self.variants),
            "nodes": [item.as_dict() for item in self.nodes],
            "edges": [item.as_dict() for item in self.edges],
            "groups": [item.as_dict() for item in self.groups],
            "lanes": [item.as_dict() for item in self.lanes],
            "constraints": [item.as_dict() for item in self.constraints],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def _canonical_size(payload: Any) -> None:
    try:
        size = len(canonical_json_bytes(payload))
    except ContractError:
        raise
    if size > MAX_VISUAL_SPEC_BYTES:
        raise _fail("E_VISUAL_SPEC_SIZE", f"visual spec exceeds {MAX_VISUAL_SPEC_BYTES} canonical bytes")


def _validate_kind(value: Any, allowed: frozenset[str], path: str) -> str:
    kind = _text(value, path)
    if kind not in allowed:
        raise _fail("E_SCHEMA_VALUE", f"{path} contains an unsupported value")
    return kind


def _validate_int(value: Any, path: str) -> int:
    if type(value) is not int:
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an integer")
    if value < 0:
        raise _fail("E_SCHEMA_VALUE", f"{path} must be non-negative")
    return value


def _parse_intent(value: Any) -> VisualIntent:
    raw = _closed(value, _INTENT_FIELDS, "visual spec.intent", required=_INTENT_FIELDS)
    kind = _validate_kind(raw["kind"], VISUAL_INTENT_KINDS, "visual spec.intent.kind")
    label, evidence_ids = _label_and_evidence(raw, "visual spec.intent", required=True)
    return VisualIntent(kind, label, evidence_ids)


def _parse_node(value: Any, index: int) -> VisualNode:
    path = f"visual spec.nodes[{index}]"
    raw = _closed(value, _NODE_FIELDS, path, required=frozenset({"id", "kind", "label", "evidence_ids"}))
    identifier = _id(raw["id"], f"{path}.id")
    kind = _validate_kind(raw["kind"], VISUAL_NODE_KINDS, f"{path}.kind")
    label, evidence_ids = _label_and_evidence(raw, path, required=True)
    group_id = None if raw.get("group_id") is None else _id(raw["group_id"], f"{path}.group_id")
    lane_id = None if raw.get("lane_id") is None else _id(raw["lane_id"], f"{path}.lane_id")
    if "group_id" in raw and group_id is None:
        raise _fail("E_VISUAL_SPEC_ID", f"{path}.group_id must not be null")
    if "lane_id" in raw and lane_id is None:
        raise _fail("E_VISUAL_SPEC_ID", f"{path}.lane_id must not be null")
    return VisualNode(identifier, kind, label, evidence_ids, group_id, lane_id)


def _parse_edge(value: Any, index: int) -> VisualEdge:
    path = f"visual spec.edges[{index}]"
    raw = _closed(value, _EDGE_FIELDS, path, required=frozenset({"id", "kind", "source", "target"}))
    identifier = _id(raw["id"], f"{path}.id")
    kind = _validate_kind(raw["kind"], VISUAL_EDGE_KINDS, f"{path}.kind")
    source = _id(raw["source"], f"{path}.source")
    target = _id(raw["target"], f"{path}.target")
    label, evidence_ids = _label_and_evidence(raw, path)
    return VisualEdge(identifier, kind, source, target, label, evidence_ids)


def _parse_group(value: Any, index: int) -> VisualGroup:
    path = f"visual spec.groups[{index}]"
    raw = _closed(value, _GROUP_FIELDS, path, required=_GROUP_FIELDS)
    identifier = _id(raw["id"], f"{path}.id")
    label, evidence_ids = _label_and_evidence(raw, path, required=True)
    return VisualGroup(identifier, label, evidence_ids)


def _parse_lane(value: Any, index: int) -> VisualLane:
    path = f"visual spec.lanes[{index}]"
    raw = _closed(value, _LANE_FIELDS, path, required=_LANE_FIELDS)
    identifier = _id(raw["id"], f"{path}.id")
    label, evidence_ids = _label_and_evidence(raw, path, required=True)
    return VisualLane(identifier, label, evidence_ids)


def _parse_constraint(value: Any, index: int) -> VisualConstraint:
    path = f"visual spec.constraints[{index}]"
    raw = _closed(value, _CONSTRAINT_FIELDS, path, required=frozenset({"target"}))
    target = _id(raw["target"], f"{path}.target")
    hints: dict[str, int] = {}
    for name in ("order", "rank", "pin"):
        if name in raw:
            hints[name] = _validate_int(raw[name], f"{path}.{name}")
    if not hints:
        raise _fail("E_SCHEMA_VALUE", f"{path} must contain order, rank, or pin")
    return VisualConstraint(target, hints.get("order"), hints.get("rank"), hints.get("pin"))


def _parse_sequence(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        raise _fail("E_SCHEMA_TYPE", f"{path} must be an array")
    return value


def _validate_payload(payload: Mapping[str, Any], evidence_graph: Mapping[str, Any] | None) -> VisualSpec:
    _closed(dict(payload), _TOP_FIELDS, "visual spec")
    raw = payload
    if type(raw["schema_version"]) is not int or raw["schema_version"] != VISUAL_SPEC_SCHEMA_VERSION:
        raise _fail("E_SCHEMA_VERSION", "visual spec requires schema_version 1")
    intent = _parse_intent(raw["intent"])
    locale = parse_locale(raw["locale"], "visual spec.locale")

    variants_raw = _parse_sequence(raw["variants"], "visual spec.variants")
    if not variants_raw:
        raise _fail("E_SCHEMA_VALUE", "visual spec.variants must not be empty")
    variants = tuple(_text(item, "visual spec.variants[]") for item in variants_raw)
    if len(variants) != len(set(variants)) or not set(variants).issubset(VISUAL_VARIANTS):
        raise _fail("E_SCHEMA_VALUE", "visual spec.variants must be a unique desktop/mobile subset")
    if variants != tuple(sorted(variants, key=lambda item: item.encode("utf-8"))):
        raise _fail("E_SCHEMA_VALUE", "visual spec.variants must be byte-sorted")

    nodes = tuple(_parse_node(item, index) for index, item in enumerate(_parse_sequence(raw["nodes"], "visual spec.nodes")))
    edges = tuple(_parse_edge(item, index) for index, item in enumerate(_parse_sequence(raw["edges"], "visual spec.edges")))
    groups = tuple(_parse_group(item, index) for index, item in enumerate(_parse_sequence(raw["groups"], "visual spec.groups")))
    lanes = tuple(_parse_lane(item, index) for index, item in enumerate(_parse_sequence(raw["lanes"], "visual spec.lanes")))
    constraints = tuple(
        sorted(
            (
                _parse_constraint(item, index)
                for index, item in enumerate(_parse_sequence(raw["constraints"], "visual spec.constraints"))
            ),
            key=lambda item: (
                item.target.encode("utf-8"),
                item.order if item.order is not None else -1,
                item.rank if item.rank is not None else -1,
                item.pin if item.pin is not None else -1,
            ),
        )
    )

    _assert_collection_order(nodes, "visual spec.nodes")
    _assert_collection_order(edges, "visual spec.edges")
    _assert_collection_order(groups, "visual spec.groups")
    _assert_collection_order(lanes, "visual spec.lanes")

    declared_nodes = {item.id for item in nodes}
    declared_groups = {item.id for item in groups}
    declared_lanes = {item.id for item in lanes}
    declared_elements = declared_nodes | {item.id for item in edges} | declared_groups | declared_lanes
    if len(declared_elements) != len(nodes) + len(edges) + len(groups) + len(lanes):
        raise _fail("E_VISUAL_SPEC_ID", "visual spec element IDs must be globally unique")

    for edge in edges:
        if edge.source not in declared_nodes or edge.target not in declared_nodes:
            raise _fail("E_VISUAL_SPEC_EDGE", f"edge {edge.id} references an undeclared node")
    for node in nodes:
        if node.group_id is not None and node.group_id not in declared_groups:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared group")
        if node.lane_id is not None and node.lane_id not in declared_lanes:
            raise _fail("E_VISUAL_SPEC_EDGE", f"node {node.id} references an undeclared lane")
    for constraint in constraints:
        if constraint.target not in declared_elements:
            raise _fail("E_VISUAL_SPEC_EDGE", f"constraint target is undeclared: {constraint.target}")

    known_evidence: set[str] | None = None
    if evidence_graph is not None:
        graph = validate_evidence_graph(dict(evidence_graph))
        known_evidence = {fact["fact_id"] for fact in graph["facts"]}
    if known_evidence is not None:
        evidence_values: list[str] = list(intent.evidence_ids)
        for item in (*nodes, *edges, *groups, *lanes):
            evidence_values.extend(item.evidence_ids)
        if not set(evidence_values).issubset(known_evidence):
            raise _fail("E_VISUAL_SPEC_EVIDENCE", "visual spec references missing Evidence v2 IDs")

    spec = VisualSpec(
        VISUAL_SPEC_SCHEMA_VERSION,
        intent,
        locale,
        variants,
        nodes,
        edges,
        groups,
        lanes,
        constraints,
    )
    if len(spec.canonical_bytes()) > MAX_VISUAL_SPEC_BYTES:
        raise _fail("E_VISUAL_SPEC_SIZE", f"visual spec exceeds {MAX_VISUAL_SPEC_BYTES} canonical bytes")
    return spec


def validate_visual_spec(
    payload: Any,
    evidence_graph: Mapping[str, Any] | None = None,
) -> VisualSpec:
    """Validate and normalize a closed Visual Spec v1 value.

    Mapping input is copied before traversal.  A :class:`VisualSpec` value is
    projected back through the same closed boundary so public dataclass
    construction cannot bypass schema, Evidence, ordering, or size gates.
    """

    if isinstance(payload, VisualSpec):
        payload = payload.as_dict()
    _reject_floats(payload)
    if not isinstance(payload, Mapping):
        raise _fail("E_SCHEMA_TYPE", "visual spec must be an object")
    # Check the source envelope before normalization so an oversized payload
    # cannot be made small by dropping or truncating any caller data.
    _canonical_size(dict(payload))
    return _validate_payload(copy.deepcopy(dict(payload)), evidence_graph)


def canonical_visual_spec_bytes(payload: Any, evidence_graph: Mapping[str, Any] | None = None) -> bytes:
    spec = validate_visual_spec(payload, evidence_graph=evidence_graph)
    return spec.canonical_bytes()


def canonical_visual_spec_sha256(payload: Any, evidence_graph: Mapping[str, Any] | None = None) -> str:
    return canonical_sha256(validate_visual_spec(payload, evidence_graph=evidence_graph).as_dict())


__all__ = [
    "MAX_VISUAL_SPEC_BYTES",
    "VISUAL_EDGE_KINDS",
    "VISUAL_INTENT_KINDS",
    "VISUAL_NODE_KINDS",
    "VISUAL_SPEC_SCHEMA_VERSION",
    "VISUAL_VARIANTS",
    "VisualConstraint",
    "VisualEdge",
    "VisualGroup",
    "VisualIntent",
    "VisualLane",
    "VisualNode",
    "VisualSpec",
    "canonical_visual_spec_bytes",
    "canonical_visual_spec_sha256",
    "validate_visual_spec",
]
