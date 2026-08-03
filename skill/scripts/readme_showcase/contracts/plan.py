from __future__ import annotations

import copy
import re
import unicodedata
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, validate_contract
from .common import normalize_posix_path
from .locale import parse_locale


README_PLAN_SCHEMA_VERSION = 1
README_PLAN_V2_SCHEMA_VERSION = 2
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
_EVIDENCE_ID = re.compile(r"[a-z]+:[0-9a-f]{64}\Z")


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


def _readme_path(value: Any, context: str) -> str:
    try:
        return normalize_posix_path(value)
    except ValueError as exc:
        raise ContractError("E_README_PATH", f"{context} must be a safe repository-relative POSIX path") from exc


def validate_locale_mappings(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not value:
        raise ContractError("E_LOCALE", "README plan.locales must be a non-empty array")
    mappings: list[dict[str, str]] = []
    tags: set[str] = set()
    paths: set[str] = set()
    for index, raw in enumerate(value):
        context = f"README plan.locales[{index}]"
        if not isinstance(raw, dict):
            raise ContractError("E_SCHEMA_TYPE", f"{context} must be an object")
        unknown = sorted(set(raw) - {"tag", "readme_path"})
        missing = sorted({"tag", "readme_path"} - set(raw))
        if unknown:
            raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
        if missing:
            raise ContractError("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
        tag = parse_locale(raw["tag"], f"{context}.tag")
        path = _readme_path(raw["readme_path"], f"{context}.readme_path")
        if tag in tags:
            raise ContractError("E_LOCALE", "README plan.locales contains duplicate tag")
        if path in paths:
            raise ContractError("E_README_PATH", "README plan.locales contains duplicate readme_path")
        tags.add(tag)
        paths.add(path)
        mappings.append({"tag": tag, "readme_path": path})
    return mappings


def validate_readme_plan(payload: Any, *, mode: str | None = None) -> dict[str, Any]:
    _reject_float(payload)
    if not isinstance(payload, dict):
        raise ContractError("E_SCHEMA_TYPE", "README plan must be a JSON object")
    version = payload.get("schema_version")
    if type(version) is not int or version not in {README_PLAN_SCHEMA_VERSION, README_PLAN_V2_SCHEMA_VERSION}:
        raise ContractError("E_SCHEMA_VERSION", "README plan requires schema_version 1 or 2")
    version_field = "languages" if version == README_PLAN_SCHEMA_VERSION else "locales"
    fields = {
        "schema_version", "mode", version_field, "sections", "visual_intent",
        "diagram_route", "commands", "evidence_ids",
    }
    unknown = sorted(set(payload) - fields)
    missing = sorted(fields - set(payload))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"README plan contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"README plan is missing required field: {missing[0]}")
    plan = payload
    normalized_mode = normalize_generation_text(plan["mode"], "README plan.mode")
    if normalized_mode not in PLAN_MODES or (mode is not None and normalized_mode != mode):
        raise ContractError("E_BUNDLE_PLAN", "README plan mode is unsupported")
    if version == README_PLAN_SCHEMA_VERSION:
        locale_contract: dict[str, Any] = {
            "languages": _strings(plan["languages"], "README plan.languages", allowed=PLAN_LANGUAGES)
        }
        if not locale_contract["languages"]:
            raise ContractError("E_README_LANGUAGE", "README plan.languages must not be empty")
    else:
        locale_contract = {"locales": validate_locale_mappings(plan["locales"])}
    diagram_route = normalize_generation_text(plan["diagram_route"], "README plan.diagram_route")
    if diagram_route not in DIAGRAM_ROUTES:
        raise ContractError("E_BUNDLE_PLAN", "README plan diagram route is unsupported")
    evidence_ids = _strings(plan["evidence_ids"], "README plan.evidence_ids")
    if version == README_PLAN_V2_SCHEMA_VERSION and not evidence_ids:
        raise ContractError("E_CLAIM_EVIDENCE", "README plan v2 requires normative evidence")
    if version == README_PLAN_V2_SCHEMA_VERSION and any(not _EVIDENCE_ID.fullmatch(item) for item in evidence_ids):
        raise ContractError("E_CLAIM_EVIDENCE", "README plan v2 evidence IDs must be normative Evidence v2 IDs")
    normalized = {
        "schema_version": version,
        "mode": normalized_mode,
        **locale_contract,
        "sections": _strings(plan["sections"], "README plan.sections"),
        "visual_intent": normalize_generation_text(
            plan["visual_intent"], "README plan.visual_intent"
        ),
        "diagram_route": diagram_route,
        "commands": _strings(plan["commands"], "README plan.commands"),
        "evidence_ids": evidence_ids,
    }
    return copy.deepcopy(normalized)


def canonical_readme_plan_bytes(payload: Any, *, mode: str | None = None) -> bytes:
    return canonical_json_bytes(validate_readme_plan(payload, mode=mode))


def validate_readme_plan_v2(payload: Any, *, mode: str | None = None) -> dict[str, Any]:
    plan = validate_readme_plan(payload, mode=mode)
    if plan["schema_version"] != README_PLAN_V2_SCHEMA_VERSION:
        raise ContractError("E_SCHEMA_VERSION", "README plan producer requires schema_version 2")
    return plan
