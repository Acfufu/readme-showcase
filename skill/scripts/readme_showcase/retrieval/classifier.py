"""Deterministic, evidence-only project classification."""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from ..contracts.common import ContractError, normalize_posix_path
from ..contracts.evidence import validate_evidence_graph, validate_fact


PROJECT_TYPES = (
    "cli",
    "sdk",
    "library",
    "api-service",
    "web-app",
    "mobile-app",
    "desktop-app",
    "github-action",
    "monorepo",
    "ml-model",
    "dataset",
    "infrastructure",
    "plugin",
    "template",
    "runtime-toolchain",
    "developer-tool",
    "web-framework",
)
ALL_PROJECT_TYPES = PROJECT_TYPES + ("unknown",)
MIN_CONFIDENCE_BASIS_POINTS = 6000
_TYPE_ORDER = {project_type: position for position, project_type in enumerate(PROJECT_TYPES)}
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_INDEX_FIELDS = frozenset({"bytes", "language", "path", "role", "selected_for_content", "sha256", "tracked"})


def _facts(evidence: Mapping[str, Any] | Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if isinstance(evidence, Mapping):
        return validate_evidence_graph(dict(evidence))["facts"]
    if isinstance(evidence, (str, bytes)):
        raise ContractError("E_SCHEMA_TYPE", "evidence facts must be typed objects")
    try:
        raw_facts = list(evidence)
    except TypeError as exc:
        raise ContractError("E_SCHEMA_TYPE", "evidence facts must be iterable") from exc
    facts: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for raw in raw_facts:
        validated = validate_fact(dict(raw)) if isinstance(raw, Mapping) else validate_fact(raw)
        fact_id = validated["fact_id"]
        if fact_id in identifiers:
            raise ContractError("E_FACT_DUPLICATE", "classifier evidence contains a duplicate or colliding fact_id")
        identifiers.add(fact_id)
        facts.append(validated)
    return sorted(facts, key=lambda fact: fact["fact_id"])


def _validate_index(index: Iterable[Mapping[str, Any]]) -> None:
    if isinstance(index, (str, bytes, Mapping)):
        raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index must be an iterable of strict records")
    try:
        records = list(index)
    except TypeError as exc:
        raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index must be iterable") from exc
    paths: set[str] = set()
    for record in records:
        if not isinstance(record, Mapping) or set(record) != _INDEX_FIELDS:
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index record fields are invalid")
        path = normalize_posix_path(record["path"])
        if path in paths:
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index contains a duplicate path")
        paths.add(path)
        if type(record["bytes"]) is not int or record["bytes"] < 0:
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index bytes must be a non-negative integer")
        if record["language"] is not None and (not isinstance(record["language"], str) or not record["language"]):
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index language is invalid")
        if not isinstance(record["role"], str) or not record["role"]:
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index role is invalid")
        if type(record["selected_for_content"]) is not bool or type(record["tracked"]) is not bool or not record["tracked"]:
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index flags are invalid")
        digest = record["sha256"]
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ContractError("E_CLASSIFIER_INDEX", "tracked-file index sha256 is invalid")


def _signal(fact: Mapping[str, Any]) -> tuple[str, int, str, str, bool] | None:
    """Return type, score, reason code/message, and explicit-type marker; never inspect fact values."""
    kind, key = fact["kind"], fact["semantic_key"]
    if fact["confidence"] != "observed":
        return None
    if kind == "config-value" and key.startswith("project-type:"):
        project_type = key.removeprefix("project-type:")
        if project_type in _TYPE_ORDER:
            return project_type, 9000, "classifier.explicit-type", "observed explicit project-type signal", True
    if kind == "cli-entrypoint":
        return "cli", 9200, "classifier.cli-entrypoint", "observed CLI entrypoint", False
    if kind == "config-value" and key in {"node-script:start", "node-script:dev"}:
        score = 9000 if key.endswith(":start") else 8000
        return "web-app", score, "classifier.web-app-script", "observed web application start signal", False
    if kind == "test-observation" and key == "test-framework":
        return "developer-tool", 5900, "classifier.test-framework", "observed test framework signal", False
    return None


def classify_project(
    evidence: Mapping[str, Any] | Iterable[Mapping[str, Any]],
    index: Iterable[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Classify validated Evidence v2 facts plus tracked-file metadata.

    Values, repository identity, source paths, and benchmark fields never take part.
    """
    facts = _facts(evidence)
    _validate_index(index)
    best: dict[str, tuple[int, str, str, str]] = {}
    explicit: dict[str, list[str]] = {}
    for fact in facts:
        signal = _signal(fact)
        if signal is None:
            continue
        project_type, score, code, message, is_explicit = signal
        fact_id = fact["fact_id"]
        if is_explicit:
            explicit.setdefault(project_type, []).append(fact_id)
        candidate = (score, fact_id, code, message)
        existing = best.get(project_type)
        if existing is None or candidate[:2] > (existing[0], existing[1]):
            best[project_type] = candidate
    explicit_types = sorted(explicit, key=_TYPE_ORDER.__getitem__)
    if len(explicit_types) > 1:
        evidence_ids = sorted(fact_id for values in explicit.values() for fact_id in values)
        return {
            "primary": "unknown",
            "secondary": [],
            "confidence_basis_points": 0,
            "reasons": [{
                "code": "classifier.conflicting-explicit-types",
                "message": "conflicting explicit project-type signals",
                "evidence_ids": evidence_ids,
            }],
        }
    ranked = sorted(best, key=lambda project_type: (-best[project_type][0], _TYPE_ORDER[project_type]))
    if not ranked or best[ranked[0]][0] < MIN_CONFIDENCE_BASIS_POINTS:
        evidence_ids = [best[ranked[0]][1]] if ranked else []
        reasons = ([{
            "code": "classifier.below-threshold",
            "message": "observed signals are below the classification threshold",
            "evidence_ids": evidence_ids,
        }] if evidence_ids else [])
        return {
            "primary": "unknown",
            "secondary": [],
            "confidence_basis_points": best[ranked[0]][0] if ranked else 0,
            "reasons": reasons,
        }
    primary = ranked[0]
    secondary = [project_type for project_type in ranked[1:] if best[project_type][0] >= MIN_CONFIDENCE_BASIS_POINTS]
    reasons = [
        {
            "code": best[project_type][2],
            "message": best[project_type][3],
            "evidence_ids": [best[project_type][1]],
        }
        for project_type in [primary, *secondary]
    ]
    return {
        "primary": primary,
        "secondary": secondary,
        "confidence_basis_points": best[primary][0],
        "reasons": reasons,
    }


classify = classify_project

__all__ = [
    "ALL_PROJECT_TYPES",
    "MIN_CONFIDENCE_BASIS_POINTS",
    "PROJECT_TYPES",
    "classify",
    "classify_project",
]
