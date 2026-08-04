"""Bounded stage-6 artifact assembly for the visual kernel.

The builder is deliberately filesystem-free.  It returns one immutable map of
safe relative paths to canonical bytes; promotion is the only operation that
hands that map to the centralized :class:`RunWorkspace` writer.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from collections.abc import Iterable, Mapping
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes
from ..contracts.locale import parse_locale
from ..orchestration.workspace import RunWorkspace
from .scene import validate_visual_scene
from .security import (
    MAX_COMPILED_BYTES,
    MAX_GATE_BYTES,
    MAX_INTERACTION_BYTES,
    MAX_SCENE_BYTES,
    MAX_SVG_BYTES,
    MAX_TIMELINE_BYTES,
    MAX_VISUAL_SPEC_BYTES,
    validate_visual_security,
)


_ARTIFACT_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VARIANTS = frozenset({"desktop", "mobile"})
_RECORD_FIELDS = frozenset({"locale", "variant", "scene", "svg", "gate", "timeline", "interaction"})
_IDENTITY_FIELDS = frozenset({"kernel", "elk", "renderer"})
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


def _bytes(value: Any, context: str) -> bytes:
    if type(value) is not bytes:
        raise _fail("E_SCHEMA_TYPE", f"{context} must be bytes")
    return value


def _safe_path(value: Any, context: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != unicodedata.normalize("NFC", value)
        or "\x00" in value
        or "\\" in value
        or _SCHEME.match(value) is not None
    ):
        raise _fail("E_VISUAL_PATH", f"{context} must be a safe relative POSIX path")
    path = PurePosixPath(value)
    if value.startswith(("/", "~/")) or path.is_absolute() or value != path.as_posix():
        raise _fail("E_VISUAL_PATH", f"{context} must be a safe relative POSIX path")
    if any(part in {"", ".", ".."} for part in path.parts):
        raise _fail("E_VISUAL_PATH", f"{context} must be a safe relative POSIX path")
    return value


def _locale(value: Any, context: str) -> str:
    if not isinstance(value, str):
        raise _fail("E_SCHEMA_TYPE", f"{context} must be a locale tag")
    return parse_locale(value, context)


def _variant(value: Any, context: str) -> str:
    if not isinstance(value, str) or value not in _VARIANTS:
        raise _fail("E_SCHEMA_VALUE", f"{context} must be desktop or mobile")
    return value


def _identities(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping):
        raise _fail("E_SCHEMA_TYPE", "identities must be an object")
    if set(value) != _IDENTITY_FIELDS or any(not isinstance(key, str) for key in value):
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", "identities must contain exactly kernel, elk, and renderer")
    result: dict[str, str] = {}
    for key in sorted(_IDENTITY_FIELDS):
        identity = value[key]
        if not isinstance(identity, str) or _SHA256.fullmatch(identity) is None:
            raise _fail("E_VISUAL_FINGERPRINT", f"identities.{key} must be a lowercase SHA-256 digest")
        result[key] = identity
    return result


def _records(value: Any) -> tuple[tuple[str, str, Mapping[str, Any]], ...]:
    if isinstance(value, (str, bytes, bytearray, Mapping)):
        raise _fail("E_SCHEMA_TYPE", "variants must be an iterable of closed records")
    try:
        iterator = iter(value)
    except TypeError as exc:
        raise _fail("E_SCHEMA_TYPE", "variants must be an iterable of closed records") from exc

    result: list[tuple[str, str, Mapping[str, Any]]] = []
    seen: set[tuple[str, str]] = set()
    for index, item in enumerate(iterator):
        if not isinstance(item, Mapping):
            raise _fail("E_SCHEMA_TYPE", f"variants[{index}] must be an object")
        if set(item) != _RECORD_FIELDS or any(not isinstance(key, str) for key in item):
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"variants[{index}] must contain exactly the compiled artifact fields")
        locale = _locale(item["locale"], f"variants[{index}].locale")
        variant = _variant(item["variant"], f"variants[{index}].variant")
        key = (locale, variant)
        if key in seen:
            raise _fail("E_VISUAL_SPEC_ID", f"variants contains duplicate locale/variant: {locale}/{variant}")
        seen.add(key)
        result.append((locale, variant, item))
    if not result:
        raise _fail("E_SCHEMA_VALUE", "variants must contain one or more locale/variant records")
    result.sort(key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")))
    return tuple(result)


def _scene_locale_variant(raw: bytes, locale: str, variant: str) -> None:
    try:
        scene = validate_visual_scene(json.loads(raw.decode("utf-8")))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _fail("E_VISUAL_RESOURCE", "scene canonical bytes cannot be decoded") from exc
    if scene.locale != locale or scene.variant != variant:
        raise _fail("E_VISUAL_FINGERPRINT", "scene locale/variant differs from its artifact path")


def _artifact_entry(
    path: str,
    kind: str,
    data: bytes,
    *,
    locale: str | None,
    variant: str | None,
) -> dict[str, Any]:
    return {
        "path": path,
        "type": kind,
        "sha256": hashlib.sha256(data).hexdigest(),
        "size": len(data),
        "locale": locale,
        "variant": variant,
    }


def _path_kind_limit(path: str) -> tuple[str, int]:
    if path == "compiled/visual-spec.json":
        return "visual-spec", MAX_VISUAL_SPEC_BYTES
    if path == "compiled/theme.json":
        return "theme", MAX_COMPILED_BYTES
    if path == "compiled/inventory.json":
        return "inventory", MAX_COMPILED_BYTES
    parts = path.split("/")
    if len(parts) == 4 and parts[:2] == ["compiled", "scenes"] and parts[3].endswith(".json"):
        _locale(parts[2], "compiled scene locale")
        _variant(parts[3][:-5], "compiled scene variant")
        return "scene", MAX_SCENE_BYTES
    if len(parts) == 4 and parts[:2] == ["compiled", "gates"] and parts[3].endswith(".json"):
        _locale(parts[2], "compiled gate locale")
        _variant(parts[3][:-5], "compiled gate variant")
        return "gate", MAX_GATE_BYTES
    if len(parts) == 4 and parts[:2] == ["compiled", "timeline"] and parts[3].endswith(".json"):
        _locale(parts[2], "compiled timeline locale")
        _variant(parts[3][:-5], "compiled timeline variant")
        return "timeline", MAX_TIMELINE_BYTES
    if len(parts) == 4 and parts[:2] == ["compiled", "interaction"] and parts[3].endswith(".json"):
        _locale(parts[2], "compiled interaction locale")
        _variant(parts[3][:-5], "compiled interaction variant")
        return "interaction", MAX_INTERACTION_BYTES
    if len(parts) == 4 and parts[:2] == ["assets", "readme-showcase"] and parts[3].endswith(".svg"):
        _locale(parts[2], "compiled SVG locale")
        _variant(parts[3][:-4], "compiled SVG variant")
        return "svg", MAX_SVG_BYTES
    raise _fail("E_VISUAL_PATH", f"unsupported compiled artifact path: {path}")


def _validate_inventory(files: Mapping[str, bytes]) -> None:
    raw = files["compiled/inventory.json"]
    try:
        inventory = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _fail("E_VISUAL_DETERMINISM", "compiled inventory must be canonical JSON") from exc
    if not isinstance(inventory, dict) or set(inventory) != {"schema_version", "identities", "artifacts"}:
        raise _fail("E_SCHEMA_UNKNOWN_FIELD", "compiled inventory has an unsupported shape")
    if inventory["schema_version"] != _ARTIFACT_SCHEMA_VERSION or canonical_json_bytes(inventory) != raw:
        raise _fail("E_VISUAL_DETERMINISM", "compiled inventory must be canonical schema v1 bytes")
    _identities(inventory["identities"])
    records = inventory["artifacts"]
    if not isinstance(records, list):
        raise _fail("E_SCHEMA_TYPE", "compiled inventory.artifacts must be an array")
    seen: set[str] = set()
    expected_paths = sorted((path for path in files if path != "compiled/inventory.json"), key=lambda item: item.encode("utf-8"))
    observed_paths: list[str] = []
    for index, record in enumerate(records):
        if not isinstance(record, Mapping) or set(record) != {"path", "type", "sha256", "size", "locale", "variant"}:
            raise _fail("E_SCHEMA_UNKNOWN_FIELD", f"compiled inventory artifact {index} is not closed")
        path = _safe_path(record["path"], f"compiled inventory artifact {index}.path")
        kind, _ = _path_kind_limit(path)
        if path in seen:
            raise _fail("E_VISUAL_PATH", f"compiled inventory contains duplicate path: {path}")
        seen.add(path)
        observed_paths.append(path)
        if record["type"] != kind or record["sha256"] != hashlib.sha256(files.get(path, b"")).hexdigest():
            raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory hash/type drift at {path}")
        if path not in files or record["size"] != len(files[path]):
            raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory size/path drift at {path}")
        locale = record["locale"]
        variant = record["variant"]
        if kind in {"visual-spec", "theme"}:
            if locale is not None or variant is not None:
                raise _fail("E_SCHEMA_VALUE", f"compiled inventory metadata drift at {path}")
        else:
            if _locale(locale, f"compiled inventory {path}.locale") != path.split("/")[2] or _variant(variant, f"compiled inventory {path}.variant") != path.split("/")[-1].rsplit(".", 1)[0]:
                raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory locale/variant drift at {path}")
    if observed_paths != expected_paths:
        raise _fail("E_VISUAL_FINGERPRINT", "compiled inventory does not close over the artifact set")


def _preflight_files(files: Mapping[str, bytes], *, require_inventory: bool = False) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or not files:
        raise _fail("E_SCHEMA_TYPE", "compiled artifact set must be a non-empty mapping")
    normalized: dict[str, bytes] = {}
    for raw_path, raw_data in files.items():
        path = _safe_path(raw_path, "compiled artifact path")
        if path in normalized:
            raise _fail("E_VISUAL_PATH", f"duplicate compiled artifact path: {path}")
        data = _bytes(raw_data, f"compiled artifact {path}")
        _, maximum = _path_kind_limit(path)
        if len(data) > maximum:
            raise _fail("E_VISUAL_RESOURCE", f"compiled artifact {path} exceeds its byte limit")
        normalized[path] = data
    if require_inventory and "compiled/inventory.json" not in normalized:
        raise _fail("E_SCHEMA_MISSING_FIELD", "compiled artifact set must contain compiled/inventory.json")
    if sum(len(data) for data in normalized.values()) > MAX_COMPILED_BYTES:
        raise _fail("E_VISUAL_RESOURCE", "compiled artifact set exceeds the 16 MiB aggregate limit")
    if require_inventory:
        _validate_inventory(normalized)
    return {path: normalized[path] for path in sorted(normalized, key=lambda item: item.encode("utf-8"))}


def build_compiled_artifacts(
    spec: Any,
    theme: Any,
    variants: Iterable[Mapping[str, Any]],
    identities: Mapping[str, str],
    *,
    evidence_graph: Mapping[str, Any] | None = None,
) -> Mapping[str, bytes]:
    """Build the complete stage-6 artifact map without touching the filesystem."""

    identity_values = _identities(identities)
    records = _records(variants)
    base = validate_visual_security(spec=spec, theme=theme, evidence_graph=evidence_graph)
    spec_bytes = base["spec"]
    theme_bytes = base["theme"]
    files: dict[str, bytes] = {
        "compiled/visual-spec.json": spec_bytes,
        "compiled/theme.json": theme_bytes,
    }
    entries = [
        _artifact_entry("compiled/visual-spec.json", "visual-spec", spec_bytes, locale=None, variant=None),
        _artifact_entry("compiled/theme.json", "theme", theme_bytes, locale=None, variant=None),
    ]
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()

    for locale, variant, record in records:
        canonical = validate_visual_security(
            scene=record["scene"],
            gate=record["gate"],
            timeline=record["timeline"],
            interaction=record["interaction"],
            svg=record["svg"],
        )
        scene_bytes = canonical["scene"]
        gate_bytes = canonical["gate"]
        timeline_bytes = canonical["timeline"]
        interaction_bytes = canonical["interaction"]
        svg_bytes = canonical["svg"]
        _scene_locale_variant(scene_bytes, locale, variant)
        try:
            gate = json.loads(gate_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
            raise _fail("E_VISUAL_RESOURCE", "gate canonical bytes cannot be decoded") from exc
        if (
            gate["spec_sha256"] != spec_sha256
            or gate["scene_sha256"] != hashlib.sha256(scene_bytes).hexdigest()
            or gate["svg_sha256"] != hashlib.sha256(svg_bytes).hexdigest()
        ):
            raise _fail("E_VISUAL_FINGERPRINT", f"gate hashes do not bind {locale}/{variant}")

        variant_paths = (
            (f"compiled/scenes/{locale}/{variant}.json", "scene", scene_bytes),
            (f"compiled/gates/{locale}/{variant}.json", "gate", gate_bytes),
            (f"compiled/timeline/{locale}/{variant}.json", "timeline", timeline_bytes),
            (f"compiled/interaction/{locale}/{variant}.json", "interaction", interaction_bytes),
            (f"assets/readme-showcase/{locale}/{variant}.svg", "svg", svg_bytes),
        )
        for path, kind, data in variant_paths:
            if path in files:
                raise _fail("E_VISUAL_PATH", f"duplicate compiled artifact path: {path}")
            files[path] = data
            entries.append(_artifact_entry(path, kind, data, locale=locale, variant=variant))

    entries.sort(key=lambda item: item["path"].encode("utf-8"))
    inventory = canonical_json_bytes(
        {
            "schema_version": _ARTIFACT_SCHEMA_VERSION,
            "identities": identity_values,
            "artifacts": entries,
        }
    )
    files["compiled/inventory.json"] = inventory
    return MappingProxyType(_preflight_files(files, require_inventory=True))


def promote_compiled_artifacts(
    workspace: RunWorkspace,
    artifacts: Mapping[str, bytes],
    *,
    attempt: int | None = None,
) -> Path:
    """Atomically append one complete compiled set to stage 6."""

    if not isinstance(workspace, RunWorkspace):
        raise _fail("E_SCHEMA_TYPE", "promotion requires a RunWorkspace")
    files = _preflight_files(artifacts, require_inventory=True)
    return workspace.append_attempt(6, "bundle-assemble", files, attempt=attempt)


__all__ = ["build_compiled_artifacts", "promote_compiled_artifacts"]
