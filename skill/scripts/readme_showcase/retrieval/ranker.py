"""Pure deterministic hybrid BM25/MMR ranker."""

from __future__ import annotations

import re
import unicodedata
from fractions import Fraction
from typing import Any, Mapping, Sequence

from ..contracts.common import ContractError
from .metrics import K, LAMBDA, basis_points, bm25_scores, jaccard, overlap


_TOKEN = re.compile(r"[a-z0-9_]+|[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]+", re.IGNORECASE)
_SIGNALS = (
    "project_type_basis_points",
    "section_overlap_basis_points",
    "tag_overlap_basis_points",
    "manifest_feature_overlap_basis_points",
    "bm25_basis_points",
    "diversity_penalty_basis_points",
)


def tokenize(value: str) -> tuple[str, ...]:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    tokens: list[str] = []
    for match in _TOKEN.finditer(normalized):
        token = match.group()
        if "\u3400" <= token[0] <= "\ufaff":
            tokens.extend(token[index:index + 2] for index in range(max(1, len(token) - 1)))
        else:
            tokens.append(token)
    return tuple(tokens)


def _text(record: Mapping[str, Any]) -> str:
    pattern = record["pattern"]
    return " ".join([
        *record["project_types"], *record["section_intents"], *record["tags"],
        pattern["summary"], pattern["structure"], pattern["proof"],
    ])


def _reason(code: str, signal: str, values: Sequence[str]) -> dict[str, object]:
    return {"code": code, "signal": signal, "matched_values": sorted(set(values))}


def rank_records(records: Sequence[Mapping[str, Any]], query: Mapping[str, Any], *, k: int = K) -> list[dict[str, object]]:
    if type(k) is not int or not 1 <= k <= K:
        raise ContractError("E_RETRIEVAL_QUERY", f"k must be between 1 and {K}")
    identifiers = [record.get("record_id") for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ContractError("E_DATASET_DUPLICATE_ID", "retrieval records contain duplicate record_id")
    ordered = sorted(records, key=lambda record: record["record_id"])
    documents = [tokenize(_text(record)) for record in ordered]
    feature_tokens = tuple(token for value in query["manifest_features"] for token in tokenize(value))
    query_tokens = tokenize(" ".join([
        query["project_type"], *query["sections"], *query["tags"], *query["manifest_features"],
    ]))
    lexical = bm25_scores(query_tokens, documents)
    lexical_max = max(lexical, default=Fraction(0))
    candidates: list[dict[str, Any]] = []
    for record, tokens, lexical_score in zip(ordered, documents, lexical, strict=True):
        project_match = Fraction(int(query["project_type"] in record["project_types"]))
        section_match = overlap(query["sections"], record["section_intents"])
        tag_match = overlap(query["tags"], record["tags"])
        manifest_match = overlap(feature_tokens, tokens)
        normalized_bm25 = lexical_score / lexical_max if lexical_max else Fraction(0)
        relevance = (
            Fraction(30, 100) * project_match
            + Fraction(20, 100) * section_match
            + Fraction(15, 100) * tag_match
            + Fraction(10, 100) * manifest_match
            + Fraction(25, 100) * normalized_bm25
        )
        if not relevance:
            continue
        matched_project = [query["project_type"]] if project_match else []
        matched_sections = sorted(set(query["sections"]) & set(record["section_intents"]))
        matched_tags = sorted(set(query["tags"]) & set(record["tags"]))
        matched_features = sorted(set(feature_tokens) & set(tokens))
        reasons = []
        for code, signal, values in (
            ("retrieval.project-type", "project_type_basis_points", matched_project),
            ("retrieval.section-intent", "section_overlap_basis_points", matched_sections),
            ("retrieval.tag", "tag_overlap_basis_points", matched_tags),
            ("retrieval.manifest-feature", "manifest_feature_overlap_basis_points", matched_features),
            ("retrieval.bm25", "bm25_basis_points", sorted(set(query_tokens) & set(tokens))),
        ):
            if values:
                reasons.append(_reason(code, signal, values))
        candidates.append({
            "record": record,
            "tokens": tokens,
            "relevance": relevance,
            "signals": {
                "project_type_basis_points": basis_points(project_match),
                "section_overlap_basis_points": basis_points(section_match),
                "tag_overlap_basis_points": basis_points(tag_match),
                "manifest_feature_overlap_basis_points": basis_points(manifest_match),
                "bm25_basis_points": basis_points(normalized_bm25),
                "diversity_penalty_basis_points": 0,
            },
            "reasons": reasons,
        })

    selected: list[dict[str, Any]] = []
    while candidates and len(selected) < k:
        for candidate in candidates:
            similarity = max((jaccard(candidate["tokens"], item["tokens"]) for item in selected), default=Fraction(0))
            candidate["similarity"] = similarity
            candidate["mmr"] = LAMBDA * candidate["relevance"] - (1 - LAMBDA) * similarity
        candidate = min(candidates, key=lambda item: (-item["mmr"], item["record"]["record_id"]))
        candidates.remove(candidate)
        selected.append(candidate)

    output: list[dict[str, object]] = []
    for candidate in selected:
        record = candidate["record"]
        signals = candidate["signals"]
        signals["diversity_penalty_basis_points"] = basis_points((1 - LAMBDA) * candidate["similarity"])
        output.append({
            "record_id": record["record_id"],
            "score_basis_points": max(0, basis_points(candidate["mmr"])),
            "signals": signals,
            "reasons": candidate["reasons"],
            "project_types": list(record["project_types"]),
            "section_intents": list(record["section_intents"]),
            "tags": list(record["tags"]),
            "pattern": dict(record["pattern"]),
            "source": dict(record["source"]),
            "source_split": record["split"],
        })
    return output


__all__ = ["rank_records", "tokenize"]
