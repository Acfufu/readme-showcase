from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import os
import stat
import sys
from pathlib import Path
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_regular_bytes,
)
from ..orchestration.stages import CandidateImportStage, RunContext
from ..orchestration.workspace import RunWorkspace

if __package__.startswith("skill."):
    from ..evaluation.editorial import evaluate_editorial
else:
    # Direct installed-Skill execution exposes ``pipeline_core`` at top level.
    # Alias that already-supported module so editorial's relative import does
    # not create a second, incorrectly prefixed core module.
    sys.modules.setdefault("scripts.pipeline_core", importlib.import_module("pipeline_core"))
    evaluate_editorial = importlib.import_module(
        "scripts.readme_showcase.evaluation.editorial"
    ).evaluate_editorial


MAX_PREVIEW_INPUT_BYTES = 16 * 1024 * 1024


def _regular_bytes(path: Path, *, required: bool = True) -> bytes | None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        if not required:
            return None
        raise ContractError("E_PREVIEW_PATH", f"preview input is missing: {path.name}")
    except OSError as exc:
        raise ContractError("E_PREVIEW_PATH", f"cannot inspect preview input: {path.name}") from exc
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode):
        raise ContractError("E_PREVIEW_PATH", f"preview input must be a regular file: {path.name}")
    try:
        return read_regular_bytes(path, maximum=MAX_PREVIEW_INPUT_BYTES)
    except ContractError as exc:
        raise ContractError("E_PREVIEW_PATH", f"cannot read preview input: {path.name}") from exc


class PreviewInputSnapshot:
    def __init__(self) -> None:
        self._values: dict[Path, bytes | None] = {}

    def read(self, path: Path, *, required: bool = True) -> bytes | None:
        absolute = path.absolute()
        if absolute not in self._values:
            self._values[absolute] = _regular_bytes(absolute, required=required)
        value = self._values[absolute]
        if required and value is None:
            raise ContractError("E_PREVIEW_PATH", f"preview input is missing: {absolute.name}")
        return value

    def assert_unchanged(self) -> None:
        for path, expected in sorted(
            self._values.items(), key=lambda item: os.fsencode(os.fspath(item[0]))
        ):
            observed = _regular_bytes(path, required=False)
            if observed != expected:
                raise ContractError(
                    "E_PREVIEW_STALE",
                    f"preview input changed during rendering: {path.name}",
                )


def _text(
    snapshot: PreviewInputSnapshot,
    path: Path,
    *,
    required: bool = True,
    fallback: str = "",
) -> str:
    raw = snapshot.read(path, required=required)
    if raw is None:
        return fallback
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ContractError("E_PREVIEW_PATH", f"preview input must be UTF-8: {path.name}") from exc


def _canonical_object(
    snapshot: PreviewInputSnapshot,
    path: Path,
    *,
    required: bool = True,
) -> dict[str, Any] | None:
    raw = snapshot.read(path, required=required)
    if raw is None:
        return None
    try:
        text = raw.decode("utf-8")
        value = json.loads(text)
    except (UnicodeDecodeError, ValueError) as exc:
        raise ContractError("E_PREVIEW_PATH", f"preview JSON is malformed: {path.name}") from exc
    if not isinstance(value, dict) or raw != canonical_json_bytes(value):
        raise ContractError("E_PREVIEW_PATH", f"preview JSON must be a canonical object: {path.name}")
    return value


def _attempt_path(workspace: RunWorkspace, manifest: dict[str, Any], index: int, name: str) -> Path | None:
    attempt = manifest["stages"][index]["attempt"]
    if attempt == 0:
        return None
    return workspace.root / "stages" / f"{index + 1:02d}-{manifest['stages'][index]['name']}" / "attempts" / str(attempt) / name


def _assert_stage_outputs_current(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    snapshot: PreviewInputSnapshot | None = None,
) -> None:
    for index in (0, 1, 2, 3, 5, 6, 7):
        stage = manifest["stages"][index]
        if stage["attempt"] == 0:
            continue
        root = workspace.root / "stages" / f"{index + 1:02d}-{stage['name']}" / "attempts" / str(stage["attempt"])
        try:
            entries = sorted(root.iterdir(), key=lambda item: os.fsencode(item.name))
        except OSError as exc:
            raise ContractError("E_PREVIEW_STALE", f"preview stage output is unavailable: {stage['name']}") from exc
        if not entries or any(path.is_symlink() or not path.is_file() for path in entries):
            raise ContractError("E_PREVIEW_PATH", f"preview stage output is unsafe: {stage['name']}")
        projection = []
        for path in entries:
            raw = snapshot.read(path) if snapshot is not None else _regular_bytes(path)
            projection.append({"path": path.name, "sha256": hashlib.sha256(raw or b"").hexdigest()})
        if canonical_sha256(projection) != stage["output_sha256"]:
            raise ContractError("E_PREVIEW_STALE", f"preview stage output hash is stale: {stage['name']}")


def _diff(path: str, before: str, after: str) -> str:
    lines = list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"base/{path}", tofile=f"candidate/{path}", lineterm="",
        )
    )
    return "\n".join(lines) + ("\n" if lines else "(no changes)\n")


