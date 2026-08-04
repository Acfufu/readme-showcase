from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import time
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
LEGACY_HASHES = {
    "architecture.json": "56d0f7440385fe1f1e86255c11a10659c395647cbc9c67ffc4424fca233b7bfc",
    "flowchart.json": "fb70cc63fc71ff1d799b6a533d0b9f32c223833e7b235a1926c6a1254a2c4d8f",
    "c4.json": "0fecb54066a709e7e379b6dfbe42ab2cd62e9e0c77d4cc341ecb6cf7fed1f1ae",
}


@unittest.skipIf(
    os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
    "Node/ELK tests run in isolated Node 22 lane",
)
class ELKAdapterTests(unittest.TestCase):
    def invoke_adapter(
        self,
        input_path: Path,
        output_path: Path,
        metadata_path: Path,
        *,
        adapter: Path = ADAPTER,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                str(adapter),
                "--input",
                str(input_path),
                "--output",
                str(output_path),
                "--metadata",
                str(metadata_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
            env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
        )

    def invoke_geometry(
        self,
        input_path: Path,
        geometry_path: Path,
        metadata_path: Path,
        *,
        adapter: Path = ADAPTER,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "node",
                str(adapter),
                "--input",
                str(input_path),
                "--geometry",
                str(geometry_path),
                "--metadata",
                str(metadata_path),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
            timeout=45,
            env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
        )

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
        result = self.invoke_adapter(input_path, output, metadata, adapter=adapter)
        return result, output, metadata

    def run_geometry(
        self,
        root: Path,
        input_name: str,
        *,
        adapter: Path = ADAPTER,
    ) -> tuple[subprocess.CompletedProcess[str], Path, Path]:
        run_dir = root / "run"
        run_dir.mkdir(exist_ok=True)
        input_path = run_dir / "diagram.diagram.json"
        shutil.copyfile(FIXTURES / input_name, input_path)
        geometry = run_dir / "diagram.geometry.json"
        metadata = run_dir / "diagram.engine.json"
        result = self.invoke_geometry(input_path, geometry, metadata, adapter=adapter)
        return result, geometry, metadata

    def test_symlinked_output_parent_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            outside = base / "outside"
            outside.mkdir()
            input_path = outside / "diagram.diagram.json"
            shutil.copyfile(FIXTURES / "architecture.json", input_path)
            outside_output = outside / "diagram.svg"
            outside_metadata = outside / "diagram.engine.json"
            outside_output.write_bytes(b"outside-sentinel-svg")
            outside_metadata.write_bytes(b"outside-sentinel-metadata")
            linked_parent = base / "run"
            linked_parent.symlink_to(outside, target_is_directory=True)

            result = self.invoke_adapter(
                linked_parent / input_path.name,
                linked_parent / outside_output.name,
                linked_parent / outside_metadata.name,
            )

            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("E_OUTPUT_PATH", result.stderr)
            self.assertEqual(outside_output.read_bytes(), b"outside-sentinel-svg")
            self.assertEqual(
                outside_metadata.read_bytes(), b"outside-sentinel-metadata"
            )
            self.assertTrue(linked_parent.is_symlink())

    def test_final_path_symlinks_are_rejected(self) -> None:
        for linked_name in ("diagram.svg", "diagram.engine.json"):
            with self.subTest(linked_name=linked_name), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                run = base / "run"
                outside = base / "outside"
                run.mkdir()
                outside.mkdir()
                input_path = run / "diagram.diagram.json"
                shutil.copyfile(FIXTURES / "architecture.json", input_path)
                output = run / "diagram.svg"
                metadata = run / "diagram.engine.json"
                output.write_bytes(b"last-good-svg")
                metadata.write_bytes(b"last-good-metadata")
                sentinel = outside / linked_name
                sentinel.write_bytes(b"outside-sentinel")
                (run / linked_name).unlink()
                (run / linked_name).symlink_to(sentinel)

                result = self.invoke_adapter(input_path, output, metadata)

                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("E_OUTPUT_PATH", result.stderr)
                self.assertTrue((run / linked_name).is_symlink())
                self.assertEqual(sentinel.read_bytes(), b"outside-sentinel")
                if linked_name == output.name:
                    self.assertEqual(metadata.read_bytes(), b"last-good-metadata")
                else:
                    self.assertEqual(output.read_bytes(), b"last-good-svg")

    def test_mixed_real_output_roots_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            run = base / "run"
            other = base / "other"
            run.mkdir()
            other.mkdir()
            input_path = run / "diagram.diagram.json"
            shutil.copyfile(FIXTURES / "architecture.json", input_path)
            output = run / "diagram.svg"
            metadata = other / "diagram.engine.json"

            result = self.invoke_adapter(input_path, output, metadata)

            self.assertEqual(result.returncode, 2)
            self.assertIn("E_OUTPUT_PATH", result.stderr)
            self.assertFalse(output.exists())
            self.assertFalse(metadata.exists())

    def test_parent_replacement_race_preserves_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            run = base / "run"
            outside = base / "outside"
            run.mkdir()
            outside.mkdir()
            input_path = run / "diagram.diagram.json"
            shutil.copyfile(FIXTURES / "architecture.json", input_path)
            output = run / "diagram.svg"
            metadata = run / "diagram.engine.json"
            output.write_bytes(b"last-good-svg")
            metadata.write_bytes(b"last-good-metadata")
            outside_output = outside / output.name
            outside_metadata = outside / metadata.name
            outside_output.write_bytes(b"outside-sentinel-svg")
            outside_metadata.write_bytes(b"outside-sentinel-metadata")

            copied_skill = base / "skill"
            shutil.copytree(REPO_ROOT / "skill", copied_skill)
            adapter = copied_skill / "scripts/render_elk.mjs"
            source = adapter.read_text(encoding="utf-8")
            marker = '    await assertDirectoryIdentity(parent);\n    await validateDestination(parent.path, name, "E_OUTPUT_PATH");'
            self.assertIn(marker, source)
            adapter.write_text(
                source.replace(
                    marker,
                    '    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));\n'
                    f"{marker}",
                    1,
                ),
                encoding="utf-8",
            )

            process = subprocess.Popen(
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
            )
            replaced = False
            backup = base / "run-real"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                if any(run.glob(".diagram.svg.tmp-*")):
                    run.rename(backup)
                    run.symlink_to(outside, target_is_directory=True)
                    replaced = True
                    break
                time.sleep(0.001)
            try:
                stdout, stderr = process.communicate(timeout=45)
            finally:
                if run.is_symlink():
                    run.unlink()
                if backup.exists():
                    backup.rename(run)

            self.assertTrue(replaced, "test did not observe output parent replacement")
            self.assertNotEqual(process.returncode, 0, stdout)
            self.assertIn("E_OUTPUT_PATH", stderr)
            self.assertEqual((run / output.name).read_bytes(), b"last-good-svg")
            self.assertEqual((run / metadata.name).read_bytes(), b"last-good-metadata")
            self.assertEqual(outside_output.read_bytes(), b"outside-sentinel-svg")
            self.assertEqual(
                outside_metadata.read_bytes(), b"outside-sentinel-metadata"
            )

    def test_real_directory_replacement_preserves_last_good_and_cleans_temp(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            run = base / "run"
            outside = base / "outside"
            run.mkdir()
            outside.mkdir()
            input_path = run / "diagram.diagram.json"
            shutil.copyfile(FIXTURES / "architecture.json", input_path)
            output = run / "diagram.svg"
            metadata = run / "diagram.engine.json"
            output.write_bytes(b"last-good-svg")
            metadata.write_bytes(b"last-good-metadata")
            outside_output = outside / output.name
            outside_metadata = outside / metadata.name
            outside_output.write_bytes(b"outside-sentinel-svg")
            outside_metadata.write_bytes(b"outside-sentinel-metadata")

            copied_skill = base / "skill"
            shutil.copytree(REPO_ROOT / "skill", copied_skill)
            adapter = copied_skill / "scripts/render_elk.mjs"
            source = adapter.read_text(encoding="utf-8")
            marker = '    await assertDirectoryIdentity(parent);\n    await validateDestination(parent.path, name, "E_OUTPUT_PATH");'
            self.assertIn(marker, source)
            adapter.write_text(
                source.replace(
                    marker,
                    '    await new Promise((resolvePromise) => setTimeout(resolvePromise, 250));\n'
                    f"{marker}",
                    1,
                ),
                encoding="utf-8",
            )

            process = subprocess.Popen(
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
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={**os.environ, "GITHUB_TOKEN": "must-not-reach-worker"},
            )
            replaced = False
            original = base / "run-original"
            replacement_output = b"replacement-sentinel-svg"
            replacement_metadata = b"replacement-sentinel-metadata"
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and process.poll() is None:
                if any(run.glob(".diagram.svg.tmp-*")):
                    run.rename(original)
                    run.mkdir()
                    (run / output.name).write_bytes(replacement_output)
                    (run / metadata.name).write_bytes(replacement_metadata)
                    replaced = True
                    break
                time.sleep(0.001)
            try:
                stdout, stderr = process.communicate(timeout=45)
            finally:
                replacement = base / "run-replacement"
                if run.exists() and not run.is_symlink():
                    run.rename(replacement)
                if original.exists():
                    original.rename(run)

            self.assertTrue(replaced, "test did not observe real output-parent replacement")
            self.assertNotEqual(process.returncode, 0, stdout)
            self.assertIn("E_OUTPUT_PATH", stderr)
            self.assertEqual(output.read_bytes(), b"last-good-svg")
            self.assertEqual(metadata.read_bytes(), b"last-good-metadata")
            self.assertEqual(outside_output.read_bytes(), b"outside-sentinel-svg")
            self.assertEqual(
                outside_metadata.read_bytes(), b"outside-sentinel-metadata"
            )
            self.assertEqual(
                list(run.glob(".diagram.svg.tmp-*"))
                + list(run.glob(".diagram.engine.json.tmp-*")),
                [],
            )

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
                    self.assertEqual(digest, LEGACY_HASHES[input_name])
                    self.assertEqual(
                        metadata["renderer_sha256"],
                        hashlib.sha256(ADAPTER.read_bytes()).hexdigest(),
                    )
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

    def test_geometry_mode_is_canonical_and_deterministic(self) -> None:
        for input_name in ("architecture.json", "flowchart.json", "c4.json"):
            with self.subTest(input_name=input_name), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                first, geometry, metadata_path = self.run_geometry(root, input_name)
                self.assertEqual(first.returncode, 0, first.stderr)
                first_raw = geometry.read_bytes()
                first_metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
                parsed = json.loads(first_raw)
                self.assertEqual(
                    set(parsed), {"schema_version", "engine", "canvas", "groups", "nodes", "ports", "edges"}
                )
                self.assertEqual(parsed["schema_version"], 1)
                self.assertEqual(
                    set(parsed["engine"]),
                    {
                        "engine_kind",
                        "package_name",
                        "package_version",
                        "package_sha256",
                        "module_sha256",
                        "node_version",
                        "renderer_sha256",
                    },
                )
                self.assertEqual(parsed["engine"]["engine_kind"], "elk")
                self.assertEqual(parsed["engine"]["package_version"], "0.9.3")
                self.assertEqual(first_metadata["output_sha256"], hashlib.sha256(first_raw).hexdigest())
                self.assertEqual(first_metadata["run_hashes"], [first_metadata["output_sha256"]] * 2)
                for collection in ("groups", "nodes", "ports", "edges"):
                    ids = [item["id"] for item in parsed[collection]]
                    self.assertEqual(ids, sorted(ids))
                    self.assertEqual(len(ids), len(set(ids)))
                for item in [*parsed["groups"], *parsed["nodes"], *parsed["ports"]]:
                    for field in ("x", "y", "width", "height"):
                        self.assertIsInstance(item[field], int)
                        self.assertGreaterEqual(item[field], 0)
                        self.assertLessEqual(item[field], 20_000)
                root2 = root / "second"
                root2.mkdir()
                second, geometry2, _ = self.run_geometry(root2, input_name)
                self.assertEqual(second.returncode, 0, second.stderr)
                self.assertEqual(first_raw, geometry2.read_bytes())

    def test_geometry_output_path_errors_preserve_last_good(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            run = base / "run"
            outside = base / "outside"
            run.mkdir()
            outside.mkdir()
            input_path = run / "diagram.diagram.json"
            shutil.copyfile(FIXTURES / "architecture.json", input_path)
            geometry = run / "diagram.geometry.json"
            metadata = run / "diagram.engine.json"
            geometry.write_bytes(b"last-good-geometry")
            metadata.write_bytes(b"last-good-metadata")
            sentinel = outside / geometry.name
            sentinel.write_bytes(b"outside-sentinel")
            geometry.unlink()
            geometry.symlink_to(sentinel)
            result = self.invoke_geometry(input_path, geometry, metadata)
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertTrue(geometry.is_symlink())
            self.assertIn("E_OUTPUT_GEOMETRY", result.stderr)
            self.assertEqual(sentinel.read_bytes(), b"outside-sentinel")
            self.assertEqual(metadata.read_bytes(), b"last-good-metadata")

            linked_parent = base / "linked-run"
            linked_parent.symlink_to(run, target_is_directory=True)
            result = self.invoke_geometry(
                linked_parent / input_path.name,
                linked_parent / geometry.name,
                linked_parent / metadata.name,
            )
            self.assertNotEqual(result.returncode, 0, result.stdout)
            self.assertIn("E_RUN_PATH", result.stderr)

    def test_geometry_validation_rejects_malformed_layout_and_cleans_temps(self) -> None:
        mutations = {
            "missing": "delete layout.children[0].width;",
            "nan": "layout.children[0].x = Number.NaN;",
            "negative": "layout.children[0].x = -1;",
            "oversize": "layout.children[0].x = 20001;",
        }
        for name, mutation in mutations.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as temporary:
                base = Path(temporary)
                copied_skill = base / "skill"
                shutil.copytree(REPO_ROOT / "skill", copied_skill)
                adapter = copied_skill / "scripts/render_elk.mjs"
                source = adapter.read_text(encoding="utf-8")
                marker = "    if (args[\"--geometry\"] !== undefined) {\n"
                self.assertIn(marker, source)
                adapter.write_text(source.replace(marker, f"    {mutation}\n{marker}", 1), encoding="utf-8")
                run = base / "run"
                run.mkdir()
                geometry = run / "diagram.geometry.json"
                metadata = run / "diagram.engine.json"
                geometry.write_bytes(b"last-good-geometry")
                metadata.write_bytes(b"last-good-metadata")
                result, _, _ = self.run_geometry(base, "architecture.json", adapter=adapter)
                self.assertNotEqual(result.returncode, 0, result.stdout)
                self.assertIn("E_OUTPUT_GEOMETRY", result.stderr)
                self.assertEqual(geometry.read_bytes(), b"last-good-geometry")
                self.assertEqual(metadata.read_bytes(), b"last-good-metadata")
                self.assertEqual(list(run.glob(".diagram.geometry.json.tmp-*")), [])


if __name__ == "__main__":
    unittest.main()
