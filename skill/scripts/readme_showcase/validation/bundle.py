from __future__ import annotations

from collections.abc import Callable, Iterable
from pathlib import Path
from typing import Any, cast

from ...pipeline_contracts import ContractError
from ..diagnostics import Diagnostic, DiagnosticReport
from ..errors import diagnostic_from_contract_error
from .policy import AGGREGATE_CONTENT, classify_error_code, require_content_diagnostic


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
                diagnostics.append(require_content_diagnostic(diagnostic))
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
    if isinstance(payload, dict) and "schema_version" in payload:
        if set(payload) == {"schema_version"}:
            raise ContractError(
                "E_SCHEMA_VERSION",
                "generated bundle requires a versioned body",
            )
        version = payload["schema_version"]
        if type(version) is not int:
            raise ContractError(
                "E_SCHEMA_VERSION",
                "generated bundle schema_version must be an integer",
            )
        if version == 2:
            from ..generation.assembler import validate_generated_bundle_v2

            return cast(dict[str, object], validate_generated_bundle_v2(payload, artifact_root))
        if version == 3:
            from ..generation.assembler import validate_generated_bundle_v3

            return cast(dict[str, object], validate_generated_bundle_v3(payload, artifact_root))
        if version != 1:
            raise ContractError(
                "E_SCHEMA_VERSION",
                "generated bundle schema_version is unsupported",
            )
    return validate_v1(payload, artifact_root)
