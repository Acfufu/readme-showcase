from __future__ import annotations

import fcntl
import hashlib
import os
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
    canonical_json_bytes,
    canonical_sha256,
    read_json_object_bytes,
    write_canonical_json_atomic,
)
from ..contracts.run import STAGE_NAMES, canonical_repository
from .logging import StageLogger
from .stages import STAGES, CandidateImportStage, RunContext
from .workspace import RunWorkspace


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


def _workspace(path: Path) -> RunWorkspace:
    raw, manifest = read_json_object_bytes(path / "run-manifest.json")
    if raw != canonical_json_bytes(manifest):
        raise ContractError("E_RUN_MANIFEST_CANONICAL", "run manifest must use canonical JSON bytes")
    target = manifest.get("target")
    if not isinstance(target, dict) or not isinstance(target.get("root"), str):
        raise ContractError("E_RUN_TARGET", "run manifest target is invalid")
    return RunWorkspace(path, Path(target["root"]))


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
    raw, value = read_json_object_bytes(plan)
    if raw != canonical_json_bytes(value):
        raise ContractError("E_RUN_INPUT", "README plan must use canonical JSON bytes")
    write_canonical_json_atomic(workspace.root / "inputs/readme-plan.json", value)


def _attempt_output_sha256(workspace: RunWorkspace, stage_index: int, attempt: int) -> str | None:
    if attempt == 0:
        return None
    root = workspace.root / "stages" / f"{stage_index + 1:02d}-{STAGE_NAMES[stage_index]}" / "attempts" / str(attempt)
    try:
        entries = sorted(root.iterdir(), key=lambda item: os.fsencode(item.name))
    except OSError:
        return None
    if not entries or any(path.is_symlink() or not path.is_file() for path in entries):
        return None
    projection = [{"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in entries]
    return canonical_sha256(projection)


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
            else _attempt_output_sha256(workspace, index, stage["attempt"])
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
            logger.emit("stage.completed", run_id=manifest["run_id"], stage=adapter.name, status=result.status, duration_ms=int((time.monotonic() - started) * 1000), input_sha256=input_sha256, output_sha256=stage["output_sha256"])
            if result.status != "pass" or stop_after == adapter.name:
                return _summary(manifest)
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
    workspace_path: Path,
    mode: str,
    project_type: str,
    locales: list[str],
    scanner_profile: str,
    plan: Path | None,
    stop_after: str | None,
    logger: StageLogger,
) -> dict[str, object]:
    target = root.resolve()
    workspace = RunWorkspace(workspace_path, target)
    workspace.initialize(
        repository=_repository(target),
        base_sha=_git(target, "rev-parse", "HEAD"),
        configuration={"mode": mode, "project_type": project_type, "locales": locales, "scanner_profile": scanner_profile},
        clock=utc_now,
    )
    with _runner_lock(workspace):
        _copy_plan(workspace, plan)
        return _drive(workspace, logger, stop_after)


def resume_run(*, workspace_path: Path, plan: Path | None, stop_after: str | None, logger: StageLogger) -> dict[str, object]:
    workspace = _workspace(workspace_path)
    with _runner_lock(workspace):
        _copy_plan(workspace, plan)
        return _drive(workspace, logger, stop_after)


def run_status(workspace_path: Path) -> dict[str, object]:
    return _summary(_workspace(workspace_path).read_manifest())


def explain_run(workspace_path: Path) -> dict[str, Any]:
    return deepcopy(_workspace(workspace_path).read_manifest())
