from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from typing import TextIO


LOG_FIELDS = (
    "event",
    "run_id",
    "stage",
    "status",
    "duration_ms",
    "input_sha256",
    "output_sha256",
)


@dataclass(frozen=True)
class StageLogger:
    format: str = "text"
    verbosity: str = "normal"
    stream: TextIO = sys.stderr

    def emit(
        self,
        event: str,
        *,
        run_id: str,
        stage: str,
        status: str,
        duration_ms: int = 0,
        input_sha256: str | None = None,
        output_sha256: str | None = None,
    ) -> None:
        if self.verbosity == "quiet" or (self.verbosity != "debug" and event.endswith("started")):
            return
        record = dict(
            zip(
                LOG_FIELDS,
                (event, run_id, stage, status, duration_ms, input_sha256, output_sha256),
                strict=True,
            )
        )
        if self.format == "json":
            print(json.dumps(record, sort_keys=True, separators=(",", ":")), file=self.stream)
        else:
            print(f"{event} stage={stage} status={status} duration_ms={duration_ms}", file=self.stream)
