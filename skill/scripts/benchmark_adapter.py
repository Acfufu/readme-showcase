from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any, NoReturn

_CONTRACTS = importlib.import_module(
    "pipeline_contracts"
    if __package__ in (None, "")
    else "skill.scripts.pipeline_contracts"
)
ContractError = _CONTRACTS.ContractError
canonical_json_bytes = _CONTRACTS.canonical_json_bytes
canonical_sha256 = _CONTRACTS.canonical_sha256
read_json_object = _CONTRACTS.read_json_object
validate_contract = _CONTRACTS.validate_contract


DATASET_ID = "patched-codes/generate-readme-eval"
DATASET_REVISION = "375c19fe9f1112017252bf400d32d86c5118aef1"
DATASET_LICENSE_SPDX = "Apache-2.0"
MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_ROWS = 200
MAX_REPO_CONTENT_BYTES = 1024 * 1024
MAX_README_BYTES = 512 * 1024
_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
_ROW_FIELDS = {"repo_name", "repo_commit", "repo_content", "repo_readme"}
_SIDECAR_FIELDS = {
    "schema_version",
    "dataset_id",
    "dataset_revision",
    "dataset_license_spdx",
    "split",
    "input_format",
    "input_sha256",
    "licenses",
}
_LICENSE_FIELDS = {
    "repo_name",
    "repo_commit",
    "license_spdx",
    "license_evidence_url",
    "license_evidence_sha256",
    "human_reviewed",
}
_REPOSITORY = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SPDX = re.compile(r"[A-Za-z0-9][A-Za-z0-9.+-]{0,63}\Z")


