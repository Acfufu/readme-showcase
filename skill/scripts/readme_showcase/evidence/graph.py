from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping
from typing import Any

from ..contracts.common import ContractError
from ..contracts.evidence import (
    EVIDENCE_SCHEMA_VERSION,
    MAX_FACTS,
    compute_graph_sha256,
    validate_evidence_graph,
    validate_fact,
)


class EvidenceGraph:
    def __init__(self, facts: Iterable[Mapping[str, Any]] = ()) -> None:
        self._facts: dict[str, dict[str, Any]] = {}
        for fact in facts:
            self.add(fact)

    def add(self, fact: Mapping[str, Any], *, source_bytes: bytes | None = None) -> dict[str, Any]:
        raw_id = fact.get("fact_id")
        if isinstance(raw_id, str) and raw_id in self._facts:
            raise ContractError("E_FACT_DUPLICATE", "duplicate or colliding fact_id")
        validated = validate_fact(dict(fact), source_bytes=source_bytes)
        fact_id = validated["fact_id"]
        if len(self._facts) >= MAX_FACTS:
            raise ContractError("E_EVIDENCE_LIMIT", f"repository evidence may contain at most {MAX_FACTS} facts")
        self._facts[fact_id] = validated
        return copy.deepcopy(validated)

    @property
    def facts(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._facts[key]) for key in sorted(self._facts))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": EVIDENCE_SCHEMA_VERSION,
            "facts": list(self.facts),
        }
        payload["evidence_sha256"] = compute_graph_sha256(payload)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "EvidenceGraph":
        validated = validate_evidence_graph(dict(payload))
        return cls(validated["facts"])


def build_graph(facts: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    return EvidenceGraph(facts).to_dict()
