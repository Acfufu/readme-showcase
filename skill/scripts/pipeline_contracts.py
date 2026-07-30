from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


class ContractError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code: str = code


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


def read_json_object(path: Path) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ContractError(
            "E_INPUT_NOT_FOUND",
            f"input not found: {path}",
        ) from exc
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
    if not isinstance(payload, dict):
        raise ContractError(
            "E_SCHEMA_TYPE",
            f"input must be a JSON object: {path}",
        )
    return payload


def write_bytes_atomic(destination: Path, data: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.name}.",
        suffix=".tmp",
        dir=destination.parent,
    )
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as output:
            _ = output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def write_canonical_json_atomic(destination: Path, value: Any) -> None:
    write_bytes_atomic(destination, canonical_json_bytes(value))
