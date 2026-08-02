from __future__ import annotations

from typing import Literal

from ..errors import AGGREGATABLE_CODES, FAIL_FAST_CODES


AGGREGATE_CONTENT = "aggregate-content"
FAIL_FAST = "fail-fast"
Disposition = Literal["aggregate-content", "fail-fast"]
KNOWN_ERROR_CODES = AGGREGATABLE_CODES | FAIL_FAST_CODES


def classify_error_code(code: str) -> Disposition:
    return AGGREGATE_CONTENT if code in AGGREGATABLE_CODES else FAIL_FAST
