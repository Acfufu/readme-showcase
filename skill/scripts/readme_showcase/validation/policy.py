from __future__ import annotations

from typing import Literal

from ...pipeline_contracts import ContractError
from ..diagnostics import Diagnostic
from ..errors import AGGREGATABLE_CODES, CONTENT_DIAGNOSTIC_POLICY, FAIL_FAST_CODES


AGGREGATE_CONTENT = "aggregate-content"
FAIL_FAST = "fail-fast"
Disposition = Literal["aggregate-content", "fail-fast"]
KNOWN_ERROR_CODES = AGGREGATABLE_CODES | FAIL_FAST_CODES


def classify_error_code(code: str) -> Disposition:
    return AGGREGATE_CONTENT if code in AGGREGATABLE_CODES else FAIL_FAST


def require_content_diagnostic(value: object) -> Diagnostic:
    if not isinstance(value, Diagnostic) or (
        not isinstance(value.code, str)
        or not value.code
        or not isinstance(value.message, str)
        or (value.path is not None and not isinstance(value.path, str))
        or not isinstance(value.related_ids, tuple)
        or any(not isinstance(item, str) for item in value.related_ids)
        or (
            value.suggested_action is not None
            and not isinstance(value.suggested_action, str)
        )
    ):
        raise ContractError("E_SCHEMA_TYPE", "diagnostic policy requires a Diagnostic")
    diagnostic = value
    expected = CONTENT_DIAGNOSTIC_POLICY.get(diagnostic.code)
    if expected != (diagnostic.severity, diagnostic.category):
        raise ContractError(
            diagnostic.code,
            f"diagnostic policy rejects {diagnostic.severity}/{diagnostic.category}",
        )
    return diagnostic
