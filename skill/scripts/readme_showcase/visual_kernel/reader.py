"""Read a compiled visual attempt through one fail-closed trust boundary.

Stage 6 owns compiled bytes.  This module only reads an already committed
attempt; it never repairs, rewrites, or promotes an artifact.  The inventory
and Asset Manifest v3 remain the authorities for the artifact set while the
existing bounded, no-follow reader protects every filesystem read.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from collections.abc import Mapping
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import ContractError, _open_directory, canonical_json_bytes
from ..contracts.assets import validate_asset_manifest_v3
from ..contracts.common import MAX_SOURCE_BYTES, normalize_posix_path, read_source_bytes
from .artifacts import (
    MAX_COMPILED_BYTES,
    _fingerprint_from_inventory,
    _path_kind_limit,
)
from .compiler import CompiledVisual


_SHA256 = frozenset("0123456789abcdef")
_INVENTORY_PATH = "compiled/inventory.json"
_MANIFEST_PATH = "asset-manifest.json"
_RETENTION_MARKER = "manual"
_MAX_TREE_DEPTH = 16
_MAX_TREE_ENTRIES = 10_000
_PATH_ERROR_CODES = frozenset(
    {
        "E_EVIDENCE_PATH",
        "E_INPUT_NOT_FOUND",
        "E_INPUT_PATH",
        "E_PATH",
        "E_RUN_PATH",
        "E_VISUAL_PATH",
    }
)
_REQUIRED_COMPILED_FIELDS = frozenset({"inventory", "fingerprint", "retention"})
_REF_FIELDS = frozenset({"path", "sha256"})


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _fingerprint_error(message: str) -> ContractError:
    return _fail("E_VISUAL_FINGERPRINT", message)


def _path_error(message: str) -> ContractError:
    return _fail("E_VISUAL_PATH", message)


def _sha256(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in _SHA256 for character in value)
    ):
        raise _fingerprint_error(f"{context} must be a lowercase SHA-256 digest")
    return value


def _relative_path(value: Any, context: str) -> str:
    try:
        normalized = normalize_posix_path(value)
    except ContractError:
        raise _path_error(f"{context} must be a safe relative POSIX path") from None
    if normalized != value:
        raise _path_error(f"{context} must be a safe relative POSIX path")
    return normalized


def _reference(value: Any, context: str, *, expected_path: str) -> tuple[str, str]:
    if not isinstance(value, Mapping) or set(value) != _REF_FIELDS:
        raise _fingerprint_error(f"{context} must contain only path and sha256")
    path = _relative_path(value.get("path"), f"{context}.path")
    if path != expected_path:
        raise _path_error(f"{context}.path does not match the compiled attempt topology")
    return path, _sha256(value.get("sha256"), f"{context}.sha256")


def _bundle_compiled(bundle: Mapping[str, Any]) -> tuple[dict[str, str], str]:
    compiled = bundle.get("compiled")
    if not isinstance(compiled, Mapping) or set(compiled) != _REQUIRED_COMPILED_FIELDS:
        raise _fingerprint_error("bundle.compiled must contain inventory, fingerprint, and retention")
    if compiled.get("retention") != _RETENTION_MARKER:
        raise _fingerprint_error("bundle.compiled.retention must be manual")

    inventory_path, inventory_sha256 = _reference(
        compiled.get("inventory"),
        "bundle.compiled.inventory",
        expected_path=_INVENTORY_PATH,
    )
    fingerprint_sha256 = _sha256(compiled.get("fingerprint"), "bundle.compiled.fingerprint")
    return {"path": inventory_path, "sha256": inventory_sha256}, fingerprint_sha256


def _asset_manifest_reference(bundle: Mapping[str, Any]) -> tuple[str, str]:
    artifacts = bundle.get("artifacts")
    if not isinstance(artifacts, Mapping):
        raise _fingerprint_error("bundle.artifacts must contain asset_manifest")
    return _reference(artifacts.get("asset_manifest"), "bundle.artifacts.asset_manifest", expected_path=_MANIFEST_PATH)


def _read_relative(root: Path, path: str, *, maximum: int, context: str) -> bytes:
    """Read one bounded regular file without exposing the absolute root."""

    try:
        return read_source_bytes(root, path, maximum=min(maximum, MAX_SOURCE_BYTES))
    except ContractError as exc:
        code = "E_VISUAL_PATH" if exc.code in _PATH_ERROR_CODES else "E_VISUAL_FINGERPRINT"
        detail = "is unavailable" if code == "E_VISUAL_PATH" else "does not satisfy its bound"
        raise _fail(code, f"{context} {detail}: {path}") from None
    except (OSError, ValueError):
        raise _path_error(f"{context} is unavailable: {path}") from None


def _decode_inventory(raw: bytes):
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _fingerprint_error("compiled inventory must be canonical JSON") from None
    try:
        fingerprint = _fingerprint_from_inventory(value, "compiled inventory")
    except ContractError as exc:
        # The fingerprint parser is the single source of truth for layer
        # ordering and prior-layer bindings.  Keep its failure code, but do
        # not include any filesystem-derived detail.
        if exc.code == "E_VISUAL_PATH":
            raise _path_error("compiled inventory contains an unsafe path") from None
        raise _fingerprint_error("compiled inventory fingerprint is invalid") from None
    if fingerprint.canonical_bytes() != raw:
        raise _fingerprint_error("compiled inventory must use canonical LayeredFingerprint bytes")
    return value, fingerprint


def _inventory_paths(fingerprint: Any, inventory_file_sha256: str) -> tuple[dict[str, str], ...]:
    """Project all inventory references into the concrete stage-6 paths."""

    records: dict[str, str] = {
        "compiled/visual-spec.json": fingerprint.spec_sha256,
        "compiled/theme.json": fingerprint.theme_sha256,
        # ``inventory_sha256`` is the identity of the LayeredFingerprint
        # projection.  The file itself has a different SHA-256, which is the
        # value carried by Bundle/Asset Manifest references.
        _INVENTORY_PATH: inventory_file_sha256,
    }
    for locale, variant, digest, _ in fingerprint.scenes:
        records[f"compiled/scenes/{locale}/{variant}.json"] = digest
    for directory, values in (
        ("gates", fingerprint.gates),
        ("timeline", fingerprint.timelines),
        ("interaction", fingerprint.interactions),
    ):
        for locale, variant, digest, _ in values:
            records[f"compiled/{directory}/{locale}/{variant}.json"] = digest
    for path, digest, _ in fingerprint.artifacts:
        if path in records and records[path] != digest:
            raise _fingerprint_error(f"compiled inventory contains conflicting digest: {path}")
        records[path] = digest
    return tuple(
        {"path": path, "sha256": records[path]}
        for path in sorted(records, key=lambda value: value.encode("utf-8"))
    )


def _open_child_directory(parent: int, name: str) -> int:
    try:
        expected = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISLNK(expected.st_mode) or not stat.S_ISDIR(expected.st_mode):
            raise _path_error(f"compiled artifact ancestry is not a real directory: {name}")
        descriptor = os.open(
            name,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
            dir_fd=parent,
        )
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (expected.st_dev, expected.st_ino):
            os.close(descriptor)
            raise _path_error(f"compiled artifact ancestry changed during read: {name}")
        return descriptor
    except ContractError:
        raise
    except OSError:
            raise _path_error(f"compiled artifact ancestry is unavailable: {name}") from None


def _enumerate_tree(
    descriptor: int,
    prefix: str,
    *,
    depth: int = 0,
    remaining: list[int] | None = None,
) -> tuple[set[str], set[str]]:
    if depth > _MAX_TREE_DEPTH:
        raise _fail("E_VISUAL_RESOURCE", "compiled artifact tree exceeds its depth bound")
    budget = [_MAX_TREE_ENTRIES] if remaining is None else remaining
    files: set[str] = set()
    directories: set[str] = {prefix}
    names: list[str] = []
    try:
        with os.scandir(descriptor) as entries:
            for entry in entries:
                budget[0] -= 1
                if budget[0] < 0:
                    raise _fail("E_VISUAL_RESOURCE", "compiled artifact tree exceeds its entry bound")
                names.append(entry.name)
    except ContractError:
        raise
    except OSError:
        raise _path_error(f"compiled artifact directory is unavailable: {prefix}") from None
    for name in sorted(names, key=os.fsencode):
        if not isinstance(name, str) or not name or "/" in name or "\\" in name:
            raise _path_error(f"compiled artifact path is unsafe: {prefix}/{name}")
        relative = f"{prefix}/{name}"
        try:
            info = os.stat(name, dir_fd=descriptor, follow_symlinks=False)
        except OSError:
            raise _path_error(f"compiled artifact entry is unavailable: {relative}") from None
        if stat.S_ISLNK(info.st_mode):
            raise _path_error(f"compiled artifact entry must not be a symlink: {relative}")
        if stat.S_ISDIR(info.st_mode):
            child = _open_child_directory(descriptor, name)
            try:
                child_files, child_directories = _enumerate_tree(
                    child,
                    relative,
                    depth=depth + 1,
                    remaining=budget,
                )
            finally:
                os.close(child)
            files.update(child_files)
            directories.update(child_directories)
            continue
        if not stat.S_ISREG(info.st_mode):
            raise _path_error(f"compiled artifact entry must be a regular file: {relative}")
        files.add(relative)
    return files, directories


def _enumerate_artifact_trees(root: Path, expected_files: set[str]) -> set[str]:
    """Enumerate the two compiled output trees without following links."""

    roots = (("compiled", "compiled"), ("assets/readme-showcase", "assets/readme-showcase"))
    all_files: set[str] = set()
    all_directories: set[str] = set()
    remaining = [_MAX_TREE_ENTRIES]
    for relative, label in roots:
        path = root.joinpath(*PurePosixPath(relative).parts)
        try:
            descriptor = _open_directory(path, create=False, code="E_VISUAL_PATH")
        except ContractError:
            raise _path_error(f"compiled artifact directory is unavailable: {label}") from None
        try:
            files, directories = _enumerate_tree(descriptor, relative, remaining=remaining)
        finally:
            os.close(descriptor)
        all_files.update(files)
        all_directories.update(directories)

    if all_files != expected_files:
        raise _fingerprint_error("compiled artifact trees do not match inventory paths")
    expected_directories: set[str] = set()
    for path in all_files:
        parts = path.split("/")
        expected_directories.update("/".join(parts[:index]) for index in range(1, len(parts)))
    if any(directory not in expected_directories for directory in all_directories):
        raise _path_error("compiled artifact trees contain an unreferenced directory")
    return all_files


def _scan_against_inventory(root: Path, expected: set[str]) -> None:
    observed = _enumerate_artifact_trees(root, expected)
    # `_enumerate_artifact_trees` compares the complete file set before
    # returning; retain this check as a defensive assertion for future callers.
    if observed != expected:
        raise _fingerprint_error("compiled artifact trees do not match inventory paths")


def _load_asset_manifest(root: Path, bundle: Mapping[str, Any]) -> dict[str, Any]:
    manifest_path, manifest_sha256 = _asset_manifest_reference(bundle)
    raw = _read_relative(root, manifest_path, maximum=MAX_COMPILED_BYTES, context="asset manifest")
    if hashlib.sha256(raw).hexdigest() != manifest_sha256:
        raise _fingerprint_error("asset manifest bytes differ from bundle reference")
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError):
        raise _fingerprint_error("asset manifest must be canonical JSON") from None
    try:
        normalized = validate_asset_manifest_v3(payload, artifact_root=root)
    except ContractError as exc:
        if exc.code in _PATH_ERROR_CODES:
            raise _path_error("asset manifest contains an unsafe path") from None
        if exc.code in {"E_VISUAL_RESOURCE", "E_VISUAL_SVG_SECURITY"}:
            raise _fail(exc.code, "asset manifest compiled artifact semantics are invalid") from None
        raise _fingerprint_error("asset manifest does not bind compiled artifacts") from None
    if canonical_json_bytes(normalized) != raw:
        raise _fingerprint_error("asset manifest must use canonical bytes")
    return normalized


def load_compiled_visual(attempt_root: os.PathLike[str] | str, bundle: Mapping[str, Any]) -> CompiledVisual:
    """Load and validate one immutable stage-6 compiled artifact set.

    ``attempt_root`` is the committed stage-6 attempt directory.  ``bundle``
    is the Generated Bundle v3 projection that points at its Asset Manifest
    and compiled inventory.  The result contains only relative artifact keys,
    canonical bytes, and the inventory identity; no absolute path is returned
    or placed in a normal-mode diagnostic.
    """

    if not isinstance(bundle, Mapping):
        raise _fingerprint_error("compiled bundle must be an object")
    if bundle.get("schema_version") != 3:
        raise _fingerprint_error("compiled bundle requires schema_version 3")
    try:
        root = Path(os.fspath(attempt_root))
    except (TypeError, ValueError) as exc:
        raise _path_error("compiled attempt root is unavailable") from None
    try:
        root_info = root.lstat()
    except OSError:
        raise _path_error("compiled attempt root is unavailable") from None
    if stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode):
        raise _path_error("compiled attempt root must be a real directory")

    inventory_ref, bundle_fingerprint = _bundle_compiled(bundle)
    inventory_raw = _read_relative(
        root,
        inventory_ref["path"],
        maximum=MAX_COMPILED_BYTES,
        context="compiled inventory",
    )
    if hashlib.sha256(inventory_raw).hexdigest() != inventory_ref["sha256"]:
        raise _fingerprint_error("compiled inventory bytes differ from bundle reference")
    _, fingerprint = _decode_inventory(inventory_raw)
    if fingerprint.inventory_sha256 != bundle_fingerprint:
        raise _fingerprint_error("bundle compiled fingerprint differs from inventory")

    references = _inventory_paths(fingerprint, inventory_ref["sha256"])
    expected_paths = {reference["path"] for reference in references}
    _scan_against_inventory(root, expected_paths)

    artifacts: dict[str, bytes] = {}
    total = 0
    for reference in references:
        path = reference["path"]
        expected_sha256 = reference["sha256"]
        try:
            _, maximum = _path_kind_limit(path)
        except ContractError as exc:
            if exc.code == "E_VISUAL_PATH":
                raise _path_error("compiled inventory contains an unsupported path") from None
            raise _fingerprint_error("compiled inventory contains an invalid artifact path") from None
        raw = _read_relative(root, path, maximum=maximum, context="compiled artifact")
        total += len(raw)
        if total > MAX_COMPILED_BYTES:
            raise _fingerprint_error("compiled artifact set exceeds its aggregate byte bound")
        if hashlib.sha256(raw).hexdigest() != expected_sha256:
            raise _fingerprint_error(f"compiled artifact hash drift: {path}")
        artifacts[path] = raw

    manifest = _load_asset_manifest(root, bundle)
    manifest_compiled = manifest.get("compiled")
    if not isinstance(manifest_compiled, Mapping):
        raise _fingerprint_error("asset manifest compiled projection is unavailable")
    manifest_inventory = manifest_compiled.get("inventory")
    if not isinstance(manifest_inventory, Mapping):
        raise _fingerprint_error("asset manifest inventory reference is unavailable")
    try:
        manifest_path = _relative_path(manifest_inventory.get("path"), "asset manifest inventory.path")
        manifest_sha = _sha256(manifest_inventory.get("sha256"), "asset manifest inventory.sha256")
    except ContractError:
        raise
    if manifest_path != inventory_ref["path"] or manifest_sha != inventory_ref["sha256"]:
        raise _fingerprint_error("asset manifest inventory reference differs from bundle")

    _scan_against_inventory(root, expected_paths)
    try:
        return CompiledVisual(artifacts, fingerprint.inventory_sha256)
    except ContractError as exc:
        if exc.code == "E_VISUAL_PATH":
            raise _path_error("compiled artifact inventory contains an unsafe path") from None
        raise _fingerprint_error("compiled artifact inventory failed validation") from None


__all__ = ["load_compiled_visual"]
