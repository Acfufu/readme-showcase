from __future__ import annotations

import copy
import json
import re
import shlex
from pathlib import Path, PurePosixPath
from typing import Any, Mapping

from ...pipeline_contracts import ContractError, canonical_json_bytes, read_regular_bytes
from ..evaluation.contract import validate_advisory_metrics


COMMAND_OBSERVATION_SCHEMA_VERSION = 1
EVALUATION_REPORT_SCHEMA_VERSION = 2
MAX_COMMAND_BYTES = 4_096
MAX_ARG_BYTES = 1_024
MAX_ARGV = 64
MAX_INPUT_HASHES = 128
MAX_OUTPUT_BYTES = 1_048_576
MAX_TIMEOUT_MS = 120_000
MAX_OBSERVATION_JSON_BYTES = 1_048_576

_HEX_256 = re.compile(r"[0-9a-f]{64}\Z")
_BASE_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_COMMAND_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_UTC_TIME = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
_SECRET_ASSIGNMENT = re.compile(r"(?i)(?:password|passwd|secret|token|api[_-]?key)\s*=")

_OBSERVATION_FIELDS = {
    "schema_version", "command_id", "command", "argv", "cwd", "exit_code",
    "stdout_sha256", "stderr_sha256", "stdout_bytes", "stderr_bytes",
    "observed_at_base_sha", "input_hashes", "source_provenance", "runner",
    "observed_at", "timeout_ms", "max_output_bytes", "verification",
}
_RUNNER_FIELDS = {"id", "controlled", "clean_environment", "network"}
_HASH_FIELDS = {"path", "sha256"}
_REPORT_FIELDS = {
    "schema_version", "status", "decision_basis", "bundle_sha256",
    "hard_gate", "advisory", "behavior", "behavior_required",
}


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_float(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise ContractError("E_SCHEMA_KEY_TYPE", f"{path} contains a non-string key")
            _reject_float(item, f"{path}.{key}")


def _strict_object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_OBSERVATION_SCHEMA", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError("E_OBSERVATION_SCHEMA", f"{context} has unknown field {unknown[0]}")
    if missing:
        raise ContractError("E_OBSERVATION_SCHEMA", f"{context} is missing {missing[0]}")
    return value


def normalize_observation_cwd(value: Any) -> str:
    if value == ".":
        return "."
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or "\x00" in value:
        raise ContractError("E_OBSERVATION_UNSAFE", "observation cwd must be a normalized POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("~/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ContractError("E_OBSERVATION_UNSAFE", "observation cwd must be a normalized POSIX relative path")
    if len(value.encode("utf-8")) > 4_096:
        raise ContractError("E_OBSERVATION_UNSAFE", "observation cwd exceeds path limit")
    return path.as_posix()


def _validate_hash_binding(value: Any, context: str) -> dict[str, str]:
    item = _strict_object(value, _HASH_FIELDS, context)
    path = normalize_observation_cwd(item["path"])
    if path == ".":
        raise ContractError("E_OBSERVATION_SCHEMA", f"{context}.path must name a file")
    digest = item["sha256"]
    if not isinstance(digest, str) or not _HEX_256.fullmatch(digest):
        raise ContractError("E_OBSERVATION_SCHEMA", f"{context}.sha256 must be lowercase SHA-256")
    return {"path": path, "sha256": digest}


def validate_command_observation(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    value = _strict_object(payload, _OBSERVATION_FIELDS, "command observation")
    if type(value["schema_version"]) is not int or value["schema_version"] != COMMAND_OBSERVATION_SCHEMA_VERSION:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation requires schema_version 1")
    command_id = value["command_id"]
    if not isinstance(command_id, str) or not _COMMAND_ID.fullmatch(command_id):
        raise ContractError("E_OBSERVATION_SCHEMA", "command_id is invalid")
    command = value["command"]
    if not isinstance(command, str) or not command or "\x00" in command or len(command.encode("utf-8")) > MAX_COMMAND_BYTES:
        raise ContractError("E_OBSERVATION_SCHEMA", "command is invalid or oversized")
    if _SECRET_ASSIGNMENT.search(command):
        raise ContractError("E_OBSERVATION_UNSAFE", "secret-bearing command must not enter observation provenance")
    argv = value["argv"]
    if (
        not isinstance(argv, list) or not argv or len(argv) > MAX_ARGV
        or any(not isinstance(arg, str) or not arg or "\x00" in arg or len(arg.encode("utf-8")) > MAX_ARG_BYTES for arg in argv)
    ):
        raise ContractError("E_OBSERVATION_SCHEMA", "argv is invalid or oversized")
    if command != shlex.join(argv):
        raise ContractError("E_OBSERVATION_BINDING", "command text does not match exact argv")
    cwd = normalize_observation_cwd(value["cwd"])
    exit_code = value["exit_code"]
    if type(exit_code) is not int or exit_code < -255 or exit_code > 255:
        raise ContractError("E_OBSERVATION_SCHEMA", "exit_code must be a bounded integer")
    for field in ("stdout_sha256", "stderr_sha256"):
        if not isinstance(value[field], str) or not _HEX_256.fullmatch(value[field]):
            raise ContractError("E_OBSERVATION_SCHEMA", f"{field} must be lowercase SHA-256")
    timeout_ms = value["timeout_ms"]
    output_limit = value["max_output_bytes"]
    if type(timeout_ms) is not int or timeout_ms < 1 or timeout_ms > MAX_TIMEOUT_MS:
        raise ContractError("E_OBSERVATION_SCHEMA", "timeout_ms is outside policy bounds")
    if type(output_limit) is not int or output_limit < 1 or output_limit > MAX_OUTPUT_BYTES:
        raise ContractError("E_OBSERVATION_SCHEMA", "max_output_bytes is outside policy bounds")
    for field in ("stdout_bytes", "stderr_bytes"):
        count = value[field]
        if type(count) is not int or count < 0 or count > output_limit:
            raise ContractError("E_OBSERVATION_SCHEMA", f"{field} exceeds declared output bound")
    if not isinstance(value["observed_at_base_sha"], str) or not _BASE_SHA.fullmatch(value["observed_at_base_sha"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "observed_at_base_sha is invalid")
    raw_inputs = value["input_hashes"]
    if not isinstance(raw_inputs, list) or len(raw_inputs) > MAX_INPUT_HASHES:
        raise ContractError("E_OBSERVATION_SCHEMA", "input_hashes is invalid or oversized")
    inputs = [_validate_hash_binding(item, f"input_hashes[{index}]") for index, item in enumerate(raw_inputs)]
    paths = [item["path"] for item in inputs]
    if paths != sorted(set(paths)):
        raise ContractError("E_OBSERVATION_SCHEMA", "input_hashes must be uniquely path ordered")
    source = _validate_hash_binding(value["source_provenance"], "source_provenance")
    runner = _strict_object(value["runner"], _RUNNER_FIELDS, "runner")
    if not isinstance(runner["id"], str) or not _COMMAND_ID.fullmatch(runner["id"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "runner.id is invalid")
    if type(runner["controlled"]) is not bool or type(runner["clean_environment"]) is not bool:
        raise ContractError("E_OBSERVATION_SCHEMA", "runner control markers must be booleans")
    if runner["network"] not in {"blocked-by-allowlist", "unknown"}:
        raise ContractError("E_OBSERVATION_SCHEMA", "runner.network is invalid")
    if not isinstance(value["observed_at"], str) or not _UTC_TIME.fullmatch(value["observed_at"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "observed_at must be second-precision UTC")
    verification = value["verification"]
    if verification not in {"imported-unverified", "verified"}:
        raise ContractError("E_OBSERVATION_SCHEMA", "verification is invalid")
    if verification == "verified" and not (
        runner["controlled"] is True
        and runner["clean_environment"] is True
        and runner["network"] == "blocked-by-allowlist"
    ):
        raise ContractError("E_OBSERVATION_BINDING", "verified observation lacks controlled clean provenance")
    normalized = copy.deepcopy(value)
    normalized["cwd"] = cwd
    normalized["input_hashes"] = inputs
    normalized["source_provenance"] = source
    return normalized


def read_command_observation(path: Path) -> dict[str, Any]:
    raw = read_regular_bytes(
        path, maximum=MAX_OBSERVATION_JSON_BYTES,
        path_code="E_OBSERVATION_UNSAFE", size_code="E_OBSERVATION_SCHEMA",
    )
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation must be canonical JSON") from exc
    value = validate_command_observation(payload)
    if canonical_json_bytes(value) != raw:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation bytes must be canonical JSON")
    return value


def validate_behavior_result(payload: Any) -> dict[str, Any]:
    _reject_float(payload, "behavior")
    fields = {"status", "reasons", "commands", "observable_commands", "total_commands"}
    if not isinstance(payload, dict) or set(payload) != fields:
        raise ContractError("E_EVALUATION_REPORT", "behavior result fields are invalid")
    if payload["status"] not in {"pass", "fail", "unverified", "unsupported", "not-observed"}:
        raise ContractError("E_EVALUATION_REPORT", "behavior status is invalid")
    reasons = payload["reasons"]
    if not isinstance(reasons, list) or reasons != sorted(set(reasons)) or any(not isinstance(reason, str) or not reason for reason in reasons):
        raise ContractError("E_EVALUATION_REPORT", "behavior reasons are invalid")
    commands = payload["commands"]
    if not isinstance(commands, list) or commands != sorted(commands, key=lambda item: item.get("command_id", "")):
        raise ContractError("E_EVALUATION_REPORT", "behavior commands are not canonically ordered")
    command_fields = {"command_id", "status", "exit_code", "verification", "observation_sha256", "reasons"}
    for command in commands:
        if not isinstance(command, dict) or set(command) != command_fields:
            raise ContractError("E_EVALUATION_REPORT", "behavior command fields are invalid")
        if not isinstance(command["command_id"], str) or not _COMMAND_ID.fullmatch(command["command_id"]):
            raise ContractError("E_EVALUATION_REPORT", "behavior command_id is invalid")
        if command["status"] not in {"pass", "fail", "unverified", "unsupported", "not-observed"}:
            raise ContractError("E_EVALUATION_REPORT", "behavior command status is invalid")
        if command["exit_code"] is not None and type(command["exit_code"]) is not int:
            raise ContractError("E_EVALUATION_REPORT", "behavior exit_code is invalid")
        if command["verification"] not in {None, "imported-unverified", "verified"}:
            raise ContractError("E_EVALUATION_REPORT", "behavior verification is invalid")
        if command["observation_sha256"] is not None and (
            not isinstance(command["observation_sha256"], str) or not _HEX_256.fullmatch(command["observation_sha256"])
        ):
            raise ContractError("E_EVALUATION_REPORT", "behavior observation hash is invalid")
        command_reasons = command["reasons"]
        if not isinstance(command_reasons, list) or command_reasons != sorted(set(command_reasons)):
            raise ContractError("E_EVALUATION_REPORT", "behavior command reasons are invalid")
    covered, total = payload["observable_commands"], payload["total_commands"]
    if type(covered) is not int or type(total) is not int or covered < 0 or covered > total or total != len(commands):
        raise ContractError("E_EVALUATION_REPORT", "behavior command counts are invalid")
    if payload["status"] == "pass" and (covered != total or total == 0):
        raise ContractError("E_EVALUATION_REPORT", "behavior pass requires all observed commands")
    return copy.deepcopy(payload)


def validate_evaluation_report_v2(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    if not isinstance(payload, dict) or set(payload) != _REPORT_FIELDS:
        raise ContractError("E_EVALUATION_REPORT", "evaluation report v2 fields are invalid")
    if type(payload["schema_version"]) is not int or payload["schema_version"] != EVALUATION_REPORT_SCHEMA_VERSION:
        raise ContractError("E_EVALUATION_REPORT", "evaluation report requires schema_version 2")
    if payload["status"] not in {"pass", "fail"} or payload["decision_basis"] != "hard-gates-with-configured-behavior":
        raise ContractError("E_EVALUATION_REPORT", "evaluation decision fields are invalid")
    if not isinstance(payload["bundle_sha256"], str) or not _HEX_256.fullmatch(payload["bundle_sha256"]):
        raise ContractError("E_EVALUATION_REPORT", "bundle_sha256 is invalid")
    hard_gate = payload["hard_gate"]
    if not isinstance(hard_gate, dict) or set(hard_gate) != {"status", "findings"} or hard_gate["status"] not in {"pass", "fail"}:
        raise ContractError("E_EVALUATION_REPORT", "hard_gate is invalid")
    findings = hard_gate["findings"]
    if not isinstance(findings, list) or any(
        not isinstance(item, dict) or set(item) != {"code", "message"}
        or not all(isinstance(item[key], str) and item[key] for key in ("code", "message"))
        for item in findings
    ):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate findings are invalid")
    if findings != sorted(findings, key=lambda item: (item["code"], item["message"])):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate findings are not canonically ordered")
    if (hard_gate["status"] == "pass") != (not findings):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate status and findings disagree")
    advisory = validate_advisory_metrics(payload["advisory"])
    behavior = validate_behavior_result(payload["behavior"])
    if type(payload["behavior_required"]) is not bool:
        raise ContractError("E_EVALUATION_REPORT", "behavior_required must be boolean")
    expected_status = "pass" if hard_gate["status"] == "pass" and (not payload["behavior_required"] or behavior["status"] == "pass") else "fail"
    if payload["status"] != expected_status:
        raise ContractError("E_EVALUATION_REPORT", "report status does not match gates")
    normalized = copy.deepcopy(payload)
    normalized["advisory"] = advisory
    normalized["behavior"] = behavior
    return normalized
