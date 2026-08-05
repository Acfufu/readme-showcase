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
from ..contracts.common import normalize_posix_path
from ..contracts.run import (
    RUN_SCHEMA_VERSION,
    STAGE_NAMES,
    canonical_repository,
    compute_run_id,
    current_revision_attempt,
    normalize_configuration,
    validate_run_manifest,
)
from .state import RunState, StageState, initial_stages


Clock = Callable[[], str]


# Attempt output fingerprints are part of the resumable-run trust boundary.
# Keep traversal bounded before allocating per-directory snapshots or reading
# file bytes.  These limits intentionally match the compiled-artifact reader's
# structural limits while using a run-specific error code.
_MAX_TREE_DEPTH = 16
_MAX_TREE_ENTRIES = 10_000
_MAX_TREE_BYTES = 16 * 1024 * 1024
# Descriptive aliases keep the ownership boundary obvious to callers while
# the short names match the compiled-artifact reader's focused test surface.
_MAX_ATTEMPT_TREE_DEPTH = _MAX_TREE_DEPTH
_MAX_ATTEMPT_TREE_ENTRIES = _MAX_TREE_ENTRIES
_MAX_ATTEMPT_TREE_BYTES = _MAX_TREE_BYTES


def _normalize_attempt_files(files: Mapping[str, bytes]) -> dict[str, bytes]:
    normalized: dict[str, bytes] = {}
    for name, data in files.items():
        try:
            path = normalize_posix_path(name)
        except ContractError as exc:
            raise ContractError("E_RUN_PATH", "attempt file names must be safe relative POSIX paths") from exc
        if not isinstance(data, bytes):
            raise ContractError("E_RUN_ATTEMPT", "attempt file values must be bytes")
        if path in normalized:
            raise ContractError("E_RUN_PATH", "attempt file names must be unique after normalization")
        normalized[path] = data
    return normalized


def _open_attempt_directory(parent: int, name: str) -> int:
    descriptor = -1
    try:
        expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        try:
            os.mkdir(name, mode=0o700, dir_fd=parent)
        except FileExistsError:
            pass
        expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
    if not stat.S_ISDIR(expected.st_mode):
        raise ContractError("E_RUN_PATH", "attempt directory ancestry must contain real directories")
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ContractError("E_RUN_PATH", "attempt directory ancestry changed during traversal")
        if not stat.S_ISDIR(opened.st_mode):
            raise ContractError("E_RUN_PATH", "attempt directory ancestry must contain real directories")
        os.fchmod(descriptor, 0o700)
        return descriptor
    except ContractError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ContractError("E_RUN_PATH", "attempt directory ancestry contains an unavailable or linked directory") from exc


def _remove_tree_entry(parent: int, name: str) -> None:
    descriptor = -1
    try:
        expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(expected.st_mode):
        os.unlink(name, dir_fd=parent)
        os.fsync(parent)
        return
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            raise ContractError("E_RUN_PATH", "attempt rollback ancestry changed during traversal")
        for child in os.listdir(descriptor):
            _remove_tree_entry(descriptor, child)
        os.fsync(descriptor)
    except ContractError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise ContractError("E_RUN_PATH", "cannot recursively roll back stage attempt") from exc
    else:
        os.close(descriptor)
        os.rmdir(name, dir_fd=parent)
        os.fsync(parent)


def _assert_attempt_consistency(root: Path, manifest: Mapping[str, Any]) -> None:
    for number, stage in enumerate(manifest["stages"], 1):
        stage_root = root / "stages" / f"{number:02d}-{stage['name']}"
        attempts_path = stage_root / "attempts"
        try:
            os.lstat(attempts_path)
        except FileNotFoundError:
            observed: list[int] = []
        else:
            attempts = _open_directory(attempts_path, create=False, code="E_RUN_PATH")
            try:
                names = os.listdir(attempts)
                if any(not name.isdigit() or int(name) < 1 for name in names):
                    raise ContractError("E_RUN_MANIFEST_STALE", "attempt directory contains an invalid entry")
                observed = sorted(int(name) for name in names)
                for name in names:
                    item = os.stat(name, dir_fd=attempts, follow_symlinks=False)
                    if not stat.S_ISDIR(item.st_mode):
                        raise ContractError("E_RUN_MANIFEST_STALE", "attempt entry must be a directory")
            finally:
                os.close(attempts)
        expected = list(range(1, stage["attempt"] + 1))
        if observed != expected:
            raise ContractError("E_RUN_MANIFEST_STALE", "manifest attempt counter does not match immutable attempts")

        current_path = stage_root / "current.json"
        if stage["attempt"] == 0:
            if current_path.exists() or current_path.is_symlink():
                raise ContractError("E_RUN_MANIFEST_STALE", "current pointer exists without a committed attempt")
            continue
        raw, current = read_json_object_bytes(current_path)
        if current != {"attempt": stage["attempt"]} or raw != canonical_json_bytes(current):
            raise ContractError("E_RUN_MANIFEST_STALE", "current pointer does not match manifest attempt")


