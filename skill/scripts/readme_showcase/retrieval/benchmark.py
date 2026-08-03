"""Deterministic, offline, read-only retrieval benchmark machinery."""
# noqa: SIZE_OK - task ownership keeps benchmark validation and scoring in one module.

from __future__ import annotations

import copy
import re
from fractions import Fraction
from typing import Any, Final, Mapping, NoReturn, Sequence

from ..contracts.common import ContractError, canonical_sha256
from ..contracts.retrieval import validate_retrieval_query
from .metrics import basis_points
from .service import retrieve_patterns_v2, validate_dataset_manifest


METRIC_NAMES = (
    "project_type_accuracy",
    "recall_at_5",
    "mrr",
    "ndcg_at_5",
    "section_intent_coverage",
    "pattern_diversity",
)
NDCG_DISCOUNTS_BASIS_POINTS = (10_000, 6_309, 5_000, 4_307, 3_869)
THRESHOLD_MARGIN_BASIS_POINTS = 200
MAX_METRIC_COMPONENT = (1 << 63) - 1

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_GIT_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SLUG = re.compile(r"[a-z0-9][a-z0-9._-]{0,127}\Z")
_REVIEWED_AT = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_QUERY_SET_FIELDS = {"schema_version", "status", "proposal_origin", "dataset", "gold_set_sha256", "queries"}
_DATASET_FIELDS = {"dataset_id", "dataset_revision", "manifest_file_sha256", "manifest_canonical_sha256"}
_QUERY_ITEM_FIELDS = {
    "query_id", "source_identity", "project_type", "section_intents", "query",
    "expected_relevant_ids", "expected_relevant_source_identities", "gold_sha256", "review",
}
_IDENTITY_FIELDS = {"record_id", "repository_url", "commit", "material_sha256", "split"}
_REVIEW_FIELDS = {
    "approval_artifact_sha256", "human_reviewed", "receipt_sha256", "review_method",
    "review_packet_sha256", "reviewed_at", "reviewer_id", "source_commit",
}
_METRIC_FIELDS = {"numerator", "denominator", "value_basis_points"}
_BASELINE_METRIC_FIELDS = _METRIC_FIELDS | {"threshold_basis_points"}
_BASELINE_FIELDS = {"schema_version", "status", "dataset", "gold_set_sha256", "review_receipt_sha256", "metrics"}
# Exact user-approved receipt and artifact bindings; callers cannot extend trust.
_REVIEW_RECEIPT_ALLOWLIST: Final[frozenset[str]] = frozenset({
    "b9a89e065c9736424b71cdea6f37c2c3816b84ad05050a7c665da90358bf3ee7",
})
_APPROVED_REVIEW_BINDING: Final[tuple[str, str, str, str, str, str]] = (
    "acfufu",
    "2026-08-03T07:20:56Z",
    "b9a89e065c9736424b71cdea6f37c2c3816b84ad05050a7c665da90358bf3ee7",
    "fe92456c6dbde1b1a53d1541a8b4f9e12fa3fa513ffecffc6611fda3f69a8310",
    "96c16fa820e2ad4fa3e5f3b114f4c1d0dd596b6bee342df6f531b41145d7f37b",
    "8b1fbe257e25ceafd6541b8f23341fbbe6253180",
)


def _fail(code: str, message: str) -> NoReturn:
    raise ContractError(code, message)


def _reject_floats(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        _fail("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_floats(child, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, child in value.items():
            if not isinstance(key, str):
                _fail("E_SCHEMA_KEY_TYPE", f"{path} contains a non-string key")
            _reject_floats(child, f"{path}.{key}")


def _object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    missing, unknown = sorted(fields - set(value)), sorted(set(value) - fields)
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing required field: {missing[0]}")
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    return value


def _slug_list(value: Any, context: str) -> list[str]:
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or not _SLUG.fullmatch(item) for item in value)
        or value != sorted(set(value))
    ):
        _fail("E_BENCHMARK_QUERY", f"{context} must be a sorted unique slug list")
    return value


