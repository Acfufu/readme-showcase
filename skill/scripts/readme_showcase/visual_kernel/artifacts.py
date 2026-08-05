"""Bounded stage-6 artifact assembly for the visual kernel.

The builder is filesystem-free.  It returns one immutable map of safe
relative paths to canonical bytes; promotion is the only operation that hands
that map to the centralized :class:`RunWorkspace` writer.
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
from .fingerprint import FINGERPRINT_SCHEMA_VERSION, build_layered_fingerprint
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


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VARIANTS = frozenset({"desktop", "mobile"})
_RECORD_FIELDS = frozenset({"locale", "variant", "scene", "svg", "gate", "timeline", "interaction"})
_IDENTITY_FIELDS = frozenset({"kernel", "elk", "renderer"})
_SCHEME = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*:")
_LAYER_NAMES = ("spec", "scenes", "theme", "identities", "gates", "timelines", "interactions", "artifacts")


def _fail(code: str, message: str) -> ContractError:
    return ContractError(code, message)


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


def _report_prior(
    gates: Iterable[Mapping[str, Any]],
    timelines: Iterable[Mapping[str, Any]],
    interactions: Iterable[Mapping[str, Any]],
) -> str:
    projection = {
        "gates": list(gates),
        "timelines": list(timelines),
        "interactions": list(interactions),
    }
    return hashlib.sha256(canonical_json_bytes(projection)).hexdigest()


def _fingerprint_from_inventory(raw: Any, context: str):
    if not isinstance(raw, Mapping) or set(raw) != {"schema_version", "layers", "inventory_sha256"}:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context} must be a closed LayeredFingerprint object")
    if raw["schema_version"] != FINGERPRINT_SCHEMA_VERSION:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context} requires schema_version 1")
    layers = raw["layers"]
    if not isinstance(layers, list) or len(layers) != len(_LAYER_NAMES):
        raise _fail("E_VISUAL_FINGERPRINT", f"{context}.layers must contain the eight ordered layers")
    if any(not isinstance(layer, Mapping) or not isinstance(layer.get("name"), str) for layer in layers):
        raise _fail("E_VISUAL_FINGERPRINT", f"{context}.layers must contain named objects")
    if tuple(layer["name"] for layer in layers) != _LAYER_NAMES:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context}.layers are not in canonical order")
    try:
        spec = layers[0]["sha256"]
        scenes = layers[1]["records"]
        theme = layers[2]["sha256"]
        identities = layers[3]["values"]
        gates = layers[4]["records"]
        timelines = layers[5]["records"]
        interactions = layers[6]["records"]
        artifacts = layers[7]["records"]
    except (KeyError, TypeError) as exc:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context} is missing a layer projection") from exc
    fingerprint = build_layered_fingerprint(spec, scenes, theme, identities, gates, timelines, interactions, artifacts)
    if fingerprint.inventory_sha256 != raw["inventory_sha256"]:
        raise _fail("E_VISUAL_FINGERPRINT", f"{context}.inventory_sha256 does not match its projection")
    return fingerprint


def _validate_inventory(files: Mapping[str, bytes]) -> None:
    raw_bytes = files["compiled/inventory.json"]
    try:
        raw = json.loads(raw_bytes.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise _fail("E_VISUAL_DETERMINISM", "compiled inventory must be canonical JSON") from exc
    fingerprint = _fingerprint_from_inventory(raw, "compiled inventory")
    if fingerprint.canonical_bytes() != raw_bytes:
        raise _fail("E_VISUAL_DETERMINISM", "compiled inventory must use LayeredFingerprint canonical bytes")
    required_paths = {"compiled/visual-spec.json", "compiled/theme.json"}
    for locale, variant, _, _ in fingerprint.scenes:
        required_paths.update(
            {
                f"compiled/scenes/{locale}/{variant}.json",
                f"compiled/gates/{locale}/{variant}.json",
                f"compiled/timeline/{locale}/{variant}.json",
                f"compiled/interaction/{locale}/{variant}.json",
                f"assets/readme-showcase/{locale}/{variant}.svg",
            }
        )
    file_paths = set(files) - {"compiled/inventory.json"}
    observed_paths = [path for path, _, _ in fingerprint.artifacts]
    if file_paths != required_paths or set(observed_paths) != required_paths:
        raise _fail("E_VISUAL_FINGERPRINT", "compiled inventory does not close over the artifact set")
    if fingerprint.spec_sha256 != hashlib.sha256(files["compiled/visual-spec.json"]).hexdigest():
        raise _fail("E_VISUAL_FINGERPRINT", "compiled inventory spec layer does not bind visual-spec.json")
    if fingerprint.theme_sha256 != hashlib.sha256(files["compiled/theme.json"]).hexdigest():
        raise _fail("E_VISUAL_FINGERPRINT", "compiled inventory theme layer does not bind theme.json")
    for locale, variant, digest, _ in fingerprint.scenes:
        path = f"compiled/scenes/{locale}/{variant}.json"
        if path not in files or hashlib.sha256(files[path]).hexdigest() != digest:
            raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory scene layer drift at {path}")
    for name, records, directory in (
        ("gate", fingerprint.gates, "gates"),
        ("timeline", fingerprint.timelines, "timeline"),
        ("interaction", fingerprint.interactions, "interaction"),
    ):
        for locale, variant, digest, _ in records:
            path = f"compiled/{directory}/{locale}/{variant}.json"
            if path not in files or hashlib.sha256(files[path]).hexdigest() != digest:
                raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory {name} layer drift at {path}")
    for path, digest, _ in fingerprint.artifacts:
        if hashlib.sha256(files[path]).hexdigest() != digest:
            raise _fail("E_VISUAL_FINGERPRINT", f"compiled inventory hash drift at {path}")


def _validate_artifact_semantics(files: Mapping[str, bytes]) -> None:
    """Revalidate inventory-bound bytes at every authoritative boundary."""

    for path, raw in files.items():
        kind, _ = _path_kind_limit(path)
        if kind == "inventory":
            continue
        validate_visual_security(**{"spec" if kind == "visual-spec" else kind: raw})


def _preflight_files(files: Mapping[str, bytes], *, require_inventory: bool = False) -> dict[str, bytes]:
    if not isinstance(files, Mapping) or not files:
        raise _fail("E_SCHEMA_TYPE", "compiled artifact set must be a non-empty mapping")
    normalized: dict[str, bytes] = {}
    for raw_path, raw_data in files.items():
        path = _safe_path(raw_path, "compiled artifact path")
        if path in normalized:
            raise _fail("E_VISUAL_PATH", f"duplicate compiled artifact path: {path}")
        if type(raw_data) is not bytes:
            raise _fail("E_SCHEMA_TYPE", f"compiled artifact {path} must be bytes")
        _, maximum = _path_kind_limit(path)
        if len(raw_data) > maximum:
            raise _fail("E_VISUAL_RESOURCE", f"compiled artifact {path} exceeds its byte limit")
        normalized[path] = raw_data
    if require_inventory and "compiled/inventory.json" not in normalized:
        raise _fail("E_SCHEMA_MISSING_FIELD", "compiled artifact set must contain compiled/inventory.json")
    if sum(len(data) for data in normalized.values()) > MAX_COMPILED_BYTES:
        raise _fail("E_VISUAL_RESOURCE", "compiled artifact set exceeds the 16 MiB aggregate limit")
    if require_inventory:
        _validate_inventory(normalized)
        _validate_artifact_semantics(normalized)
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
    spec_sha256 = hashlib.sha256(spec_bytes).hexdigest()
    scene_records: list[dict[str, str]] = []
    gate_records: list[dict[str, str]] = []
    timeline_records: list[dict[str, str]] = []
    interaction_records: list[dict[str, str]] = []
    prepared: list[tuple[str, str, bytes, bytes, bytes, bytes, bytes]] = []

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
        scene_sha256 = hashlib.sha256(scene_bytes).hexdigest()
        svg_sha256 = hashlib.sha256(svg_bytes).hexdigest()
        if gate["spec_sha256"] != spec_sha256 or gate["scene_sha256"] != scene_sha256 or gate["svg_sha256"] != svg_sha256:
            raise _fail("E_VISUAL_FINGERPRINT", f"gate hashes do not bind {locale}/{variant}")
        gate_sha256 = hashlib.sha256(gate_bytes).hexdigest()
        timeline_sha256 = hashlib.sha256(timeline_bytes).hexdigest()
        interaction_sha256 = hashlib.sha256(interaction_bytes).hexdigest()
        scene_records.append({"locale": locale, "variant": variant, "sha256": scene_sha256, "prior_sha256": spec_sha256})
        gate_records.append({"locale": locale, "variant": variant, "sha256": gate_sha256, "prior_sha256": scene_sha256})
        timeline_records.append({"locale": locale, "variant": variant, "sha256": timeline_sha256, "prior_sha256": gate_sha256})
        interaction_records.append({"locale": locale, "variant": variant, "sha256": interaction_sha256, "prior_sha256": timeline_sha256})
        prepared.append((locale, variant, scene_bytes, gate_bytes, timeline_bytes, interaction_bytes, svg_bytes))

    report_prior = _report_prior(gate_records, timeline_records, interaction_records)
    files.update(
        {
            f"compiled/scenes/{locale}/{variant}.json": scene_bytes
            for locale, variant, scene_bytes, _, _, _, _ in prepared
        }
    )
    files.update(
        {
            f"compiled/gates/{locale}/{variant}.json": gate_bytes
            for locale, variant, _, gate_bytes, _, _, _ in prepared
        }
    )
    files.update(
        {
            f"compiled/timeline/{locale}/{variant}.json": timeline_bytes
            for locale, variant, _, _, timeline_bytes, _, _ in prepared
        }
    )
    files.update(
        {
            f"compiled/interaction/{locale}/{variant}.json": interaction_bytes
            for locale, variant, _, _, _, interaction_bytes, _ in prepared
        }
    )
    files.update(
        {
            f"assets/readme-showcase/{locale}/{variant}.svg": svg_bytes
            for locale, variant, _, _, _, _, svg_bytes in prepared
        }
    )
    artifacts = [
        {"path": path, "sha256": hashlib.sha256(data).hexdigest(), "prior_sha256": report_prior}
        for path, data in files.items()
    ]
    fingerprint = build_layered_fingerprint(
        spec_sha256,
        scene_records,
        hashlib.sha256(theme_bytes).hexdigest(),
        identity_values,
        gate_records,
        timeline_records,
        interaction_records,
        artifacts,
    )
    files["compiled/inventory.json"] = fingerprint.canonical_bytes()
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