def _fail(code: str, message: str) -> NoReturn:
    raise ContractError(code, message)


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _exact_object(value: Any, fields: set[str], context: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        _fail("E_BENCHMARK_SCHEMA", f"{context} must be a JSON object")
    missing = sorted(fields - set(value))
    if missing:
        _fail(
            "E_BENCHMARK_SCHEMA",
            f"{context} is missing required field: {missing[0]}",
        )
    unknown = sorted(set(value) - fields)
    if unknown:
        _fail(
            "E_BENCHMARK_SCHEMA",
            f"{context} contains unknown field: {unknown[0]}",
        )
    return value


def _read_input(path: Path, input_format: str) -> tuple[bytes, list[dict[str, Any]]]:
    try:
        if path.is_symlink() or not path.is_file():
            _fail("E_BENCHMARK_INPUT", "benchmark input must be a regular file")
        size = path.stat().st_size
        if size == 0 or size > MAX_INPUT_BYTES:
            _fail(
                "E_BENCHMARK_INPUT_SIZE",
                f"benchmark input must be 1..{MAX_INPUT_BYTES} bytes",
            )
        raw = path.read_bytes()
        text = raw.decode("utf-8")
    except FileNotFoundError as exc:
        raise ContractError("E_INPUT_NOT_FOUND", f"input not found: {path}") from exc
    except UnicodeDecodeError as exc:
        raise ContractError(
            "E_INPUT_ENCODING",
            f"input is not valid UTF-8: {path}",
        ) from exc

    try:
        if input_format == "json":
            parsed = json.loads(text)
            if not isinstance(parsed, list):
                _fail("E_BENCHMARK_SCHEMA", "JSON benchmark input must be an array")
            values = parsed
        elif input_format == "jsonl":
            lines = text.splitlines()
            if not lines or any(not line.strip() for line in lines):
                _fail(
                    "E_BENCHMARK_SCHEMA",
                    "JSONL benchmark input must contain one object per nonempty line",
                )
            values = [json.loads(line) for line in lines]
        else:
            _fail("E_BENCHMARK_FORMAT", "input_format must be json or jsonl")
    except json.JSONDecodeError as exc:
        raise ContractError(
            "E_INPUT_JSON",
            f"invalid benchmark JSON at line {exc.lineno}, column {exc.colno}",
        ) from exc
    return raw, values


def _validate_row(value: Any, index: int) -> dict[str, str]:
    context = f"benchmark rows[{index}]"
    row = _exact_object(value, _ROW_FIELDS, context)
    repo_name = row["repo_name"]
    commit = row["repo_commit"]
    if not isinstance(repo_name, str) or not _REPOSITORY.fullmatch(repo_name):
        _fail("E_BENCHMARK_REPOSITORY", f"{context}.repo_name must be owner/repo")
    if (
        repo_name.startswith(".")
        or "/." in repo_name
        or repo_name.endswith(".")
        or ".." in repo_name
    ):
        _fail("E_BENCHMARK_REPOSITORY", f"{context}.repo_name is unsafe")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        _fail(
            "E_BENCHMARK_COMMIT",
            f"{context}.repo_commit must be a lowercase 40-character SHA",
        )
    limits = {
        "repo_content": MAX_REPO_CONTENT_BYTES,
        "repo_readme": MAX_README_BYTES,
    }
    for field, limit in limits.items():
        value = row[field]
        if (
            not isinstance(value, str)
            or not value
            or "\0" in value
            or len(value.encode("utf-8")) > limit
        ):
            _fail(
                "E_BENCHMARK_TEXT",
                f"{context}.{field} must be nonempty UTF-8 text within {limit} bytes",
            )
    return {
        "repo_name": repo_name,
        "repo_commit": commit,
        "repo_content": row["repo_content"],
        "repo_readme": row["repo_readme"],
    }


def _validate_license(value: Any, index: int) -> dict[str, object]:
    context = f"benchmark license sidecar.licenses[{index}]"
    license_entry = _exact_object(value, _LICENSE_FIELDS, context)
    repo_name = license_entry["repo_name"]
    commit = license_entry["repo_commit"]
    if not isinstance(repo_name, str) or not _REPOSITORY.fullmatch(repo_name):
        _fail("E_BENCHMARK_LICENSE", f"{context}.repo_name must be owner/repo")
    if not isinstance(commit, str) or not _COMMIT.fullmatch(commit):
        _fail("E_BENCHMARK_LICENSE", f"{context}.repo_commit must be pinned")
    spdx = license_entry["license_spdx"]
    if (
        not isinstance(spdx, str)
        or spdx.upper() in {"UNKNOWN", "NOASSERTION"}
        or not _SPDX.fullmatch(spdx)
    ):
        _fail("E_BENCHMARK_LICENSE", f"{context}.license_spdx must be reviewed SPDX")
    evidence_url = license_entry["license_evidence_url"]
    expected = f"https://github.com/{repo_name}/blob/{commit}/"
    if not isinstance(evidence_url, str) or not evidence_url.startswith(expected):
        _fail(
            "E_BENCHMARK_LICENSE",
            f"{context}.license_evidence_url must pin repository commit",
        )
    digest = license_entry["license_evidence_sha256"]
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        _fail(
            "E_BENCHMARK_LICENSE",
            f"{context}.license_evidence_sha256 must be lowercase SHA-256",
        )
    if license_entry["human_reviewed"] is not True:
        _fail("E_BENCHMARK_LICENSE", f"{context}.human_reviewed must be true")
    return {
        "repo_name": repo_name,
        "repo_commit": commit,
        "license_spdx": spdx,
        "license_evidence_url": evidence_url,
        "license_evidence_sha256": digest,
        "human_reviewed": True,
    }


def _validated_payload(
    input_path: Path,
    sidecar_path: Path,
) -> tuple[list[dict[str, str]], list[dict[str, object]], bytes, bytes]:
    sidecar = validate_contract(
        read_json_object(sidecar_path),
        required=_SIDECAR_FIELDS,
        optional=set(),
        context="benchmark license sidecar",
    )
    if sidecar["dataset_id"] != DATASET_ID:
        _fail("E_BENCHMARK_DATASET", f"dataset_id must be {DATASET_ID}")
    if sidecar["dataset_revision"] != DATASET_REVISION:
        _fail("E_BENCHMARK_REVISION", "dataset_revision does not match pinned revision")
    if sidecar["dataset_license_spdx"] != DATASET_LICENSE_SPDX:
        _fail(
            "E_BENCHMARK_DATASET_LICENSE",
            f"dataset metadata license must be {DATASET_LICENSE_SPDX}",
        )
    if sidecar["split"] != "test":
        _fail("E_BENCHMARK_SPLIT", "only pinned test split may be imported")
    input_format = sidecar["input_format"]
    if input_format not in {"json", "jsonl"} or input_path.suffix != f".{input_format}":
        _fail("E_BENCHMARK_FORMAT", "input_format must match .json or .jsonl suffix")

    raw_input, raw_rows = _read_input(input_path, input_format)
    input_digest = hashlib.sha256(raw_input).hexdigest()
    if sidecar["input_sha256"] != input_digest:
        _fail("E_BENCHMARK_INPUT_HASH", "input_sha256 does not match supplied bytes")
    if not raw_rows or len(raw_rows) > MAX_ROWS:
        _fail("E_BENCHMARK_ROWS", f"benchmark input must contain 1..{MAX_ROWS} rows")

    rows = [_validate_row(value, index) for index, value in enumerate(raw_rows)]
    row_identities = [(row["repo_name"], row["repo_commit"]) for row in rows]
    if len(row_identities) != len(set(row_identities)):
        _fail("E_BENCHMARK_GOLD_COLLISION", "duplicate benchmark repository identity")

    raw_licenses = sidecar["licenses"]
    if not isinstance(raw_licenses, list):
        _fail("E_BENCHMARK_LICENSE", "licenses must be a list")
    licenses = [
        _validate_license(value, index) for index, value in enumerate(raw_licenses)
    ]
    license_identities = [
        (str(item["repo_name"]), str(item["repo_commit"])) for item in licenses
    ]
    if len(license_identities) != len(set(license_identities)):
        _fail("E_BENCHMARK_LICENSE", "duplicate license identity")
    if sorted(row_identities) != sorted(license_identities):
        _fail(
            "E_BENCHMARK_LICENSE_COVERAGE",
            "license sidecar must cover every row exactly once",
        )
    return rows, licenses, raw_input, sidecar_path.read_bytes()


def _validate_paths(input_path: Path, sidecar_path: Path, output_dir: Path) -> None:
    if input_path == sidecar_path:
        _fail("E_BENCHMARK_PATH", "input and license sidecar must differ")
    input_real = input_path.resolve(strict=True)
    sidecar_real = sidecar_path.resolve(strict=True)
    output_real = output_dir.resolve(strict=False)
    if output_dir.name != "evaluation-only":
        _fail("E_BENCHMARK_PATH", "output directory must be named evaluation-only")
    if _inside(output_real, _REPOSITORY_ROOT):
        _fail("E_BENCHMARK_PATH", "evaluation-only output must stay outside repository")
    for source in (input_real, sidecar_real):
        if _inside(source, output_real) or _inside(output_real, source):
            _fail("E_BENCHMARK_PATH", "output must not overlap input paths")
    if output_dir.exists():
        _fail("E_BENCHMARK_OUTPUT_EXISTS", "evaluation-only output already exists")


def import_benchmark(
    input_path: Path,
    sidecar_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    _validate_paths(input_path, sidecar_path, output_dir)
    rows, licenses, raw_input, raw_sidecar = _validated_payload(
        input_path,
        sidecar_path,
    )
    license_by_identity = {
        (str(item["repo_name"]), str(item["repo_commit"])): item
        for item in licenses
    }
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.tmp-", dir=parent))
    try:
        rows_dir = stage / "rows"
        rows_dir.mkdir()
        manifest_rows: list[dict[str, object]] = []
        for index, row in enumerate(rows):
            filename = f"{index:03d}-{row['repo_name'].replace('/', '-')}.json"
            row_bytes = canonical_json_bytes(row)
            (rows_dir / filename).write_bytes(row_bytes)
            license_entry = license_by_identity[
                (row["repo_name"], row["repo_commit"])
            ]
            manifest_rows.append(
                {
                    "repo_name": row["repo_name"],
                    "repo_commit": row["repo_commit"],
                    "path": f"rows/{filename}",
                    "sha256": hashlib.sha256(row_bytes).hexdigest(),
                    "license_spdx": license_entry["license_spdx"],
                    "license_evidence_url": license_entry["license_evidence_url"],
                    "license_evidence_sha256": license_entry[
                        "license_evidence_sha256"
                    ],
                }
            )
        manifest = {
            "schema_version": 1,
            "dataset_id": DATASET_ID,
            "dataset_revision": DATASET_REVISION,
            "dataset_license_spdx": DATASET_LICENSE_SPDX,
            "purpose": "evaluation-only",
            "coverage": "generator-filtered-python-files",
            "split": "test",
            "input_sha256": hashlib.sha256(raw_input).hexdigest(),
            "license_sidecar_sha256": hashlib.sha256(raw_sidecar).hexdigest(),
            "row_count": len(rows),
            "rows": manifest_rows,
        }
        (stage / "manifest.json").write_bytes(canonical_json_bytes(manifest))
        os.replace(stage, output_dir)
    finally:
        if stage.exists():
            shutil.rmtree(stage)

    return {
        "schema_version": 1,
        "status": "imported",
        "dataset_id": DATASET_ID,
        "dataset_revision": DATASET_REVISION,
        "row_count": len(rows),
        "manifest_sha256": canonical_sha256(manifest),
    }
