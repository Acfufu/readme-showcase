from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
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
        "E_VISUAL_COUNT",
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


_COUNT_CONSISTENCY_FIELDS = frozenset({"pass", "scene_count", "claim_count", "inventory_count", "mismatches", "sampled"})
_SAMPLED_NAMES = frozenset({"scene", "claim"})


@dataclass(frozen=True, slots=True)
class CountConsistency:
    """Reverse inventory projection: rendered scene counts must equal claim
    counts, and both must fit the evidence inventory.

    A mismatch that exceeds the inventory is a ``(sample)`` violation: the
    scene or claims describe more elements than the evidence can support.
    """

    pass_: bool
    scene_count: int
    claim_count: int
    inventory_count: int
    mismatches: tuple[str, ...] = ()
    sampled: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if type(self.pass_) is not bool:
            raise _schema_type("count consistency pass must be a boolean")
        for name in ("scene_count", "claim_count", "inventory_count"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise _schema_type(f"count consistency {name} must be a non-negative integer")
        if type(self.mismatches) is not tuple:
            if isinstance(self.mismatches, Sequence) and not isinstance(self.mismatches, (str, bytes)):
                object.__setattr__(self, "mismatches", tuple(self.mismatches))
            else:
                raise _schema_type("count consistency mismatches must be an array")
        if any(type(item) is not str or not item for item in self.mismatches):
            raise _schema_type("count consistency mismatches must contain non-empty strings")
        if self.mismatches != tuple(sorted(set(self.mismatches))):
            raise _schema_value("count consistency mismatches must be sorted and unique")
        if self.pass_ and self.mismatches:
            raise _schema_value("count consistency cannot pass with mismatches")
        if not self.pass_ and not self.mismatches:
            raise _schema_value("count consistency failure requires mismatches")
        if type(self.sampled) is not tuple:
            if isinstance(self.sampled, Sequence) and not isinstance(self.sampled, (str, bytes)):
                object.__setattr__(self, "sampled", tuple(self.sampled))
            else:
                raise _schema_type("count consistency sampled must be an array")
        if any(type(item) is not str or not item or item not in _SAMPLED_NAMES for item in self.sampled):
            raise _schema_type("count consistency sampled must contain only 'scene' or 'claim'")
        if self.sampled != tuple(sorted(set(self.sampled))):
            raise _schema_value("count consistency sampled must be sorted and unique")
        if self.pass_ and self.sampled:
            raise _schema_value("count consistency cannot pass with sampled violations")
        if self.sampled and not self.mismatches:
            raise _schema_value("count consistency sampled violations require mismatches")

    def as_dict(self) -> dict[str, object]:
        return {
            "pass": self.pass_,
            "scene_count": self.scene_count,
            "claim_count": self.claim_count,
            "inventory_count": self.inventory_count,
            "mismatches": list(self.mismatches),
            "sampled": list(self.sampled),
        }

    def canonical_bytes(self) -> bytes:
        return canonical_json_bytes(self.as_dict())


def validate_count_consistency(value: Any) -> CountConsistency:
    """Validate a closed count-consistency projection without reordering it."""

    if isinstance(value, CountConsistency):
        return value
    if not isinstance(value, Mapping):
        raise _schema_type("count consistency must be an object")
    raw = dict(value)
    if any(not isinstance(key, str) for key in raw):
        raise _schema_type("count consistency contains a non-string field name")
    unknown = sorted(set(raw) - _COUNT_CONSISTENCY_FIELDS)
    missing = sorted(_COUNT_CONSISTENCY_FIELDS - set(raw))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"count consistency contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"count consistency is missing field: {missing[0]}")
    return CountConsistency(
        raw["pass"],
        raw["scene_count"],
        raw["claim_count"],
        raw["inventory_count"],
        raw["mismatches"],
        raw["sampled"],
    )


@dataclass(frozen=True, slots=True)
class VisualGateReport:
    status: Literal["pass", "fail"]
    spec_sha256: str = ""
    scene_sha256: str = ""
    svg_sha256: str = ""
    diagnostics: tuple[VisualDiagnostic, ...] = ()
    count_consistency: CountConsistency | None = None

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
        if self.count_consistency is not None and not isinstance(self.count_consistency, CountConsistency):
            raise _schema_type("visual gate count_consistency must be a CountConsistency value")
        self._check_count_consistency()

    def _check_count_consistency(self) -> None:
        consistency = self.count_consistency
        if consistency is None:
            return
        count_failures = tuple(item for item in self.diagnostics if item.code == "E_VISUAL_COUNT")
        if consistency.pass_ and count_failures:
            raise _schema_value("count consistency pass conflicts with E_VISUAL_COUNT diagnostics")
        if not consistency.pass_ and not count_failures:
            raise _schema_value("count consistency failure requires an E_VISUAL_COUNT diagnostic")

    @classmethod
    def build(
        cls,
        spec_sha256: str = "",
        scene_sha256: str = "",
        svg_sha256: str = "",
        diagnostics: Iterable[VisualDiagnostic] = (),
        *,
        status: Literal["pass", "fail"] | None = None,
        count_consistency: CountConsistency | None = None,
    ) -> VisualGateReport:
        values = tuple(diagnostics)
        if any(not isinstance(item, VisualDiagnostic) for item in values):
            raise _schema_type("visual gate diagnostics must contain VisualDiagnostic values")
        expected = "fail" if any(item.severity == "error" for item in values) else "pass"
        return cls(status or expected, spec_sha256, scene_sha256, svg_sha256, values, count_consistency)

    @classmethod
    def from_diagnostics(
        cls,
        diagnostics: Iterable[VisualDiagnostic],
        *,
        spec_sha256: str = "",
        scene_sha256: str = "",
        svg_sha256: str = "",
        count_consistency: CountConsistency | None = None,
    ) -> VisualGateReport:
        return cls.build(spec_sha256, scene_sha256, svg_sha256, diagnostics, count_consistency=count_consistency)

    def as_dict(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "spec_sha256": self.spec_sha256,
            "scene_sha256": self.scene_sha256,
            "svg_sha256": self.svg_sha256,
            "diagnostics": [item.as_dict() for item in self.diagnostics],
            "count_consistency": None if self.count_consistency is None else self.count_consistency.as_dict(),
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
