from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


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


if __name__ == "__main__":
    _ = unittest.main()
