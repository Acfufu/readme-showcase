"""Project Interaction v1 into deterministic, inert preview data.

The projection is intentionally data-only.  It does not render HTML, emit
script, or read from the filesystem; callers can decide how to place the
canonical JSON into a later preview surface.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import escape
from types import MappingProxyType
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from ..contracts.evidence import validate_evidence_graph
from ..evidence.graph import EvidenceGraph
from ..visual_kernel.interaction import InteractionGraph


INTERACTION_PREVIEW_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _id_key(value: str) -> bytes:
    return value.encode("utf-8")


def _escape_text(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be text")
    # quote=True also protects attribute-like consumers of the data.  The
    # value remains a string in JSON; it is never interpreted as markup here.
    return escape(value, quote=True)


def _tuple_map(value: Any, context: str) -> dict[str, tuple[str, ...]]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    result: dict[str, tuple[str, ...]] = {}
    for identifier, targets in value.items():
        if not isinstance(identifier, str) or not isinstance(targets, (list, tuple)):
            raise _fail("E_SCHEMA_TYPE", f"{context} must map IDs to arrays")
        result[identifier] = tuple(targets)
    return result


def _normalize_interaction(value: Any) -> InteractionGraph:
    if isinstance(value, InteractionGraph):
        return value
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "interaction must be an InteractionGraph or JSON object")
    required = {
        "schema_version",
        "focus_order",
        "evidence_links",
        "adjacency",
        "group_navigation",
        "lane_navigation",
    }
    unknown = sorted(set(value) - required)
    if unknown:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"interaction contains unknown field: {unknown[0]}")
    missing = sorted(required - set(value))
    if missing:
        raise _fail("E_SCHEMA_MISSING_FIELD", f"interaction is missing field: {missing[0]}")
    if type(value["schema_version"]) is not int or value["schema_version"] != InteractionGraph.schema_version:
        raise _fail("E_SCHEMA_VERSION", "interaction requires schema_version 1")
    focus_order = value["focus_order"]
    if not isinstance(focus_order, (list, tuple)):
        raise _fail("E_SCHEMA_TYPE", "interaction.focus_order must be an array")
    return InteractionGraph(
        tuple(focus_order),
        _tuple_map(value["evidence_links"], "interaction.evidence_links"),
        _tuple_map(value["adjacency"], "interaction.adjacency"),
        _tuple_map(value["group_navigation"], "interaction.group_navigation"),
        _tuple_map(value["lane_navigation"], "interaction.lane_navigation"),
    )


def _normalize_evidence(value: EvidenceGraph | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(value, EvidenceGraph):
        value = value.to_dict()
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "evidence_graph must be an EvidenceGraph or JSON object")
    # The validator requires a concrete dict and returns a detached canonical
    # copy, so no caller-owned evidence object can be mutated by projection.
    return validate_evidence_graph(dict(value))


def _freeze_record(record: Mapping[str, Any]) -> Mapping[str, Any]:
    frozen: dict[str, Any] = {}
    for key, value in record.items():
        if isinstance(value, list):
            frozen[key] = tuple(value)
        else:
            frozen[key] = value
    return MappingProxyType(frozen)


def _plain_records(records: tuple[Mapping[str, Any], ...]) -> list[dict[str, Any]]:
    return [
        {
            key: list(value) if isinstance(value, tuple) else value
            for key, value in record.items()
        }
        for record in records
    ]


@dataclass(frozen=True, slots=True)
class InteractionPreview:
    """Immutable focus/evidence/adjacency data for a static preview.

    Public records use tuples and read-only mapping proxies internally.  The
    JSON-facing ``as_dict`` method returns detached lists/dicts suitable for
    canonical serialization.
    """

    schema_version: int
    interaction_sha256: str
    focus: tuple[Mapping[str, Any], ...]
    evidence: tuple[Mapping[str, Any], ...]
    adjacency: tuple[Mapping[str, Any], ...]
    fallback_order: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "interaction_sha256": self.interaction_sha256,
            "focus": _plain_records(self.focus),
            "evidence": _plain_records(self.evidence),
            "adjacency": _plain_records(self.adjacency),
            "fallback_order": list(self.fallback_order),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def _element_digest(
    element_id: str,
    label: str,
    evidence_ids: tuple[str, ...],
) -> str:
    return canonical_sha256(
        {
            "element_id": element_id,
            "label": label,
            "evidence_ids": list(evidence_ids),
        }
    )


def _validate_bindings(
    interaction: InteractionGraph,
    focus_records: tuple[Mapping[str, Any], ...],
    *,
    element_hashes: Mapping[str, str] | None,
    expected_interaction_sha256: str | None,
) -> None:
    if element_hashes is not None:
        if not isinstance(element_hashes, Mapping):
            raise _fail("E_SCHEMA_TYPE", "element_hashes must be an object")
        expected_ids = set(interaction.focus_order)
        provided_ids = set(element_hashes)
        if provided_ids != expected_ids:
            raise _fail("E_VISUAL_FINGERPRINT", "element_hashes must cover exactly every focus target")
        for record in focus_records:
            identifier = record["element_id"]
            digest = element_hashes[identifier]
            if not isinstance(digest, str) or _SHA256.fullmatch(digest) is None:
                raise _fail("E_VISUAL_FINGERPRINT", f"element hash is invalid: {identifier}")
            if digest != record["element_sha256"]:
                raise _fail("E_VISUAL_FINGERPRINT", f"element hash is stale: {identifier}")

    interaction_sha256 = interaction.sha256()
    if expected_interaction_sha256 is not None:
        if (
            not isinstance(expected_interaction_sha256, str)
            or _SHA256.fullmatch(expected_interaction_sha256) is None
            or expected_interaction_sha256 != interaction_sha256
        ):
            raise _fail("E_VISUAL_FINGERPRINT", "interaction hash is stale")


def project_interaction_preview(
    interaction: InteractionGraph | Mapping[str, Any],
    evidence_graph: EvidenceGraph | Mapping[str, Any],
    *,
    labels: Mapping[str, str],
    element_hashes: Mapping[str, str] | None = None,
    expected_interaction_sha256: str | None = None,
) -> InteractionPreview:
    """Return an escaped, canonical, script-free Interaction v1 projection.

    ``labels`` is deliberately required: a focus target ID is not silently
    promoted to user-visible copy.  All focusable IDs are retained in the
    graph's authoritative order for keyboard and no-script fallback use.
    """

    graph = _normalize_interaction(interaction)
    validated_evidence = _normalize_evidence(evidence_graph)
    if not isinstance(labels, Mapping):
        raise _fail("E_SCHEMA_TYPE", "labels must be an object")

    for identifier, label in labels.items():
        if not isinstance(identifier, str) or not isinstance(label, str):
            raise _fail("E_SCHEMA_TYPE", "labels must map string IDs to text")
    focus_ids = set(graph.focus_order)
    extra_labels = set(labels) - focus_ids
    if extra_labels:
        identifier = sorted(extra_labels, key=_id_key)[0]
        raise _fail("E_VISUAL_SPEC_ID", f"labels references undeclared focus target: {identifier}")
    facts = {fact["fact_id"]: fact for fact in validated_evidence["facts"]}
    referenced_ids = {
        evidence_id
        for evidence_ids in graph.evidence_links.values()
        for evidence_id in evidence_ids
    }
    unknown_ids = sorted(referenced_ids - set(facts), key=_id_key)
    if unknown_ids:
        raise _fail("E_VISUAL_SPEC_EVIDENCE", f"interaction references unknown Evidence ID: {unknown_ids[0]}")

    focus_records: list[Mapping[str, Any]] = []
    for identifier in graph.focus_order:
        if identifier not in labels:
            raise _fail("E_SCHEMA_MISSING_FIELD", f"labels is missing focus target: {identifier}")
        escaped_label = _escape_text(labels[identifier], f"labels.{identifier}")
        evidence_ids = graph.evidence_links[identifier]
        focus_records.append(
            _freeze_record(
                {
                    "element_id": identifier,
                    "label": escaped_label,
                    "evidence_ids": evidence_ids,
                    "element_sha256": _element_digest(identifier, escaped_label, evidence_ids),
                }
            )
        )

    evidence_records = tuple(
        _freeze_record(
            {
                "evidence_id": fact_id,
                "kind": _escape_text(fact["kind"], f"evidence.{fact_id}.kind"),
                "source_path": _escape_text(
                    fact["source"]["path"], f"evidence.{fact_id}.source.path"
                ),
                "semantic_key": _escape_text(
                    fact["semantic_key"], f"evidence.{fact_id}.semantic_key"
                ),
            }
        )
        for fact_id in sorted(referenced_ids, key=_id_key)
        for fact in (facts[fact_id],)
    )

    adjacency_records = tuple(
        _freeze_record(
            {
                "element_id": identifier,
                "neighbors": graph.adjacency[identifier],
            }
        )
        for identifier in sorted(graph.adjacency, key=_id_key)
    )
    focus_tuple = tuple(focus_records)
    _validate_bindings(
        graph,
        focus_tuple,
        element_hashes=element_hashes,
        expected_interaction_sha256=expected_interaction_sha256,
    )
    return InteractionPreview(
        schema_version=INTERACTION_PREVIEW_SCHEMA_VERSION,
        interaction_sha256=graph.sha256(),
        focus=focus_tuple,
        evidence=evidence_records,
        adjacency=adjacency_records,
        fallback_order=graph.focus_order,
    )


__all__ = ["InteractionPreview", "project_interaction_preview"]
