from __future__ import annotations

import fcntl
import json
import os
import stat
from collections.abc import Callable
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    _open_directory,
    canonical_json_bytes,
    read_json_object_bytes,
)
from ..contracts.feedback import build_feedback_event, validate_feedback_event
from ..contracts.publishing import validate_delivery_result_v1
from ..contracts.run import validate_run_manifest


LOG_PATH = "feedback/events.v1.jsonl"
MAX_LOG_BYTES = 64 * 1024 * 1024
Write = Callable[[int, bytes], int]


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _canonical_input(path: Path, validator: Callable[[Any], dict[str, Any]], code: str) -> dict[str, Any]:
    try:
        raw, payload = read_json_object_bytes(path)
        validator(payload)
    except ContractError as exc:
        raise ContractError(code, f"bound feedback input is invalid: {path.name}") from exc
    if raw != canonical_json_bytes(payload):
        _fail(code, f"bound feedback input is not canonical JSON: {path.name}")
    return payload


def resolve_feedback_bindings(
    workspace: Path,
    delivery_result_path: Path,
    *,
    run_id: str,
    fingerprint: str,
) -> tuple[dict[str, Any], dict[str, Any]]:
    manifest = _canonical_input(workspace / "run-manifest.json", validate_run_manifest, "E_FEEDBACK_RUN")
    delivery = _canonical_input(delivery_result_path, validate_delivery_result_v1, "E_FEEDBACK_DELIVERY")
    if manifest["run_id"] != run_id:
        _fail("E_FEEDBACK_RUN", "feedback run_id is unknown or stale")
    if delivery["operation_id"] != fingerprint:
        _fail("E_FEEDBACK_BINDING", "feedback fingerprint differs from delivery result")
    return manifest, delivery


def _parse_log(raw: bytes) -> tuple[list[dict[str, Any]], dict[str, bytes]]:
    if len(raw) > MAX_LOG_BYTES:
        _fail("E_FEEDBACK_LOG", "feedback log exceeds fixed local size limit")
    if raw and not raw.endswith(b"\n"):
        _fail("E_FEEDBACK_LOG", "feedback log contains an incomplete line")
    events: list[dict[str, Any]] = []
    indexed: dict[str, bytes] = {}
    for number, line in enumerate(raw.splitlines(keepends=True), 1):
        try:
            payload = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ContractError("E_FEEDBACK_LOG", f"feedback log line {number} is malformed") from exc
        if not isinstance(payload, dict) or canonical_json_bytes(payload) != line:
            _fail("E_FEEDBACK_LOG", f"feedback log line {number} is not canonical JSONL")
        try:
            validate_feedback_event(payload)
        except ContractError as exc:
            raise ContractError("E_FEEDBACK_LOG", f"feedback log line {number} is invalid") from exc
        event_id = payload["event_id"]
        previous = indexed.get(event_id)
        if previous is not None and previous != line:
            _fail("E_FEEDBACK_COLLISION", f"feedback log line {number} collides with an earlier event")
        indexed[event_id] = line
        events.append(payload)
    return events, indexed


def _open_log(workspace: Path, relative_log: str) -> tuple[int, int, bool]:
    if relative_log != LOG_PATH or PurePosixPath(relative_log).as_posix() != relative_log:
        _fail("E_FEEDBACK_PATH", "feedback log path is fixed inside the local workspace")
    root = _open_directory(workspace, create=False, code="E_FEEDBACK_PATH")
    os.close(root)
    parent_path = workspace / "feedback"
    parent = _open_directory(parent_path, create=True, code="E_FEEDBACK_PATH")
    name = "events.v1.jsonl"
    flags = (
        os.O_RDWR
        | os.O_APPEND
        | getattr(os, "O_NOFOLLOW", 0)
        | getattr(os, "O_NONBLOCK", 0)
    )
    created = False
    try:
        try:
            descriptor = os.open(name, flags, dir_fd=parent)
        except FileNotFoundError:
            try:
                descriptor = os.open(name, flags | os.O_CREAT | os.O_EXCL, 0o600, dir_fd=parent)
                created = True
            except FileExistsError:
                descriptor = os.open(name, flags, dir_fd=parent)
        info = os.fstat(descriptor)
        current = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(info.st_mode)
            or stat.S_ISLNK(current.st_mode)
            or (info.st_dev, info.st_ino) != (current.st_dev, current.st_ino)
        ):
            os.close(descriptor)
            _fail("E_FEEDBACK_PATH", "feedback log must be one stable regular file")
        return parent, descriptor, created
    except ContractError:
        os.close(parent)
        raise
    except OSError as exc:
        os.close(parent)
        raise ContractError("E_FEEDBACK_PATH", "feedback log path is unavailable or special") from exc


