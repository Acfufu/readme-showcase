from __future__ import annotations

import hashlib
import importlib
import os
import stat
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Protocol

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object_bytes,
    read_regular_bytes,
    validate_contract,
)
from ..contracts.run import STAGE_NAMES
from .workspace import RunWorkspace

_CORE = importlib.import_module(
    "skill.scripts.pipeline_core" if __package__.startswith("skill.") else "pipeline_core"
)
CoreContractError = _CORE.ContractError
evaluate_generated_bundle = _CORE.evaluate_generated_bundle
retrieve_patterns = _CORE.retrieve_patterns
scan_repository = _CORE.scan_repository
validate_generated_bundle = _CORE.validate_generated_bundle


MAX_CANDIDATE_BYTES = 16 * 1024 * 1024
_PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DATASET = next(
    root / "dataset/retrieval/manifest.json"
    for root in (_PACKAGE_ROOT, _PACKAGE_ROOT.parent)
    if (root / "dataset/retrieval/manifest.json").is_file()
)


@dataclass
class RunContext:
    workspace: RunWorkspace
    manifest: dict[str, Any]
    cache: dict[str, Any] = field(default_factory=dict)

    def attempt_file(self, stage_index: int, name: str) -> Path:
        stage = self.manifest["stages"][stage_index]
        return (
            self.workspace.root
            / "stages"
            / f"{stage_index + 1:02d}-{stage['name']}"
            / "attempts"
            / str(stage["attempt"])
            / name
        )


@dataclass(frozen=True)
class StageResult:
    status: str
    files: Mapping[str, bytes] = field(default_factory=dict)
    output_sha256: str | None = None


class Stage(Protocol):
    name: str

    def fingerprint(self, context: RunContext) -> str: ...

    def execute(self, context: RunContext) -> StageResult: ...


def _canonical_object(path: Path, code: str = "E_RUN_INPUT") -> tuple[bytes, dict[str, Any]]:
    raw, value = read_json_object_bytes(path)
    if raw != canonical_json_bytes(value):
        raise ContractError(code, f"JSON input must use canonical bytes: {path.name}")
    return raw, value


def _upstream(context: RunContext, index: int) -> str | None:
    return context.manifest["stages"][index]["output_sha256"]


def _validate_plan(plan: Any, mode: str) -> dict[str, Any]:
    value = validate_contract(
        plan,
        required={
            "schema_version", "mode", "languages", "sections", "visual_intent",
            "diagram_route", "commands", "evidence_ids",
        },
        optional=set(),
        context="README plan",
    )
    if value["mode"] != mode or value["diagram_route"] not in {"none", "static", "elk"}:
        raise ContractError("E_BUNDLE_PLAN", "README plan mode or diagram route is unsupported")
    for name in ("languages", "sections", "commands", "evidence_ids"):
        items = value[name]
        if not isinstance(items, list) or any(not isinstance(item, str) or not item for item in items):
            raise ContractError("E_SCHEMA_TYPE", f"README plan.{name} must be a string list")
        if items != sorted(set(items)):
            raise ContractError("E_SCHEMA_TYPE", f"README plan.{name} must be sorted and unique")
    if not value["languages"] or not set(value["languages"]).issubset({"en", "zh"}):
        raise ContractError("E_README_LANGUAGE", "README plan.languages must contain en and/or zh")
    if not isinstance(value["visual_intent"], str) or not value["visual_intent"]:
        raise ContractError("E_SCHEMA_TYPE", "README plan.visual_intent must be text")
    return value


class ScanStage:
    name = "scan"

    def _value(self, context: RunContext) -> dict[str, Any]:
        if self.name not in context.cache:
            context.cache[self.name] = scan_repository(context.workspace.target_root)
        return context.cache[self.name]

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256(self._value(context))

    def execute(self, context: RunContext) -> StageResult:
        value = self._value(context)
        return StageResult("pass" if value["status"] == "complete" else "failed", {"repository-evidence.json": canonical_json_bytes(value)})


class RetrieveStage:
    name = "retrieve"

    def fingerprint(self, context: RunContext) -> str:
        dataset_raw = read_regular_bytes(DATASET, maximum=MAX_CANDIDATE_BYTES)
        return canonical_sha256({"dataset_sha256": hashlib.sha256(dataset_raw).hexdigest(), "scan": _upstream(context, 0), "project_type": context.manifest["configuration"]["project_type"]})

    def execute(self, context: RunContext) -> StageResult:
        _, evidence = _canonical_object(context.attempt_file(0, "repository-evidence.json"))
        _, dataset = read_json_object_bytes(DATASET)
        packet = retrieve_patterns(
            evidence,
            dataset,
            project_type=context.manifest["configuration"]["project_type"],
            sections=[],
            tags=[],
            mode="production",
        )
        return StageResult("pass", {"retrieval-packet.json": canonical_json_bytes(packet)})


