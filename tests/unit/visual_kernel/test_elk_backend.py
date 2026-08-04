from __future__ import annotations

import copy
import json
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
            self.assertEqual(command[1], str(elk_backend._adapter_path()))
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
