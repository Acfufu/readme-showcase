from __future__ import annotations

import json
import re
import subprocess
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

_LINK_TARGET = re.compile(r"!?\[[^\]]*\]\(([^)]+)\)")
_FENCE_MARKER = re.compile(r"^\s*```")


def _link_targets_outside_fences(text: str):
    """Yield (line_number, match) for link targets outside fenced code blocks.

    A line whose first non-whitespace run is ``` toggles fence membership;
    links inside fences (example URLs in command blocks) are not scanned.
    """

    in_fence = False
    for line_number, line in enumerate(text.splitlines(), start=1):
        if _FENCE_MARKER.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in _LINK_TARGET.finditer(line):
            yield line_number, match

# docs/superpowers/ holds archived scratch plans, not the user-documentation
# contract. Their broken relative links are exempted per exact (path, line)
# entry below — deliberately NOT a blanket directory exemption: any other
# unreachable link, including a new one inside those files, still fails.
_SUPERPOWERS_EXEMPTIONS = frozenset(
    {
        ("docs/superpowers/plans/2026-08-13-visual-quality-production.md", 1326),
        ("docs/superpowers/plans/2026-08-13-visual-quality-production.md", 1327),
    }
)

_DOC_CONTRACT_FILES = (
    "README.md",
    "README_zh.md",
    "skill/SKILL.md",
    "skill/.env.example",
)

_DOC_CONTRACT_TREES = ("skill/references", "skill/workflows", "docs")


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


def _scanned_document_set() -> dict[str, str]:
    """Read the documentation contract into memory as {relative path: text}."""

    scanned: dict[str, str] = {}
    for relative in _DOC_CONTRACT_FILES:
        path = REPO_ROOT / relative
        if path.is_file():
            scanned[relative] = path.read_text(encoding="utf-8")
    for directory in _DOC_CONTRACT_TREES:
        for path in sorted((REPO_ROOT / directory).rglob("*.md")):
            scanned[str(path.relative_to(REPO_ROOT))] = path.read_text(encoding="utf-8")
    return scanned


def _forward_reachability_violations(scanned: dict[str, str]) -> list[str]:
    """Return every relative link target that does not resolve to a file."""

    violations: list[str] = []
    for relative, text in sorted(scanned.items()):
        if not relative.endswith(".md"):
            continue
        source = REPO_ROOT / relative
        for line_number, match in _link_targets_outside_fences(text):
            target = match.group(1).strip()
            if target.startswith(("http://", "https://", "mailto:", "ftp://", "#")):
                continue
            path_part = target.split("#", 1)[0].rstrip("/")
            if not path_part:
                continue
            if " " in path_part:
                violations.append(f"{relative}:{line_number} -> spaced target {target!r}")
                continue
            resolved = (source.parent / path_part).resolve()
            if not resolved.exists() and (relative, line_number) not in _SUPERPOWERS_EXEMPTIONS:
                violations.append(f"{relative}:{line_number} -> {target}")
    return violations


def _assert_forward_reachability(scanned: dict[str, str]) -> None:
    violations = _forward_reachability_violations(scanned)
    if violations:
        raise AssertionError("unreachable documentation links: " + "; ".join(violations))