class PlanImportStage:
    name = "plan-import"

    def _path(self, context: RunContext) -> Path:
        return context.workspace.root / "inputs/readme-plan.json"

    def fingerprint(self, context: RunContext) -> str:
        try:
            raw = read_regular_bytes(self._path(context), maximum=MAX_CANDIDATE_BYTES)
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                return canonical_sha256({"plan": None})
            raise
        return hashlib.sha256(raw).hexdigest()

    def execute(self, context: RunContext) -> StageResult:
        try:
            raw, plan = _canonical_object(self._path(context))
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                return StageResult("waiting-for-plan")
            raise
        _validate_plan(plan, context.manifest["configuration"]["mode"])
        return StageResult("pass", {"readme-plan.json": raw})


class GenerationRequestStage:
    name = "generation-request"

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256({"plan": _upstream(context, 2), "retrieval": _upstream(context, 1), "scan": _upstream(context, 0)})

    def execute(self, context: RunContext) -> StageResult:
        request = {
            "schema_version": 1,
            "run_id": context.manifest["run_id"],
            "mode": context.manifest["configuration"]["mode"],
            "locales": context.manifest["configuration"]["locales"],
            "inputs": {
                "repository_evidence_sha256": _upstream(context, 0),
                "retrieval_sha256": _upstream(context, 1),
                "plan_sha256": _upstream(context, 2),
            },
            "candidate_paths": [
                "README.md", "README_zh.md", "claim-map.json", "asset-manifest.json", "assets/**",
            ],
        }
        return StageResult("pass", {"generation-request.json": canonical_json_bytes(request)})


def candidate_files(context: RunContext) -> list[tuple[str, bytes]] | None:
    root = context.workspace.root / "stages/05-candidate"
    _, plan = _canonical_object(context.attempt_file(2, "readme-plan.json"))
    required = ["claim-map.json", "asset-manifest.json"]
    if context.manifest["configuration"]["mode"] == "readme":
        required.append("README.md")
    if "zh" in plan["languages"]:
        required.append("README_zh.md")
    output: list[tuple[str, bytes]] = []
    for relative in required:
        try:
            raw = read_regular_bytes(root / relative, maximum=MAX_CANDIDATE_BYTES)
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                return None
            raise
        output.append((relative, raw))
    assets = root / "assets"
    if assets.exists() or assets.is_symlink():
        if assets.is_symlink() or not assets.is_dir():
            raise ContractError("E_RUN_PATH", "candidate assets must be a real directory")
        for path in sorted(assets.rglob("*"), key=lambda item: os.fsencode(item.relative_to(root).as_posix())):
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or (not stat.S_ISREG(info.st_mode) and not stat.S_ISDIR(info.st_mode)):
                raise ContractError("E_RUN_PATH", "candidate assets may contain only real directories and files")
            if stat.S_ISREG(info.st_mode):
                output.append((path.relative_to(root).as_posix(), read_regular_bytes(path, maximum=MAX_CANDIDATE_BYTES)))
    return output


