from __future__ import annotations

import argparse
import re
import unittest
from pathlib import Path

from skill.scripts import pipeline_core, readme_pipeline
from skill.scripts.pipeline_contracts import canonical_json_bytes


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_CORE_BASELINE_LINES = 700
PUBLIC_SYMBOLS = {
    "build_pr_bundle",
    "check_publish_gate",
    "evaluate_generated_bundle",
    "retrieve_patterns",
    "scan_repository",
    "segment_markdown_blocks",
    "validate_dataset_manifest",
    "validate_generated_bundle",
}
PUBLIC_MODULES = (
    REPO_ROOT / "skill/scripts/pipeline_contracts.py",
    REPO_ROOT / "skill/scripts/pipeline_core.py",
    REPO_ROOT / "skill/scripts/readme_pipeline.py",
)


class ArchitectureBaselineTests(unittest.TestCase):
    def test_cli_and_python_compatibility_surface_is_stable(self) -> None:
        parser = readme_pipeline.build_parser()
        subcommands = next(
            action
            for action in parser._actions
            if isinstance(action, argparse._SubParsersAction)
        )
        legacy_commands = {
            "build-pr-bundle",
            "check-publish-gate",
            "evaluate",
            "import-benchmark",
            "retrieve",
            "scan",
            "validate-bundle",
            "validate-dataset",
        }
        self.assertEqual(
            set(subcommands.choices),
            legacy_commands | {"run", "resume", "status", "explain", "preview"},
        )
        self.assertEqual(
            {name for name in PUBLIC_SYMBOLS if callable(getattr(pipeline_core, name, None))},
            PUBLIC_SYMBOLS,
        )

    def test_canonical_json_and_error_codes_are_stable(self) -> None:
        self.assertEqual(
            canonical_json_bytes({"z": "证据", "schema_version": 1, "a": [3, 2, 1]}),
            b'{"a":[3,2,1],"schema_version":1,"z":"\xe8\xaf\x81\xe6\x8d\xae"}\n',
        )
        expected = set(
            (REPO_ROOT / "tests/fixtures/architecture/error_codes.txt")
            .read_text(encoding="utf-8")
            .splitlines()
        )
        observed = {
            code
            for path in PUBLIC_MODULES
            for code in re.findall(r"\bE_[A-Z0-9_]+\b", path.read_text(encoding="utf-8"))
        }
        self.assertEqual(observed, expected)

    def test_pipeline_core_does_not_grow_past_the_extraction_baseline(self) -> None:
        lines = (REPO_ROOT / "skill/scripts/pipeline_core.py").read_bytes().count(b"\n")
        self.assertLessEqual(lines, PIPELINE_CORE_BASELINE_LINES)


if __name__ == "__main__":
    unittest.main()
