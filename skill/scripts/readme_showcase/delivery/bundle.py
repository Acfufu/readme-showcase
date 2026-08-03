from __future__ import annotations

import hashlib
from typing import Mapping, Sequence

from ...pipeline_contracts import canonical_sha256


def build_delivery_result(
    *,
    repository: str,
    base_sha: str,
    base_tree: str,
    candidate_tree: str,
    candidate_sha256: str,
    changes: Sequence[Mapping[str, object]],
    diff: bytes,
) -> dict[str, object]:
    """Build the deterministic, local-only v2 delivery preparation receipt."""

    result: dict[str, object] = {
        "schema_version": 2,
        "status": "prepared",
        "target": {
            "repository": repository,
            "base_sha": base_sha,
            "base_tree": base_tree,
        },
        "candidate_sha256": candidate_sha256,
        "candidate_tree": candidate_tree,
        "candidate_files": [dict(change) for change in changes],
        "diff_sha256": hashlib.sha256(diff).hexdigest(),
        "diff_hex": diff.hex(),
        "publish_authority": False,
    }
    result["fingerprint"] = canonical_sha256(result)
    return result


__all__ = ["build_delivery_result"]
