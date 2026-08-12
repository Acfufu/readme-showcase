"""Advisory evaluation helpers with v1-compatible pipeline adapters."""

from .contract import (
    ADVISORY_METRIC_NAMES,
    validate_advisory_metrics,
    validate_metric,
)
from .incident_log import (
    INCIDENT_LOG_ERROR_CODE,
    LESSONS_PENDING_NAME,
    LESSONS_PENDING_SCHEMA_VERSION,
    append_failure_entry,
    record_identity_override,
)
from .metrics import (
    compute_advisory_metrics,
    empty_advisory_metrics,
    evaluate_v1_legacy,
    evaluate_v2_advisory,
)

__all__ = [
    "ADVISORY_METRIC_NAMES",
    "INCIDENT_LOG_ERROR_CODE",
    "LESSONS_PENDING_NAME",
    "LESSONS_PENDING_SCHEMA_VERSION",
    "append_failure_entry",
    "compute_advisory_metrics",
    "empty_advisory_metrics",
    "evaluate_v1_legacy",
    "evaluate_v2_advisory",
    "record_identity_override",
    "validate_advisory_metrics",
    "validate_metric",
]
