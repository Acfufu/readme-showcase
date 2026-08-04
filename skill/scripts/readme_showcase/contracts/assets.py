from __future__ import annotations

import copy
import hashlib
import json
import re
from pathlib import Path
from typing import Any, Mapping, Sequence

from ...pipeline_contracts import ContractError, canonical_json_bytes
from .common import MAX_SOURCE_BYTES, normalize_posix_path, normalize_text, read_source_bytes
from .evidence import validate_evidence_graph
from .locale import parse_locale
from ..visual_kernel.fingerprint import build_layered_fingerprint


ASSET_MANIFEST_SCHEMA_VERSION = 2
ASSET_MANIFEST_V3_SCHEMA_VERSION = 3
MAX_ASSETS = 10_000
PROVENANCE_KINDS = frozenset({"hand-authored", "derived", "generated"})
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_EVIDENCE_ID = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")
_ASSET_FIELDS = {
    "asset_id", "path", "locale", "language_neutral", "provenance", "artifact_sha256",
    "candidate_sha256", "evidence_ids",
}
_PROVENANCE_FIELDS = {"kind", "path", "sha256"}
_V3_COMPILED_FIELDS = {
    "spec", "theme", "inventory", "scenes", "gates", "timelines", "interactions", "svgs", "identities",
}
_V3_REF_FIELDS = {"path", "sha256"}
_V3_VARIANT_REF_FIELDS = {"locale", "variant", "path", "sha256"}
_V3_ASSET_REQUIRED_FIELDS = {
    "asset_id", "path", "artifact_sha256", "evidence_ids", "role", "locale", "variant", "scene_sha256", "gate_sha256",
}
_V3_ASSET_OPTIONAL_FIELDS = {"provenance"}
_V3_ASSET_ROLES = frozenset({"diagram"})
_V3_VARIANT_COLLECTIONS = {
    "scenes": "compiled/scenes/{locale}/{variant}.json",
    "gates": "compiled/gates/{locale}/{variant}.json",
    "timelines": "compiled/timeline/{locale}/{variant}.json",
    "interactions": "compiled/interaction/{locale}/{variant}.json",
    "svgs": "assets/readme-showcase/{locale}/{variant}.svg",
}
_V3_SINGLE_REFS = {
    "spec": "compiled/visual-spec.json",
    "theme": "compiled/theme.json",
    "inventory": "compiled/inventory.json",
}
_V3_LAYER_NAMES = ("spec", "scenes", "theme", "identities", "gates", "timelines", "interactions", "artifacts")


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, child in enumerate(value):
            _reject_float(child, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, child in value.items():
            _reject_float(child, f"{path}.{key}")


def _closed(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return value


def _sha(value: Any, context: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise ContractError("E_BUNDLE_HASH", f"{context} must be lowercase SHA-256")
    return value


def _path(value: Any, context: str) -> str:
    try:
        return normalize_posix_path(value)
    except ValueError as exc:
        raise ContractError("E_PATH", f"{context} must be safe relative POSIX path") from exc


def _ids(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or not value:
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} requires one or more evidence IDs")
    result = [normalize_text(item, f"{context}[]", maximum=512) for item in value]
    if any(not _EVIDENCE_ID.fullmatch(item) for item in result):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} must contain normative Evidence v2 IDs")
    if len(result) != len(set(result)):
        raise ContractError("E_CLAIM_EVIDENCE", f"{context} contains duplicate evidence IDs")
    return result


def _safe_read(root: Path, path: str, context: str) -> bytes:
    try:
        return read_source_bytes(root, path, maximum=MAX_SOURCE_BYTES)
    except ValueError as exc:
        if getattr(exc, "code", None) in {"E_EVIDENCE_PATH", "E_INPUT_PATH", "E_INPUT_NOT_FOUND"}:
            raise ContractError("E_PATH", f"{context} must be a regular file below artifact root") from exc
        raise


def _validate_asset_manifest_v2(
    payload: Any,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
    artifact_root: Path | None = None,
    candidate_assets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    _reject_float(payload)
    manifest = _closed(payload, {"schema_version", "assets"}, "asset manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != ASSET_MANIFEST_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "asset manifest requires schema_version 2")
    raw_assets = manifest["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_ASSETS:
        raise ContractError("E_SCHEMA_TYPE", f"asset manifest.assets must contain at most {MAX_ASSETS} entries")

    facts: dict[str, dict[str, Any]] | None = None
    if evidence_graph is not None:
        graph = validate_evidence_graph(dict(evidence_graph))
        facts = {fact["fact_id"]: fact for fact in graph["facts"]}
    candidates: dict[str, str] | None = None
    if candidate_assets is not None:
        candidates = {}
        for index, reference in enumerate(candidate_assets):
            ref = _closed(dict(reference), {"path", "sha256"}, f"candidate.assets[{index}]")
            path = _path(ref["path"], f"candidate.assets[{index}].path")
            if path in candidates:
                raise ContractError("E_BUNDLE_ASSET", "candidate assets contain duplicate path")
            candidates[path] = _sha(ref["sha256"], f"candidate.assets[{index}].sha256")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, raw in enumerate(raw_assets):
        context = f"asset manifest.assets[{index}]"
        if not isinstance(raw, dict):
            raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
        unknown = sorted(set(raw) - _ASSET_FIELDS)
        required = _ASSET_FIELDS - {"locale"}
        missing = sorted(required - set(raw))
        if unknown:
            raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
        if missing:
            raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
        asset = raw
        asset_id = normalize_text(asset["asset_id"], f"{context}.asset_id", maximum=512)
        path = _path(asset["path"], f"{context}.path")
        if asset_id in seen_ids or path in seen_paths:
            raise ContractError("E_BUNDLE_ASSET", f"{context} duplicates asset identity or path")
        seen_ids.add(asset_id)
        seen_paths.add(path)
        neutral = asset["language_neutral"]
        if type(neutral) is not bool:
            raise ContractError("E_ASSET_LOCALE", f"{context}.language_neutral must be boolean")
        has_locale = "locale" in asset
        if neutral == has_locale:
            raise ContractError("E_ASSET_LOCALE", f"{context} must declare exactly localized or language-neutral metadata")
        locale = None if neutral else parse_locale(asset["locale"], f"{context}.locale")
        provenance = _closed(asset["provenance"], _PROVENANCE_FIELDS, f"{context}.provenance")
        kind = provenance["kind"]
        if kind not in PROVENANCE_KINDS:
            raise ContractError("E_BUNDLE_ASSET", f"{context}.provenance.kind is unsupported")
        source_path = _path(provenance["path"], f"{context}.provenance.path")
        if source_path == path:
            raise ContractError("E_BUNDLE_HASH", f"{context} cannot use candidate bytes as provenance")
        source_hash = _sha(provenance["sha256"], f"{context}.provenance.sha256")
        artifact_hash = _sha(asset["artifact_sha256"], f"{context}.artifact_sha256")
        candidate_hash = _sha(asset["candidate_sha256"], f"{context}.candidate_sha256")
        if artifact_hash != candidate_hash:
            raise ContractError("E_BUNDLE_HASH", f"{context} candidate and artifact hashes differ")
        identifiers = _ids(asset["evidence_ids"], f"{context}.evidence_ids")
        if facts is not None:
            if not set(identifiers).issubset(facts):
                raise ContractError("E_CLAIM_EVIDENCE", f"{context} references missing evidence")
            source_facts = [
                facts[identifier]
                for identifier in identifiers
                if facts[identifier]["source"]["path"] == source_path
                and facts[identifier]["source_sha256"] == source_hash
            ]
            if not source_facts:
                raise ContractError("E_BUNDLE_HASH", f"{context} provenance is not bound to normative evidence")
        if candidates is not None and candidates.get(path) != candidate_hash:
            raise ContractError("E_BUNDLE_HASH", f"{context} differs from candidate reference")
        if artifact_root is not None:
            if hashlib.sha256(_safe_read(artifact_root, source_path, f"{context}.provenance")).hexdigest() != source_hash:
                raise ContractError("E_BUNDLE_HASH", f"{context} provenance bytes changed")
            if hashlib.sha256(_safe_read(artifact_root, path, context)).hexdigest() != artifact_hash:
                raise ContractError("E_BUNDLE_HASH", f"{context} artifact bytes changed")
        normalized_asset = {
            "asset_id": asset_id,
            "path": path,
            "language_neutral": neutral,
            "provenance": {"kind": kind, "path": source_path, "sha256": source_hash},
            "artifact_sha256": artifact_hash,
            "candidate_sha256": candidate_hash,
            "evidence_ids": identifiers,
        }
        if locale is not None:
            normalized_asset["locale"] = locale
        normalized.append(normalized_asset)
    if [item["path"] for item in normalized] != sorted(item["path"] for item in normalized):
        raise ContractError("E_BUNDLE_ASSET", "asset manifest must use path order")
    if candidates is not None and set(candidates) != seen_paths:
        raise ContractError("E_BUNDLE_ASSET", "candidate assets and asset manifest differ")
    return copy.deepcopy({"schema_version": ASSET_MANIFEST_SCHEMA_VERSION, "assets": normalized})


def _v3_ref(value: Any, context: str, *, expected_path: str | None = None) -> dict[str, str]:
    ref = _closed(value, _V3_REF_FIELDS, context)
    path = _path(ref["path"], f"{context}.path")
    if expected_path is not None and path != expected_path:
        raise ContractError("E_VISUAL_PATH", f"{context}.path must be {expected_path}")
    return {"path": path, "sha256": _sha(ref["sha256"], f"{context}.sha256")}


def _v3_variant_refs(value: Any, name: str) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ContractError("E_SCHEMA_TYPE", f"asset manifest.compiled.{name} must be a non-empty array")
    normalized: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, raw in enumerate(value):
        context = f"asset manifest.compiled.{name}[{index}]"
        ref = _closed(raw, _V3_VARIANT_REF_FIELDS, context)
        locale = parse_locale(ref["locale"], f"{context}.locale")
        variant = ref["variant"]
        if not isinstance(variant, str) or variant not in {"desktop", "mobile"}:
            raise ContractError("E_SCHEMA_VALUE", f"{context}.variant must be desktop or mobile")
        key = (locale, variant)
        if key in seen:
            raise ContractError("E_VISUAL_FINGERPRINT", f"asset manifest.compiled.{name} contains duplicate locale/variant")
        seen.add(key)
        path = _path(ref["path"], f"{context}.path")
        expected = _V3_VARIANT_COLLECTIONS[name].format(locale=locale, variant=variant)
        if path != expected:
            raise ContractError("E_VISUAL_PATH", f"{context}.path does not match its locale/variant")
        normalized.append({"locale": locale, "variant": variant, "path": path, "sha256": _sha(ref["sha256"], f"{context}.sha256")})
    expected_order = sorted(normalized, key=lambda item: (item["locale"].encode("utf-8"), item["variant"].encode("utf-8")))
    if normalized != expected_order:
        raise ContractError("E_VISUAL_FINGERPRINT", f"asset manifest.compiled.{name} must use locale/variant order")
    return normalized


def _v3_identity(value: Any) -> dict[str, str]:
    identities = _closed(value, {"kernel", "elk", "renderer"}, "asset manifest.compiled.identities")
    return {name: _sha(identities[name], f"asset manifest.compiled.identities.{name}") for name in ("kernel", "elk", "renderer")}


def _v3_read(root: Path, path: str, context: str) -> bytes:
    return _safe_read(root, path, context)


def _v3_hash_ref(root: Path, ref: Mapping[str, str], context: str) -> bytes:
    raw = _v3_read(root, ref["path"], context)
    if hashlib.sha256(raw).hexdigest() != ref["sha256"]:
        raise ContractError("E_BUNDLE_HASH", f"{context} bytes differ from its reference")
    return raw


def _v3_inventory(
    compiled: Mapping[str, Any],
    *,
    artifact_root: Path,
) -> tuple[dict[str, Any], Any]:
    inventory_ref = compiled["inventory"]
    inventory_raw = _v3_hash_ref(artifact_root, inventory_ref, "asset manifest.compiled.inventory")
    try:
        inventory = json.loads(inventory_raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ContractError("E_VISUAL_DETERMINISM", "asset manifest inventory must be canonical JSON") from exc
    if canonical_json_bytes(inventory) != inventory_raw:
        raise ContractError("E_VISUAL_DETERMINISM", "asset manifest inventory must use canonical bytes")
    if not isinstance(inventory, dict) or set(inventory) != {"schema_version", "layers", "inventory_sha256"}:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory must be a closed LayeredFingerprint")
    if type(inventory["schema_version"]) is not int or inventory["schema_version"] != 1:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory requires schema_version 1")
    layers = inventory["layers"]
    if not isinstance(layers, list) or len(layers) != len(_V3_LAYER_NAMES):
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory must contain eight ordered layers")
    if any(not isinstance(layer, Mapping) for layer in layers) or tuple(layer.get("name") for layer in layers) != _V3_LAYER_NAMES:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory layers are not canonical")
    try:
        fingerprint = build_layered_fingerprint(
            layers[0]["sha256"],
            layers[1]["records"],
            layers[2]["sha256"],
            layers[3]["values"],
            layers[4]["records"],
            layers[5]["records"],
            layers[6]["records"],
            layers[7]["records"],
        )
    except KeyError as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory is missing a layer projection") from exc
    if fingerprint.inventory_sha256 != inventory["inventory_sha256"]:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest inventory hash is stale")
    if fingerprint.canonical_bytes() != inventory_raw:
        raise ContractError("E_VISUAL_DETERMINISM", "asset manifest inventory fingerprint bytes changed")
    if compiled["spec"]["sha256"] != fingerprint.spec_sha256 or compiled["theme"]["sha256"] != fingerprint.theme_sha256:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest compiled base refs differ from inventory")
    if compiled["identities"] != dict(fingerprint.identities):
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest compiler identities differ from inventory")
    return inventory, fingerprint


def _v3_compare_variant_refs(
    name: str,
    refs: Sequence[Mapping[str, str]],
    inventory_records: Sequence[Mapping[str, Any]],
) -> None:
    expected = {
        (record["locale"], record["variant"]): record["sha256"]
        for record in inventory_records
    }
    actual = {(ref["locale"], ref["variant"]): ref["sha256"] for ref in refs}
    if actual != expected:
        raise ContractError("E_VISUAL_FINGERPRINT", f"asset manifest compiled.{name} differs from inventory")


def _validate_asset_manifest_v3(
    payload: Any,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
    artifact_root: Path | None = None,
    candidate_assets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    _reject_float(payload)
    if artifact_root is None:
        raise ContractError("E_INPUT_PATH", "asset manifest v3 requires an artifact root")
    manifest = _closed(payload, {"schema_version", "assets", "compiled"}, "asset manifest")
    if type(manifest["schema_version"]) is not int or manifest["schema_version"] != ASSET_MANIFEST_V3_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "asset manifest requires schema_version 3")
    compiled = _closed(manifest["compiled"], _V3_COMPILED_FIELDS, "asset manifest.compiled")
    single_refs = {
        name: _v3_ref(compiled[name], f"asset manifest.compiled.{name}", expected_path=path)
        for name, path in _V3_SINGLE_REFS.items()
    }
    variant_refs = {
        name: _v3_variant_refs(compiled[name], name)
        for name in _V3_VARIANT_COLLECTIONS
    }
    identities = _v3_identity(compiled["identities"])
    compiled_normalized = {**single_refs, **variant_refs, "identities": identities}
    inventory, fingerprint = _v3_inventory(compiled_normalized, artifact_root=artifact_root)
    layers = inventory["layers"]
    _v3_compare_variant_refs("scenes", variant_refs["scenes"], layers[1]["records"])
    _v3_compare_variant_refs("gates", variant_refs["gates"], layers[4]["records"])
    _v3_compare_variant_refs("timelines", variant_refs["timelines"], layers[5]["records"])
    _v3_compare_variant_refs("interactions", variant_refs["interactions"], layers[6]["records"])

    inventory_artifacts = {
        record["path"]: record["sha256"]
        for record in layers[7]["records"]
    }
    referenced_artifacts = {
        single_refs["spec"]["path"]: single_refs["spec"]["sha256"],
        single_refs["theme"]["path"]: single_refs["theme"]["sha256"],
        **{ref["path"]: ref["sha256"] for refs in variant_refs.values() for ref in refs},
    }
    if referenced_artifacts != inventory_artifacts:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest compiled refs do not close over inventory artifacts")
    for path, digest in referenced_artifacts.items():
        raw = _v3_read(artifact_root, path, f"asset manifest compiled.{path}")
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ContractError("E_BUNDLE_HASH", f"asset manifest compiled artifact bytes changed: {path}")

    if evidence_graph is not None:
        graph = validate_evidence_graph(dict(evidence_graph))
        known_ids = {fact["fact_id"] for fact in graph["facts"]}
    else:
        known_ids = None
    if candidate_assets is not None:
        candidates: dict[str, str] = {}
        for index, reference in enumerate(candidate_assets):
            ref = _closed(reference, {"path", "sha256"}, f"candidate.assets[{index}]")
            path = _path(ref["path"], f"candidate.assets[{index}].path")
            if path in candidates:
                raise ContractError("E_BUNDLE_ASSET", "candidate assets contain duplicate path")
            candidates[path] = _sha(ref["sha256"], f"candidate.assets[{index}].sha256")
        spec_hash = hashlib.sha256(_v3_read(artifact_root, single_refs["spec"]["path"], "asset manifest compiled.spec")).hexdigest()
        if candidates.get("visual-spec.json") != spec_hash:
            raise ContractError("E_BUNDLE_HASH", "candidate visual-spec.json does not bind compiled spec bytes")

    raw_assets = manifest["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) > MAX_ASSETS:
        raise ContractError("E_SCHEMA_TYPE", f"asset manifest.assets must contain at most {MAX_ASSETS} entries")
    svg_refs = {(ref["locale"], ref["variant"]): ref for ref in variant_refs["svgs"]}
    normalized_assets: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_paths: set[str] = set()
    for index, raw in enumerate(raw_assets):
        context = f"asset manifest.assets[{index}]"
        if not isinstance(raw, dict):
            raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
        unknown = sorted(set(raw) - _V3_ASSET_REQUIRED_FIELDS - _V3_ASSET_OPTIONAL_FIELDS)
        missing = sorted(_V3_ASSET_REQUIRED_FIELDS - set(raw))
        if unknown:
            raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
        if missing:
            raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
        asset_id = normalize_text(raw["asset_id"], f"{context}.asset_id", maximum=512)
        path = _path(raw["path"], f"{context}.path")
        locale = parse_locale(raw["locale"], f"{context}.locale")
        variant = raw["variant"]
        if not isinstance(variant, str) or variant not in {"desktop", "mobile"}:
            raise ContractError("E_SCHEMA_VALUE", f"{context}.variant must be desktop or mobile")
        key = (locale, variant)
        svg = svg_refs.get(key)
        if svg is None or svg["path"] != path:
            raise ContractError("E_VISUAL_FINGERPRINT", f"{context} does not reference an inventory SVG")
        if asset_id in seen_ids or path in seen_paths:
            raise ContractError("E_BUNDLE_ASSET", f"{context} duplicates asset identity or path")
        seen_ids.add(asset_id)
        seen_paths.add(path)
        role = normalize_text(raw["role"], f"{context}.role", maximum=128)
        if role not in _V3_ASSET_ROLES:
            raise ContractError("E_BUNDLE_ASSET", f"{context}.role is unsupported")
        artifact_hash = _sha(raw["artifact_sha256"], f"{context}.artifact_sha256")
        scene_hash = _sha(raw["scene_sha256"], f"{context}.scene_sha256")
        gate_hash = _sha(raw["gate_sha256"], f"{context}.gate_sha256")
        if artifact_hash != svg["sha256"] or scene_hash != next(ref["sha256"] for ref in variant_refs["scenes"] if (ref["locale"], ref["variant"]) == key) or gate_hash != next(ref["sha256"] for ref in variant_refs["gates"] if (ref["locale"], ref["variant"]) == key):
            raise ContractError("E_VISUAL_FINGERPRINT", f"{context} source hashes differ from compiled inventory")
        identifiers = _ids(raw["evidence_ids"], f"{context}.evidence_ids")
        if known_ids is not None and not set(identifiers).issubset(known_ids):
            raise ContractError("E_CLAIM_EVIDENCE", f"{context} references missing evidence")
        normalized_asset: dict[str, Any] = {
            "asset_id": asset_id,
            "path": path,
            "artifact_sha256": artifact_hash,
            "evidence_ids": identifiers,
            "role": role,
            "locale": locale,
            "variant": variant,
            "scene_sha256": scene_hash,
            "gate_sha256": gate_hash,
        }
        if "provenance" in raw:
            provenance = _closed(raw["provenance"], _PROVENANCE_FIELDS, f"{context}.provenance")
            if provenance["kind"] != "generated":
                raise ContractError("E_BUNDLE_ASSET", f"{context}.provenance.kind must be generated")
            source_path = _path(provenance["path"], f"{context}.provenance.path")
            source_hash = _sha(provenance["sha256"], f"{context}.provenance.sha256")
            expected_scene_path = next(ref["path"] for ref in variant_refs["scenes"] if (ref["locale"], ref["variant"]) == key)
            if source_path != expected_scene_path or source_hash != scene_hash:
                raise ContractError("E_VISUAL_FINGERPRINT", f"{context}.provenance must bind its Scene source")
            if hashlib.sha256(_v3_read(artifact_root, source_path, f"{context}.provenance")).hexdigest() != source_hash:
                raise ContractError("E_BUNDLE_HASH", f"{context}.provenance bytes changed")
            normalized_asset["provenance"] = {"kind": "generated", "path": source_path, "sha256": source_hash}
        if hashlib.sha256(_v3_read(artifact_root, path, context)).hexdigest() != artifact_hash:
            raise ContractError("E_BUNDLE_HASH", f"{context} artifact bytes changed")
        normalized_assets.append(normalized_asset)
    if [item["path"] for item in normalized_assets] != sorted(item["path"] for item in normalized_assets):
        raise ContractError("E_BUNDLE_ASSET", "asset manifest v3 must use path order")
    if set(seen_paths) != {ref["path"] for ref in variant_refs["svgs"]}:
        raise ContractError("E_VISUAL_FINGERPRINT", "asset manifest assets do not close over compiled SVG refs")
    return copy.deepcopy({"schema_version": ASSET_MANIFEST_V3_SCHEMA_VERSION, "assets": normalized_assets, "compiled": compiled_normalized})


def validate_asset_manifest(
    payload: Any,
    *,
    evidence_graph: Mapping[str, Any] | None = None,
    artifact_root: Path | None = None,
    candidate_assets: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    if isinstance(payload, Mapping) and payload.get("schema_version") == ASSET_MANIFEST_V3_SCHEMA_VERSION:
        return _validate_asset_manifest_v3(
            payload,
            evidence_graph=evidence_graph,
            artifact_root=artifact_root,
            candidate_assets=candidate_assets,
        )
    return _validate_asset_manifest_v2(
        payload,
        evidence_graph=evidence_graph,
        artifact_root=artifact_root,
        candidate_assets=candidate_assets,
    )


validate_asset_manifest_v3 = _validate_asset_manifest_v3


def canonical_asset_manifest_bytes(payload: Any, **kwargs: Any) -> bytes:
    return canonical_json_bytes(validate_asset_manifest(payload, **kwargs))


def read_asset_manifest(payload: Mapping[str, Any], **kwargs: Any) -> dict[str, Any]:
    if payload.get("schema_version") == 1:
        legacy = copy.deepcopy(dict(payload))
        if set(legacy) != {"schema_version", "assets"} or not isinstance(legacy["assets"], list):
            raise ContractError("E_SCHEMA_FIELDS", "v1 asset manifest fields are invalid")
        return legacy
    return validate_asset_manifest(payload, **kwargs)
