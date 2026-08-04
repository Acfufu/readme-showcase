"""Deterministic, ordered fingerprints for compiled visual artifacts.

The compiler produces bytes in later stages.  This module only binds their
already-computed SHA-256 values into one canonical, ordered projection.  The
projection intentionally accepts relative artifact paths and no filesystem or
clock metadata, so moving an attempt cannot change its identity.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes
from ..contracts.locale import LOCALE_TAG_SET, parse_locale


FINGERPRINT_SCHEMA_VERSION = 1
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VARIANTS = frozenset({"desktop", "mobile"})
_IDENTITIES = ("kernel", "elk", "renderer")
_RECORD_FIELDS = frozenset({"locale", "variant", "sha256", "prior_sha256"})
_ARTIFACT_FIELDS = frozenset({"path", "sha256", "prior_sha256"})


def _fail(message: str) -> None:
    raise ContractError("E_VISUAL_FINGERPRINT", message)


def _sha256(value: Any, context: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(f"{context} must be a lowercase SHA-256 digest")
    return value


def _text(value: Any, context: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        _fail(f"{context} must be a non-empty string")
    normalized = unicodedata.normalize("NFC", value)
    if normalized != value or any(ord(char) < 0x20 for char in value):
        _fail(f"{context} must be normalized text")
    return value


def _canonical_hash(value: Any, context: str) -> str:
    try:
        raw = canonical_json_bytes(value)
    except ContractError as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", f"{context} is not canonical JSON") from exc
    return hashlib.sha256(raw).hexdigest()


def _closed(raw: Mapping[str, Any], fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        _fail(f"{context} must be an object")
    keys = set(raw)
    unknown = sorted(keys - fields)
    if unknown:
        _fail(f"{context} contains unsupported field: {unknown[0]}")
    missing = sorted(fields - keys)
    if missing:
        _fail(f"{context} is missing required field: {missing[0]}")
    return {field: raw[field] for field in fields}


def _locale(value: Any, context: str) -> str:
    if not isinstance(value, str) or value not in LOCALE_TAG_SET:
        _fail(f"{context} must be a canonical locale tag")
    try:
        return parse_locale(value, context)
    except ContractError as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", str(exc)) from exc


def _variant(value: Any, context: str) -> str:
    variant = _text(value, context)
    if variant not in _VARIANTS:
        _fail(f"{context} must be desktop or mobile")
    return variant


def _relative_path(value: Any, context: str) -> str:
    path = _text(value, context)
    if "\\" in path or path.startswith("/") or path.startswith("~/"):
        _fail(f"{context} must be a relative POSIX path")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or path != parsed.as_posix()
        or any(part in {"", ".", ".."} for part in parsed.parts)
    ):
        _fail(f"{context} must be a normalized relative POSIX path")
    return parsed.as_posix()


def _variant_records(source: Any, context: str) -> tuple[tuple[str, str, str, str], ...]:
    if isinstance(source, Mapping) or isinstance(source, (str, bytes, bytearray)):
        _fail(f"{context} must be an iterable of record objects")
    try:
        values = iter(source)
    except TypeError as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", f"{context} must be an iterable of record objects") from exc
    entries: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            _fail(f"{context}[{index}] must be an object")
        entries.append(_closed(value, _RECORD_FIELDS, f"{context}[{index}]"))

    if not entries:
        _fail(f"{context} must contain one or more records")

    normalized: list[tuple[str, str, str, str]] = []
    seen: set[tuple[str, str]] = set()
    for index, entry in enumerate(entries):
        locale = _locale(entry["locale"], f"{context}[{index}].locale")
        variant = _variant(entry["variant"], f"{context}[{index}].variant")
        key = (locale, variant)
        if key in seen:
            _fail(f"{context} contains duplicate locale/variant: {locale}/{variant}")
        seen.add(key)
        normalized.append(
            (
                locale,
                variant,
                _sha256(entry["sha256"], f"{context}[{index}].sha256"),
                _sha256(entry["prior_sha256"], f"{context}[{index}].prior_sha256"),
            )
        )
    normalized.sort(key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8")))
    return tuple(normalized)


def _artifact_records(source: Any, context: str) -> tuple[tuple[str, str, str], ...]:
    if isinstance(source, Mapping) or isinstance(source, (str, bytes, bytearray)):
        _fail(f"{context} must be an iterable of record objects")
    try:
        values = iter(source)
    except TypeError as exc:
        raise ContractError("E_VISUAL_FINGERPRINT", f"{context} must be an iterable of record objects") from exc
    entries: list[dict[str, Any]] = []
    for index, value in enumerate(values):
        if not isinstance(value, Mapping):
            _fail(f"{context}[{index}] must be an object")
        entries.append(_closed(value, _ARTIFACT_FIELDS, f"{context}[{index}]"))

    if not entries:
        _fail(f"{context} must contain one or more records")
    normalized: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        path = _relative_path(entry["path"], f"{context}[{index}].path")
        if path in seen:
            _fail(f"{context} contains duplicate path: {path}")
        seen.add(path)
        normalized.append(
            (
                path,
                _sha256(entry["sha256"], f"{context}[{index}].sha256"),
                _sha256(entry["prior_sha256"], f"{context}[{index}].prior_sha256"),
            )
        )
    normalized.sort(key=lambda item: item[0].encode("utf-8"))
    return tuple(normalized)


def _variant_projection(records: tuple[tuple[str, str, str, str], ...]) -> list[dict[str, str]]:
    return [
        {"locale": locale, "variant": variant, "sha256": digest, "prior_sha256": prior}
        for locale, variant, digest, prior in records
    ]


def _artifact_projection(records: tuple[tuple[str, str, str], ...]) -> list[dict[str, str]]:
    return [{"path": path, "sha256": digest, "prior_sha256": prior} for path, digest, prior in records]


@dataclass(frozen=True, slots=True)
class LayeredFingerprint:
    """Immutable fingerprint projection and its inventory digest."""

    spec_sha256: str
    scenes: tuple[tuple[str, str, str, str], ...]
    theme_sha256: str
    identities: tuple[tuple[str, str], ...]
    gates: tuple[tuple[str, str, str, str], ...]
    timelines: tuple[tuple[str, str, str, str], ...]
    interactions: tuple[tuple[str, str, str, str], ...]
    artifacts: tuple[tuple[str, str, str], ...]
    inventory_sha256: str

    def projection(self) -> dict[str, Any]:
        scenes = _variant_projection(self.scenes)
        gates = _variant_projection(self.gates)
        timelines = _variant_projection(self.timelines)
        interactions = _variant_projection(self.interactions)
        artifacts = _artifact_projection(self.artifacts)
        return {
            "schema_version": FINGERPRINT_SCHEMA_VERSION,
            "layers": [
                {"name": "spec", "sha256": self.spec_sha256},
                {"name": "scenes", "records": scenes},
                {"name": "theme", "sha256": self.theme_sha256},
                {"name": "identities", "values": dict(self.identities)},
                {"name": "gates", "records": gates},
                {"name": "timelines", "records": timelines},
                {"name": "interactions", "records": interactions},
                {"name": "artifacts", "records": artifacts},
            ],
        }

    def as_dict(self) -> dict[str, Any]:
        result = self.projection()
        result["inventory_sha256"] = self.inventory_sha256
        return result

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return self.inventory_sha256


def build_layered_fingerprint(
    spec_sha256: str,
    scenes: Iterable[Mapping[str, Any]],
    theme_sha256: str,
    identities: Mapping[str, str],
    gates: Iterable[Mapping[str, Any]],
    timelines: Iterable[Mapping[str, Any]],
    interactions: Iterable[Mapping[str, Any]],
    artifacts: Iterable[Mapping[str, Any]],
) -> LayeredFingerprint:
    """Build the canonical Visual Kernel fingerprint projection.

    Scene/report records are iterable objects sorted by UTF-8 locale then
    variant. Artifact records are iterable objects sorted by UTF-8 relative
    path. Scene records bind the canonical Spec hash; each report binds the
    previous report layer at the same locale/variant; artifacts bind the
    aggregate hash of all three report layers. All three compiler identities
    are SHA-256 byte identities. These bindings make stale downstream bytes
    fail closed.
    """

    spec = _sha256(spec_sha256, "spec_sha256")
    theme = _sha256(theme_sha256, "theme_sha256")
    if not isinstance(identities, Mapping) or set(identities) != set(_IDENTITIES):
        _fail("identities must contain exactly kernel, elk, and renderer")
    identity_values: tuple[tuple[str, str], ...] = tuple(
        (name, _sha256(identities[name], f"identities.{name}")) for name in _IDENTITIES
    )

    scene_records = _variant_records(scenes, "scenes")
    gate_records = _variant_records(gates, "gates")
    timeline_records = _variant_records(timelines, "timelines")
    interaction_records = _variant_records(interactions, "interactions")
    artifact_records = _artifact_records(artifacts, "artifacts")

    scene_by_key = {(locale, variant): digest for locale, variant, digest, _ in scene_records}
    scene_keys = set(scene_by_key)
    for locale, variant, _, prior in scene_records:
        if prior != spec:
            _fail(f"scenes {locale}/{variant} has a stale prior-layer digest")

    def check_report_layer(
        records: tuple[tuple[str, str, str, str], ...],
        name: str,
        previous: Mapping[tuple[str, str], str],
    ) -> dict[tuple[str, str], str]:
        values: dict[tuple[str, str], str] = {}
        keys = {(locale, variant) for locale, variant, _, _ in records}
        if keys != scene_keys:
            _fail(f"{name} locale/variant set does not match scenes")
        for locale, variant, digest, prior in records:
            key = (locale, variant)
            if prior != previous[key]:
                _fail(f"{name} {locale}/{variant} has a stale prior-layer digest")
            values[key] = digest
        return values

    gate_by_key = check_report_layer(gate_records, "gates", scene_by_key)
    timeline_by_key = check_report_layer(timeline_records, "timelines", gate_by_key)
    interaction_by_key = check_report_layer(interaction_records, "interactions", timeline_by_key)

    gate_projection = _variant_projection(gate_records)
    timeline_projection = _variant_projection(timeline_records)
    interaction_projection = _variant_projection(interaction_records)
    reports_prior = _canonical_hash(
        {
            "gates": gate_projection,
            "timelines": timeline_projection,
            "interactions": interaction_projection,
        },
        "reports prior-layer projection",
    )
    for index, (path, _, prior) in enumerate(artifact_records):
        if prior != reports_prior:
            _fail(f"artifacts[{index}] {path} has a stale prior-layer digest")

    result = LayeredFingerprint(
        spec,
        scene_records,
        theme,
        identity_values,
        gate_records,
        timeline_records,
        interaction_records,
        artifact_records,
        "",
    )
    inventory = _canonical_hash(result.projection(), "fingerprint projection")
    return LayeredFingerprint(
        result.spec_sha256,
        result.scenes,
        result.theme_sha256,
        result.identities,
        result.gates,
        result.timelines,
        result.interactions,
        result.artifacts,
        inventory,
    )


__all__ = ["FINGERPRINT_SCHEMA_VERSION", "LayeredFingerprint", "build_layered_fingerprint"]
