from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from typing import Any

from ..contracts.common import ContractError, normalize_posix_path
from ..contracts.evidence import build_fact
from .graph import EvidenceGraph


_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_V1_FIELDS = {"fact_id", "kind", "path", "evidence_sha256"}
def adapt_v1_file_fact(fact: Mapping[str, Any]) -> dict[str, Any]:
    legacy = copy.deepcopy(dict(fact))
    if set(legacy) != _V1_FIELDS or legacy.get("kind") != "repository-file":
        raise ContractError("E_V1_EVIDENCE", "v1 file fact fields or kind are invalid")
    path = normalize_posix_path(legacy.get("path"))
    if legacy.get("fact_id") != f"file:{legacy.get('path')}":
        raise ContractError("E_V1_EVIDENCE", "v1 file fact_id does not match path")
    digest = legacy.get("evidence_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise ContractError("E_V1_EVIDENCE", "v1 evidence_sha256 must be lowercase SHA-256")
    return build_fact(
        kind="file-presence",
        path=path,
        locator=None,
        semantic_key="presence",
        value=True,
        source_sha256=digest,
        confidence="observed",
    )


def adapt_v1_repository_evidence(packet: Mapping[str, Any]) -> dict[str, Any]:
    legacy = copy.deepcopy(dict(packet))
    if legacy.get("schema_version") != 1 or not isinstance(legacy.get("facts"), list):
        raise ContractError("E_V1_EVIDENCE", "v1 repository evidence packet is invalid")
    return EvidenceGraph(adapt_v1_file_fact(fact) for fact in legacy["facts"]).to_dict()


def adapt_verified_command_observation(
    observation: Mapping[str, Any],
    *,
    path: str,
    source_bytes: bytes | None = None,
    source_sha256: str | None = None,
) -> dict[str, Any]:
    """Copy an upstream-verified envelope into Evidence v2 without executing it."""
    if observation.get("verification") != "verified":
        raise ValueError("command observation must already be verified")
    command_id = observation.get("command_id")
    if not isinstance(command_id, str) or not command_id:
        raise ValueError("verified command observation command_id is invalid")
    value = copy.deepcopy(dict(observation))
    return build_fact(
        kind="command-observation",
        path=path,
        locator={"json_pointer": ""},
        semantic_key=command_id,
        value=value,
        source_bytes=source_bytes,
        source_sha256=source_sha256,
        confidence="observed",
    )


adapt_command_observation = adapt_verified_command_observation


adapt_v1_fact = adapt_v1_file_fact
v1_file_fact_to_v2 = adapt_v1_file_fact
