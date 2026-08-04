from __future__ import annotations

import copy
import hashlib
import importlib
import json
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
    write_bytes_atomic,
)
from ..contracts.run import STAGE_NAMES
from ..contracts.plan import validate_readme_plan
from ..contracts.assets import validate_asset_manifest
from ..contracts.common import normalize_posix_path
from ..evidence.adapters import adapt_v1_repository_evidence
from ..generation.assembler import assemble_generated_bundle_v3
from ..generation.request import (
    MAX_GENERATION_REQUEST_BYTES,
    build_generation_request,
    canonical_generation_request,
)
from ..visual_kernel import compile_visual
from ..visual_kernel.reader import load_compiled_visual
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


def _v3_evidence_graph(evidence: Mapping[str, Any]) -> dict[str, Any]:
    version = evidence.get("schema_version")
    if type(version) is int and version == 1:
        return adapt_v1_repository_evidence(evidence)
    if type(version) is int and version == 2:
        return dict(evidence)
    raise ContractError("E_SCHEMA_VERSION", "README Plan v3 inputs require repository evidence schema_version 1 or 2")


def _validate_compiled_visual_spec(value: object, evidence_graph: object) -> None:
    validator = importlib.import_module(f"{__package__.rsplit('.', 1)[0]}.visual_kernel.model").validate_visual_spec
    validator(value, evidence_graph=evidence_graph)


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
            raw = read_regular_bytes(self._path(context), maximum=MAX_GENERATION_REQUEST_BYTES)
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                return canonical_sha256({"plan": None})
            if exc.code == "E_INPUT_SIZE":
                raise ContractError("E_GENERATION_REQUEST_SIZE", f"README plan exceeds {MAX_GENERATION_REQUEST_BYTES} bytes") from exc
            raise
        return hashlib.sha256(raw).hexdigest()

    def execute(self, context: RunContext) -> StageResult:
        try:
            raw, plan = _canonical_object(self._path(context))
        except ContractError as exc:
            if exc.code == "E_INPUT_NOT_FOUND":
                return StageResult("waiting-for-plan")
            raise
        validate_readme_plan(plan, mode=context.manifest["configuration"]["mode"])
        return StageResult("pass", {"readme-plan.json": raw})


class GenerationRequestStage:
    name = "generation-request"

    def fingerprint(self, context: RunContext) -> str:
        return canonical_sha256({"plan": _upstream(context, 2), "retrieval": _upstream(context, 1), "scan": _upstream(context, 0)})

    def execute(self, context: RunContext) -> StageResult:
        _, evidence = _canonical_object(context.attempt_file(0, "repository-evidence.json"))
        _, retrieval = _canonical_object(context.attempt_file(1, "retrieval-packet.json"))
        _, plan = _canonical_object(context.attempt_file(2, "readme-plan.json"))
        evidence_for_request: Mapping[str, Any] = evidence
        retrieval_for_request: Mapping[str, Any] = retrieval
        if plan["schema_version"] == 3:
            evidence_for_request = _v3_evidence_graph(evidence)
            if type(evidence.get("schema_version")) is int and evidence["schema_version"] == 1:
                retrieval_for_request = copy.deepcopy(retrieval)
                query = retrieval_for_request.get("query")
                if isinstance(query, dict):
                    query["evidence_sha256"] = hashlib.sha256(canonical_json_bytes(evidence_for_request)).hexdigest()
        request = build_generation_request(
            target={
                "repository": context.manifest["target"]["repository"],
                "base_sha": context.manifest["target"]["base_sha"],
            },
            locales=["zh-Hans" if locale == "zh" else locale for locale in context.manifest["configuration"]["locales"]],
            project_classification=context.manifest["configuration"]["project_type"],
            plan=plan,
            retrieval_packet=retrieval_for_request,
            evidence_packet=evidence_for_request,
        )
        return StageResult("pass", {"generation-request.json": canonical_generation_request(request)})


