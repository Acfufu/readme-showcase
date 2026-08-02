from __future__ import annotations

import errno
import fcntl
import hashlib
import os
import secrets
import stat
from pathlib import Path
from types import TracebackType
from typing import Any, Callable, Mapping

from ...pipeline_contracts import (
    ContractError,
    _absolute,
    _open_directory,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object_bytes,
    write_canonical_json_atomic,
)
from ..contracts.run import (
    RUN_SCHEMA_VERSION,
    STAGE_NAMES,
    canonical_repository,
    compute_run_id,
    normalize_configuration,
    validate_run_manifest,
)
from .state import RunState, StageState, initial_stages


Clock = Callable[[], str]


class _RunLock:
    def __init__(self, workspace: Path) -> None:
        self.workspace = workspace
        self.descriptor = -1

    def __enter__(self) -> _RunLock:
        parent = _open_directory(self.workspace, create=False, code="E_RUN_PATH")
        try:
            self.descriptor = os.open(
                ".lock",
                os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=parent,
            )
            if not stat.S_ISREG(os.fstat(self.descriptor).st_mode):
                raise ContractError("E_RUN_PATH", "run lock must be a regular file")
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise ContractError("E_RUN_LOCKED", f"run workspace is locked: {self.workspace}") from exc
        except ContractError:
            self._close()
            raise
        except OSError as exc:
            self._close()
            code = "E_RUN_LOCKED" if exc.errno in {errno.EACCES, errno.EAGAIN} else "E_RUN_PATH"
            raise ContractError(code, f"cannot lock run workspace: {self.workspace}") from exc
        finally:
            os.close(parent)
        return self

    def _close(self) -> None:
        if self.descriptor >= 0:
            try:
                fcntl.flock(self.descriptor, fcntl.LOCK_UN)
            finally:
                os.close(self.descriptor)
                self.descriptor = -1

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._close()


