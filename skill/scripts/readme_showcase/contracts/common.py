from __future__ import annotations

import importlib
import stat
import unicodedata
from pathlib import Path, PurePosixPath
from typing import Any


_contracts = importlib.import_module(
    "skill.scripts.pipeline_contracts" if __package__.startswith("skill.") else "pipeline_contracts"
)
ContractError = _contracts.ContractError
canonical_json_bytes = _contracts.canonical_json_bytes
canonical_sha256 = _contracts.canonical_sha256

MAX_PATH_BYTES = 4096
MAX_TEXT_BYTES = 4096
MAX_JSON_DEPTH = _contracts.MAX_JSON_DEPTH
MAX_JSON_NODES = 100_000
MAX_SOURCE_BYTES = 8 * 1024 * 1024


def normalize_text(value: Any, context: str, *, maximum: int = MAX_TEXT_BYTES) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ContractError("E_EVIDENCE_VALUE", f"{context} must be a non-empty normalized string")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized.encode("utf-8")) > maximum:
        raise ContractError("E_EVIDENCE_LIMIT", f"{context} exceeds {maximum} bytes")
    return normalized


def normalize_posix_path(value: Any) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value:
        raise ContractError("E_EVIDENCE_PATH", "source.path must be a non-empty POSIX path")
    normalized = unicodedata.normalize("NFC", value)
    path = PurePosixPath(normalized)
    if (
        path.is_absolute()
        or normalized.startswith("~/")
        or any(part in {"", ".", ".."} for part in normalized.split("/"))
        or len(normalized.encode("utf-8")) > MAX_PATH_BYTES
    ):
        raise ContractError("E_EVIDENCE_PATH", "source.path must be a bounded relative path without traversal")
    return path.as_posix()


def validate_bounded_json(value: Any) -> Any:
    nodes = 0

    def visit(item: Any, depth: int) -> None:
        nonlocal nodes
        nodes += 1
        if nodes > MAX_JSON_NODES or depth > MAX_JSON_DEPTH:
            raise ContractError("E_EVIDENCE_LIMIT", "evidence value exceeds structural limits")
        if item is None or isinstance(item, bool) or type(item) is int:
            return
        if isinstance(item, str):
            if len(item.encode("utf-8")) > MAX_TEXT_BYTES:
                raise ContractError("E_EVIDENCE_LIMIT", "evidence string exceeds byte limit")
            return
        if isinstance(item, list):
            for child in item:
                visit(child, depth + 1)
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ContractError("E_SCHEMA_KEY_TYPE", "evidence value contains a non-string key")
                if len(key.encode("utf-8")) > MAX_TEXT_BYTES:
                    raise ContractError("E_EVIDENCE_LIMIT", "evidence key exceeds byte limit")
                visit(child, depth + 1)
            return
        raise ContractError("E_SCHEMA_TYPE", f"evidence value contains unsupported {type(item).__name__}")

    visit(value, 0)
    return value


def read_source_bytes(root: Path, relative: str, *, maximum: int = MAX_SOURCE_BYTES) -> bytes:
    if type(maximum) is not int or maximum < 0 or maximum > MAX_SOURCE_BYTES:
        raise ContractError("E_EVIDENCE_LIMIT", f"source byte limit must be between 0 and {MAX_SOURCE_BYTES}")
    path = normalize_posix_path(relative)
    try:
        root_info = root.lstat()
    except OSError as exc:
        raise ContractError("E_EVIDENCE_PATH", "source root is unavailable") from exc
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise ContractError("E_EVIDENCE_PATH", "source root must be a real directory")
    try:
        return _contracts.read_regular_bytes(
            root.joinpath(*PurePosixPath(path).parts),
            maximum=maximum,
            path_code="E_EVIDENCE_PATH",
            size_code="E_EVIDENCE_LIMIT",
        )
    except ContractError as exc:
        if exc.code == "E_INPUT_NOT_FOUND":
            raise ContractError("E_EVIDENCE_PATH", "source path is unavailable") from exc
        raise