def _rollback_attempt(stage_root: Path, attempt: int, names: list[str], previous: int) -> None:
    stage = _open_directory(stage_root, create=False, code="E_RUN_PATH")
    try:
        if previous == 0:
            try:
                current = os.stat("current.json", dir_fd=stage, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                if not stat.S_ISREG(current.st_mode):
                    raise ContractError("E_RUN_PATH", "current pointer must be a regular file")
                os.unlink("current.json", dir_fd=stage)
                os.fsync(stage)
        else:
            write_canonical_json_atomic(stage_root / "current.json", {"attempt": previous})
    finally:
        os.close(stage)

    attempts = _open_directory(stage_root / "attempts", create=False, code="E_RUN_PATH")
    try:
        _remove_tree_entry(attempts, str(attempt))
        os.fsync(attempts)
    finally:
        os.close(attempts)


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
                "current_revision": None,
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

    def attempt_output_sha256(self, stage_index: int, attempt: int) -> str | None:
        if attempt == 0:
            return None
        root = self.root / "stages" / f"{stage_index + 1:02d}-{STAGE_NAMES[stage_index]}" / "attempts" / str(attempt)
        try:
            root_info = root.lstat()
            if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
                return None
        except OSError:
            return None
        try:
            descriptor = _open_directory(root, create=False, code="E_RUN_PATH")
        except (ContractError, OSError):
            return None

        projection: dict[str, str] = {}
        remaining_entries = [_MAX_TREE_ENTRIES]
        total_bytes = [0]

        def resource_error(message: str) -> ContractError:
            return ContractError("E_RUN_RESOURCE", message)

        def scan_names(parent: int, *, count_budget: bool) -> list[str] | None:
            """Read one bounded directory snapshot without unbounded list growth."""

            names: list[str] = []
            try:
                with os.scandir(parent) as entries:
                    for entry in entries:
                        if count_budget:
                            remaining_entries[0] -= 1
                            if remaining_entries[0] < 0:
                                raise resource_error("run attempt tree exceeds its entry bound")
                        elif len(names) >= _MAX_TREE_ENTRIES:
                            raise resource_error("run attempt tree exceeds its entry bound")
                        name = entry.name
                        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                            return None
                        names.append(name)
            except ContractError:
                raise
            except OSError:
                return None
            return names

        def read_file(parent: int, name: str, expected: os.stat_result) -> str | None:
            if expected.st_size < 0 or total_bytes[0] > _MAX_TREE_BYTES - expected.st_size:
                raise resource_error("run attempt output exceeds its aggregate byte bound")
            file_descriptor = -1
            try:
                file_descriptor = os.open(
                    name,
                    os.O_RDONLY
                    | getattr(os, "O_NOFOLLOW", 0)
                    | getattr(os, "O_NONBLOCK", 0),
                    dir_fd=parent,
                )
                opened = os.fstat(file_descriptor)
                expected_identity = (expected.st_dev, expected.st_ino, expected.st_size, expected.st_mtime_ns)
                opened_identity = (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
                if not stat.S_ISREG(opened.st_mode) or opened_identity != expected_identity:
                    return None
                if opened.st_size < 0 or total_bytes[0] > _MAX_TREE_BYTES - opened.st_size:
                    raise resource_error("run attempt output exceeds its aggregate byte bound")
                digest = hashlib.sha256()
                read_bytes = 0
                while True:
                    chunk = os.read(file_descriptor, 64 * 1024)
                    if not chunk:
                        break
                    read_bytes += len(chunk)
                    if total_bytes[0] > _MAX_TREE_BYTES - read_bytes:
                        raise resource_error("run attempt output exceeds its aggregate byte bound")
                    digest.update(chunk)
                after = os.fstat(file_descriptor)
                if (
                    not stat.S_ISREG(after.st_mode)
                    or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != opened_identity
                ):
                    return None
                if read_bytes != opened.st_size:
                    return None
                total_bytes[0] += read_bytes
                return digest.hexdigest()
            except ContractError:
                raise
            except OSError:
                return None
            finally:
                if file_descriptor >= 0:
                    os.close(file_descriptor)

        def visit(parent: int, prefix: str, depth: int) -> bool | None:
            if depth > _MAX_TREE_DEPTH:
                raise resource_error("run attempt tree exceeds its depth bound")
            names = scan_names(parent, count_budget=True)
            if names is None:
                return None
            names.sort()
            if not names:
                return False
            found = False
            for name in names:
                if not isinstance(name, str) or not name or "/" in name or "\\" in name:
                    return None
                relative = f"{prefix}/{name}" if prefix else name
                try:
                    expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except OSError:
                    return None
                if stat.S_ISLNK(expected.st_mode):
                    return None
                if stat.S_ISDIR(expected.st_mode):
                    child = -1
                    try:
                        child = os.open(
                            name,
                            os.O_RDONLY
                            | getattr(os, "O_DIRECTORY", 0)
                            | getattr(os, "O_NOFOLLOW", 0),
                            dir_fd=parent,
                        )
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
                            return None
                        child_found = visit(child, relative, depth + 1)
                        if child_found is not True:
                            return None
                    except OSError:
                        return None
                    finally:
                        if child >= 0:
                            os.close(child)
                    found = True
                elif stat.S_ISREG(expected.st_mode):
                    digest = read_file(parent, name, expected)
                    if digest is None:
                        return None
                    projection[relative] = digest
                    found = True
                else:
                    return None
                try:
                    current = os.stat(name, dir_fd=parent, follow_symlinks=False)
                except OSError:
                    return None
                if (current.st_dev, current.st_ino, current.st_mode, current.st_size, current.st_mtime_ns) != (
                    expected.st_dev,
                    expected.st_ino,
                    expected.st_mode,
                    expected.st_size,
                    expected.st_mtime_ns,
                ):
                    return None
            current_names = scan_names(parent, count_budget=False)
            if current_names is None:
                return None
            if sorted(current_names) != names:
                return None
            return found

        try:
            if visit(descriptor, "", 0) is not True:
                return None
            try:
                after_root = root.lstat()
            except OSError:
                return None
            if (
                stat.S_ISLNK(after_root.st_mode)
                or (after_root.st_dev, after_root.st_ino, after_root.st_mode, after_root.st_mtime_ns)
                != (root_info.st_dev, root_info.st_ino, root_info.st_mode, root_info.st_mtime_ns)
            ):
                return None
        finally:
            os.close(descriptor)
        return canonical_sha256(
            [{"path": path, "sha256": projection[path]} for path in sorted(projection)]
        )

    def write_manifest(self, manifest: Mapping[str, Any]) -> None:
        payload = dict(manifest)
        validate_run_manifest(payload)
        if payload["target"]["root"] != os.fspath(self.target_root):
            raise ContractError("E_RUN_TARGET", "run manifest target does not match workspace target")
        with self.lock():
            current = self.read_manifest()
            _assert_attempt_consistency(self.root, current)
            if payload["created_at"] != current["created_at"] or any(
                candidate["attempt"] != existing["attempt"]
                for candidate, existing in zip(payload["stages"], current["stages"], strict=True)
            ):
                raise ContractError("E_RUN_MANIFEST_STALE", "manifest snapshot would rewrite immutable run history")
            current_revision = current_revision_attempt(current.get("current_revision"))
            candidate_revision = current_revision_attempt(payload.get("current_revision"))
            if candidate_revision != current_revision:
                expected = 1 if current_revision is None else current_revision + 1
                if candidate_revision != expected:
                    raise ContractError(
                        "E_REVISION_POINTER",
                        "run manifest current_revision must advance contiguously",
                    )
            if candidate_revision is not None:
                revision_root = self.root / "stages/04-generation-request/revisions"
                try:
                    raw, pointer = read_json_object_bytes(
                        revision_root / "revision-manifest.json"
                    )
                except (ContractError, OSError) as exc:
                    raise ContractError(
                        "E_REVISION_POINTER",
                        "run manifest current_revision lacks matching internal pointer",
                    ) from exc
                if raw != canonical_json_bytes(pointer) or pointer != {
                    "current": f"{candidate_revision}/revision-request.json"
                }:
                    raise ContractError(
                        "E_REVISION_POINTER",
                        "run manifest current_revision lacks matching internal pointer",
                    )
                request = revision_root / str(candidate_revision) / "revision-request.json"
                try:
                    info = request.lstat()
                except OSError as exc:
                    raise ContractError(
                        "E_REVISION_POINTER",
                        "run manifest current_revision lacks immutable request",
                    ) from exc
                if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
                    raise ContractError(
                        "E_REVISION_POINTER",
                        "run manifest current_revision lacks immutable request",
                    )
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
        normalized_files = _normalize_attempt_files(files)

        stage_root = self.root / "stages" / f"{stage_number:02d}-{stage_name}"
        with self.lock():
            manifest = self.read_manifest()
            _assert_attempt_consistency(self.root, manifest)
            current_attempt = manifest["stages"][stage_number - 1]["attempt"]
            next_attempt = current_attempt + 1 if attempt is None else attempt
            attempts = _open_directory(stage_root / "attempts", create=True, code="E_RUN_PATH")
            temporary = f".{next_attempt}.{secrets.token_hex(8)}.tmp"
            temporary_created = False
            directory_descriptors: dict[tuple[str, ...], int] = {}
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
                temporary_descriptor = _open_attempt_directory(attempts, temporary)
                directory_descriptors[()] = temporary_descriptor
                for name in sorted(normalized_files):
                    parts = tuple(name.split("/"))
                    parent_key: tuple[str, ...] = ()
                    parent_descriptor = temporary_descriptor
                    for part in parts[:-1]:
                        child_key = parent_key + (part,)
                        child_descriptor = directory_descriptors.get(child_key)
                        if child_descriptor is None:
                            child_descriptor = _open_attempt_directory(parent_descriptor, part)
                            directory_descriptors[child_key] = child_descriptor
                        parent_descriptor = child_descriptor
                        parent_key = child_key
                    descriptor = os.open(
                        parts[-1],
                        os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                        0o600,
                        dir_fd=parent_descriptor,
                    )
                    try:
                        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                            raise ContractError("E_RUN_PATH", "attempt artifacts must be regular files")
                        os.fchmod(descriptor, 0o600)
                        view = memoryview(normalized_files[name])
                        written = 0
                        while written < len(view):
                            count = os.write(descriptor, view[written:])
                            if count <= 0:
                                raise ContractError("E_RUN_PATH", "attempt artifact write made no progress")
                            written += count
                        os.fsync(descriptor)
                    finally:
                        os.close(descriptor)
                for _, descriptor in sorted(
                    directory_descriptors.items(), key=lambda item: len(item[0]), reverse=True
                ):
                    os.fsync(descriptor)
                for descriptor in directory_descriptors.values():
                    os.close(descriptor)
                directory_descriptors.clear()
                os.rename(temporary, str(next_attempt), src_dir_fd=attempts, dst_dir_fd=attempts)
                try:
                    os.fsync(attempts)
                except OSError:
                    temporary_created = False
                    _remove_tree_entry(attempts, str(next_attempt))
                    raise
                temporary_created = False
            except OSError as exc:
                raise ContractError("E_RUN_PATH", f"cannot append stage attempt: {stage_root}") from exc
            finally:
                for descriptor in directory_descriptors.values():
                    os.close(descriptor)
                directory_descriptors.clear()
                if temporary_created:
                    _remove_tree_entry(attempts, temporary)
                os.close(attempts)

            timestamp = self._clock() if self._clock is not None else manifest["updated_at"]
            output_projection = [
                {"path": name, "sha256": hashlib.sha256(normalized_files[name]).hexdigest()}
                for name in sorted(normalized_files)
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
            try:
                write_canonical_json_atomic(stage_root / "current.json", {"attempt": next_attempt})
                write_canonical_json_atomic(self.root / "run-manifest.json", manifest)
            except Exception:
                try:
                    committed = self.read_manifest()
                    _assert_attempt_consistency(self.root, committed)
                except (ContractError, OSError):
                    committed = None
                if committed == manifest:
                    return stage_root / "attempts" / str(next_attempt)
                _rollback_attempt(stage_root, next_attempt, [], current_attempt)
                raise
            return stage_root / "attempts" / str(next_attempt)
