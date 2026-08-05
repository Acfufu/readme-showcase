from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1
MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 32


class ContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code: str = code


def validate_json_nesting(
    raw: bytes,
    *,
    maximum_depth: int = MAX_JSON_DEPTH,
    code: str = "E_INPUT_SIZE",
    context: str = "JSON input",
) -> None:
    """Reject excessive JSON nesting before the recursive stdlib decoder."""

    depth = 0
    in_string = False
    escaped = False
    for byte in raw:
        if in_string:
            if escaped:
                escaped = False
            elif byte == 0x5C:
                escaped = True
            elif byte == 0x22:
                in_string = False
            continue
        if byte == 0x22:
            in_string = True
        elif byte in (0x5B, 0x7B):
            depth += 1
            if depth > maximum_depth:
                raise ContractError(code, f"{context} exceeds structural limits")
        elif byte in (0x5D, 0x7D):
            depth -= 1


def _validate_json_value(value: Any, path: str = "$") -> None:
    if value is None or isinstance(value, (bool, str)):
        return
    if type(value) is int:
        return
    if isinstance(value, float):
        raise ContractError(
            "E_SCHEMA_FLOAT",
            f"{path} must use integer or count-pair values, not floats",
        )
    if isinstance(value, list):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError(
                    "E_SCHEMA_KEY_TYPE",
                    f"{path} contains a non-string object key",
                )
            _validate_json_value(item, f"{path}.{key}")
        return
    raise ContractError(
        "E_SCHEMA_TYPE",
        f"{path} contains unsupported value type {type(value).__name__}",
    )


