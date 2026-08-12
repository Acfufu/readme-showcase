from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evaluation import validate_evaluation_report_v3
from skill.scripts.readme_showcase.contracts.evidence import build_fact, compute_graph_sha256, validate_fact
from skill.scripts.readme_showcase.evaluation.identity import (
    collect_visual_tokens,
    evaluate_identity_gate,
    identity_check,
)
from skill.scripts.readme_showcase.evaluation.legacy import evaluate_generated_bundle
from skill.scripts.readme_showcase.scanner.visual import extract_visual_tokens, svg_tokens


PRODUCT_TOKENS = {
    "palette": ["#0a1f44", "#22c55e", "#eab308"],
    "typography": ["Inter", "system-ui"],
    "sources": ["visual:assets/logo.svg"],
}

PRODUCT_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="120" height="120">'
    '<rect width="120" height="120" fill="#0a1f44"/>'
    '<text font-family="Inter" fill="#22c55e">Demo</text>'
    "</svg>"
)

OVERRIDE = {"reason": "brand refresh approved by design lead", "approved_by": "alice@example.com"}


class IdentityGateTests(unittest.TestCase):
    def test_non_product_color_conflicts_with_product_value_evidence(self) -> None:
        result = identity_check(
            {"palette": ["#ff0000"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
        )
        self.assertFalse(result["pass"])
        self.assertFalse(result["override"])
        self.assertEqual(len(result["conflicts"]), 1)
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["token"], "palette")
        self.assertEqual(conflict["chosen"], "#ff0000")
        self.assertEqual(conflict["product"], PRODUCT_TOKENS["palette"])
        self.assertIn("#ff0000", conflict["message"])
        self.assertIn("#0a1f44", conflict["message"])

    def test_product_palette_candidate_passes(self) -> None:
        result = identity_check(
            {"palette": ["#0a1f44", "#22c55e"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
        )
        self.assertTrue(result["pass"])
        self.assertEqual(result["conflicts"], [])

    def test_neutral_scaffolding_colors_never_conflict(self) -> None:
        result = identity_check(
            {"palette": ["#000000", "#ffffff", "#808080"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
        )
        self.assertTrue(result["pass"])
        self.assertEqual(result["conflicts"], [])

    def test_system_fonts_never_conflict(self) -> None:
        result = identity_check(
            {"palette": ["#0a1f44"], "typography": ["ui-monospace", "sans-serif"]},
            PRODUCT_TOKENS,
        )
        self.assertTrue(result["pass"])

    def test_github_default_font_stack_never_conflicts(self) -> None:
        stack = ["-apple-system", "BlinkMacSystemFont", "Segoe UI", "sans-serif"]
        self.assertTrue(identity_check({"palette": ["#ffffff"], "typography": stack}, {})["pass"])
        self.assertTrue(
            identity_check({"palette": ["#0a1f44"], "typography": stack}, PRODUCT_TOKENS)["pass"]
        )

    def test_non_system_font_outside_product_typography_conflicts(self) -> None:
        result = identity_check(
            {"palette": ["#0a1f44"], "typography": ["Comic Sans MS"]},
            PRODUCT_TOKENS,
        )
        self.assertFalse(result["pass"])
        conflict = result["conflicts"][0]
        self.assertEqual(conflict["token"], "typography")
        self.assertEqual(conflict["chosen"], "Comic Sans MS")
        self.assertEqual(conflict["product"], PRODUCT_TOKENS["typography"])

    def test_missing_product_tokens_passes_open_with_explanation(self) -> None:
        result = evaluate_identity_gate(
            {"palette": ["#ff0000"], "typography": ["system-ui"]},
            {},
        )
        self.assertTrue(result["pass"])
        self.assertFalse(result["override"])
        self.assertIsNone(result["identity_override"])
        self.assertIn("no visual identity", result["evidence"])

    def test_conflict_without_override_fails_closed(self) -> None:
        result = evaluate_identity_gate(
            {"palette": ["#ff0000"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
        )
        self.assertFalse(result["pass"])
        self.assertFalse(result["override"])
        self.assertIsNone(result["identity_override"])

    def test_written_reason_override_turns_conflict_into_pass(self) -> None:
        result = evaluate_identity_gate(
            {"palette": ["#ff0000"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
            override=OVERRIDE,
        )
        self.assertTrue(result["pass"])
        self.assertTrue(result["override"])
        self.assertEqual(result["identity_override"], OVERRIDE)
        self.assertEqual(len(result["conflicts"]), 1)

    def test_override_without_conflicts_is_not_recorded(self) -> None:
        result = evaluate_identity_gate(
            {"palette": ["#0a1f44"], "typography": ["system-ui"]},
            PRODUCT_TOKENS,
            override=OVERRIDE,
        )
        self.assertTrue(result["pass"])
        self.assertFalse(result["override"])
        self.assertIsNone(result["identity_override"])

    def test_malformed_override_is_rejected(self) -> None:
        for malformed in ({"reason": "x"}, {"approved_by": "y"}, {"reason": "", "approved_by": "y"}, "yes"):
            with self.subTest(override=malformed):
                with self.assertRaises(ContractError) as caught:
                    evaluate_identity_gate(
                        {"palette": ["#ff0000"], "typography": ["system-ui"]},
                        PRODUCT_TOKENS,
                        override=malformed,
                    )
                self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")


class VisualScanExtractionTests(unittest.TestCase):
    def test_scan_extracts_visual_facts_from_svg_and_design_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "assets").mkdir()
            (root / "assets" / "logo.svg").write_text(PRODUCT_SVG, encoding="utf-8")
            (root / "theme.json").write_text(
                json.dumps({"colors": {"primary": "#0a1f44", "accent": "#22c55e"}}),
                encoding="utf-8",
            )
            facts = extract_visual_tokens(root)
            self.assertGreaterEqual(len(facts), 2)
            for fact in facts:
                self.assertEqual(validate_fact(fact), fact)
                self.assertEqual(fact["kind"], "visual")
                self.assertEqual(fact["confidence"], "derived")
                self.assertIn("derivation", fact)
                value = fact["value"]
                self.assertIn("palette", value)
                self.assertIn("typography", value)
                self.assertIn("#0a1f44", value["palette"])
                self.assertIn("#22c55e", value["palette"])
            typography = sorted(
                family
                for fact in facts
                for family in fact["value"]["typography"]
            )
            self.assertIn("Inter", typography)
            sources = [fact["source"]["path"] for fact in facts]
            self.assertIn("assets/logo.svg", sources)
            self.assertIn("theme.json", sources)
            for fact in facts:
                on_disk = (root / fact["source"]["path"]).read_bytes()
                self.assertEqual(fact["source_sha256"], hashlib.sha256(on_disk).hexdigest())

    def test_scan_without_visual_materials_extracts_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            self.assertEqual(extract_visual_tokens(root), [])

    def test_svg_tokens_are_canonical_lowercase_and_deduped(self) -> None:
        tokens = svg_tokens(
            '<svg><rect fill="#0A1F44"/><rect fill="#0a1f44"/>'
            '<text font-family="Inter, system-ui">x</text></svg>'
        )
        self.assertEqual(tokens["palette"], ["#0a1f44"])
        self.assertEqual(tokens["typography"], ["Inter", "system-ui"])


class IdentityReportContractTests(unittest.TestCase):
    def _report(self, **changes: object) -> dict[str, object]:
        with open("tests/fixtures/contracts/evaluation-report-v3.valid.json", encoding="utf-8") as handle:
            report = json.load(handle)
        report.update(changes)
        return report

    def test_valid_report_accepts_null_identity_override(self) -> None:
        self.assertEqual(validate_evaluation_report_v3(self._report())["identity_override"], None)

    def test_valid_report_accepts_written_identity_override(self) -> None:
        report = self._report(identity_override=OVERRIDE)
        self.assertEqual(validate_evaluation_report_v3(report)["identity_override"], OVERRIDE)

    def test_report_rejects_identity_override_with_extra_field(self) -> None:
        report = self._report(identity_override={**OVERRIDE, "extra": True})
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")

    def test_report_rejects_missing_identity_override(self) -> None:
        report = self._report()
        del report["identity_override"]
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")

    def test_report_rejects_empty_override_reason(self) -> None:
        report = self._report(identity_override={"reason": "", "approved_by": "alice"})
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")


class IdentityBundleEvaluationTests(unittest.TestCase):
    """End-to-end evaluate: candidate visual tokens vs evidence visual facts."""

    def _bundle_with_visual_facts(self, root: Path) -> dict[str, object]:
        from tests.contract.test_bundle_v3 import BundleV3ContractTests
        from tests.unit.visual_kernel.test_scene import EVIDENCE
        from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph

        bundle = BundleV3ContractTests().make_bundle(root)
        visual = build_fact(
            kind="visual",
            path="assets/logo.svg",
            locator={"line_start": 1, "line_end": 1},
            semantic_key="visual:assets/logo.svg",
            value={"palette": ["#123456", "#abcdef"], "typography": ["Inter"]},
            source_bytes=PRODUCT_SVG.encode("utf-8"),
            confidence="derived",
            derivation="visual tokens derived from project logo and design tokens",
        )
        graph = EvidenceGraph([*EVIDENCE["facts"], visual]).to_dict()
        from skill.scripts.pipeline_contracts import canonical_json_bytes

        raw = canonical_json_bytes(graph)
        (root / "repository-evidence.json").write_bytes(raw)
        import hashlib

        bundle["artifacts"]["evidence"]["sha256"] = hashlib.sha256(raw).hexdigest()  # type: ignore[index]
        return bundle

    def test_conflicting_candidate_identity_fails_evaluation_with_finding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle_with_visual_facts(root)
            report = evaluate_generated_bundle(bundle, root)
            self.assertEqual(report["status"], "fail")
            self.assertIsNone(report["identity_override"])
            codes = [finding["code"] for finding in report["hard_gate"]["findings"]]
            self.assertIn("E_IDENTITY_MATCH", codes)
            message = next(
                finding["message"]
                for finding in report["hard_gate"]["findings"]
                if finding["code"] == "E_IDENTITY_MATCH"
            )
            self.assertIn("#123456", message)

    def test_written_override_passes_and_records_identity_override(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle_with_visual_facts(root)
            report = evaluate_generated_bundle(bundle, root, identity_override=OVERRIDE)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["identity_override"], OVERRIDE)

    def test_collected_visual_tokens_merge_facts_from_evidence_graph(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._bundle_with_visual_facts(root)
            evidence = json.loads((root / "repository-evidence.json").read_text(encoding="utf-8"))
            tokens = collect_visual_tokens(evidence)
            self.assertEqual(tokens["palette"], ["#123456", "#abcdef"])
            self.assertEqual(tokens["typography"], ["Inter"])
            self.assertEqual(tokens["sources"], ["visual:assets/logo.svg"])


if __name__ == "__main__":
    unittest.main()
