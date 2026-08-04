from __future__ import annotations

import hashlib
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.orchestration import workspace as workspace_module
from skill.scripts.readme_showcase.contracts.run import (
    RUN_SCHEMA_VERSION,
    compute_run_id,
    validate_run_manifest,
)
from skill.scripts.readme_showcase.orchestration.state import reconcile_inputs
from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace


REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests/fixtures/contracts"


class RunManifestContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.target.mkdir()
        self.workspace_path = self.root / "workspace"
        self.configuration = {
            "mode": "readme",
            "project_type": "developer-tool",
            "locales": ["zh-Hans", "en", "en"],
            "scanner_profile": "balanced",
        }

    def create(self, *, clock=lambda: "2026-08-02T00:00:00Z") -> RunWorkspace:
        workspace = RunWorkspace(self.workspace_path, self.target)
        workspace.initialize(
            repository="Acfufu/readme-showcase.git",
            base_sha="a" * 40,
            configuration=self.configuration,
            clock=clock,
        )
        return workspace

    def test_fixtures_and_schema_are_strict(self) -> None:
        valid = json.loads((FIXTURES / "run-manifest-v1.valid.json").read_text())
        invalid = json.loads((FIXTURES / "run-manifest-v1.invalid.json").read_text())
        self.assertEqual(validate_run_manifest(valid), valid)
        with self.assertRaises(ContractError):
            validate_run_manifest(invalid)
        for mutation in (
            lambda value: value.update(created_at=None),
            lambda value: value["configuration"].update(locales=["zh-Hans", "en"]),
            lambda value: value["target"].update(repository="Acfufu/readme-showcase"),
        ):
            changed = json.loads(json.dumps(valid))
            mutation(changed)
            with self.assertRaises(ContractError):
                validate_run_manifest(changed)
        schema = json.loads((REPO_ROOT / "skill/schemas/run-manifest.v1.schema.json").read_text())
        self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
        self.assertFalse(schema["additionalProperties"])

    def test_current_revision_is_optional_nullable_and_strictly_relative(self) -> None:
        legacy = json.loads((FIXTURES / "run-manifest-v1.valid.json").read_text())
        self.assertNotIn("current_revision", legacy)
        self.assertEqual(validate_run_manifest(legacy), legacy)
        nullable = json.loads(json.dumps(legacy))
        nullable["current_revision"] = None
        self.assertEqual(validate_run_manifest(nullable), nullable)
        current = json.loads(json.dumps(legacy))
        current["current_revision"] = (
            "stages/04-generation-request/revisions/2/revision-request.json"
        )
        self.assertEqual(validate_run_manifest(current), current)
        for invalid in (
            "../revision-request.json",
            "/tmp/revision-request.json",
            "stages/04-generation-request/revisions/0/revision-request.json",
            "stages/04-generation-request/revisions/4/revision-request.json",
            "stages/04-generation-request/revisions/1/../revision-request.json",
            "stages/04-generation-request/revisions/1/other.json",
        ):
            changed = json.loads(json.dumps(legacy))
            changed["current_revision"] = invalid
            with self.subTest(invalid=invalid), self.assertRaises(ContractError) as raised:
                validate_run_manifest(changed)
            self.assertEqual(raised.exception.code, "E_REVISION_POINTER")

    def test_run_id_is_stable_and_excludes_time_paths_and_secrets(self) -> None:
        first = self.create(clock=lambda: "2026-08-02T00:00:00Z").read_manifest()
        other_root = self.root / "other"
        other_target = other_root / "target"
        other_target.mkdir(parents=True)
        second = RunWorkspace(other_root / "workspace", other_target).initialize(
            repository="acfufu/readme-showcase",
            base_sha="a" * 40,
            configuration={**self.configuration, "locales": ["en", "zh-Hans"]},
            clock=lambda: "2030-01-01T12:34:56Z",
        )
        expected = compute_run_id(
            repository="acfufu/readme-showcase",
            base_sha="a" * 40,
            configuration=self.configuration,
        )
        self.assertEqual(first["run_id"], second["run_id"])
        self.assertEqual(first["run_id"], expected)
        projection = canonical_json_bytes({"run_id": expected})
        self.assertNotIn(os.fspath(self.workspace_path).encode(), projection)
        self.assertNotIn(b"2030", projection)

    def test_base_or_configuration_change_invalidates_every_stage(self) -> None:
        manifest = self.create().read_manifest()
        for stage in manifest["stages"]:
            stage["status"] = "pass"
            stage["attempt"] = 1
        changed_base = reconcile_inputs(
            manifest,
            repository="acfufu/readme-showcase",
            base_sha="b" * 40,
            configuration=self.configuration,
            timestamp="2026-08-02T01:00:00Z",
        )
        self.assertTrue(all(stage["status"] == "stale" for stage in changed_base["stages"]))
        changed_config = reconcile_inputs(
            manifest,
            repository="acfufu/readme-showcase",
            base_sha="a" * 40,
            configuration={**self.configuration, "scanner_profile": "strict"},
            timestamp="2026-08-02T01:00:00Z",
        )
        self.assertTrue(all(stage["status"] == "stale" for stage in changed_config["stages"]))

    def test_lock_is_nonblocking_and_reports_stable_code(self) -> None:
        workspace = self.create()
        with workspace.lock():
            with self.assertRaises(ContractError) as raised:
                with workspace.lock():
                    self.fail("second holder acquired lock")
        self.assertEqual(raised.exception.code, "E_RUN_LOCKED")
        with workspace.lock():
            pass

    def test_workspace_rejects_symlink_and_target_descendant(self) -> None:
        linked = self.root / "linked-workspace"
        linked.symlink_to(self.workspace_path, target_is_directory=True)
        with self.assertRaises(ContractError) as symlink_error:
            RunWorkspace(linked, self.target)
        self.assertEqual(symlink_error.exception.code, "E_RUN_PATH")
        with self.assertRaises(ContractError) as inside_error:
            RunWorkspace(self.target / "workspace", self.target)
        self.assertEqual(inside_error.exception.code, "E_RUN_PATH")

    def test_manifest_current_pointer_and_attempts_are_canonical_and_immutable(self) -> None:
        workspace = self.create()
        attempt = workspace.append_attempt(1, "scan", {"result.json": b'{"ok":true}\n'})
        before = hashlib.sha256((attempt / "result.json").read_bytes()).hexdigest()
        manifest_bytes = (self.workspace_path / "run-manifest.json").read_bytes()
        self.assertEqual(manifest_bytes, canonical_json_bytes(json.loads(manifest_bytes)))
        current_bytes = (self.workspace_path / "stages/01-scan/current.json").read_bytes()
        self.assertEqual(current_bytes, canonical_json_bytes({"attempt": 1}))
        with self.assertRaises(ContractError) as raised:
            workspace.append_attempt(1, "scan", {"result.json": b'overwritten\n'}, attempt=1)
        self.assertEqual(raised.exception.code, "E_RUN_ATTEMPT_EXISTS")
        self.assertEqual(hashlib.sha256((attempt / "result.json").read_bytes()).hexdigest(), before)
        second = workspace.append_attempt(1, "scan", {"result.json": b'{"ok":false}\n'})
        self.assertEqual(second.name, "2")
        self.assertEqual((attempt / "result.json").read_bytes(), b'{"ok":true}\n')

    def test_nested_attempt_has_canonical_tree_and_private_modes(self) -> None:
        workspace = self.create()
        attempt = workspace.append_attempt(
            1,
            "scan",
            {
                "compiled/scenes/en/mobile.json": b"mobile\n",
                "compiled/scenes/zh/desktop.json": b"desktop\n",
            },
        )
        self.assertEqual(
            sorted(path.relative_to(attempt).as_posix() for path in attempt.rglob("*")),
            [
                "compiled",
                "compiled/scenes",
                "compiled/scenes/en",
                "compiled/scenes/en/mobile.json",
                "compiled/scenes/zh",
                "compiled/scenes/zh/desktop.json",
            ],
        )
        self.assertEqual((attempt / "compiled/scenes/en/mobile.json").read_bytes(), b"mobile\n")
        self.assertEqual((attempt / "compiled/scenes/zh/desktop.json").read_bytes(), b"desktop\n")
        for directory in (
            attempt,
            attempt / "compiled",
            attempt / "compiled/scenes",
            attempt / "compiled/scenes/en",
            attempt / "compiled/scenes/zh",
        ):
            self.assertEqual(stat.S_IMODE(directory.stat().st_mode), 0o700)
        for file in (
            attempt / "compiled/scenes/en/mobile.json",
            attempt / "compiled/scenes/zh/desktop.json",
        ):
            self.assertEqual(stat.S_IMODE(file.stat().st_mode), 0o600)

    def test_nested_attempt_rejects_unsafe_and_duplicate_normalized_paths(self) -> None:
        workspace = self.create()
        for path in ("", ".", "..", "a/../b", "a//b", "a/./b", "/absolute", "~/home", r"a\b", "a\x00b"):
            with self.subTest(path=path), self.assertRaises(ContractError) as raised:
                workspace.append_attempt(1, "scan", {path: b"bad"})
            self.assertEqual(raised.exception.code, "E_RUN_PATH")
        with self.assertRaises(ContractError) as raised:
            workspace.append_attempt(
                1,
                "scan",
                {"caf\u00e9/result.json": b"one", "cafe\u0301/result.json": b"two"},
            )
        self.assertEqual(raised.exception.code, "E_RUN_PATH")
        self.assertFalse((self.workspace_path / "stages/01-scan/attempts/1").exists())

    def test_nested_attempt_rejects_symlink_ancestor_and_rolls_back(self) -> None:
        workspace = self.create()
        outside = self.root / "outside"
        outside.mkdir()
        stage = self.workspace_path / "stages/01-scan"
        (stage / "attempts").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ContractError) as raised:
            workspace.append_attempt(1, "scan", {"compiled/scenes/en/desktop.json": b"bad"})
        self.assertEqual(raised.exception.code, "E_RUN_PATH")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((outside / "1").exists())

    def test_nested_attempt_rejects_parent_replacement_race(self) -> None:
        workspace = self.create()
        outside = self.root / "outside"
        outside.mkdir()
        real_open = workspace_module.os.open
        swapped = False

        def swap_before_open(path: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
            nonlocal swapped
            if path == "compiled" and not swapped:
                os.rename("compiled", "compiled.real", src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
                os.symlink(outside, "compiled", dir_fd=dir_fd)
                swapped = True
            return real_open(path, flags, mode, dir_fd=dir_fd)

        with mock.patch.object(workspace_module.os, "open", side_effect=swap_before_open):
            with self.assertRaises(ContractError) as raised:
                workspace.append_attempt(1, "scan", {"compiled/scenes/en/desktop.json": b"bad"})
        self.assertTrue(swapped)
        self.assertEqual(raised.exception.code, "E_RUN_PATH")
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse((self.workspace_path / "stages/01-scan/attempts/1").exists())

    def test_nested_attempt_mid_write_failure_rolls_back_recursively(self) -> None:
        workspace = self.create()
        previous = workspace.append_attempt(1, "scan", {"previous.json": b"keep\n"})
        manifest_before = (self.workspace_path / "run-manifest.json").read_bytes()
        current_before = (self.workspace_path / "stages/01-scan/current.json").read_bytes()
        original_write = workspace_module.os.write
        calls = 0

        def fail_mid_write(descriptor: int, data: bytes | bytearray | memoryview) -> int:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ContractError("E_TEST_WRITE", "injected nested write failure")
            return original_write(descriptor, data)

        with mock.patch.object(workspace_module.os, "write", side_effect=fail_mid_write):
            with self.assertRaises(ContractError) as raised:
                workspace.append_attempt(
                    1,
                    "scan",
                    {
                        "compiled/scenes/en/desktop.json": b"desktop\n",
                        "compiled/scenes/en/mobile.json": b"mobile\n",
                    },
                )
        self.assertEqual(raised.exception.code, "E_TEST_WRITE")
        self.assertEqual((self.workspace_path / "run-manifest.json").read_bytes(), manifest_before)
        self.assertEqual((self.workspace_path / "stages/01-scan/current.json").read_bytes(), current_before)
        self.assertEqual((previous / "previous.json").read_bytes(), b"keep\n")
        attempts = self.workspace_path / "stages/01-scan/attempts"
        self.assertEqual(sorted(path.name for path in attempts.iterdir()), ["1"])
        self.assertEqual(sorted(path.relative_to(attempts / "1").as_posix() for path in (attempts / "1").rglob("*")), ["previous.json"])

    def test_attempt_rejects_traversal_and_symlinked_stage(self) -> None:
        workspace = self.create()
        with self.assertRaises(ContractError) as traversal:
            workspace.append_attempt(1, "scan", {"../escape": b"bad"})
        self.assertEqual(traversal.exception.code, "E_RUN_PATH")
        stage = self.workspace_path / "stages/01-scan"
        stage.rmdir()
        stage.symlink_to(self.root / "outside", target_is_directory=True)
        with self.assertRaises(ContractError) as linked:
            workspace.append_attempt(1, "scan", {"result": b"bad"})
        self.assertEqual(linked.exception.code, "E_RUN_PATH")

    def test_write_manifest_rejects_stale_attempt_snapshot(self) -> None:
        workspace = self.create()
        stale = workspace.read_manifest()
        attempt = workspace.append_attempt(1, "scan", {"result.json": b"committed\n"})
        before = (self.workspace_path / "run-manifest.json").read_bytes()
        with self.assertRaises(ContractError) as raised:
            workspace.write_manifest(stale)
        self.assertEqual(raised.exception.code, "E_RUN_MANIFEST_STALE")
        self.assertEqual((self.workspace_path / "run-manifest.json").read_bytes(), before)
        self.assertEqual((attempt / "result.json").read_bytes(), b"committed\n")

    def test_manifest_write_failure_rolls_back_attempt_for_retry(self) -> None:
        workspace = self.create()
        atomic_write = workspace_module.write_canonical_json_atomic

        def fail_manifest(path: Path, value: object) -> None:
            if path.name == "run-manifest.json":
                raise ContractError("E_TEST_WRITE", "injected manifest write failure")
            atomic_write(path, value)

        with mock.patch.object(workspace_module, "write_canonical_json_atomic", side_effect=fail_manifest):
            with self.assertRaises(ContractError) as raised:
                workspace.append_attempt(1, "scan", {"result.json": b"uncommitted\n"})
        self.assertEqual(raised.exception.code, "E_TEST_WRITE")
        stage = self.workspace_path / "stages/01-scan"
        self.assertFalse((stage / "attempts/1").exists())
        self.assertFalse((stage / "current.json").exists())
        self.assertEqual(workspace.read_manifest()["stages"][0]["attempt"], 0)
        retry = workspace.append_attempt(1, "scan", {"result.json": b"committed\n"})
        self.assertEqual(retry.name, "1")
        self.assertEqual((retry / "result.json").read_bytes(), b"committed\n")
        with mock.patch.object(workspace_module, "write_canonical_json_atomic", side_effect=fail_manifest):
            with self.assertRaises(ContractError):
                workspace.append_attempt(1, "scan", {"result.json": b"uncommitted second\n"})
        self.assertFalse((stage / "attempts/2").exists())
        self.assertEqual(json.loads((stage / "current.json").read_bytes()), {"attempt": 1})
        self.assertEqual(workspace.read_manifest()["stages"][0]["attempt"], 1)
        second = workspace.append_attempt(1, "scan", {"result.json": b"committed second\n"})
        self.assertEqual(second.name, "2")


if __name__ == "__main__":
    unittest.main()