def canonical_json_bytes(value: Any) -> bytes:
    _validate_json_value(value)
    text = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )
    return f"{text}\n".encode("utf-8")


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def validate_contract(
    payload: Any,
    *,
    required: set[str],
    optional: set[str],
    context: str,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ContractError(
            "E_SCHEMA_TYPE",
            f"{context} must be a JSON object",
        )

    keys = set(payload)
    version = payload.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        raise ContractError(
            "E_SCHEMA_VERSION",
            f"{context} requires schema_version {SCHEMA_VERSION}",
        )

    unknown = sorted(keys - required - optional)
    if unknown:
        raise ContractError(
            "E_SCHEMA_UNKNOWN_FIELD",
            f"{context} contains unknown field: {unknown[0]}",
        )

    missing = sorted(required - keys)
    if missing:
        raise ContractError(
            "E_SCHEMA_MISSING_FIELD",
            f"{context} is missing required field: {missing[0]}",
        )

    _validate_json_value(payload)
    return payload


def _absolute(path: Path) -> Path:
    absolute = Path(os.path.abspath(os.fspath(path)))
    if len(absolute.parts) > 1:
        first = Path(absolute.anchor) / absolute.parts[1]
        private_alias = Path(absolute.anchor) / "private" / absolute.parts[1]
        try:
            if first.is_symlink() and first.resolve(strict=True) == private_alias:
                return private_alias.joinpath(*absolute.parts[2:])
        except OSError:
            pass
    return absolute


def _open_directory(path: Path, *, create: bool, code: str) -> int:
    absolute = _absolute(path)
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(absolute.anchor, flags)
    try:
        for part in absolute.parts[1:]:
            if create:
                try:
                    os.mkdir(part, mode=0o755, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(
                part,
                flags | getattr(os, "O_NOFOLLOW", 0),
                dir_fd=descriptor,
            )
            os.close(descriptor)
            descriptor = child
            if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
                raise OSError(f"not a directory: {part}")
        return descriptor
    except OSError as exc:
        os.close(descriptor)
        raise ContractError(code, f"path contains unavailable or linked directory: {path}") from exc


def read_regular_bytes(
    path: Path,
    *,
    maximum: int,
    path_code: str = "E_INPUT_PATH",
    size_code: str = "E_INPUT_SIZE",
) -> bytes:
    absolute = _absolute(path)
    if not absolute.name:
        raise ContractError(path_code, f"input must name a regular file: {path}")
    parent = _open_directory(absolute.parent, create=False, code=path_code)
    descriptor = -1
    try:
        try:
            expected = os.stat(absolute.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError as exc:
            raise ContractError(
                "E_INPUT_NOT_FOUND",
                f"input not found: {path}",
            ) from exc
        if not stat.S_ISREG(expected.st_mode):
            raise ContractError(path_code, f"input must be a regular file: {path}")
        if expected.st_size > maximum:
            raise ContractError(size_code, f"input exceeds {maximum} bytes: {path}")
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY
            | getattr(os, "O_NOFOLLOW", 0)
            | getattr(os, "O_NONBLOCK", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
            != (
                expected.st_dev,
                expected.st_ino,
                expected.st_size,
                expected.st_mtime_ns,
            )
        ):
            raise ContractError(path_code, f"input changed before read: {path}")
        chunks: list[bytes] = []
        remaining = maximum + 1
        while remaining:
            chunk = os.read(descriptor, min(64 * 1024, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        raw = b"".join(chunks)
        after = os.fstat(descriptor)
        if len(raw) > maximum:
            raise ContractError(size_code, f"input exceeds {maximum} bytes: {path}")
        if (
            len(raw) != opened.st_size
            or (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            != (opened.st_dev, opened.st_ino, opened.st_size, opened.st_mtime_ns)
        ):
            raise ContractError(path_code, f"input changed during read: {path}")
        return raw
    except OSError as exc:
        raise ContractError(path_code, f"cannot read regular input: {path}") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        os.close(parent)


def read_json_object_bytes(
    path: Path,
    *,
    maximum: int = MAX_JSON_BYTES,
) -> tuple[bytes, dict[str, Any]]:
    raw = read_regular_bytes(path, maximum=maximum)
    validate_json_nesting(raw, context=f"JSON input {path}")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError(
            "E_INPUT_ENCODING",
            f"input is not valid UTF-8: {path}",
        ) from exc

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ContractError(
            "E_INPUT_JSON",
            f"invalid JSON at line {exc.lineno}, column {exc.colno}: {path}",
        ) from exc
    except RecursionError:
        raise ContractError(
            "E_INPUT_SIZE",
            f"JSON input exceeds structural limits: {path}",
        ) from None
    if not isinstance(payload, dict):
        raise ContractError(
            "E_SCHEMA_TYPE",
            f"input must be a JSON object: {path}",
        )
    return raw, payload


def read_json_object(path: Path) -> dict[str, Any]:
    return read_json_object_bytes(path)[1]


def write_bytes_atomic(destination: Path, data: bytes) -> None:
    absolute = _absolute(destination)
    if not absolute.name:
        raise ContractError("E_OUTPUT_PATH", f"output must name a file: {destination}")
    parent = _open_directory(
        absolute.parent,
        create=True,
        code="E_OUTPUT_PATH",
    )
    temporary_name = f".{absolute.name}.{secrets.token_hex(8)}.tmp"
    descriptor = -1
    try:
        try:
            existing = os.stat(
                absolute.name,
                dir_fd=parent,
                follow_symlinks=False,
            )
        except FileNotFoundError:
            existing = None
        if existing is not None and not stat.S_ISREG(existing.st_mode):
            raise ContractError(
                "E_OUTPUT_PATH",
                f"output must be absent or a regular file: {destination}",
            )
        descriptor = os.open(
            temporary_name,
            os.O_WRONLY
            | os.O_CREAT
            | os.O_EXCL
            | getattr(os, "O_NOFOLLOW", 0),
            0o600,
            dir_fd=parent,
        )
        view = memoryview(data)
        written = 0
        while written < len(view):
            written += os.write(descriptor, view[written:])
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = -1
        os.replace(
            temporary_name,
            absolute.name,
            src_dir_fd=parent,
            dst_dir_fd=parent,
        )
        os.fsync(parent)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        try:
            os.unlink(temporary_name, dir_fd=parent)
        except FileNotFoundError:
            pass
        os.close(parent)


def write_canonical_json_atomic(destination: Path, value: Any) -> None:
    write_bytes_atomic(destination, canonical_json_bytes(value))
