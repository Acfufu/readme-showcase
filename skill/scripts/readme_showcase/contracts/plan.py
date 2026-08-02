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
_ABSOLUTE_PATH = re.compile(r"(?:^|\s)(?:/|~/)[^\s]*")
_SECRET_ASSIGNMENT = re.compile(
    r"(?i)(?:^|\s)(?:api[_-]?key|api[_-]?token|access[_-]?token|auth[_-]?token|password|private[_-]?key|secret)\s*[:=]"
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


def _text(value: Any, path: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip() or "\x00" in value:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be non-empty normalized text")
    normalized = unicodedata.normalize("NFC", value)
    if len(normalized.encode("utf-8")) > MAX_PLAN_TEXT_BYTES:
        raise ContractError("E_GENERATION_REQUEST_VALUE", f"{path} exceeds {MAX_PLAN_TEXT_BYTES} bytes")
    if _ABSOLUTE_PATH.search(normalized) or _SECRET_ASSIGNMENT.search(normalized):
        raise ContractError("E_GENERATION_REQUEST_VALUE", f"{path} contains an absolute path or secret assignment")
    return normalized


def _strings(value: Any, path: str, *, allowed: frozenset[str] | None = None) -> list[str]:
    if not isinstance(value, list) or len(value) > MAX_PLAN_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be a bounded array")
    result = [_text(item, f"{path}[]") for item in value]
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
    normalized_mode = _text(plan["mode"], "README plan.mode")
    if normalized_mode not in PLAN_MODES or (mode is not None and normalized_mode != mode):
        raise ContractError("E_BUNDLE_PLAN", "README plan mode is unsupported")
    languages = _strings(plan["languages"], "README plan.languages", allowed=PLAN_LANGUAGES)
    if not languages:
        raise ContractError("E_README_LANGUAGE", "README plan.languages must not be empty")
    diagram_route = _text(plan["diagram_route"], "README plan.diagram_route")
    if diagram_route not in DIAGRAM_ROUTES:
        raise ContractError("E_BUNDLE_PLAN", "README plan diagram route is unsupported")
    normalized = {
        "schema_version": README_PLAN_SCHEMA_VERSION,
        "mode": normalized_mode,
        "languages": languages,
        "sections": _strings(plan["sections"], "README plan.sections"),
        "visual_intent": _text(plan["visual_intent"], "README plan.visual_intent"),
        "diagram_route": diagram_route,
        "commands": _strings(plan["commands"], "README plan.commands"),
        "evidence_ids": _strings(plan["evidence_ids"], "README plan.evidence_ids"),
    }
    return copy.deepcopy(normalized)


def canonical_readme_plan_bytes(payload: Any, *, mode: str | None = None) -> bytes:
    return canonical_json_bytes(validate_readme_plan(payload, mode=mode))