def _identity(record: Mapping[str, Any]) -> dict[str, str]:
    source = record["source"]
    return {
        "record_id": record["record_id"],
        "repository_url": source["repository_url"],
        "commit": source["commit"],
        "material_sha256": source["material_sha256"],
        "split": record["split"],
    }


def _validate_identity(value: Any, expected: Mapping[str, Any], context: str) -> dict[str, Any]:
    identity = _object(value, _IDENTITY_FIELDS, context)
    if identity != _identity(expected):
        code = (
            "E_DATASET_SPLIT_LEAK"
            if expected["split"] != "train" or identity.get("split") != "train"
            else "E_BENCHMARK_SOURCE_IDENTITY"
        )
        _fail(code, f"{context} does not match immutable manifest source identity")
    if identity["split"] != "train":
        _fail("E_DATASET_SPLIT_LEAK", f"{context} is not train split")
    return identity


def _gold_payload(item: Mapping[str, Any]) -> dict[str, Any]:
    return {
        key: copy.deepcopy(item[key])
        for key in (
            "query_id", "source_identity", "project_type", "section_intents", "query",
            "expected_relevant_ids", "expected_relevant_source_identities",
        )
    }


def _validated_query_items(manifest: Any, payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    _reject_floats(payload)
    manifest_result = validate_dataset_manifest(manifest)
    query_set = _object(payload, _QUERY_SET_FIELDS, "benchmark query set")
    if query_set["schema_version"] != 1 or query_set["status"] not in {"pending-human-review", "reviewed"}:
        _fail("E_BENCHMARK_QUERY", "benchmark query set schema/status is invalid")
    if query_set["proposal_origin"] not in {"codex-unreviewed", "human-reviewed"}:
        _fail("E_BENCHMARK_QUERY", "benchmark query set proposal_origin is invalid")
    dataset = _object(query_set["dataset"], _DATASET_FIELDS, "benchmark query set dataset")
    if (
        dataset["dataset_id"] != manifest["dataset_id"]
        or dataset["dataset_revision"] != manifest["dataset_revision"]
        or dataset["manifest_canonical_sha256"] != manifest_result["manifest_sha256"]
        or not isinstance(dataset["manifest_file_sha256"], str)
        or not _SHA256.fullmatch(dataset["manifest_file_sha256"])
    ):
        _fail("E_BENCHMARK_MANIFEST_HASH", "benchmark query set does not bind current manifest")
    records = {record["record_id"]: record for record in manifest["records"]}
    raw_items = query_set["queries"]
    if not isinstance(raw_items, list) or not raw_items:
        _fail("E_BENCHMARK_DENOMINATOR", "benchmark requires at least one query")
    items: list[dict[str, Any]] = []
    identifiers: set[str] = set()
    for index, raw in enumerate(raw_items):
        context = f"benchmark query set queries[{index}]"
        item = _object(raw, _QUERY_ITEM_FIELDS, context)
        query_id = item["query_id"]
        if not isinstance(query_id, str) or not _SLUG.fullmatch(query_id) or query_id in identifiers:
            _fail("E_BENCHMARK_QUERY", f"{context}.query_id is invalid or duplicate")
        identifiers.add(query_id)
        if query_id not in records:
            _fail("E_BENCHMARK_UNKNOWN_RECORD", f"{context}.query_id is absent from manifest")
        source_record = records[query_id]
        _validate_identity(item["source_identity"], source_record, f"{context}.source_identity")
        if item["project_type"] not in source_record["project_types"]:
            _fail("E_BENCHMARK_QUERY", f"{context}.project_type is not source-bound")
        section_intents = _slug_list(item["section_intents"], f"{context}.section_intents")
        if not set(section_intents) <= set(source_record["section_intents"]):
            _fail("E_BENCHMARK_QUERY", f"{context}.section_intents are not source-bound")
        query = validate_retrieval_query(item["query"])
        if (
            query["project_type"] != item["project_type"]
            or query["sections"] != section_intents
            or query["evidence_sha256"] != source_record["source"]["material_sha256"]
        ):
            _fail("E_BENCHMARK_QUERY", f"{context}.query does not match source/type/intent")
        relevant = _slug_list(item["expected_relevant_ids"], f"{context}.expected_relevant_ids")
        identities = item["expected_relevant_source_identities"]
        if not isinstance(identities, list) or len(identities) != len(relevant):
            _fail("E_BENCHMARK_SOURCE_IDENTITY", f"{context} relevant identities are incomplete")
        for record_id, identity in zip(relevant, identities, strict=True):
            if record_id not in records:
                _fail("E_BENCHMARK_UNKNOWN_RECORD", f"{context} relevant record is absent: {record_id}")
            _validate_identity(identity, records[record_id], f"{context}.expected_relevant_source_identities")
            if identity["record_id"] != record_id:
                _fail("E_BENCHMARK_SOURCE_IDENTITY", f"{context} relevant identity order disagrees")
        if item["gold_sha256"] != canonical_sha256(_gold_payload(item)):
            _fail("E_BENCHMARK_GOLD_HASH", f"{context}.gold_sha256 does not bind query/gold fields")
        _object(item["review"], _REVIEW_FIELDS, f"{context}.review")
        items.append(copy.deepcopy(item))
    items.sort(key=lambda item: item["query_id"])
    if query_set["gold_set_sha256"] != canonical_sha256([item["gold_sha256"] for item in items]):
        _fail("E_BENCHMARK_GOLD_HASH", "gold_set_sha256 does not bind canonical query/gold order")
    return copy.deepcopy(query_set), items


def _expected_receipt(query_set: Mapping[str, Any], review: Mapping[str, Any]) -> str:
    return canonical_sha256({
        "schema_version": 1,
        "kind": "readme-showcase-retrieval-gold-review",
        "review_method": review["review_method"],
        "reviewer_id": review["reviewer_id"],
        "reviewed_at": review["reviewed_at"],
        "dataset": query_set["dataset"],
        "gold_set_sha256": query_set["gold_set_sha256"],
    })


def validate_reviewed_query_set(
    manifest: Any,
    payload: Any,
) -> dict[str, Any]:
    query_set, items = _validated_query_items(manifest, payload)
    if query_set["status"] != "reviewed" or query_set["proposal_origin"] != "human-reviewed":
        _fail("E_BENCHMARK_REVIEW_REQUIRED", "benchmark gold remains an unapproved proposal")
    receipts: set[str] = set()
    for item in items:
        review = item["review"]
        method = review["review_method"]
        reviewer = review["reviewer_id"]
        if method == "generated" or (isinstance(reviewer, str) and reviewer.casefold().startswith("agent:")):
            _fail("E_BENCHMARK_GENERATED_GOLD", "generated gold/review receipts are forbidden")
        if (
            review["human_reviewed"] is not True
            or method != "independent-human"
            or not isinstance(reviewer, str)
            or not reviewer.strip()
            or reviewer.casefold() in {"codex", "self", "agent"}
            or not isinstance(review["reviewed_at"], str)
            or not _REVIEWED_AT.fullmatch(review["reviewed_at"])
            or not isinstance(review["receipt_sha256"], str)
            or not _SHA256.fullmatch(review["receipt_sha256"])
            or not isinstance(review["review_packet_sha256"], str)
            or not _SHA256.fullmatch(review["review_packet_sha256"])
            or not isinstance(review["approval_artifact_sha256"], str)
            or not _SHA256.fullmatch(review["approval_artifact_sha256"])
            or not isinstance(review["source_commit"], str)
            or not _GIT_SHA1.fullmatch(review["source_commit"])
        ):
            _fail("E_BENCHMARK_REVIEW_REQUIRED", "independent human review receipt is missing or self-attested")
        if review["receipt_sha256"] != _expected_receipt(query_set, review):
            _fail("E_BENCHMARK_REVIEW_RECEIPT", "review receipt does not bind dataset/query/gold hashes")
        binding = (
            reviewer,
            review["reviewed_at"],
            review["receipt_sha256"],
            review["review_packet_sha256"],
            review["approval_artifact_sha256"],
            review["source_commit"],
        )
        if binding != _APPROVED_REVIEW_BINDING:
            _fail("E_BENCHMARK_REVIEW_RECEIPT", "review receipt is not bound to the approved source artifact")
        receipts.add(review["receipt_sha256"])
    if len(receipts) != 1 or receipts.isdisjoint(_REVIEW_RECEIPT_ALLOWLIST):
        _fail("E_BENCHMARK_REVIEW_RECEIPT", "review receipt lacks an independent trust anchor")
    approved = copy.deepcopy(query_set)
    approved["queries"] = items
    return approved


def validate_production_rankings(
    manifest: Any,
    rankings: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, list[dict[str, Any]]]:
    validate_dataset_manifest(manifest)
    if not isinstance(rankings, Mapping):
        _fail("E_BENCHMARK_RANKING", "rankings must be an object")
    manifest_records = {record["record_id"]: record for record in manifest["records"]}
    manifest_sources = {
        (record["source"]["repository_url"], record["source"]["commit"], record["source"]["material_sha256"]): record
        for record in manifest["records"]
    }
    authoritative: dict[str, list[dict[str, Any]]] = {}
    for query_id, records in rankings.items():
        if not isinstance(query_id, str) or not isinstance(records, Sequence) or isinstance(records, (str, bytes)):
            _fail("E_BENCHMARK_RANKING", "ranking entry is malformed")
        seen: set[str] = set()
        authoritative_records: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, Mapping):
                _fail("E_BENCHMARK_RANKING", f"ranking {query_id} contains a malformed result")
            record_id = record.get("record_id")
            if not isinstance(record_id, str) or record_id in seen:
                _fail("E_DATASET_DUPLICATE_ID", f"ranking {query_id} contains duplicate/invalid record_id")
            seen.add(record_id)
            manifest_record = manifest_records.get(record_id)
            if manifest_record is None:
                _fail("E_BENCHMARK_UNKNOWN_RECORD", f"ranking {query_id} contains unknown record_id: {record_id}")
            if manifest_record["split"] != "train" or record.get("source_split") != "train":
                _fail("E_DATASET_SPLIT_LEAK", f"production ranking {query_id} contains held-out/test identity")
            provided_source = record.get("source")
            if provided_source is not None:
                if not isinstance(provided_source, Mapping):
                    _fail("E_BENCHMARK_SOURCE_IDENTITY", f"ranking {query_id} contains malformed source identity")
                source_identity = (
                    provided_source.get("repository_url"),
                    provided_source.get("commit"),
                    provided_source.get("material_sha256"),
                )
                if any(not isinstance(value, str) for value in source_identity):
                    _fail("E_BENCHMARK_SOURCE_IDENTITY", f"ranking {query_id} contains malformed source identity")
                expected_source = manifest_record["source"]
                expected_identity = (
                    expected_source["repository_url"],
                    expected_source["commit"],
                    expected_source["material_sha256"],
                )
                if source_identity != expected_identity:
                    source_record = manifest_sources.get(source_identity)
                    code = "E_DATASET_SPLIT_LEAK" if source_record is not None and source_record["split"] != "train" else "E_BENCHMARK_SOURCE_IDENTITY"
                    _fail(code, f"ranking {query_id} source identity does not match record_id")
            ranked = copy.deepcopy(dict(record))
            for field in ("project_types", "section_intents", "tags", "pattern", "source"):
                ranked[field] = copy.deepcopy(manifest_record[field])
            ranked["source_split"] = manifest_record["split"]
            authoritative_records.append(ranked)
        authoritative[query_id] = authoritative_records
    return authoritative


