from __future__ import annotations

import fcntl
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import time
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from ...pipeline_contracts import (
    ContractError,
    _absolute,
    _open_directory,
    canonical_json_bytes,
    read_json_object_bytes,
    read_regular_bytes,
    write_canonical_json_atomic,
)
from ..contracts.run import canonical_repository
from ..errors import AGGREGATABLE_CODES
from ..generation.request import (
    MAX_GENERATION_REQUEST_BYTES,
    MAX_REVISION_ATTEMPTS,
    build_revision_request,
    canonical_generation_request,
    canonical_revision_request,
    validate_revision_request,
)
from ..preview.renderer import render_preview
from .logging import StageLogger
from .stages import STAGES, CandidateImportStage, RunContext, candidate_files
from .workspace import RunWorkspace


_DEFAULT_STATE_DIRECTORY = ("state", "readme-showcase")
_DEFAULT_RUN_NAME = re.compile(r"run-[0-9a-f]{32}\Z")


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _git(root: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", os.fspath(root), *arguments],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ContractError("E_RUN_TARGET", "cannot inspect target Git repository") from exc
    if result.returncode != 0:
        raise ContractError("E_RUN_TARGET", "target must be a Git repository with an immutable HEAD")
    return result.stdout.strip()


def _repository(root: Path) -> str:
    result = subprocess.run(
        ["git", "-C", os.fspath(root), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    if result.returncode == 0:
        try:
            return canonical_repository(result.stdout.strip())
        except ContractError:
            pass
    return "local/repository"


def _target_root(path: Path) -> Path:
    return Path(_git(path.resolve(), "rev-parse", "--show-toplevel")).resolve()


def _workspace(path: Path) -> RunWorkspace:
    raw, manifest = read_json_object_bytes(path / "run-manifest.json")
    if raw != canonical_json_bytes(manifest):
        raise ContractError("E_RUN_MANIFEST_CANONICAL", "run manifest must use canonical JSON bytes")
    target = manifest.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("root"), str):
        raise ContractError("E_RUN_TARGET", "run manifest target is invalid")
    return RunWorkspace(path, Path(target["root"]))


def _default_runs_root(target: Path, *, create: bool) -> Path:
    raw_home = os.environ.get("CODEX_HOME")
    codex_home = Path(raw_home).expanduser() if raw_home else Path.home() / ".codex"
    if not codex_home.is_absolute():
        raise ContractError("E_RUN_STATE_ROOT", "CODEX_HOME must be an absolute path")
    target = _absolute(target)
    state_root = _absolute(codex_home.joinpath(*_DEFAULT_STATE_DIRECTORY))
    if os.path.commonpath((state_root, target)) == os.fspath(target):
        raise ContractError("E_RUN_PATH", "default run state must stay outside target repository")
    repository_key = hashlib.sha256(os.fsencode(target)).hexdigest()
    runs = state_root / repository_key / "runs"
    try:
        descriptor = _open_directory(runs, create=create, code="E_RUN_STATE_ROOT")
    except ContractError as exc:
        if not create:
            raise ContractError("E_RUN_NOT_FOUND", "no centralized run exists for target repository") from exc
        raise
    try:
        os.fchmod(descriptor, 0o700)
    finally:
        os.close(descriptor)
    return runs


def create_default_workspace(target: Path) -> Path:
    runs = _default_runs_root(target, create=True)
    descriptor = _open_directory(runs, create=False, code="E_RUN_STATE_ROOT")
    try:
        for _ in range(16):
            name = f"run-{secrets.token_hex(16)}"
            try:
                os.mkdir(name, mode=0o700, dir_fd=descriptor)
            except FileExistsError:
                continue
            os.fsync(descriptor)
            return runs / name
    finally:
        os.close(descriptor)
    raise ContractError("E_RUN_STATE_ROOT", "cannot allocate a unique centralized run directory")


def latest_default_workspace(target: Path) -> Path:
    target = _absolute(target)
    runs = _default_runs_root(target, create=False)
    descriptor = _open_directory(runs, create=False, code="E_RUN_STATE_ROOT")
    try:
        names = sorted(os.listdir(descriptor))
        for name in names:
            if not _DEFAULT_RUN_NAME.fullmatch(name):
                continue
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                raise ContractError("E_RUN_STATE_ROOT", "centralized run entry must be a real directory")
    finally:
        os.close(descriptor)

    candidates: list[tuple[str, str, Path]] = []
    for name in names:
        if not _DEFAULT_RUN_NAME.fullmatch(name):
            continue
        path = runs / name
        workspace = _workspace(path)
        manifest = workspace.read_manifest()
        if manifest["target"]["root"] != os.fspath(target):
            raise ContractError("E_RUN_TARGET", "centralized run target does not match repository")
        candidates.append((manifest["updated_at"], name, path))
    if not candidates:
        raise ContractError("E_RUN_NOT_FOUND", "no centralized run exists for target repository")
    return max(candidates)[2]


def _resolved_workspace(workspace_path: Path | None, target: Path | None) -> RunWorkspace:
    if workspace_path is not None:
        return _workspace(workspace_path)
    if target is None:
        raise ContractError("E_RUN_TARGET", "target repository is required when workspace is omitted")
    return _workspace(latest_default_workspace(_target_root(target)))


def _debug_summary(
    summary: dict[str, object],
    workspace: RunWorkspace,
    logger: StageLogger,
) -> dict[str, object]:
    if logger.verbosity == "debug":
        summary["workspace"] = os.fspath(workspace.root)
    return summary


@contextmanager
def _runner_lock(workspace: RunWorkspace) -> Iterator[None]:
    # M1-T1 mutation methods take .lock themselves; this outer lock serializes full runs.
    try:
        with workspace.lock():
            pass
    except ContractError as exc:
        if exc.code == "E_RUN_LOCKED":
            raise ContractError("E_RUN_LOCKED", "run workspace is locked") from exc
        raise
    path = workspace.root / ".runner.lock"
    descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ContractError("E_RUN_PATH", "runner lock must be a regular file")
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ContractError("E_RUN_LOCKED", "run workspace is locked") from exc
        yield
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)


def _copy_plan(workspace: RunWorkspace, plan: Path | None) -> None:
    if plan is None:
        return
    try:
        raw, value = read_json_object_bytes(plan, maximum=MAX_GENERATION_REQUEST_BYTES)
    except ContractError as exc:
        if exc.code == "E_INPUT_SIZE":
            raise ContractError("E_GENERATION_REQUEST_SIZE", f"README plan exceeds {MAX_GENERATION_REQUEST_BYTES} bytes") from exc
        raise
    if raw != canonical_json_bytes(value):
        raise ContractError("E_RUN_INPUT", "README plan must use canonical JSON bytes")
    write_canonical_json_atomic(workspace.root / "inputs/readme-plan.json", value)


def _write_state(workspace: RunWorkspace, manifest: dict[str, Any]) -> dict[str, Any]:
    manifest["updated_at"] = utc_now()
    workspace.write_manifest(manifest)
    return workspace.read_manifest()


def _summary(manifest: dict[str, Any]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "run_id": manifest["run_id"],
        "status": manifest["status"],
        "current_stage": manifest["current_stage"],
    }


def _stale_from(manifest: dict[str, Any], index: int) -> None:
    for stage in manifest["stages"][index:]:
        stage["status"] = "stale"


_REVISION_POINTER = "revision-manifest.json"
_REVISION_CURRENT = re.compile(r"([1-9][0-9]*)/revision-request\.json\Z")
_REVISION_TEMPORARY = re.compile(r"\.[1-9][0-9]*\.[0-9a-f]{16}\.tmp\Z")


def _cleanup_revision_temporaries(root: Path) -> None:
    for path in root.iterdir():
        if not _REVISION_TEMPORARY.fullmatch(path.name):
            continue
        info = path.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ContractError("E_RUN_PATH", "revision temporary must be a real directory")
        entries = list(path.iterdir())
        if any(
            entry.name != "revision-request.json"
            or entry.is_symlink()
            or not entry.is_file()
            for entry in entries
        ):
            raise ContractError("E_RUN_PATH", "revision temporary contains unexpected data")
        for entry in entries:
            entry.unlink()
        path.rmdir()


def _revision_root(workspace: RunWorkspace) -> Path:
    stage_root = workspace.root / "stages/04-generation-request"
    try:
        info = stage_root.lstat()
    except OSError as exc:
        raise ContractError("E_RUN_PATH", "generation request stage is unavailable") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_RUN_PATH", "generation request stage must be a real directory")
    revisions = stage_root / "revisions"
    try:
        info = revisions.lstat()
    except FileNotFoundError:
        try:
            revisions.mkdir(mode=0o700)
        except FileExistsError:
            pass
        except OSError as exc:
            raise ContractError("E_RUN_PATH", "cannot create revision root") from exc
        info = revisions.lstat()
    except OSError as exc:
        raise ContractError("E_RUN_PATH", "cannot inspect revision root") from exc
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_RUN_PATH", "revision root must be a real directory")
    _cleanup_revision_temporaries(revisions)
    return revisions


def _revision_history(root: Path) -> list[dict[str, Any]]:
    pointer = root / _REVISION_POINTER
    try:
        pointer_info = pointer.lstat()
    except FileNotFoundError:
        return []
    except OSError as exc:
        raise ContractError("E_RUN_PATH", "cannot inspect revision pointer") from exc
    if not stat.S_ISREG(pointer_info.st_mode) or stat.S_ISLNK(pointer_info.st_mode):
        raise ContractError("E_RUN_PATH", "revision pointer must be a regular file")
    raw, value = read_json_object_bytes(pointer)
    if raw != canonical_json_bytes(value) or set(value) != {"current"} or not isinstance(value["current"], str):
        raise ContractError("E_REVISION_POINTER", "revision pointer must contain canonical relative current path")
    match = _REVISION_CURRENT.fullmatch(value["current"])
    if match is None:
        raise ContractError("E_REVISION_POINTER", "revision pointer must contain canonical relative current path")
    current = int(match.group(1))
    history: list[dict[str, Any]] = []
    for attempt in range(1, current + 1):
        directory = root / str(attempt)
        try:
            info = directory.lstat()
        except OSError as exc:
            raise ContractError("E_REVISION_POINTER", "revision history is not contiguous") from exc
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
            raise ContractError("E_RUN_PATH", "revision attempt must be a real directory")
        request_path = directory / "revision-request.json"
        request_raw, request = read_json_object_bytes(request_path)
        normalized = validate_revision_request(request)
        if request_raw != canonical_revision_request(normalized) or normalized["attempt"] != attempt:
            raise ContractError("E_REVISION_MUTATED", "revision history bytes or attempt identity changed")
        if attempt == 1 and normalized["before_candidate_sha256"] != normalized["after_candidate_sha256"]:
            raise ContractError("E_REVISION_MUTATED", "initial revision candidate hash changed")
        if history:
            previous = history[-1]
            if (
                normalized["original_request_sha256"]
                != previous["original_request_sha256"]
                or normalized["before_candidate_sha256"] != previous["after_candidate_sha256"]
            ):
                raise ContractError("E_REVISION_MUTATED", "revision history hash chain changed")
        history.append(normalized)
    return history


def _append_revision(
    root: Path,
    request: dict[str, Any],
    previous_pointer: bytes | None,
) -> None:
    attempt = request["attempt"]
    destination = root / str(attempt)
    try:
        destination.lstat()
    except FileNotFoundError:
        pass
    except OSError as exc:
        raise ContractError("E_RUN_PATH", "cannot inspect revision attempt") from exc
    else:
        raise ContractError("E_REVISION_EXISTS", f"revision attempt {attempt} already exists")

    temporary = root / f".{attempt}.{secrets.token_hex(8)}.tmp"
    committed = False
    reserved = False
    try:
        temporary.mkdir(mode=0o700)
        temporary_descriptor = os.open(
            temporary, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            descriptor = os.open(
                "revision-request.json",
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
                0o600,
                dir_fd=temporary_descriptor,
            )
            try:
                data = canonical_revision_request(request)
                view = memoryview(data)
                written = 0
                while written < len(view):
                    written += os.write(descriptor, view[written:])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.fsync(temporary_descriptor)
        finally:
            os.close(temporary_descriptor)
        try:
            destination.mkdir(mode=0o700)
        except FileExistsError as exc:
            raise ContractError("E_REVISION_EXISTS", f"revision attempt {attempt} already exists") from exc
        reserved = True
        os.rename(
            temporary / "revision-request.json",
            destination / "revision-request.json",
        )
        temporary.rmdir()
        root_descriptor = os.open(
            root, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
        )
        try:
            os.fsync(root_descriptor)
        finally:
            os.close(root_descriptor)
        committed = True
        write_canonical_json_atomic(
            root / _REVISION_POINTER,
            {"current": f"{attempt}/revision-request.json"},
        )
    except Exception:
        if committed:
            try:
                (destination / "revision-request.json").unlink()
                destination.rmdir()
            except FileNotFoundError:
                pass
            if previous_pointer is not None:
                previous_value = read_json_object_bytes_from_bytes(previous_pointer)
                write_canonical_json_atomic(root / _REVISION_POINTER, previous_value)
            else:
                try:
                    (root / _REVISION_POINTER).unlink()
                except FileNotFoundError:
                    pass
        else:
            try:
                (temporary / "revision-request.json").unlink()
            except FileNotFoundError:
                pass
            try:
                temporary.rmdir()
            except FileNotFoundError:
                pass
            if reserved:
                try:
                    (destination / "revision-request.json").unlink()
                except FileNotFoundError:
                    pass
                try:
                    destination.rmdir()
                except FileNotFoundError:
                    pass
        raise


def read_json_object_bytes_from_bytes(raw: bytes) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractError("E_REVISION_POINTER", "revision pointer backup is invalid") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ContractError("E_REVISION_POINTER", "revision pointer backup is invalid")
    return value


def _restore_canonical_file(path: Path, raw: bytes, *, code: str) -> None:
    value = read_json_object_bytes_from_bytes(raw)
    write_canonical_json_atomic(path, value)
    try:
        restored = read_regular_bytes(path, maximum=max(len(raw), 4096))
    except ContractError as exc:
        raise ContractError(code, f"cannot verify restored file: {path.name}") from exc
    if restored != raw:
        raise ContractError(code, f"restored file bytes differ: {path.name}")


def _remove_revision_attempt(root: Path, attempt: int) -> None:
    directory = root / str(attempt)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_REVISION_RECOVERY", "new revision attempt is not a real directory")
    request = directory / "revision-request.json"
    request_info = request.lstat()
    if not stat.S_ISREG(request_info.st_mode) or stat.S_ISLNK(request_info.st_mode):
        raise ContractError("E_REVISION_RECOVERY", "new revision request is not a regular file")
    request.unlink()
    directory.rmdir()


def _rollback_revision_commit(
    workspace: RunWorkspace,
    root: Path,
    attempt: int,
    prior_manifest: bytes,
    prior_pointer: bytes | None,
) -> None:
    manifest_path = workspace.root / "run-manifest.json"
    try:
        current_manifest = read_regular_bytes(
            manifest_path, maximum=MAX_GENERATION_REQUEST_BYTES
        )
        if current_manifest != prior_manifest:
            _restore_canonical_file(
                manifest_path, prior_manifest, code="E_REVISION_RECOVERY"
            )
        pointer_path = root / _REVISION_POINTER
        if prior_pointer is None:
            try:
                pointer_path.unlink()
            except FileNotFoundError:
                pass
        else:
            _restore_canonical_file(
                pointer_path, prior_pointer, code="E_REVISION_RECOVERY"
            )
        _remove_revision_attempt(root, attempt)
    except Exception as exc:
        if isinstance(exc, ContractError) and exc.code == "E_REVISION_RECOVERY":
            raise
        raise ContractError(
            "E_REVISION_RECOVERY",
            f"revision {attempt} recovery failed; preserved workspace state requires manual repair",
        ) from exc


def _commit_revision(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    root: Path,
    request: dict[str, Any],
    prior_pointer: bytes | None,
) -> None:
    manifest_path = workspace.root / "run-manifest.json"
    prior_manifest = read_regular_bytes(
        manifest_path, maximum=MAX_GENERATION_REQUEST_BYTES
    )
    if prior_manifest != canonical_json_bytes(manifest):
        raise ContractError("E_RUN_MANIFEST_STALE", "revision manifest snapshot is stale")
    _append_revision(root, request, prior_pointer)
    current_revision = (
        "stages/04-generation-request/revisions/"
        f"{request['attempt']}/revision-request.json"
    )
    updated = deepcopy(manifest)
    updated["current_revision"] = current_revision
    try:
        workspace.write_manifest(updated)
    except Exception as exc:
        try:
            _rollback_revision_commit(
                workspace,
                root,
                request["attempt"],
                prior_manifest,
                prior_pointer,
            )
        except ContractError as recovery:
            raise ContractError(
                "E_REVISION_RECOVERY",
                f"revision {request['attempt']} commit and recovery failed",
            ) from recovery
        raise ContractError(
            "E_REVISION_COMMIT",
            f"revision {request['attempt']} run-manifest commit failed and was rolled back",
        ) from exc
    manifest.clear()
    manifest.update(updated)


def _assert_authoritative_revision_pointer(
    manifest: dict[str, Any], history: list[dict[str, Any]]
) -> None:
    current = manifest.get("current_revision")
    expected = (
        None
        if not history
        else "stages/04-generation-request/revisions/"
        f"{history[-1]['attempt']}/revision-request.json"
    )
    legacy_absent = "current_revision" not in manifest
    if current != expected and not (legacy_absent and current is None):
        raise ContractError(
            "E_REVISION_POINTER",
            "run manifest current_revision does not match immutable revision history",
        )


def _record_revision_if_content_failure(workspace: RunWorkspace, manifest: dict[str, Any]) -> bool:
    validation_attempt = manifest["stages"][6]["attempt"]
    report_path = (
        workspace.root / "stages/07-validation/attempts" / str(validation_attempt)
        / "validation-report.json"
    )
    report_raw, report = read_json_object_bytes(report_path)
    if report_raw != canonical_json_bytes(report) or report.get("status") != "fail":
        return False
    diagnostics = report.get("diagnostics")
    if (
        not isinstance(diagnostics, list)
        or not diagnostics
        or any(not isinstance(item, dict) or item.get("code") not in AGGREGATABLE_CODES for item in diagnostics)
    ):
        return False

    generation_path = (
        workspace.root / "stages/04-generation-request/attempts"
        / str(manifest["stages"][3]["attempt"]) / "generation-request.json"
    )
    generation_raw, generation_request = read_json_object_bytes(generation_path)
    if generation_raw != canonical_generation_request(generation_request):
        raise ContractError("E_REVISION_MUTATED", "generation request bytes changed")
    original_sha256 = hashlib.sha256(generation_raw).hexdigest()
    candidate_sha256 = manifest["stages"][4]["output_sha256"]
    if not isinstance(candidate_sha256, str):
        raise ContractError("E_REVISION_CANDIDATE", "revision requires a candidate hash")

    root = _revision_root(workspace)
    pointer_path = root / _REVISION_POINTER
    try:
        previous_pointer = read_regular_bytes(pointer_path, maximum=4096)
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            previous_pointer = None
        else:
            raise
    history = _revision_history(root)
    _assert_authoritative_revision_pointer(manifest, history)
    if history and history[0]["original_request_sha256"] != original_sha256:
        return False
    if history and history[-1]["after_candidate_sha256"] == candidate_sha256:
        return False
    if len(history) >= MAX_REVISION_ATTEMPTS:
        return False

    files = candidate_files(RunContext(workspace, manifest))
    if files is None:
        raise ContractError("E_REVISION_CANDIDATE", "revision candidate files are unavailable")
    allowed_files = sorted(name for name, _ in files)
    request_forbidden = generation_request["output_contract"]["forbidden_paths"]
    forbidden_paths = sorted(set(request_forbidden) | {
        ".omo/**",
        "inputs/readme-plan.json",
        f"stages/01-scan/attempts/{manifest['stages'][0]['attempt']}/repository-evidence.json",
        f"stages/03-plan-import/attempts/{manifest['stages'][2]['attempt']}/readme-plan.json",
    })
    previous_candidate = history[-1]["after_candidate_sha256"] if history else candidate_sha256
    request = build_revision_request(
        attempt=len(history) + 1,
        original_request_sha256=original_sha256,
        before_candidate_sha256=previous_candidate,
        after_candidate_sha256=candidate_sha256,
        diagnostic_report=report,
        allowed_files=allowed_files,
        forbidden_paths=forbidden_paths,
    )
    _commit_revision(workspace, manifest, root, request, previous_pointer)
    return True


def _drive(workspace: RunWorkspace, logger: StageLogger, stop_after: str | None) -> dict[str, object]:
    manifest = workspace.read_manifest()
    context = RunContext(workspace, manifest)
    for index, adapter in enumerate(STAGES):
        context.manifest = manifest
        input_sha256 = adapter.fingerprint(context)
        stage = manifest["stages"][index]
        stored_output = (
            input_sha256
            if isinstance(adapter, CandidateImportStage) and stage["attempt"] == 0
            else workspace.attempt_output_sha256(index, stage["attempt"])
        )
        if stage["status"] == "pass" and stage["input_sha256"] == input_sha256 and stored_output == stage["output_sha256"]:
            logger.emit("stage.skipped", run_id=manifest["run_id"], stage=adapter.name, status="pass", input_sha256=input_sha256, output_sha256=stage["output_sha256"])
            if stop_after == adapter.name:
                return _summary(manifest)
            continue

        if stage["input_sha256"] != input_sha256 or (stage["status"] == "pass" and stored_output != stage["output_sha256"]):
            _stale_from(manifest, index)
        stage = manifest["stages"][index]
        stage.update({"status": "running", "input_sha256": input_sha256, "started_at": utc_now(), "completed_at": None})
        manifest["status"] = "running"
        manifest["current_stage"] = adapter.name
        manifest = _write_state(workspace, manifest)
        context.manifest = manifest
        logger.emit("stage.started", run_id=manifest["run_id"], stage=adapter.name, status="running", input_sha256=input_sha256)
        started = time.monotonic()
        try:
            result = adapter.execute(context)
            if result.status.startswith("waiting-for-"):
                manifest = workspace.read_manifest()
                stage = manifest["stages"][index]
                stage.update({"status": result.status, "input_sha256": input_sha256, "output_sha256": None, "completed_at": utc_now()})
                manifest["status"] = result.status
                manifest["current_stage"] = adapter.name
                manifest = _write_state(workspace, manifest)
                logger.emit("stage.completed", run_id=manifest["run_id"], stage=adapter.name, status=result.status, duration_ms=int((time.monotonic() - started) * 1000), input_sha256=input_sha256)
                return _summary(manifest)

            if result.files:
                workspace.append_attempt(index + 1, adapter.name, result.files)
                manifest = workspace.read_manifest()
            stage = manifest["stages"][index]
            stage.update(
                {
                    "status": "pass" if result.status == "pass" else "failed",
                    "input_sha256": input_sha256,
                    "output_sha256": result.output_sha256 or stage["output_sha256"],
                    "started_at": stage["started_at"] or utc_now(),
                    "completed_at": utc_now(),
                }
            )
            if result.status != "pass":
                _stale_from(manifest, index + 1)
                manifest["status"] = "manual-review-required"
                manifest["current_stage"] = adapter.name
            elif index + 1 == len(STAGES):
                manifest["status"] = "complete"
                manifest["current_stage"] = None
            else:
                manifest["status"] = "running"
                manifest["current_stage"] = STAGES[index + 1].name
            manifest = _write_state(workspace, manifest)
            if index == 6 and result.status != "pass":
                _record_revision_if_content_failure(workspace, manifest)
            logger.emit("stage.completed", run_id=manifest["run_id"], stage=adapter.name, status=result.status, duration_ms=int((time.monotonic() - started) * 1000), input_sha256=input_sha256, output_sha256=stage["output_sha256"])
            if result.status != "pass" or stop_after == adapter.name:
                return _summary(manifest)
        except ContractError as exc:
            if exc.code in {"E_REVISION_COMMIT", "E_REVISION_RECOVERY"}:
                raise
            manifest = workspace.read_manifest()
            failed = manifest["stages"][index]
            failed["status"] = "failed"
            failed["completed_at"] = utc_now()
            manifest["status"] = "failed"
            manifest["current_stage"] = adapter.name
            _write_state(workspace, manifest)
            raise
        except Exception:
            manifest = workspace.read_manifest()
            failed = manifest["stages"][index]
            failed["status"] = "failed"
            failed["completed_at"] = utc_now()
            manifest["status"] = "failed"
            manifest["current_stage"] = adapter.name
            _write_state(workspace, manifest)
            raise
    return _summary(manifest)


def start_run(
    *,
    root: Path,
    workspace_path: Path | None,
    mode: str,
    project_type: str,
    locales: list[str],
    scanner_profile: str,
    plan: Path | None,
    stop_after: str | None,
    logger: StageLogger,
) -> dict[str, object]:
    target = _target_root(root)
    repository = _repository(target)
    base_sha = _git(target, "rev-parse", "HEAD")
    workspace_path = workspace_path or create_default_workspace(target)
    workspace = RunWorkspace(workspace_path, target)
    workspace.initialize(
        repository=repository,
        base_sha=base_sha,
        configuration={"mode": mode, "project_type": project_type, "locales": locales, "scanner_profile": scanner_profile},
        clock=utc_now,
    )
    with _runner_lock(workspace):
        _copy_plan(workspace, plan)
        return _debug_summary(_drive(workspace, logger, stop_after), workspace, logger)


def resume_run(
    *,
    workspace_path: Path | None,
    plan: Path | None,
    stop_after: str | None,
    logger: StageLogger,
    root: Path | None = None,
) -> dict[str, object]:
    workspace = _resolved_workspace(workspace_path, root)
    with _runner_lock(workspace):
        _copy_plan(workspace, plan)
        return _debug_summary(_drive(workspace, logger, stop_after), workspace, logger)


def run_status(
    workspace_path: Path | None,
    root: Path | None = None,
    *,
    debug: bool = False,
) -> dict[str, object]:
    workspace = _resolved_workspace(workspace_path, root)
    summary = _summary(workspace.read_manifest())
    if debug:
        summary["workspace"] = os.fspath(workspace.root)
    return summary


def explain_run(workspace_path: Path | None, root: Path | None = None) -> dict[str, Any]:
    workspace = _resolved_workspace(workspace_path, root)
    return deepcopy(workspace.read_manifest())


def preview_run(workspace_path: Path | None, root: Path | None = None) -> dict[str, object]:
    workspace = _resolved_workspace(workspace_path, root)
    with _runner_lock(workspace):
        manifest = workspace.read_manifest()
        if manifest.get("current_revision") is not None:
            root = workspace.root / "stages/04-generation-request/revisions"
            try:
                info = root.lstat()
            except OSError as exc:
                raise ContractError("E_REVISION_POINTER", "revision root is unavailable") from exc
            if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode):
                raise ContractError("E_REVISION_POINTER", "revision root must be a real directory")
            _assert_authoritative_revision_pointer(manifest, _revision_history(root))
        return render_preview(workspace, manifest)
