"""Narrow public facade for the deterministic visual compiler."""

from collections.abc import Mapping

from ...pipeline_contracts import ContractError
from .compiler import CompiledVisual, compile_visual
from .diagnostics import (
    VISUAL_DIAGNOSTIC_CODES,
    VISUAL_ERROR_CODES,
    VISUAL_SEVERITY_ORDER,
    VisualDiagnostic,
    VisualGateReport,
    contract_error_from_visual_diagnostic,
    to_contract_error,
    visual_diagnostic_from_contract_error,
)
from .gates import validate_visual_gate_report
from .model import VisualSpec, validate_visual_spec
from .scene import Scene, validate_visual_scene


def load_compiled_visual(
    artifacts: Mapping[str, bytes],
    inventory_sha256: str,
) -> CompiledVisual:
    """Copy and validate an authoritative compiled artifact mapping."""

    if not isinstance(artifacts, Mapping):
        raise ContractError("E_SCHEMA_TYPE", "compiled visual artifacts must be a mapping")
    if type(inventory_sha256) is not str:
        raise ContractError("E_SCHEMA_TYPE", "compiled visual inventory identity must be an immutable string")
    return CompiledVisual(dict(artifacts), inventory_sha256)


__all__ = [
    "compile_visual",
    "validate_visual_spec",
    "validate_visual_scene",
    "validate_visual_gate_report",
    "load_compiled_visual",
    "CompiledVisual",
    "VisualSpec",
    "Scene",
    "VisualDiagnostic",
    "VisualGateReport",
]
