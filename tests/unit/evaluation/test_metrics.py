from __future__ import annotations

import copy
import json
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.evaluation.contract import validate_advisory_metrics, validate_metric
from skill.scripts.readme_showcase.evaluation.metrics import compute_advisory_metrics


class AdvisoryMetricTests(unittest.TestCase):
    @staticmethod
    def write_inputs(root: Path) -> None:
        source = b"source evidence\n"
        fact = build_fact(
            kind="file-presence",
            path="source/README.md",
            locator=None,
            semantic_key="presence",
            value=True,
            source_bytes=source,
        )
        evidence = EvidenceGraph([fact]).to_dict()
        fact_id = fact["fact_id"]
        values = {
            "readme-plan.json": {
                "schema_version": 2,
                "mode": "readme",
                "locales": [{"tag": "en", "readme_path": "README.md"}],
                "sections": ["overview"],
                "visual_intent": "hero",
                "diagram_route": "static",
                "commands": [],
                "evidence_ids": [fact_id],
            },
            "retrieval-packet.json": {"schema_version": 1, "status": "unavailable", "records": []},
            "repository-evidence.json": evidence,
            "claim-map.json": {
                "schema_version": 2,
                "markdown_blocks": [{
                    "claim_id": "markdown:en:overview",
                    "content_sha256": "0" * 64,
                    "claim_kind": "factual",
                    "evidence_ids": [fact_id],
                    "language_pair_id": None,
                    "support_level": "direct",
                }],
                "diagram_labels": [],
            },
            "asset-manifest.json": {
                "schema_version": 2,
                "assets": [{
                    "asset_id": "hero",
                    "path": "assets/hero.png",
                    "locale": "en",
                    "language_neutral": False,
                    "provenance": {
                        "kind": "derived",
                        "path": "source/README.md",
                        "sha256": fact["source_sha256"],
                    },
                    "artifact_sha256": "1" * 64,
                    "candidate_sha256": "1" * 64,
                    "evidence_ids": [fact_id],
                }],
            },
        }
        for name, value in values.items():
            write_canonical_json_atomic(root / name, value)

    @staticmethod
    def artifacts(root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        return tuple(
            json.loads((root / name).read_text(encoding="utf-8"))
            for name in (
                "readme-plan.json",
                "retrieval-packet.json",
                "repository-evidence.json",
                "claim-map.json",
                "asset-manifest.json",
            )
        )  # type: ignore[return-value]

    def compute(self, root: Path) -> dict[str, dict[str, object]]:
        plan, retrieval, evidence, claims, assets = self.artifacts(root)
        return compute_advisory_metrics(
            plan=plan,
            retrieval=retrieval,
            evidence=evidence,
            claims=claims,
            asset_manifest=assets,
        )

    def test_low_medium_high_are_distinct_exact_integer_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            results: dict[str, dict[str, dict[str, object]]] = {}
            for quality in ("low", "medium", "high"):
                root = base / quality
                root.mkdir()
                self.write_inputs(root)
                claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
                fact_id = claims["markdown_blocks"][0]["evidence_ids"][0]
                if quality == "low":
                    claims["markdown_blocks"][0]["support_level"] = "documented-only"
                if quality == "high":
                    claims["diagram_labels"] = [{
                        **copy.deepcopy(claims["markdown_blocks"][0]),
                        "claim_id": "diagram:en:hero",
                        "content_sha256": "f" * 64,
                    }]
                write_canonical_json_atomic(root / "claim-map.json", claims)
                plan = json.loads((root / "readme-plan.json").read_text(encoding="utf-8"))
                plan["commands"] = ["python -m demo"]
                write_canonical_json_atomic(root / "readme-plan.json", plan)
                results[quality] = self.compute(root)
                self.assertEqual(
                    results[quality]["observable_commands"],
                    {
                        "basis_points": 0,
                        "covered": 0,
                        "reasons": ["command-not-observed:python -m demo"],
                        "status": "measured",
                        "total": 1,
                    },
                )
            self.assertEqual(results["low"]["claim_coverage"]["covered"], 0)
            self.assertEqual(results["medium"]["claim_coverage"]["covered"], 1)
            self.assertEqual(results["high"]["claim_coverage"]["total"], 2)
            self.assertEqual(results["medium"]["visual_provenance"]["covered"], 0)
            self.assertEqual(results["high"]["visual_provenance"]["covered"], 1)
            self.assertEqual(len({canonical_sha256(value) for value in results.values()}), 3)
            self.assertFalse(any(isinstance(value, float) for result in results.values() for value in _walk(result)))

    def test_zero_total_is_not_applicable_and_decorative_visual_is_excluded(self) -> None:
        result = compute_advisory_metrics(
            plan={"commands": [], "evidence_ids": [], "locales": [{"tag": "en", "readme_path": "README.md"}], "sections": []},
            retrieval={"records": []},
            evidence={"facts": []},
            claims={"markdown_blocks": [], "diagram_labels": []},
            asset_manifest={"assets": []},
        )
        for name in ("claim_coverage", "diagram_label_coverage", "evidence_sources", "observable_commands", "section_intents", "visual_provenance"):
            self.assertEqual(result[name], {"covered": 0, "reasons": [], "status": "not-applicable", "total": 0})
            self.assertNotIn("basis_points", result[name])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_inputs(root)
            plan, retrieval, evidence, claims, assets = self.artifacts(root)
            claims["markdown_blocks"] = []
            claims["diagram_labels"] = [{
                "claim_id": "diagram:en:hero",
                "claim_kind": "decorative",
                "evidence_ids": [plan["evidence_ids"][0]],
                "support_level": "direct",
                "language_pair_id": None,
            }]
            decorative = compute_advisory_metrics(
                plan=plan, retrieval=retrieval, evidence=evidence,
                claims=claims, asset_manifest=assets,
            )
            self.assertEqual(decorative["visual_provenance"], {"covered": 0, "reasons": [], "status": "not-applicable", "total": 0})

    def test_explicit_locale_pairs_and_neutral_assets_do_not_infer_filename_locale(self) -> None:
        fact_id = "file:" + "a" * 64
        claims = []
        for locale in ("en", "ja"):
            claims.append({
                "claim_id": f"markdown:{locale}:overview",
                "claim_kind": "factual",
                "evidence_ids": [fact_id],
                "support_level": "direct",
                "language_pair_id": "overview",
            })
        result = compute_advisory_metrics(
            plan={
                "commands": [], "evidence_ids": [fact_id], "sections": [],
                "locales": [
                    {"tag": "en", "readme_path": "docs/日本語.md"},
                    {"tag": "ja", "readme_path": "docs/readme-zh.md"},
                ],
            },
            retrieval={"records": []},
            evidence={"facts": [{"fact_id": fact_id}]},
            claims={"markdown_blocks": claims, "diagram_labels": []},
            asset_manifest={"assets": [{
                "asset_id": "neutral", "path": "assets/deceptive-zh.png",
                "language_neutral": True, "evidence_ids": [fact_id], "provenance": {},
            }]},
        )
        self.assertEqual(result["language_truth_pairs"]["covered"], 1)
        self.assertEqual(result["language_truth_pairs"]["total"], 1)
        self.assertEqual(result["visual_provenance"]["total"], 1)

    def test_unique_normative_evidence_ids_not_truth_ids_or_raw_uses(self) -> None:
        first = "file:" + "a" * 64
        second = "config:" + "b" * 64
        result = compute_advisory_metrics(
            plan={"commands": [], "evidence_ids": [first, second], "locales": [{"tag": "en", "readme_path": "README.md"}], "sections": []},
            retrieval={"records": []},
            evidence={"facts": [{"fact_id": first}, {"fact_id": second}]},
            claims={"markdown_blocks": [
                {"claim_id": "markdown:en:a", "claim_kind": "factual", "evidence_ids": [first], "support_level": "direct", "language_pair_id": None},
                {"claim_id": "markdown:en:b", "claim_kind": "factual", "evidence_ids": [first], "support_level": "direct", "language_pair_id": None},
            ], "diagram_labels": []},
            asset_manifest={"assets": []},
        )
        self.assertEqual(result["evidence_sources"], {
            "basis_points": 5000, "covered": 1,
            "reasons": [f"unused-evidence:{second}"], "status": "measured", "total": 2,
        })
        reversed_result = compute_advisory_metrics(
            plan={"commands": [], "evidence_ids": [second, first], "locales": [{"tag": "en", "readme_path": "README.md"}], "sections": []},
            retrieval={"records": []},
            evidence={"facts": [{"fact_id": second}, {"fact_id": first}]},
            claims={"markdown_blocks": list(reversed([
                {"claim_id": "markdown:en:a", "claim_kind": "factual", "evidence_ids": [first], "support_level": "direct", "language_pair_id": None},
                {"claim_id": "markdown:en:b", "claim_kind": "factual", "evidence_ids": [first], "support_level": "direct", "language_pair_id": None},
            ])), "diagram_labels": []},
            asset_manifest={"assets": []},
        )
        self.assertEqual(canonical_json_bytes(result), canonical_json_bytes(reversed_result))

    def test_duplicate_and_dangling_evidence_fail_closed(self) -> None:
        fact_id = "file:" + "a" * 64
        base = {
            "plan": {"commands": [], "evidence_ids": [fact_id], "locales": [{"tag": "en", "readme_path": "README.md"}], "sections": []},
            "retrieval": {"records": []},
            "evidence": {"facts": [{"fact_id": fact_id}]},
            "claims": {"markdown_blocks": [], "diagram_labels": []},
            "asset_manifest": {"assets": []},
        }
        duplicate = copy.deepcopy(base)
        duplicate["plan"]["evidence_ids"].append(fact_id)
        dangling = copy.deepcopy(base)
        dangling["claims"]["markdown_blocks"] = [{
            "claim_id": "markdown:en:x", "claim_kind": "factual",
            "evidence_ids": ["config:" + "b" * 64],
            "support_level": "direct", "language_pair_id": None,
        }]
        for payload in (duplicate, dangling):
            with self.assertRaises(ContractError) as raised:
                compute_advisory_metrics(**payload)
            self.assertEqual(raised.exception.code, "E_EVALUATION_METRIC")

    def test_contract_rejects_float_overflow_and_inconsistent_metric(self) -> None:
        for payload, code in (
            ({"covered": 2, "total": 1, "status": "measured", "basis_points": 20000, "reasons": []}, "E_EVALUATION_METRIC"),
            ({"covered": 1.0, "total": 1, "status": "measured", "basis_points": 10000, "reasons": []}, "E_SCHEMA_FLOAT"),
            ({"covered": 2**63, "total": 2**63, "status": "measured", "basis_points": 10000, "reasons": []}, "E_EVALUATION_METRIC"),
            ({"covered": 1, "total": 2, "status": "measured", "basis_points": 5001, "reasons": []}, "E_EVALUATION_METRIC"),
        ):
            with self.subTest(code=code), self.assertRaises(ContractError) as raised:
                validate_metric(payload, "probe")
            self.assertEqual(raised.exception.code, code)

    def test_input_order_mutation_and_concurrency_are_stable(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.write_inputs(root)
            values = self.artifacts(root)
            before = canonical_json_bytes(list(values))
            outputs: list[bytes] = []
            def run() -> None:
                plan, retrieval, evidence, claims, assets = copy.deepcopy(values)
                outputs.append(canonical_json_bytes(compute_advisory_metrics(
                    plan=plan, retrieval=retrieval, evidence=evidence,
                    claims=claims, asset_manifest=assets,
                )))
            threads = [threading.Thread(target=run) for _ in range(32)]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(len(set(outputs)), 1)
            self.assertEqual(canonical_json_bytes(list(values)), before)
            validate_advisory_metrics(json.loads(outputs[0]))


def _walk(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value, *[child for item in value.values() for child in _walk(item)]]
    if isinstance(value, list):
        return [value, *[child for item in value for child in _walk(item)]]
    return [value]


if __name__ == "__main__":
    unittest.main()
