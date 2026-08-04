from __future__ import annotations

import copy
import json
import re
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import ContractError, canonical_json_bytes, read_regular_bytes
from ..evaluation.contract import validate_advisory_metrics, validate_metric


COMMAND_OBSERVATION_SCHEMA_VERSION = 1
EVALUATION_REPORT_SCHEMA_VERSION = 2
EVALUATION_REPORT_V3_SCHEMA_VERSION = 3
MAX_COMMAND_BYTES = 4_096
MAX_INPUT_HASHES = 128
MAX_OBSERVATION_JSON_BYTES = 1_048_576

_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_BASE_SHA = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?\Z")
_COMMAND_ID = re.compile(r"[a-z0-9][a-z0-9_.:-]{0,127}\Z")
_OBSERVATION_FIELDS = {
    "schema_version", "command_id", "command", "cwd", "exit_code",
    "stdout_sha256", "stderr_sha256", "observed_at_base_sha",
    "input_hashes", "runner", "verification",
}
_REPORT_FIELDS = {
    "schema_version", "status", "decision_basis", "bundle_sha256",
    "hard_gate", "advisory", "behavior", "behavior_required",
}
_COMPILED_REPORT_FIELDS = {
    "gate_pass", "element_evidence_coverage", "variant_completeness",
    "determinism", "resource_budgets",
}
_REPORT_V3_FIELDS = {
    "schema_version", "status", "decision_basis", "bundle_sha256",
    "compiled_fingerprint", "hard_gate", "compiled", "advisory",
    "behavior", "behavior_required",
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


def _strict_object(value: Any, fields: set[str], context: str, code: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError(code, f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        raise ContractError(code, f"{context} has unknown field {unknown[0]}")
    if missing:
        raise ContractError(code, f"{context} is missing {missing[0]}")
    return value


def normalize_observation_cwd(value: Any) -> str:
    if value == ".":
        return "."
    if not isinstance(value, str) or not value or value != value.strip() or "\\" in value or "\x00" in value:
        raise ContractError("E_OBSERVATION_BINDING", "observation cwd must be a normalized POSIX relative path")
    path = PurePosixPath(value)
    if path.is_absolute() or value.startswith("~/") or any(part in {"", ".", ".."} for part in value.split("/")):
        raise ContractError("E_OBSERVATION_BINDING", "observation cwd must be a normalized POSIX relative path")
    return path.as_posix()


def validate_input_hashes(payload: Any) -> dict[str, str]:
    if not isinstance(payload, dict) or len(payload) > MAX_INPUT_HASHES:
        raise ContractError("E_OBSERVATION_SCHEMA", "input_hashes must be a bounded object")
    result: dict[str, str] = {}
    for path, digest in sorted(payload.items()):
        normalized = normalize_observation_cwd(path)
        if normalized == "." or not isinstance(digest, str) or not _SHA256.fullmatch(digest):
            raise ContractError("E_OBSERVATION_SCHEMA", "input_hashes contains an invalid binding")
        result[normalized] = digest
    if list(payload) != list(result):
        raise ContractError("E_OBSERVATION_SCHEMA", "input_hashes must use canonical path order")
    return result


def validate_command_observation(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    value = _strict_object(payload, _OBSERVATION_FIELDS, "command observation", "E_OBSERVATION_SCHEMA")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation requires schema_version 1")
    if not isinstance(value["command_id"], str) or not _COMMAND_ID.fullmatch(value["command_id"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "command_id is invalid")
    command = value["command"]
    if not isinstance(command, str) or not command or "\x00" in command or len(command.encode()) > MAX_COMMAND_BYTES:
        raise ContractError("E_OBSERVATION_SCHEMA", "command is invalid or oversized")
    cwd = normalize_observation_cwd(value["cwd"])
    if type(value["exit_code"]) is not int or not -255 <= value["exit_code"] <= 255:
        raise ContractError("E_OBSERVATION_SCHEMA", "exit_code must be a bounded integer")
    for field in ("stdout_sha256", "stderr_sha256"):
        if not isinstance(value[field], str) or not _SHA256.fullmatch(value[field]):
            raise ContractError("E_OBSERVATION_SCHEMA", f"{field} must be lowercase SHA-256")
    if not isinstance(value["observed_at_base_sha"], str) or not _BASE_SHA.fullmatch(value["observed_at_base_sha"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "observed_at_base_sha is invalid")
    inputs = validate_input_hashes(value["input_hashes"])
    if not isinstance(value["runner"], str) or not _COMMAND_ID.fullmatch(value["runner"]):
        raise ContractError("E_OBSERVATION_SCHEMA", "runner is invalid")
    if value["verification"] not in {"imported-unverified", "verified"}:
        raise ContractError("E_OBSERVATION_SCHEMA", "verification is invalid")
    result = copy.deepcopy(value)
    result["cwd"] = cwd
    result["input_hashes"] = inputs
    return result


def read_command_observation(path: Path) -> dict[str, Any]:
    raw = read_regular_bytes(path, maximum=MAX_OBSERVATION_JSON_BYTES,
                             path_code="E_OBSERVATION_PATH", size_code="E_OBSERVATION_SCHEMA")
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation must be canonical JSON") from exc
    value = validate_command_observation(payload)
    if canonical_json_bytes(value) != raw:
        raise ContractError("E_OBSERVATION_SCHEMA", "command observation bytes must be canonical JSON")
    return value


def validate_behavior_result(payload: Any) -> dict[str, Any]:
    fields = {"status", "reasons", "commands", "observable_commands", "total_commands"}
    value = _strict_object(payload, fields, "behavior result", "E_EVALUATION_REPORT")
    statuses = {"pass", "fail", "unverified", "unsupported", "not-observed"}
    if value["status"] not in statuses:
        raise ContractError("E_EVALUATION_REPORT", "behavior status is invalid")
    reasons = value["reasons"]
    if not isinstance(reasons, list) or reasons != sorted(set(reasons)) or any(not isinstance(x, str) or not x for x in reasons):
        raise ContractError("E_EVALUATION_REPORT", "behavior reasons are invalid")
    commands = value["commands"]
    command_fields = {"command_id", "status", "exit_code", "verification", "observation_sha256", "reasons"}
    if not isinstance(commands, list) or commands != sorted(commands, key=lambda item: item.get("command_id", "")):
        raise ContractError("E_EVALUATION_REPORT", "behavior commands are not canonically ordered")
    for item in commands:
        if not isinstance(item, dict) or set(item) != command_fields or item["status"] not in statuses:
            raise ContractError("E_EVALUATION_REPORT", "behavior command is invalid")
        if item["exit_code"] is not None and type(item["exit_code"]) is not int:
            raise ContractError("E_EVALUATION_REPORT", "behavior exit_code is invalid")
        if item["verification"] not in {None, "imported-unverified", "verified"}:
            raise ContractError("E_EVALUATION_REPORT", "behavior verification is invalid")
        digest = item["observation_sha256"]
        if digest is not None and (not isinstance(digest, str) or not _SHA256.fullmatch(digest)):
            raise ContractError("E_EVALUATION_REPORT", "behavior observation hash is invalid")
        if not isinstance(item["reasons"], list) or item["reasons"] != sorted(set(item["reasons"])):
            raise ContractError("E_EVALUATION_REPORT", "behavior command reasons are invalid")
    covered, total = value["observable_commands"], value["total_commands"]
    if type(covered) is not int or type(total) is not int or not 0 <= covered <= total or total != len(commands):
        raise ContractError("E_EVALUATION_REPORT", "behavior counts are invalid")
    if value["status"] == "pass" and (total == 0 or covered != total):
        raise ContractError("E_EVALUATION_REPORT", "behavior pass requires trusted observations")
    return copy.deepcopy(value)


def validate_evaluation_report_v2(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    value = _strict_object(payload, _REPORT_FIELDS, "evaluation report", "E_EVALUATION_REPORT")
    if value["schema_version"] != 2 or value["status"] not in {"pass", "fail"} or value["decision_basis"] != "hard-gates-with-configured-behavior":
        raise ContractError("E_EVALUATION_REPORT", "evaluation report decision fields are invalid")
    if not isinstance(value["bundle_sha256"], str) or not _SHA256.fullmatch(value["bundle_sha256"]):
        raise ContractError("E_EVALUATION_REPORT", "bundle_sha256 is invalid")
    hard_gate = value["hard_gate"]
    if not isinstance(hard_gate, dict) or set(hard_gate) != {"status", "findings"} or hard_gate["status"] not in {"pass", "fail"}:
        raise ContractError("E_EVALUATION_REPORT", "hard_gate is invalid")
    findings = hard_gate["findings"]
    if not isinstance(findings, list) or any(not isinstance(x, dict) or set(x) != {"code", "message"} for x in findings):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate findings are invalid")
    if (hard_gate["status"] == "pass") != (not findings):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate status and findings disagree")
    advisory = validate_advisory_metrics(value["advisory"])
    behavior = validate_behavior_result(value["behavior"])
    if type(value["behavior_required"]) is not bool:
        raise ContractError("E_EVALUATION_REPORT", "behavior_required must be boolean")
    expected = "pass" if hard_gate["status"] == "pass" and (not value["behavior_required"] or behavior["status"] == "pass") else "fail"
    if value["status"] != expected:
        raise ContractError("E_EVALUATION_REPORT", "report status does not match gates")
    result = copy.deepcopy(value)
    result["advisory"] = advisory
    result["behavior"] = behavior
    return result


def validate_evaluation_report_v3(payload: Any) -> dict[str, Any]:
    """Validate the closed Evaluation Report v3 projection.

    The compiled measures are deliberately ordinary bounded metrics.  Their
    semantic meaning is owned by the evaluator, while this boundary keeps the
    report canonical and prevents a caller from manufacturing a passing report
    with missing measures or an unbound inventory identity.
    """

    _reject_float(payload)
    value = _strict_object(payload, _REPORT_V3_FIELDS, "evaluation report", "E_EVALUATION_REPORT")
    if (
        value["schema_version"] != EVALUATION_REPORT_V3_SCHEMA_VERSION
        or value["status"] not in {"pass", "fail"}
        or value["decision_basis"] != "hard-compiled-gates-with-configured-behavior"
    ):
        raise ContractError("E_EVALUATION_REPORT", "evaluation report decision fields are invalid")
    for name in ("bundle_sha256", "compiled_fingerprint"):
        if not isinstance(value[name], str) or not _SHA256.fullmatch(value[name]):
            raise ContractError("E_EVALUATION_REPORT", f"{name} is invalid")

    hard_gate = value["hard_gate"]
    if not isinstance(hard_gate, dict) or set(hard_gate) != {"status", "findings"} or hard_gate["status"] not in {"pass", "fail"}:
        raise ContractError("E_EVALUATION_REPORT", "hard_gate is invalid")
    findings = hard_gate["findings"]
    if not isinstance(findings, list) or any(
        not isinstance(item, dict) or set(item) != {"code", "message"}
        or not isinstance(item["code"], str) or not item["code"]
        or not isinstance(item["message"], str) or not item["message"]
        for item in findings
    ):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate findings are invalid")
    ordered_findings = sorted(findings, key=lambda item: (item["code"], item["message"]))
    if findings != ordered_findings:
        raise ContractError("E_EVALUATION_REPORT", "hard_gate findings are not canonically ordered")
    if (hard_gate["status"] == "pass") != (not findings):
        raise ContractError("E_EVALUATION_REPORT", "hard_gate status and findings disagree")

    compiled = _strict_object(value["compiled"], _COMPILED_REPORT_FIELDS, "evaluation report.compiled", "E_EVALUATION_REPORT")
    normalized_compiled: dict[str, dict[str, object]] = {}
    for name in sorted(_COMPILED_REPORT_FIELDS):
        metric = validate_metric(compiled[name], f"compiled.{name}")
        # Every compiled measure has a concrete inventory-backed denominator;
        # a missing variant or empty artifact set is a failure, not N/A.
        if metric["total"] == 0:
            raise ContractError("E_EVALUATION_REPORT", f"compiled metric {name} must be measured")
        normalized_compiled[name] = metric

    advisory = validate_advisory_metrics(value["advisory"])
    behavior = validate_behavior_result(value["behavior"])
    if type(value["behavior_required"]) is not bool:
        raise ContractError("E_EVALUATION_REPORT", "behavior_required must be boolean")
    compiled_pass = all(
        metric["status"] == "measured" and metric["basis_points"] == 10_000
        for metric in normalized_compiled.values()
    )
    expected = (
        "pass"
        if hard_gate["status"] == "pass"
        and compiled_pass
        and (not value["behavior_required"] or behavior["status"] == "pass")
        else "fail"
    )
    if value["status"] != expected:
        raise ContractError("E_EVALUATION_REPORT", "report status does not match compiled gates")

    result = copy.deepcopy(value)
    result["compiled"] = normalized_compiled
    result["advisory"] = advisory
    result["behavior"] = behavior
    return result
