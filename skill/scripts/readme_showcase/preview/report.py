from __future__ import annotations

import difflib
import hashlib
import importlib
import json
import os
import stat
import sys
from pathlib import Path, PurePosixPath
from typing import Any

from ...pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    read_regular_bytes,
)
from ..contracts.assets import validate_asset_manifest_v3
from ..orchestration.stages import CandidateImportStage, RunContext
from ..orchestration.workspace import RunWorkspace
from ..visual_kernel.reader import load_compiled_visual

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
            info = root.lstat()
        except OSError as exc:
            raise ContractError("E_PREVIEW_STALE", f"preview stage output is unavailable: {stage['name']}") from exc
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ContractError("E_PREVIEW_PATH", f"preview stage output is unsafe: {stage['name']}")

        # RunWorkspace owns the immutable-attempt hashing trust boundary.  In
        # particular, compiled Bundle v3 attempts contain nested directories;
        # preview must not maintain a second flat/recursive traversal here.
        observed = workspace.attempt_output_sha256(index, stage["attempt"])
        if observed is None or observed != stage["output_sha256"]:
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


def _compiled_report_projection(
    workspace: RunWorkspace,
    manifest: dict[str, Any],
    snapshot: PreviewInputSnapshot,
) -> dict[str, Any]:
    bundle_path = _attempt_path(workspace, manifest, 5, "generated-readme-bundle.json")
    if bundle_path is None:
        raise ContractError("E_PREVIEW_STATE", "compiled preview requires a committed generated bundle")
    bundle_raw = snapshot.read(bundle_path)
    bundle = _canonical_object(snapshot, bundle_path)
    if bundle is None or bundle.get("schema_version") != 3:
        raise ContractError("E_SCHEMA_VERSION", "compiled preview requires Generated Bundle v3")

    loaded = load_compiled_visual(bundle_path.parent, bundle)
    manifest_path = _attempt_path(workspace, manifest, 5, "asset-manifest.json")
    if manifest_path is None:
        raise ContractError("E_PREVIEW_STATE", "compiled preview requires an Asset Manifest v3")
    manifest_payload = _canonical_object(snapshot, manifest_path)
    if manifest_payload is None:
        raise ContractError("E_PREVIEW_PATH", "compiled Asset Manifest is unavailable")
    normalized = validate_asset_manifest_v3(manifest_payload, artifact_root=bundle_path.parent)
    compiled_manifest = normalized["compiled"]
    compiled_bundle = bundle.get("compiled")
    if not isinstance(compiled_bundle, dict) or compiled_bundle.get("fingerprint") != loaded.inventory_sha256:
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled inventory reference is stale")

    references = {
        name: [{"path": item["path"], "sha256": item["sha256"]} for item in compiled_manifest[name]]
        for name in ("svgs", "scenes", "gates", "timelines", "interactions")
    }
    by_layer = {
        name: {(item["locale"], item["variant"]) for item in items}
        for name, items in ((name, compiled_manifest[name]) for name in references)
    }
    keys = set().union(*(set(values) for values in by_layer.values()))
    checks = []
    for locale, variant in sorted(keys, key=lambda item: (item[0].encode("utf-8"), item[1].encode("utf-8"))):
        check = {"locale": locale, "variant": variant}
        for name in references:
            check[name[:-1] if name.endswith("s") else name] = (locale, variant) in by_layer[name]
        check["complete"] = all(check[name] for name in ("svg", "scene", "gate", "timeline", "interaction"))
        checks.append(check)
    locales = {check["locale"] for check in checks}
    if (
        not checks
        or not all(check["complete"] for check in checks)
        or any(
            {
                check["variant"]
                for check in checks
                if check["locale"] == locale
            }
            != {"desktop", "mobile"}
            for locale in locales
        )
    ):
        raise ContractError("E_VISUAL_FINGERPRINT", "compiled viewport set is incomplete")

    inventory_ref = compiled_manifest["inventory"]
    return {
        "bundle": {
            "path": bundle_path.relative_to(workspace.root).as_posix(),
            "sha256": hashlib.sha256(bundle_raw or b"").hexdigest(),
        },
        "inventory": {
            "path": inventory_ref["path"],
            "sha256": inventory_ref["sha256"],
            "fingerprint": loaded.inventory_sha256,
        },
        "artifacts": references,
        "identities": compiled_manifest["identities"],
        "viewports": {"complete": True, "checks": checks},
    }


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
    explicit_locales = plan.get("locales") if isinstance(plan, dict) and plan.get("schema_version") == 2 else None
    locale_by_path: dict[str, str] | None = None
    if isinstance(explicit_locales, list):
        locale_by_path = {entry["readme_path"]: entry["tag"] for entry in explicit_locales}
        readmes = {
            path: _text(snapshot, candidate_root.joinpath(*PurePosixPath(path).parts))
            for path in locale_by_path
        }
        diffs = {
            path: _diff(
                path,
                _text(snapshot, workspace.target_root.joinpath(*PurePosixPath(path).parts), required=False),
                readmes[path],
            )
            for path in locale_by_path
        }
    else:
        languages = plan.get("languages", []) if isinstance(plan, dict) else []
        localized_required = isinstance(languages, list) and "zh" in languages
        primary = _text(snapshot, candidate_root / "README.md")
        localized = _text(
            snapshot,
            candidate_root / "README_zh.md",
            required=localized_required,
            fallback="Localized README was not requested for this run.\n",
        ) if localized_required else "Localized README was not requested for this run.\n"
        readmes = {"README.md": primary, "README_zh.md": localized}
        diffs = {
            "README.md": _diff("README.md", _text(snapshot, workspace.target_root / "README.md", required=False), primary),
            "README_zh.md": _diff("README_zh.md", _text(snapshot, workspace.target_root / "README_zh.md", required=False) if localized_required else "", localized),
        }
    editorial = evaluate_editorial(
        readmes,
        planned_sections=planned_sections if isinstance(planned_sections, list) else [],
        diff_lines={name: sum(line.startswith(("+", "-")) and not line.startswith(("+++", "---")) for line in value.splitlines()) for name, value in diffs.items()},
        locale_by_path=locale_by_path,
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
        "mobile": {"source": next(iter(readmes)), "width_px": 375},
        "revision": {"current": manifest.get("current_revision") or "none"},
    }
    if isinstance(plan, dict) and plan.get("schema_version") == 3 and plan.get("diagram_route") == "compiled":
        report["compiled"] = _compiled_report_projection(workspace, manifest, snapshot)
    if locale_by_path is not None:
        report["locale_by_path"] = locale_by_path
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
