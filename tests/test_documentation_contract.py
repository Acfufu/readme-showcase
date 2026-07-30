from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_bilingual_readmes_publish_three_owned_workflows_and_counts(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README_zh.md").read_text(encoding="utf-8")
        for text in (english, chinese):
            self.assertEqual(text.count("```mermaid"), 3)
            self.assertIn("19/19", text)
            self.assertIn("7/8", text)
            self.assertIn("7/19", text)
            self.assertIn("1/19", text)
            self.assertIn("0/29", text)
            self.assertIn("649/692", text)
            self.assertIn("@glyphicjs/core@1.3.1", text)
            self.assertIn("schema 1.1.1", text)
            self.assertIn("showcase-contribution", text)
        self.assertIn("ORIGINAL README SHOWCASE", english)
        self.assertIn("BEAUTIFY-GITHUB-README", english)
        self.assertIn("GLYPHIC USED ONLY HERE", english)
        self.assertIn("Fingerprint PR bundle", english)
        self.assertIn("原始 README SHOWCASE", chinese)
        self.assertIn("仅此处使用 GLYPHIC", chinese)
        self.assertIn("带指纹 PR bundle", chinese)
        for text in (english, chinese):
            self.assertIn("python3 scripts/install_skill.py", text)
            self.assertIn("readme-showcase.backup.<UTC>.<hash>", text)
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
        self.assertIn("Ten records are production-retrieval", text)
        self.assertIn("two are isolated `test`", text)
        self.assertIn("never production retrieval", text)
        self.assertEqual(text.count("```mermaid"), 1)

    def test_skill_references_match_runtime_and_publish_boundary(self) -> None:
        skill = (REPO_ROOT / "skill/SKILL.md").read_text(encoding="utf-8")
        glyphic = (
            REPO_ROOT / "skill/references/glyphic-structure.md"
        ).read_text(encoding="utf-8")
        flat_glyphic = " ".join(glyphic.split())
        delta = (
            REPO_ROOT / "skill/references/beautify-github-readme-delta.md"
        ).read_text(encoding="utf-8")
        metadata = (
            REPO_ROOT / "skill/agents/openai.yaml"
        ).read_text(encoding="utf-8")
        self.assertIn('README_SHOWCASE_SKILL="${CODEX_HOME', skill)
        self.assertIn("10 production `train`", skill)
        self.assertIn("@glyphicjs/schema@1.1.1", glyphic)
        self.assertNotIn("system font `Arial`", glyphic)
        self.assertIn("defined in the same SVG", flat_glyphic)
        self.assertIn("`992`", delta)
        self.assertIn("`649/692 = 93.79%`", delta)
        self.assertNotIn("`691/692", delta)
        self.assertNotIn("`711`", delta)
        self.assertIn("stop at a local PR bundle", metadata)


if __name__ == "__main__":
    _ = unittest.main()
