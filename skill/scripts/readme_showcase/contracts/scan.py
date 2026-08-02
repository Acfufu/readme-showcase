from __future__ import annotations

import copy
import hashlib
from pathlib import PurePosixPath
from typing import Any, Mapping

from .common import ContractError, canonical_json_bytes, normalize_posix_path


SCAN_SCHEMA_VERSION = 2
PROJECT_TYPES = frozenset({"cli", "library", "app", "extension", "service", "unknown"})
SKIP_REASONS = frozenset(
    {
        "binary",
        "file-count-limit",
        "file-size-limit",
        "invalid-utf8",
        "race",
        "required-evidence-missing",
        "secret",
        "special-file",
        "submodule",
        "symlink",
        "time-limit",
        "total-size-limit",
    }
)
MINIMUM_EVIDENCE = {
    "cli": ("manifest", "install-entry", "usage-or-test"),
    "library": ("manifest", "install-entry", "usage-or-test"),
    "app": ("manifest", "entrypoint", "install-artifact-or-runtime-entry"),
    "extension": ("manifest", "entrypoint", "install-artifact-or-runtime-entry"),
    "service": ("manifest", "entrypoint", "deploy-or-health"),
    "unknown": ("readme", "manifest"),
}

_TOP_FIELDS = frozenset(
    {"schema_version", "status", "target", "scan_limits", "project_type", "coverage", "files", "facts", "skipped", "warnings", "policy"}
)
_TARGET_FIELDS = frozenset({"name", "base_sha"})
_LIMIT_FIELDS = frozenset({"max_depth", "max_directories", "max_file_bytes", "max_files", "max_seconds", "max_total_bytes"})
_COVERAGE_FIELDS = frozenset({"tracked_files", "indexed_files", "selected_files", "content_files", "skipped_files", "content_bytes"})
_FILE_FIELDS = frozenset({"path", "bytes", "lines", "sha256", "content"})
_FACT_FIELDS = frozenset({"fact_id", "kind", "path", "evidence_sha256"})
_SKIP_FIELDS = frozenset({"path", "reason", "required_for_generation"})
_WARNING_FIELDS = frozenset({"code", "path"})
_POLICY_FIELDS = frozenset({"required_evidence", "satisfied_evidence", "missing_evidence", "allowed_consumers", "publish_eligible"})


def _fail(code: str, message: str) -> None:
    raise ContractError(code, message)


