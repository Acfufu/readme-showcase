from __future__ import annotations

import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"


class CiContractTests(unittest.TestCase):
    workflow = ""

    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_python_lane_is_node_free_and_covers_supported_versions(self) -> None:
        for version in ("3.10", "3.11", "3.12", "3.13"):
            self.assertIn(f'"{version}"', self.workflow)
        self.assertIn('README_SHOWCASE_SKIP_NODE: "1"', self.workflow)
        self.assertIn("validate-dataset", self.workflow)
        self.assertIn("audit_readme.py README.md", self.workflow)
        self.assertIn("audit_readme.py README_zh.md", self.workflow)

    def test_glyphic_lanes_are_pinned_read_only_and_network_isolated(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertNotIn("pull-requests: write", self.workflow)
        self.assertIn("node-version: 22", self.workflow)
        self.assertIn("@glyphicjs/core@1.3.1", self.workflow)
        self.assertIn("@glyphicjs/schema@1.1.1", self.workflow)
        self.assertIn(
            "sha512-+wWBhFXOkgS6ZtGk4cHPooIueXt01g3meuHHcZnapBtgPW8IXy8nDFPO1lZX"
            "eETVK+NZ6BeCu+blmD3QGr5hDw==",
            self.workflow,
        )
        self.assertIn("--network none", self.workflow)
        self.assertIn("--read-only", self.workflow)
        self.assertIn("--cap-drop ALL", self.workflow)
        self.assertIn("architecture flowchart c4", self.workflow)

    def test_motion_is_isolated_and_no_dependency_payload_is_tracked(self) -> None:
        self.assertIn("Pillow==11.3.0", self.workflow)
        self.assertIn("render_motion_gif.py", self.workflow)
        for path in (
            REPO_ROOT / "package.json",
            REPO_ROOT / "package-lock.json",
            REPO_ROOT / "node_modules",
        ):
            self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
