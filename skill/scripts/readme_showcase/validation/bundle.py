from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from ...pipeline_contracts import ContractError
from ..diagnostics import Diagnostic, DiagnosticReport
from ..errors import contract_error_from_diagnostic, diagnostic_from_contract_error
from .policy import AGGREGATE_CONTENT, classify_error_code


ValidationCheck = Callable[[], Diagnostic | None]


def capture_content_error(
    diagnostics: list[Diagnostic],
    error: ContractError,
    *,
    path: str | None = None,
    line: int | None = None,
) -> None:
    if classify_error_code(error.code) != AGGREGATE_CONTENT:
        raise error
    diagnostics.append(diagnostic_from_contract_error(error, path=path, line=line))


def validate_checks(checks: Iterable[ValidationCheck]) -> DiagnosticReport:
    diagnostics: list[Diagnostic] = []
    for check in checks:
        try:
            diagnostic = check()
        except ContractError as error:
            capture_content_error(diagnostics, error)
        else:
            if diagnostic is not None:
                if classify_error_code(diagnostic.code) != AGGREGATE_CONTENT:
                    raise contract_error_from_diagnostic(diagnostic)
                diagnostics.append(diagnostic)
    return DiagnosticReport.build(diagnostics)


def validation_report(diagnostics: Iterable[Diagnostic]) -> dict[str, object]:
    report = DiagnosticReport.build(diagnostics)
    return {"schema_version": 1, **report.as_dict()}


def validate_generated_bundle(
    payload: Any,
    artifact_root: Path,
    *,
    validate_v1: Callable[[Any, Path], dict[str, object]],
) -> dict[str, object]:
    if (
        isinstance(payload, dict)
        and payload.get("schema_version") == 2
        and set(payload) != {"schema_version"}
    ):
        from ..generation.assembler import validate_generated_bundle_v2

        return cast(dict[str, object], validate_generated_bundle_v2(payload, artifact_root))
    return validate_v1(payload, artifact_root)