class CandidateImportStage:
    name = "candidate"

    def fingerprint(self, context: RunContext) -> str:
        files = candidate_files(context)
        return canonical_sha256({"candidate": None if files is None else [{"path": name, "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in files]})

    def execute(self, context: RunContext) -> StageResult:
        files = candidate_files(context)
        if files is None:
            return StageResult("waiting-for-candidate")
        return StageResult("pass", output_sha256=self.fingerprint(context))


def _read_candidate(context: RunContext, name: str) -> bytes:
    return read_regular_bytes(context.workspace.root / "stages/05-candidate" / name, maximum=MAX_CANDIDATE_BYTES)


class BundleAssembleStage:
    name = "bundle-assemble"

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256({"candidate": _upstream(context, 4), "plan": _upstream(context, 2), "retrieval": _upstream(context, 1)})

    def execute(self, context: RunContext) -> StageResult:
        _, manifest = _canonical_object(context.workspace.root / "stages/05-candidate/asset-manifest.json")
        assets = manifest.get("assets")
        if not isinstance(assets, list):
            raise ContractError("E_SCHEMA_TYPE", "asset manifest.assets must be a list")
        candidate_assets = []
        for item in assets:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str) or not isinstance(item.get("sha256"), str):
                raise ContractError("E_SCHEMA_TYPE", "asset manifest entries require path and sha256")
            path = PurePosixPath(item["path"])
            if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0] != "assets":
                raise ContractError("E_RUN_PATH", "candidate asset path must stay under assets")
            raw = _read_candidate(context, path.as_posix())
            if hashlib.sha256(raw).hexdigest() != item["sha256"]:
                raise ContractError("E_BUNDLE_HASH", "candidate asset hash mismatch")
            candidate_assets.append({"path": path.as_posix(), "sha256": item["sha256"]})
        candidate_assets.sort(key=lambda item: item["path"])
        mode = context.manifest["configuration"]["mode"]
        readme = None
        if mode == "readme":
            raw = _read_candidate(context, "README.md")
            readme = {"path": "README.md", "sha256": hashlib.sha256(raw).hexdigest()}
        refs = {
            "plan": ("readme-plan.json", context.attempt_file(2, "readme-plan.json")),
            "retrieval": ("retrieval-packet.json", context.attempt_file(1, "retrieval-packet.json")),
            "claim_map": ("claim-map.json", context.workspace.root / "stages/05-candidate/claim-map.json"),
            "asset_manifest": ("asset-manifest.json", context.workspace.root / "stages/05-candidate/asset-manifest.json"),
        }
        artifacts = {
            key: {"path": name, "sha256": hashlib.sha256(read_regular_bytes(path, maximum=MAX_CANDIDATE_BYTES)).hexdigest()}
            for key, (name, path) in refs.items()
        }
        bundle = {
            "schema_version": 1,
            "mode": mode,
            "target": {"repository": context.manifest["target"]["repository"], "base_sha": context.manifest["target"]["base_sha"]},
            "candidate": {"readme": readme, "assets": candidate_assets},
            "artifacts": artifacts,
        }
        return StageResult("pass", {"generated-readme-bundle.json": canonical_json_bytes(bundle)})


def _materialize(context: RunContext, root: Path) -> dict[str, Any]:
    _, bundle = _canonical_object(context.attempt_file(5, "generated-readme-bundle.json"))
    sources = {
        "repository-evidence.json": context.attempt_file(0, "repository-evidence.json"),
        "retrieval-packet.json": context.attempt_file(1, "retrieval-packet.json"),
        "readme-plan.json": context.attempt_file(2, "readme-plan.json"),
        "claim-map.json": context.workspace.root / "stages/05-candidate/claim-map.json",
        "asset-manifest.json": context.workspace.root / "stages/05-candidate/asset-manifest.json",
    }
    for name in ("README.md", "README_zh.md"):
        candidate = context.workspace.root / "stages/05-candidate" / name
        if candidate.exists():
            sources[name] = candidate
    for item in bundle["candidate"]["assets"]:
        sources[item["path"]] = context.workspace.root / "stages/05-candidate" / item["path"]
    for relative, source in sources.items():
        destination = root.joinpath(*PurePosixPath(relative).parts)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(read_regular_bytes(source, maximum=MAX_CANDIDATE_BYTES))
    return bundle


class ValidateStage:
    name = "validation"

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256({"bundle": _upstream(context, 5)})

    def execute(self, context: RunContext) -> StageResult:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = _materialize(context, root)
            try:
                report = validate_generated_bundle(bundle, root)
            except (ContractError, CoreContractError) as exc:
                report = {"schema_version": 1, "status": "fail", "diagnostics": [{"code": exc.code}]}
        return StageResult("pass" if report["status"] == "pass" else "failed", {"validation-report.json": canonical_json_bytes(report)})


class EvaluateStage:
    name = "evaluation"

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256({"bundle": _upstream(context, 5), "validation": _upstream(context, 6)})

    def execute(self, context: RunContext) -> StageResult:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = _materialize(context, root)
            report = evaluate_generated_bundle(bundle, root)
        return StageResult("pass" if report["status"] == "pass" else "failed", {"evaluation-report.json": canonical_json_bytes(report)})


STAGES: tuple[Stage, ...] = (
    ScanStage(), RetrieveStage(), PlanImportStage(), GenerationRequestStage(),
    CandidateImportStage(), BundleAssembleStage(), ValidateStage(), EvaluateStage(),
)

if tuple(stage.name for stage in STAGES) != STAGE_NAMES:
    raise RuntimeError("stage registry differs from run-manifest contract")
