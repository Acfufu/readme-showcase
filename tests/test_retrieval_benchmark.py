from __future__ import annotations
# noqa: SIZE_OK - single benchmark contract suite; task ownership forbids a cross-file split.

import copy
import json
import socket
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
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
APPROVAL_ARTIFACT_SHA256 = "96c16fa820e2ad4fa3e5f3b114f4c1d0dd596b6bee342df6f531b41145d7f37b"
APPROVAL_RECEIPT_SHA256 = "e414a807b2961b40199e8575697cb7f7c693cdce7459b353e9c53dca7e4a4cfd"
REVIEW_PACKET_SHA256 = "fe92456c6dbde1b1a53d1541a8b4f9e12fa3fa513ffecffc6611fda3f69a8310"
SOURCE_COMMIT = "8b1fbe257e25ceafd6541b8f23341fbbe6253180"


def _result(
    record_id: str,
    project_type: str,
    sections: list[str],
    pattern: str,
    *,
    split: str = "train",
) -> dict[str, Any]:
    return {
        "record_id": record_id,
        "project_types": [project_type],
        "section_intents": sections,
        "pattern": {"summary": pattern, "structure": pattern, "proof": pattern},
        "source_split": split,
    }


def _perfect_metrics() -> dict[str, dict[str, int]]:
    return {
        name: {"numerator": 1, "denominator": 1, "value_basis_points": 10_000}
        for name in METRIC_NAMES
    }


def _verified_metric_baseline() -> dict[str, Any]:
    return {
        "status": "verified",
        "metrics": {
            name: {
                "numerator": 1,
                "denominator": 1,
                "value_basis_points": 10_000,
                "threshold_basis_points": 9_800,
            }
            for name in METRIC_NAMES
        },
    }


class RetrievalBenchmarkTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def query_set(self) -> dict[str, Any]:
        return json.loads(QUERIES.read_text(encoding="utf-8"))

    def baseline(self) -> dict[str, Any]:
        return json.loads(BASELINE.read_text(encoding="utf-8"))

    def test_exact_integer_metrics_cover_reciprocal_and_discounted_ranks(self) -> None:
        queries = [
            {
                "query_id": "query-a",
                "project_type": "library",
                "section_intents": ["installation"],
                "expected_relevant_ids": ["httpx-dual-interface"],
            },
            {
                "query_id": "query-b",
                "project_type": "developer-tool",
                "section_intents": ["integration"],
                "expected_relevant_ids": ["ruff-command-and-editor-paths"],
            },
        ]
        rankings = {
            "query-a": [
                _result("requests-minimal-session", "library", ["installation"], "pattern-b"),
                _result("httpx-dual-interface", "library", ["compatibility"], "pattern-a"),
            ],
            "query-b": [
                _result("ruff-command-and-editor-paths", "developer-tool", ["integration"], "pattern-c"),
            ],
        }

        metrics = score_rankings(self.manifest(), queries, rankings)

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
            {"query_id": "b", "project_type": "library", "section_intents": ["verification"], "expected_relevant_ids": ["pydantic-capability-to-proof"]},
            {"query_id": "a", "project_type": "library", "section_intents": ["quick-start"], "expected_relevant_ids": ["requests-minimal-session"]},
        ]
        rankings = {
            "a": [_result("requests-minimal-session", "library", ["quick-start"], "a")],
            "b": [_result("pydantic-capability-to-proof", "library", ["verification"], "b")],
        }
        manifest = self.manifest()
        first = score_rankings(manifest, queries, rankings)
        second = score_rankings(manifest, list(reversed(queries)), rankings)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        for invalid in ([], [{"query_id": "empty", "project_type": "library", "section_intents": ["overview"], "expected_relevant_ids": []}]):
            with self.subTest(invalid=invalid), self.assertRaises(ContractError) as raised:
                score_rankings(manifest, invalid, {} if not invalid else {"empty": []})
            self.assertEqual(raised.exception.code, "E_BENCHMARK_DENOMINATOR")

    def test_fixed_threshold_edges_and_named_regression(self) -> None:
        metrics = _perfect_metrics()
        baseline = _verified_metric_baseline()
        thresholded = apply_thresholds(metrics, baseline)
        self.assertTrue(all(metric["threshold_basis_points"] == 9_800 for metric in thresholded.values()))
        at_threshold = copy.deepcopy(thresholded)
        at_threshold["mrr"].update({"numerator": 49, "denominator": 50, "value_basis_points": 9_800})
        assert_thresholds(at_threshold)
        below_threshold = copy.deepcopy(thresholded)
        below_threshold["mrr"].update({"numerator": 9_799, "denominator": 10_000, "value_basis_points": 9_799})
        with self.assertRaises(ContractError) as raised:
            assert_thresholds(below_threshold)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_THRESHOLD")
        self.assertIn("mrr", str(raised.exception))

    def test_apply_thresholds_rejects_malformed_current_metric_entries(self) -> None:
        metrics = _perfect_metrics()
        baseline = _verified_metric_baseline()
        current_mutations = (
            ("zero-denominator", "denominator", 0),
            ("float-denominator", "denominator", 1.0),
            ("boolean-denominator", "denominator", True),
            ("negative-numerator", "numerator", -1),
            ("boolean-numerator", "numerator", True),
            ("oversized-numerator", "numerator", 10**1000),
            ("numerator-over-denominator", "numerator", 2),
            ("boolean-value", "value_basis_points", True),
            ("float-value", "value_basis_points", 10_000.0),
            ("negative-value", "value_basis_points", -1),
            ("value-over-maximum", "value_basis_points", 10_001),
            ("oversized-value", "value_basis_points", 10**1000),
            ("inconsistent-value", "value_basis_points", 9_999),
        )
        for case, field, value in current_mutations:
            with self.subTest(case=case):
                mutated = {
                    **metrics,
                    "mrr": {**metrics["mrr"], field: value},
                }
                with self.assertRaises(ContractError) as raised:
                    apply_thresholds(mutated, baseline)
                self.assertEqual(raised.exception.code, "E_BENCHMARK_METRIC")

    def test_apply_thresholds_rejects_malformed_baseline_metric_entries(self) -> None:
        metrics = _perfect_metrics()
        baseline_metrics = _verified_metric_baseline()["metrics"]
        baseline_mutations = (
            ("zero-denominator", "denominator", 0),
            ("float-denominator", "denominator", 1.0),
            ("boolean-denominator", "denominator", False),
            ("negative-numerator", "numerator", -1),
            ("boolean-numerator", "numerator", True),
            ("oversized-numerator", "numerator", 10**1000),
            ("numerator-over-denominator", "numerator", 2),
            ("inconsistent-numerator", "numerator", 0),
            ("boolean-value", "value_basis_points", True),
            ("float-value", "value_basis_points", 10_000.0),
            ("negative-value", "value_basis_points", -1),
            ("value-over-maximum", "value_basis_points", 10_001),
            ("oversized-value", "value_basis_points", 10**1000),
            ("inconsistent-value", "value_basis_points", 9_999),
            ("boolean-threshold", "threshold_basis_points", True),
            ("float-threshold", "threshold_basis_points", 9_800.0),
            ("negative-threshold", "threshold_basis_points", -1),
            ("threshold-over-maximum", "threshold_basis_points", 10_001),
            ("oversized-threshold", "threshold_basis_points", 10**1000),
            ("inconsistent-threshold", "threshold_basis_points", 9_799),
        )
        for case, field, value in baseline_mutations:
            with self.subTest(case=case):
                mutated_metrics = {
                    **baseline_metrics,
                    "mrr": {**baseline_metrics["mrr"], field: value},
                }
                mutated = {"status": "verified", "metrics": mutated_metrics}
                with self.assertRaises(ContractError) as raised:
                    apply_thresholds(metrics, mutated)
                self.assertEqual(raised.exception.code, "E_BENCHMARK_BASELINE")

    def test_assert_thresholds_rejects_malformed_metric_entries(self) -> None:
        thresholded = apply_thresholds(_perfect_metrics(), _verified_metric_baseline())
        mutations = (
            ("zero-denominator", "denominator", 0),
            ("boolean-numerator", "numerator", True),
            ("oversized-numerator", "numerator", 10**1000),
            ("boolean-value", "value_basis_points", True),
            ("oversized-value", "value_basis_points", 10**1000),
            ("inconsistent-value", "value_basis_points", 9_999),
            ("inconsistent-value-below-threshold", "value_basis_points", 0),
            ("boolean-threshold", "threshold_basis_points", True),
            ("threshold-over-maximum", "threshold_basis_points", 10_001),
        )
        for case, field, value in mutations:
            with self.subTest(case=case):
                mutated = {
                    **thresholded,
                    "mrr": {**thresholded["mrr"], field: value},
                }
                with self.assertRaises(ContractError) as raised:
                    assert_thresholds(mutated)
                self.assertEqual(raised.exception.code, "E_BENCHMARK_METRIC")

    def test_metric_entrypoints_reject_equation_consistent_unbounded_integers(self) -> None:
        huge = 10**1000
        current = _perfect_metrics()
        baseline = _verified_metric_baseline()
        huge_current = copy.deepcopy(current)
        huge_current["mrr"].update({"numerator": huge, "denominator": huge})
        huge_baseline = copy.deepcopy(baseline)
        huge_baseline["metrics"]["mrr"].update({"numerator": huge, "denominator": huge})
        huge_thresholded = apply_thresholds(current, baseline)
        huge_thresholded["mrr"].update({"numerator": huge, "denominator": huge})
        cases = (
            ("current", "E_BENCHMARK_METRIC", lambda: apply_thresholds(huge_current, baseline)),
            ("baseline", "E_BENCHMARK_BASELINE", lambda: apply_thresholds(current, huge_baseline)),
            ("assert", "E_BENCHMARK_METRIC", lambda: assert_thresholds(huge_thresholded)),
        )
        for case, expected_code, invoke in cases:
            with self.subTest(case=case), self.assertRaises(ContractError) as raised:
                invoke()
            self.assertEqual(raised.exception.code, expected_code)

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

    def test_approved_fixture_runs_frozen_baseline_and_reversed_order_is_byte_identical(self) -> None:
        manifest = self.manifest()
        query_set = self.query_set()
        baseline = self.baseline()
        reversed_query_set = copy.deepcopy(query_set)
        reversed_query_set["queries"].reverse()

        with mock.patch.object(socket, "create_connection", side_effect=AssertionError("network call")):
            first = run_benchmark(manifest, query_set, baseline)
            second = run_benchmark(manifest, reversed_query_set, baseline)

        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["metrics"], baseline["metrics"])
        self.assertEqual(first["ranking_sha256"], "654c2b81d8b01bf99e1bcd7dab46064a96eae5670f9de6fe120bf292cb9f1ecf")
        self.assertEqual(first["review_receipt_sha256"], APPROVAL_RECEIPT_SHA256)

    def test_real_relevant_rank_regression_fails_named_mrr_threshold(self) -> None:
        manifest = self.manifest()
        query_set = self.query_set()
        rankings = rank_queries(manifest, query_set["queries"])
        query_id = query_set["queries"][0]["query_id"]
        rankings[query_id][0], rankings[query_id][1] = rankings[query_id][1], rankings[query_id][0]
        thresholded = apply_thresholds(
            score_rankings(manifest, query_set["queries"], rankings),
            self.baseline(),
        )

        with self.assertRaises(ContractError) as raised:
            assert_thresholds(thresholded)

        self.assertEqual(raised.exception.code, "E_BENCHMARK_THRESHOLD")
        self.assertIn("mrr", str(raised.exception))

    def test_approval_binding_drift_and_untrusted_receipts_fail_closed(self) -> None:
        manifest = self.manifest()
        query_set = self.query_set()
        query_drift = copy.deepcopy(query_set)
        query_drift["queries"][0]["query"]["manifest_features"] = ["drift"]
        gold_drift = copy.deepcopy(query_set)
        gold_drift["gold_set_sha256"] = "0" * 64
        dataset_drift = copy.deepcopy(query_set)
        dataset_drift["dataset"]["dataset_revision"] += 1
        review_drifts: list[tuple[str, str, str]] = [
            ("reviewer", "reviewer_id", "other-human"),
            ("review-time", "reviewed_at", "2026-08-03T07:20:57Z"),
            ("receipt", "receipt_sha256", "0" * 64),
            ("review-packet", "review_packet_sha256", "0" * 64),
            ("approval-artifact", "approval_artifact_sha256", "0" * 64),
            ("source-commit", "source_commit", "0" * 40),
        ]
        cases: list[tuple[str, dict[str, Any], str]] = [
            ("query", query_drift, "E_BENCHMARK_GOLD_HASH"),
            ("gold", gold_drift, "E_BENCHMARK_GOLD_HASH"),
            ("dataset", dataset_drift, "E_BENCHMARK_MANIFEST_HASH"),
        ]
        for name, field, value in review_drifts:
            drifted = copy.deepcopy(query_set)
            for item in drifted["queries"]:
                item["review"][field] = value
            cases.append((name, drifted, "E_BENCHMARK_REVIEW_RECEIPT"))

        for name, payload, expected_code in cases:
            with self.subTest(name=name), self.assertRaises(ContractError) as raised:
                validate_reviewed_query_set(manifest, payload)
            self.assertEqual(raised.exception.code, expected_code)

    def test_split_leak_uses_stable_error(self) -> None:
        manifest = self.manifest()
        with self.assertRaises(ContractError) as raised:
            validate_production_rankings(
                manifest,
                {"query": [_result("nextjs-route-map", "web-framework", ["overview"], "held", split="train")]},
            )
        self.assertEqual(raised.exception.code, "E_DATASET_SPLIT_LEAK")

    def test_reviewed_gold_cannot_spoof_heldout_manifest_identity_as_train(self) -> None:
        manifest = self.manifest()
        heldout = next(record for record in manifest["records"] if record["record_id"] == "nextjs-route-map")
        query_set = self.query_set()
        query_set["queries"][0]["expected_relevant_ids"] = [heldout["record_id"]]
        query_set["queries"][0]["expected_relevant_source_identities"] = [{
            "record_id": heldout["record_id"],
            "repository_url": heldout["source"]["repository_url"],
            "commit": heldout["source"]["commit"],
            "material_sha256": heldout["source"]["material_sha256"],
            "split": "train",
        }]

        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, query_set)

        self.assertEqual(raised.exception.code, "E_DATASET_SPLIT_LEAK")

    def test_pending_generated_and_self_attested_gold_are_rejected(self) -> None:
        manifest = self.manifest()
        proposals = self.query_set()
        proposals["status"] = "pending-human-review"
        proposals["proposal_origin"] = "codex-unreviewed"
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
            validate_reviewed_query_set(manifest, generated)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_GENERATED_GOLD")

        self_attested = copy.deepcopy(generated)
        for item in self_attested["queries"]:
            item["review"]["review_method"] = "self-attested"
            item["review"]["reviewer_id"] = "fixture-human"
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, self_attested)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_REQUIRED")

        forged = copy.deepcopy(generated)
        for item in forged["queries"]:
            item["review"].update({"review_method": "independent-human", "reviewer_id": "fixture-human"})
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, forged)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_RECEIPT")

        altered_gold = copy.deepcopy(proposals)
        altered_gold["queries"][0]["expected_relevant_ids"] = ["deno-runtime-first-run"]
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, altered_gold)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_SOURCE_IDENTITY")

    def test_self_computed_caller_receipt_is_not_a_trust_anchor(self) -> None:
        manifest = self.manifest()
        proposals = json.loads(QUERIES.read_text(encoding="utf-8"))
        self_computed = copy.deepcopy(proposals)
        self_computed["status"] = "reviewed"
        self_computed["proposal_origin"] = "human-reviewed"
        review = {
            "human_reviewed": True,
            "review_method": "independent-human",
            "reviewer_id": "fake-human",
            "reviewed_at": "2026-08-03T01:02:03Z",
            "receipt_sha256": None,
            "review_packet_sha256": REVIEW_PACKET_SHA256,
            "approval_artifact_sha256": APPROVAL_ARTIFACT_SHA256,
            "source_commit": SOURCE_COMMIT,
        }
        receipt = canonical_sha256({
            "schema_version": 1,
            "kind": "readme-showcase-retrieval-gold-review",
            "review_method": review["review_method"],
            "reviewer_id": review["reviewer_id"],
            "reviewed_at": review["reviewed_at"],
            "dataset": self_computed["dataset"],
            "gold_set_sha256": self_computed["gold_set_sha256"],
        })
        review["receipt_sha256"] = receipt
        for item in self_computed["queries"]:
            item["review"] = copy.deepcopy(review)

        with self.assertRaises(TypeError):
            validate_reviewed_query_set(
                manifest,
                self_computed,
                **{"trusted_receipt_sha256": receipt},
            )
        with self.assertRaises(ContractError) as raised:
            validate_reviewed_query_set(manifest, self_computed)
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_RECEIPT")

    def test_production_entrypoint_stops_before_unapproved_baseline(self) -> None:
        manifest = self.manifest()
        proposals = self.query_set()
        proposals["status"] = "pending-human-review"
        proposals["proposal_origin"] = "codex-unreviewed"
        with self.assertRaises(ContractError) as raised:
            run_benchmark(manifest, proposals, self.baseline())
        self.assertEqual(raised.exception.code, "E_BENCHMARK_REVIEW_REQUIRED")

    def test_approved_query_set_stops_before_pending_baseline(self) -> None:
        pending_baseline = self.baseline()
        pending_baseline["status"] = "pending-human-review"
        pending_baseline["review_receipt_sha256"] = None
        pending_baseline["metrics"] = None

        with self.assertRaises(ContractError) as raised:
            run_benchmark(self.manifest(), self.query_set(), pending_baseline)

        self.assertEqual(raised.exception.code, "E_BENCHMARK_BASELINE")


if __name__ == "__main__":
    unittest.main()
