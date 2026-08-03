from __future__ import annotations

import hashlib
import os
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from skill.scripts.pipeline_contracts import canonical_json_bytes
from skill.scripts.readme_showcase.evaluation.editorial import (
    EditorialDiagnostic,
    evaluate_editorial,
)


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "editorial"


def read_fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class EditorialTests(unittest.TestCase):
    def evaluate(self, en: str, zh: str, **kwargs: object):
        diffs = kwargs.pop("diff_lines", {"README.md": 0, "README_zh.md": 0})
        return evaluate_editorial(
            {"README.md": en, "README_zh.md": zh},
            planned_sections=("quick start", "plan"),
            diff_lines=diffs,
            **kwargs,
        )

    def test_good_bilingual_fixture_is_advisory_pass_with_explicit_non_applicable(self) -> None:
        report = self.evaluate(read_fixture("good-README.md"), read_fixture("good-README_zh.md"))
        self.assertEqual(report.status, "pass")
        self.assertEqual(report.findings, ())
        self.assertIn("plan-coverage", {item.related_ids[0] for item in report.not_applicable})
        self.assertTrue(all(isinstance(item, EditorialDiagnostic) for item in report.reasons))
        self.assertTrue(all(item.category == "editorial" for item in report.reasons))

    def test_bad_bilingual_fixture_exposes_every_independent_advisory_finding(self) -> None:
        report = self.evaluate(
            read_fixture("bad-README.md"), read_fixture("bad-README_zh.md"),
            diff_lines={"README.md": 501, "README_zh.md": 0},
        )
        self.assertEqual(report.status, "pass")
        self.assertEqual(
            {item.code for item in report.findings},
            {
                "W_EDITORIAL_ADJACENT_IMAGES", "W_EDITORIAL_BADGES", "W_EDITORIAL_DIFF_SIZE",
                "W_EDITORIAL_DUPLICATE_PARAGRAPH", "W_EDITORIAL_FIRST_SCREEN",
                "W_EDITORIAL_HEADING_HIERARCHY", "W_EDITORIAL_LOCALE_STRUCTURE",
                "W_EDITORIAL_LONG_PARAGRAPH", "W_EDITORIAL_PLAN_COVERAGE",
                "W_EDITORIAL_QUICK_START_DISTANCE",
            },
        )
        self.assertTrue(all(item.path and item.heading and item.line and item.suggested_action for item in report.findings))
        self.assertTrue(all(item.severity == "warning" and item.category == "editorial" for item in report.findings))
        self.assertEqual(report.findings, tuple(sorted(report.findings, key=EditorialDiagnostic.sort_key)))

    def test_threshold_edges_are_exact(self) -> None:
        base = "# Demo\n\nProject definition is clear enough for readers.\n\n[Quick Start](#quick-start)\n\n## Quick Start\n"
        cases = (
            ("paragraph", base + "\n" + "x" * 599, "W_EDITORIAL_LONG_PARAGRAPH", False),
            ("paragraph", base + "\n" + "x" * 600, "W_EDITORIAL_LONG_PARAGRAPH", False),
            ("paragraph", base + "\n" + "x" * 601, "W_EDITORIAL_LONG_PARAGRAPH", True),
            ("badges", base + "\n".join(f"![badge](https://img.shields.io/{index})" for index in range(7)), "W_EDITORIAL_BADGES", False),
            ("badges", base + "\n".join(f"![badge](https://img.shields.io/{index})" for index in range(8)), "W_EDITORIAL_BADGES", False),
            ("badges", base + "\n".join(f"![badge](https://img.shields.io/{index})" for index in range(9)), "W_EDITORIAL_BADGES", True),
        )
        for _name, text, code, expected in cases:
            with self.subTest(code=code, length=len(text)):
                codes = {item.code for item in evaluate_editorial({"README.md": text}).findings}
                self.assertEqual(code in codes, expected)
        for lines, expected in ((499, False), (500, False), (501, True)):
            with self.subTest(diff_lines=lines):
                codes = {item.code for item in evaluate_editorial({"README.md": base}, diff_lines={"README.md": lines}).findings}
                self.assertEqual("W_EDITORIAL_DIFF_SIZE" in codes, expected)
        for nonempty, expected in ((119, False), (120, False), (121, True)):
            with self.subTest(nonempty=nonempty):
                text = "# Demo\n\nProject definition is clear enough for readers.\n" + "\n".join("filler" for _ in range(nonempty - 3)) + "\n## Quick Start\n"
                codes = {item.code for item in evaluate_editorial({"README.md": text}).findings}
                self.assertEqual("W_EDITORIAL_QUICK_START_DISTANCE" in codes, expected)

    def test_unicode_decoys_order_and_no_input_execution(self) -> None:
        text = """# Demo

Project definition is clear enough for readers.

[Quick Start](#quick-start)

```markdown
### skipped heading
![badge](https://img.shields.io/fake)
<script>![badge](https://img.shields.io/fake)</script>
```

<script>![badge](https://img.shields.io/also-fake)</script>

## Quick Start

e\u0301
"""
        first = evaluate_editorial({"README.md": text, "../README_zh.md": text}, diff_lines={"README.md": 0})
        second = evaluate_editorial({"../README_zh.md": text, "README.md": text}, diff_lines={"README.md": 0})
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertNotIn("W_EDITORIAL_BADGES", {item.code for item in first.findings})
        self.assertNotIn("W_EDITORIAL_HEADING_HIERARCHY", {item.code for item in first.findings})
        self.assertEqual(first.findings, ())
        self.assertEqual(canonical_json_bytes(first.as_dict()), first.canonical_bytes())

    def test_gate_regression_table_for_locale_images_and_comments(self) -> None:
        base = "# Demo\n\nProject definition is clear enough for readers.\n\n[Quick Start](#quick-start)\n\n## Quick Start\n"
        en = base + "\n## Install\n\nInstall it.\n\n## Usage\n\nUse it.\n"
        rows = (
            (
                "reversed translated sections",
                en,
                base + "\n## 使用\n\n使用它。\n\n## 安装\n\n安装它。\n",
                {"W_EDITORIAL_LOCALE_STRUCTURE"},
                ("README_zh.md", "使用", 9),
            ),
            (
                "matching translated sections",
                en,
                base + "\n## 安装\n\n安装它。\n\n## 使用\n\n使用它。\n",
                set(),
                None,
            ),
            (
                "image blocks separated by blank lines",
                base + "\n![](first.png)\n\n![](second.png)\n",
                base,
                {"W_EDITORIAL_ADJACENT_IMAGES"},
                ("README.md", "Quick Start", 11),
            ),
            (
                "caption breaks image blocks",
                base + "\n![](first.png)\n\n*Caption for first image.*\n\n![](second.png)\n",
                base,
                set(),
                None,
            ),
            (
                "comments are inert",
                base + "\n<!--\n### skipped heading\n![badge](https://img.shields.io/comment)\n![](first.png)\n\n![](second.png)\n",
                base,
                set(),
                None,
            ),
        )
        for name, english, chinese, expected, location in rows:
            with self.subTest(name=name):
                findings = self.evaluate(english, chinese).findings
                codes = {item.code for item in findings}
                relevant = {"W_EDITORIAL_LOCALE_STRUCTURE", "W_EDITORIAL_ADJACENT_IMAGES", "W_EDITORIAL_HEADING_HIERARCHY", "W_EDITORIAL_BADGES"}
                self.assertEqual(codes & relevant, expected)
                if location is not None:
                    finding = next(item for item in findings if item.code in expected)
                    self.assertEqual((finding.path, finding.heading, finding.line), location)

    def test_paths_are_never_opened_and_fixtures_remain_immutable_under_concurrency(self) -> None:
        fixture_paths = sorted(FIXTURES.glob("*.md"))
        before = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture_paths}
        text = read_fixture("good-README.md")
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            link = root / "candidate-link.md"
            link.symlink_to(FIXTURES / "bad-README.md")
            pipe = root / "candidate.pipe"
            os.mkfifo(pipe)
            report = evaluate_editorial({str(link): text, str(pipe): text})
            self.assertEqual(report.status, "pass")
            with ThreadPoolExecutor(max_workers=8) as executor:
                outputs = list(executor.map(lambda _index: evaluate_editorial({str(link): text, str(pipe): text}).canonical_bytes(), range(32)))
            self.assertEqual(len(set(outputs)), 1)
        after = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture_paths}
        self.assertEqual(before, after)


if __name__ == "__main__":
    unittest.main()
