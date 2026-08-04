from __future__ import annotations

import copy
import hashlib
import re
from pathlib import PurePosixPath
from typing import Any, Mapping, Sequence

from ...pipeline_contracts import ContractError, canonical_json_bytes, validate_contract
from ..contracts.plan import normalize_generation_text, validate_readme_plan
from ..contracts.locale import parse_locale
from ..contracts.evidence import validate_evidence_graph
from ..contracts.run import canonical_repository
from ..errors import AGGREGATABLE_CODES


GENERATION_REQUEST_SCHEMA_VERSION = 1
MAX_GENERATION_REQUEST_BYTES = 1024 * 1024
MAX_REVISION_REQUEST_BYTES = 256 * 1024
MAX_REVISION_ATTEMPTS = 3
MAX_REQUEST_ITEMS = 10_000
MAX_REQUEST_TEXT_BYTES = 4096
PROJECT_CLASSIFICATIONS = frozenset({"developer-tool", "library", "runtime-toolchain", "web-framework"})
REQUEST_MODES = frozenset({"readme", "asset-only", "audit-only"})
_SHA1 = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_DEFAULT_FORBIDDEN_PATHS = (".env", ".git/**", ".omo/**", "node_modules/**")
_COMPILED_AUTHOR_PATHS = ("claim-map.json", "visual-spec.json")
_COMPILED_SCHEMA_REFERENCES = {
    "claim-map.json": "schemas/claim-map.v3.schema.json",
    "visual-spec.json": "schemas/visual-spec.v1.schema.json",
}
_COMPILED_FINAL_MANIFEST = "asset-manifest.json"


def _revision_reason(value: Any, index: int) -> dict[str, Any]:
    path = f"revision request.reasons[{index}]"
    reason = _object(
        value,
        {
            "category", "code", "line", "message", "path", "related_ids",
            "severity", "suggested_action",
        },
        path,
    )
    code = _text(reason["code"], f"{path}.code")
    if code not in AGGREGATABLE_CODES:
        raise ContractError("E_REVISION_DIAGNOSTIC", "revision reason must be an aggregatable content diagnostic")
    if reason["category"] != "content" or reason["severity"] != "error":
        raise ContractError("E_REVISION_DIAGNOSTIC", "revision reason must preserve content/error policy")
    line = reason["line"]
    if line is not None and (type(line) is not int or line < 1):
        raise ContractError("E_SCHEMA_TYPE", f"{path}.line must be null or a positive integer")
    reason_path = reason["path"]
    if reason_path is not None:
        reason_path = _path(reason_path, f"{path}.path")
    related = reason["related_ids"]
    if not isinstance(related, list) or len(related) > MAX_REQUEST_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", f"{path}.related_ids must be a bounded array")
    related_ids = [_text(item, f"{path}.related_ids[]") for item in related]
    if related_ids != sorted(set(related_ids)):
        raise ContractError("E_SCHEMA_VALUE", f"{path}.related_ids must be sorted and unique")
    suggested = reason["suggested_action"]
    if suggested is not None:
        suggested = _text(suggested, f"{path}.suggested_action")
    return {
        "category": "content",
        "code": code,
        "line": line,
        "message": _text(reason["message"], f"{path}.message"),
        "path": reason_path,
        "related_ids": related_ids,
        "severity": "error",
        "suggested_action": suggested,
    }


