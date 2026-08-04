from __future__ import annotations

import hashlib
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel import (
    VISUAL_DIAGNOSTIC_CODES,
    VisualDiagnostic,
    VisualGateReport,
    contract_error_from_visual_diagnostic,
    visual_diagnostic_from_contract_error,
)


class VisualDiagnosticTests(unittest.TestCase):
    def test_registry_is_closed_and_report_is_canonical(self) -> None:
        self.assertEqual(len(VISUAL_DIAGNOSTIC_CODES), 13)
        first = VisualDiagnostic(
            "E_VISUAL_GEOMETRY",
            "error",
            "$.nodes[2]",
            ("node-2",),
            "node is outside the view box",
        )
        second = VisualDiagnostic(
            "E_VISUAL_OVERLAP",
            "error",
            "$.nodes[1]",
            ("node-1", "node-3"),
            "unrelated nodes overlap",
        )
        advisory = VisualDiagnostic(
            "E_VISUAL_TEXT_FIT",
            "warning",
            None,
            (),
            "text may wrap",
        )
        report = VisualGateReport.build("spec", "scene", "svg", [first, second, advisory])
        reordered = VisualGateReport.build("spec", "scene", "svg", [advisory, second, first])

        self.assertEqual(report.status, "fail")
        self.assertEqual(
            [item.code for item in report.diagnostics],
            ["E_VISUAL_GEOMETRY", "E_VISUAL_OVERLAP", "E_VISUAL_TEXT_FIT"],
        )
        self.assertEqual(report.canonical_bytes(), reordered.canonical_bytes())
        self.assertEqual(
            hashlib.sha256(report.canonical_bytes()).hexdigest(),
            hashlib.sha256(reordered.canonical_bytes()).hexdigest(),
        )

    def test_unknown_code_is_rejected_at_value_boundary(self) -> None:
        with self.assertRaises(ContractError) as raised:
            VisualDiagnostic("E_VISUAL_UNKNOWN", "error", "$.nodes", (), "unknown")
        self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

    def test_unsorted_element_ids_are_rejected_at_value_boundary(self) -> None:
        with self.assertRaises(ContractError) as raised:
            VisualDiagnostic(
                "E_VISUAL_GEOMETRY",
                "error",
                "$.nodes",
                ("node-b", "node-a"),
                "unsorted",
            )
        self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

    def test_duplicate_diagnostics_are_rejected(self) -> None:
        diagnostic = VisualDiagnostic("E_VISUAL_PATH", "error", "$.path", (), "unsafe")
        with self.assertRaises(ContractError) as raised:
            VisualGateReport.build("spec", "scene", "svg", [diagnostic, diagnostic])
        self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

    def test_contract_error_conversion_preserves_code_and_message(self) -> None:
        error = ContractError("E_VISUAL_RESOURCE", "resource blocked")
        diagnostic = visual_diagnostic_from_contract_error(error, path="$.assets[0]")
        converted = contract_error_from_visual_diagnostic(diagnostic)
        self.assertEqual((diagnostic.code, diagnostic.message), (error.code, str(error)))
        self.assertEqual((converted.code, str(converted)), (error.code, str(error)))

    def test_values_are_immutable(self) -> None:
        diagnostic = VisualDiagnostic("E_VISUAL_PATH", "error", "$.path", (), "unsafe")
        with self.assertRaises((AttributeError, TypeError)):
            diagnostic.message = "changed"  # type: ignore[misc]
        report = VisualGateReport.build(diagnostics=[diagnostic])
        with self.assertRaises((AttributeError, TypeError)):
            report.diagnostics += (diagnostic,)  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
