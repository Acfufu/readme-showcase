from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.contracts.plan_lock import (
    APPROVAL_ENVELOPE_PATH,
    PLAN_LOCK_PATH,
    PLAN_LOCK_REPORT_PATH,
    build_plan_lock,
    check_plan_lock,
    validate_plan_lock_report_v1,
    validate_plan_lock_v1,
)
from skill.scripts.readme_showcase.contracts.plan import canonical_readme_plan_bytes
from skill.scripts.readme_showcase.orchestration.stages import EvaluateStage
from skill.scripts.readme_showcase.validation.policy import KNOWN_ERROR_CODES


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
FIXTURES = REPO_ROOT / "tests/fixtures/run-workspaces"


def _v2_bundle_with_plan(root: Path, plan: dict[str, Any]) -> dict[str, Any]:
    """Rebuild the v2 bundle at root after the plan artifact bytes changed."""
    return _v2_bundle(root, plan=plan)


def _fact() -> dict[str, Any]:
    return build_fact(
        kind="file-presence", path="source/README.md", locator=None,
        semantic_key="presence", value=True, source_bytes=b"source evidence\n",
    )


def _v2_plan(fact_id: str) -> dict[str, Any]:
    return {
        "schema_version": 2,
        "mode": "readme",
        "locales": [{"tag": "en", "readme_path": "README.generated.md"}],
        "sections": ["overview"],
        "visual_intent": "hero",
        "diagram_route": "static",
        "commands": ["python -m demo"],
        "evidence_ids": [fact_id],
    }


def _v2_bundle(
    root: Path, *, asset_bytes: bytes = b"asset bytes\n", plan: dict[str, Any] | None = None
) -> dict[str, Any]:
    """Build a v2 bundle at root, returning the bundle payload."""
    from skill.scripts.readme_showcase.generation.assembler import assemble_generated_bundle

    asset_raw = asset_bytes
    readme_raw = b"# Overview\n"
    readme_block = b"# Overview"
    fact = build_fact(
        kind="file-presence", path="README.generated.md", locator=None,
        semantic_key="presence", value=True, source_bytes=readme_raw,
    )
    fact_id = fact["fact_id"]
    plan = plan or _v2_plan(fact_id)
    claims = {
        "schema_version": 2,
        "markdown_blocks": [{
            "claim_id": "markdown:en:overview",
            "content_sha256": hashlib.sha256(readme_block).hexdigest(),
            "claim_kind": "factual", "evidence_ids": [fact_id],
            "language_pair_id": None,
            "support_level": "direct",
        }],
        "diagram_labels": [],
    }
    assets = {
        "schema_version": 2,
        "assets": [{
            "asset_id": "hero", "path": "assets/hero.png", "locale": "en", "language_neutral": False,
            "provenance": {
                "kind": "derived", "path": "README.generated.md",
                "sha256": hashlib.sha256(readme_raw).hexdigest(),
            },
            "artifact_sha256": hashlib.sha256(asset_raw).hexdigest(),
            "candidate_sha256": hashlib.sha256(asset_raw).hexdigest(),
            "evidence_ids": [fact_id],
        }],
    }
    retrieval = {
        "schema_version": 1,
        "status": "available",
        "records": [{"section_intents": ["overview"]}],
    }
    (root / "assets").mkdir(exist_ok=True)
    (root / "assets" / "hero.png").write_bytes(asset_raw)
    (root / "README.generated.md").write_bytes(readme_raw)
    candidate = {
        "readme": {"path": "README.generated.md", "sha256": hashlib.sha256(readme_raw).hexdigest()},
        "assets": [{"path": "assets/hero.png", "sha256": hashlib.sha256(asset_raw).hexdigest()}],
    }
    values = {
        "plan": plan, "retrieval": retrieval, "evidence": _fact_graph(fact),
        "claim_map": claims, "asset_manifest": assets, "evaluation": {
            "schema_version": 2, "status": "pass",
            "candidate_sha256": canonical_sha256(candidate),
        },
    }
    paths = {
        "plan": "readme-plan.json", "retrieval": "retrieval-packet.json",
        "evidence": "repository-evidence.json", "claim_map": "claim-map.json",
        "asset_manifest": "asset-manifest.json", "evaluation": "evaluation.json",
    }
    artifacts: dict[str, Any] = {}
    for name, value in values.items():
        write_canonical_json_atomic(root / paths[name], value)
        artifacts[name] = {"path": paths[name], "sha256": canonical_sha256(value)}
    return assemble_generated_bundle(
        root, mode="readme",
        target={"repository": "owner/repo", "base_sha": "a" * 40},
        candidate=candidate, artifacts=artifacts,
    )