def _revision_reasons(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not value or len(value) > MAX_REQUEST_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", "revision request.reasons must be a non-empty bounded array")
    reasons = [_revision_reason(item, index) for index, item in enumerate(value)]
    expected = sorted(
        reasons,
        key=lambda item: (
            item["code"], item["path"] or "", item["line"] or 0, item["message"],
            item["related_ids"], item["suggested_action"] or "",
        ),
    )
    if reasons != expected:
        raise ContractError("E_SCHEMA_VALUE", "revision reasons must use canonical diagnostic order")
    if len({canonical_json_bytes(item) for item in reasons}) != len(reasons):
        raise ContractError("E_SCHEMA_VALUE", "revision reasons must be unique")
    return reasons


def _revision_paths(value: Any, path: str, *, nonempty: bool = True) -> list[str]:
    if not isinstance(value, list) or (nonempty and not value) or len(value) > MAX_REQUEST_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be a bounded path array")
    paths = [_path(item, f"{path}[]") for item in value]
    if paths != sorted(set(paths)):
        raise ContractError("E_SCHEMA_VALUE", f"{path} must be sorted and unique")
    return paths


def validate_revision_request(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    request = validate_contract(
        payload,
        required={
            "schema_version", "attempt", "original_request_sha256",
            "before_candidate_sha256", "after_candidate_sha256", "diagnostics_sha256",
            "reasons", "allowed_files", "forbidden_paths",
        },
        optional=set(),
        context="revision request",
    )
    if request["schema_version"] != 1:
        raise ContractError("E_SCHEMA_VERSION", "revision request requires schema_version 1")
    attempt = request["attempt"]
    if type(attempt) is not int or not 1 <= attempt <= MAX_REVISION_ATTEMPTS:
        raise ContractError("E_SCHEMA_VALUE", f"revision request.attempt must be between 1 and {MAX_REVISION_ATTEMPTS}")
    reasons = _revision_reasons(request["reasons"])
    diagnostics = {"diagnostics": reasons, "schema_version": 1, "status": "fail"}
    diagnostics_sha256 = _sha(request["diagnostics_sha256"], "revision request.diagnostics_sha256")
    if diagnostics_sha256 != hashlib.sha256(canonical_json_bytes(diagnostics)).hexdigest():
        raise ContractError("E_REVISION_DIAGNOSTIC", "revision request diagnostics hash is stale")
    allowed = _revision_paths(request["allowed_files"], "revision request.allowed_files")
    forbidden = _revision_paths(request["forbidden_paths"], "revision request.forbidden_paths")
    if set(allowed) & set(forbidden):
        raise ContractError("E_REVISION_PATH", "revision allowed and forbidden paths overlap")
    normalized = {
        "schema_version": 1,
        "attempt": attempt,
        "original_request_sha256": _sha(
            request["original_request_sha256"],
            "revision request.original_request_sha256",
        ),
        "before_candidate_sha256": _sha(
            request["before_candidate_sha256"], "revision request.before_candidate_sha256"
        ),
        "after_candidate_sha256": _sha(
            request["after_candidate_sha256"], "revision request.after_candidate_sha256"
        ),
        "diagnostics_sha256": diagnostics_sha256,
        "reasons": reasons,
        "allowed_files": allowed,
        "forbidden_paths": forbidden,
    }
    if len(canonical_json_bytes(normalized)) > MAX_REVISION_REQUEST_BYTES:
        raise ContractError("E_REVISION_SIZE", f"revision request exceeds {MAX_REVISION_REQUEST_BYTES} bytes")
    return copy.deepcopy(normalized)


def build_revision_request(
    *,
    attempt: int,
    original_request_sha256: str,
    before_candidate_sha256: str,
    after_candidate_sha256: str,
    diagnostic_report: Mapping[str, Any],
    allowed_files: Sequence[str],
    forbidden_paths: Sequence[str],
) -> dict[str, Any]:
    if not isinstance(diagnostic_report, Mapping) or diagnostic_report.get("status") != "fail":
        raise ContractError("E_REVISION_DIAGNOSTIC", "revision requires a failed diagnostic report")
    raw_reasons = diagnostic_report.get("diagnostics")
    if not isinstance(raw_reasons, list):
        raise ContractError("E_REVISION_DIAGNOSTIC", "revision diagnostic report is malformed")
    reasons: list[dict[str, Any]] = []
    for raw in raw_reasons:
        if not isinstance(raw, Mapping) or not isinstance(raw.get("code"), str):
            raise ContractError("E_REVISION_DIAGNOSTIC", "revision diagnostic is malformed")
        if set(raw) == {"code"}:
            raw = {
                "category": "content",
                "code": raw["code"],
                "line": None,
                "message": f"content validation failed: {raw['code']}",
                "path": None,
                "related_ids": [],
                "severity": "error",
                "suggested_action": None,
            }
        reasons.append(dict(raw))
    reasons = sorted(
        reasons,
        key=lambda item: (
            item.get("code", ""), item.get("path") or "", item.get("line") or 0,
            item.get("message", ""), item.get("related_ids", []),
            item.get("suggested_action") or "",
        ),
    )
    normalized_reasons = _revision_reasons(reasons)
    canonical_report = {"diagnostics": normalized_reasons, "schema_version": 1, "status": "fail"}
    request = {
        "schema_version": 1,
        "attempt": attempt,
        "original_request_sha256": original_request_sha256,
        "before_candidate_sha256": before_candidate_sha256,
        "after_candidate_sha256": after_candidate_sha256,
        "diagnostics_sha256": hashlib.sha256(canonical_json_bytes(canonical_report)).hexdigest(),
        "reasons": normalized_reasons,
        "allowed_files": sorted(set(allowed_files)),
        "forbidden_paths": sorted(set(forbidden_paths)),
    }
    return validate_revision_request(request)


def canonical_revision_request(payload: Any) -> bytes:
    return canonical_json_bytes(validate_revision_request(payload))


def _reject_float(value: Any, path: str = "$") -> None:
    if isinstance(value, float):
        raise ContractError("E_SCHEMA_FLOAT", f"{path} must not contain floats")
    if isinstance(value, list):
        for index, item in enumerate(value):
            _reject_float(item, f"{path}[{index}]")
    elif isinstance(value, dict):
        for key, item in value.items():
            _reject_float(item, f"{path}.{key}")


def _text(
    value: Any,
    path: str,
    *,
    maximum: int = MAX_REQUEST_TEXT_BYTES,
) -> str:
    return normalize_generation_text(value, path, maximum=maximum)


def _sha(value: Any, path: str, *, sha1: bool = False) -> str:
    if not isinstance(value, str) or not (_SHA1 if sha1 else _SHA256).fullmatch(value):
        raise ContractError("E_SCHEMA_VALUE", f"{path} must be a lowercase SHA digest")
    return value


def _path(value: Any, path: str) -> str:
    text = _text(value, path)
    pure = PurePosixPath(text)
    if pure.is_absolute() or "\\" in text or any(part in {"", ".", ".."} for part in text.split("/")):
        raise ContractError("E_GENERATION_REQUEST_VALUE", f"{path} must be a safe relative POSIX path")
    return pure.as_posix()


def _object(value: Any, required: set[str], path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ContractError("E_SCHEMA_TYPE", f"{path} must be an object")
    unknown = sorted(set(value) - required)
    missing = sorted(required - set(value))
    if unknown:
        raise ContractError("E_SCHEMA_UNKNOWN_FIELD", f"{path} contains unknown field: {unknown[0]}")
    if missing:
        raise ContractError("E_SCHEMA_MISSING_FIELD", f"{path} is missing field: {missing[0]}")
    return value


def _output_contract(value: Any) -> dict[str, Any]:
    contract = _object(value, {"required_files", "schemas", "forbidden_paths"}, "generation request.output_contract")
    required = contract["required_files"]
    forbidden = contract["forbidden_paths"]
    schemas = contract["schemas"]
    if not isinstance(required, list) or not isinstance(forbidden, list) or not isinstance(schemas, dict):
        raise ContractError("E_SCHEMA_TYPE", "generation request.output_contract collections are invalid")
    required_paths = [_path(item, "generation request.output_contract.required_files[]") for item in required]
    forbidden_paths = [_path(item, "generation request.output_contract.forbidden_paths[]") for item in forbidden]
    if required_paths != sorted(set(required_paths)) or forbidden_paths != sorted(set(forbidden_paths)):
        raise ContractError("E_SCHEMA_VALUE", "generation request output paths must be sorted and unique")
    schema_map = {
        _path(key, "generation request.output_contract.schemas key"):
        _path(item, "generation request.output_contract.schemas value")
        for key, item in schemas.items()
    }
    if list(schemas) != sorted(schemas) or set(schema_map) - set(required_paths):
        raise ContractError("E_SCHEMA_VALUE", "generation request schemas must be sorted and reference required files")
    return {"required_files": required_paths, "schemas": schema_map, "forbidden_paths": forbidden_paths}


def _validate_compiled_output_contract(
    contract: dict[str, Any],
    plan: dict[str, Any],
) -> dict[str, Any]:
    """Bind Plan v3 compiled author inputs to the installed schema atoms."""
    expected_readme_paths = [entry["readme_path"] for entry in plan["locales"]]
    expected_required = set(expected_readme_paths) | set(_COMPILED_AUTHOR_PATHS)
    required = set(contract["required_files"])
    forbidden = set(contract["forbidden_paths"])
    schemas = contract["schemas"]
    if required != expected_required:
        raise ContractError(
            "E_SCHEMA_VALUE",
            "compiled generation request must require every README, claim map, and visual spec author output",
        )
    if _COMPILED_FINAL_MANIFEST in required or _COMPILED_FINAL_MANIFEST not in forbidden:
        raise ContractError(
            "E_SCHEMA_VALUE",
            "compiled generation request must forbid the final asset manifest",
        )
    if expected_required & forbidden:
        raise ContractError(
            "E_SCHEMA_VALUE",
            "compiled generation request required and forbidden paths overlap",
        )
    if schemas != _COMPILED_SCHEMA_REFERENCES:
        raise ContractError(
            "E_SCHEMA_VALUE",
            "compiled generation request schema references must cover exactly the author JSON outputs",
        )
    return contract


def _revision_context(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    context = _object(value, {"original_request_sha256", "attempt"}, "generation request.revision_context")
    attempt = context["attempt"]
    if type(attempt) is not int or attempt < 1:
        raise ContractError("E_SCHEMA_TYPE", "generation request.revision_context.attempt must be positive integer")
    return {"original_request_sha256": _sha(context["original_request_sha256"], "generation request.revision_context.original_request_sha256"), "attempt": attempt}


def _records(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_REQUEST_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", "generation request.retrieval_records must be a bounded array")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        record = _object(raw, {"record_id", "score", "pattern"}, f"generation request.retrieval_records[{index}]")
        record_id = _text(record["record_id"], f"generation request.retrieval_records[{index}].record_id")
        score = record["score"]
        if type(score) is not int or score < 0:
            raise ContractError("E_SCHEMA_TYPE", "generation request retrieval score must be a non-negative integer")
        pattern = _object(record["pattern"], {"summary", "structure", "proof"}, f"generation request.retrieval_records[{index}].pattern")
        if record_id in seen:
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval records contain duplicate record_id")
        seen.add(record_id)
        records.append({
            "record_id": record_id,
            "score": score,
            "pattern": {
                name: _text(
                    pattern[name],
                    f"generation request retrieval pattern.{name}",
                )
                for name in ("summary", "structure", "proof")
            },
        })
    expected = sorted(records, key=lambda item: (-item["score"], item["record_id"]))
    if records != expected:
        raise ContractError("E_SCHEMA_VALUE", "generation request retrieval records must use priority order")
    return records


def _evidence_index(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or len(value) > MAX_REQUEST_ITEMS:
        raise ContractError("E_SCHEMA_TYPE", "generation request.evidence_index must be a bounded array")
    result: list[dict[str, str]] = []
    seen: set[str] = set()
    for index, raw in enumerate(value):
        entry = _object(raw, {"fact_id", "evidence_sha256", "packet_sha256"}, f"generation request.evidence_index[{index}]")
        fact_id = _text(entry["fact_id"], f"generation request.evidence_index[{index}].fact_id")
        if fact_id in seen:
            raise ContractError("E_GENERATION_EVIDENCE_DUPLICATE", "generation request contains duplicate evidence fact")
        seen.add(fact_id)
        result.append({
            "fact_id": fact_id,
            "evidence_sha256": _sha(entry["evidence_sha256"], "generation request evidence hash"),
            "packet_sha256": _sha(entry["packet_sha256"], "generation request evidence packet hash"),
        })
    if result != sorted(result, key=lambda item: item["fact_id"]):
        raise ContractError("E_SCHEMA_VALUE", "generation request evidence index must use fact_id order")
    return result


def validate_generation_request(payload: Any) -> dict[str, Any]:
    _reject_float(payload)
    request = validate_contract(
        payload,
        required={
            "schema_version", "mode", "target", "locales", "project_classification", "plan",
            "retrieval_records", "evidence_index", "output_contract", "revision_context",
        },
        optional=set(),
        context="generation request",
    )
    mode = _text(request["mode"], "generation request.mode")
    if mode not in REQUEST_MODES:
        raise ContractError("E_SCHEMA_VALUE", "generation request mode is unsupported")
    target = _object(request["target"], {"repository", "base_sha"}, "generation request.target")
    repository = canonical_repository(_text(target["repository"], "generation request.target.repository"))
    base_sha = _sha(target["base_sha"], "generation request.target.base_sha", sha1=True)
    locales = request["locales"]
    if not isinstance(locales, list) or not locales or len(locales) != len(set(locales)):
        raise ContractError("E_SCHEMA_TYPE", "generation request.locales must be a non-empty unique array")
    normalized_locales = [parse_locale(locale, "generation request.locales[]") for locale in locales]
    classification = request["project_classification"]
    if classification is not None:
        classification = _text(classification, "generation request.project_classification")
        if classification not in PROJECT_CLASSIFICATIONS:
            raise ContractError("E_SCHEMA_VALUE", "generation request project classification is unsupported")
    plan = validate_readme_plan(request["plan"], mode=mode)
    plan_locales = (
        [entry["tag"] for entry in plan["locales"]]
        if plan["schema_version"] in {2, 3}
        else ["zh-Hans" if tag == "zh" else tag for tag in plan["languages"]]
    )
    if normalized_locales != plan_locales:
        raise ContractError("E_LOCALE", "generation request.locales must match README plan order")
    evidence_index = _evidence_index(request["evidence_index"])
    if {entry["fact_id"] for entry in evidence_index} != set(plan["evidence_ids"]):
        raise ContractError("E_GENERATION_EVIDENCE_DANGLING", "generation request evidence index does not bind every plan fact")
    output_contract = _output_contract(request["output_contract"])
    if plan["schema_version"] == 3 and plan["diagram_route"] == "compiled":
        output_contract = _validate_compiled_output_contract(output_contract, plan)
    normalized = {
        "schema_version": GENERATION_REQUEST_SCHEMA_VERSION,
        "mode": mode,
        "target": {"repository": repository, "base_sha": base_sha},
        "locales": normalized_locales,
        "project_classification": classification,
        "plan": plan,
        "retrieval_records": _records(request["retrieval_records"]),
        "evidence_index": evidence_index,
        "output_contract": output_contract,
        "revision_context": _revision_context(request["revision_context"]),
    }
    if len(canonical_json_bytes(normalized)) > MAX_GENERATION_REQUEST_BYTES:
        raise ContractError("E_GENERATION_REQUEST_SIZE", f"generation request exceeds {MAX_GENERATION_REQUEST_BYTES} bytes")
    return copy.deepcopy(normalized)


def _source_records(packet: Any) -> list[dict[str, Any]]:
    if not isinstance(packet, Mapping) or packet.get("schema_version") != 1 or packet.get("status") != "available":
        raise ContractError("E_GENERATION_RETRIEVAL", "retrieval packet is not available v1 data")
    records = packet.get("records")
    if not isinstance(records, list) or len(records) > MAX_REQUEST_ITEMS:
        raise ContractError("E_GENERATION_RETRIEVAL", "retrieval packet records are invalid")
    selected: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in records:
        if not isinstance(raw, Mapping):
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval record must be an object")
        try:
            record_id, score, pattern = raw["record_id"], raw["score"], raw["pattern"]
        except KeyError as exc:
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval record is incomplete") from exc
        if not isinstance(pattern, Mapping) or set(pattern) != {"summary", "structure", "proof"}:
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval pattern fields are invalid")
        if not isinstance(record_id, str) or type(score) is not int or score < 0 or record_id in seen:
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval record identity or score is invalid")
        seen.add(record_id)
        try:
            selected.append({
                "record_id": _text(record_id, "retrieval record_id"),
                "score": score,
                "pattern": {
                    name: _text(
                        pattern[name], f"retrieval pattern.{name}"
                    )
                    for name in ("summary", "structure", "proof")
                },
            })
        except ContractError as exc:
            raise ContractError("E_GENERATION_RETRIEVAL", "retrieval pattern values are invalid") from exc
    return sorted(selected, key=lambda item: (-item["score"], item["record_id"]))


def _source_evidence(packet: Any, fact_ids: Sequence[str], base_sha: str) -> tuple[str, list[dict[str, str]]]:
    if isinstance(packet, Mapping) and packet.get("schema_version") == 2:
        graph = validate_evidence_graph(dict(packet))
        packet_sha256 = hashlib.sha256(canonical_json_bytes(graph)).hexdigest()
        by_id = {fact["fact_id"]: fact["evidence_sha256"] for fact in graph["facts"]}
        missing = sorted(set(fact_ids) - set(by_id))
        if missing:
            raise ContractError("E_GENERATION_EVIDENCE_DANGLING", f"README plan references missing evidence: {missing[0]}")
        return packet_sha256, [
            {"fact_id": fact_id, "evidence_sha256": by_id[fact_id], "packet_sha256": packet_sha256}
            for fact_id in sorted(fact_ids)
        ]
    if not isinstance(packet, Mapping) or packet.get("schema_version") != 1 or packet.get("status") != "complete":
        raise ContractError("E_GENERATION_EVIDENCE", "evidence packet is not complete v1 data")
    packet_sha256 = hashlib.sha256(canonical_json_bytes(dict(packet))).hexdigest()
    target = packet.get("target")
    if not isinstance(target, Mapping) or set(target) != {"name", "base_sha"} or target.get("base_sha") != base_sha:
        raise ContractError("E_GENERATION_EVIDENCE_STALE", "evidence packet does not bind target base SHA")
    files = packet.get("files")
    if not isinstance(files, list) or len(files) > MAX_REQUEST_ITEMS:
        raise ContractError("E_GENERATION_EVIDENCE", "evidence packet files are invalid")
    file_hashes: dict[str, str] = {}
    for raw in files:
        if not isinstance(raw, Mapping) or set(raw) != {"path", "bytes", "lines", "sha256", "content"}:
            raise ContractError("E_GENERATION_EVIDENCE", "v1 evidence file fields are invalid")
        path, content, digest = raw["path"], raw["content"], raw["sha256"]
        if not isinstance(content, str) or not isinstance(path, str):
            raise ContractError("E_GENERATION_EVIDENCE", "v1 evidence file value is invalid")
        _path(path, "v1 evidence file.path")
        _sha(digest, "v1 evidence file.sha256")
        encoded = content.encode("utf-8")
        if raw["bytes"] != len(encoded) or raw["lines"] != len(content.splitlines()) or hashlib.sha256(encoded).hexdigest() != digest:
            raise ContractError("E_GENERATION_EVIDENCE_STALE", "v1 evidence file bytes or hash changed")
        if path in file_hashes:
            raise ContractError("E_GENERATION_EVIDENCE_DUPLICATE", "evidence packet contains duplicate file path")
        file_hashes[path] = digest
    facts = packet.get("facts")
    if not isinstance(facts, list) or len(facts) > MAX_REQUEST_ITEMS:
        raise ContractError("E_GENERATION_EVIDENCE", "evidence packet facts are invalid")
    by_id: dict[str, str] = {}
    for raw in facts:
        if not isinstance(raw, Mapping) or set(raw) != {"fact_id", "kind", "path", "evidence_sha256"}:
            raise ContractError("E_GENERATION_EVIDENCE", "v1 evidence fact fields are invalid")
        fact_id, path, digest = raw["fact_id"], raw["path"], raw["evidence_sha256"]
        if not isinstance(fact_id, str) or not isinstance(path, str) or fact_id != f"file:{path}" or raw["kind"] != "repository-file":
            raise ContractError("E_GENERATION_EVIDENCE", "v1 evidence fact identity is invalid")
        _path(path, "v1 evidence fact.path")
        _sha(digest, "v1 evidence fact.evidence_sha256")
        if file_hashes.get(path) != digest:
            raise ContractError("E_GENERATION_EVIDENCE_STALE", "v1 evidence fact does not bind file hash")
        if fact_id in by_id:
            raise ContractError("E_GENERATION_EVIDENCE_DUPLICATE", "evidence packet contains duplicate fact_id")
        by_id[fact_id] = digest
    missing = sorted(set(fact_ids) - set(by_id))
    if missing:
        raise ContractError("E_GENERATION_EVIDENCE_DANGLING", f"README plan references missing evidence: {missing[0]}")
    return packet_sha256, [
        {"fact_id": fact_id, "evidence_sha256": by_id[fact_id], "packet_sha256": packet_sha256}
        for fact_id in sorted(fact_ids)
    ]


def build_generation_request(
    *,
    target: Mapping[str, Any],
    locales: Sequence[str],
    project_classification: str | None,
    plan: Mapping[str, Any],
    retrieval_packet: Mapping[str, Any],
    evidence_packet: Mapping[str, Any],
    output_contract: Mapping[str, Any] | None = None,
    revision_context: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    _reject_float({"target": target, "locales": locales, "plan": plan, "retrieval": retrieval_packet, "evidence": evidence_packet})
    normalized_plan = validate_readme_plan(dict(plan))
    target_value = _object(dict(target), {"repository", "base_sha"}, "generation request.target")
    base_sha = _sha(target_value["base_sha"], "generation request.target.base_sha", sha1=True)
    packet_sha256, evidence_index = _source_evidence(evidence_packet, normalized_plan["evidence_ids"], base_sha)
    query = retrieval_packet.get("query")
    if not isinstance(query, Mapping) or query.get("evidence_sha256") != packet_sha256:
        raise ContractError("E_GENERATION_EVIDENCE_STALE", "retrieval packet does not bind current evidence packet")
    if project_classification is not None and query.get("project_type") != project_classification:
        raise ContractError("E_GENERATION_RETRIEVAL", "retrieval packet does not bind project classification")
    compiled = normalized_plan["schema_version"] == 3 and normalized_plan["diagram_route"] == "compiled"
    required = list(_COMPILED_AUTHOR_PATHS if compiled else ("asset-manifest.json", "claim-map.json"))
    if compiled:
        required.extend(entry["readme_path"] for entry in normalized_plan["locales"])
    elif normalized_plan["mode"] == "readme":
        if normalized_plan["schema_version"] in {2, 3}:
            required.extend(entry["readme_path"] for entry in normalized_plan["locales"])
        else:
            required.append("README.md")
            if "zh" in normalized_plan["languages"]:
                required.append("README_zh.md")
    forbidden = list(_DEFAULT_FORBIDDEN_PATHS)
    schemas: dict[str, str] = {}
    if compiled:
        forbidden.append(_COMPILED_FINAL_MANIFEST)
        schemas.update(_COMPILED_SCHEMA_REFERENCES)
    request: dict[str, Any] = {
        "schema_version": GENERATION_REQUEST_SCHEMA_VERSION,
        "mode": normalized_plan["mode"],
        "target": dict(target),
        "locales": list(locales),
        "project_classification": project_classification,
        "plan": normalized_plan,
        "retrieval_records": _source_records(retrieval_packet),
        "evidence_index": evidence_index,
        "output_contract": dict(output_contract) if output_contract is not None else {
            "required_files": sorted(required),
            "schemas": schemas,
            "forbidden_paths": sorted(forbidden),
        },
        "revision_context": None if revision_context is None else dict(revision_context),
    }
    while len(canonical_json_bytes(request)) > MAX_GENERATION_REQUEST_BYTES and request["retrieval_records"]:
        request["retrieval_records"].pop()
    return validate_generation_request(request)


def canonical_generation_request(payload: Any) -> bytes:
    return canonical_json_bytes(validate_generation_request(payload))
