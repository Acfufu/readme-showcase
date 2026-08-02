from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, validate_contract


README_PLAN_SCHEMA_VERSION = 1
PLAN_MODES = frozenset({"readme", "asset-only", "audit-only"})
PLAN_LANGUAGES = frozenset({"en", "zh"})
DIAGRAM_ROUTES = frozenset({"none", "static", "elk"})
MAX_PLAN_ITEMS = 10_000
MAX_PLAN_TEXT_BYTES = 4096
_URL = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s<>(){}\[\]\"']+")
_JSON_POINTER = re.compile(r"/(?:[^~/\s]|~[01])*(?:/(?:[^~/\s]|~[01])*)*\Z")
_LABELED_JSON_POINTER = re.compile(
    r"(?i)\b(?:json\s+pointer|rfc\s*6901\s+pointer)\s*(?::|=)?\s*"
    r'(?P<pointer>/[^\s<>(){}\[\],;]*|"")'
)
_PARENT_TRAVERSAL = re.compile(r"(?<![A-Za-z0-9_.])\.\.(?:$|[/\\])")
_POSIX_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9_/])/(?![/\s])")
_HOME_ABSOLUTE = re.compile(r"(?<![A-Za-z0-9_])~/")
_WINDOWS_DRIVE = re.compile(r"(?:^|[^A-Za-z0-9])[A-Za-z]:[\\/]")
_WINDOWS_UNC = re.compile(r"(?:^|[^A-Za-z0-9_:])(?:\\\\|//)[^\\/\s]+[\\/][^\\/\s]+")
_SECRET_KEY_NAMES = frozenset(
    {"apikey", "apitoken", "accesstoken", "authtoken", "password", "privatekey", "secret"}
)
_SECRET_ASSIGNMENT_CANDIDATE = re.compile(
    r"(?<![A-Za-z0-9_])(?P<quote>[\"'`]?)"
    r"(?P<key>[A-Za-z][A-Za-z0-9_-]*)(?P=quote)\s*[:=]"
)


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_float(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_float(item, f"{path}.{key}")


def _masked_safe_references(value: str, *, allow_json_pointer: bool) -> str:
    characters = list(value)

    def mask(start: int, end: int) -> None:
        characters[start:end] = " " * (end - start)

    for match in _URL.finditer(value):
        mask(*match.span())
    if allow_json_pointer and _JSON_POINTER.fullmatch(value):
        mask(0, len(value))
    for match in _LABELED_JSON_POINTER.finditer(value):
        pointer = match.group("pointer")
        if pointer == '""' or _JSON_POINTER.fullmatch(pointer):
            mask(*match.span("pointer"))
    return "".join(characters)


def _contains_secret_assignment(value: str) -> bool:
    for match in _SECRET_ASSIGNMENT_CANDIDATE.finditer(value):
        key = match.group("key").casefold().replace("_", "").replace("-", "")
        if key in _SECRET_KEY_NAMES:
            return True
    return False


def normalize_generation_text(
    value: Any,
    path: str,
    *,
    maximum: int = MAX_PLAN_TEXT_BYTES,
    allow_json_pointer: bool = False,
) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be non-empty normalized text")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized.encode("utf-8")) > maximum:
        raise ContractError("E_GENERATION_REQUEST_VALUE", f"{path} exceeds {maximum} bytes")
    inspected = _masked_safe_references(normalized, allow_json_pointer=allow_json_pointer)
    if (
        _PARENT_TRAVERSAL.search(inspected)
        or _POSIX_ABSOLUTE.search(inspected)
        or _HOME_ABSOLUTE.search(inspected)
        or _WINDOWS_DRIVE.search(inspected)
        or _WINDOWS_UNC.search(inspected)
        or _contains_secret_assignment(normalized)
    ):
        raise ContractError("E_GENERATION_REQUEST_VALUE", f"{path} contains an absolute path or secret assignment")
    return normalized


def _strings(
    value: Any,
    path: str,
    *,
    allowed: frozenset[str] | None = None,
) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PLAN_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be a bounded array")
    result = [normalize_generation_text(item, f"{path}[]") for item in value]
    if len(result) != len(set(result)):
        raise ContractError("E_SCHEMA_VALUE", f"{path} must not contain duplicates")
    if allowed is not None and not set(result).issubset(allowed):
        raise ContractError("E_SCHEMA_VALUE", f"{path} contains an unsupported value")
    return result


def validate_readme_plan(payload: Any, *, mode: str | None = None) -> dict[str, Any]:
    _reject_float(payload)
    plan = validate_contract(
        payload,
        required={
            "schema_version", "mode", "languages", "sections", "visual_intent",
            "diagram_route", "commands", "evidence_ids",
        },
        optional=set(),
        context="README plan",
    )
    normalized_mode = normalize_generation_text(plan["mode"], "README plan.mode")
    if normalized_mode not in PLAN_MODES or (mode is not None and normalized_mode != mode):
        raise ContractError("E_BUNDLE_PLAN", "README plan mode is unsupported")
    languages = _strings(plan["languages"], "README plan.languages", allowed=PLAN_LANGUAGES)
    if not languages:
        raise ContractError("E_README_LANGUAGE", "README plan.languages must not be empty")
    diagram_route = normalize_generation_text(plan["diagram_route"], "README plan.diagram_route")
    if diagram_route not in DIAGRAM_ROUTES:
        raise ContractError("E_BUNDLE_PLAN", "README plan diagram route is unsupported")
    normalized = {
        "schema_version": README_PLAN_SCHEMA_VERSION,
        "mode": normalized_mode,
        "languages": languages,
        "sections": _strings(plan["sections"], "README plan.sections"),
        "visual_intent": normalize_generation_text(
            plan["visual_intent"], "README plan.visual_intent"
        ),
        "diagram_route": diagram_route,
        "commands": _strings(plan["commands"], "README plan.commands"),
        "evidence_ids": _strings(plan["evidence_ids"], "README plan.evidence_ids"),
    }
    return copy.deepcopy(normalized)


def canonical_readme_plan_bytes(payload: Any, *, mode: str | None = None) -> bytes:
    return canonical_json_bytes(validate_readme_plan(payload, mode=mode))
