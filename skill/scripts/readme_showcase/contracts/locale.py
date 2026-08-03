from __future__ import annotations

from typing import Any

from ...pipeline_contracts import ContractError


LOCALE_TAGS = (
    "en",
    "zh-Hans",
    "zh-Hant",
    "ja",
    "ko",
    "fr",
    "de",
)
LOCALE_TAG_SET = frozenset(LOCALE_TAGS)


def parse_locale(value: Any, context: str = "locale") -> str:
    if not isinstance(value, str) or value not in LOCALE_TAG_SET:
        raise ContractError("E_LOCALE", f"{context} must be one of the supported canonical locale tags")
    return value
