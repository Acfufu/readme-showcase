from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.contracts.locale import LOCALE_TAGS


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPO_ROOT / "skill/schemas/interview.v1.schema.json"
INTERVIEW_DOC_PATH = REPO_ROOT / "skill/references/novice-interview.md"
SKILL_DOC_PATH = REPO_ROOT / "skill/SKILL.md"

NEW_LOCALE_LABEL_FIELDS = ("label_zh-Hant", "label_ja", "label_ko", "label_fr", "label_de")
NEW_LOCALE_DESCRIPTION_FIELDS = (
    "description_zh",
    "description_zh-Hant",
    "description_ja",
    "description_ko",
    "description_fr",
    "description_de",
)
ALL_LOCALE_LABEL_FIELDS = ("label", "label_zh") + NEW_LOCALE_LABEL_FIELDS
ALL_LOCALE_DESCRIPTION_FIELDS = ("description",) + NEW_LOCALE_DESCRIPTION_FIELDS

_TABLE_ROW = re.compile(r"^\|\s*(\w+)\s*\|\s*(.*?)\s*\|\s*(.*?)\s*\|\s*(\w+)\s*\|$")
_OPTION_SEGMENT = re.compile(r"^(\d+)\s+(.+?)\s+\((.+?)\)(?:\s*→\s*(.*))?$")


def _load_schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _catalog_by_id(schema: dict) -> dict[str, dict]:
    return {entry["id"]: entry for entry in schema["x-question-catalog"]}


def _parse_option_table_rows() -> dict[str, dict]:
    """Parse the §4 fixed question set table into
    {question_id: {"prompt": str, "default": str, "options": [(key, zh, en, route_or_None), ...]}}.

    Only rows with a leading pipe are considered; the table header row
    ("| id | prompt (EN) | ...") is skipped because its first cell is not a
    known question id.
    """

    known_ids = {"goal", "scope", "audience", "proof", "style", "extras"}
    rows: dict[str, dict] = {}
    for line in INTERVIEW_DOC_PATH.read_text(encoding="utf-8").splitlines():
        match = _TABLE_ROW.match(line.strip())
        if match is None:
            continue
        question_id, prompt, options_cell, default = match.groups()
        if question_id not in known_ids:
            continue
        options = []
        for segment in options_cell.split("·"):
            segment = segment.strip()
            if not segment:
                continue
            option_match = _OPTION_SEGMENT.match(segment)
            if option_match is None:
                raise AssertionError(f"unparseable option segment: {segment!r}")
            key, zh_label, en_label, route = option_match.groups()
            options.append((key, zh_label.strip(), en_label.strip(), route))
        rows[question_id] = {"prompt": prompt, "default": default, "options": options}
    return rows


