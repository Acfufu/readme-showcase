from __future__ import annotations

import json
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = REPO_ROOT / ".github/workflows/ci.yml"
README = REPO_ROOT / "README.md"
README_ZH = REPO_ROOT / "README_zh.md"


class CiContractTests(unittest.TestCase):
    workflow = ""

    def setUp(self) -> None:
        self.workflow = WORKFLOW.read_text(encoding="utf-8")

    def test_python_lane_is_node_free_and_covers_supported_versions(self) -> None:
        self.assertIn("legacy-all:", self.workflow)
        self.assertIn("timeout-minutes: 15", self.workflow)
        self.assertIn("time python -m unittest discover -s tests -v", self.workflow)
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f'"{version}"', self.workflow)
        self.assertNotIn('"3.10"', self.workflow)
        self.assertIn("Python 3.11+", README.read_text(encoding="utf-8"))
        self.assertIn("Python 3.11+", README_ZH.read_text(encoding="utf-8"))
        self.assertNotIn("Python 3.10+", README.read_text(encoding="utf-8"))
        self.assertNotIn("Python 3.10+", README_ZH.read_text(encoding="utf-8"))
        self.assertIn('README_SHOWCASE_SKIP_NODE: "1"', self.workflow)
        self.assertIn("validate-dataset", self.workflow)
        self.assertIn("audit_readme.py README.md", self.workflow)
        self.assertIn("audit_readme.py README_zh.md", self.workflow)
        self.assertIn("-r requirements-dev.txt", self.workflow)
        self.assertIn(
            "python -m unittest tests.test_schema_parity tests.test_installer tests.test_ci_contract -v",
            self.workflow,
        )
        self.assertEqual(
            (REPO_ROOT / "requirements-dev.txt").read_text(encoding="utf-8"),
            "jsonschema==4.26.0\n",
        )
        self.assertNotIn("jsonschema>=", self.workflow)

    def test_elk_lanes_are_pinned_read_only_and_network_isolated(self) -> None:
        self.assertIn("permissions:\n  contents: read", self.workflow)
        self.assertNotIn("contents: write", self.workflow)
        self.assertNotIn("pull-requests: write", self.workflow)
        self.assertIn('node-version: "22.22.3"', self.workflow)
        self.assertIn("npm ci --ignore-scripts", self.workflow)
        self.assertIn("node_modules/elkjs/lib/elk.bundled.js", self.workflow)
        self.assertIn("sudo unshare --net", self.workflow)
        self.assertNotIn("docker ", self.workflow.lower())
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
            "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4",
        ):
            self.assertIn(pinned, self.workflow)
        self.assertGreaterEqual(
            self.workflow.count("persist-credentials: false"),
            5,
        )
        self.assertIn("expected_files =", self.workflow)
        self.assertIn("if-no-files-found: error", self.workflow)

    def test_motion_and_npm_package_include_only_pinned_development_source(self) -> None:
        self.assertIn("Pillow==11.3.0", self.workflow)
        self.assertIn("render_motion_gif.py", self.workflow)
        self.assertIn("npm-package:", self.workflow)
        self.assertIn("npm pack --dry-run", self.workflow)
        package = json.loads((REPO_ROOT / "package.json").read_text(encoding="utf-8"))
        self.assertEqual(package["bin"], {"readme-showcase": "scripts/install_skill.py"})
        self.assertEqual(package["os"], ["darwin", "linux"])
        self.assertEqual(package["devDependencies"], {"elkjs": "0.9.3"})
        for field in ("dependencies", "optionalDependencies", "peerDependencies"):
            self.assertNotIn(field, package)
        lock = json.loads((REPO_ROOT / "package-lock.json").read_text(encoding="utf-8"))
        self.assertEqual(lock["packages"]["node_modules/elkjs"]["version"], "0.9.3")
        self.assertTrue((REPO_ROOT / "skill/vendor/elkjs/LICENSE.md").is_file())

    def test_compiled_visual_kernel_lane_is_matrix_pinned_and_offline(self) -> None:
        self.assertIn("visual-kernel:", self.workflow)
        self.assertIn("name: visual-kernel / py${{ matrix.python-version }}", self.workflow)
        self.assertIn("timeout-minutes: 20", self.workflow)
        self.assertIn('python-version: ["3.11", "3.12", "3.13"]', self.workflow)
        for version in ("3.11", "3.12", "3.13"):
            self.assertIn(f'"{version}"', self.workflow)
        self.assertIn('node-version: "22.22.3"', self.workflow)
        self.assertIn("librsvg2-bin", self.workflow)
        self.assertIn("npm ci --ignore-scripts", self.workflow)
        self.assertIn("npm install --offline --ignore-scripts", self.workflow)
        self.assertIn("sudo unshare --net", self.workflow)
        self.assertIn("tests.test_schema_parity", self.workflow)
        self.assertIn("tests.test_documentation_contract", self.workflow)
        self.assertIn("tests.test_elk_adapter", self.workflow)
        self.assertIn("tests.unit.visual_kernel.test_elk_backend", self.workflow)
        self.assertIn("tests.e2e.test_compiled_visual_qa", self.workflow)
        self.assertIn("VISUAL_QA_EVIDENCE_DIR", self.workflow)
        self.assertIn("VISUAL_QA_EVIDENCE_DIR: /tmp/readme-showcase-visual-kernel-evidence", self.workflow)
        self.assertIn("rsvg-convert", self.workflow)
        self.assertIn("npm pack --dry-run", self.workflow)
        self.assertIn("persist-credentials: false", self.workflow)


if __name__ == "__main__":
    unittest.main()