def append_feedback_event(
    workspace: Path,
    event: dict[str, Any],
    *,
    relative_log: str = LOG_PATH,
    write: Write = os.write,
) -> dict[str, object]:
    validate_feedback_event(event)
    line = canonical_json_bytes(event)
    parent, descriptor, created = _open_log(workspace, relative_log)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX)
        opened = os.fstat(descriptor)
        current = os.stat("events.v1.jsonl", dir_fd=parent, follow_symlinks=False)
        if (
            not stat.S_ISREG(current.st_mode)
            or (opened.st_dev, opened.st_ino) != (current.st_dev, current.st_ino)
        ):
            _fail("E_FEEDBACK_PATH", "feedback log changed before append")
        if opened.st_size > MAX_LOG_BYTES:
            _fail("E_FEEDBACK_LOG", "feedback log exceeds fixed local size limit")
        os.lseek(descriptor, 0, os.SEEK_SET)
        chunks: list[bytes] = []
        remaining = opened.st_size
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                _fail("E_FEEDBACK_LOG", "feedback log changed during read")
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after_read = os.fstat(descriptor)
        if (after_read.st_dev, after_read.st_ino, after_read.st_size) != (
            opened.st_dev,
            opened.st_ino,
            opened.st_size,
        ):
            _fail("E_FEEDBACK_LOG", "feedback log changed during read")
        events, indexed = _parse_log(raw)
        previous = indexed.get(event["event_id"])
        if previous is not None:
            if previous == line:
                return {
                    "schema_version": 1,
                    "status": "ignored_duplicate",
                    "event_id": event["event_id"],
                    "log": LOG_PATH,
                    "line_count": len(events),
                }
            _fail("E_FEEDBACK_COLLISION", "event_id already belongs to different canonical bytes")

        if opened.st_size + len(line) > MAX_LOG_BYTES:
            _fail("E_FEEDBACK_LOG", "feedback append would exceed fixed local size limit")

        original_size = opened.st_size
        try:
            written = write(descriptor, line)
            if written != len(line):
                _fail("E_FEEDBACK_INTERRUPTED", "feedback append was short or interrupted")
            os.fsync(descriptor)
            committed = os.fstat(descriptor)
            try:
                visible = os.stat("events.v1.jsonl", dir_fd=parent, follow_symlinks=False)
            except OSError as exc:
                raise ContractError("E_FEEDBACK_PATH", "feedback log disappeared during append") from exc
            expected_size = original_size + len(line)
            if (
                not stat.S_ISREG(visible.st_mode)
                or (visible.st_dev, visible.st_ino) != (committed.st_dev, committed.st_ino)
                or visible.st_size != expected_size
                or committed.st_size != expected_size
            ):
                _fail("E_FEEDBACK_PATH", "feedback log was replaced during append")
        except (OSError, InterruptedError, KeyboardInterrupt, ContractError) as exc:
            os.ftruncate(descriptor, original_size)
            os.fsync(descriptor)
            if created:
                try:
                    current = os.stat("events.v1.jsonl", dir_fd=parent, follow_symlinks=False)
                except OSError:
                    current = None
                if current is not None and (
                    current.st_dev,
                    current.st_ino,
                    current.st_size,
                ) == (opened.st_dev, opened.st_ino, 0):
                    os.unlink("events.v1.jsonl", dir_fd=parent)
            if isinstance(exc, ContractError):
                raise
            raise ContractError("E_FEEDBACK_INTERRUPTED", "feedback append was interrupted") from exc
        return {
            "schema_version": 1,
            "status": "appended",
            "event_id": event["event_id"],
            "log": LOG_PATH,
            "line_count": len(events) + 1,
        }
    finally:
        try:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
            os.close(parent)


def record_feedback(
    *,
    workspace: Path,
    delivery_result_path: Path,
    run_id: str,
    fingerprint: str,
    event: str,
    details: Any,
    recorded_at: str,
    write: Write = os.write,
) -> dict[str, object]:
    _, delivery = resolve_feedback_bindings(
        workspace,
        delivery_result_path,
        run_id=run_id,
        fingerprint=fingerprint,
    )
    payload = build_feedback_event(
        run_id=run_id,
        fingerprint=fingerprint,
        event=event,
        recorded_at=recorded_at,
        details=details,
    )
    if event.startswith("pr-"):
        if delivery["status"] != "delivered":
            _fail("E_FEEDBACK_DELIVERY", "PR feedback requires a delivered result")
        if payload["details"]["pr_number"] != delivery["pr_number"]:
            _fail("E_FEEDBACK_BINDING", "feedback PR number differs from delivery result")
    return append_feedback_event(workspace, payload, write=write)


__all__ = [
    "LOG_PATH",
    "append_feedback_event",
    "record_feedback",
    "resolve_feedback_bindings",
]
