"""Bounded Python transport for the repository's pinned ELK geometry mode.

This module deliberately transports the adapter's closed semantic envelope.  It
does not translate Visual Kernel kinds into the older ELK envelope.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import selectors
import shutil
import signal
import stat
import subprocess
import tempfile
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, read_regular_bytes


_NODE_VERSION = "22.22.3"
_ELK_VERSION = "0.9.3"
_PACKAGE_INTEGRITY = "sha512-f/ZeWvW/BCXbhGEf1Ujp29EASo/lk1FDnETgNKwJrsVvGZhUWCZyg3xLJjAsxfOmt8KjswHmI5EwCQcPMpOYhQ=="
_PACKAGE_SHA256 = "fb9bb80b980c72022fb4540b38aa0545242b4eb67b82250aeae2f0beb67eea25"
_MODULE_SHA256 = "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4"
_LICENSE_SHA256 = "89591d4578fb1ebd91501312a3d25f021bd865a2e436641c1cf7b1bc7e3c1617"
_LICENSE = "EPL-2.0"

_MAX_INPUT_BYTES = 256 * 1024
_MAX_GEOMETRY_BYTES = 2 * 1024 * 1024
_MAX_METADATA_BYTES = 64 * 1024
_MAX_PROCESS_BYTES = 1024 * 1024
_TIMEOUT_SECONDS = 35.0
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}\Z")
_COLOR = re.compile(r"#[0-9a-fA-F]{6}\Z")
_ERROR_CODE = re.compile(r"\b(E_[A-Z0-9_]+)\b")

_ENVELOPE_FIELDS = frozenset(
    {
        "schema_version",
        "diagram_type",
        "accessibility_title",
        "accessibility_claim_id",
        "direction",
        "palette",
        "groups",
        "nodes",
        "edges",
        "claim_ids",
    }
)
_PALETTE_FIELDS = frozenset(
    {"background", "node_background", "node_border", "node_text", "edge_color", "edge_label_color"}
)
_GROUP_FIELDS = frozenset({"id", "label", "parent_id", "claim_id"})
_NODE_FIELDS = frozenset({"id", "label", "group_id", "kind", "claim_id"})
_EDGE_FIELDS = frozenset({"source", "target", "label", "claim_id"})
_ALLOWED_TYPES = frozenset({"architecture", "flowchart", "c4"})
_ALLOWED_DIRECTIONS = frozenset({"TB", "BT", "LR", "RL"})
_ALLOWED_KINDS = frozenset({"component", "service", "database", "person", "system", "external", "container"})

_GEOMETRY_FIELDS = frozenset({"schema_version", "engine", "canvas", "groups", "nodes", "ports", "edges"})
_ENGINE_FIELDS = frozenset(
    {
        "engine_kind",
        "package_name",
        "package_version",
        "package_sha256",
        "module_sha256",
        "node_version",
        "renderer_sha256",
    }
)
_CANVAS_FIELDS = frozenset({"width", "height"})
_RECT_FIELDS = frozenset({"id", "parent_id", "x", "y", "width", "height"})
_PORT_FIELDS = frozenset({"id", "node_id", "x", "y", "width", "height"})
_EDGE_GEOMETRY_FIELDS = frozenset({"id", "sections"})
_SECTION_FIELDS = frozenset({"start", "bends", "end"})
_POINT_FIELDS = frozenset({"x", "y"})
_METADATA_FIELDS = frozenset(
    {
        "schema_version",
        "engine_kind",
        "package_name",
        "package_version",
        "package_integrity",
        "package_sha256",
        "module_sha256",
        "license_spdx",
        "license_sha256",
        "node_version",
        "platform",
        "architecture",
        "input_sha256",
        "renderer_sha256",
        "output_sha256",
        "run_hashes",
        "validation",
        "fallback_state",
    }
)


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _freeze(value: Any) -> Any:
    if isinstance(value, dict):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _closed(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or isinstance(value, (str, bytes, bytearray)):
        raise _fail("E_INPUT_SCHEMA", f"{context} must be an object")
    keys = set(value)
    if keys != fields:
        raise _fail("E_INPUT_SCHEMA", f"{context} field set is invalid")
    return dict(value)


def _identifier(value: Any, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or _ID.fullmatch(value) is None:
        raise _fail("E_INPUT_SCHEMA", f"{context} is invalid")
    return value


def _text(value: Any, context: str, *, nullable: bool = False) -> str | None:
    if nullable and value is None:
        return None
    if not isinstance(value, str) or not value or len(value) > 120 or "\n" in value or "\r" in value:
        raise _fail("E_INPUT_SCHEMA", f"{context} is invalid")
    return value


def _validate_envelope(value: Any) -> dict[str, Any]:
    envelope = _closed(value, _ENVELOPE_FIELDS, "semantic envelope")
    if envelope["schema_version"] != 1 or envelope["diagram_type"] not in _ALLOWED_TYPES:
        raise _fail("E_INPUT_SCHEMA", "semantic envelope identity is invalid")
    if envelope["direction"] not in _ALLOWED_DIRECTIONS:
        raise _fail("E_INPUT_SCHEMA", "semantic envelope direction is invalid")
    _text(envelope["accessibility_title"], "accessibility_title")
    _identifier(envelope["accessibility_claim_id"], "accessibility_claim_id")

    palette = _closed(envelope["palette"], _PALETTE_FIELDS, "palette")
    if any(not isinstance(palette[field], str) or _COLOR.fullmatch(palette[field]) is None for field in _PALETTE_FIELDS):
        raise _fail("E_INPUT_SCHEMA", "palette contains an invalid color")

    groups_raw = envelope["groups"]
    nodes_raw = envelope["nodes"]
    edges_raw = envelope["edges"]
    if not isinstance(groups_raw, list) or len(groups_raw) > 50:
        raise _fail("E_INPUT_SCHEMA", "groups exceed bound")
    if not isinstance(nodes_raw, list) or not 1 <= len(nodes_raw) <= 100:
        raise _fail("E_INPUT_SCHEMA", "nodes exceed bound")
    if not isinstance(edges_raw, list) or len(edges_raw) > 200:
        raise _fail("E_INPUT_SCHEMA", "edges exceed bound")

    groups: list[dict[str, Any]] = []
    for index, raw in enumerate(groups_raw):
        context = f"groups[{index}]"
        item = _closed(raw, _GROUP_FIELDS, context)
        _identifier(item["id"], f"{context}.id")
        _text(item["label"], f"{context}.label")
        _identifier(item["parent_id"], f"{context}.parent_id", nullable=True)
        _identifier(item["claim_id"], f"{context}.claim_id")
        groups.append(item)

    nodes: list[dict[str, Any]] = []
    for index, raw in enumerate(nodes_raw):
        context = f"nodes[{index}]"
        item = _closed(raw, _NODE_FIELDS, context)
        _identifier(item["id"], f"{context}.id")
        _text(item["label"], f"{context}.label")
        _identifier(item["group_id"], f"{context}.group_id", nullable=True)
        if item["kind"] not in _ALLOWED_KINDS:
            raise _fail("E_INPUT_SCHEMA", f"{context}.kind is invalid")
        _identifier(item["claim_id"], f"{context}.claim_id")
        nodes.append(item)

    edges: list[dict[str, Any]] = []
    for index, raw in enumerate(edges_raw):
        context = f"edges[{index}]"
        item = _closed(raw, _EDGE_FIELDS, context)
        _identifier(item["source"], f"{context}.source")
        _identifier(item["target"], f"{context}.target")
        _text(item["label"], f"{context}.label", nullable=True)
        _identifier(item["claim_id"], f"{context}.claim_id", nullable=True)
        if (item["label"] is None) != (item["claim_id"] is None):
            raise _fail("E_INPUT_SCHEMA", f"{context} label and claim are inconsistent")
        edges.append(item)

    all_ids = [item["id"] for item in groups] + [item["id"] for item in nodes]
    if len(set(all_ids)) != len(all_ids):
        raise _fail("E_INPUT_SCHEMA", "semantic IDs are not unique")
    group_ids = {item["id"] for item in groups}
    node_ids = {item["id"] for item in nodes}
    parent_by_group = {item["id"]: item["parent_id"] for item in groups}
    for group in groups:
        parent = group["parent_id"]
        if parent is not None and parent not in group_ids:
            raise _fail("E_INPUT_SCHEMA", "group parent is unknown")
        seen = {group["id"]}
        while parent is not None:
            if parent in seen:
                raise _fail("E_INPUT_SCHEMA", "group hierarchy contains a cycle")
            seen.add(parent)
            parent = parent_by_group[parent]
    for node in nodes:
        if node["group_id"] is not None and node["group_id"] not in group_ids:
            raise _fail("E_INPUT_SCHEMA", "node group is unknown")
    for edge in edges:
        if edge["source"] not in node_ids or edge["target"] not in node_ids:
            raise _fail("E_INPUT_SCHEMA", "edge endpoint is unknown")

    claim_ids = envelope["claim_ids"]
    if (
        not isinstance(claim_ids, list)
        or any(_identifier(item, "claim_ids[]") != item for item in claim_ids)
        or claim_ids != sorted(set(claim_ids))
    ):
        raise _fail("E_INPUT_SCHEMA", "claim IDs are not sorted and unique")
    used_claims = sorted(
        [envelope["accessibility_claim_id"]]
        + [item["claim_id"] for item in groups]
        + [item["claim_id"] for item in nodes]
        + [item["claim_id"] for item in edges if item["claim_id"] is not None]
    )
    if claim_ids != used_claims:
        raise _fail("E_INPUT_SCHEMA", "claim IDs do not match semantic claims")
    return envelope


def _canonical_json(raw: bytes, code: str, context: str, maximum: int) -> dict[str, Any]:
    if not raw or len(raw) > maximum:
        raise _fail(code, f"{context} exceeds byte contract")
    try:
        text = raw.decode("utf-8")
        value = json.loads(text)
        expected = canonical_json_bytes(value)
    except (UnicodeDecodeError, json.JSONDecodeError, ContractError):
        raise _fail(code, f"{context} is invalid canonical JSON") from None
    if expected != raw or not isinstance(value, dict):
        raise _fail(code, f"{context} is not canonical")
    return value


def _regular_bytes(path: Path, maximum: int, code: str) -> bytes:
    try:
        before = path.lstat()
    except OSError:
        raise _fail(code, "adapter output is unavailable") from None
    if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > maximum:
        raise _fail(code, "adapter output path is unsafe")
    try:
        raw = path.read_bytes()
        after = path.lstat()
    except OSError:
        raise _fail(code, "adapter output cannot be read") from None
    if (
        stat.S_ISLNK(after.st_mode)
        or not stat.S_ISREG(after.st_mode)
        or before.st_dev != after.st_dev
        or before.st_ino != after.st_ino
        or before.st_size != after.st_size
    ):
        raise _fail(code, "adapter output changed during read")
    return raw


def _bounded_int(value: Any, context: str) -> None:
    if type(value) is not int or value < 0 or value > 20_000:
        raise _fail("E_OUTPUT_GEOMETRY", f"{context} is invalid")


def _geometry_object(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != fields:
        raise _fail("E_OUTPUT_GEOMETRY", f"{context} field set is invalid")
    return value


def _sorted_ids(items: Any, context: str) -> set[str]:
    if not isinstance(items, list):
        raise _fail("E_OUTPUT_GEOMETRY", f"{context} is not an array")
    ids: list[str] = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            raise _fail("E_OUTPUT_GEOMETRY", f"{context} contains an invalid ID")
        ids.append(item["id"])
    if len(ids) != len(set(ids)) or ids != sorted(ids):
        raise _fail("E_OUTPUT_GEOMETRY", f"{context} IDs are not sorted and unique")
    return set(ids)


def _validate_geometry(value: dict[str, Any], envelope: dict[str, Any], renderer_sha256: str) -> None:
    _geometry_object(value, _GEOMETRY_FIELDS, "geometry")
    if value["schema_version"] != 1:
        raise _fail("E_OUTPUT_GEOMETRY", "geometry schema version is invalid")
    engine = _geometry_object(value["engine"], _ENGINE_FIELDS, "geometry.engine")
    expected_engine = {
        "engine_kind": "elk",
        "package_name": "elkjs",
        "package_version": _ELK_VERSION,
        "package_sha256": _PACKAGE_SHA256,
        "module_sha256": _MODULE_SHA256,
        "node_version": _NODE_VERSION,
        "renderer_sha256": renderer_sha256,
    }
    if engine != expected_engine:
        raise _fail("E_ENGINE_IDENTITY", "geometry engine identity is not pinned")
    canvas = _geometry_object(value["canvas"], _CANVAS_FIELDS, "geometry.canvas")
    _bounded_int(canvas["width"], "geometry.canvas.width")
    _bounded_int(canvas["height"], "geometry.canvas.height")

    group_ids = {item["id"] for item in envelope["groups"]}
    node_ids = {item["id"] for item in envelope["nodes"]}
    actual_group_ids = _sorted_ids(value["groups"], "geometry.groups")
    actual_node_ids = _sorted_ids(value["nodes"], "geometry.nodes")
    actual_port_ids = _sorted_ids(value["ports"], "geometry.ports")
    actual_edge_ids = _sorted_ids(value["edges"], "geometry.edges")
    if actual_group_ids != group_ids or actual_node_ids != node_ids:
        raise _fail("E_OUTPUT_GEOMETRY", "geometry semantic IDs do not match input")
    group_parents = {item["id"]: item["parent_id"] for item in envelope["groups"]}
    node_parents = {item["id"]: item["group_id"] for item in envelope["nodes"]}
    for item in value["groups"]:
        _geometry_object(item, _RECT_FIELDS, "geometry.group")
        if item["parent_id"] != group_parents[item["id"]]:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry group parent changed")
        if item["parent_id"] is not None and item["parent_id"] not in group_ids:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry group parent is unknown")
        for field in ("x", "y", "width", "height"):
            _bounded_int(item[field], f"geometry.group.{field}")
    for item in value["nodes"]:
        _geometry_object(item, _RECT_FIELDS, "geometry.node")
        if item["parent_id"] != node_parents[item["id"]]:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry node parent changed")
        if item["parent_id"] is not None and item["parent_id"] not in group_ids:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry node parent is unknown")
        for field in ("x", "y", "width", "height"):
            _bounded_int(item[field], f"geometry.node.{field}")
    for item in value["ports"]:
        _geometry_object(item, _PORT_FIELDS, "geometry.port")
        if item["node_id"] not in node_ids:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry port node is unknown")
        for field in ("x", "y", "width", "height"):
            _bounded_int(item[field], f"geometry.port.{field}")
    expected_edge_ids = {f"edge-{index}" for index in range(len(envelope["edges"]))}
    if actual_edge_ids != expected_edge_ids:
        raise _fail("E_OUTPUT_GEOMETRY", "geometry edge IDs do not match input")
    for edge in value["edges"]:
        _geometry_object(edge, _EDGE_GEOMETRY_FIELDS, "geometry.edge")
        if not isinstance(edge["sections"], list) or not edge["sections"]:
            raise _fail("E_OUTPUT_GEOMETRY", "geometry edge sections are missing")
        for section in edge["sections"]:
            _geometry_object(section, _SECTION_FIELDS, "geometry.section")
            if not isinstance(section["bends"], list):
                raise _fail("E_OUTPUT_GEOMETRY", "geometry bends are not an array")
            for point in (section["start"], section["end"], *section["bends"]):
                _geometry_object(point, _POINT_FIELDS, "geometry.point")
                _bounded_int(point["x"], "geometry.point.x")
                _bounded_int(point["y"], "geometry.point.y")


def _validate_metadata(
    value: dict[str, Any], *, input_sha256: str, output_sha256: str, renderer_sha256: str
) -> None:
    _geometry_object(value, _METADATA_FIELDS, "engine metadata")
    expected = {
        "schema_version": 1,
        "engine_kind": "elk",
        "package_name": "elkjs",
        "package_version": _ELK_VERSION,
        "package_integrity": _PACKAGE_INTEGRITY,
        "package_sha256": _PACKAGE_SHA256,
        "module_sha256": _MODULE_SHA256,
        "license_spdx": _LICENSE,
        "license_sha256": _LICENSE_SHA256,
        "node_version": _NODE_VERSION,
        "renderer_sha256": renderer_sha256,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "run_hashes": [output_sha256, output_sha256],
        "validation": "pass",
        "fallback_state": "preserved",
    }
    for key, expected_value in expected.items():
        if value.get(key) != expected_value:
            raise _fail("E_ENGINE_METADATA", "engine metadata identity is not pinned")
    for key in ("platform", "architecture"):
        if not isinstance(value[key], str) or not value[key] or any(char in value[key] for char in "\r\n"):
            raise _fail("E_ENGINE_METADATA", "engine metadata runtime field is invalid")


def _skill_root() -> Path:
    # The module is shipped beneath the flattened Skill root.  Resolving from
    # the module keeps repository and installed layouts on the same path.
    return Path(__file__).resolve().parents[3]


def _read_adapter_snapshot(path: Path) -> tuple[bytes, tuple[int, int, int, int]]:
    descriptor = -1
    try:
        before = path.lstat()
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > _MAX_GEOMETRY_BYTES:
            raise OSError
        descriptor = os.open(
            os.fspath(path),
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (opened.st_dev, opened.st_ino, opened.st_size) != (before.st_dev, before.st_ino, before.st_size)
        ):
            raise OSError
        chunks: list[bytes] = []
        total = 0
        while total <= _MAX_GEOMETRY_BYTES:
            chunk = os.read(descriptor, min(64 * 1024, _MAX_GEOMETRY_BYTES + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
        after = os.fstat(descriptor)
        if total > _MAX_GEOMETRY_BYTES or after.st_size != opened.st_size or after.st_ino != opened.st_ino or after.st_dev != opened.st_dev:
            raise OSError
        raw = b"".join(chunks)
        return raw, (opened.st_dev, opened.st_ino, opened.st_size, getattr(opened, "st_mtime_ns", 0))
    except OSError:
        raise _fail("E_ENGINE_IDENTITY", "pinned ELK adapter is unavailable") from None
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _adapter_path() -> Path:
    root = _skill_root()
    adapter = root / "scripts" / "render_elk.mjs"
    # Resolution is repository-owned; bytes are snapshotted separately so the
    # same identity can be checked again after the child process exits.
    return adapter


def _verify_vendor_identity() -> None:
    root = _skill_root() / "vendor" / "elkjs"
    expected = (
        (root / "package.json", _PACKAGE_SHA256),
        (root / "lib" / "elk.bundled.js", _MODULE_SHA256),
        (root / "LICENSE.md", _LICENSE_SHA256),
    )
    try:
        observed = [(path, hashlib.sha256(path.read_bytes()).hexdigest()) for path, _ in expected]
    except OSError:
        raise _fail("E_ENGINE_IDENTITY", "pinned ELK vendor is unavailable") from None
    if any(actual != digest for (_, actual), (_, digest) in zip(observed, expected, strict=True)):
        raise _fail("E_ENGINE_IDENTITY", "pinned ELK vendor identity changed")


def _vendor_snapshots() -> tuple[tuple[str, bytes], ...]:
    root = _skill_root() / "vendor" / "elkjs"
    expected = (
        ("package.json", _PACKAGE_SHA256),
        ("lib/elk.bundled.js", _MODULE_SHA256),
        ("LICENSE.md", _LICENSE_SHA256),
    )
    try:
        observed = tuple(
            (
                relative,
                read_regular_bytes(
                    root.joinpath(*relative.split("/")),
                    maximum=_MAX_GEOMETRY_BYTES,
                    path_code="E_ENGINE_IDENTITY",
                    size_code="E_ENGINE_IDENTITY",
                ),
            )
            for relative, _ in expected
        )
    except ContractError:
        raise _fail("E_ENGINE_IDENTITY", "pinned ELK vendor is unavailable") from None
    if any(
        hashlib.sha256(raw).hexdigest() != digest
        for (_, raw), (_, digest) in zip(observed, expected, strict=True)
    ):
        raise _fail("E_ENGINE_IDENTITY", "pinned ELK vendor identity changed")
    return observed


def _attempt_directory(value: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(value)
    except TypeError:
        raise _fail("E_RUN_PATH", "run attempt directory is invalid") from None
    if not isinstance(raw, str) or not raw or "\x00" in raw:
        raise _fail("E_RUN_PATH", "run attempt directory is invalid")
    path = Path(raw)
    if not path.is_absolute():
        path = Path.cwd() / path
    if any(part == ".." for part in path.parts):
        raise _fail("E_RUN_PATH", "run attempt directory traversal is forbidden")
    # macOS exposes /tmp and /var as stable aliases.  Canonicalize only these
    # OS-owned roots; caller-owned symlinks remain reject-only below.
    for alias in ("/tmp", "/var"):
        alias_path = Path(alias)
        if path == alias_path or alias_path in path.parents:
            target = Path(os.path.realpath(alias))
            path = target / path.relative_to(alias_path)
            break
    current = Path(path.anchor)
    try:
        for part in path.parts[1:]:
            current /= part
            info = current.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise _fail("E_RUN_PATH", "run attempt directory ancestry is unsafe")
    except ContractError:
        raise
    except OSError:
        raise _fail("E_RUN_PATH", "run attempt directory is unavailable") from None
    return current


def _safe_environment(node: Path, attempt: Path) -> dict[str, str]:
    return {
        "PATH": os.fspath(node.parent),
        "LC_ALL": "C",
        "TZ": "UTC",
        "TMPDIR": os.fspath(attempt),
    }


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    try:
        if hasattr(os, "killpg") and process.pid:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass


def _run_adapter(command: list[str], *, cwd: Path, environment: Mapping[str, str]) -> tuple[int, bytes, bytes]:
    try:
        process = subprocess.Popen(
            command,
            cwd=os.fspath(cwd),
            env=dict(environment),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            shell=False,
            start_new_session=True,
        )
    except OSError:
        raise _fail("E_ENGINE_PROCESS", "pinned ELK process is unavailable") from None
    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    streams = {process.stdout: bytearray(), process.stderr: bytearray()}
    for stream in streams:
        os.set_blocking(stream.fileno(), False)
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + _TIMEOUT_SECONDS
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _kill_process(process)
                process.wait()
                raise _fail("E_ENGINE_TIMEOUT", "pinned ELK process timed out")
            events = selector.select(remaining)
            if not events:
                _kill_process(process)
                process.wait()
                raise _fail("E_ENGINE_TIMEOUT", "pinned ELK process timed out")
            for key, _ in events:
                stream = key.fileobj
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except OSError:
                    chunk = b""
                if not chunk:
                    selector.unregister(stream)
                    stream.close()
                    continue
                buffer = streams[stream]
                if len(buffer) + len(chunk) > _MAX_PROCESS_BYTES:
                    _kill_process(process)
                    process.wait()
                    raise _fail("E_ENGINE_OUTPUT_LIMIT", "pinned ELK process output exceeded limit")
                buffer.extend(chunk)
        returncode = process.wait(timeout=max(0.0, deadline - time.monotonic()))
        return returncode, bytes(streams[process.stdout]), bytes(streams[process.stderr])
    except subprocess.TimeoutExpired:
        _kill_process(process)
        process.wait()
        raise _fail("E_ENGINE_TIMEOUT", "pinned ELK process timed out") from None
    finally:
        selector.close()
        for stream in (process.stdout, process.stderr):
            if stream is not None and not stream.closed:
                stream.close()


def _map_process_failure(returncode: int, stderr: bytes) -> ContractError:
    known = {
        "E_ENGINE_TIMEOUT",
        "E_ENGINE_OUTPUT_LIMIT",
        "E_ENGINE_RUNTIME",
        "E_ENGINE_IDENTITY",
        "E_ENGINE_LICENSE",
        "E_ENGINE_IMPORT",
        "E_ENGINE_RENDER",
        "E_ENGINE_PROCESS",
        "E_ENGINE_NONDETERMINISTIC",
        "E_OUTPUT_GEOMETRY",
        "E_INPUT_SCHEMA",
        "E_RUN_PATH",
    }
    try:
        text = stderr.decode("ascii", errors="ignore")
    except Exception:
        text = ""
    match = _ERROR_CODE.search(text)
    code = match.group(1) if match and match.group(1) in known else "E_ENGINE_PROCESS"
    if code == "E_ENGINE_RUNTIME" and returncode != 0:
        return _fail(code, "pinned Node runtime is unavailable")
    return _fail(code, "pinned ELK adapter failed")


@dataclass(frozen=True, slots=True)
class ElkGeometryResult:
    """Immutable canonical geometry plus its verified engine metadata."""

    geometry: Mapping[str, Any]
    metadata: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "geometry", _freeze(_thaw(self.geometry)))
        object.__setattr__(self, "metadata", _freeze(_thaw(self.metadata)))

    @property
    def identity(self) -> Mapping[str, Any]:
        return self.geometry["engine"]

    def as_dict(self) -> dict[str, Any]:
        return {"geometry": _thaw(self.geometry), "metadata": _thaw(self.metadata)}

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())


def render_elk_geometry(envelope: Mapping[str, Any], attempt_dir: os.PathLike[str] | str) -> ElkGeometryResult:
    """Run pinned repository ELK geometry mode inside ``attempt_dir``."""

    semantic = _validate_envelope(envelope)
    try:
        input_raw = canonical_json_bytes(semantic)
    except ContractError:
        raise _fail("E_INPUT_SCHEMA", "semantic envelope cannot be canonicalized") from None
    if len(input_raw) > _MAX_INPUT_BYTES:
        raise _fail("E_INPUT_SCHEMA", "semantic envelope exceeds byte contract")
    attempt = _attempt_directory(attempt_dir)
    adapter = _adapter_path()
    renderer_raw, adapter_identity = _read_adapter_snapshot(adapter)
    renderer_sha256 = hashlib.sha256(renderer_raw).hexdigest()
    vendor_snapshots = _vendor_snapshots()
    node_raw = shutil.which("node")
    if not node_raw:
        raise _fail("E_ENGINE_RUNTIME", "pinned Node runtime is unavailable")
    try:
        node = Path(node_raw).resolve(strict=True)
        info = node.stat()
        if not stat.S_ISREG(info.st_mode) or not os.access(node, os.X_OK):
            raise OSError
    except OSError:
        raise _fail("E_ENGINE_RUNTIME", "pinned Node runtime is unavailable") from None

    with tempfile.TemporaryDirectory(prefix=".elk-", dir=os.fspath(attempt)) as temporary:
        work = Path(temporary)
        input_path = work / "input.json"
        geometry_path = work / "geometry.json"
        metadata_path = work / "metadata.json"
        execution_root = work / "skill"
        snapshot_adapter = execution_root / "scripts/render_elk.mjs"
        try:
            input_path.write_bytes(input_raw)
            snapshot_adapter.parent.mkdir(parents=True)
            snapshot_adapter.write_bytes(renderer_raw)
            for relative, raw in vendor_snapshots:
                destination = execution_root / "vendor/elkjs" / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
        except OSError:
            raise _fail("E_RUN_PATH", "cannot create ELK attempt input") from None
        command = [
            os.fspath(node),
            os.fspath(snapshot_adapter),
            "--input",
            os.fspath(input_path),
            "--geometry",
            os.fspath(geometry_path),
            "--metadata",
            os.fspath(metadata_path),
        ]
        returncode, _stdout, stderr = _run_adapter(
            command,
            cwd=attempt,
            environment=_safe_environment(node, attempt),
        )
        if returncode != 0:
            raise _map_process_failure(returncode, stderr)
        after_raw, after_identity = _read_adapter_snapshot(adapter)
        if after_identity != adapter_identity or hashlib.sha256(after_raw).hexdigest() != renderer_sha256:
            raise _fail("E_ENGINE_IDENTITY", "pinned ELK adapter changed during execution")
        geometry_raw = _regular_bytes(geometry_path, _MAX_GEOMETRY_BYTES, "E_OUTPUT_GEOMETRY")
        geometry = _canonical_json(geometry_raw, "E_OUTPUT_GEOMETRY", "geometry", _MAX_GEOMETRY_BYTES)
        _validate_geometry(geometry, semantic, renderer_sha256)
        metadata_raw = _regular_bytes(metadata_path, _MAX_METADATA_BYTES, "E_ENGINE_METADATA")
        metadata = _canonical_json(metadata_raw, "E_ENGINE_METADATA", "engine metadata", _MAX_METADATA_BYTES)
        _validate_metadata(
            metadata,
            input_sha256=hashlib.sha256(input_raw).hexdigest(),
            output_sha256=hashlib.sha256(geometry_raw).hexdigest(),
            renderer_sha256=renderer_sha256,
        )
        return ElkGeometryResult(geometry, metadata)


__all__ = ["ElkGeometryResult", "render_elk_geometry"]