def _fact_graph(fact: dict[str, Any]) -> dict[str, Any]:
    from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph

    return EvidenceGraph([fact]).to_dict()


def _v1_bundle(root: Path) -> dict[str, Any]:
    """Build a v1 bundle at root, returning the bundle payload."""
    fact = _fact()
    fact_id = fact["fact_id"]
    plan = {
        "schema_version": 1, "mode": "readme", "languages": ["en"],
        "sections": ["overview"], "visual_intent": "project-structure",
        "diagram_route": "static", "commands": [], "evidence_ids": [fact_id],
    }
    claim_map = {
        "schema_version": 1,
        "markdown_blocks": [{
            "claim_id": "markdown:en:overview", "content_sha256": "0" * 64,
            "claim_kind": "factual", "evidence_sha256": "1" * 64,
            "truth_id": fact_id, "language_pair_id": None,
        }],
        "diagram_labels": [],
    }
    asset_raw = b"<svg>legacy\n</svg>\n"
    asset_manifest = {
        "schema_version": 2,
        "assets": [{
            "asset_id": "diagram", "path": "assets/diagram.svg",
            "sha256": hashlib.sha256(asset_raw).hexdigest(),
        }],
    }
    retrieval = {"schema_version": 1, "status": "available", "records": []}
    (root / "assets").mkdir()
    (root / "assets" / "diagram.svg").write_bytes(asset_raw)
    (root / "README.md").write_bytes(b"# Legacy\n")
    write_canonical_json_atomic(root / "readme-plan.json", plan)
    write_canonical_json_atomic(root / "claim-map.json", claim_map)
    write_canonical_json_atomic(root / "asset-manifest.json", asset_manifest)
    write_canonical_json_atomic(root / "retrieval-packet.json", retrieval)
    readme_ref = {"path": "README.md", "sha256": hashlib.sha256(b"# Legacy\n").hexdigest()}
    candidate = {
        "readme": readme_ref,
        "assets": [{"path": "assets/diagram.svg", "sha256": hashlib.sha256(asset_raw).hexdigest()}],
    }
    artifacts = {
        name: {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
        for name, (path, raw) in {
            "plan": ("readme-plan.json", canonical_json_bytes(plan)),
            "retrieval": ("retrieval-packet.json", canonical_json_bytes(retrieval)),
            "claim_map": ("claim-map.json", canonical_json_bytes(claim_map)),
            "asset_manifest": ("asset-manifest.json", canonical_json_bytes(asset_manifest)),
        }.items()
    }
    return {
        "schema_version": 1,
        "mode": "readme",
        "target": {"repository": "owner/repo", "base_sha": "a" * 40},
        "candidate": candidate,
        "artifacts": artifacts,
    }


def _approval() -> dict[str, Any]:
    return {
        "schema_version": 2,
        "decision": "approve",
        "repository": "owner/repo",
        "base_sha": "a" * 40,
        "proposed_branch": "readme-showcase/aaaaaaaaaaaa",
        "pr_fingerprint": "e" * 64,
        "candidate_hashes": [{"path": "README.md", "sha256": "b" * 64}],
        "evaluation_sha256": "d" * 64,
        "preview": {
            "path": "output/preview/index.html",
            "preview_sha256": "f" * 64,
            "report_path": "output/preview/report.json",
            "report_sha256": "1" * 64,
        },
        "actions": ["create-branch", "commit-files", "push-branch", "open-pull-request"],
    }


def _lock_for(bundle: dict[str, Any], root: Path, approval: dict[str, Any]) -> dict[str, Any]:
    plan_ref = bundle["artifacts"]["plan"]
    from skill.scripts.readme_showcase.validation.legacy import _artifact_json

    plan, _ = _artifact_json(root, plan_ref, "bundle artifacts.plan")
    from skill.scripts.readme_showcase.contracts.plan_lock import _claim_ids

    claim_ids = _claim_ids(root, bundle["artifacts"])
    asset_hashes = [
        {"path": item["path"], "sha256": item["sha256"]}
        for item in bundle["candidate"]["assets"]
    ]
    return build_plan_lock(
        plan,
        asset_hashes=asset_hashes,
        claim_ids=claim_ids,
        approval_sha256=canonical_sha256(approval),
        locked_after="2026-08-16T00:00:00Z",
    )


class PlanLockSchemaTests(unittest.TestCase):
    def test_valid_lock_passes(self) -> None:
        payload = json.loads(
            (REPO_ROOT / "tests/fixtures/contracts/plan-lock-v1.valid.json").read_text()
        )
        self.assertEqual(validate_plan_lock_v1(payload), payload)

    def test_missing_field_raises_plan_lock(self) -> None:
        payload = json.loads(
            (REPO_ROOT / "tests/fixtures/contracts/plan-lock-v1.valid.json").read_text()
        )
        del payload["plan_sha256"]
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(payload)
        self.assertEqual(raised.exception.code, "E_PLAN_LOCK")

    def test_extra_field_raises_schema_unknown(self) -> None:
        payload = json.loads(
            (REPO_ROOT / "tests/fixtures/contracts/plan-lock-v1.valid.json").read_text()
        )
        payload["lock_source"] = "agent"
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(payload)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

    def test_report_valid_and_invalid_codes(self) -> None:
        report = {
            "schema_version": 1, "status": "pass",
            "lock_sha256": "a" * 64, "plan_sha256": "b" * 64, "findings": [],
        }
        self.assertEqual(validate_plan_lock_report_v1(report), report)
        missing = json.loads(json.dumps(report))
        del missing["status"]
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_report_v1(missing)
        self.assertEqual(raised.exception.code, "E_PLAN_LOCK")
        extra = json.loads(json.dumps(report))
        extra["decision_input"] = True
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_report_v1(extra)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")


class PlanLockDriftTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bundle = _v2_bundle(self.root)
        self.approval = _approval()
        self.lock = _lock_for(self.bundle, self.root, self.approval)
        write_canonical_json_atomic(self.root / APPROVAL_ENVELOPE_PATH, self.approval)

    def _check(self, lock: dict[str, Any], bundle: dict[str, Any] | None = None) -> dict[str, Any]:
        return validate_plan_lock_v1(
            lock,
            bundle=bundle if bundle is not None else self.bundle,
            artifact_root=self.root,
            approval_envelope=self.approval,
        )

    def test_consistent_lock_passes_and_pins_canonical_plan(self) -> None:
        plan_ref = self.bundle["artifacts"]["plan"]
        from skill.scripts.readme_showcase.validation.legacy import _artifact_json

        plan, _ = _artifact_json(self.root, plan_ref, "bundle artifacts.plan")
        self.assertEqual(
            self.lock["plan_sha256"],
            hashlib.sha256(canonical_readme_plan_bytes(plan, version=2)).hexdigest(),
        )
        report = self._check(self.lock)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["lock_sha256"], canonical_sha256(self.lock))
        self.assertEqual(report["plan_sha256"], self.lock["plan_sha256"])

    def test_plan_byte_drift_raises_plan_drift(self) -> None:
        tampered = json.loads(json.dumps(self.lock))
        tampered["plan_sha256"] = "f" * 64
        with self.assertRaises(ContractError) as raised:
            self._check(tampered)
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_locale_drift_raises_plan_drift(self) -> None:
        tampered = json.loads(json.dumps(self.lock))
        tampered["locale_pairs"] = [["en", "zh-Hans"]]
        with self.assertRaises(ContractError) as raised:
            self._check(tampered)
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_asset_drift_raises_plan_drift(self) -> None:
        tampered = json.loads(json.dumps(self.lock))
        tampered["asset_sha256"] = []
        with self.assertRaises(ContractError) as raised:
            self._check(tampered)
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_claim_drift_raises_plan_drift(self) -> None:
        tampered = json.loads(json.dumps(self.lock))
        tampered["claim_ids"] = []
        with self.assertRaises(ContractError) as raised:
            self._check(tampered)
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_claim_map_ref_deleted_with_empty_lock_still_raises_drift(self) -> None:
        bundle = json.loads(json.dumps(self.bundle))
        bundle["artifacts"].pop("claim_map")
        tampered = json.loads(json.dumps(self.lock))
        tampered["claim_ids"] = []
        with self.assertRaises(ContractError) as raised:
            self._check(tampered, bundle=bundle)
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_plan_artifact_bytes_tampered_after_lock_raises_drift(self) -> None:
        from skill.scripts.readme_showcase.generation.assembler import assemble_generated_bundle

        plan_path = self.root / "readme-plan.json"
        plan = json.loads(plan_path.read_text())
        plan["visual_intent"] = "tampered intent"
        write_canonical_json_atomic(plan_path, plan)
        rebuilt = _v2_bundle_with_plan(self.root, plan)
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(
                self.lock,
                bundle=rebuilt,
                artifact_root=self.root,
                approval_envelope=self.approval,
            )
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_forged_lock_without_approval_envelope_is_rejected(self) -> None:
        forged = json.loads(json.dumps(self.lock))
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(
                forged,
                bundle=self.bundle,
                artifact_root=self.root,
                approval_envelope=None,
            )
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_forged_lock_with_rejected_approval_is_rejected(self) -> None:
        rejected = json.loads(json.dumps(self.approval))
        rejected["decision"] = "reject"
        forged = json.loads(json.dumps(self.lock))
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(
                forged,
                bundle=self.bundle,
                artifact_root=self.root,
                approval_envelope=rejected,
            )
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_forged_lock_with_different_approval_binding_is_rejected(self) -> None:
        other = json.loads(json.dumps(self.approval))
        other["proposed_branch"] = "readme-showcase/bbbbbbbbbbbb"
        forged = json.loads(json.dumps(self.lock))
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(
                forged,
                bundle=self.bundle,
                artifact_root=self.root,
                approval_envelope=other,
            )
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_v1_bundle_drift_is_also_blocked(self) -> None:
        v1_root = self.root / "v1"
        v1_root.mkdir()
        v1_bundle = _v1_bundle(v1_root)
        v1_lock = _lock_for(v1_bundle, v1_root, self.approval)
        report = validate_plan_lock_v1(
            v1_lock,
            bundle=v1_bundle,
            artifact_root=v1_root,
            approval_envelope=self.approval,
        )
        self.assertEqual(report["status"], "pass")
        drifted = json.loads(json.dumps(v1_lock))
        drifted["plan_sha256"] = "f" * 64
        with self.assertRaises(ContractError) as raised:
            validate_plan_lock_v1(
                drifted,
                bundle=v1_bundle,
                artifact_root=v1_root,
                approval_envelope=self.approval,
            )
        self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")