def _object(value: Any, fields: frozenset[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_SCHEMA_TYPE", f"{context} must be an object")
    unknown = sorted(set(value) - fields)
    missing = sorted(fields - set(value))
    if unknown:
        _fail("E_SCHEMA_UNKNOWN_FIELD", f"{context} contains unknown field: {unknown[0]}")
    if missing:
        _fail("E_SCHEMA_MISSING_FIELD", f"{context} is missing field: {missing[0]}")
    return value


def _integer(value: Any, context: str, *, minimum: int = 0) -> int:
    if type(value) is not int or value < minimum:
        _fail("E_SCHEMA_TYPE", f"{context} must be an integer >= {minimum}")
    return value


def _sorted_strings(value: Any, context: str) -> list[str]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value) or value != sorted(set(value)):
        _fail("E_SCAN_POLICY", f"{context} must be a sorted unique string list")
    return value


def evidence_categories(path: str) -> frozenset[str]:
    value = PurePosixPath(path.lower())
    name = value.name
    parts = set(value.parts[:-1])
    suffix = value.suffix
    manifest = name in {
        "cargo.toml", "composer.json", "go.mod", "manifest.json", "package.json", "pom.xml",
        "pyproject.toml", "requirements.txt", "setup.cfg", "setup.py",
    } or name.startswith("build.gradle")
    readme = name.startswith("readme")
    entrypoint = (
        "bin" in parts
        or name in {"app.py", "main.go", "main.js", "main.py", "main.rs", "main.swift", "main.ts"}
        or value.as_posix() in {"src/index.js", "src/index.ts"}
    )
    install_entry = (
        "bin" in parts
        or name in {"install", "install.sh", "requirements.txt", "setup.cfg", "setup.py"}
        or name.startswith("install-")
    )
    usage_or_test = readme or bool(parts & {"example", "examples", "test", "tests"}) or name.startswith("test_") or name.endswith("_test.py")
    runtime = entrypoint or suffix in {".app", ".apk", ".crx", ".ipa", ".vsix"} or name.endswith(".xcodeproj")
    deploy = (
        name in {"dockerfile", "procfile", "compose.json", "compose.yml", "docker-compose.yml"}
        or bool(parts & {"deploy", "deployment", "health", "k8s", "kubernetes"})
        or name.startswith("health")
    )
    return frozenset(
        category
        for category, present in (
            ("manifest", manifest),
            ("readme", readme),
            ("entrypoint", entrypoint),
            ("install-entry", install_entry),
            ("usage-or-test", usage_or_test),
            ("install-artifact-or-runtime-entry", runtime),
            ("deploy-or-health", deploy),
        )
        if present
    )


def minimum_evidence(project_type: str, paths: list[str]) -> tuple[list[str], list[str], list[str]]:
    if project_type not in PROJECT_TYPES:
        _fail("E_SCAN_PROJECT_TYPE", "project_type is unsupported")
    required = list(MINIMUM_EVIDENCE[project_type])
    observed = set().union(*(evidence_categories(path) for path in paths)) if paths else set()
    satisfied = sorted(set(required) & observed)
    missing = sorted(set(required) - observed)
    return sorted(required), satisfied, missing


def validate_repository_scan_v2(payload: Any) -> dict[str, Any]:
    packet = _object(payload, _TOP_FIELDS, "repository scan")
    if packet["schema_version"] != SCAN_SCHEMA_VERSION:
        _fail("E_SCAN_VERSION", "repository scan schema_version must be 2")
    if packet["status"] not in {"complete", "partial", "incomplete"}:
        _fail("E_SCAN_STATUS", "repository scan status is unsupported")
    project_type = packet["project_type"]
    if project_type not in PROJECT_TYPES:
        _fail("E_SCAN_PROJECT_TYPE", "project_type is unsupported")

    target = _object(packet["target"], _TARGET_FIELDS, "repository scan.target")
    if not isinstance(target["name"], str) or not target["name"] or not isinstance(target["base_sha"], (str, type(None))):
        _fail("E_SCHEMA_TYPE", "repository scan target is invalid")
    limits = _object(packet["scan_limits"], _LIMIT_FIELDS, "repository scan.scan_limits")
    for key, value in limits.items():
        _integer(value, f"scan_limits.{key}", minimum=1)

    files = packet["files"]
    if not isinstance(files, list):
        _fail("E_SCHEMA_TYPE", "repository scan.files must be an array")
    file_paths: list[str] = []
    for index, value in enumerate(files):
        item = _object(value, _FILE_FIELDS, f"repository scan.files[{index}]")
        path = normalize_posix_path(item["path"])
        if item["path"] != path or path in file_paths:
            _fail("E_SCAN_FILE", "repository scan file paths must be normalized and unique")
        if not isinstance(item["content"], str):
            _fail("E_SCHEMA_TYPE", "repository scan file content must be text")
        raw = item["content"].encode("utf-8")
        if _integer(item["bytes"], "repository scan file bytes") != len(raw):
            _fail("E_SCAN_FILE", "repository scan file byte count is wrong")
        if _integer(item["lines"], "repository scan file lines") != len(item["content"].splitlines()):
            _fail("E_SCAN_FILE", "repository scan file line count is wrong")
        if item["sha256"] != hashlib.sha256(raw).hexdigest():
            _fail("E_SCAN_FILE", "repository scan file hash is wrong")
        file_paths.append(path)
    if file_paths != sorted(file_paths, key=lambda value: value.encode("utf-8")):
        _fail("E_SCAN_FILE", "repository scan files must use stable POSIX byte order")

    facts = packet["facts"]
    if not isinstance(facts, list) or len(facts) != len(files):
        _fail("E_SCAN_FACT", "repository scan facts must correspond one-to-one with files")
    for index, value in enumerate(facts):
        fact = _object(value, _FACT_FIELDS, f"repository scan.facts[{index}]")
        file = files[index]
        if fact != {"fact_id": f"file:{file['path']}", "kind": "repository-file", "path": file["path"], "evidence_sha256": file["sha256"]}:
            _fail("E_SCAN_FACT", "repository scan fact does not bind its retained file")

    skipped = packet["skipped"]
    if not isinstance(skipped, list):
        _fail("E_SCHEMA_TYPE", "repository scan.skipped must be an array")
    skip_paths: list[str] = []
    for index, value in enumerate(skipped):
        item = _object(value, _SKIP_FIELDS, f"repository scan.skipped[{index}]")
        path = normalize_posix_path(item["path"])
        if item["path"] != path or path in skip_paths or path in file_paths:
            _fail("E_SCAN_SKIP", "skip paths must be normalized, unique, and not retained")
        if item["reason"] not in SKIP_REASONS or type(item["required_for_generation"]) is not bool:
            _fail("E_SCAN_SKIP", "skip reason or required flag is invalid")
        skip_paths.append(path)
    if skip_paths != sorted(skip_paths, key=lambda value: value.encode("utf-8")):
        _fail("E_SCAN_SKIP", "skip paths must use stable POSIX byte order")

    warnings = packet["warnings"]
    if not isinstance(warnings, list):
        _fail("E_SCHEMA_TYPE", "repository scan.warnings must be an array")
    for index, value in enumerate(warnings):
        warning = _object(value, _WARNING_FIELDS, f"repository scan.warnings[{index}]")
        if not isinstance(warning["code"], str) or warning["path"] != normalize_posix_path(warning["path"]):
            _fail("E_SCAN_WARNING", "repository scan warning is invalid")

    coverage = _object(packet["coverage"], _COVERAGE_FIELDS, "repository scan.coverage")
    for key, value in coverage.items():
        _integer(value, f"coverage.{key}")
    if (
        coverage["selected_files"] != coverage["content_files"] + coverage["skipped_files"]
        or coverage["content_files"] != len(files)
        or coverage["skipped_files"] != len(skipped)
        or coverage["content_bytes"] != sum(item["bytes"] for item in files)
    ):
        _fail("E_SCAN_COVERAGE", "repository scan coverage counters do not reconcile")
    virtual_skips = sum(item["reason"] == "required-evidence-missing" for item in skipped)
    if (
        coverage["indexed_files"] > coverage["tracked_files"]
        or coverage["content_files"] > coverage["indexed_files"]
        or coverage["selected_files"] - virtual_skips > coverage["indexed_files"]
        or (packet["status"] == "complete" and coverage["tracked_files"] != coverage["indexed_files"])
    ):
        _fail("E_SCAN_COVERAGE", "repository scan tracked/indexed/selected counters are impossible")

    policy = _object(packet["policy"], _POLICY_FIELDS, "repository scan.policy")
    required = _sorted_strings(policy["required_evidence"], "policy.required_evidence")
    satisfied = _sorted_strings(policy["satisfied_evidence"], "policy.satisfied_evidence")
    missing = _sorted_strings(policy["missing_evidence"], "policy.missing_evidence")
    expected_required, expected_satisfied, expected_missing = minimum_evidence(project_type, file_paths)
    if (required, satisfied, missing) != (expected_required, expected_satisfied, expected_missing):
        _fail("E_SCAN_POLICY", "minimum-evidence policy does not match retained files")
    consumers = _sorted_strings(policy["allowed_consumers"], "policy.allowed_consumers")
    publish = policy["publish_eligible"]
    if type(publish) is not bool or publish != (packet["status"] == "complete") or (publish and "publish" not in consumers):
        _fail("E_SCAN_POLICY", "publish eligibility must require complete status")
    expected_consumers = ["audit"] if project_type == "unknown" and packet["status"] != "complete" else ["audit", "readme"]
    if packet["status"] == "complete":
        expected_consumers.append("publish")
    if consumers != sorted(expected_consumers):
        _fail("E_SCAN_POLICY", "allowed consumers do not match scan status")
    if missing and packet["status"] != "incomplete":
        _fail("E_SCAN_STATUS", "missing minimum evidence requires incomplete status")
    if not missing and packet["status"] == "incomplete":
        _fail("E_SCAN_STATUS", "incomplete status requires missing minimum evidence")
    if packet["status"] == "complete" and skipped:
        _fail("E_SCAN_STATUS", "complete status cannot contain skips")
    if packet["status"] == "partial" and not skipped:
        _fail("E_SCAN_STATUS", "partial status requires at least one skip")
    if missing and not any(item["required_for_generation"] for item in skipped):
        _fail("E_SCAN_POLICY", "missing minimum evidence requires a generation-required skip")
    return copy.deepcopy(packet)


def scan_allows_publish(payload: Any) -> bool:
    packet = validate_repository_scan_v2(payload)
    return packet["status"] == "complete" and packet["policy"]["publish_eligible"] is True


def adapt_v1_scan(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        _fail("E_SCHEMA_TYPE", "repository scan must be an object")
    if payload.get("schema_version") == 1:
        return copy.deepcopy(dict(payload))
    packet = validate_repository_scan_v2(dict(payload))
    complete = packet["status"] == "complete"
    warnings = list(packet["warnings"])
    warnings.extend({"code": f"W_SCAN_{skip['reason'].replace('-', '_').upper()}", "path": skip["path"]} for skip in packet["skipped"])
    return {
        "schema_version": 1,
        "status": "complete" if complete else "incomplete",
        "target": copy.deepcopy(packet["target"]),
        "scan_limits": copy.deepcopy(packet["scan_limits"]),
        "files": copy.deepcopy(packet["files"]) if complete else [],
        "facts": copy.deepcopy(packet["facts"]) if complete else [],
        "warnings": sorted(warnings, key=lambda item: (item["path"].encode("utf-8"), item["code"])),
    }


def canonical_v1_scan_bytes(payload: Any) -> bytes:
    return canonical_json_bytes(adapt_v1_scan(payload))
