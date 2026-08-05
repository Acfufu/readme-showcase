from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.evaluation import validate_evaluation_report_v3
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.evaluation.contract import validate_advisory_metrics, validate_metric
from skill.scripts.readme_showcase.evaluation.metrics import compute_advisory_metrics
from skill.scripts.pipeline_core import evaluate_generated_bundle
from skill.scripts.readme_showcase.evaluation.legacy import _v3_compiled_metrics
from skill.scripts.readme_showcase.visual_kernel.fingerprint import build_layered_fingerprint
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.reader import load_compiled_visual
from tests.contract.test_bundle_v3 import BundleV3ContractTests


class AdvisoryMetricTests(unittest.TestCase):
    @staticmethod
    def rewrite_gate_noncanonical(
        root: Path,
        bundle: dict[str, Any],
        *,
        canonical: bool = False,
        failed: bool = False,
    ) -> None:
        gate_path = root / "compiled/gates/en/desktop.json"
        gate_value = json.loads(gate_path.read_bytes())
        if failed:
            gate_value["status"] = "fail"
            gate_value["diagnostics"] = [{
                "code": "E_VISUAL_TEXT_FIT",
                "severity": "error",
                "path": "$.svg.title",
                "element_ids": [],
                "message": "forced gate failure",
            }]
        gate_raw = (
            canonical_json_bytes(gate_value)
            if canonical
            else json.dumps(gate_value, separators=(", ", ": ")).encode("utf-8")
        )
        gate_path.write_bytes(gate_raw)
        gate_sha256 = hashlib.sha256(gate_raw).hexdigest()

        inventory = json.loads((root / "compiled/inventory.json").read_bytes())
        layers = inventory["layers"]
        gates = copy.deepcopy(layers[4]["records"])
        timelines = copy.deepcopy(layers[5]["records"])
        interactions = copy.deepcopy(layers[6]["records"])
        artifacts = copy.deepcopy(layers[7]["records"])
        for record in gates:
            if record["locale"] == "en" and record["variant"] == "desktop":
                record["sha256"] = gate_sha256
        for record in timelines:
            if record["locale"] == "en" and record["variant"] == "desktop":
                record["prior_sha256"] = gate_sha256
        for record in artifacts:
            if record["path"] == "compiled/gates/en/desktop.json":
                record["sha256"] = gate_sha256
        reports_prior = canonical_sha256({
            "gates": gates,
            "timelines": timelines,
            "interactions": interactions,
        })
        for record in artifacts:
            record["prior_sha256"] = reports_prior
        fingerprint = build_layered_fingerprint(
            layers[0]["sha256"], layers[1]["records"], layers[2]["sha256"],
            layers[3]["values"], gates, timelines, interactions, artifacts,
        )
        inventory_raw = canonical_json_bytes(fingerprint.as_dict())
        (root / "compiled/inventory.json").write_bytes(inventory_raw)
        inventory_sha256 = hashlib.sha256(inventory_raw).hexdigest()

        manifest = json.loads((root / "asset-manifest.json").read_bytes())
        for record in manifest["compiled"]["gates"]:
            if record["locale"] == "en" and record["variant"] == "desktop":
                record["sha256"] = gate_sha256
        for asset in manifest["assets"]:
            if asset["locale"] == "en" and asset["variant"] == "desktop":
                asset["gate_sha256"] = gate_sha256
        manifest["compiled"]["inventory"]["sha256"] = inventory_sha256
        manifest_raw = canonical_json_bytes(manifest)
        (root / "asset-manifest.json").write_bytes(manifest_raw)

        bundle["compiled"]["inventory"]["sha256"] = inventory_sha256
        bundle["compiled"]["fingerprint"] = fingerprint.inventory_sha256
        bundle["artifacts"]["asset_manifest"]["sha256"] = hashlib.sha256(manifest_raw).hexdigest()

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

    def test_compiled_bundle_v3_evaluation_passes_all_hard_measures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)
            before = {
                path: (root / path).read_bytes()
                for path in (
                    "compiled/inventory.json",
                    "compiled/gates/en/desktop.json",
                    "compiled/gates/en/mobile.json",
                )
            }
            first = evaluate_generated_bundle(bundle, root)
            second = evaluate_generated_bundle(copy.deepcopy(bundle), root)
            self.assertEqual(first["schema_version"], 3)
            self.assertEqual(first["status"], "pass")
            self.assertEqual(first["hard_gate"], {"status": "pass", "findings": []})
            for name, compiled_metric in first["compiled"].items():
                with self.subTest(metric=name):
                    self.assertEqual(compiled_metric["basis_points"], 10_000)
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first, validate_evaluation_report_v3(first))
            self.assertEqual(
                {path: (root / path).read_bytes() for path in before},
                before,
            )

    def test_compiled_bundle_v3_evaluation_fails_closed_for_reader_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)
            stale = copy.deepcopy(bundle)
            stale["compiled"]["fingerprint"] = "0" * 64
            report = evaluate_generated_bundle(stale, root)
            self.assertEqual(report["schema_version"], 3)
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["hard_gate"]["status"], "fail")
            self.assertEqual(report["hard_gate"]["findings"][0]["code"], "E_VISUAL_FINGERPRINT")
            self.assertEqual(report, validate_evaluation_report_v3(report))

            missing = copy.deepcopy(bundle)
            (root / "compiled/scenes/en/mobile.json").unlink()
            missing_report = evaluate_generated_bundle(missing, root)
            self.assertEqual(missing_report["status"], "fail")
            self.assertIn(
                missing_report["hard_gate"]["findings"][0]["code"],
                {"E_PATH", "E_VISUAL_FINGERPRINT", "E_VISUAL_PATH"},
            )

    def test_compiled_bundle_v3_rejects_author_and_gate_byte_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)

            invalid_candidate = copy.deepcopy(bundle)
            invalid_candidate["candidate"]["readmes"][0]["path"] = "missing.md"
            invalid_report = evaluate_generated_bundle(invalid_candidate, root)
            self.assertEqual(invalid_report["status"], "fail")
            self.assertEqual(invalid_report["hard_gate"]["status"], "fail")

            noncanonical = copy.deepcopy(bundle)
            self.rewrite_gate_noncanonical(root, noncanonical)
            noncanonical_report = evaluate_generated_bundle(noncanonical, root)
            self.assertEqual(noncanonical_report["status"], "fail")
            self.assertIn(
                "E_VISUAL_DETERMINISM",
                {item["code"] for item in noncanonical_report["hard_gate"]["findings"]},
            )

    def test_compiled_bundle_v3_unobserved_behavior_counts_planned_commands(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)
            plan_path = root / "readme-plan.json"
            plan = json.loads(plan_path.read_bytes())
            plan["commands"] = ["printf hello"]
            plan_raw = canonical_json_bytes(plan)
            plan_path.write_bytes(plan_raw)
            bundle["artifacts"]["plan"]["sha256"] = hashlib.sha256(plan_raw).hexdigest()

            report = evaluate_generated_bundle(bundle, root)
            self.assertEqual(report["status"], "pass")
            self.assertFalse(report["behavior_required"])
            self.assertEqual(report["behavior"]["total_commands"], 1)
            self.assertEqual(report["advisory"]["observable_commands"]["total"], 1)

    def test_compiled_bundle_v3_failed_gate_is_a_hard_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)
            self.rewrite_gate_noncanonical(root, bundle, canonical=True, failed=True)

            report = evaluate_generated_bundle(bundle, root)
            self.assertEqual(report["status"], "fail")
            self.assertEqual(report["hard_gate"]["status"], "fail")
            self.assertEqual(report["compiled"]["gate_pass"]["covered"], 1)
            self.assertEqual(report["compiled"]["gate_pass"]["total"], 2)

    def test_compiled_bundle_v3_malformed_gate_does_not_count_variant_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = BundleV3ContractTests("runTest").make_bundle(root)
            loaded = load_compiled_visual(root, bundle)
            malformed_gate = json.loads(loaded.artifacts["compiled/gates/en/desktop.json"])
            malformed_gate["status"] = "invalid"
            artifacts = dict(loaded.artifacts)
            artifacts["compiled/gates/en/desktop.json"] = canonical_json_bytes(malformed_gate)
            malformed_loaded = SimpleNamespace(
                artifacts=artifacts,
                inventory_sha256=loaded.inventory_sha256,
            )
            spec_payload = json.loads((root / "visual-spec.json").read_bytes())
            evidence = json.loads((root / "repository-evidence.json").read_bytes())
            spec = validate_visual_spec(spec_payload, evidence_graph=evidence)
            claims = json.loads((root / "claim-map.json").read_bytes())

            metrics, findings = _v3_compiled_metrics(
                malformed_loaded,
                bundle,
                spec,
                claims,
                evidence,
            )

            self.assertEqual(metrics["variant_completeness"], {
                "basis_points": 5000,
                "covered": 1,
                "reasons": ["variant-incomplete:en/desktop"],
                "status": "measured",
                "total": 2,
            })
            self.assertEqual(findings[0]["code"], "E_SCHEMA_VALUE")


def _walk(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return [value, *[child for item in value.values() for child in _walk(item)]]
    if isinstance(value, list):
        return [value, *[child for item in value for child in _walk(item)]]
    return [value]


if __name__ == "__main__":
    unittest.main()
