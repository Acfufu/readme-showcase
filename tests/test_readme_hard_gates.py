from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill.scripts.audit_readme import audit_svg_bytes, visible_svg_text


REPO_ROOT = Path(__file__).resolve().parents[1]
AUDIT = REPO_ROOT / "skill/scripts/audit_readme.py"
VALID_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" '
    'viewBox="0 0 1200 480" role="img" aria-labelledby="title">'
    '<title id="title">Architecture</title><text>Agent</text></svg>\n'
)


class ReadmeHardGateTests(unittest.TestCase):
    def run_audit(self, readme: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(AUDIT), str(readme)],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_local_links_anchors_alt_and_svg_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "diagram.svg").write_text(
                VALID_SVG.replace(
                    "<text>",
                    '<text font-family="Inter, system-ui, sans-serif">',
                ),
                encoding="utf-8",
            )
            (root / "guide.md").write_text("# Quick Start\n", encoding="utf-8")
            readme = root / "README.md"
            readme.write_text(
                "# Demo\n\n"
                "![Architecture](diagram.svg)\n\n"
                "[Guide](guide.md#quick-start)\n\n"
                "<a id=spot></a>\n\n"
                "[Spot](#spot)\n\n"
                "<a href=guide.md#quick-start>Guide HTML</a>\n",
                encoding="utf-8",
            )

            result = self.run_audit(readme)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

    def test_localized_readme_rejects_unlocalized_text_svg(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "workflow.svg").write_text(VALID_SVG, encoding="utf-8")
            readme = root / "README_zh.md"
            readme.write_text(
                "# 演示\n\n![工作流](workflow.svg)\n",
                encoding="utf-8",
            )

            result = self.run_audit(readme)

            self.assertEqual(result.returncode, 1)
            self.assertIn("E_SVG_LOCALE", result.stdout)

            (root / "workflow-zh.svg").write_text(VALID_SVG, encoding="utf-8")
            readme.write_text(
                "# 演示\n\n![工作流](workflow-zh.svg)\n",
                encoding="utf-8",
            )
            renamed_only = self.run_audit(readme)
            self.assertEqual(renamed_only.returncode, 1)
            self.assertIn("contains no visible Chinese text", renamed_only.stdout)

            (root / "workflow-zh.svg").write_text(
                VALID_SVG.replace("Architecture", "工作流").replace("Agent", "证据"),
                encoding="utf-8",
            )
            readme.write_text(
                "# 演示\n\n![工作流](workflow-zh.svg)\n",
                encoding="utf-8",
            )
            localized = self.run_audit(readme)
            self.assertEqual(
                localized.returncode,
                0,
                localized.stdout + localized.stderr,
            )

            (root / "workflow.svg").write_text(
                VALID_SVG.replace(
                    "<svg ",
                    '<svg data-readme-language="neutral" ',
                ),
                encoding="utf-8",
            )
            readme.write_text(
                "# 演示\n\n![工作流](workflow.svg)\n",
                encoding="utf-8",
            )
            neutral = self.run_audit(readme)
            self.assertEqual(
                neutral.returncode,
                0,
                neutral.stdout + neutral.stderr,
            )

    def test_broken_anchor_missing_alt_and_root_escape_report_lines(self) -> None:
        cases = {
            "anchor": ("# Demo\n\n[Broken](#missing)\n", "broken anchor", 3),
            "alt": ("# Demo\n\n![](diagram.svg)\n", "missing useful alt", 3),
            "missing": (
                "# Demo\n\n![Missing](missing.svg)\n",
                "missing local reference",
                3,
            ),
            "escape": (
                "# Demo\n\n![Outside](../outside.svg)\n",
                "escapes README root",
                3,
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            (base / "outside.svg").write_text(VALID_SVG, encoding="utf-8")
            for name, (content, expected, line) in cases.items():
                with self.subTest(name=name):
                    root = base / name
                    root.mkdir()
                    (root / "diagram.svg").write_text(VALID_SVG, encoding="utf-8")
                    readme = root / "README.md"
                    readme.write_text(content, encoding="utf-8")

                    result = self.run_audit(readme)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn(expected, result.stdout)
                    self.assertIn(f"line {line}", result.stdout)

    def test_svg_active_content_remote_resource_and_bad_ids_fail(self) -> None:
        variants = {
            "doctype": VALID_SVG.replace("<svg ", "<!DOCTYPE svg><svg "),
            "event": VALID_SVG.replace("<svg ", '<svg onload="alert(1)" '),
            "malformed": VALID_SVG.removesuffix("</svg>\n"),
            "missing-title": VALID_SVG.replace(
                '<title id="title">Architecture</title>',
                "",
            ),
            "remote-font": VALID_SVG.replace(
                "<text>",
                '<text font-family="RemoteFont">',
            ),
            "style": VALID_SVG.replace(
                "<text>",
                '<style>@import url("https://example.com/x.css")</style><text>',
            ),
            "foreign": VALID_SVG.replace(
                "<text>",
                "<foreignObject><p>bad</p></foreignObject><text>",
            ),
            "image": VALID_SVG.replace(
                "<text>",
                '<image href="https://example.com/x.png"/><text>',
            ),
            "duplicate-id": VALID_SVG.replace(
                "<text>",
                '<g id="title"></g><text>',
            ),
            "unresolved-id": VALID_SVG.replace(
                'aria-labelledby="title"',
                'aria-labelledby="missing"',
            ),
            "deep": VALID_SVG.replace(
                "<text>",
                "<g>" * 65 + "<text>",
            ).replace("</text>", "</text>" + "</g>" * 65),
            "oversize": VALID_SVG.replace(
                "<text>",
                " " * (2 * 1024 * 1024) + "<text>",
            ),
            "animate-transform": VALID_SVG.replace(
                "<text>",
                '<animateTransform attributeName="transform"/><text>',
            ),
            "discard": VALID_SVG.replace(
                "<text>",
                "<discard/><text>",
            ),
            "xml-stylesheet": VALID_SVG.replace(
                "<svg ",
                '<?xml-stylesheet href="https://example.com/x.css"?><svg ',
            ),
            "xml-base": VALID_SVG.replace(
                "<svg ",
                '<svg xml:base="https://example.com/" ',
            ),
            "nan-viewbox": VALID_SVG.replace(
                'viewBox="0 0 1200 480"',
                'viewBox="0 0 NaN 480"',
            ),
            "hidden-label": VALID_SVG.replace(
                "<text>",
                '<text style="display:none">',
            ),
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, svg in variants.items():
                with self.subTest(name=name):
                    root = base / name
                    root.mkdir()
                    (root / "diagram.svg").write_text(svg, encoding="utf-8")
                    readme = root / "README.md"
                    readme.write_text(
                        "# Demo\n\n![Architecture](diagram.svg)\n",
                        encoding="utf-8",
                    )

                    result = self.run_audit(readme)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn("diagram.svg:", result.stdout)

    def test_zero_equivalent_opacity_never_binds_visible_labels(self) -> None:
        variants = {
            "decimal": '<text opacity="0.000">Agent</text>',
            "leading-zero": '<text fill-opacity="00">Agent</text>',
            "leading-dot": '<text style="opacity:.0">Agent</text>',
            "negative-zero": '<text style="fill-opacity:-0">Agent</text>',
            "exponent": '<text opacity="0e3">Agent</text>',
            "negative": '<text opacity="-1">Agent</text>',
            "negative-percent": '<text opacity="-10%">Agent</text>',
            "inherited": '<g opacity="0.000"><text>Agent</text></g>',
        }
        for name, body in variants.items():
            with self.subTest(name=name):
                raw = VALID_SVG.replace("<text>Agent</text>", body).encode()

                issues = audit_svg_bytes(
                    raw,
                    expected_title="Architecture",
                    expected_labels=["Agent"],
                )

                self.assertIn(
                    ("E_SVG_UNSAFE", "contains hidden semantic text"),
                    issues,
                )
                self.assertEqual(visible_svg_text(raw), [])

    def test_invalid_opacity_fails_closed(self) -> None:
        for value in ("bogus", "NaN", "calc(0)", "0 1"):
            with self.subTest(value=value):
                raw = VALID_SVG.replace(
                    "<text>",
                    f'<text opacity="{value}">',
                ).encode()

                issues = audit_svg_bytes(raw)

                self.assertIn(
                    ("E_SVG_UNSAFE", "contains invalid opacity"),
                    issues,
                )
                self.assertEqual(visible_svg_text(raw), [])

    def test_nonzero_opacity_remains_visible(self) -> None:
        for value in ("0.5", "50%", "1e-1"):
            with self.subTest(value=value):
                raw = VALID_SVG.replace(
                    "<text>",
                    f'<text opacity="{value}">',
                ).encode()

                issues = audit_svg_bytes(
                    raw,
                    expected_title="Architecture",
                    expected_labels=["Agent"],
                )

                self.assertEqual(issues, [])
                self.assertEqual(visible_svg_text(raw), ["Agent"])

    def test_reference_images_unquoted_html_and_indented_fence_are_audited(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "unsafe.svg").write_text(
                VALID_SVG.replace("<text>", "<script/><text>"),
                encoding="utf-8",
            )
            cases = {
                "reference": (
                    "# Demo\n\n![Architecture][diagram]\n\n"
                    "[diagram]: unsafe.svg\n"
                ),
                "unquoted": "# Demo\n\n<img src=unsafe.svg alt=Architecture>\n",
                "indented-fence": (
                    "# Demo\n\n    ```\n"
                    "![Architecture](unsafe.svg)\n"
                    "    ```\n"
                ),
            }
            for name, content in cases.items():
                with self.subTest(name=name):
                    readme = root / f"{name}.md"
                    readme.write_text(content, encoding="utf-8")

                    result = self.run_audit(readme)

                    self.assertEqual(result.returncode, 1)
                    self.assertIn("unsafe.svg:", result.stdout)

    def test_repository_readmes_keep_existing_cli_contract(self) -> None:
        for name in ("README.md", "README_zh.md"):
            with self.subTest(name=name):
                result = self.run_audit(REPO_ROOT / name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
