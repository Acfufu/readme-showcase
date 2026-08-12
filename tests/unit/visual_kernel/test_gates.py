from __future__ import annotations

import copy
from dataclasses import replace
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.diagnostics import (
    VISUAL_DIAGNOSTIC_CODES,
    CountConsistency,
    VisualDiagnostic,
    VisualGateReport,
    validate_count_consistency,
)
from skill.scripts.readme_showcase.visual_kernel.gates import (
    count_consistency_gate,
    run_visual_gates,
    validate_visual_gate_report,
)
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel import scene as scene_module
from skill.scripts.readme_showcase.visual_kernel.scene import ScenePrimitive
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, EVIDENCE_IDS, _build, _spec


class VisualGateTests(unittest.TestCase):
    def _inputs(self, kind: str = "flow", variant: str = "desktop") -> tuple[object, object, object, object, object, bytes]:
        cjk = variant == "mobile"
        payload = _spec(kind, swimlanes=kind == "swimlane", cjk=cjk)
        plan = normalize_visual_spec(payload, EVIDENCE)
        scene = _build(kind, variant, cjk=cjk)
        theme = resolve_theme()
        timeline = derive_timeline(plan)
        # The shared scene fixture includes a reverse edge for geometry tests;
        # derive the keyboard projection from its forward-only semantic view.
        interaction_payload = copy.deepcopy(payload)
        interaction_payload["edges"] = interaction_payload["edges"][:1]  # type: ignore[index]
        interaction = derive_interaction(normalize_visual_spec(interaction_payload, EVIDENCE))
        return (
            payload,
            scene,
            theme,
            timeline,
            interaction,
            serialize_svg(scene, theme),
        )

    def test_all_four_intents_pass_per_variant(self) -> None:
        for kind in ("architecture", "flow", "swimlane", "sequence"):
            for variant in ("desktop", "mobile"):
                with self.subTest(kind=kind, variant=variant):
                    spec, scene, theme, timeline, interaction, svg = self._inputs(kind, variant)
                    report = run_visual_gates(
                        spec,
                        scene,
                        theme,
                        timeline,
                        interaction,
                        svg,
                        evidence_graph=EVIDENCE,
                    )
                    self.assertEqual(report.status, "pass")
                    self.assertEqual(report.diagnostics, ())
                    self.assertEqual(len(report.spec_sha256), 64)

    def test_diagnostics_are_canonical_under_permutation(self) -> None:
        first_diagnostic = VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.description", (), "missing")
        second_diagnostic = VisualDiagnostic("E_VISUAL_DETERMINISM", "error", "$.interaction.focus_order", (), "drift")
        first = VisualGateReport.build("a" * 64, "b" * 64, "c" * 64, [first_diagnostic, second_diagnostic])
        second = VisualGateReport.build("a" * 64, "b" * 64, "c" * 64, [second_diagnostic, first_diagnostic])
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_combined_content_failures_are_aggregated(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        interaction_bad = copy.deepcopy(interaction.as_dict())
        interaction_bad["focus_order"] = list(reversed(interaction_bad["focus_order"]))
        scene_bad = replace(
            scene,
            primitives=tuple(
                replace(item, widths=(1000,)) if item.kind == "text" and item.source_id == "a" else item
                for item in scene.primitives
            ),
        )
        svg_bad = serialize_svg(scene_bad, theme).replace(b"Static desktop visual scene for locale en.", b"")
        report = run_visual_gates(spec, scene_bad, theme, timeline, interaction_bad, svg_bad, evidence_graph=EVIDENCE)
        self.assertEqual(report.status, "fail")
        self.assertIn("E_VISUAL_DETERMINISM", {item.code for item in report.diagnostics})
        self.assertIn("E_VISUAL_TEXT_FIT", {item.code for item in report.diagnostics})
        self.assertIn("$.scene", {item.path for item in report.diagnostics})
        self.assertIn("$.interaction.focus_order", {item.path for item in report.diagnostics})
        self.assertIn("$.svg.description", {item.path for item in report.diagnostics})

    def test_unsafe_svg_aborts_before_report(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        with self.assertRaises(ContractError) as raised:
            run_visual_gates(
                spec,
                scene,
                theme,
                timeline,
                interaction,
                svg.replace(b"<text", b"<script/><text"),
                evidence_graph=EVIDENCE,
            )
        self.assertEqual(raised.exception.code, "E_VISUAL_SVG_SECURITY")

    def test_report_validator_is_closed_and_requires_hashes(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        report = run_visual_gates(spec, scene, theme, timeline, interaction, svg, evidence_graph=EVIDENCE)
        payload = report.as_dict()
        self.assertEqual(validate_visual_gate_report(payload).canonical_bytes(), report.canonical_bytes())
        payload["spec_sha256"] = "bad"
        with self.assertRaises(ContractError) as raised:
            validate_visual_gate_report(payload)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")
        failed = VisualGateReport.build(
            report.spec_sha256,
            report.scene_sha256,
            report.svg_sha256,
            [
                VisualDiagnostic("E_VISUAL_TEXT_FIT", "error", "$.svg.description", (), "missing"),
                VisualDiagnostic("E_VISUAL_DETERMINISM", "error", "$.interaction.focus_order", (), "drift"),
            ],
        )
        payload = failed.as_dict()
        payload["diagnostics"] = list(reversed(payload["diagnostics"]))
        with self.assertRaises(ContractError) as raised:
            validate_visual_gate_report(payload)
        self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

    def test_report_carries_count_consistency_projection(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        report = run_visual_gates(spec, scene, theme, timeline, interaction, svg, evidence_graph=EVIDENCE)
        self.assertIsNotNone(report.count_consistency)
        assert report.count_consistency is not None
        self.assertIs(report.count_consistency.pass_, True)
        self.assertEqual(report.count_consistency.scene_count, report.count_consistency.claim_count)
        self.assertLessEqual(report.count_consistency.claim_count, report.count_consistency.inventory_count)
        self.assertEqual(report.count_consistency.mismatches, ())
        payload = report.as_dict()
        self.assertEqual(validate_visual_gate_report(payload).canonical_bytes(), report.canonical_bytes())

    def test_scene_claim_count_drift_fails_the_count_gate(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        phantom = ScenePrimitive(
            "rect",
            "z",
            "z",
            (EVIDENCE_IDS[0],),
            "nodes",
            2,
            100,
            300,
            100,
            50,
        )
        primitives = list(scene.primitives)
        primitives.append(phantom)
        primitives.sort(key=lambda item: (scene_module._LAYER_INDEX[item.layer], item.z, scene_module._id_key(item.id)))
        scene_bad = replace(scene, primitives=tuple(primitives))
        svg_bad = serialize_svg(scene_bad, theme)
        report = run_visual_gates(
            spec,
            scene_bad,
            theme,
            timeline,
            interaction,
            svg_bad,
            evidence_graph=EVIDENCE,
        )
        self.assertEqual(report.status, "fail")
        self.assertIn("E_VISUAL_COUNT", {item.code for item in report.diagnostics})
        assert report.count_consistency is not None
        self.assertIs(report.count_consistency.pass_, False)
        self.assertIn("scene:5 != claim:4", report.count_consistency.mismatches)

    def test_count_gate_code_is_registered(self) -> None:
        self.assertIn("E_VISUAL_COUNT", VISUAL_DIAGNOSTIC_CODES)

    def test_count_consistency_projection_validates_as_closed_object(self) -> None:
        spec, scene, theme, timeline, interaction, svg = self._inputs()
        report = run_visual_gates(spec, scene, theme, timeline, interaction, svg, evidence_graph=EVIDENCE)
        payload = report.as_dict()
        payload["count_consistency"]["mystery"] = True
        with self.assertRaises(ContractError) as raised:
            validate_visual_gate_report(payload)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")
        passing = CountConsistency(True, 4, 4, 7, ())
        self.assertTrue(passing.as_dict()["pass"])
        self.assertEqual(passing.as_dict()["mismatches"], [])


class CountConsistencyGateTests(unittest.TestCase):
    """Reverse inventory gate: rendered scene counts must equal claim counts
    and both must fit the evidence inventory."""

    def test_equal_counts_within_inventory_pass(self) -> None:
        result = count_consistency_gate(8, 8, 20)
        self.assertIs(result["pass"], True)
        self.assertEqual(result["mismatches"], [])
        self.assertEqual(result["sampled"], [])

    def test_count_gate_reports_sampled_fields_structurally(self) -> None:
        result = count_consistency_gate(scene_node_count=5, claim_count=5, evidence_inventory=3)
        self.assertFalse(result["pass"])
        self.assertEqual(result["sampled"], ["claim", "scene"])

    def test_count_gate_sampled_empty_when_within_inventory(self) -> None:
        result = count_consistency_gate(scene_node_count=2, claim_count=2, evidence_inventory=3)
        self.assertTrue(result["pass"])
        self.assertEqual(result["sampled"], [])

    def test_count_consistency_round_trips_sampled_field(self) -> None:
        value = validate_count_consistency({
            "pass": False,
            "scene_count": 5,
            "claim_count": 5,
            "inventory_count": 3,
            "mismatches": ["claim:5 > inventory:3 (sample)", "scene:5 > inventory:3 (sample)"],
            "sampled": ["claim", "scene"],
        })
        self.assertEqual(value.sampled, ("claim", "scene"))
        self.assertEqual(value.as_dict()["sampled"], ["claim", "scene"])

    def test_scene_claim_mismatch_fails(self) -> None:
        result = count_consistency_gate(8, 20, 30)
        self.assertIs(result["pass"], False)
        self.assertIn("scene:8 != claim:20", result["mismatches"])

    def test_claim_exceeding_inventory_marks_sample(self) -> None:
        result = count_consistency_gate(8, 20, 12)
        self.assertIs(result["pass"], False)
        self.assertIn("claim:20 > inventory:12 (sample)", result["mismatches"])

    def test_scene_exceeding_inventory_marks_sample(self) -> None:
        result = count_consistency_gate(30, 20, 12)
        self.assertIs(result["pass"], False)
        self.assertIn("scene:30 > inventory:12 (sample)", result["mismatches"])
        self.assertIn("claim:20 > inventory:12 (sample)", result["mismatches"])

    def test_rejects_non_integer_counts(self) -> None:
        with self.assertRaises(ContractError) as raised:
            count_consistency_gate("8", 8, 20)
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

    def test_rejects_negative_counts(self) -> None:
        with self.assertRaises(ContractError) as raised:
            count_consistency_gate(-1, 8, 20)
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")


if __name__ == "__main__":
    unittest.main()