class InterviewI18nContractTests(unittest.TestCase):
    def test_supported_ui_locales_match_canonical_tags(self) -> None:
        schema = _load_schema()
        self.assertIn(
            "x-supported-ui-locales",
            schema,
            "catalog must declare x-supported-ui-locales",
        )
        self.assertEqual(
            list(schema["x-supported-ui-locales"]),
            list(LOCALE_TAGS),
            "x-supported-ui-locales must equal the canonical LOCALE_TAGS",
        )

    def test_catalog_covers_all_seven_locales_for_every_question_and_option(self) -> None:
        schema = _load_schema()
        for entry in schema["x-question-catalog"]:
            question_id = entry["id"]
            for field in NEW_LOCALE_LABEL_FIELDS:
                with self.subTest(question=question_id, field=field):
                    self.assertIn(field, entry, f"question {question_id} missing {field}")
                    self.assertTrue(str(entry[field]).strip(), f"question {question_id} {field} empty")
            for option in entry["options"]:
                option_key = option["key"]
                for field in ALL_LOCALE_LABEL_FIELDS:
                    with self.subTest(question=question_id, key=option_key, field=field):
                        self.assertIn(field, option, f"option {question_id}.{option_key} missing {field}")
                        self.assertTrue(str(option[field]).strip(), f"option {question_id}.{option_key} {field} empty")
                for field in ALL_LOCALE_DESCRIPTION_FIELDS:
                    with self.subTest(question=question_id, key=option_key, field=field):
                        self.assertIn(field, option, f"option {question_id}.{option_key} missing {field}")
                        self.assertTrue(str(option[field]).strip(), f"option {question_id}.{option_key} {field} empty")

    def test_reference_table_mirrors_catalog_zh_subset(self) -> None:
        schema = _load_schema()
        catalog = _catalog_by_id(schema)
        rows = _parse_option_table_rows()
        self.assertEqual(
            set(rows),
            set(catalog),
            "table question ids must match catalog ids",
        )
        for question_id, catalog_entry in catalog.items():
            table_row = rows[question_id]
            self.assertEqual(
                table_row["prompt"],
                catalog_entry["prompt"],
                f"question {question_id} prompt mismatch",
            )
            self.assertEqual(
                str(table_row["default"]),
                str(catalog_entry["default"]),
                f"question {question_id} default mismatch",
            )
            catalog_options = catalog_entry["options"]
            self.assertEqual(
                len(table_row["options"]),
                len(catalog_options),
                f"question {question_id} option count mismatch",
            )
            catalog_by_key = {option["key"]: option for option in catalog_options}
            for (key, zh_label, en_label, route), catalog_option in zip(
                table_row["options"], catalog_options
            ):
                self.assertEqual(key, catalog_option["key"], f"question {question_id} option key mismatch")
                self.assertIn(
                    zh_label,
                    {catalog_option["label_zh"]},
                    f"question {question_id} option {key} zh label mismatch",
                )
                self.assertEqual(
                    en_label.casefold(),
                    catalog_option["label"].casefold(),
                    f"question {question_id} option {key} en label mismatch",
                )
                catalog_route = catalog_option.get("routes_to")
                if route is not None and catalog_route is not None:
                    route_token = re.search(r"`([^`]+)`", route)
                    self.assertIsNotNone(
                        route_token,
                        f"question {question_id} option {key} table route lacks a backticked token: {route!r}",
                    )
                    self.assertEqual(
                        route_token.group(1),
                        catalog_route,
                        f"question {question_id} option {key} route mismatch",
                    )
                elif route is not None and catalog_route is None:
                    # Plan-effect notes (scope/audience/proof/style/extras) are not routes_to.
                    continue
                elif route is None and catalog_route is not None:
                    raise AssertionError(
                        f"question {question_id} option {key} catalog routes_to={catalog_route!r} but table has no route"
                    )

    def test_locale_ladder_is_documented_in_skill(self) -> None:
        interview_doc = INTERVIEW_DOC_PATH.read_text(encoding="utf-8")
        for marker in ("L1", "L2", "L3"):
            self.assertIn(
                marker,
                interview_doc,
                f"novice-interview.md must document ladder step {marker}",
            )
        l1_section = re.search(
            r"- \*\*L1 — Request script detection\.\*\*(.*?)(?=\n- \*\*L2|\Z)",
            interview_doc,
            re.DOTALL,
        )
        self.assertIsNotNone(l1_section, "L1 bullet block must be parseable")
        l1_text = l1_section.group(1)
        signal_patterns = (
            re.compile(r"Hiragana/Katakana"),
            re.compile(r"Hangul"),
            re.compile(r"traditional-only"),
            re.compile(r"Han\s+characters"),
        )
        positions = [pattern.search(l1_text) for pattern in signal_patterns]
        self.assertTrue(
            all(match is not None for match in positions),
            f"L1 must mention each script signal in order: {signal_patterns}",
        )
        ordered_positions = [match.start() for match in positions if match is not None]
        self.assertEqual(
            ordered_positions,
            sorted(ordered_positions),
            "L1 script signals must be ordered most specific first "
            "(kana, hangul, traditional-only, then generic Han) so no branch is dead",
        )
        skill_doc = SKILL_DOC_PATH.read_text(encoding="utf-8")
        self.assertIn(
            "ladder",
            skill_doc,
            "SKILL.md must reference the interview locale ladder",
        )


if __name__ == "__main__":
    _ = unittest.main()