def _assert_env_example_hygiene(text: str) -> None:
    """Reject any KEY=<concrete value> template line in the .env example."""

    violations: list[str] = []
    for line_number, line in enumerate(text.splitlines(), start=1):
        probe = line.strip().lstrip("#").strip()
        key, sep, value = probe.partition("=")
        if sep and value.strip():
            violations.append(f"{line_number}: {line.strip()}")
    if violations:
        raise AssertionError(".env.example must stay a value-free template: " + "; ".join(violations))


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
        self.assertIn("20 production `train`", skill)
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

    def test_roadmap_stays_within_public_readme_boundary(self) -> None:
        roadmap = (REPO_ROOT / "docs/roadmap.md").read_text(encoding="utf-8")
        _assert_public_readme_boundary(roadmap)
        zh_roadmap = (REPO_ROOT / "docs/zh/roadmap.md").read_text(encoding="utf-8")
        _assert_public_readme_boundary(zh_roadmap)

    def test_doc_contract_forward_reachability(self) -> None:
        scanned = _scanned_document_set()
        for required in _DOC_CONTRACT_FILES:
            self.assertIn(required, scanned, f"contract set missing: {required}")
        for directory in _DOC_CONTRACT_TREES:
            self.assertTrue(
                any(relative.startswith(directory + "/") for relative in scanned),
                f"contract set has no files under {directory}/",
            )
        _assert_forward_reachability(scanned)

    def test_doc_contract_reverse_reachability(self) -> None:
        scanned = _scanned_document_set()
        referenced: set[str] = set()
        for relative, text in scanned.items():
            if not relative.endswith(".md"):
                continue
            source = REPO_ROOT / relative
            for _line_number, match in _link_targets_outside_fences(text):
                target = match.group(1).strip().split("#", 1)[0].rstrip("/")
                if (
                    not target
                    or " " in target
                    or target.startswith(("http://", "https://", "mailto:", "ftp://", "#"))
                ):
                    continue
                resolved = (source.parent / target).resolve()
                try:
                    referenced.add(str(resolved.relative_to(REPO_ROOT)))
                except ValueError:
                    continue
        orphans: list[str] = []
        for relative in scanned:
            is_workflow = relative.startswith("skill/workflows/") and relative.endswith(".md")
            is_top_level_doc = (
                relative.startswith("docs/")
                and relative.endswith(".md")
                and "/" not in relative[len("docs/") :]
            )
            if (is_workflow or is_top_level_doc) and relative not in referenced:
                orphans.append(relative)
        self.assertEqual(orphans, [], "unreferenced documentation: " + ", ".join(orphans))

    def test_doc_contract_content_assertions(self) -> None:
        failure_recovery = (REPO_ROOT / "skill/references/failure-recovery.md").read_text(
            encoding="utf-8"
        )
        split_matrix = failure_recovery.split("## Stage × failure-mode matrix", 1)
        self.assertEqual(
            len(split_matrix),
            2,
            "failure-recovery.md missing heading: ## Stage × failure-mode matrix",
        )
        matrix = split_matrix[1].split("\n## ", 1)[0]
        stage_rows = [row for row in matrix.splitlines() if row.lstrip().startswith("| `")]
        self.assertEqual(len(stage_rows), 8, "failure-recovery matrix must hold eight stages")
        for row in stage_rows:
            cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
            self.assertGreaterEqual(len(cells), 6)
            self.assertTrue(cells[5], f"stage without recovery entry: {cells[0]}")

        commands = (REPO_ROOT / "skill/references/commands.md").read_text(encoding="utf-8")
        self.assertIn(
            "## Request routing at a glance",
            commands,
            "commands.md missing heading: ## Request routing at a glance",
        )
        self.assertIn("## Routing", commands, "commands.md missing heading: ## Routing")
        self.assertLess(
            commands.index("## Request routing at a glance"),
            commands.index("## Routing"),
            "routing table must sit above the ## Routing rules",
        )
        self.assertIn("| Request shape |", commands)

        index_path = REPO_ROOT / "dataset/retrieval/exemplars_index.json"
        self.assertTrue(index_path.is_file(), "exemplars_index.json missing")
        index = json.loads(index_path.read_text(encoding="utf-8"))
        self.assertIsInstance(index.get("records"), list)
        self.assertGreater(len(index["records"]), 0)

        for relative in (
            "docs/getting-started.md",
            "docs/faq.md",
            "docs/roadmap.md",
            "docs/project-positioning.md",
            "docs/zh/getting-started.md",
            "docs/zh/faq.md",
            "docs/zh/roadmap.md",
            "docs/zh/project-positioning.md",
        ):
            self.assertTrue((REPO_ROOT / relative).is_file(), f"missing user doc: {relative}")

        for relative, marker in (
            ("docs/getting-started.md", "exactly one place"),
            ("docs/zh/getting-started.md", "只放在一个位置"),
        ):
            text = (REPO_ROOT / relative).read_text(encoding="utf-8")
            self.assertIn(marker, text, f"{relative} must state the .env-only-place rule")
            self.assertIn("chmod 600", text, f"{relative} must carry chmod 600 guidance")
            self.assertIn("skill/.env", text)

        _assert_env_example_hygiene(
            (REPO_ROOT / "skill/.env.example").read_text(encoding="utf-8")
        )

    def test_doc_contract_mutation_isolation(self) -> None:
        scanned = _scanned_document_set()
        baseline_status = self._porcelain_status()
        with self.subTest(mutation="broken_link"):
            mutated = dict(scanned)
            mutated["skill/SKILL.md"] = (
                mutated["skill/SKILL.md"] + "\n\n[broken link](./definitely-missing.md)\n"
            )
            with self.assertRaises(AssertionError):
                _assert_forward_reachability(mutated)
            self.assertEqual(self._porcelain_status(), baseline_status, "broken-link mutation dirtied the tree")
        with self.subTest(mutation="superpowers_requires_exact_allowlist"):
            mutated = dict(scanned)
            scratch = "docs/superpowers/plans/2026-08-13-visual-quality-gate.md"
            mutated[scratch] = mutated[scratch] + "\n\n[scratch broken](./missing.md)\n"
            with self.assertRaises(AssertionError):
                _assert_forward_reachability(mutated)
            self.assertEqual(self._porcelain_status(), baseline_status, "scratch mutation dirtied the tree")
        with self.subTest(mutation="env_example_secret"):
            env = (REPO_ROOT / "skill/.env.example").read_text(encoding="utf-8")
            with self.assertRaises(AssertionError):
                _assert_env_example_hygiene(env + "\nVISION_REVIEW_API_KEY=sk-live-secret\n")
            self.assertEqual(self._porcelain_status(), baseline_status, "env mutation dirtied the tree")

    def _porcelain_status(self) -> str:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "status", "--porcelain"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout


if __name__ == "__main__":
    _ = unittest.main()
