from __future__ import annotations

from typing import Mapping, Sequence

from ...pipeline_contracts import ContractError, canonical_sha256
from ..contracts.evaluation import validate_behavior_result, validate_command_observation, validate_input_hashes


def evaluate_behavior(
    commands: Sequence[str],
    observations: Sequence[Mapping[str, object]],
    *,
    base_sha: str,
    input_hashes: Mapping[str, str],
    cwd: str = ".",
    trusted_observation_sha256s: frozenset[str] = frozenset(),
) -> dict[str, object]:
    """Validate imported envelopes. This function never executes their commands."""
    if not isinstance(commands, Sequence) or isinstance(commands, (str, bytes)) or any(not isinstance(x, str) or not x for x in commands):
        raise ContractError("E_OBSERVATION_BINDING", "planned commands are invalid")
    planned = sorted(set(commands))
    if len(planned) != len(commands):
        raise ContractError("E_OBSERVATION_BINDING", "planned commands must be unique")
    expected_inputs = validate_input_hashes(dict(input_hashes))
    by_command: dict[str, dict[str, object]] = {}
    for raw in observations:
        observation = validate_command_observation(raw)
        command = observation["command"]
        if command not in planned:
            raise ContractError("E_OBSERVATION_BINDING", "observation command differs from plan")
        if command in by_command:
            raise ContractError("E_OBSERVATION_BINDING", "duplicate observation command")
        if observation["observed_at_base_sha"] != base_sha:
            raise ContractError("E_OBSERVATION_BINDING", "observation base SHA differs from bundle")
        if observation["cwd"] != cwd:
            raise ContractError("E_OBSERVATION_BINDING", "observation cwd differs from evaluation root")
        if observation["input_hashes"] != expected_inputs:
            raise ContractError("E_OBSERVATION_BINDING", "observation input hashes differ from bundle")
        by_command[command] = observation

    command_results: list[dict[str, object]] = []
    for index, command in enumerate(planned):
        observation = by_command.get(command)
        if observation is None:
            command_results.append({
                "command_id": f"plan:{index}", "status": "not-observed",
                "exit_code": None, "verification": None,
                "observation_sha256": None,
                "reasons": [f"observation-missing:plan:{index}"],
            })
            continue
        digest = canonical_sha256(observation)
        trusted = digest in trusted_observation_sha256s
        verified = trusted and observation["verification"] == "verified" and observation["runner"] == "controlled-ci"
        if not verified:
            reason = "observation-receipt-untrusted" if not trusted else "observation-provenance-unverified"
            command_results.append({
                "command_id": observation["command_id"], "status": "unverified",
                "exit_code": observation["exit_code"], "verification": "imported-unverified",
                "observation_sha256": digest,
                "reasons": [f"{reason}:{observation['command_id']}"],
            })
            continue
        passed = observation["exit_code"] == 0
        command_results.append({
            "command_id": observation["command_id"], "status": "pass" if passed else "fail",
            "exit_code": observation["exit_code"], "verification": "verified",
            "observation_sha256": digest,
            "reasons": [] if passed else [f"observation-nonzero-exit:{observation['command_id']}"],
        })

    command_results.sort(key=lambda item: str(item["command_id"]))
    statuses = {str(item["status"]) for item in command_results}
    covered = sum(item["status"] == "pass" for item in command_results)
    if command_results and statuses == {"pass"}:
        status = "pass"
    elif "fail" in statuses:
        status = "fail"
    elif "unverified" in statuses:
        status = "unverified"
    elif "unsupported" in statuses:
        status = "unsupported"
    else:
        status = "not-observed"
    return validate_behavior_result({
        "status": status,
        "reasons": sorted({reason for item in command_results for reason in item["reasons"]}),
        "commands": command_results,
        "observable_commands": covered,
        "total_commands": len(command_results),
    })