def candidate_files(context: RunContext) -> list[tuple[str, bytes]] | None:
    root = context.workspace.root / "stages/05-candidate"
    _, plan = _canonical_object(context.attempt_file(2, "readme-plan.json"))
    compiled = plan["schema_version"] == 3 and plan["diagram_route"] == "compiled"
    required = ["claim-map.json", "visual-spec.json"] if compiled else ["claim-map.json", "asset-manifest.json"]
    if compiled:
        required.extend(entry["readme_path"] for entry in plan["locales"])
    elif context.manifest["configuration"]["mode"] == "readme":
        if plan["schema_version"] == 2:
            required.extend(entry["readme_path"] for entry in plan["locales"])
        else:
            required.append("README.md")
            if "zh" in plan["languages"]:
                required.append("README_zh.md")
    if compiled:
        manifest = root / "asset-manifest.json"
        if manifest.exists() or manifest.is_symlink():
            raise ContractError("E_SCHEMA_VALUE", "compiled candidates must not supply asset-manifest.json")
    output: list[tuple[str, bytes]] = []
    for relative in required:
        try:
            path = root / relative
            if compiled and relative == "claim-map.json":
                raw, claim_map = _canonical_object(path)
                if claim_map.get("schema_version") != 3:
                    raise ContractError("E_SCHEMA_VERSION", "compiled candidate claim map requires schema_version 3")
            elif compiled and relative == "visual-spec.json":
                raw, visual_spec = _canonical_object(path)
                _, evidence = _canonical_object(context.attempt_file(0, "repository-evidence.json"))
                evidence_graph = _v3_evidence_graph(evidence)
                _validate_compiled_visual_spec(visual_spec, evidence_graph)
            else:
                raw = read_regular_bytes(path, maximum=MAX_CANDIDATE_BYTES)
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
        plan_raw, plan = _canonical_object(context.attempt_file(2, "readme-plan.json"))
        if plan.get("schema_version") == 3 and plan.get("diagram_route") == "compiled":
            return self._execute_compiled(context, plan_raw, plan)
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
            readme_path = plan["locales"][0]["readme_path"] if plan["schema_version"] == 2 else "README.md"
            raw = _read_candidate(context, readme_path)
            readme = {"path": readme_path, "sha256": hashlib.sha256(raw).hexdigest()}
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

    @staticmethod
    def _execute_compiled(context: RunContext, plan_raw: bytes, plan: Mapping[str, Any]) -> StageResult:
        """Compile one validated Plan v3 candidate into a closed stage-6 map."""

        validate_readme_plan(plan, mode=context.manifest["configuration"]["mode"])
        _, evidence = _canonical_object(context.attempt_file(0, "repository-evidence.json"))
        evidence_graph = _v3_evidence_graph(evidence)
        retrieval_raw, _ = _canonical_object(context.attempt_file(1, "retrieval-packet.json"))
        claim_raw, _ = _canonical_object(context.workspace.root / "stages/05-candidate/claim-map.json")
        spec_raw, spec = _canonical_object(context.workspace.root / "stages/05-candidate/visual-spec.json")

        readmes: dict[str, bytes] = {}
        for locale in plan["locales"]:
            readmes[locale["readme_path"]] = _read_candidate(context, locale["readme_path"])

        compiled = compile_visual(spec, evidence_graph)
        compiled_files = dict(compiled.artifacts)
        inventory = json.loads(compiled_files["compiled/inventory.json"].decode("utf-8"))
        layers = {layer["name"]: layer for layer in inventory["layers"]}
        artifact_hashes = {
            record["path"]: record["sha256"]
            for record in layers["artifacts"]["records"]
        }

        def single(path: str) -> dict[str, str]:
            return {"path": path, "sha256": artifact_hashes[path]}

        def variants(layer: str, pattern: str) -> list[dict[str, str]]:
            return [
                {
                    "locale": record["locale"],
                    "variant": record["variant"],
                    "path": pattern.format(locale=record["locale"], variant=record["variant"]),
                    "sha256": record["sha256"],
                }
                for record in layers[layer]["records"]
            ]

        svgs = [
            {
                "locale": path.split("/")[2],
                "variant": path.split("/")[3][:-4],
                "path": path,
                "sha256": digest,
            }
            for path, digest in sorted(artifact_hashes.items())
            if path.startswith("assets/readme-showcase/")
        ]
        compiled_projection = {
            "spec": single("compiled/visual-spec.json"),
            "theme": single("compiled/theme.json"),
            "inventory": {
                "path": "compiled/inventory.json",
                "sha256": hashlib.sha256(compiled_files["compiled/inventory.json"]).hexdigest(),
            },
            "scenes": variants("scenes", "compiled/scenes/{locale}/{variant}.json"),
            "gates": variants("gates", "compiled/gates/{locale}/{variant}.json"),
            "timelines": variants("timelines", "compiled/timeline/{locale}/{variant}.json"),
            "interactions": variants("interactions", "compiled/interaction/{locale}/{variant}.json"),
            "svgs": svgs,
            "identities": layers["identities"]["values"],
        }
        scene_by_key = {
            (item["locale"], item["variant"]): item
            for item in compiled_projection["scenes"]
        }
        gate_by_key = {
            (item["locale"], item["variant"]): item
            for item in compiled_projection["gates"]
        }
        manifest_assets = [
            {
                "asset_id": f"diagram-{svg['locale']}-{svg['variant']}",
                "path": svg["path"],
                "artifact_sha256": svg["sha256"],
                "evidence_ids": list(plan["evidence_ids"]),
                "role": "diagram",
                "locale": svg["locale"],
                "variant": svg["variant"],
                "scene_sha256": scene_by_key[(svg["locale"], svg["variant"])]["sha256"],
                "gate_sha256": gate_by_key[(svg["locale"], svg["variant"])]["sha256"],
                "provenance": {
                    "kind": "generated",
                    "path": f"compiled/scenes/{svg['locale']}/{svg['variant']}.json",
                    "sha256": scene_by_key[(svg["locale"], svg["variant"])]["sha256"],
                },
            }
            for svg in compiled_projection["svgs"]
        ]
        manifest_payload = {
            "schema_version": 3,
            "assets": manifest_assets,
            "compiled": compiled_projection,
        }

        mode = context.manifest["configuration"]["mode"]
        stage_files = {
            "readme-plan.json": plan_raw,
            "retrieval-packet.json": retrieval_raw,
            # Stage 1 remains the authoritative raw v1 packet on disk.  Bundle
            # v3 consumes the canonical v2 projection without promoting that
            # internal adapter output as a stage-6 candidate artifact.
            "repository-evidence.json": canonical_json_bytes(evidence_graph),
            "claim-map.json": claim_raw,
            "visual-spec.json": spec_raw,
            **readmes,
            **compiled_files,
        }
        with tempfile.TemporaryDirectory(prefix=".bundle-v3-") as temporary:
            artifact_root = Path(temporary)
            for relative, raw in stage_files.items():
                destination = artifact_root.joinpath(*PurePosixPath(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            manifest = validate_asset_manifest(
                manifest_payload,
                evidence_graph=evidence_graph,
                artifact_root=artifact_root,
                candidate_assets=[{"path": "visual-spec.json", "sha256": hashlib.sha256(spec_raw).hexdigest()}],
            )
            manifest_raw = canonical_json_bytes(manifest)
            (artifact_root / "asset-manifest.json").write_bytes(manifest_raw)
            stage_files["asset-manifest.json"] = manifest_raw
            artifacts = {
                name: {
                    "path": path,
                    "sha256": hashlib.sha256(stage_files[path]).hexdigest(),
                }
                for name, path in {
                    "plan": "readme-plan.json",
                    "retrieval": "retrieval-packet.json",
                    "evidence": "repository-evidence.json",
                    "claim_map": "claim-map.json",
                    "visual_spec": "visual-spec.json",
                    "asset_manifest": "asset-manifest.json",
                }.items()
            }
            candidate = {
                "readmes": (
                    [
                        {
                            "path": entry["readme_path"],
                            "sha256": hashlib.sha256(readmes[entry["readme_path"]]).hexdigest(),
                        }
                        for entry in plan["locales"]
                    ]
                    if mode == "readme"
                    else []
                ),
                "assets": (
                    [
                        {"path": asset["path"], "sha256": asset["artifact_sha256"]}
                        for asset in manifest["assets"]
                    ]
                    if mode != "audit-only"
                    else []
                ),
            }
            bundle = assemble_generated_bundle_v3(
                artifact_root,
                mode=mode,
                target={
                    "repository": context.manifest["target"]["repository"],
                    "base_sha": context.manifest["target"]["base_sha"],
                },
                candidate=candidate,
                artifacts=artifacts,
                compiled={
                    "inventory": manifest["compiled"]["inventory"],
                    "fingerprint": compiled.inventory_sha256,
                    "retention": "manual",
                },
            )
        return StageResult(
            "pass",
            {
                **compiled_files,
                "asset-manifest.json": manifest_raw,
                "generated-readme-bundle.json": canonical_json_bytes(bundle),
            },
        )


def _materialize(context: RunContext, root: Path) -> dict[str, Any]:
    bundle_path = context.attempt_file(5, "generated-readme-bundle.json")
    _, bundle = _canonical_object(bundle_path)
    _, plan = _canonical_object(context.attempt_file(2, "readme-plan.json"))
    if type(bundle.get("schema_version")) is int and bundle["schema_version"] == 3:
        validate_readme_plan(plan, mode=context.manifest["configuration"]["mode"])
        if plan["schema_version"] != 3 or plan["diagram_route"] != "compiled":
            raise ContractError("E_BUNDLE_PLAN", "compiled bundle requires README Plan v3")

        artifacts = bundle.get("artifacts")
        required_artifacts = {"plan", "retrieval", "evidence", "claim_map", "visual_spec", "asset_manifest"}
        if not isinstance(artifacts, Mapping) or set(artifacts) != required_artifacts:
            raise ContractError("E_SCHEMA_FIELDS", "generated bundle v3 artifacts are not closed")

        def reference(name: str, expected_path: str) -> str:
            value = artifacts.get(name)
            if not isinstance(value, Mapping) or set(value) != {"path", "sha256"}:
                raise ContractError("E_SCHEMA_FIELDS", f"generated bundle.artifacts.{name} is invalid")
            if value.get("path") != expected_path:
                raise ContractError("E_VISUAL_PATH", f"generated bundle.artifacts.{name}.path is invalid")
            digest = value.get("sha256")
            if (
                not isinstance(digest, str)
                or len(digest) != 64
                or any(character not in "0123456789abcdef" for character in digest)
            ):
                raise ContractError("E_BUNDLE_HASH", f"generated bundle.artifacts.{name}.sha256 is invalid")
            return digest

        projection: dict[str, bytes] = {}

        def project_bytes(relative: str, raw: bytes, digest: str) -> None:
            try:
                normalized = normalize_posix_path(relative)
            except ContractError:
                raise ContractError("E_VISUAL_PATH", "materialized path must be a safe relative POSIX path") from None
            if normalized != relative:
                raise ContractError("E_VISUAL_PATH", "materialized path must be a normalized relative POSIX path")
            if normalized in projection:
                raise ContractError("E_RUN_PATH", f"materialized path is duplicated: {normalized}")
            if hashlib.sha256(raw).hexdigest() != digest:
                raise ContractError("E_BUNDLE_HASH", f"materialized bytes differ from reference: {normalized}")
            projection[normalized] = raw

        def project_source(relative: str, source: Path, digest: str) -> None:
            project_bytes(relative, read_regular_bytes(source, maximum=MAX_CANDIDATE_BYTES), digest)

        project_source("readme-plan.json", context.attempt_file(2, "readme-plan.json"), reference("plan", "readme-plan.json"))
        project_source("retrieval-packet.json", context.attempt_file(1, "retrieval-packet.json"), reference("retrieval", "retrieval-packet.json"))

        evidence_raw, evidence = _canonical_object(context.attempt_file(0, "repository-evidence.json"))
        evidence_graph = _v3_evidence_graph(evidence)
        canonical_evidence = evidence_raw if evidence.get("schema_version") == 2 else canonical_json_bytes(evidence_graph)
        project_bytes("repository-evidence.json", canonical_evidence, reference("evidence", "repository-evidence.json"))

        candidate_root = context.workspace.root / "stages/05-candidate"
        project_source("claim-map.json", candidate_root / "claim-map.json", reference("claim_map", "claim-map.json"))
        spec_digest = reference("visual_spec", "visual-spec.json")
        spec_raw = read_regular_bytes(candidate_root / "visual-spec.json", maximum=MAX_CANDIDATE_BYTES)
        project_bytes("visual-spec.json", spec_raw, spec_digest)

        stage6_root = bundle_path.parent
        compiled = load_compiled_visual(stage6_root, bundle)
        compiled_artifacts = dict(compiled.artifacts)
        compiled_spec = compiled_artifacts.get("compiled/visual-spec.json")
        if compiled_spec is None or compiled_spec != spec_raw:
            raise ContractError("E_VISUAL_FINGERPRINT", "stage-6 compiled Visual Spec differs from stage-5 source")
        for relative, raw in sorted(compiled_artifacts.items(), key=lambda item: item[0].encode("utf-8")):
            project_bytes(relative, raw, hashlib.sha256(raw).hexdigest())

        project_source(
            "asset-manifest.json",
            stage6_root / "asset-manifest.json",
            reference("asset_manifest", "asset-manifest.json"),
        )

        candidate = bundle.get("candidate")
        if not isinstance(candidate, Mapping) or set(candidate) != {"readmes", "assets", "candidate_sha256"}:
            raise ContractError("E_SCHEMA_FIELDS", "generated bundle v3 candidate is not closed")
        readme_refs = candidate.get("readmes")
        asset_refs = candidate.get("assets")
        if not isinstance(readme_refs, list) or not isinstance(asset_refs, list):
            raise ContractError("E_SCHEMA_TYPE", "generated bundle v3 candidate references must be arrays")
        expected_readmes = [entry["readme_path"] for entry in plan["locales"]]
        if bundle.get("mode") == "readme" and [item.get("path") for item in readme_refs if isinstance(item, Mapping)] != expected_readmes:
            raise ContractError("E_CLAIM_LANGUAGE", "generated bundle README references differ from Plan v3")
        if bundle.get("mode") != "readme" and readme_refs:
            raise ContractError("E_BUNDLE_MODE", "non-readme bundle cannot contain README references")

        candidate_body: dict[str, list[dict[str, str]]] = {"readmes": [], "assets": []}
        for index, item in enumerate(readme_refs):
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
                raise ContractError("E_SCHEMA_FIELDS", f"generated bundle.candidate.readmes[{index}] is invalid")
            path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise ContractError("E_SCHEMA_TYPE", f"generated bundle.candidate.readmes[{index}] is invalid")
            try:
                normalized = normalize_posix_path(path)
            except ContractError:
                raise ContractError("E_VISUAL_PATH", "generated bundle README path is unsafe") from None
            if normalized != path:
                raise ContractError("E_VISUAL_PATH", "generated bundle README path is not normalized")
            if path not in expected_readmes or path in {entry["path"] for entry in candidate_body["readmes"]}:
                raise ContractError("E_CLAIM_LANGUAGE", "generated bundle README references are not closed")
            if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
                raise ContractError("E_BUNDLE_HASH", "generated bundle README hash is invalid")
            project_source(path, candidate_root / path, digest)
            candidate_body["readmes"].append({"path": path, "sha256": digest})

        for index, item in enumerate(asset_refs):
            if not isinstance(item, Mapping) or set(item) != {"path", "sha256"}:
                raise ContractError("E_SCHEMA_FIELDS", f"generated bundle.candidate.assets[{index}] is invalid")
            path = item.get("path")
            digest = item.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise ContractError("E_SCHEMA_TYPE", f"generated bundle.candidate.assets[{index}] is invalid")
            try:
                normalized = normalize_posix_path(path)
            except ContractError:
                raise ContractError("E_VISUAL_PATH", "generated bundle SVG path is unsafe") from None
            if normalized != path or not path.startswith("assets/readme-showcase/"):
                raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate asset is not a stage-6 SVG")
            if path in {entry["path"] for entry in candidate_body["assets"]}:
                raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate assets contain duplicates")
            raw = compiled_artifacts.get(path)
            if raw is None:
                raise ContractError("E_BUNDLE_ASSET", "generated bundle candidate SVG is absent from stage-6 inventory")
            project_digest = hashlib.sha256(raw).hexdigest()
            if digest != project_digest:
                raise ContractError("E_BUNDLE_HASH", "generated bundle candidate SVG hash differs from stage-6 bytes")
            candidate_body["assets"].append({"path": path, "sha256": digest})

        if candidate.get("candidate_sha256") != canonical_sha256(candidate_body):
            raise ContractError("E_BUNDLE_HASH", "generated bundle candidate hash differs from references")

        try:
            root_info = root.lstat()
        except FileNotFoundError:
            root_info = None
        if root_info is not None and (stat.S_ISLNK(root_info.st_mode) or not stat.S_ISDIR(root_info.st_mode)):
            raise ContractError("E_OUTPUT_PATH", "materialization root must be a real directory")
        for relative in projection:
            parent = root
            for part in PurePosixPath(relative).parts[:-1]:
                parent /= part
                try:
                    info = parent.lstat()
                except FileNotFoundError:
                    break
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                    raise ContractError("E_OUTPUT_PATH", "materialization destination ancestry must be real directories")
        for relative in sorted(projection, key=lambda item: item.encode("utf-8")):
            destination = root.joinpath(*PurePosixPath(relative).parts)
            write_bytes_atomic(destination, projection[relative])
        return bundle

    sources = {
        "repository-evidence.json": context.attempt_file(0, "repository-evidence.json"),
        "retrieval-packet.json": context.attempt_file(1, "retrieval-packet.json"),
        "readme-plan.json": context.attempt_file(2, "readme-plan.json"),
        "claim-map.json": context.workspace.root / "stages/05-candidate/claim-map.json",
        "asset-manifest.json": context.workspace.root / "stages/05-candidate/asset-manifest.json",
    }
    readme_paths = (
        [entry["readme_path"] for entry in plan["locales"]]
        if plan["schema_version"] == 2
        else ["README.md", "README_zh.md"]
    )
    for name in readme_paths:
        candidate = context.workspace.root.joinpath("stages/05-candidate", *PurePosixPath(name).parts)
        if candidate.exists():
            sources[name] = candidate
    for item in bundle["candidate"]["assets"]:
        sources[item["path"]] = context.workspace.root / "stages/05-candidate" / item["path"]
    for relative, source in sources.items():
        destination = root.joinpath(*PurePosixPath(relative).parts)
        write_bytes_atomic(destination, read_regular_bytes(source, maximum=MAX_CANDIDATE_BYTES))
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
