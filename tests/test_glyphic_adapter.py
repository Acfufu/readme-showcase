from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/glyphic"
ADAPTER = REPO_ROOT / "skill/scripts/render_glyphic.mjs"
BUILDER = REPO_ROOT / "scripts/build_glyphic_engine_lock.py"
SOURCE_COMMIT = "ed79edb1624e2de78041611971a963efaea5e080"
CORE_SRI = (
    "sha512-+wWBhFXOkgS6ZtGk4cHPooIueXt01g3meuHHcZnapBtgPW8IXy8nDFPO1lZX"
    "eETVK+NZ6BeCu+blmD3QGr5hDw=="
)


@unittest.skipIf(
    os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
    "Node/Glyphic tests run in isolated Node 22 lane",
)
class GlyphicAdapterTests(unittest.TestCase):
    def build_engine(
        self,
        root: Path,
        variant: str = "valid",
    ) -> tuple[Path, Path]:
        node_modules = root / "install/node_modules"
        core = node_modules / "@glyphicjs/core"
        schema = node_modules / "@glyphicjs/schema"
        (core / "dist").mkdir(parents=True)
        schema.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "core-package.json", core / "package.json")
        shutil.copyfile(FIXTURES / "schema-package.json", schema / "package.json")
        shutil.copyfile(FIXTURES / "LICENSE", core / "LICENSE")
        shutil.copyfile(FIXTURES / f"modules/{variant}.mjs", core / "dist/index.js")
        node_version = subprocess.check_output(
            ["node", "-p", "process.versions.node"],
            text=True,
        ).strip()
        lock_path = root / "glyphic-engine-lock.json"
        built = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--install-root",
                str(root / "install"),
                "--npm-sri",
                CORE_SRI,
                "--node-version",
                node_version,
                "--output",
                str(lock_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        return core, lock_path

    def run_adapter(
        self,
        root: Path,
        module_root: Path,
        lock: Path,
        input_name: str,
        *,
        timeout: int = 45,
        input_value: dict[str, Any] | None = None,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        run_dir = root / "run"
        run_dir.mkdir(exist_ok=True)
        input_path = run_dir / "diagram.glyphic.json"
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
                str(ADAPTER),
                "--module-root",
                str(module_root),
                "--engine-lock",
                str(lock),
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
            timeout=timeout,
            env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
        )
        return result, output, metadata

    def test_all_allowed_types_render_with_exact_metadata_binding(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module_root, lock = self.build_engine(root)
            for input_name in ("architecture.json", "flowchart.json", "c4.json"):
                with self.subTest(input_name=input_name):
                    result, output, metadata_path = self.run_adapter(
                        root,
                        module_root,
                        lock,
                        input_name,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
                    response = json.loads(result.stdout)
                    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                    raw = output.read_bytes()
                    digest = hashlib.sha256(raw).hexdigest()
                    self.assertEqual(response["status"], "available")
                    self.assertEqual(metadata["output_sha256"], digest)
                    self.assertEqual(metadata["run_hashes"], [digest, digest])
                    self.assertNotIn(str(module_root), json.dumps(metadata))

    def test_help_documents_exact_optional_cli(self) -> None:
        result = subprocess.run(
            ["node", str(ADAPTER), "--help"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--module-root", result.stdout)
        self.assertIn("--engine-lock", result.stdout)
        self.assertIn("--metadata", result.stdout)
        self.assertNotIn("mcp", result.stdout.lower())

    def test_absent_root_and_tree_mismatch_fail_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            mismatch_root = root / "mismatch"
            mismatch_root.mkdir()
            module_root, lock = self.build_engine(mismatch_root)
            result, output, metadata = self.run_adapter(
                root,
                root / "absent",
                lock,
                "architecture.json",
            )
            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "unavailable")
            self.assertFalse(output.exists())
            self.assertFalse(metadata.exists())

            module_root, lock = self.build_engine(root)
            (module_root / "dist/index.js").write_text(
                "\nexport const altered = true;\n",
                encoding="utf-8",
            )
            result, output, metadata = self.run_adapter(
                root,
                module_root,
                lock,
                "architecture.json",
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("E_ENGINE_TREE", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(metadata.exists())

    def test_lock_identity_fields_fail_closed(self) -> None:
        mutations = {
            "package_name": "glyphic",
            "package_version": "9.9.9",
            "schema_package_version": "9.9.9",
            "source_commit": "0" * 40,
            "license_spdx": "MIT",
            "npm_sri": "floating",
            "unknown": True,
        }
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for field, value in mutations.items():
                with self.subTest(field=field):
                    root = base / field
                    root.mkdir()
                    module_root, lock = self.build_engine(root)
                    payload = json.loads(lock.read_text(encoding="utf-8"))
                    payload[field] = value
                    lock.write_text(
                        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    result, output, metadata = self.run_adapter(
                        root,
                        module_root,
                        lock,
                        "architecture.json",
                    )
                    self.assertEqual(result.returncode, 2)
                    self.assertIn(
                        "E_ENGINE_LOCK" if field in {"npm_sri", "unknown"} else "E_ENGINE_IDENTITY",
                        result.stderr,
                    )
                    self.assertFalse(output.exists())
                    self.assertFalse(metadata.exists())

    def test_unsafe_nondeterministic_and_forbidden_input_preserve_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            cases: list[tuple[str, str, str, str, dict[str, Any] | None]] = [
                ("unsafe", "architecture.json", "invalid", "E_SVG_UNSAFE", None),
                (
                    "nondeterministic",
                    "architecture.json",
                    "nondeterministic",
                    "E_ENGINE_NONDETERMINISTIC",
                    None,
                ),
                ("oversize", "architecture.json", "invalid", "E_SVG_UNSAFE", None),
                ("valid", "invalid-coordinate.json", "invalid", "E_INPUT_SCHEMA", None),
            ]
            for forbidden_field in ("icon", "font"):
                payload = json.loads((FIXTURES / "architecture.json").read_text(encoding="utf-8"))
                if forbidden_field == "icon":
                    payload["nodes"][0]["icon"] = "fas-bolt"
                else:
                    payload["palette"]["font"] = "Inter"
                cases.append(("valid", "architecture.json", "invalid", "E_INPUT_SCHEMA", payload))
            invalid_type = json.loads((FIXTURES / "architecture.json").read_text(encoding="utf-8"))
            invalid_type["diagram_type"] = "canvas"
            cases.append(("valid", "architecture.json", "invalid", "E_INPUT_SCHEMA", invalid_type))
            for index, (
                variant,
                input_name,
                expected_status,
                expected_code,
                input_value,
            ) in enumerate(cases):
                with self.subTest(variant=variant, index=index):
                    root = base / f"{index}-{variant}"
                    root.mkdir()
                    module_root, lock = self.build_engine(root, variant)
                    run_dir = root / "run"
                    run_dir.mkdir()
                    output = run_dir / "diagram.svg"
                    metadata = run_dir / "diagram.engine.json"
                    output.write_bytes(b"last-good-svg")
                    metadata.write_bytes(b"last-good-metadata")
                    result, _, _ = self.run_adapter(
                        root,
                        module_root,
                        lock,
                        input_name,
                        input_value=input_value,
                    )
                    self.assertEqual(result.returncode, 2 if expected_code == "E_INPUT_SCHEMA" else 1)
                    self.assertEqual(json.loads(result.stdout)["status"], expected_status)
                    self.assertIn(expected_code, result.stderr)
                    self.assertEqual(output.read_bytes(), b"last-good-svg")
                    self.assertEqual(metadata.read_bytes(), b"last-good-metadata")

    def test_timeout_preserves_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            module_root, lock = self.build_engine(root, "timeout")
            run_dir = root / "run"
            run_dir.mkdir()
            output = run_dir / "diagram.svg"
            metadata = run_dir / "diagram.engine.json"
            output.write_bytes(b"last-good-svg")
            metadata.write_bytes(b"last-good-metadata")

            result, _, _ = self.run_adapter(
                root,
                module_root,
                lock,
                "architecture.json",
            )

            self.assertEqual(result.returncode, 1)
            self.assertEqual(json.loads(result.stdout)["status"], "timeout")
            self.assertIn("E_ENGINE_TIMEOUT", result.stderr)
            self.assertEqual(output.read_bytes(), b"last-good-svg")
            self.assertEqual(metadata.read_bytes(), b"last-good-metadata")


if __name__ == "__main__":
    unittest.main()
