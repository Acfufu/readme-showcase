from __future__ import annotations

import hashlib
import random
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.diagnostics import Diagnostic
from skill.scripts.readme_showcase.errors import AGGREGATABLE_CODES, FAIL_FAST_CODES
from skill.scripts.readme_showcase.validation.bundle import validation_report, validate_checks
from skill.scripts.readme_showcase.validation.policy import (
    AGGREGATE_CONTENT,
    FAIL_FAST,
    KNOWN_ERROR_CODES,
    classify_error_code,
)


REPO_ROOT = Path(__file__).resolve().parents[3]


class ValidationPolicyTests(unittest.TestCase):
    def test_snapshot_codes_are_classified_exactly_once(self) -> None:
        snapshot = frozenset(
            (REPO_ROOT / "tests/fixtures/architecture/error_codes.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )

        self.assertEqual(snapshot, KNOWN_ERROR_CODES)
        self.assertTrue(AGGREGATABLE_CODES.isdisjoint(FAIL_FAST_CODES))
        self.assertEqual(
            {classify_error_code(code) for code in snapshot},
            {AGGREGATE_CONTENT, FAIL_FAST},
        )
        self.assertEqual(
            {code for code in snapshot if classify_error_code(code) == AGGREGATE_CONTENT},
            AGGREGATABLE_CODES,
        )

    def test_unknown_and_security_errors_abort_before_later_read(self) -> None:
        for code in ("E_FUTURE_TEST", "E_SVG_UNSAFE", "E_BUNDLE_HASH", "E_PATH"):
            with self.subTest(code=code):
                reads: list[str] = []

                def content() -> None:
                    raise ContractError("E_README_COMMAND", "missing command")

                def blocked() -> None:
                    raise ContractError(code, "blocked")

                def secret_read() -> None:
                    reads.append("secret")

                with self.assertRaises(ContractError) as raised:
                    validate_checks((content, blocked, secret_read))
                self.assertEqual(raised.exception.code, code)
                self.assertEqual(reads, [])

    def test_content_diagnostics_are_stable_complete_and_deduplicated(self) -> None:
        findings = [
            Diagnostic(
                "E_README_LANGUAGE",
                "error",
                "content",
                "language pair missing",
                "README.md",
                8,
                ("claim:zh",),
                "link README_zh.md",
            ),
            Diagnostic(
                "E_README_COMMAND",
                "error",
                "content",
                "command missing",
                "README.md",
                4,
                (),
                "insert npm test",
            ),
            Diagnostic(
                "E_README_ACCESSIBILITY",
                "error",
                "content",
                "alt missing",
                "assets/workflow.svg",
                2,
            ),
        ]

        def checks(values: list[Diagnostic]):
            return tuple(lambda item=item: item for item in values)

        expected = validate_checks(checks(findings))
        randomized = [*findings, findings[0]]
        random.Random(10).shuffle(randomized)
        actual = validate_checks(checks(randomized))

        self.assertEqual(len(actual.diagnostics), 3)
        self.assertEqual(
            [item.code for item in actual.diagnostics],
            [
                "E_README_ACCESSIBILITY",
                "E_README_COMMAND",
                "E_README_LANGUAGE",
            ],
        )
        self.assertEqual(actual.canonical_bytes(), expected.canonical_bytes())
        self.assertEqual(
            actual.sha256(),
            hashlib.sha256(actual.canonical_bytes()).hexdigest(),
        )
        self.assertEqual(
            list(actual.diagnostics[1].as_dict()),
            [
                "category",
                "code",
                "line",
                "message",
                "path",
                "related_ids",
                "severity",
                "suggested_action",
            ],
        )
        self.assertEqual(
            validation_report(actual.diagnostics),
            {
                "schema_version": 1,
                "status": "fail",
                "diagnostics": [item.as_dict() for item in actual.diagnostics],
            },
        )


if __name__ == "__main__":
    unittest.main()
