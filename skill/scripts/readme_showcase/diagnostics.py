from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Literal

from ..pipeline_contracts import canonical_json_bytes, canonical_sha256


Severity = Literal["error", "warning", "info"]
Category = Literal["security", "contract", "content", "behavior", "editorial"]
_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}


@dataclass(frozen=True, slots=True)
class Diagnostic:
    code: str
    severity: Severity
    category: Category
    message: str
    path: str | None = None
    line: int | None = None
    related_ids: tuple[str, ...] = ()
    suggested_action: str | None = None

    def __post_init__(self) -> None:
        if self.severity not in _SEVERITY_ORDER:
            raise ValueError(f"unsupported diagnostic severity: {self.severity}")
        if self.line is not None and (type(self.line) is not int or self.line < 1):
            raise ValueError("diagnostic line must be a positive integer")

    def sort_key(self) -> tuple[object, ...]:
        return (
            _SEVERITY_ORDER[self.severity],
            self.code,
            self.path or "",
            self.line or 0,
            self.message,
            self.related_ids,
            self.suggested_action or "",
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "category": self.category,
            "code": self.code,
            "line": self.line,
            "message": self.message,
            "path": self.path,
            "related_ids": list(self.related_ids),
            "severity": self.severity,
            "suggested_action": self.suggested_action,
        }


@dataclass(frozen=True, slots=True)
class DiagnosticReport:
    status: Literal["pass", "fail"]
    diagnostics: tuple[Diagnostic, ...]

    @classmethod
    def build(cls, diagnostics: Iterable[Diagnostic]) -> DiagnosticReport:
        normalized = tuple(sorted(set(diagnostics), key=Diagnostic.sort_key))
        status = "fail" if any(item.severity == "error" for item in normalized) else "pass"
        return cls(status=status, diagnostics=normalized)

    def as_dict(self) -> dict[str, object]:
        return {
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "status": self.status,
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())

    def sha256(self) -> str:
        return canonical_sha256(self.as_dict())
