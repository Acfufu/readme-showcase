from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from skill.scripts.pipeline_contracts import (
    MAX_JSON_BYTES,
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object,
    validate_contract,
    write_bytes_atomic,
)
from skill.scripts.readme_showcase.contracts.plan import (
    canonical_readme_plan_bytes,
    read_readme_plan,
    validate_readme_plan,
)
from skill.scripts.readme_showcase.evidence.adapters import adapt_v1_repository_evidence
from skill.scripts.readme_showcase.orchestration.stages import (
    MAX_CANDIDATE_BYTES,
    BundleAssembleStage,
    CandidateImportStage,
    GenerationRequestStage,
    STAGES,
    candidate_files,
)
from skill.scripts.readme_showcase.contracts.run import STAGE_NAMES
from skill.scripts.readme_showcase.generation.assembler import (
    canonical_markdown_blocks,
    validate_generated_bundle_v3,
)
from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace
from skill.scripts.readme_showcase.orchestration import workspace as workspace_module
from tests.unit.visual_kernel.test_scene import EVIDENCE, _spec


REPO_ROOT = Path(__file__).resolve().parents[1]


class ExistingAuditCompatibilityTests(unittest.TestCase):
    def test_audit_without_readme_preserves_usage_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, "skill/scripts/audit_readme.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "usage: audit_readme.py /path/to/README.md\n",
        )
        self.assertEqual(result.stdout, "")


