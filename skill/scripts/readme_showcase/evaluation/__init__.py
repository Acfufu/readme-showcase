"""Advisory evaluation helpers with v1-compatible pipeline adapters."""

from .contract import (
    ADVISORY_METRIC_NAMES,
    validate_advisory_metrics,
    validate_metric,
)
from .metrics import (
    compute_advisory_metrics,
    empty_advisory_metrics,
    evaluate_v1_legacy,
    evaluate_v2_advisory,
)

__all__ = [
    "ADVISORY_METRIC_NAMES",
    "compute_advisory_metrics",
    "empty_advisory_metrics",
    "evaluate_v1_legacy",
    "evaluate_v2_advisory",
    "validate_advisory_metrics",
    "validate_metric",
]
