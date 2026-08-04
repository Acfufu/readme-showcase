from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
VISUAL_KERNEL_ROOT = REPO_ROOT / "skill/scripts/readme_showcase/visual_kernel"
_RUNTIME_SUFFIXES = {".cjs", ".js", ".mjs", ".py", ".ts", ".tsx"}
_IMPORT_STATEMENT = re.compile(
    r"(?:\bfrom\s+|\bimport(?:\s|[\"'])|\brequire\s*\()", re.IGNORECASE
)
_FORBIDDEN_IMPORT_TOKENS = ("archscribe", "rough.js", "roughjs", "font", "icon")


def _visual_kernel_boundary_violations(root: Path) -> list[str]:
    """Return runtime clean-room violations under one kernel package root."""

    if not root.exists():
        return []

    violations: list[str] = []
    vendor = root / "vendor"
    if vendor.exists():
        violations.append("vendor/")

    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in _RUNTIME_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        for line_number, line in enumerate(text.splitlines(), start=1):
            if line.lstrip().startswith(("#", "//", "/*", "*")):
                continue
            if not _IMPORT_STATEMENT.search(line):
                continue
            lowered = line.casefold()
            if any(token in lowered for token in _FORBIDDEN_IMPORT_TOKENS):
                relative = path.relative_to(root)
                violations.append(f"{relative}:{line_number}")
    return violations


def _assert_visual_kernel_clean(root: Path) -> None:
    violations = _visual_kernel_boundary_violations(root)
    if violations:
        raise AssertionError("visual kernel clean-room violations: " + ", ".join(violations))


class DocumentationContractTests(unittest.TestCase):
    def test_bilingual_readmes_publish_evidence_first_homepage(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README_zh.md").read_text(encoding="utf-8")
        for text in (english, chinese):
            self.assertEqual(text.count("```mermaid"), 0)
            self.assertIn("22", text)
            self.assertIn("20", text)
            self.assertIn("2", text)
            self.assertIn("npx --yes github:Acfufu/readme-showcase", text)
            self.assertIn('"status":"installed"', text)
            self.assertIn('"status":"current"', text)
            self.assertIn("elkjs@0.9.3", text)
            self.assertIn("22.22.3", text)
            self.assertIn("build-pr-bundle", text)
            self.assertIn("state/readme-showcase/", text)
            self.assertNotIn("../readme-showcase-run", text)
            self.assertNotIn("19/19", text)
            self.assertNotIn("649/692", text)
        self.assertIn("assets/readme/hero.gif", english)
        self.assertIn("virtual environment", english)
        self.assertIn("assets/readme/workflow.svg", english)
        self.assertIn("one README Agent", english)
        self.assertIn("assets/readme/hero-zh.gif", chinese)
        self.assertIn("虚拟环境", chinese)
        self.assertIn("assets/readme/workflow-zh.svg", chinese)
        self.assertIn("单一 README Agent", chinese)
        for text in (english, chinese):
            self.assertIn("npx --yes github:Acfufu/readme-showcase", text)
            self.assertNotIn("cp -R skill", text)

    def test_dataset_ledger_names_all_pinned_sources_and_split_boundary(self) -> None:
        text = (REPO_ROOT / "dataset/README.md").read_text(encoding="utf-8")
        for repository in (
            "cli/cli",
            "denoland/deno",
            "fastapi/fastapi",
            "pallets/flask",
            "encode/httpx",
            "pydantic/pydantic",
            "psf/requests",
            "astral-sh/ruff",
            "tokio-rs/tokio",
            "vitejs/vite",
            "vercel/next.js",
            "pytest-dev/pytest",
        ):
            self.assertIn(repository, text)
        self.assertIn("Twenty records are production-retrieval", text)
        self.assertIn("two are isolated `test`", text)
        self.assertIn("never production retrieval", text)
        self.assertEqual(text.count("```mermaid"), 1)

    def test_skill_references_match_runtime_and_publish_boundary(self) -> None:
        skill = (REPO_ROOT / "skill/SKILL.md").read_text(encoding="utf-8")
        elk = (
            REPO_ROOT / "skill/references/elk-structure.md"
        ).read_text(encoding="utf-8")
        flat_elk = " ".join(elk.split())
        delta = (
            REPO_ROOT / "skill/references/beautify-github-readme-delta.md"
        ).read_text(encoding="utf-8")
        metadata = (
            REPO_ROOT / "skill/agents/openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('README_SHOWCASE_SKILL="${CODEX_HOME', skill)
        self.assertIn("state/readme-showcase/", skill)
        self.assertIn("Never\ncreate `.readme-showcase-run-*`", skill)
        self.assertIn("Never create a per-run virtual environment", skill)
        self.assertIn("10 production `train`", skill)
        self.assertIn("elkjs@0.9.3", elk)
        self.assertIn("EPL-2.0", elk)
        self.assertNotIn("engine lock", elk.lower())
        self.assertNotIn("system font `Arial`", elk)
        self.assertIn("defined in the same SVG", flat_elk)
        self.assertIn("`992`", delta)
        self.assertIn("`649/692 = 93.79%`", delta)
        self.assertNotIn("`691/692", delta)
        self.assertNotIn("`711`", delta)
        self.assertIn("stop at a local PR bundle", metadata)

    def test_visual_kernel_clean_room_rejects_runtime_payload_mutations(self) -> None:
        _assert_visual_kernel_clean(VISUAL_KERNEL_ROOT)

        with tempfile.TemporaryDirectory(prefix="visual-kernel-clean-room-") as temporary:
            fixture = Path(temporary) / "visual_kernel"
            fixture.mkdir()
            forbidden_imports = (
                "from archscribe import render",
                'import "rough.js";',
                "import fontkit",
                'import icons from "icon-package";',
            )
            for index, source in enumerate(forbidden_imports):
                with self.subTest(source=source):
                    runtime_file = fixture / f"mutation_{index}.py"
                    runtime_file.write_text(source + "\n", encoding="utf-8")
                    with self.assertRaises(AssertionError):
                        _assert_visual_kernel_clean(fixture)
                    runtime_file.unlink()

            (fixture / "vendor").mkdir()
            with self.assertRaises(AssertionError):
                _assert_visual_kernel_clean(fixture)


if __name__ == "__main__":
    _ = unittest.main()
