from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Protocol, cast


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/glyphic"
BUILDER = REPO_ROOT / "scripts/build_glyphic_engine_lock.py"
CORE_SRI = (
    "sha512-+wWBhFXOkgS6ZtGk4cHPooIueXt01g3meuHHcZnapBtgPW8IXy8nDFPO1lZX"
    "eETVK+NZ6BeCu+blmD3QGr5hDw=="
)


class BuilderModule(Protocol):
    def tree_sha256(self, root: Path) -> str: ...


builder_spec = importlib.util.spec_from_file_location(
    "readme_showcase_engine_lock_builder",
    BUILDER,
)
if builder_spec is None or builder_spec.loader is None:
    raise RuntimeError(f"cannot load builder: {BUILDER}")
builder_module = importlib.util.module_from_spec(builder_spec)
builder_spec.loader.exec_module(builder_module)
builder = cast(BuilderModule, cast(object, builder_module))


class EngineLockBuilderTests(unittest.TestCase):
    def install(self, root: Path) -> Path:
        install_root = root / "install"
        core = install_root / "node_modules/@glyphicjs/core"
        schema = install_root / "node_modules/@glyphicjs/schema"
        (core / "dist").mkdir(parents=True)
        schema.mkdir(parents=True)
        shutil.copyfile(FIXTURES / "core-package.json", core / "package.json")
        shutil.copyfile(FIXTURES / "schema-package.json", schema / "package.json")
        shutil.copyfile(FIXTURES / "LICENSE", core / "LICENSE")
        (core / "dist/index.js").write_text("export const processSVG = 1;\n")
        return install_root

    def run_builder(
        self,
        install_root: Path,
        output: Path,
        *,
        sri: str = CORE_SRI,
        expected_tree_sha256: str,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--install-root",
                str(install_root),
                "--npm-sri",
                sri,
                "--node-version",
                "22.22.3",
                "--expected-tree-sha256",
                expected_tree_sha256,
                "--output",
                str(output),
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_lock_is_canonical_deterministic_and_complete(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_root = self.install(root)
            first = root / "first.json"
            second = root / "second.json"
            expected_tree_sha256 = builder.tree_sha256(
                install_root / "node_modules"
            )

            results = [
                self.run_builder(
                    install_root,
                    output,
                    expected_tree_sha256=expected_tree_sha256,
                )
                for output in (first, second)
            ]

            self.assertEqual([result.returncode for result in results], [0, 0])
            self.assertEqual(first.read_bytes(), second.read_bytes())
            lock = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(lock["package_version"], "1.3.1")
            self.assertEqual(lock["schema_package_version"], "1.1.1")
            self.assertEqual(lock["node_version"], "22.22.3")
            self.assertRegex(lock["tree_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(
                first.read_text(encoding="utf-8"),
                json.dumps(lock, sort_keys=True, separators=(",", ":")) + "\n",
            )

    def test_identity_sri_and_symlink_fail_without_replacement(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            cases: list[tuple[str, object]] = [
                ("schema", "9.9.9"),
                ("sri", "sha512-invalid"),
            ]
            if hasattr(os, "symlink"):
                cases.append(("symlink", True))
            for name, value in cases:
                with self.subTest(name=name):
                    case = root / name
                    install_root = self.install(case)
                    expected_tree_sha256 = builder.tree_sha256(
                        install_root / "node_modules"
                    )
                    output = case / "lock.json"
                    output.write_bytes(b"last-good")
                    sri = CORE_SRI
                    if name == "schema":
                        path = (
                            install_root
                            / "node_modules/@glyphicjs/schema/package.json"
                        )
                        package = json.loads(path.read_text(encoding="utf-8"))
                        package["version"] = value
                        path.write_text(json.dumps(package), encoding="utf-8")
                    elif name == "sri":
                        sri = str(value)
                    else:
                        os.symlink(
                            "package.json",
                            install_root
                            / "node_modules/@glyphicjs/core/linked-package.json",
                        )

                    result = self.run_builder(
                        install_root,
                        output,
                        sri=sri,
                        expected_tree_sha256=expected_tree_sha256,
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(output.read_bytes(), b"last-good")
                    self.assertNotIn("last-good", result.stderr)

    def test_empty_directories_change_digest_and_depth_is_bounded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            install_root = self.install(root)
            node_modules = install_root / "node_modules"
            before = builder.tree_sha256(node_modules)
            (node_modules / "empty").mkdir()
            after = builder.tree_sha256(node_modules)
            self.assertNotEqual(before, after)

            current = node_modules
            for index in range(65):
                current = current / f"d{index}"
                current.mkdir()
            output = root / "lock.json"
            result = self.run_builder(
                install_root,
                output,
                expected_tree_sha256=after,
            )
            self.assertEqual(result.returncode, 2)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
