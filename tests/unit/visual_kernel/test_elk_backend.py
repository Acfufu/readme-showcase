from __future__ import annotations

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.visual_kernel import elk_backend
from skill.scripts.readme_showcase.visual_kernel.elk_backend import (
    ElkGeometryResult,
    render_elk_geometry,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURE = ROOT / "tests" / "fixtures" / "elk" / "architecture.json"


class ElkBackendTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.envelope = json.loads(FIXTURE.read_text(encoding="utf-8"))

    def test_repository_layout_keeps_pinned_asset_paths(self) -> None:
        self.assertEqual(elk_backend._adapter_path(), ROOT / "skill" / "scripts" / "render_elk.mjs")
        elk_backend._verify_vendor_identity()

    def test_real_pinned_runs_are_identical_and_clean_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = render_elk_geometry(self.envelope, root)
            second = render_elk_geometry(self.envelope, root)

            self.assertIsInstance(first, ElkGeometryResult)
            self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
            self.assertEqual(first.identity, second.identity)
            self.assertEqual(first.geometry["engine"]["node_version"], "22.22.3")
            self.assertEqual(first.geometry["engine"]["package_version"], "0.9.3")
            self.assertEqual(first.metadata["run_hashes"], (first.metadata["output_sha256"],) * 2)
            self.assertEqual(list(root.iterdir()), [])
            with self.assertRaises(AttributeError):
                first.geometry = {}  # type: ignore[misc]

    def test_private_fake_process_receives_only_allowlisted_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = render_elk_geometry(self.envelope, root)
            observed: dict[str, object] = {}

            def fake_run(command: list[str], *, cwd: Path, environment: dict[str, str]):
                observed["command"] = command
                observed["cwd"] = cwd
                observed["environment"] = environment
                observed["adapter_bytes"] = Path(command[1]).read_bytes()
                geometry = Path(command[command.index("--geometry") + 1])
                metadata = Path(command[command.index("--metadata") + 1])
                geometry.write_bytes(canonical_json_bytes(expected.as_dict()["geometry"]))
                metadata.write_bytes(canonical_json_bytes(expected.as_dict()["metadata"]))
                return 0, b"", b""

            with mock.patch.object(elk_backend, "_run_adapter", side_effect=fake_run):
                actual = render_elk_geometry(self.envelope, root)

            self.assertEqual(actual.canonical_bytes(), expected.canonical_bytes())
            command = observed["command"]
            self.assertIsInstance(command, list)
            self.assertNotEqual(command[1], str(elk_backend._adapter_path()))
            self.assertEqual(observed["adapter_bytes"], elk_backend._adapter_path().read_bytes())
            self.assertEqual(
                set(observed["environment"]),
                {"PATH", "LC_ALL", "TZ", "TMPDIR"},
            )
            self.assertEqual(observed["environment"]["PATH"], str(Path(command[0]).parent))  # type: ignore[index]
            self.assertEqual(observed["environment"]["TMPDIR"], str(observed["cwd"]))  # type: ignore[index]
            self.assertEqual(list(root.iterdir()), [])

    def test_noncanonical_geometry_is_rejected_and_cleaned(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def fake_run(command: list[str], **_kwargs: object):
                Path(command[command.index("--geometry") + 1]).write_bytes(b'{ "schema_version": 1 }\n')
                return 0, b"", b""

            with mock.patch.object(elk_backend, "_run_adapter", side_effect=fake_run):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, root)
            self.assertEqual(raised.exception.code, "E_OUTPUT_GEOMETRY")
            self.assertEqual(list(root.iterdir()), [])

    def test_identity_mismatch_is_rejected_without_process_text(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            expected = render_elk_geometry(self.envelope, root)
            changed = copy.deepcopy(expected.as_dict()["geometry"])
            changed["engine"]["renderer_sha256"] = "0" * 64

            def fake_run(command: list[str], **_kwargs: object):
                Path(command[command.index("--geometry") + 1]).write_bytes(canonical_json_bytes(changed))
                return 0, b"", b""

            with mock.patch.object(elk_backend, "_run_adapter", side_effect=fake_run):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, root)
            self.assertEqual(raised.exception.code, "E_ENGINE_IDENTITY")
            self.assertNotIn("0" * 64, str(raised.exception))
            self.assertEqual(list(root.iterdir()), [])

    def test_adapter_identity_is_rechecked_after_process(self) -> None:
        adapter = elk_backend._adapter_path()
        initial_raw, initial_identity = elk_backend._read_adapter_snapshot(adapter)
        changed_identity = (
            initial_identity[0],
            initial_identity[1],
            initial_identity[2] + 1,
            initial_identity[3],
        )
        with tempfile.TemporaryDirectory() as temporary:
            with (
                mock.patch.object(
                    elk_backend,
                    "_read_adapter_snapshot",
                    side_effect=[(initial_raw, initial_identity), (initial_raw + b"x", changed_identity)],
                ),
                mock.patch.object(elk_backend, "_run_adapter", return_value=(0, b"", b"")),
            ):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, Path(temporary))
            self.assertEqual(raised.exception.code, "E_ENGINE_IDENTITY")

    def test_adapter_executes_verified_snapshot_not_mutable_live_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            adapter = root / "render_elk.mjs"
            original = b"export default 'verified';\n"
            adapter.write_bytes(original)
            observed: dict[str, object] = {}

            def fake_run(command: list[str], **_kwargs: object):
                adapter.write_bytes(b"MALICIOUS\n")
                executed = Path(command[1])
                observed["path"] = executed
                observed["bytes"] = executed.read_bytes()
                raise ContractError("E_ENGINE_PROCESS", "stop after execution-path observation")

            with (
                mock.patch.object(elk_backend, "_adapter_path", return_value=adapter),
                mock.patch.object(elk_backend, "_run_adapter", side_effect=fake_run),
            ):
                with self.assertRaises(ContractError):
                    render_elk_geometry(self.envelope, root)

            self.assertNotEqual(observed["path"], adapter)
            self.assertEqual(observed["bytes"], original)

    def test_timeout_secret_stderr_and_missing_node_are_redacted(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)

            def timeout_run(*_args: object, **_kwargs: object):
                raise ContractError("E_ENGINE_TIMEOUT", "pinned ELK process timed out")

            with mock.patch.object(elk_backend, "_run_adapter", side_effect=timeout_run):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, root)
            self.assertEqual(raised.exception.code, "E_ENGINE_TIMEOUT")

            def secret_run(*_args: object, **_kwargs: object):
                return 1, b"", b"E_ENGINE_RENDER: API_TOKEN=fixture-secret"

            with mock.patch.object(elk_backend, "_run_adapter", side_effect=secret_run):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, root)
            self.assertEqual(raised.exception.code, "E_ENGINE_RENDER")
            self.assertNotIn("fixture-secret", str(raised.exception))

            with mock.patch.object(elk_backend.shutil, "which", return_value=None):
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(self.envelope, root)
            self.assertEqual(raised.exception.code, "E_ENGINE_RUNTIME")
            self.assertEqual(list(root.iterdir()), [])

    def test_symlinked_attempt_directory_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            linked = root / "linked"
            linked.symlink_to(target, target_is_directory=True)
            with self.assertRaises(ContractError) as raised:
                render_elk_geometry(self.envelope, linked)
            self.assertEqual(raised.exception.code, "E_RUN_PATH")
            self.assertEqual(list(target.iterdir()), [])

    def test_flattened_installed_skill_resolves_pinned_adapter_and_vendor(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "installed"
            adapter = root / "scripts" / "render_elk.mjs"
            module_root = root / "scripts" / "readme_showcase" / "visual_kernel"
            module_root.mkdir(parents=True)
            shutil.copy2(ROOT / "skill" / "scripts" / "render_elk.mjs", adapter)
            shutil.copytree(ROOT / "skill" / "vendor" / "elkjs", root / "vendor" / "elkjs")
            fake_module = module_root / "elk_backend.py"
            run = root / "run"
            run.mkdir()

            with mock.patch.object(elk_backend, "__file__", str(fake_module)):
                self.assertEqual(elk_backend._adapter_path(), adapter.resolve())
                elk_backend._verify_vendor_identity()
                result = render_elk_geometry(self.envelope, run)

            self.assertEqual(result.geometry["engine"]["package_version"], "0.9.3")
            self.assertEqual(result.metadata["module_sha256"], elk_backend._MODULE_SHA256)
            self.assertEqual(list(run.iterdir()), [])

    def test_flattened_installed_skill_keeps_missing_and_symlink_failures(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "installed"
            adapter = root / "scripts" / "render_elk.mjs"
            module_root = root / "scripts" / "readme_showcase" / "visual_kernel"
            module_root.mkdir(parents=True)
            shutil.copy2(ROOT / "skill" / "scripts" / "render_elk.mjs", adapter)
            vendor = root / "vendor" / "elkjs"
            shutil.copytree(ROOT / "skill" / "vendor" / "elkjs", vendor)
            fake_module = module_root / "elk_backend.py"

            with mock.patch.object(elk_backend, "__file__", str(fake_module)):
                adapter.unlink()
                with self.assertRaises(ContractError) as missing_adapter:
                    elk_backend._read_adapter_snapshot(adapter)
                self.assertEqual(missing_adapter.exception.code, "E_ENGINE_IDENTITY")

                adapter.symlink_to(ROOT / "skill" / "scripts" / "render_elk.mjs")
                with self.assertRaises(ContractError) as symlink_adapter:
                    elk_backend._read_adapter_snapshot(adapter)
                self.assertEqual(symlink_adapter.exception.code, "E_ENGINE_IDENTITY")

                (vendor / "lib" / "elk.bundled.js").unlink()
                with self.assertRaises(ContractError) as missing_vendor:
                    elk_backend._verify_vendor_identity()
                self.assertEqual(missing_vendor.exception.code, "E_ENGINE_IDENTITY")

    def test_input_envelope_is_closed_before_subprocess(self) -> None:
        invalid = copy.deepcopy(self.envelope)
        invalid["unexpected"] = "secret"
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(elk_backend, "_run_adapter") as process:
                with self.assertRaises(ContractError) as raised:
                    render_elk_geometry(invalid, Path(temporary))
            self.assertEqual(raised.exception.code, "E_INPUT_SCHEMA")
            process.assert_not_called()


if __name__ == "__main__":
    unittest.main()
