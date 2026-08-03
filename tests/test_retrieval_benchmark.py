from __future__ import annotations

import copy
import json
import socket
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.retrieval.benchmark import (
    METRIC_NAMES,
    apply_thresholds,
    assert_thresholds,
    rank_queries,
    run_benchmark,
    score_rankings,
    validate_production_rankings,
    validate_reviewed_query_set,
)


ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "dataset/retrieval/manifest.json"
QUERIES = ROOT / "dataset/retrieval/queries.json"
BASELINE = ROOT / "tests/fixtures/retrieval/benchmark-baseline.json"


def _result(
    record_id: str,
    project_type: str,
    sections: list[str],
    pattern: str,
    *,
    split: str = "train",
) -> dict[str, object]:
    return {
        "record_id": record_id,
        "project_types": [project_type],
        "section_intents": sections,
        "pattern": {"summary": pattern, "structure": pattern, "proof": pattern},
        "source_split": split,
    }


class RetrievalBenchmarkTests(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_exact_integer_metrics_cover_reciprocal_and_discounted_ranks(self) -> None:
        queries = [
            {
                "query_id": "query-a",
                "project_type": "library",
                "section_intents": ["installation"],
                "expected_relevant_ids": ["record-a"],
            },
            {
                "query_id": "query-b",
                "project_type": "developer-tool",
                "section_intents": ["verification"],
                "expected_relevant_ids": ["record-c"],
            },
        ]
        rankings = {
            "query-a": [
                _result("record-b", "library", ["compatibility"], "pattern-b"),
                _result("record-a", "library", ["installation"], "pattern-a"),
            ],
            "query-b": [
                _result("record-c", "developer-tool", ["verification"], "pattern-c"),
            ],
        }

        metrics = score_rankings(queries, rankings)

        self.assertEqual(tuple(metrics), METRIC_NAMES)
        self.assertEqual(metrics["project_type_accuracy"], {"numerator": 2, "denominator": 2, "value_basis_points": 10_000})
        self.assertEqual(metrics["recall_at_5"], {"numerator": 2, "denominator": 2, "value_basis_points": 10_000})
        self.assertEqual(metrics["mrr"], {"numerator": 3, "denominator": 4, "value_basis_points": 7_500})
        self.assertEqual(metrics["ndcg_at_5"], {"numerator": 16_309, "denominator": 20_000, "value_basis_points": 8_155})
        self.assertEqual(metrics["section_intent_coverage"], {"numerator": 2, "denominator": 2, "value_basis_points": 10_000})
        self.assertEqual(metrics["pattern_diversity"], {"numerator": 3, "denominator": 3, "value_basis_points": 10_000})
        self.assertTrue(all(type(value) is int for metric in metrics.values() for value in metric.values()))

    def test_query_order_is_canonical_and_empty_relevance_fails_closed(self) -> None:
        queries = [
            {"query_id": "b", "project_type": "library", "section_intents": ["overview"], "expected_relevant_ids": ["b"]},
            {"query_id": "a", "project_type": "library", "section_intents": ["overview"], "expected_relevant_ids": ["a"]},
        ]
        rankings = {
            "a": [_result("a", "library", ["overview"], "a")],
            "b": [_result("b", "library", ["overview"], "b")],
        }
        first = score_rankings(queries, rankings)
        second = score_rankings(list(reversed(queries)), rankings)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        for invalid in ([], [{"query_id": "empty", "project_type": "library", "section_intents": ["overview"], "expected_relevant_ids": []}]):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError) as raised:
                score_rankings(invalid, {} if not invalid else {"empty": []})
            self.assertEqual(raised.exception.code, "E_BENCHMARK_DENOMINATOR")

    def test_fixed_threshold_edges_and_named_regression(self) -> None:
        metrics = {
            name: {"numerator": 1, "denominator": 1, "value_basis_points": 10_000}
            for name in METRIC_NAMES
        }
        baseline = {
            "status": "verified",
            "metrics": {
                name: {"numerator": 1, "denominator": 1, "value_basis_points": 10_000, "threshold_basis_points": 9_800}
                for name in METRIC_NAMES
            },
        }
        thresholded = apply_thresholds(metrics, baseline)
        self.assertTrue(all(metric["threshold_basis_points"] == 9_800 for metric in thresholded.values()))
        assert_thresholds(thresholded)
        thresholded["mrr"]["value_basis_points"] = 9_799
        with self.assertRaises(ContractError) as raised:
            assert_thresholds(thresholded)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_THRESHOLD")
        self.assertIn("mrr", str(raised.exception))

    def test_rank_queries_is_offline_read_only_train_only_and_deterministic(self) -> None:
        manifest = self.manifest()
        before = copy.deepcopy(manifest)
        record = next(item for item in manifest["records"] if item["record_id"] == "fastapi-proof-first-overview")
        query_items = [
            {
                "query_id": "fastapi-proof-first-overview",
                "query": {
                    "project_type": record["project_types"][0],
                    "sections": record["section_intents"],
                    "tags": record["tags"],
                    "manifest_features": [record["pattern"]["summary"]],
                    "evidence_sha256": record["source"]["material_sha256"],
                },
            }
        ]
        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network call")):
            first = rank_queries(manifest, query_items)
            second = rank_queries(manifest, list(reversed(query_items)))
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(manifest, before)
        self.assertTrue(all(item["source_split"] == "train" for item in first["fastapi-proof-first-overview"]))

    def test_split_leak_uses_stable_error(self) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_production_rankings({"query": [_result("held-out", "library", ["overview"], "held", split="test")]})
        self.assertEqual(raised.exception.code, "E_DATASET_SPLIT_LEAK")

    def test_pending_generated_and_self_attested_gold_are_rejected(self) -> None:
        manifest = self.manifest()
        proposals = json.loads(QUERIES.read_text(encoding="utf-8"))
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, proposals)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_REQUIRED")

        generated = copy.deepcopy(proposals)
        generated["status"] = "reviewed"
        generated["proposal_origin"] = "human-reviewed"
        for item in generated["queries"]:
            item["review"].update({
                "human_reviewed": True,
                "review_method": "generated",
                "reviewer_id": "agent:self",
                "reviewed_at": "2026-08-03T00:00:00Z",
                "receipt_sha256": "0" * 64,
            })
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, generated, trusted_receipt_sha256="0" * 64)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_GENERATED_GOLD")

        self_attested = copy.deepcopy(generated)
        for item in self_attested["queries"]:
            item["review"]["review_method"] = "self-attested"
            item["review"]["reviewer_id"] = "fixture-human"
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, self_attested, trusted_receipt_sha256="0" * 64)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_REQUIRED")

        forged = copy.deepcopy(generated)
        for item in forged["queries"]:
            item["review"].update({"review_method": "independent-human", "reviewer_id": "fixture-human"})
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, forged, trusted_receipt_sha256="0" * 64)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_RECEIPT")

        altered_gold = copy.deepcopy(proposals)
        altered_gold["queries"][0]["expected_relevant_ids"] = ["deno-runtime-first-run"]
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, altered_gold)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_SOURCE_IDENTITY")

    def test_production_entrypoint_stops_before_unapproved_baseline(self) -> None:
        manifest = self.manifest()
        proposals = json.loads(QUERIES.read_text(encoding="utf-8"))
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(baseline["status"], "pending-human-review")
        with self.assertRaises(ContractError) as raised:
            run_benchmark(manifest, proposals, baseline)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_REQUIRED")


if __name__ == "__main__":
    unittest.main()
