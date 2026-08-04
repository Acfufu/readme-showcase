from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import ClassVar, Literal

from ...pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256


VisualSeverity = Literal["error", "warning", "info"]
VISUAL_DIAGNOSTIC_CODES = frozenset(
    {
        "E_VISUAL_SPEC_SIZE",
        "E_VISUAL_SPEC_ID",
        "E_VISUAL_SPEC_EDGE",
        "E_VISUAL_SPEC_EVIDENCE",
        "E_VISUAL_RESOURCE",
        "E_VISUAL_PATH",
        "E_VISUAL_GEOMETRY",
        "E_VISUAL_OVERLAP",
        "E_VISUAL_EDGE_INTERSECTION",
        "E_VISUAL_TEXT_FIT",
        "E_VISUAL_SVG_SECURITY",
        "E_VISUAL_DETERMINISM",
        "E_VISUAL_FINGERPRINT",
    }
)
VISUAL_ERROR_CODES = VISUAL_DIAGNOSTIC_CODES
VISUAL_SEVERITY_ORDER = MappingProxyType({"error": 0, "warning": 1, "info": 2})


def _schema_type(message: str) -> ContractError:
    return ContractError("E_SCHEMA_TYPE", message)


def _schema_value(message: str) -> ContractError:
    return ContractError("E_SCHEMA_VALUE", message)


@dataclass(frozen=True, slots=True)
class VisualDiagnostic:
    code: str
    severity: VisualSeverity
    path: str | None = None
    element_ids: tuple[str, ...] = ()
    message: str = ""

    def __post_init__(self) -> None:
        if type(self.code) is not str:
            raise _schema_type("visual diagnostic code must be a string")
        if self.code not in VISUAL_DIAGNOSTIC_CODES:
            raise _schema_value(f"unknown visual diagnostic code: {self.code}")
        if type(self.severity) is not str:
            raise _schema_type("visual diagnostic severity must be a string")
        if self.severity not in VISUAL_SEVERITY_ORDER:
            raise _schema_value(f"unsupported visual diagnostic severity: {self.severity}")
        if self.path is not None and type(self.path) is not str:
            raise _schema_type("visual diagnostic path must be a string or null")
        if type(self.element_ids) is not tuple:
            if isinstance(self.element_ids, Sequence) and not isinstance(self.element_ids, (str, bytes)):
                element_ids = tuple(self.element_ids)
                object.__setattr__(self, "element_ids", element_ids)
            else:
                raise _schema_type("visual diagnostic element_ids must be an array")
        if any(type(item) is not str or not item for item in self.element_ids):
            raise _schema_type("visual diagnostic element_ids must contain non-empty strings")
        if self.element_ids != tuple(sorted(set(self.element_ids))):
            raise _schema_value("visual diagnostic element_ids must be sorted and unique")
        if type(self.message) is not str:
            raise _schema_type("visual diagnostic message must be a string")

    def sort_key(self) -> tuple[object, ...]:
        return (
            VISUAL_SEVERITY_ORDER[self.severity],
            self.code,
            self.path or "",
            self.element_ids,
            self.message,
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "code": self.code,
            "severity": self.severity,
            "path": self.path,
            "element_ids": list(self.element_ids),
            "message": self.message,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def to_contract_error(self) -> ContractError:
        return contract_error_from_visual_diagnostic(self)

    as_contract_error = to_contract_error


@dataclass(frozen=True, slots=True)
class VisualGateReport:
    status: Literal["pass", "fail"]
    spec_sha256: str = ""
    scene_sha256: str = ""
    svg_sha256: str = ""
    diagnostics: tuple[VisualDiagnostic, ...] = ()

    schema_version: ClassVar[int] = 1

    def __post_init__(self) -> None:
        if type(self.status) is not str:
            raise _schema_type("visual gate status must be a string")
        if self.status not in {"pass", "fail"}:
            raise _schema_value(f"unsupported visual gate status: {self.status}")
        for name in ("spec_sha256", "scene_sha256", "svg_sha256"):
            if type(getattr(self, name)) is not str:
                raise _schema_type(f"visual gate {name} must be a string")
        if type(self.diagnostics) is not tuple:
            if isinstance(self.diagnostics, Sequence) and not isinstance(self.diagnostics, (str, bytes)):
                diagnostics = tuple(self.diagnostics)
                object.__setattr__(self, "diagnostics", diagnostics)
            else:
                raise _schema_type("visual gate diagnostics must be an array")
        if any(not isinstance(item, VisualDiagnostic) for item in self.diagnostics):
            raise _schema_type("visual gate diagnostics must contain VisualDiagnostic values")
        if len(set(self.diagnostics)) != len(self.diagnostics):
            raise _schema_value("visual gate diagnostics must be unique")
        ordered = tuple(sorted(self.diagnostics, key=VisualDiagnostic.sort_key))
        object.__setattr__(self, "diagnostics", ordered)
        expected = "fail" if any(item.severity == "error" for item in ordered) else "pass"
        if self.status != expected:
            raise _schema_value("visual gate status must match diagnostic severity")

    @classmethod
    def build(
        cls,
        spec_sha256: str = "",
        scene_sha256: str = "",
        svg_sha256: str = "",
        diagnostics: Iterable[VisualDiagnostic] = (),
        *,
        status: Literal["pass", "fail"] | None = None,
    ) -> VisualGateReport:
        values = tuple(diagnostics)
        if any(not isinstance(item, VisualDiagnostic) for item in values):
            raise _schema_type("visual gate diagnostics must contain VisualDiagnostic values")
        expected = "fail" if any(item.severity == "error" for item in values) else "pass"
        return cls(status or expected, spec_sha256, scene_sha256, svg_sha256, values)

    @classmethod
    def from_diagnostics(
        cls,
        diagnostics: Iterable[VisualDiagnostic],
        *,
        spec_sha256: str = "",
        scene_sha256: str = "",
        svg_sha256: str = "",
    ) -> VisualGateReport:
        return cls.build(spec_sha256, scene_sha256, svg_sha256, diagnostics)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "spec_sha256": self.spec_sha256,
            "scene_sha256": self.scene_sha256,
            "svg_sha256": self.svg_sha256,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())


def contract_error_from_visual_diagnostic(
    diagnostic: VisualDiagnostic,
) -> ContractError:
    if not isinstance(diagnostic, VisualDiagnostic):
        raise _schema_type("visual diagnostic conversion requires VisualDiagnostic")
    return ContractError(diagnostic.code, diagnostic.message)


def visual_diagnostic_from_contract_error(
    error: ContractError,
    *,
    path: str | None = None,
    element_ids: Iterable[str] = (),
    severity: VisualSeverity = "error",
) -> VisualDiagnostic:
    if not isinstance(error, ContractError):
        raise _schema_type("visual diagnostic conversion requires ContractError")
    return VisualDiagnostic(error.code, severity, path, tuple(element_ids), str(error))


def to_contract_error(diagnostic: VisualDiagnostic) -> ContractError:
    return contract_error_from_visual_diagnostic(diagnostic)
