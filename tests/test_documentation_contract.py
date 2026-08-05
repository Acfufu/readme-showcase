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


def _assert_public_readme_boundary(text: str) -> None:
    """Reject stale, copied, or remote-write claims in public README prose."""

    lowered = text.casefold()
    stale_markers = ("2fb0f2a", "e77c1c3", "19/19", "649/692", "691/692", "711")
    for marker in stale_markers:
        if marker.casefold() in lowered:
            raise AssertionError(f"stale baseline marker in README: {marker}")

    if re.search(
        r"(?im)(?:^\s*9\s*[.)]\s*(?:`[^`]+`|(?:stage|step|阶段))"
        r"|\bninth\s+stage\b|\bnine\s+stages?\b|第九阶段|9\s*个阶段)",
        text,
    ):
        raise AssertionError("README must preserve the eight-stage pipeline")

    if re.search(
        r"(?i)(?:\$TARGET|target/)[^\n`]{0,80}/(?:state/readme-showcase|\.readme-showcase-run-)",
        text,
    ) or re.search(
        r"(?i)/(?:state/readme-showcase|\.readme-showcase-run-)[^\n`]{0,80}(?:\$TARGET|target/)",
        text,
    ):
        raise AssertionError("README state must not be target-adjacent")

    for token in ("archscribe", "rough.js", "roughjs", "lazypay/"):
        if token in lowered:
            raise AssertionError(f"copied visual-runtime claim in README: {token}")

    if re.search(
        r"(?i)\blive(?:[- ](?:providers?|delivery|publication|publish|write))\b"
        r"|\b(?:browser|production)[- ](?:validated|tested|ready)\b",
        text,
    ):
        raise AssertionError("README must not claim live, browser, or production proof")


def _assert_compiled_readme_contract(text: str, *, language: str) -> None:
    required = (
        "Plan v3",
        'diagram_route: "compiled"',
        "`none`",
        "`static`",
        "`elk`",
        "state/readme-showcase/",
        "stages/06-bundle-assemble/attempts/<attempt>/compiled/",
        "desktop",
        "mobile",
        "1,200",
        "900 px",
        "720",
        "360 px",
        "dry-run",
        "visual-compiler.md",
    )
    for marker in required:
        if marker not in text:
            raise AssertionError(f"compiled README contract is missing: {marker}")
    language_markers = {
        "en": ("deterministic", "eight-stage", "local-only"),
        "zh": ("确定性的", "八阶段", "单一 README Agent", "只在本地运行"),
    }
    for marker in language_markers[language]:
        if marker not in text:
            raise AssertionError(f"{language} README contract is missing: {marker}")
    if language == "en" and re.search(r"one\s+README Agent", text) is None:
        raise AssertionError("en README contract is missing: one README Agent")
    _assert_public_readme_boundary(text)


class DocumentationContractTests(unittest.TestCase):
    def test_bilingual_readmes_publish_evidence_first_homepage(self) -> None:
        english = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        chinese = (REPO_ROOT / "README_zh.md").read_text(encoding="utf-8")
        _assert_compiled_readme_contract(english, language="en")
        _assert_compiled_readme_contract(chinese, language="zh")
        for text in (english, chinese):
            self.assertEqual(text.count("```mermaid"), 0)
            self.assertIn("22", text)
            self.assertIn("20", text)
            self.assertIn("2", text)
            self.assertIn("npx --yes github:Acfufu/readme-showcase", text)
            self.assertIn("skills install", text)
            self.assertIn("skills check", text)
            self.assertIn("skills update", text)
            self.assertIn(".agents/skills/readme-showcase", text)
            self.assertIn("$readme-showcase shape [target]", text)
            self.assertIn("$readme-showcase audit [target]", text)
            self.assertIn("$readme-showcase redesign [target]", text)
            self.assertIn("$readme-showcase polish [target]", text)
            self.assertIn("$readme-showcase visualize [target]", text)
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
        self.assertIn(
            "Please install this Skill: https://github.com/Acfufu/readme-showcase",
            english,
        )
        self.assertIn("assets/readme/hero-zh.gif", chinese)
        self.assertIn("虚拟环境", chinese)
        self.assertIn("assets/readme/workflow-zh.svg", chinese)
        self.assertIn("单一 README Agent", chinese)
        self.assertIn(
            "请安装这个 Skill：https://github.com/Acfufu/readme-showcase",
            chinese,
        )
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
        commands = (
            REPO_ROOT / "skill/references/commands.md"
        ).read_text(encoding="utf-8")
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
        self.assertIn("user-invocable: true", skill)
        self.assertIn("references/commands.md", skill)
        for command in ("shape", "audit", "redesign", "polish", "visualize"):
            self.assertIn(f"`{command} [target]`", skill)
            self.assertIn(f"## `{command} [target]`", commands)
        self.assertIn("never\nauthorizes commit, push, publication", commands)
        self.assertIn("Leave every README byte-for-byte unchanged", commands)

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

    def test_public_readme_negative_contract_rejects_boundary_mutations(self) -> None:
        baseline = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
        mutations = {
            "stale_baseline": "\nThis page still reports baseline 2fb0f2a.\n",
            "ninth_stage": "\n9. `publish`\n",
            "target_adjacent_state": "\nRun state: $TARGET/.readme-showcase-run-bad/\n",
            "copied_archscribe_claim": "\nArchscribe output is production-ready.\n",
            "live_write": "\nThe compiled route writes to live providers.\n",
        }
        for name, injection in mutations.items():
            with self.subTest(mutation=name):
                with self.assertRaises(AssertionError):
                    _assert_public_readme_boundary(baseline + injection)


if __name__ == "__main__":
    _ = unittest.main()
