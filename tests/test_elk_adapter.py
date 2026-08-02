from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/elk"
ADAPTER = REPO_ROOT / "skill/scripts/render_elk.mjs"
PACKAGE_INTEGRITY = (
    "sha512-f/ZeWvW/BCXbhGEf1Ujp29EASo/lk1FDnETgNKwJrsVvGZhUWCZyg3xLJjAsxf"
    "Omt8KjswHmI5EwCQcPMpOYhQ=="
)


@unittest.skipIf(
    os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
    "Node/ELK tests run in isolated Node 22 lane",
)
class ELKAdapterTests(unittest.TestCase):
    def run_adapter(
        self,
        root: Path,
        input_name: str,
        *,
        input_value: dict[str, Any] | None = None,
        adapter: Path = ADAPTER,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        run_dir = root / "run"
        run_dir.mkdir(exist_ok=True)
        input_path = run_dir / "diagram.diagram.json"
        if input_value is None:
            shutil.copyfile(FIXTURES / input_name, input_path)
        else:
            input_path.write_text(
                json.dumps(input_value, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
        output = run_dir / "diagram.svg"
        metadata = run_dir / "diagram.engine.json"
        result = subprocess.run(
            [
                "node",
                str(adapter),
                "--input",
                str(input_path),
                "--output",
                str(output),
                "--metadata",
                str(metadata),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
            env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
        )
        return result, output, metadata

    def test_all_allowed_types_render_with_exact_metadata_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for input_name in ("architecture.json", "flowchart.json", "c4.json"):
                with self.subTest(input_name=input_name):
                    root = base / input_name.removesuffix(".json")
                    root.mkdir()
                    result, output, metadata_path = self.run_adapter(root, input_name)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    response = json.loads(result.stdout)
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    digest = hashlib.sha256(output.read_bytes()).hexdigest()
                    self.assertEqual(response["status"], "available")
                    self.assertEqual(metadata["engine_kind"], "elk")
                    self.assertEqual(metadata["package_name"], "elkjs")
                    self.assertEqual(metadata["package_version"], "0.9.3")
                    self.assertEqual(metadata["package_integrity"], PACKAGE_INTEGRITY)
                    self.assertEqual(metadata["node_version"], "22.22.3")
                    self.assertEqual(metadata["license_spdx"], "EPL-2.0")
                    self.assertEqual(metadata["output_sha256"], digest)
                    self.assertEqual(metadata["run_hashes"], [digest, digest])
                    self.assertNotIn("must-not-reach-worker", json.dumps(metadata))

    def test_help_documents_self_contained_cli(self) -> None:
        result = subprocess.run(
            ["node", str(ADAPTER), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--input", result.stdout)
        self.assertIn("--metadata", result.stdout)
        self.assertNotIn("--module-root", result.stdout)
        self.assertNotIn("--engine-lock", result.stdout)

    def test_invalid_input_preserves_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cases: list[tuple[str, dict[str, Any] | None]] = [
                ("invalid-coordinate.json", None),
            ]
            for forbidden_field in ("icon", "font"):
                payload = json.loads((FIXTURES / "architecture.json").read_text(encoding="utf-8"))
                if forbidden_field == "icon":
                    payload["nodes"][0]["icon"] = "bolt"
                else:
                    payload["palette"]["font"] = "Inter"
                cases.append(("architecture.json", payload))
            for index, (input_name, payload) in enumerate(cases):
                with self.subTest(input_name=input_name, index=index):
                    root = base / str(index)
                    (root / "run").mkdir(parents=True)
                    output = root / "run/diagram.svg"
                    metadata = root / "run/diagram.engine.json"
                    output.write_bytes(b"last-good-svg")
                    metadata.write_bytes(b"last-good-metadata")
                    result, _, _ = self.run_adapter(root, input_name, input_value=payload)
                    self.assertEqual(result.returncode, 2)
                    self.assertIn("E_INPUT_SCHEMA", result.stderr)
                    self.assertEqual(output.read_bytes(), b"last-good-svg")
                    self.assertEqual(metadata.read_bytes(), b"last-good-metadata")

    def test_vendor_digest_mismatch_preserves_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            copied_skill = root / "skill"
            shutil.copytree(REPO_ROOT / "skill", copied_skill)
            module = copied_skill / "vendor/elkjs/lib/elk.bundled.js"
            module.write_bytes(module.read_bytes() + b"\n")
            (root / "run").mkdir()
            output = root / "run/diagram.svg"
            metadata = root / "run/diagram.engine.json"
            output.write_bytes(b"last-good-svg")
            metadata.write_bytes(b"last-good-metadata")

            result, _, _ = self.run_adapter(
                root,
                "architecture.json",
                adapter=copied_skill / "scripts/render_elk.mjs",
            )

            self.assertEqual(result.returncode, 2)
            self.assertIn("E_ENGINE_IDENTITY", result.stderr)
            self.assertEqual(output.read_bytes(), b"last-good-svg")
            self.assertEqual(metadata.read_bytes(), b"last-good-metadata")


if __name__ == "__main__":
    unittest.main()