class PipelineContractTests(unittest.TestCase):
    @staticmethod
    def _v3_plan(route: str = "compiled") -> dict[str, object]:
        return {
            "schema_version": 3,
            "mode": "readme",
            "locales": [{"tag": "en", "readme_path": "README.md"}],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": route,
            "commands": [],
            "evidence_ids": ["file:" + "a" * 64],
        }

    @staticmethod
    def _v2_plan(route: str = "static") -> dict[str, object]:
        return {
            "schema_version": 2,
            "mode": "readme",
            "locales": [{"tag": "en", "readme_path": "README.md"}],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": route,
            "commands": [],
            "evidence_ids": ["file:" + "a" * 64],
        }

    def test_readme_plan_v3_compiled_route_and_version_aware_reader(self) -> None:
        for route in ("none", "static", "elk", "compiled"):
            with self.subTest(route=route):
                payload = self._v3_plan(route)
                self.assertEqual(read_readme_plan(payload, mode="readme"), payload)
                self.assertEqual(read_readme_plan(payload, version=3), payload)
                self.assertEqual(canonical_readme_plan_bytes(payload), canonical_json_bytes(payload))

        with self.assertRaises(ContractError) as raised:
            read_readme_plan(self._v3_plan(), version=2)
        self.assertEqual(raised.exception.code, "E_SCHEMA_VERSION")

    def test_compiled_route_is_rejected_before_plan_v3(self) -> None:
        payload_v1 = {
            "schema_version": 1,
            "mode": "readme",
            "languages": ["en"],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": "compiled",
            "commands": [],
            "evidence_ids": ["file:README.md"],
        }
        for version, payload in ((1, payload_v1), (2, self._v2_plan("compiled"))):
            with self.subTest(version=version):
                with self.assertRaises(ContractError) as raised:
                    validate_readme_plan(payload)
                self.assertEqual(raised.exception.code, "E_BUNDLE_PLAN")

    def test_plan_v3_rejects_malformed_trust_inputs(self) -> None:
        cases = (
            ("unknown-route", {"diagram_route": "browser"}, "E_BUNDLE_PLAN"),
            ("unknown-field", {"unknown": True}, "E_SCHEMA_UNKNOWN_FIELD"),
            ("float", {"schema_version": 3.0}, "E_SCHEMA_FLOAT"),
            (
                "unsafe-locale-path",
                {"locales": [{"tag": "en", "readme_path": "../README.md"}]},
                "E_README_PATH",
            ),
            (
                "stale-evidence-id",
                {"evidence_ids": ["file:README.md"]},
                "E_CLAIM_EVIDENCE",
            ),
        )
        for name, update, code in cases:
            with self.subTest(case=name):
                payload = self._v3_plan()
                payload.update(update)
                with self.assertRaises(ContractError) as raised:
                    validate_readme_plan(payload)
                self.assertEqual(raised.exception.code, code)

    def test_legacy_plan_bytes_and_hashes_remain_stable(self) -> None:
        expected_fixture_hashes = {
            "readme-plan-v1.valid.json": "ecfd65f67dabb6ea688ddd99db9b472863ffb3a338c39e786a94cbf85648a3e6",
            "readme-plan-v2.valid.json": "0505e851996c2343590afdee622ab5468e382a1ff5f7aba401d12d8dc0a0993c",
        }
        for name, expected in expected_fixture_hashes.items():
            with self.subTest(fixture=name):
                actual = hashlib.sha256((REPO_ROOT / "tests/fixtures/contracts" / name).read_bytes()).hexdigest()
                self.assertEqual(actual, expected)

        v1 = json.loads((REPO_ROOT / "tests/fixtures/contracts/readme-plan-v1.valid.json").read_text(encoding="utf-8"))
        self.assertEqual(
            hashlib.sha256(canonical_readme_plan_bytes(v1)).hexdigest(),
            "ecfd65f67dabb6ea688ddd99db9b472863ffb3a338c39e786a94cbf85648a3e6",
        )
        v2 = json.loads((REPO_ROOT / "tests/fixtures/contracts/readme-plan-v2.valid.json").read_text(encoding="utf-8"))
        v2["evidence_ids"] = ["file:" + "a" * 64]
        self.assertEqual(
            hashlib.sha256(canonical_readme_plan_bytes(v2)).hexdigest(),
            "86a4973990ea0e5c6b198c235e1fdf7a496358a8d681b2a231cb45710bc2d694",
        )

    def test_canonical_json_is_stable_utf8_and_lf_terminated(self) -> None:
        first = {"z": "证据", "schema_version": 1, "a": [3, 2, 1]}
        second = {"a": [3, 2, 1], "schema_version": 1, "z": "证据"}

        expected = (
            '{"a":[3,2,1],"schema_version":1,"z":"证据"}\n'.encode()
        )
        self.assertEqual(canonical_json_bytes(first), expected)
        self.assertEqual(canonical_json_bytes(second), expected)
        self.assertEqual(
            canonical_sha256(first),
            hashlib.sha256(expected).hexdigest(),
        )

    def test_schema_version_and_unknown_fields_fail_with_stable_codes(self) -> None:
        with self.assertRaises(ContractError) as version_error:
            validate_contract(
                {"schema_version": 2},
                required={"schema_version"},
                optional=set(),
                context="fixture",
            )
        self.assertEqual(version_error.exception.code, "E_SCHEMA_VERSION")

        with self.assertRaises(ContractError) as field_error:
            validate_contract(
                {"schema_version": 1, "extra": True},
                required={"schema_version"},
                optional=set(),
                context="fixture",
            )
        self.assertEqual(
            field_error.exception.code,
            "E_SCHEMA_UNKNOWN_FIELD",
        )

    def test_atomic_write_failure_preserves_previous_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "result.json"
            destination.write_bytes(b"previous\n")

            with mock.patch.object(
                os,
                "replace",
                side_effect=OSError("forced replacement failure"),
            ):
                with self.assertRaises(OSError):
                    write_bytes_atomic(destination, b"candidate\n")

            self.assertEqual(destination.read_bytes(), b"previous\n")
            self.assertEqual(
                list(destination.parent.glob(f".{destination.name}.*.tmp")),
                [],
            )

    def test_json_reader_rejects_symlinks_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.json"
            target.write_text('{"schema_version":1}\n', encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)

            with self.assertRaises(ContractError) as linked:
                read_json_object(link)
            self.assertEqual(linked.exception.code, "E_INPUT_PATH")

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (MAX_JSON_BYTES + 1))
            with self.assertRaises(ContractError) as bounded:
                read_json_object(oversized)
            self.assertEqual(bounded.exception.code, "E_INPUT_SIZE")

    def test_atomic_write_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir()
            linked_parent = root / "run"
            linked_parent.symlink_to(outside, target_is_directory=True)
            destination = linked_parent / "result.json"

            with self.assertRaises(ContractError) as raised:
                write_bytes_atomic(destination, b"candidate\n")

            self.assertEqual(raised.exception.code, "E_OUTPUT_PATH")
            self.assertFalse((outside / "result.json").exists())