def _evidence(
    snapshot: PreviewInputSnapshot,
    workspace: RunWorkspace,
    manifest: dict[str, Any],
) -> list[dict[str, str]]:
    references = (
        ("repository evidence", _attempt_path(workspace, manifest, 0, "repository-evidence.json")),
        ("retrieval packet", _attempt_path(workspace, manifest, 1, "retrieval-packet.json")),
        ("README plan", _attempt_path(workspace, manifest, 2, "readme-plan.json")),
        ("claim map", workspace.root / "stages/05-candidate/claim-map.json"),
        ("asset manifest", workspace.root / "stages/05-candidate/asset-manifest.json"),
    )
    output: list[dict[str, str]] = []
    for label, path in references:
        if path is None:
            continue
        raw = snapshot.read(path, required=False)
        if raw is None:
            continue
        output.append({
            "label": label,
            "path": path.relative_to(workspace.root).as_posix(),
            "sha256": hashlib.sha256(raw).hexdigest(),
        })
    return output or [{"label": "unavailable", "path": "none", "sha256": "0" * 64}]


def build_preview_snapshot(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    snapshot: PreviewInputSnapshot,
) -> tuple[dict[str, Any], dict[str, str]]:
    candidate_root = workspace.root / "stages/05-candidate"
    try:
        candidate_info = candidate_root.lstat()
    except OSError as exc:
        raise ContractError("E_PREVIEW_PATH", "candidate root is unavailable") from exc
    if not stat.S_ISDIR(candidate_info.st_mode) or stat.S_ISLNK(candidate_info.st_mode):
        raise ContractError("E_PREVIEW_PATH", "candidate root must be a real directory")

    manifest_raw = snapshot.read(workspace.root / "run-manifest.json")
    if manifest_raw != canonical_json_bytes(manifest):
        raise ContractError("E_PREVIEW_STALE", "run manifest changed before preview rendering")
    _assert_stage_outputs_current(workspace, manifest, snapshot)

    context = RunContext(workspace, manifest)
    candidate_stage = manifest["stages"][4]
    if candidate_stage["status"] != "pass" or not isinstance(candidate_stage["output_sha256"], str):
        raise ContractError("E_PREVIEW_STATE", "preview requires a committed candidate stage")

    plan_path = _attempt_path(workspace, manifest, 2, "readme-plan.json")
    plan = _canonical_object(snapshot, plan_path) if plan_path is not None else None
    planned_sections = plan.get("sections", []) if isinstance(plan, dict) else []
    languages = plan.get("languages", []) if isinstance(plan, dict) else []
    localized_required = isinstance(languages, list) and "zh" in languages

    primary = _text(snapshot, candidate_root / "README.md")
    localized = _text(
        snapshot,
        candidate_root / "README_zh.md",
        required=localized_required,
        fallback="Localized README was not requested for this run.\n",
    ) if localized_required else "Localized README was not requested for this run.\n"
    before_primary = _text(
        snapshot, workspace.target_root / "README.md", required=False
    )
    before_localized = (
        _text(snapshot, workspace.target_root / "README_zh.md", required=False)
        if localized_required else ""
    )
    readmes = {"README.md": primary, "README_zh.md": localized}
    diffs = {
        "README.md": _diff("README.md", before_primary, primary),
        "README_zh.md": _diff("README_zh.md", before_localized, localized),
    }
    editorial = evaluate_editorial(
        readmes,
        planned_sections=planned_sections if isinstance(planned_sections, list) else [],
        diff_lines={name: sum(line.startswith(("+", "-")) and not line.startswith(("+++", "---")) for line in value.splitlines()) for name, value in diffs.items()},
    ).as_dict()

    validation_path = _attempt_path(workspace, manifest, 6, "validation-report.json")
    validation = _canonical_object(snapshot, validation_path, required=False) if validation_path else None
    evaluation_path = _attempt_path(workspace, manifest, 7, "evaluation-report.json")
    evaluation = _canonical_object(snapshot, evaluation_path, required=False) if evaluation_path else None
    diagnostics = (
        validation.get("diagnostics", [])
        if isinstance(validation, dict) and isinstance(validation.get("diagnostics"), list)
        else []
    )
    if not diagnostics:
        diagnostics = [{"code": "I_PREVIEW_NO_DIAGNOSTICS", "message": "No validation diagnostics were reported."}]
    if evaluation is None:
        evaluation = {"status": "not-available", "reason": "Evaluation stage did not produce a report."}

    claim_map = _canonical_object(snapshot, candidate_root / "claim-map.json") or {}
    evidence = _evidence(snapshot, workspace, manifest)
    current_candidate_sha256 = CandidateImportStage().fingerprint(context)
    if current_candidate_sha256 != candidate_stage["output_sha256"]:
        raise ContractError("E_PREVIEW_STALE", "candidate bytes differ from committed candidate stage")
    report = {
        "schema_version": 1,
        "generated_at": manifest["created_at"],
        "run_id": manifest["run_id"],
        "run_status": manifest["status"],
        "candidate_sha256": current_candidate_sha256,
        "diff": diffs,
        "evidence": evidence,
        "claims": claim_map,
        "diagnostics": diagnostics,
        "evaluation": evaluation,
        "editorial": editorial,
        "mobile": {"source": "README.md", "width_px": 375},
        "revision": {"current": manifest.get("current_revision") or "none"},
    }
    return report, readmes


def assert_preview_inputs_current(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    snapshot: PreviewInputSnapshot,
) -> None:
    manifest_raw = _regular_bytes(workspace.root / "run-manifest.json")
    if manifest_raw != canonical_json_bytes(manifest):
        raise ContractError("E_PREVIEW_STALE", "run manifest changed during preview rendering")
    _assert_stage_outputs_current(workspace, manifest)
    expected = manifest["stages"][4]["output_sha256"]
    if CandidateImportStage().fingerprint(RunContext(workspace, manifest)) != expected:
        raise ContractError("E_PREVIEW_STALE", "candidate bytes changed during preview rendering")
    snapshot.assert_unchanged()


def build_preview_report(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, str]]:
    return build_preview_snapshot(workspace, manifest, PreviewInputSnapshot())
