from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
            (root / "diagram.svg").write_text(VALID_SVG, encoding="utf-8")
            (root / "guide.md").write_text("# Quick Start\n", encoding="utf-8")
            readme = root / "README.md"
            readme.write_text(
                "# Demo\n\n"
                "![Architecture](diagram.svg)\n\n"
                "[Guide](guide.md#quick-start)\n",
                encoding="utf-8",
            )

            result = self.run_audit(readme)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

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

    def test_repository_readmes_keep_existing_cli_contract(self) -> None:
        for name in ("README.md", "README_zh.md"):
            with self.subTest(name=name):
                result = self.run_audit(REPO_ROOT / name)
                self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == "__main__":
    unittest.main()
