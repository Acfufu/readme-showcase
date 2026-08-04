from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skill/SKILL.md"
VISUAL_COMPILER = REPO_ROOT / "skill/references/visual-compiler.md"
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"

_STAGES = (
    "scan",
    "retrieve",
    "plan-import",
    "generation-request",
    "candidate",
    "bundle-assemble",
    "validation",
    "evaluation",
)
_DOCUMENTED_PIPELINE_COMMANDS = frozenset(
    {
        "validate-dataset",
        "scan",
        "retrieve",
        "validate-bundle",
        "evaluate",
        "import-benchmark",
        "build-pr-bundle",
        "check-publish-gate",
        "run",
        "resume",
        "status",
        "explain",
        "preview",
    }
)


def _assert_compiled_documentation_contract(root: Path) -> None:
    """Check the compiled reference's boundaries without trusting its prose."""

    skill = (root / "skill/SKILL.md").read_text(encoding="utf-8")
    reference = (root / "skill/references/visual-compiler.md").read_text(encoding="utf-8")
    combined = "\n".join((skill, reference))
    flat = " ".join(combined.split())

    if "references/visual-compiler.md" not in skill:
        raise AssertionError("SKILL.md must link the compiled visual reference")
    if "visual-kernel-clean-room.md" not in reference:
        raise AssertionError("compiled reference must link the clean-room note")
    if "diagram_route: \"compiled\"" not in reference and "`compiled`" not in reference:
        raise AssertionError("compiled route must be explicitly opt-in")
    if not all(route in flat for route in ("`none`", "`static`", "`elk`")):
        raise AssertionError("ordinary diagram routes must remain documented")
    if "one README Agent" not in flat or "one-README-Agent" not in flat:
        raise AssertionError("one-agent ownership must remain explicit")
    for command in _DOCUMENTED_PIPELINE_COMMANDS:
        if f"`{command}`" not in flat:
            raise AssertionError(f"current command is missing from the docs: {command}")

    observed = tuple(re.findall(r"^\d+\. `([^`]+)`$", reference, re.MULTILINE))
    if observed != _STAGES:
        raise AssertionError(f"compiled stage order changed: {observed!r}")
    if re.search(r"(?im)^\s*(?:9|9th|nine)\s*[-.)]?\s*(?:stage|step)\b", combined):
        raise AssertionError("compiled documentation must not introduce a ninth stage")
    if re.search(r"(?i)\bstage\s*(?:9|9th|nine)\b", combined):
        raise AssertionError("compiled documentation must not introduce a ninth stage")

    # A state path must never be adjacent to the target.  The ordinary prose
    # may say that state is outside the target, but must not show a target path
    # that contains the state directory or a per-run directory.
    if re.search(r"(?i)(?:\$TARGET|target(?:\s+repository)?)[^\n`]{0,80}/(?:state/readme-showcase|\.readme-showcase-run-)", combined):
        raise AssertionError("run state must not be target-adjacent")
    if re.search(r"(?i)/(?:state/readme-showcase|\.readme-showcase-run-)[^\n`]{0,80}(?:\$TARGET|target(?:\s+repository)?)", combined):
        raise AssertionError("run state must not be target-adjacent")

    for line in combined.splitlines():
        lowered = line.casefold()
        if re.search(r"\b(?:motion|animation)\b[^.\n]{0,60}\bautomatically?\b", lowered):
            raise AssertionError("motion must remain opt-in, never automatic")
        if re.search(r"\blive[- ]provider\b|\blive\s+(?:delivery|publication|publish)\b", lowered):
            raise AssertionError("documentation must not claim live delivery/provider support")
        if re.search(r"\b(?:browser|production)[- ](?:validated|tested|ready)\b", lowered):
            raise AssertionError("unverified browser/production claim")

    help_result = subprocess.run(
        [sys.executable, str(PIPELINE), "--help"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if help_result.returncode != 0:
        raise AssertionError(f"readme_pipeline --help failed: {help_result.stderr}")
    help_text = help_result.stdout
    missing = sorted(command for command in _DOCUMENTED_PIPELINE_COMMANDS if command not in help_text)
    if missing:
        raise AssertionError(f"documented commands missing from --help: {missing}")


def _copy_docs_for_mutation(destination: Path) -> None:
    (destination / "skill/references").mkdir(parents=True)
    shutil.copyfile(SKILL, destination / "skill/SKILL.md")
    shutil.copyfile(VISUAL_COMPILER, destination / "skill/references/visual-compiler.md")


class SkillWorkflowTests(unittest.TestCase):
    def test_one_agent_order_modes_and_routes_are_explicit(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertIn("name: readme-showcase", text)
        self.assertIn("one README Agent", flat)
        self.assertEqual(
            set(re.findall(r"\\*\\*(README|Asset-only|Audit-only) mode\\*\\*", text)),
            {"README", "Asset-only", "Audit-only"},
        )
        ordered_commands = (
            "validate-dataset",
            " scan ",
            " retrieve ",
            "render_elk.mjs",
            "validate-bundle",
            " evaluate ",
            "build-pr-bundle",
            "check-publish-gate",
        )
        positions = [flat.index(command) for command in ordered_commands]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("| `static` |", text)
        self.assertIn("| `elk` |", text)
        self.assertIn("raw SVG", flat)
        self.assertIn("last-known-good", flat)
        self.assertIn("Retrieval patterns are not target facts", flat)
        self.assertIn("Never delegate README truth or writing", flat)
        self.assertIn("Never publish from evaluation success alone", flat)
        self.assertIn("locale-matched text-bearing assets", flat)
        self.assertIn('data-readme-language="neutral"', text)

    def test_compiled_reference_documents_opt_in_outputs_limits_and_local_boundary(self) -> None:
        _assert_compiled_documentation_contract(REPO_ROOT)
        reference = VISUAL_COMPILER.read_text(encoding="utf-8")
        flat = " ".join(reference.split())
        from skill.scripts.audit_readme import (
            MAX_SVG_BYTES,
            MAX_SVG_DEPTH,
            MAX_SVG_DIMENSION,
            MAX_SVG_ELEMENTS,
            MAX_SVG_PATHS,
        )
        from skill.scripts.readme_showcase.visual_kernel.geometry import (
            _MAX_COORDINATE,
        )
        from skill.scripts.readme_showcase.visual_kernel.security import (
            MAX_COMPILED_BYTES,
            MAX_GATE_BYTES,
            MAX_INTERACTION_BYTES,
            MAX_SCENE_BYTES,
            MAX_TIMELINE_BYTES,
            MAX_VISUAL_SPEC_BYTES,
        )
        from skill.scripts.readme_showcase.visual_kernel.theme import _VARIANT_DEFAULTS

        for expected in (
            "README Plan v3",
            "Claim Map v3",
            "Visual Spec v1",
            "stages/05-candidate/",
            "stages/06-bundle-assemble/attempts/<attempt>/",
            "generated-readme-bundle.json",
            "asset-manifest.json",
            "compiled/scenes/<locale>/<variant>.json",
            "compiled/gates/<locale>/<variant>.json",
            "compiled/timeline/<locale>/<variant>.json",
            "compiled/interaction/<locale>/<variant>.json",
            "state/readme-showcase/",
            "retention as `manual`",
            "check-publish-gate",
            "no authority to push",
        ):
            self.assertIn(expected, flat)
        self.assertIn(f"{MAX_VISUAL_SPEC_BYTES // 1024} KiB", flat)
        self.assertIn(f"{MAX_SCENE_BYTES // (1024 * 1024)} MiB", flat)
        self.assertIn(f"{MAX_GATE_BYTES // 1024} KiB", flat)
        self.assertIn(f"{MAX_TIMELINE_BYTES // 1024} KiB", flat)
        self.assertIn(f"{MAX_INTERACTION_BYTES // 1024} KiB", flat)
        self.assertIn(f"{MAX_COMPILED_BYTES // (1024 * 1024)} MiB", flat)
        self.assertIn(f"{MAX_SVG_ELEMENTS:,} elements", flat)
        self.assertIn(f"{MAX_SVG_PATHS:,} paths", flat)
        self.assertIn(f"depth {MAX_SVG_DEPTH}", flat)
        self.assertIn(f"dimension {MAX_SVG_DIMENSION:,}", flat)
        self.assertIn(f"no larger than {_MAX_COORDINATE:,}", flat)
        self.assertIn(f"{MAX_SVG_BYTES // (1024 * 1024)} MiB", flat)
        self.assertIn(f"{_VARIANT_DEFAULTS['desktop']['width']:,}-wide", flat)
        self.assertIn(f"at least {_VARIANT_DEFAULTS['desktop']['min_font_size']} units", flat)
        self.assertIn(f"at {_VARIANT_DEFAULTS['desktop']['render_width']} px", flat)
        self.assertIn(f"at most {_VARIANT_DEFAULTS['mobile']['width']}", flat)
        self.assertIn(f"at least {_VARIANT_DEFAULTS['mobile']['min_font_size']}", flat)
        self.assertIn(f"at {_VARIANT_DEFAULTS['mobile']['render_width']} px", flat)

    def test_compiled_documentation_negative_contract_rejects_boundary_mutations(self) -> None:
        mutations = {
            "ninth_stage": "\n9. `publish`\n",
            "target_adjacent_state": "\nRun state: $TARGET/.readme-showcase-run-bad/\n",
            "automatic_motion": "\nMotion is generated automatically for every route.\n",
            "live_delivery": "\nThe compiler supports live delivery to providers.\n",
            "unverified_claim": "\nThe compiled route is browser-validated and production-ready.\n",
        }
        for name, injection in mutations.items():
            with self.subTest(mutation=name), tempfile.TemporaryDirectory(prefix="skill-docs-") as temporary:
                root = Path(temporary)
                _copy_docs_for_mutation(root)
                path = root / "skill/references/visual-compiler.md"
                path.write_text(path.read_text(encoding="utf-8") + injection, encoding="utf-8")
                with self.assertRaises(AssertionError):
                    _assert_compiled_documentation_contract(root)


if __name__ == "__main__":
    unittest.main()
