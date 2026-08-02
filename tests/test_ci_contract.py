from __future__ import annotations

import json
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
        self.assertIn('node-version: "22.22.3"', self.workflow)
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
        for mutable in (
            "actions/checkout@v4",
            "actions/setup-python@v5",
            "actions/setup-node@v4",
            "actions/upload-artifact@v4",
            "node:22-bookworm-slim",
        ):
            self.assertNotIn(mutable, self.workflow)
        for pinned in (
            "11d5960a326750d5838078e36cf38b85af677262",
            "a26af69be951a213d495a4c3e4e4022e16d87065",
            "49933ea5288caeca8642d1e84afbd3f7d6820020",
            "ea165f8d65b6e75b540449e92b4886f43607fa02",
            "sha256:16d364eebf6b62da439dc993d9b80940c78b0ca38438452f011ab9a25c752644",
            "57e9a8686f850adda3bd2bc639b6dfbfc1e119d40b92d9517ca472881d873e6e",
        ):
            self.assertIn(pinned, self.workflow)
        self.assertGreaterEqual(
            self.workflow.count("persist-credentials: false"),
            4,
        )
        self.assertIn("--expected-tree-sha256", self.workflow)
        self.assertIn("expected_files =", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)

    def test_motion_and_npm_package_are_isolated_without_dependency_payload(self) -> None:
        self.assertIn("Pillow==11.3.0", self.workflow)
        self.assertIn("render_motion_gif.py", self.workflow)
        self.assertIn("npm-package:", self.workflow)
        self.assertIn("npm pack --dry-run", self.workflow)
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["bin"], {"readme-showcase": "scripts/install_skill.py"})
        self.assertEqual(package["os"], ["darwin", "linux"])
        for field in (
            "dependencies",
            "devDependencies",
            "optionalDependencies",
            "peerDependencies",
        ):
            self.assertNotIn(field, package)
        for path in (
            REPO_ROOT / "package-lock.json",
            REPO_ROOT / "node_modules",
        ):
            self.assertFalse(path.exists(), path)


if __name__ == "__main__":
    unittest.main()