class CandidateFilesVersionTests(unittest.TestCase):
    @staticmethod
    def _compiled_plan(route: str = "compiled", locales: list[dict[str, str]] | None = None) -> dict[str, object]:
        return {
            "schema_version": 3,
            "mode": "readme",
            "locales": locales or [{"tag": "en", "readme_path": "README.md"}],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": route,
            "commands": [],
            "evidence_ids": ["file:" + "a" * 64],
        }

    @staticmethod
    def _candidate_spec(evidence: dict[str, object] | None = None) -> dict[str, object]:
        spec = json.loads((REPO_ROOT / "tests/fixtures/contracts/visual-spec-v1.valid.json").read_text(encoding="utf-8"))
        graph = evidence or json.loads((REPO_ROOT / "tests/fixtures/contracts/repository-evidence-v2.valid.json").read_text(encoding="utf-8"))
        fact_id = graph["facts"][0]["fact_id"]

        def replace(value: object) -> object:
            if isinstance(value, dict):
                return {key: replace(item) for key, item in value.items()}
            if isinstance(value, list):
                return [replace(item) for item in value]
            return fact_id if value == "FACT_ID" else value

        return replace(spec)  # type: ignore[return-value]

    @staticmethod
    def _evidence_graph() -> dict[str, object]:
        return json.loads((REPO_ROOT / "tests/fixtures/contracts/repository-evidence-v2.valid.json").read_text(encoding="utf-8"))

    @staticmethod
    def _v1_scan_evidence() -> dict[str, object]:
        content = "# Demo\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        return {
            "schema_version": 1,
            "status": "complete",
            "target": {"name": "demo", "base_sha": "a" * 40},
            "scan_limits": {
                "max_depth": 12,
                "max_directories": 500,
                "max_file_bytes": 512 * 1024,
                "max_files": 2000,
                "max_seconds": 5,
                "max_total_bytes": 4 * 1024 * 1024,
            },
            "files": [{"path": "README.md", "bytes": len(content.encode()), "lines": 1, "sha256": digest, "content": content}],
            "facts": [{"fact_id": "file:README.md", "kind": "repository-file", "path": "README.md", "evidence_sha256": digest}],
            "warnings": [],
        }

    @staticmethod
    def _context(
        root: Path,
        plan: dict[str, object],
        *,
        mode: str = "readme",
        evidence: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        plan_path = root / "stages/03-plan-import/attempts/1/readme-plan.json"
        plan_path.parent.mkdir(parents=True)
        candidate_root = root / "stages/05-candidate"
        candidate_root.mkdir(parents=True)
        plan_path.write_bytes(canonical_json_bytes(plan))
        evidence_path = root / "stages/01-scan/attempts/1/repository-evidence.json"
        evidence_path.parent.mkdir(parents=True)
        evidence_path.write_bytes(canonical_json_bytes(evidence or CandidateFilesVersionTests._evidence_graph()))

        class Workspace:
            def __init__(self, workspace_root: Path) -> None:
                self.root = workspace_root

        class Context:
            def __init__(self, workspace_root: Path, workspace_mode: str) -> None:
                self.workspace = Workspace(workspace_root)
                self.manifest = {
                    "configuration": {
                        "mode": workspace_mode,
                        "locales": ["en"],
                        "project_type": "developer-tool",
                    },
                    "target": {"repository": "owner/demo", "base_sha": "a" * 40},
                }

            def attempt_file(self, stage_index: int, name: str) -> Path:
                if stage_index == 0:
                    return self.workspace.root / "stages/01-scan/attempts/1" / name
                if stage_index == 1:
                    return self.workspace.root / "stages/02-retrieve/attempts/1" / name
                return self.workspace.root / "stages/03-plan-import/attempts/1" / name

        return Context(root, mode)  # type: ignore[return-value]

    def _write_compiled_inputs(
        self,
        root: Path,
        *,
        locales: list[dict[str, str]] | None = None,
        evidence: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        plan = self._compiled_plan(locales=locales)
        context = self._context(root, plan, evidence=evidence)
        candidate_root = root / "stages/05-candidate"
        candidate_root.joinpath("claim-map.json").write_bytes(canonical_json_bytes({"schema_version": 3}))
        evidence_graph = adapt_v1_repository_evidence(evidence) if evidence is not None and evidence.get("schema_version") == 1 else None
        candidate_root.joinpath("visual-spec.json").write_bytes(canonical_json_bytes(self._candidate_spec(evidence_graph)))
        for entry in plan["locales"]:  # type: ignore[index]
            path = candidate_root / entry["readme_path"]  # type: ignore[index]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"# {entry['tag']}\n".encode())  # type: ignore[index]
        return context

    def test_compiled_plan_requires_raw_spec_claim_map_and_all_readmes_without_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._write_compiled_inputs(
                root,
                locales=[
                    {"tag": "en", "readme_path": "README.md"},
                    {"tag": "zh-Hans", "readme_path": "docs/README.zh-Hans.md"},
                ],
            )
            (root / "stages/05-candidate/assets").mkdir()
            (root / "stages/05-candidate/assets/diagram.svg").write_bytes(b"<svg/>")

            files = candidate_files(context)
            self.assertIsNotNone(files)
            self.assertEqual(
                [name for name, _ in files or []],
                ["claim-map.json", "visual-spec.json", "README.md", "docs/README.zh-Hans.md", "assets/diagram.svg"],
            )
            self.assertNotIn("asset-manifest.json", [name for name, _ in files or []])

    def test_compiled_visual_spec_adapts_real_v1_scan_evidence_without_rewriting_stage1(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._v1_scan_evidence()
            context = self._write_compiled_inputs(root, evidence=evidence)
            evidence_path = root / "stages/01-scan/attempts/1/repository-evidence.json"
            raw_before = evidence_path.read_bytes()

            files = candidate_files(context)
            self.assertIsNotNone(files)
            self.assertEqual(evidence_path.read_bytes(), raw_before)

            evidence_path.write_bytes(canonical_json_bytes({"schema_version": 3, "facts": []}))
            with self.assertRaises(ContractError) as raised:
                candidate_files(context)
            self.assertEqual(raised.exception.code, "E_SCHEMA_VERSION")

    def test_compiled_plan_requires_localized_readmes_outside_readme_mode(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._write_compiled_inputs(
                root,
                locales=[
                    {"tag": "en", "readme_path": "README.md"},
                    {"tag": "zh-Hans", "readme_path": "docs/README.zh-Hans.md"},
                ],
            )
            context.manifest["configuration"]["mode"] = "audit"
            (root / "stages/05-candidate/docs/README.zh-Hans.md").unlink()
            self.assertIsNone(candidate_files(context))

    def test_compiled_candidate_manifest_is_forbidden_and_fingerprint_binds_raw_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._write_compiled_inputs(root)
            manifest = root / "stages/05-candidate/asset-manifest.json"
            manifest.write_bytes(canonical_json_bytes({"schema_version": 2}))
            with self.assertRaises(ContractError) as raised:
                candidate_files(context)
            self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

            manifest.unlink()
            fingerprint = CandidateImportStage().fingerprint(context)
            (root / "stages/05-candidate/README.md").write_bytes(b"# changed\n")
            self.assertNotEqual(fingerprint, CandidateImportStage().fingerprint(context))

    def test_generation_request_adapts_v1_for_compiled_plan_and_rebinds_temporary_retrieval(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = self._v1_scan_evidence()
            evidence_graph = adapt_v1_repository_evidence(evidence)
            fact_id = evidence_graph["facts"][0]["fact_id"]
            plan = self._compiled_plan()
            plan["evidence_ids"] = [fact_id]
            context = self._context(root, plan, evidence=evidence)
            context.manifest["configuration"]["locales"] = ["en"]
            retrieval = {
                "schema_version": 1,
                "status": "available",
                "dataset": {"dataset_id": "demo", "dataset_revision": 1, "manifest_sha256": "b" * 64},
                "query": {
                    "evidence_sha256": hashlib.sha256(canonical_json_bytes(evidence)).hexdigest(),
                    "project_type": "developer-tool",
                    "sections": [],
                    "tags": [],
                },
                "records": [
                    {
                        "record_id": "pattern",
                        "score": 1,
                        "pattern": {"summary": "summary", "structure": "structure", "proof": "proof"},
                    }
                ],
                "reason": None,
            }
            retrieval_path = root / "stages/02-retrieve/attempts/1/retrieval-packet.json"
            retrieval_path.parent.mkdir(parents=True)
            retrieval_path.write_bytes(canonical_json_bytes(retrieval))
            evidence_path = root / "stages/01-scan/attempts/1/repository-evidence.json"
            evidence_before = evidence_path.read_bytes()
            retrieval_before = retrieval_path.read_bytes()

            result = GenerationRequestStage().execute(context)
            self.assertEqual(result.status, "pass")
            request = json.loads(result.files["generation-request.json"])
            self.assertEqual(request["evidence_index"][0]["fact_id"], fact_id)
            self.assertEqual(evidence_path.read_bytes(), evidence_before)
            self.assertEqual(retrieval_path.read_bytes(), retrieval_before)

    def test_compiled_spec_waits_or_fails_closed_before_candidate_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._write_compiled_inputs(root)
            spec_path = root / "stages/05-candidate/visual-spec.json"

            spec_path.unlink()
            self.assertIsNone(candidate_files(context))
            spec_path.write_bytes(canonical_json_bytes({"schema_version": 1}))
            with self.assertRaises(ContractError) as malformed:
                candidate_files(context)
            self.assertEqual(malformed.exception.code, "E_SCHEMA_MISSING_FIELD")

            spec_path.write_bytes(b" " * (MAX_CANDIDATE_BYTES + 1))
            with self.assertRaises(ContractError) as oversized:
                candidate_files(context)
            self.assertEqual(oversized.exception.code, "E_INPUT_SIZE")

            spec_path.write_bytes(canonical_json_bytes(self._candidate_spec()))
            claim_path = root / "stages/05-candidate/claim-map.json"
            claim_path.write_bytes(canonical_json_bytes({"schema_version": 2}))
            with self.assertRaises(ContractError) as stale_claim:
                candidate_files(context)
            self.assertEqual(stale_claim.exception.code, "E_SCHEMA_VERSION")

    def test_compiled_symlinked_asset_and_legacy_routes_preserve_boundaries(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._write_compiled_inputs(root)
            assets = root / "stages/05-candidate/assets"
            assets.mkdir()
            outside = root / "outside.svg"
            outside.write_bytes(b"<svg/>")
            (assets / "escape.svg").symlink_to(outside)
            with self.assertRaises(ContractError) as raised:
                candidate_files(context)
            self.assertEqual(raised.exception.code, "E_RUN_PATH")

            legacy_root = root / "legacy"
            legacy_context = self._context(legacy_root, {
                "schema_version": 2,
                "mode": "readme",
                "locales": [{"tag": "en", "readme_path": "README.md"}],
                "sections": ["overview"],
                "visual_intent": "project-structure",
                "diagram_route": "static",
                "commands": [],
                "evidence_ids": ["file:" + "a" * 64],
            })
            legacy_candidate = legacy_root / "stages/05-candidate"
            legacy_candidate.joinpath("claim-map.json").write_bytes(b"claim")
            legacy_candidate.joinpath("asset-manifest.json").write_bytes(b"manifest")
            legacy_candidate.joinpath("README.md").write_bytes(b"readme")
            self.assertEqual(
                [name for name, _ in candidate_files(legacy_context) or []],
                ["claim-map.json", "asset-manifest.json", "README.md"],
            )


class BundleAssembleStageTests(unittest.TestCase):
    @staticmethod
    def _context(
        root: Path,
        plan: dict[str, object],
        *,
        candidate: dict[str, bytes],
        mode: str,
        evidence: dict[str, object] | None = None,
    ) -> SimpleNamespace:
        stage_roots: dict[int, Path] = {}
        for index, name in (
            (0, "repository-evidence.json"),
            (1, "retrieval-packet.json"),
            (2, "readme-plan.json"),
        ):
            stage_root = root / f"stages/{index + 1:02d}-stage/attempts/1"
            stage_root.mkdir(parents=True, exist_ok=True)
            stage_roots[index] = stage_root
        (stage_roots[0] / "repository-evidence.json").write_bytes(
            canonical_json_bytes(evidence or EVIDENCE)
        )
        (stage_roots[1] / "retrieval-packet.json").write_bytes(
            canonical_json_bytes({"schema_version": 1, "status": "unavailable", "records": []})
        )
        (stage_roots[2] / "readme-plan.json").write_bytes(canonical_json_bytes(plan))
        candidate_root = root / "stages/05-candidate"
        for relative, raw in candidate.items():
            destination = candidate_root.joinpath(*Path(relative).parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

        class Workspace:
            def __init__(self, workspace_root: Path) -> None:
                self.root = workspace_root

        class Context:
            def __init__(self, workspace_root: Path, workspace_mode: str) -> None:
                self.workspace = Workspace(workspace_root)
                self.manifest = {
                    "configuration": {
                        "mode": workspace_mode,
                        "locales": ["en"],
                        "project_type": "developer-tool",
                    },
                    "target": {"repository": "owner/demo", "base_sha": "a" * 40},
                }

            def attempt_file(self, stage_index: int, name: str) -> Path:
                return stage_roots[stage_index] / name

        return Context(root, mode)  # type: ignore[return-value]

    @staticmethod
    def _compiled_inputs() -> tuple[dict[str, object], dict[str, bytes]]:
        spec = _spec("flow")
        plan = {
            "schema_version": 3,
            "mode": "readme",
            "locales": [{"tag": "en", "readme_path": "README.md"}],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": "compiled",
            "commands": [],
            "evidence_ids": [fact["fact_id"] for fact in EVIDENCE["facts"]],
        }
        readme = b"# README\n"
        markdown_claim = {
            "claim_id": "markdown:en:overview",
            "content_sha256": hashlib.sha256(canonical_markdown_blocks(readme)[0]).hexdigest(),
            "claim_kind": "factual",
            "evidence_ids": [EVIDENCE["facts"][0]["fact_id"]],
            "language_pair_id": None,
            "support_level": "direct",
        }
        diagram_claims = []
        for collection in (spec["nodes"], spec["edges"], spec["groups"], spec["lanes"]):
            for element in collection:
                if element.get("label") is None:
                    continue
                diagram_claims.append(
                    {
                        "claim_id": f"diagram:en:{element['id']}",
                        "content_sha256": hashlib.sha256(element["label"].encode("utf-8")).hexdigest(),
                        "claim_kind": "factual",
                        "evidence_ids": element["evidence_ids"],
                        "language_pair_id": None,
                        "support_level": "direct",
                        "element_id": element["id"],
                    }
                )
        claim_map = {
            "schema_version": 3,
            "markdown_blocks": [markdown_claim],
            "diagram_labels": sorted(diagram_claims, key=lambda item: item["claim_id"]),
        }
        return plan, {
            "claim-map.json": canonical_json_bytes(claim_map),
            "visual-spec.json": canonical_json_bytes(spec),
            "README.md": readme,
        }

    @staticmethod
    def _compiled_inputs_with_v1_evidence() -> tuple[dict[str, object], dict[str, bytes], dict[str, object], dict[str, object]]:
        plan, candidate = BundleAssembleStageTests._compiled_inputs()
        v1_evidence = CandidateFilesVersionTests._v1_scan_evidence()
        evidence_v2 = adapt_v1_repository_evidence(v1_evidence)
        fact_id = evidence_v2["facts"][0]["fact_id"]

        def replace(value: object) -> object:
            if isinstance(value, dict):
                return {
                    key: [fact_id] if key == "evidence_ids" else replace(item)
                    for key, item in value.items()
                }
            if isinstance(value, list):
                return [replace(item) for item in value]
            return value

        spec = replace(json.loads(candidate["visual-spec.json"]))
        claim_map = replace(json.loads(candidate["claim-map.json"]))
        plan["evidence_ids"] = [fact_id]
        candidate["visual-spec.json"] = canonical_json_bytes(spec)
        candidate["claim-map.json"] = canonical_json_bytes(claim_map)
        return plan, candidate, v1_evidence, evidence_v2

    def test_legacy_bundle_bytes_and_stage_registry_are_unchanged(self) -> None:
        plan = {
            "schema_version": 2,
            "mode": "readme",
            "locales": [{"tag": "en", "readme_path": "README.md"}],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": "static",
            "commands": [],
            "evidence_ids": ["file:" + "a" * 64],
        }
        asset = b"<svg>legacy</svg>\n"
        candidate = {
            "claim-map.json": b"claim-map\n",
            "asset-manifest.json": canonical_json_bytes(
                {
                    "schema_version": 2,
                    "assets": [
                        {
                            "path": "assets/diagram.svg",
                            "sha256": hashlib.sha256(asset).hexdigest(),
                        }
                    ],
                }
            ),
            "README.md": b"# Legacy\n",
            "assets/diagram.svg": asset,
        }
        with tempfile.TemporaryDirectory() as temporary:
            context = self._context(Path(temporary), plan, candidate=candidate, mode="readme")
            result = BundleAssembleStage().execute(context)
        expected = {
            "schema_version": 1,
            "mode": "readme",
            "target": {"repository": "owner/demo", "base_sha": "a" * 40},
            "candidate": {
                "readme": {
                    "path": "README.md",
                    "sha256": hashlib.sha256(b"# Legacy\n").hexdigest(),
                },
                "assets": [
                    {
                        "path": "assets/diagram.svg",
                        "sha256": hashlib.sha256(asset).hexdigest(),
                    }
                ],
            },
            "artifacts": {
                "asset_manifest": {
                    "path": "asset-manifest.json",
                    "sha256": hashlib.sha256(candidate["asset-manifest.json"]).hexdigest(),
                },
                "claim_map": {
                    "path": "claim-map.json",
                    "sha256": hashlib.sha256(candidate["claim-map.json"]).hexdigest(),
                },
                "plan": {
                    "path": "readme-plan.json",
                    "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest(),
                },
                "retrieval": {
                    "path": "retrieval-packet.json",
                    "sha256": hashlib.sha256(
                        canonical_json_bytes({"schema_version": 1, "status": "unavailable", "records": []})
                    ).hexdigest(),
                },
            },
        }
        self.assertEqual(result.files, {"generated-readme-bundle.json": canonical_json_bytes(expected)})
        self.assertEqual(tuple(stage.name for stage in STAGES), STAGE_NAMES)

    def test_compiled_stage_emits_real_bundle_v3_and_complete_nested_topology(self) -> None:
        plan, candidate = self._compiled_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._context(root, plan, candidate=candidate, mode="readme")
            before = {name: (root / "stages/05-candidate" / name).read_bytes() for name in candidate}
            result = BundleAssembleStage().execute(context)
            self.assertEqual(result.status, "pass")
            self.assertEqual(
                set(result.files),
                {
                    "generated-readme-bundle.json",
                    "asset-manifest.json",
                    "compiled/visual-spec.json",
                    "compiled/theme.json",
                    "compiled/inventory.json",
                    "compiled/scenes/en/desktop.json",
                    "compiled/scenes/en/mobile.json",
                    "compiled/gates/en/desktop.json",
                    "compiled/gates/en/mobile.json",
                    "compiled/timeline/en/desktop.json",
                    "compiled/timeline/en/mobile.json",
                    "compiled/interaction/en/desktop.json",
                    "compiled/interaction/en/mobile.json",
                    "assets/readme-showcase/en/desktop.svg",
                    "assets/readme-showcase/en/mobile.svg",
                },
            )
            self.assertEqual(
                {name: (root / "stages/05-candidate" / name).read_bytes() for name in candidate},
                before,
            )

            artifact_root = root / "materialized"
            for relative, raw in result.files.items():
                destination = artifact_root.joinpath(*Path(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            for relative, raw in candidate.items():
                destination = artifact_root.joinpath(*Path(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            for index, name in ((0, "repository-evidence.json"), (1, "retrieval-packet.json"), (2, "readme-plan.json")):
                source = context.attempt_file(index, name)
                destination = artifact_root / name
                destination.write_bytes(source.read_bytes())
            bundle = json.loads(result.files["generated-readme-bundle.json"])
            report = validate_generated_bundle_v3(bundle, artifact_root)
            self.assertEqual(report["schema_version"], 3)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(bundle["compiled"]["retention"], "manual")
            self.assertNotIn("evaluation", bundle["artifacts"])

    def test_compiled_stage_adapts_v1_scan_evidence_without_promoting_raw_stage1(self) -> None:
        plan, candidate, v1_evidence, evidence_v2 = self._compiled_inputs_with_v1_evidence()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._context(
                root,
                plan,
                candidate=candidate,
                mode="readme",
                evidence=v1_evidence,
            )
            stage1_path = context.attempt_file(0, "repository-evidence.json")
            stage1_before = stage1_path.read_bytes()
            result = BundleAssembleStage().execute(context)
            self.assertEqual(stage1_path.read_bytes(), stage1_before)
            self.assertNotIn("repository-evidence.json", result.files)

            artifact_root = root / "materialized"
            for relative, raw in result.files.items():
                destination = artifact_root.joinpath(*Path(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            for relative, raw in candidate.items():
                destination = artifact_root.joinpath(*Path(relative).parts)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw)
            for index, name in ((1, "retrieval-packet.json"), (2, "readme-plan.json")):
                source = context.attempt_file(index, name)
                (artifact_root / name).write_bytes(source.read_bytes())
            adapted_raw = canonical_json_bytes(evidence_v2)
            (artifact_root / "repository-evidence.json").write_bytes(adapted_raw)
            bundle = json.loads(result.files["generated-readme-bundle.json"])
            report = validate_generated_bundle_v3(bundle, artifact_root)
            self.assertEqual(report["schema_version"], 3)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(
                bundle["artifacts"]["evidence"]["sha256"],
                hashlib.sha256(adapted_raw).hexdigest(),
            )
            self.assertNotEqual(
                bundle["artifacts"]["evidence"]["sha256"],
                hashlib.sha256(stage1_before).hexdigest(),
            )

    def test_compiled_stage_failure_does_not_mutate_stage5_or_return_partial_attempt(self) -> None:
        plan, candidate = self._compiled_inputs()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            context = self._context(root, plan, candidate=candidate, mode="readme")
            before = {name: (root / "stages/05-candidate" / name).read_bytes() for name in candidate}
            with mock.patch(
                "skill.scripts.readme_showcase.orchestration.stages.compile_visual",
                side_effect=ContractError("E_OUTPUT_GEOMETRY", "forced compiler failure"),
            ):
                with self.assertRaises(ContractError) as raised:
                    BundleAssembleStage().execute(context)
            self.assertEqual(raised.exception.code, "E_OUTPUT_GEOMETRY")
            self.assertEqual(
                {name: (root / "stages/05-candidate" / name).read_bytes() for name in candidate},
                before,
            )

    def test_nested_write_failure_preserves_prior_current_attempt_and_stage5(self) -> None:
        plan, candidate = self._compiled_inputs()
        with tempfile.TemporaryDirectory() as source_temporary, tempfile.TemporaryDirectory() as workspace_temporary:
            source_root = Path(source_temporary)
            context = self._context(source_root, plan, candidate=candidate, mode="readme")
            result = BundleAssembleStage().execute(context)
            stage5_before = {
                name: hashlib.sha256((source_root / "stages/05-candidate" / name).read_bytes()).hexdigest()
                for name in candidate
            }

            workspace = RunWorkspace(Path(workspace_temporary), REPO_ROOT)
            workspace.initialize(
                repository="local/repository",
                base_sha="a" * 40,
                configuration={
                    "mode": "readme",
                    "project_type": "developer-tool",
                    "locales": ["en"],
                    "scanner_profile": "default",
                },
                clock=lambda: "2026-08-05T00:00:00Z",
            )
            prior = workspace.append_attempt(6, "bundle-assemble", result.files)
            stage_root = workspace.root / "stages/06-bundle-assemble"
            prior_hashes = {
                str(path.relative_to(prior)): hashlib.sha256(path.read_bytes()).hexdigest()
                for path in prior.rglob("*")
                if path.is_file()
            }
            current_before = (stage_root / "current.json").read_bytes()
            manifest_before = (workspace.root / "run-manifest.json").read_bytes()
            real_write = workspace_module.os.write
            calls = 0

            def fail_after_one_write(fd: int, value: bytes) -> int:
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise OSError("forced nested write failure")
                return real_write(fd, value)

            with mock.patch.object(workspace_module.os, "write", side_effect=fail_after_one_write):
                with self.assertRaises(ContractError) as raised:
                    workspace.append_attempt(6, "bundle-assemble", result.files)
            self.assertEqual(raised.exception.code, "E_RUN_PATH")
            self.assertEqual(
                {
                    str(path.relative_to(prior)): hashlib.sha256(path.read_bytes()).hexdigest()
                    for path in prior.rglob("*")
                    if path.is_file()
                },
                prior_hashes,
            )
            self.assertEqual((stage_root / "current.json").read_bytes(), current_before)
            self.assertEqual((workspace.root / "run-manifest.json").read_bytes(), manifest_before)
            self.assertFalse((stage_root / "attempts/2").exists())
            self.assertEqual(workspace.read_manifest()["stages"][5]["attempt"], 1)
            self.assertEqual(
                {
                    name: hashlib.sha256((source_root / "stages/05-candidate" / name).read_bytes()).hexdigest()
                    for name in candidate
                },
                stage5_before,
            )


class PipelineCliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "skill/scripts/readme_pipeline.py", *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_lists_all_approved_subcommands(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0)
        for subcommand in (
            "validate-dataset",
            "scan",
            "retrieve",
            "validate-bundle",
            "evaluate",
            "import-benchmark",
            "build-pr-bundle",
            "check-publish-gate",
        ):
            self.assertIn(subcommand, result.stdout)

    def test_invalid_schema_diagnostics_use_stderr_and_exit_two(self) -> None:
        fixtures = (
            ({"schema_version": 2}, "E_SCHEMA_VERSION"),
            (
                {"schema_version": 1, "unexpected": True},
                "E_SCHEMA_UNKNOWN_FIELD",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            for index, (payload, code) in enumerate(fixtures):
                with self.subTest(code=code):
                    bundle = Path(temporary_directory) / f"bundle-{index}.json"
                    bundle.write_text(json.dumps(payload), encoding="utf-8")

                    result = self.run_cli(
                        "validate-bundle",
                        "--bundle",
                        str(bundle),
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(code, result.stderr)


if __name__ == "__main__":
    unittest.main()
