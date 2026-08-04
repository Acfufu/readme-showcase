from __future__ import annotations

import importlib
import subprocess
import sys
import unittest
from dataclasses import is_dataclass
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase import visual_kernel as facade
from skill.scripts.readme_showcase.visual_kernel.compiler import CompiledVisual
from skill.scripts.readme_showcase.visual_kernel.diagnostics import (
    VisualDiagnostic,
    VisualGateReport,
)
from skill.scripts.readme_showcase.visual_kernel.gates import validate_visual_gate_report as module_gate_validator
from skill.scripts.readme_showcase.visual_kernel.model import VisualSpec, validate_visual_spec as module_spec_validator
from skill.scripts.readme_showcase.visual_kernel.scene import Scene, validate_visual_scene as module_scene_validator
from skill.scripts.readme_showcase.visual_kernel.compiler import compile_visual as module_compiler


EXPECTED_EXPORTS = {
    "compile_visual",
    "validate_visual_spec",
    "validate_visual_scene",
    "validate_visual_gate_report",
    "load_compiled_visual",
    "CompiledVisual",
    "VisualSpec",
    "Scene",
    "VisualDiagnostic",
    "VisualGateReport",
}


class VisualKernelPublicApiTests(unittest.TestCase):
    def test_facade_exports_only_the_documented_surface(self) -> None:
        self.assertEqual(set(facade.__all__), EXPECTED_EXPORTS)
        self.assertIs(facade.compile_visual, module_compiler)
        self.assertIs(facade.validate_visual_spec, module_spec_validator)
        self.assertIs(facade.validate_visual_scene, module_scene_validator)
        self.assertIs(facade.validate_visual_gate_report, module_gate_validator)
        for internal in ("render_elk_geometry", "normalize_visual_spec", "build_compiled_artifacts"):
            self.assertNotIn(internal, facade.__all__)

    def test_installed_layout_import_exposes_the_same_surface(self) -> None:
        root = Path(__file__).resolve().parents[3]
        result = subprocess.run(
            [sys.executable, "-c", "import skill.scripts.readme_showcase.visual_kernel as vk; print(sorted(vk.__all__))"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
            env={"PATH": ":".join(Path(path).as_posix() for path in (Path(sys.executable).parent, Path("/usr/bin"))), "PYTHONPATH": str(root)},
        )
        self.assertIn("compile_visual", result.stdout)
        self.assertIn("load_compiled_visual", result.stdout)

    def test_immutable_result_and_diagnostic_types_are_frozen(self) -> None:
        for value_type in (CompiledVisual, VisualSpec, Scene, VisualDiagnostic, VisualGateReport):
            with self.subTest(value_type=value_type.__name__):
                self.assertTrue(is_dataclass(value_type))
                self.assertTrue(value_type.__dataclass_params__.frozen)

    def test_loader_rejects_mutable_identity_and_invalid_artifacts(self) -> None:
        with self.assertRaises(ContractError) as raised:
            facade.load_compiled_visual(
                {"compiled/inventory.json": b"{}"},
                bytearray(b"identity"),
            )
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

    def test_import_does_not_spawn_or_read_files(self) -> None:
        with (
            mock.patch("subprocess.run") as run,
            mock.patch("subprocess.Popen") as popen,
            mock.patch.object(Path, "read_bytes", side_effect=AssertionError("import read bytes")),
            mock.patch.object(Path, "iterdir", side_effect=AssertionError("import iterdir")),
        ):
            importlib.reload(facade)
        run.assert_not_called()
        popen.assert_not_called()


if __name__ == "__main__":
    unittest.main()
