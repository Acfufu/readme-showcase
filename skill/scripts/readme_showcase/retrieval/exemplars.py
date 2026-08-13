"""Exemplar collection loading for the production retrieval path.

Exemplars are a SEPARATE collection from the retrieval manifest: they never
enter manifest["records"] (validate_dataset_manifest pins purpose ==
"retrieval-only" and the manifest bytes are hash-locked). Production retrieval
appends them to the ranker input collection only.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any

from ..contracts.retrieval import validate_exemplar_record_v1

_contracts = importlib.import_module(
    "skill.scripts.pipeline_contracts" if (__package__ or "").startswith("skill.") else "pipeline_contracts"
)
ContractError = _contracts.ContractError


def load_exemplar_records(exemplars_dir: Path) -> list[dict[str, Any]]:
    """Validate every dataset/retrieval/exemplars/*.json and return them sorted by record_id."""
    if not exemplars_dir.is_dir():
        return []
    files = sorted(exemplars_dir.glob("*.json"))
    records = [
        validate_exemplar_record_v1(json.loads(path.read_text(encoding="utf-8")))
        for path in files
    ]
    identifiers = [record["record_id"] for record in records]
    if len(set(identifiers)) != len(identifiers):
        raise ContractError("E_DATASET_DUPLICATE_ID", "exemplar records contain duplicate record_id")
    return sorted(records, key=lambda record: record["record_id"])
