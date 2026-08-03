from __future__ import annotations

import unittest
from typing import Any, Callable

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.locale import parse_locale
from skill.scripts.readme_showcase.validation.readme import validate_readme_locales


CANONICAL_TAGS = ("en", "zh-Hans", "zh-Hant", "ja", "ko", "fr", "de")


class LocaleContractTests(unittest.TestCase):
    def assert_code(self, code: str, function: Callable[..., Any], *args: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*args)
        self.assertEqual(raised.exception.code, code)

    def test_exact_canonical_tags_accept_without_normalization_or_count_cap(self) -> None:
        for tag in CANONICAL_TAGS:
            with self.subTest(tag=tag):
                self.assertIs(parse_locale(tag), tag)

        sequence = [tag for _ in range(128) for tag in CANONICAL_TAGS]
        self.assertEqual([parse_locale(tag) for tag in sequence], sequence)

    def test_nearby_missing_and_non_string_tags_fail_closed(self) -> None:
        for value in ("EN", "ZH-hans", "zh_CN", "en-US", "x-private", "en-u-hc-h12", "es", "", None, 7, []):
            with self.subTest(value=repr(value)):
                self.assert_code("E_LOCALE", parse_locale, value)

    def test_explicit_readme_mappings_reject_duplicate_and_unsafe_identity(self) -> None:
        self.assertEqual(
            validate_readme_locales([
                {"tag": "en", "readme_path": "docs/start.md"},
                {"tag": "zh-Hans", "readme_path": "localized/guide.md"},
                {"tag": "ja", "readme_path": "notes/not-zh.md"},
            ]),
            [
                {"tag": "en", "readme_path": "docs/start.md"},
                {"tag": "zh-Hans", "readme_path": "localized/guide.md"},
                {"tag": "ja", "readme_path": "notes/not-zh.md"},
            ],
        )
        self.assert_code(
            "E_LOCALE",
            validate_readme_locales,
            [{"tag": "en", "readme_path": "README.md"}, {"tag": "en", "readme_path": "other.md"}],
        )
        self.assert_code(
            "E_README_PATH",
            validate_readme_locales,
            [{"tag": "en", "readme_path": "README.md"}, {"tag": "ja", "readme_path": "README.md"}],
        )
        self.assert_code(
            "E_README_PATH",
            validate_readme_locales,
            [{"tag": "en", "readme_path": "../README.md"}],
        )


if __name__ == "__main__":
    unittest.main()
