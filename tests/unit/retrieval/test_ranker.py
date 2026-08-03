from __future__ import annotations

import copy
import json
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.retrieval import adapt_v2_to_v1, validate_retrieval_packet_v2
from skill.scripts.readme_showcase.retrieval.metrics import K1, B, LAMBDA, basis_points, bm25_scores
from skill.scripts.readme_showcase.retrieval.ranker import rank_records, tokenize
from skill.scripts.readme_showcase.retrieval.service import retrieve_patterns_v2


ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "dataset/retrieval/manifest.json"
FIXTURES = ROOT / "tests/fixtures/contracts"


def query() -> dict[str, object]:
    return {
        "project_type": "web-framework",
        "sections": ["overview", "quick-start"],
        "tags": ["api", "observable-output"],
        "manifest_features": ["local server", "generated interface"],
        "evidence_sha256": "a" * 64,
    }


class HybridRankerTests(unittest.TestCase):
    def manifest(self) -> dict[str, object]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def test_fraction_parameters_rounding_and_tokenization_are_exact(self) -> None:
        self.assertEqual((K1.numerator, K1.denominator), (6, 5))
        self.assertEqual((B.numerator, B.denominator), (3, 4))
        self.assertEqual((LAMBDA.numerator, LAMBDA.denominator), (7, 10))
        self.assertEqual(basis_points(1, 6), 1667)
        self.assertEqual(tokenize("Ｆoo_BAR-42 中文检索 A"), ("foo_bar", "42", "中文", "文检", "检索", "a"))

    def test_bm25_is_fraction_only_and_stable_for_empty_and_extreme_documents(self) -> None:
        scores = bm25_scores(("alpha",), [("alpha",), ("alpha",) * 10_000, ()])
        self.assertEqual([type(score).__name__ for score in scores], ["Fraction"] * 3)
        self.assertLess(scores[0], scores[1])
        self.assertEqual(scores[2], 0)
        self.assertEqual(bm25_scores((), [("alpha",)]), [0])
        empty = {**query(), "project_type": "unknown", "sections": [], "tags": [], "manifest_features": []}
        self.assertEqual(rank_records(self.manifest()["records"], empty), [])

    def test_golden_order_signals_mmr_tie_and_shuffle_are_deterministic(self) -> None:
        manifest = self.manifest()
        records = copy.deepcopy(manifest["records"])
        first = rank_records(records, query())
        second = rank_records(list(reversed(records)), query())
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual([item["record_id"] for item in first[:2]], [
            "fastapi-proof-first-overview", "gin-json-api-first-run",
        ])
        self.assertEqual(set(first[0]["signals"]), {
            "project_type_basis_points", "section_overlap_basis_points", "tag_overlap_basis_points",
            "manifest_feature_overlap_basis_points", "bm25_basis_points", "diversity_penalty_basis_points",
        })
        self.assertTrue(all(type(value) is int for value in first[0]["signals"].values()))
        self.assertTrue(all(set(reason) == {"code", "signal", "matched_values"} for reason in first[0]["reasons"]))
        self.assertGreater(first[1]["signals"]["diversity_penalty_basis_points"], 0)

        tied = [copy.deepcopy(records[0]), copy.deepcopy(records[0])]
        tied[0]["record_id"], tied[1]["record_id"] = "tie-b", "tie-a"
        tied[0]["source"]["material_sha256"] = "b" * 64
        tied[1]["source"]["material_sha256"] = "c" * 64
        self.assertEqual([x["record_id"] for x in rank_records(tied, query())], ["tie-a", "tie-b"])

    def test_identical_patterns_are_mmr_penalized_and_duplicate_ids_rejected(self) -> None:
        record = copy.deepcopy(self.manifest()["records"][0])
        duplicate = copy.deepcopy(record)
        duplicate["record_id"] = "structural-duplicate"
        duplicate["source"]["material_sha256"] = "d" * 64
        ranked = rank_records([record, duplicate], query())
        self.assertEqual(ranked[1]["signals"]["diversity_penalty_basis_points"], 3000)
        with self.assertRaises(ContractError) as raised:
            rank_records([record, copy.deepcopy(record)], query())
        self.assertEqual(raised.exception.code, "E_DATASET_DUPLICATE_ID")

    def test_service_is_immutable_concurrent_train_only_and_benchmark_explicit(self) -> None:
        manifest = self.manifest()
        before = copy.deepcopy(manifest)
        with ThreadPoolExecutor(max_workers=8) as pool:
            packets = list(pool.map(lambda _: retrieve_patterns_v2(manifest, query(), mode="production"), range(24)))
        self.assertTrue(all(canonical_json_bytes(packet) == canonical_json_bytes(packets[0]) for packet in packets))
        self.assertEqual(manifest, before)
        self.assertTrue(all(record["source_split"] == "train" for record in packets[0]["records"]))
        self.assertNotIn("nextjs-route-map", {record["record_id"] for record in packets[0]["records"]})
        with self.assertRaises(ContractError) as raised:
            retrieve_patterns_v2(manifest, query(), mode="benchmark")
        self.assertEqual(raised.exception.code, "E_RETRIEVAL_BENCHMARK")
        benchmark = retrieve_patterns_v2(manifest, query(), mode="benchmark", benchmark=True)
        self.assertEqual([record["record_id"] for record in benchmark["records"]], [
            "fastapi-proof-first-overview", "gin-json-api-first-run", "flask-progressive-entry",
            "django-docs-learning-route", "rails-mvc-first-run",
        ])

    def test_contract_fixture_parity_float_unknown_split_leak_and_v1_adapter(self) -> None:
        valid = json.loads((FIXTURES / "retrieval-packet-v2.valid.json").read_text())
        self.assertEqual(validate_retrieval_packet_v2(valid), valid)
        for case in json.loads((FIXTURES / "retrieval-packet-v2.invalid.json").read_text())["cases"]:
            with self.subTest(case=case["name"]), self.assertRaises(ContractError) as raised:
                validate_retrieval_packet_v2(case["payload"])
            self.assertEqual(raised.exception.code, case["code"])
        dangling = copy.deepcopy(valid)
        dangling["records"][0]["signals"]["tag_overlap_basis_points"] = 0
        with self.assertRaises(ContractError) as raised:
            validate_retrieval_packet_v2(dangling)
        self.assertEqual(raised.exception.code, "E_RETRIEVAL_REASON_DANGLING")
        duplicate = copy.deepcopy(valid)
        duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        with self.assertRaises(ContractError) as raised:
            validate_retrieval_packet_v2(duplicate)
        self.assertEqual(raised.exception.code, "E_DATASET_DUPLICATE_ID")
        adapted = adapt_v2_to_v1(valid)
        self.assertEqual(adapted["schema_version"], 1)
        self.assertEqual(adapted["records"][0]["score"], 180)
        self.assertEqual(set(adapted["records"][0]), {
            "record_id", "score", "components", "project_types", "section_intents", "tags", "pattern", "source",
        })

    def test_injected_cross_split_identity_fails_before_ranking(self) -> None:
        manifest = self.manifest()
        leaked = copy.deepcopy(manifest["records"][0])
        leaked["record_id"] = "injected-test-source"
        leaked["split"] = "test"
        manifest["records"].append(leaked)
        with self.assertRaises(ContractError) as raised:
            retrieve_patterns_v2(manifest, query(), mode="production")
        self.assertEqual(raised.exception.code, "E_DATASET_SPLIT_LEAK")


if __name__ == "__main__":
    unittest.main()
