from __future__ import annotations

from typing import Any

from ..contracts.plan import validate_locale_mappings, validate_readme_plan_v2


def validate_readme_locales(value: Any) -> list[dict[str, str]]:
    return validate_locale_mappings(value)


def validate_explicit_readme_plan(payload: Any) -> dict[str, Any]:
    return validate_readme_plan_v2(payload)