def rank_queries(manifest: Any, query_items: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(query_items, Sequence) or isinstance(query_items, (str, bytes)):
        _fail("E_BENCHMARK_QUERY", "query_items must be a list")
    ordered = sorted(query_items, key=lambda item: item.get("query_id", ""))
    if len({item.get("query_id") for item in ordered}) != len(ordered):
        _fail("E_BENCHMARK_QUERY", "query IDs must be unique")
    rankings: dict[str, list[dict[str, Any]]] = {}
    for item in ordered:
        query_id = item.get("query_id")
        if not isinstance(query_id, str) or not _SLUG.fullmatch(query_id) or not isinstance(item.get("query"), Mapping):
            _fail("E_BENCHMARK_QUERY", "query item is malformed")
        packet = retrieve_patterns_v2(manifest, item["query"], mode="production")
        rankings[query_id] = copy.deepcopy(packet["records"])
    return validate_production_rankings(manifest, rankings)


def _metric(numerator: int, denominator: int) -> dict[str, int]:
    if type(numerator) is not int or type(denominator) is not int or numerator < 0 or denominator <= 0 or numerator > denominator:
        _fail("E_BENCHMARK_DENOMINATOR", "metric numerator/denominator is invalid or zero")
    return {"numerator": numerator, "denominator": denominator, "value_basis_points": basis_points(numerator, denominator)}


def _validated_metric(
    value: Any,
    fields: set[str],
    context: str,
    code: str,
    *,
    threshold_code: str | None = None,
) -> dict[str, int]:
    if not isinstance(value, Mapping):
        _fail(code, f"{context} must be an object")
    metric = dict(value)
    if set(metric) != fields:
        _fail(code, f"{context} fields are incomplete or unknown")
    raw_numerator = metric["numerator"]
    raw_denominator = metric["denominator"]
    raw_value_basis_points = metric["value_basis_points"]
    if (
        type(raw_numerator) is not int
        or type(raw_denominator) is not int
        or type(raw_value_basis_points) is not int
    ):
        _fail(code, f"{context} must contain integers")
    numerator = raw_numerator
    denominator = raw_denominator
    value_basis_points = raw_value_basis_points
    if (
        numerator < 0
        or denominator <= 0
        or numerator > denominator
        or numerator > MAX_METRIC_COMPONENT
        or denominator > MAX_METRIC_COMPONENT
        or not 0 <= value_basis_points <= 10_000
    ):
        _fail(code, f"{context} is out of bounds")
    parsed = {
        "numerator": numerator,
        "denominator": denominator,
        "value_basis_points": value_basis_points,
    }
    if value_basis_points != basis_points(numerator, denominator):
        _fail(code, f"{context} is equation-inconsistent")
    if "threshold_basis_points" in fields:
        raw_threshold = metric["threshold_basis_points"]
        if type(raw_threshold) is not int:
            _fail(code, f"{context} must contain integers")
        if not 0 <= raw_threshold <= 10_000:
            _fail(code, f"{context} threshold is out of bounds")
        if threshold_code is not None and value_basis_points < raw_threshold:
            _fail(threshold_code, f"metric below fixed threshold: {context.removeprefix('metric ')}")
        parsed["threshold_basis_points"] = raw_threshold
    return parsed


def score_rankings(
    manifest: Any,
    query_items: Sequence[Mapping[str, Any]],
    rankings: Mapping[str, Sequence[Mapping[str, Any]]],
) -> dict[str, dict[str, int]]:
    if not query_items:
        _fail("E_BENCHMARK_DENOMINATOR", "benchmark requires at least one query")
    authoritative_rankings = validate_production_rankings(manifest, rankings)
    ordered = sorted(query_items, key=lambda item: item.get("query_id", ""))
    if len({item.get("query_id") for item in ordered}) != len(ordered):
        _fail("E_BENCHMARK_QUERY", "query IDs must be unique")
    if set(rankings) != {item.get("query_id") for item in ordered}:
        _fail("E_BENCHMARK_RANKING", "rankings do not match canonical query IDs")

    project_hits = recall_hits = recall_total = section_hits = section_total = 0
    reciprocal_sum = Fraction(0)
    dcg_total = ideal_dcg_total = diversity_unique = diversity_total = 0
    for item in ordered:
        query_id = item.get("query_id")
        if not isinstance(query_id, str):
            _fail("E_BENCHMARK_QUERY", "query_id is invalid")
        if item.get("expected_relevant_ids") == []:
            _fail("E_BENCHMARK_DENOMINATOR", f"{query_id} has no relevant gold IDs")
        relevant = _slug_list(item.get("expected_relevant_ids"), f"{query_id}.expected_relevant_ids")
        desired_sections = _slug_list(item.get("section_intents"), f"{query_id}.section_intents")
        project_type = item.get("project_type")
        if not isinstance(project_type, str) or not _SLUG.fullmatch(project_type):
            _fail("E_BENCHMARK_QUERY", f"{query_id}.project_type is invalid")
        records = authoritative_rankings[query_id]
        if len(records) > 5:
            _fail("E_BENCHMARK_RANKING", f"{query_id} exceeds Recall@5 boundary")
        identifiers = [record["record_id"] for record in records]
        relevant_set = set(relevant)
        if records and project_type in records[0].get("project_types", []):
            project_hits += 1
        hits = relevant_set & set(identifiers)
        recall_hits += len(hits)
        recall_total += len(relevant)
        first_rank = next((index for index, record_id in enumerate(identifiers, 1) if record_id in relevant_set), None)
        if first_rank is not None:
            reciprocal_sum += Fraction(1, first_rank)
        dcg_total += sum(
            NDCG_DISCOUNTS_BASIS_POINTS[index]
            for index, record_id in enumerate(identifiers)
            if record_id in relevant_set
        )
        ideal_dcg_total += sum(NDCG_DISCOUNTS_BASIS_POINTS[:min(len(relevant), 5)])
        available_sections = {
            section
            for record in records
            for section in record.get("section_intents", [])
            if isinstance(section, str)
        }
        section_hits += len(set(desired_sections) & available_sections)
        section_total += len(desired_sections)
        diversity_unique += len({canonical_sha256(record.get("pattern")) for record in records})
        diversity_total += len(records)

    average_reciprocal = reciprocal_sum / len(ordered)
    metrics = {
        "project_type_accuracy": _metric(project_hits, len(ordered)),
        "recall_at_5": _metric(recall_hits, recall_total),
        "mrr": _metric(average_reciprocal.numerator, average_reciprocal.denominator),
        "ndcg_at_5": _metric(dcg_total, ideal_dcg_total),
        "section_intent_coverage": _metric(section_hits, section_total),
        "pattern_diversity": _metric(diversity_unique, diversity_total),
    }
    return {name: metrics[name] for name in METRIC_NAMES}


def apply_thresholds(metrics: Mapping[str, Mapping[str, Any]], baseline: Any) -> dict[str, dict[str, int]]:
    if not isinstance(metrics, Mapping) or set(metrics) != set(METRIC_NAMES):
        _fail("E_BENCHMARK_METRIC", "six current metrics are required")
    if not isinstance(baseline, Mapping) or baseline.get("status") != "verified":
        _fail("E_BENCHMARK_BASELINE", "verified six-metric baseline is required")
    baseline_metrics = baseline.get("metrics")
    if not isinstance(baseline_metrics, Mapping) or set(baseline_metrics) != set(METRIC_NAMES):
        _fail("E_BENCHMARK_BASELINE", "baseline metrics are incomplete")
    output: dict[str, dict[str, int]] = {}
    for name in METRIC_NAMES:
        current = _validated_metric(metrics[name], _METRIC_FIELDS, f"metric {name}", "E_BENCHMARK_METRIC")
        frozen = _validated_metric(
            baseline_metrics[name],
            _BASELINE_METRIC_FIELDS,
            f"baseline metric {name}",
            "E_BENCHMARK_BASELINE",
        )
        threshold = frozen["threshold_basis_points"]
        if threshold != max(0, frozen["value_basis_points"] - THRESHOLD_MARGIN_BASIS_POINTS):
            _fail("E_BENCHMARK_BASELINE", f"baseline metric {name} has invalid integer threshold")
        output[name] = {**current, "threshold_basis_points": threshold}
    return output


def assert_thresholds(metrics: Mapping[str, Mapping[str, Any]]) -> None:
    if not isinstance(metrics, Mapping) or set(metrics) != set(METRIC_NAMES):
        _fail("E_BENCHMARK_METRIC", "thresholded metrics are incomplete")
    for name in METRIC_NAMES:
        _validated_metric(
            metrics[name],
            _BASELINE_METRIC_FIELDS,
            f"metric {name}",
            "E_BENCHMARK_METRIC",
            threshold_code="E_BENCHMARK_THRESHOLD",
        )


def _validate_baseline(baseline: Any, approved: Mapping[str, Any], receipt: str) -> dict[str, Any]:
    _reject_floats(baseline)
    frozen = _object(baseline, _BASELINE_FIELDS, "retrieval benchmark baseline")
    if (
        frozen["schema_version"] != 1
        or frozen["status"] != "verified"
        or frozen["dataset"] != approved["dataset"]
        or frozen["gold_set_sha256"] != approved["gold_set_sha256"]
        or frozen["review_receipt_sha256"] != receipt
    ):
        _fail("E_BENCHMARK_BASELINE", "baseline is pending or not bound to reviewed gold")
    baseline_metrics = frozen["metrics"]
    if not isinstance(baseline_metrics, Mapping) or set(baseline_metrics) != set(METRIC_NAMES):
        _fail("E_BENCHMARK_BASELINE", "baseline metrics are incomplete")
    current_metrics: dict[str, dict[str, int]] = {}
    for name in METRIC_NAMES:
        metric = _validated_metric(
            baseline_metrics[name],
            _BASELINE_METRIC_FIELDS,
            f"baseline metric {name}",
            "E_BENCHMARK_BASELINE",
        )
        current_metrics[name] = {key: metric[key] for key in _METRIC_FIELDS}
    apply_thresholds(current_metrics, frozen)
    return copy.deepcopy(frozen)


def run_benchmark(
    manifest: Any,
    query_set: Any,
    baseline: Any,
) -> dict[str, Any]:
    approved = validate_reviewed_query_set(manifest, query_set)
    receipt_sha256 = approved["queries"][0]["review"]["receipt_sha256"]
    frozen = _validate_baseline(baseline, approved, receipt_sha256)
    rankings = rank_queries(manifest, approved["queries"])
    metrics = apply_thresholds(score_rankings(manifest, approved["queries"], rankings), frozen)
    assert_thresholds(metrics)
    return {
        "schema_version": 1,
        "status": "pass",
        "dataset": copy.deepcopy(approved["dataset"]),
        "gold_set_sha256": approved["gold_set_sha256"],
        "review_receipt_sha256": receipt_sha256,
        "ranking_sha256": canonical_sha256(rankings),
        "rankings": rankings,
        "metrics": metrics,
    }


__all__ = [
    "METRIC_NAMES", "NDCG_DISCOUNTS_BASIS_POINTS", "THRESHOLD_MARGIN_BASIS_POINTS",
    "apply_thresholds", "assert_thresholds", "rank_queries", "run_benchmark", "score_rankings",
    "validate_production_rankings", "validate_reviewed_query_set",
]
