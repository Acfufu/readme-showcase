from __future__ import annotations

from ..pipeline_contracts import ContractError
from .diagnostics import Diagnostic


AGGREGATABLE_CODES = frozenset(
    {
        "E_CLAIM_COVERAGE",
        "E_CLAIM_LANGUAGE",
        "E_README_ACCESSIBILITY",
        "E_README_COMMAND",
        "E_README_LANGUAGE",
    }
)

SECURITY_CODES = frozenset(
    {
        "E_APPROVAL_FINGERPRINT",
        "E_BUNDLE_HASH",
        "E_CANDIDATE_DRIFT",
        "E_CLAIM_EVIDENCE",
        "E_DATASET_SPLIT_LEAK",
        "E_ENGINE_METADATA",
        "E_EVALUATION_DRIFT",
        "E_INPUT_PATH",
        "E_OUTPUT_PATH",
        "E_PATH",
        "E_PR_BASE",
        "E_PR_GIT",
        "E_PR_INDEX",
        "E_PR_PATH",
        "E_PR_WORKTREE",
        "E_PUBLISH_PATH",
        "E_REMOTE_PERMISSION",
        "E_SVG_UNSAFE",
    }
)


def diagnostic_from_contract_error(
    error: ContractError,
    *,
    path: str | None = None,
    line: int | None = None,
) -> Diagnostic:
    if error.code not in AGGREGATABLE_CODES:
        raise error
    return Diagnostic(
        code=error.code,
        severity="error",
        category="content",
        message=str(error),
        path=path,
        line=line,
    )


def contract_error_from_diagnostic(diagnostic: Diagnostic) -> ContractError:
    return ContractError(diagnostic.code, diagnostic.message)
