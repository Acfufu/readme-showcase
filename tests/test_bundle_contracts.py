from __future__ import annotations

import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


_CONTRACTS = importlib.import_module("skill.scripts.pipeline_contracts")
_CORE = importlib.import_module("skill.scripts.pipeline_core")
ContractError = _CONTRACTS.ContractError
canonical_sha256 = _CONTRACTS.canonical_sha256
write_canonical_json_atomic = _CONTRACTS.write_canonical_json_atomic
validate_generated_bundle = _CORE.validate_generated_bundle
REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_COMMIT = "ed79edb1624e2de78041611971a963efaea5e080"


class BundleContractTests(unittest.TestCase):
    def valid_svg(
        self,
        title: str = "Project architecture",
        labels: list[str] | None = None,
    ) -> bytes:
        text = "".join(
            f'<text x="10" y="{40 + index * 20}">{label}</text>'
            for index, label in enumerate(labels or [])
        )
        return (
            '<svg xmlns="http://www.w3.org/2000/svg" width="1200" height="480" '
            f'viewBox="0 0 1200 480" role="img"><title>{title}</title>{text}</svg>\n'
        ).encode()

    def write_json(self, root: Path, path: str, value: object) -> dict[str, str]:
        destination = root / path
        write_canonical_json_atomic(destination, value)
        return {"path": path, "sha256": canonical_sha256(value)}

    def write_bytes(self, root: Path, path: str, value: bytes) -> dict[str, str]:
        destination = root / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
        return {"path": path, "sha256": __import__("hashlib").sha256(value).hexdigest()}

    def make_bundle(
        self,
        root: Path,
        mode: str,
        *,
        glyphic: bool = False,
        production_kind: str = "static",
    ) -> tuple[dict[str, Any], Path]:
        plan = self.write_json(
            root,
            "readme-plan.json",
            {
                "schema_version": 1,
                "mode": mode,
                "languages": ["en"],
                "sections": ["overview"],
                "visual_intent": "project-structure",
                "diagram_route": "glyphic" if glyphic else "static",
                "commands": [],
                "evidence_ids": ["file:README.md"],
            },
        )
        claims = self.write_json(
            root,
            "claim-map.json",
            {
                "schema_version": 1,
                "markdown_blocks": [],
                "diagram_labels": [
                    {
                        "claim_id": "diagram:architecture",
                        "content_sha256": "6" * 64,
                        "claim_kind": "factual",
                        "evidence_sha256": "7" * 64,
                        "truth_id": "file:README.md",
                        "language_pair_id": None,
                    }
                ],
            },
        )
        retrieval = self.write_json(
            root,
            "retrieval-packet.json",
            {"schema_version": 1, "status": "unavailable", "records": []},
        )
        assets: list[dict[str, object]] = []
        candidate_assets: list[dict[str, str]] = []
        if mode != "audit-only":
            semantic_value = (
                json.loads(
                    (REPO_ROOT / "tests/fixtures/glyphic/architecture.json").read_text(
                        encoding="utf-8",
                    )
                )
                if glyphic
                else None
            )
            if production_kind == "hybrid":
                raw = self.write_bytes(root, "assets/readme/hero.png", b"\x89PNG\r\n\x1a\nhybrid")
                asset_type = "png"
            elif production_kind == "motion":
                raw = self.write_bytes(root, "assets/readme/hero.gif", b"GIF89a-motion")
                asset_type = "gif"
            else:
                raw = self.write_bytes(
                    root,
                    "assets/readme/diagram.svg",
                    self.valid_svg(
                        semantic_value["accessibility_title"] if semantic_value else "Project architecture",
                        (
                            [item["label"] for item in semantic_value["groups"]]
                            + [item["label"] for item in semantic_value["nodes"]]
                            + [
                                item["label"]
                                for item in semantic_value["edges"]
                                if item["label"] is not None
                            ]
                            if semantic_value
                            else None
                        ),
                    ),
                )
                asset_type = "svg"
            candidate_assets.append(raw)
            asset: dict[str, object] = {
                **raw,
                "type": asset_type,
                "engine_kind": "glyphic" if glyphic else "hand-authored",
                "production_kind": production_kind,
                "alt": "Project architecture",
                "caption": "Evidence-bound architecture.",
                "truth_ids": ["file:README.md"],
            }
            if glyphic:
                assert semantic_value is not None
                semantic = self.write_json(
                    root,
                    "assets/readme/diagram.glyphic.json",
                    semantic_value,
                )
                fallback = self.write_bytes(
                    root,
                    "assets/readme/diagram.static.svg",
                    self.valid_svg("Static architecture fallback"),
                )
                metadata_value = {
                    "schema_version": 1,
                    "engine_kind": "glyphic",
                    "source_commit": SOURCE_COMMIT,
                    "package_version": "1.3.1",
                    "core_version": "1.3.1",
                    "engine_schema_version": "1",
                    "package_sha256": "1" * 64,
                    "tree_sha256": "2" * 64,
                    "sri": "sha512-ZmZmZmZmZmZmZmZm",
                    "license_spdx": "FSL-1.1-ALv2",
                    "license_sha256": "3" * 64,
                    "lock_sha256": "4" * 64,
                    "node_version": "22.17.0",
                    "platform": "darwin",
                    "architecture": "arm64",
                    "input_sha256": semantic["sha256"],
                    "theme_sha256": "5" * 64,
                    "output_sha256": raw["sha256"],
                    "run_hashes": [raw["sha256"], raw["sha256"]],
                    "validation": "pass",
                    "fallback_state": "preserved",
                }
                metadata = self.write_json(
                    root,
                    "assets/readme/diagram.engine.json",
                    metadata_value,
                )
                asset.update(
                    {
                        "semantic": semantic,
                        "engine_metadata": metadata,
                        "fallback": fallback,
                    }
                )
            elif production_kind == "hybrid":
                asset.update(
                    {
                        "layout": self.write_bytes(
                            root,
                            "assets/readme/source/hero-layout.svg",
                            self.valid_svg("Editable hero layout"),
                        ),
                        "subject": self.write_bytes(
                            root,
                            "assets/readme/source/hero-subject.png",
                            b"\x89PNG\r\n\x1a\nsubject",
                        ),
                        "prompt": self.write_bytes(
                            root,
                            "assets/readme/source/hero-prompt.txt",
                            b"Project-specific subject without text.\n",
                        ),
                        "fallback": self.write_bytes(
                            root,
                            "assets/readme/hero-static.svg",
                            self.valid_svg("Static hero fallback"),
                        ),
                    }
                )
            elif production_kind == "motion":
                fallback = self.write_bytes(
                    root,
                    "assets/readme/hero-static.svg",
                    self.valid_svg("Static hero fallback"),
                )
                asset.update(
                    {
                        "source": fallback,
                        "motion_spec": self.write_json(
                            root,
                            "assets/readme/hero-motion.json",
                            {"schema_version": 1, "duration_ms": 5000},
                        ),
                        "fallback": fallback,
                        "motion_approved": True,
                    }
                )
            assets.append(asset)
        asset_manifest = self.write_json(
            root,
            "asset-manifest.json",
            {"schema_version": 1, "assets": assets},
        )
        readme = (
            self.write_bytes(
                root,
                "README.generated.md",
                (
                    b"# Generated\n\n"
                    + (
                        f"![Project architecture]({candidate_assets[0]['path']})\n\n"
                        "Evidence-bound architecture.\n"
                    ).encode()
                    if candidate_assets
                    else b"# Generated\n"
                ),
            )
            if mode == "readme"
            else None
        )
        bundle: dict[str, Any] = {
            "schema_version": 1,
            "mode": mode,
            "target": {"repository": "owner/repository", "base_sha": "a" * 40},
            "candidate": {"readme": readme, "assets": candidate_assets},
            "artifacts": {
                "plan": plan,
                "retrieval": retrieval,
                "claim_map": claims,
                "asset_manifest": asset_manifest,
            },
        }
        bundle_path = root / "generated-readme-bundle.json"
        write_canonical_json_atomic(bundle_path, bundle)
        return bundle, bundle_path

    def replace_primary_asset(
        self,
        root: Path,
        bundle: dict[str, Any],
        raw: bytes,
    ) -> None:
        asset_path = bundle["candidate"]["assets"][0]["path"]
        reference = self.write_bytes(root, asset_path, raw)
        bundle["candidate"]["assets"][0] = reference
        manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        asset = manifest["assets"][0]
        asset.update(reference)
        if asset["engine_kind"] == "glyphic":
            metadata_path = root / asset["engine_metadata"]["path"]
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["output_sha256"] = reference["sha256"]
            metadata["run_hashes"] = [reference["sha256"], reference["sha256"]]
            write_canonical_json_atomic(metadata_path, metadata)
            asset["engine_metadata"]["sha256"] = canonical_sha256(metadata)
        write_canonical_json_atomic(manifest_path, manifest)
        bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)

    def assert_code(self, root: Path, bundle: dict[str, Any], code: str) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_generated_bundle(bundle, root)
        self.assertEqual(raised.exception.code, code)

    def test_readme_asset_only_and_audit_modes_validate(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for mode, glyphic in (
                ("readme", False),
                ("asset-only", True),
                ("audit-only", False),
            ):
                with self.subTest(mode=mode):
                    root = base / mode
                    root.mkdir()
                    bundle, _ = self.make_bundle(root, mode, glyphic=glyphic)
                    self.assertEqual(validate_generated_bundle(bundle, root)["status"], "pass")

    def test_mode_mutation_and_stale_hash_fail_without_mutation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "asset-only")
            bundle["candidate"]["readme"] = {"path": "README.md", "sha256": "0" * 64}
            self.assert_code(root, bundle, "E_BUNDLE_MODE")

            bundle, _ = self.make_bundle(root, "readme")
            readme = root / "README.generated.md"
            readme.write_text("# Altered\n", encoding="utf-8")
            before = readme.read_bytes()
            self.assert_code(root, bundle, "E_BUNDLE_HASH")
            self.assertEqual(readme.read_bytes(), before)

    def test_traversal_missing_pair_and_engine_mismatch_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            bundle["candidate"]["assets"][0]["path"] = "../diagram.svg"
            self.assert_code(root, bundle, "E_PATH")

            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["assets"][0]["semantic"]
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_SCHEMA_MISSING_FIELD")

            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            metadata_path = root / "assets/readme/diagram.engine.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["output_sha256"] = "9" * 64
            write_canonical_json_atomic(metadata_path, metadata)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["engine_metadata"]["sha256"] = canonical_sha256(metadata)
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_ENGINE_METADATA")

            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            metadata_path = root / "assets/readme/diagram.engine.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            metadata["license_spdx"] = "MIT"
            write_canonical_json_atomic(metadata_path, metadata)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["engine_metadata"]["sha256"] = canonical_sha256(metadata)
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_ENGINE_METADATA")

    def test_cli_validates_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, bundle_path = self.make_bundle(root, "readme")
            result = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "validate-bundle",
                    "--bundle",
                    str(bundle_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "pass")

    def test_asset_type_command_language_and_alt_are_hard_gates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "readme")
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["type"] = "png"
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_BUNDLE_ASSET")

            bundle, _ = self.make_bundle(root, "readme")
            plan_path = root / bundle["artifacts"]["plan"]["path"]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["commands"] = ["python3 -m demo"]
            write_canonical_json_atomic(plan_path, plan)
            bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)
            self.assert_code(root, bundle, "E_README_COMMAND")

            bundle, _ = self.make_bundle(root, "readme")
            plan_path = root / bundle["artifacts"]["plan"]["path"]
            plan = json.loads(plan_path.read_text(encoding="utf-8"))
            plan["languages"] = ["en", "zh"]
            write_canonical_json_atomic(plan_path, plan)
            bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)
            self.assert_code(root, bundle, "E_README_LANGUAGE")

            bundle, _ = self.make_bundle(root, "readme")
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["assets"][0]["alt"] = ""
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_README_ACCESSIBILITY")

    def test_generic_svg_safety_and_glyphic_visible_text_are_hard_gates(self) -> None:
        unsafe = (
            b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
            b'viewBox="0 0 10 10" role="img"><title>Unsafe</title>'
            b'<script>alert(1)</script></svg>'
        )
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "asset-only")
            self.replace_primary_asset(root, bundle, unsafe)
            self.assert_code(root, bundle, "E_SVG_UNSAFE")

            bundle, _ = self.make_bundle(root, "asset-only")
            unresolved = (
                b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10" '
                b'viewBox="0 0 10 10" role="img" aria-labelledby="missing">'
                b"<title>Project architecture</title></svg>"
            )
            self.replace_primary_asset(root, bundle, unresolved)
            self.assert_code(root, bundle, "E_SVG_REFERENCE")

            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            semantic = json.loads(
                (
                    root
                    / "assets/readme/diagram.glyphic.json"
                ).read_text(encoding="utf-8")
            )
            labels = (
                [item["label"] for item in semantic["groups"]]
                + [item["label"] for item in semantic["nodes"]]
                + [
                    item["label"]
                    for item in semantic["edges"]
                    if item["label"] is not None
                ]
                + ["Engine invented claim"]
            )
            self.replace_primary_asset(
                root,
                bundle,
                self.valid_svg(semantic["accessibility_title"], labels),
            )
            self.assert_code(root, bundle, "E_SVG_LABELS")

            bundle, _ = self.make_bundle(root, "asset-only", glyphic=True)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            fallback = manifest["assets"][0]["fallback"]
            (root / fallback["path"]).write_bytes(unsafe)
            fallback["sha256"] = __import__("hashlib").sha256(unsafe).hexdigest()
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_SVG_UNSAFE")


if __name__ == "__main__":
    unittest.main()
