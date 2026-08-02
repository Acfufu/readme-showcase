from __future__ import annotations

import hashlib
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.diagnostics import Diagnostic, DiagnosticReport
from skill.scripts.readme_showcase.errors import (
    SECURITY_CODES,
    contract_error_from_diagnostic,
    diagnostic_from_contract_error,
)


class DiagnosticTests(unittest.TestCase):
    def test_report_order_deduplication_and_hash_are_deterministic(self) -> None:
        first = Diagnostic("E_README_LANGUAGE", "error", "content", "language", "README.md", 8)
        second = Diagnostic("W_HINT", "warning", "editorial", "hint")
        third = Diagnostic("E_README_COMMAND", "error", "content", "command", "README.md", 4)

        report = DiagnosticReport.build([first, second, first, third])
        reordered = DiagnosticReport.build([third, first, second])

        self.assertEqual(report.status, "fail")
        self.assertEqual(
            [item.code for item in report.diagnostics],
            ["E_README_COMMAND", "E_README_LANGUAGE", "W_HINT"],
        )
        self.assertEqual(report.canonical_bytes(), reordered.canonical_bytes())
        self.assertEqual(
            report.sha256(),
            hashlib.sha256(report.canonical_bytes()).hexdigest(),
        )

    def test_only_exact_content_allowlist_is_aggregated(self) -> None:
        error = ContractError("E_README_COMMAND", "missing command")
        diagnostic = diagnostic_from_contract_error(error, path="README.md", line=12)

        self.assertEqual(diagnostic.category, "content")
        self.assertEqual(diagnostic.code, error.code)
        self.assertEqual(contract_error_from_diagnostic(diagnostic).code, error.code)

    def test_security_and_unknown_errors_remain_fail_fast(self) -> None:
        for code in (*sorted(SECURITY_CODES), "E_FUTURE_SECURITY_FAILURE"):
            with self.subTest(code=code):
                error = ContractError(code, "blocked")
                with self.assertRaises(ContractError) as raised:
                    diagnostic_from_contract_error(error)
                self.assertIs(raised.exception, error)

    def test_invalid_line_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Diagnostic("E_TEST", "error", "content", "invalid", line=0)


if __name__ == "__main__":
    unittest.main()