class PlanLockGateTests(unittest.TestCase):
    @staticmethod
    def _v1_evidence() -> dict[str, Any]:
        contents = {
            "README.md": "target repository evidence\n",
            "docs/guide.md": "target guide evidence\n",
            "src/main.py": "print('demo')\n",
            "tests/test_main.py": "def test_demo():\n    pass\n",
        }

        def digest(content: str) -> str:
            return hashlib.sha256(content.encode()).hexdigest()

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
            "files": [
                {"path": path, "bytes": len(content.encode()), "lines": len(content.splitlines()), "sha256": digest(content), "content": content}
                for path, content in contents.items()
            ],
            "facts": [
                {"fact_id": f"file:{path}", "kind": "repository-file", "path": path, "evidence_sha256": digest(content)}
                for path, content in contents.items()
            ],
            "warnings": [],
        }

    def _context(self, root: Path, *, requires: bool, bundle: dict[str, Any]) -> Any:
        candidate = FIXTURES / "v1-candidate"
        plan_raw = (FIXTURES / "v1-plan.json").read_bytes()
        evidence_raw = canonical_json_bytes(self._v1_evidence())
        retrieval_raw = canonical_json_bytes(
            {"schema_version": 1, "status": "unavailable", "records": []}
        )
        claim_raw = (candidate / "claim-map.json").read_bytes()
        manifest_raw = (candidate / "asset-manifest.json").read_bytes()
        for destination, name, source in (
            (root / "stages/01-scan/attempts/1", "repository-evidence.json", evidence_raw),
            (root / "stages/02-retrieve/attempts/1", "retrieval-packet.json", retrieval_raw),
            (root / "stages/03-plan-import/attempts/1", "readme-plan.json", plan_raw),
        ):
            destination.mkdir(parents=True, exist_ok=True)
            (destination / name).write_bytes(source)
        bundle_path = root / "stages/06-bundle-assemble/attempts/1/generated-readme-bundle.json"
        bundle_path.parent.mkdir(parents=True, exist_ok=True)
        bundle_path.write_bytes(canonical_json_bytes(bundle))
        stage5 = root / "stages/05-candidate"
        stage5.mkdir(parents=True, exist_ok=True)
        (stage5 / "claim-map.json").write_bytes(claim_raw)
        (stage5 / "asset-manifest.json").write_bytes(manifest_raw)
        for relative in ("README.md", "README_zh.md"):
            (stage5 / relative).write_bytes((candidate / relative).read_bytes())

        class Workspace:
            def __init__(self, workspace_root: Path) -> None:
                self.root = workspace_root

        class Context:
            def __init__(self, workspace_root: Path) -> None:
                self.workspace = Workspace(workspace_root)
                self.manifest = {
                    "requires_plan_lock": requires,
                    "configuration": {"mode": "readme", "locales": ["en"]},
                    "target": {"repository": "local/repository", "base_sha": "a" * 40},
                }
                self.cache: dict[str, Any] = {}

            def attempt_file(self, stage_index: int, name: str) -> Path:
                if stage_index == 0:
                    return self.workspace.root / "stages/01-scan/attempts/1" / name
                if stage_index == 1:
                    return self.workspace.root / "stages/02-retrieve/attempts/1" / name
                if stage_index == 2:
                    return self.workspace.root / "stages/03-plan-import/attempts/1" / name
                if stage_index == 5:
                    return self.workspace.root / "stages/06-bundle-assemble/attempts/1" / name
                return self.workspace.root / "stages/07-validation/attempts/1" / name

        return Context(root)

    @staticmethod
    def _v1_bundle(root: Path) -> dict[str, Any]:
        candidate = FIXTURES / "v1-candidate"
        plan_raw = (FIXTURES / "v1-plan.json").read_bytes()
        retrieval_raw = canonical_json_bytes(
            {"schema_version": 1, "status": "unavailable", "records": []}
        )
        claim_raw = (candidate / "claim-map.json").read_bytes()
        manifest_raw = (candidate / "asset-manifest.json").read_bytes()
        readme_raw = (candidate / "README.md").read_bytes()
        return {
            "schema_version": 1,
            "mode": "readme",
            "target": {"repository": "local/repository", "base_sha": "a" * 40},
            "candidate": {
                "readme": {"path": "README.md", "sha256": hashlib.sha256(readme_raw).hexdigest()},
                "assets": [],
            },
            "artifacts": {
                name: {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}
                for name, (path, raw) in {
                    "plan": ("readme-plan.json", plan_raw),
                    "retrieval": ("retrieval-packet.json", retrieval_raw),
                    "claim_map": ("claim-map.json", claim_raw),
                    "asset_manifest": ("asset-manifest.json", manifest_raw),
                }.items()
            },
        }

    def _fixture_lock(self, root: Path, bundle: dict[str, Any], approval: dict[str, Any]) -> dict[str, Any]:
        plan_raw = (FIXTURES / "v1-plan.json").read_bytes()
        plan = json.loads(plan_raw)
        claim_map = json.loads((FIXTURES / "v1-candidate/claim-map.json").read_text(encoding="utf-8"))
        claim_ids = sorted(
            claim["claim_id"]
            for claim in claim_map.get("markdown_blocks", [])
            if isinstance(claim.get("claim_id"), str)
        )
        return build_plan_lock(
            plan,
            asset_hashes=[],
            claim_ids=claim_ids,
            approval_sha256=canonical_sha256(approval),
            locked_after="2026-08-16T00:00:00Z",
        )

    def test_requires_lock_missing_lock_raises_plan_lock(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            context = self._context(root, requires=True, bundle=bundle)
            with self.assertRaises(ContractError) as raised:
                EvaluateStage().execute(context)
            self.assertEqual(raised.exception.code, "E_PLAN_LOCK")

    def test_requires_lock_present_consistent_passes_and_writes_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            approval = _approval()
            write_canonical_json_atomic(root / APPROVAL_ENVELOPE_PATH, approval)
            lock = self._fixture_lock(root, bundle, approval)
            write_canonical_json_atomic(root / PLAN_LOCK_PATH, lock)
            context = self._context(root, requires=True, bundle=bundle)
            result = EvaluateStage().execute(context)
            self.assertEqual(result.status, "pass")
            self.assertIn("plan-lock-report.v1.json", result.files)
            report = json.loads(result.files["plan-lock-report.v1.json"])
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["plan_sha256"], lock["plan_sha256"])

    def test_no_lock_required_without_lock_skips_report(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            context = self._context(root, requires=False, bundle=bundle)
            result = EvaluateStage().execute(context)
            self.assertEqual(result.status, "pass")
            self.assertNotIn("plan-lock-report.v1.json", result.files)

    def test_lock_present_but_manifest_false_still_checks_belt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            approval = _approval()
            write_canonical_json_atomic(root / APPROVAL_ENVELOPE_PATH, approval)
            lock = self._fixture_lock(root, bundle, approval)
            lock["plan_sha256"] = "f" * 64
            write_canonical_json_atomic(root / PLAN_LOCK_PATH, lock)
            context = self._context(root, requires=False, bundle=bundle)
            with self.assertRaises(ContractError) as raised:
                EvaluateStage().execute(context)
            self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_missing_claim_map_artifact_fails_gate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            approval = _approval()
            write_canonical_json_atomic(root / APPROVAL_ENVELOPE_PATH, approval)
            lock = self._fixture_lock(root, bundle, approval)
            write_canonical_json_atomic(root / PLAN_LOCK_PATH, lock)
            bundle["artifacts"].pop("claim_map")
            context = self._context(root, requires=True, bundle=bundle)
            with self.assertRaises(ContractError) as raised:
                EvaluateStage().execute(context)
            self.assertIn(raised.exception.code, {"E_PLAN_LOCK", "E_PLAN_DRIFT"})

    def test_forged_report_with_pass_status_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            approval = _approval()
            write_canonical_json_atomic(root / APPROVAL_ENVELOPE_PATH, approval)
            lock = self._fixture_lock(root, bundle, approval)
            lock["plan_sha256"] = "f" * 64
            write_canonical_json_atomic(root / PLAN_LOCK_PATH, lock)
            forged = {
                "schema_version": 1, "status": "pass",
                "lock_sha256": canonical_sha256(lock),
                "plan_sha256": lock["plan_sha256"], "findings": [],
            }
            report_path = root / "stages/08-evaluation/attempts/1" / PLAN_LOCK_REPORT_PATH
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_bytes(canonical_json_bytes(forged))
            context = self._context(root, requires=True, bundle=bundle)
            with self.assertRaises(ContractError) as raised:
                EvaluateStage().execute(context)
            self.assertEqual(raised.exception.code, "E_PLAN_DRIFT")

    def test_check_plan_lock_direct_belt_semantics(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self._v1_bundle(root)
            (root / "readme-plan.json").write_bytes(
                (FIXTURES / "v1-plan.json").read_bytes()
            )
            (root / "claim-map.json").write_bytes(
                (FIXTURES / "v1-candidate/claim-map.json").read_bytes()
            )
            approval = _approval()
            write_canonical_json_atomic(root / APPROVAL_ENVELOPE_PATH, approval)
            lock = self._fixture_lock(root, bundle, approval)
            write_canonical_json_atomic(root / PLAN_LOCK_PATH, lock)
            self.assertIsNotNone(
                check_plan_lock(
                    lock_path=root / PLAN_LOCK_PATH,
                    approval_path=root / APPROVAL_ENVELOPE_PATH,
                    manifest={"requires_plan_lock": False},
                    bundle=bundle,
                    artifact_root=root,
                )
            )
            (root / PLAN_LOCK_PATH).unlink()
            self.assertIsNone(
                check_plan_lock(
                    lock_path=root / PLAN_LOCK_PATH,
                    approval_path=root / APPROVAL_ENVELOPE_PATH,
                    manifest={"requires_plan_lock": False},
                    bundle=bundle,
                    artifact_root=root,
                )
            )
            with self.assertRaises(ContractError) as raised:
                check_plan_lock(
                    lock_path=root / PLAN_LOCK_PATH,
                    approval_path=root / APPROVAL_ENVELOPE_PATH,
                    manifest={"requires_plan_lock": True},
                    bundle=bundle,
                    artifact_root=root,
                )
            self.assertEqual(raised.exception.code, "E_PLAN_LOCK")


class PlanLockRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.workspace = self.root / "workspace"
        self.target.mkdir()
        for relative, content in {
            "README.md": "target repository evidence\n",
            "docs/guide.md": "target guide evidence\n",
            "src/main.py": "print('demo')\n",
            "tests/test_main.py": "def test_demo():\n    pass\n",
        }.items():
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", ".")
        self.git("commit", "-m", "fixture")

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=self.target, capture_output=True, text=True, check=True
        ).stdout.strip()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def manifest(self) -> dict[str, Any]:
        return json.loads((self.workspace / "run-manifest.json").read_text(encoding="utf-8"))

    def start(self) -> None:
        result = self.cli(
            "run", "--root", str(self.target), "--workspace", str(self.workspace),
            "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
            "--plan", str(FIXTURES / "v1-plan.json"), "--stop-after", "generation-request",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copytree(
            FIXTURES / "v1-candidate",
            self.workspace / "stages/05-candidate",
            dirs_exist_ok=True,
        )

    def write_workspace_lock(self, *, tampered: bool = False) -> None:
        plan = json.loads(
            (self.workspace / "inputs/readme-plan.json").read_text(encoding="utf-8")
        )
        claim_map = json.loads(
            (self.workspace / "stages/05-candidate/claim-map.json").read_text(encoding="utf-8")
        )
        claim_ids = sorted(
            claim["claim_id"]
            for claim in claim_map.get("markdown_blocks", [])
            if isinstance(claim.get("claim_id"), str)
        )
        asset_root = self.workspace / "stages/05-candidate"
        asset_hashes = []
        asset_manifest_path = asset_root / "asset-manifest.json"
        if asset_manifest_path.is_file():
            asset_manifest = json.loads(asset_manifest_path.read_text(encoding="utf-8"))
            for item in asset_manifest.get("assets", []):
                if isinstance(item.get("path"), str) and isinstance(item.get("sha256"), str):
                    asset_hashes.append({"path": item["path"], "sha256": item["sha256"]})
        approval = _approval()
        lock = build_plan_lock(
            plan,
            asset_hashes=asset_hashes,
            claim_ids=claim_ids,
            approval_sha256=canonical_sha256(approval),
            locked_after="2026-08-16T00:00:00Z",
        )
        if tampered:
            lock["plan_sha256"] = "f" * 64
        write_canonical_json_atomic(self.workspace / PLAN_LOCK_PATH, lock)
        write_canonical_json_atomic(self.workspace / APPROVAL_ENVELOPE_PATH, approval)

    def mutate_candidate(self) -> None:
        readme = self.workspace / "stages/05-candidate/README.md"
        readme.write_text("# Changed\n", encoding="utf-8")
        claim_path = self.workspace / "stages/05-candidate/claim-map.json"
        claim_map = json.loads(claim_path.read_text(encoding="utf-8"))
        claim_map["markdown_blocks"][0]["content_sha256"] = hashlib.sha256(
            b"# Changed"
        ).hexdigest()
        claim_path.write_bytes(canonical_json_bytes(claim_map))

    def test_locked_run_passes_and_writes_pass_report(self) -> None:
        self.start()
        self.write_workspace_lock()
        completed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        manifest = self.manifest()
        self.assertEqual(manifest["status"], "complete")
        self.assertEqual(manifest["requires_plan_lock"], True)
        report_path = (
            self.workspace / "stages/08-evaluation/attempts/1" / PLAN_LOCK_REPORT_PATH
        )
        self.assertTrue(report_path.is_file())
        report = json.loads(report_path.read_text(encoding="utf-8"))
        self.assertEqual(report["status"], "pass")

    def test_requires_plan_lock_with_deleted_lock_fails_run(self) -> None:
        self.start()
        self.write_workspace_lock()
        completed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(self.manifest()["requires_plan_lock"], True)
        (self.workspace / PLAN_LOCK_PATH).unlink()
        self.mutate_candidate()
        failed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2, failed.stderr)
        self.assertIn("E_PLAN_LOCK", failed.stderr)
        self.assertEqual(self.manifest()["status"], "failed")

    def test_unlocked_run_with_inconsistent_lock_fails_run(self) -> None:
        self.start()
        self.assertNotEqual(self.manifest().get("requires_plan_lock", False), True)
        self.write_workspace_lock(tampered=True)
        failed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(failed.returncode, 2, failed.stderr)
        self.assertIn("E_PLAN_DRIFT", failed.stderr)
        self.assertEqual(self.manifest()["status"], "failed")


class PlanLockErrorCodeTests(unittest.TestCase):
    def test_known_error_codes_contain_all_three_plan_lock_codes(self) -> None:
        for code in ("E_PLAN_DRIFT", "E_PLAN_LOCK", "E_CONFIG_MISSING_KEY"):
            self.assertIn(code, KNOWN_ERROR_CODES)


class PlanLockFieldParityTests(unittest.TestCase):
    def test_python_field_sets_match_schema_required_arrays(self) -> None:
        from skill.scripts.readme_showcase.contracts.plan_lock import (
            _LOCK_FIELDS,
            _REPORT_FIELDS,
        )

        lock_schema = json.loads(
            (REPO_ROOT / "skill/schemas/plan-lock.v1.schema.json").read_text(encoding="utf-8")
        )
        report_schema = json.loads(
            (REPO_ROOT / "skill/schemas/plan-lock-report.v1.schema.json").read_text(encoding="utf-8")
        )
        self.assertEqual(_LOCK_FIELDS, set(lock_schema["required"]))
        self.assertEqual(_REPORT_FIELDS, set(report_schema["required"]))


if __name__ == "__main__":
    unittest.main()
