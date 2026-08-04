from __future__ import annotations

import copy
from dataclasses import replace
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.diagnostics import VisualDiagnostic, VisualGateReport
from skill.scripts.readme_showcase.visual_kernel.gates import (
    run_visual_gates,
    validate_visual_gate_report,
)
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec


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


if __name__ == "__main__":
    unittest.main()