class RunWorkspace:
    def __init__(self, root: Path, target_root: Path) -> None:
        self.root = _absolute(Path(root))
        self.target_root = _absolute(Path(target_root))
        self._clock: Clock | None = None
        target = _open_directory(self.target_root, create=False, code="E_RUN_PATH")
        os.close(target)
        if Path(root).is_symlink() or os.path.commonpath((self.root, self.target_root)) == os.fspath(self.target_root):
            raise ContractError("E_RUN_PATH", "run workspace must be a non-symlink outside target repository")
        if self.root.exists() and not self.root.is_dir():
            raise ContractError("E_RUN_PATH", "run workspace must be a directory")
        if self.root.exists():
            workspace = _open_directory(self.root, create=False, code="E_RUN_PATH")
            os.close(workspace)

    def lock(self) -> _RunLock:
        if not self.root.exists():
            raise ContractError("E_RUN_PATH", f"run workspace does not exist: {self.root}")
        return _RunLock(self.root)

    def initialize(
        self,
        *,
        repository: str,
        base_sha: str,
        configuration: Mapping[str, Any],
        clock: Clock,
    ) -> dict[str, Any]:
        root = _open_directory(self.root, create=True, code="E_RUN_PATH")
        os.close(root)
        self._clock = clock
        with self.lock():
            root = _open_directory(self.root, create=False, code="E_RUN_PATH")
            try:
                try:
                    os.stat("run-manifest.json", dir_fd=root, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ContractError("E_RUN_EXISTS", f"run workspace already initialized: {self.root}")
            finally:
                os.close(root)

            for relative in ("inputs", "stages", "diagnostics", "output"):
                descriptor = _open_directory(self.root / relative, create=True, code="E_RUN_PATH")
                os.close(descriptor)
            for number, name in enumerate(STAGE_NAMES, 1):
                descriptor = _open_directory(
                    self.root / "stages" / f"{number:02d}-{name}",
                    create=True,
                    code="E_RUN_PATH",
                )
                os.close(descriptor)

            timestamp = clock()
            normalized_repository = canonical_repository(repository)
            normalized_configuration = normalize_configuration(configuration)
            manifest = {
                "schema_version": RUN_SCHEMA_VERSION,
                "run_id": compute_run_id(
                    repository=normalized_repository,
                    base_sha=base_sha,
                    configuration=normalized_configuration,
                ),
                "created_at": timestamp,
                "updated_at": timestamp,
                "status": RunState.CREATED.value,
                "target": {
                    "root": os.fspath(self.target_root),
                    "repository": normalized_repository,
                    "base_sha": base_sha.lower(),
                },
                "configuration": normalized_configuration,
                "current_stage": STAGE_NAMES[0],
                "stages": initial_stages(),
            }
            validate_run_manifest(manifest)
            write_canonical_json_atomic(self.root / "run-manifest.json", manifest)
            return manifest

    def read_manifest(self) -> dict[str, Any]:
        raw, manifest = read_json_object_bytes(self.root / "run-manifest.json")
        validate_run_manifest(manifest)
        if manifest["target"]["root"] != os.fspath(self.target_root):
            raise ContractError("E_RUN_TARGET", "run manifest target does not match workspace target")
        if raw != canonical_json_bytes(manifest):
            raise ContractError("E_RUN_MANIFEST_CANONICAL", "run manifest must use canonical JSON bytes")
        return manifest

    def write_manifest(self, manifest: Mapping[str, Any]) -> None:
        payload = dict(manifest)
        validate_run_manifest(payload)
        if payload["target"]["root"] != os.fspath(self.target_root):
            raise ContractError("E_RUN_TARGET", "run manifest target does not match workspace target")
        with self.lock():
            write_canonical_json_atomic(self.root / "run-manifest.json", payload)

    def append_attempt(
        self,
        stage_number: int,
        stage_name: str,
        files: Mapping[str, bytes],
        *,
        attempt: int | None = None,
    ) -> Path:
        if type(stage_number) is not int or not 1 <= stage_number <= len(STAGE_NAMES):
            raise ContractError("E_RUN_STAGE", "stage number is invalid")
        if STAGE_NAMES[stage_number - 1] != stage_name:
            raise ContractError("E_RUN_STAGE", "stage number and name do not match")
        if not isinstance(files, Mapping) or not files:
            raise ContractError("E_RUN_ATTEMPT", "attempt must contain at least one file")
        for name, data in files.items():
            if not isinstance(name, str) or name in {"", ".", ".."} or Path(name).name != name:
                raise ContractError("E_RUN_PATH", "attempt file names must be single safe path components")
            if not isinstance(data, bytes):
                raise ContractError("E_RUN_ATTEMPT", "attempt file values must be bytes")

        stage_root = self.root / "stages" / f"{stage_number:02d}-{stage_name}"
        with self.lock():
            manifest = self.read_manifest()
            current_attempt = manifest["stages"][stage_number - 1]["attempt"]
            next_attempt = current_attempt + 1 if attempt is None else attempt
            attempts = _open_directory(stage_root / "attempts", create=True, code="E_RUN_PATH")
            temporary = f".{next_attempt}.{secrets.token_hex(8)}.tmp"
            written_names: list[str] = []
            temporary_created = False
            try:
                try:
                    os.stat(str(next_attempt), dir_fd=attempts, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise ContractError("E_RUN_ATTEMPT_EXISTS", f"attempt {next_attempt} already exists")
                if type(next_attempt) is not int or next_attempt < 1 or next_attempt != current_attempt + 1:
                    raise ContractError("E_RUN_ATTEMPT_SEQUENCE", "attempts must append in one-based order")
                os.mkdir(temporary, mode=0o700, dir_fd=attempts)
                temporary_created = True
                temporary_descriptor = os.open(
                    temporary,
                    os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                    dir_fd=attempts,
                )
                try:
                    for name in sorted(files):
                        descriptor = os.open(
                            name,
                            os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                            0o600,
                            dir_fd=temporary_descriptor,
                        )
                        try:
                            view = memoryview(files[name])
                            written = 0
                            while written < len(view):
                                written += os.write(descriptor, view[written:])
                            os.fsync(descriptor)
                        finally:
                            os.close(descriptor)
                        written_names.append(name)
                    os.fsync(temporary_descriptor)
                finally:
                    os.close(temporary_descriptor)
                os.rename(temporary, str(next_attempt), src_dir_fd=attempts, dst_dir_fd=attempts)
                temporary_created = False
                os.fsync(attempts)
            except OSError as exc:
                raise ContractError("E_RUN_PATH", f"cannot append stage attempt: {stage_root}") from exc
            finally:
                if temporary_created:
                    temporary_descriptor = os.open(temporary, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0), dir_fd=attempts)
                    try:
                        for name in written_names:
                            try:
                                os.unlink(name, dir_fd=temporary_descriptor)
                            except FileNotFoundError:
                                pass
                    finally:
                        os.close(temporary_descriptor)
                    os.rmdir(temporary, dir_fd=attempts)
                os.close(attempts)

            write_canonical_json_atomic(stage_root / "current.json", {"attempt": next_attempt})
            timestamp = self._clock() if self._clock is not None else manifest["updated_at"]
            output_projection = [
                {"path": name, "sha256": hashlib.sha256(files[name]).hexdigest()}
                for name in sorted(files)
            ]
            stage = manifest["stages"][stage_number - 1]
            stage.update(
                {
                    "status": StageState.PASS.value,
                    "output_sha256": canonical_sha256(output_projection),
                    "attempt": next_attempt,
                    "started_at": timestamp,
                    "completed_at": timestamp,
                }
            )
            manifest["status"] = RunState.RUNNING.value
            manifest["current_stage"] = stage_name
            manifest["updated_at"] = timestamp
            validate_run_manifest(manifest)
            write_canonical_json_atomic(self.root / "run-manifest.json", manifest)
            return stage_root / "attempts" / str(next_attempt)
