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


class BundleContractTests(unittest.TestCase):
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
                    b'<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
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
                semantic_value = json.loads(
                    (REPO_ROOT / "tests/fixtures/glyphic/architecture.json").read_text(
                        encoding="utf-8",
                    )
                )
                semantic = self.write_json(
                    root,
                    "assets/readme/diagram.glyphic.json",
                    semantic_value,
                )
                fallback = self.write_bytes(
                    root,
                    "assets/readme/diagram.static.svg",
                    b'<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
                )
                metadata_value = {
                    "schema_version": 1,
                    "engine_kind": "glyphic",
                    "source_commit": "e" * 40,
                    "package_version": "1.3.1",
                    "core_version": "1.3.1",
                    "engine_schema_version": "1",
                    "package_sha256": "1" * 64,
                    "tree_sha256": "2" * 64,
                    "sri": "sha512-example",
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
                            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
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
                            b'<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
                        ),
                    }
                )
            elif production_kind == "motion":
                fallback = self.write_bytes(
                    root,
                    "assets/readme/hero-static.svg",
                    b'<svg xmlns="http://www.w3.org/2000/svg"></svg>\n',
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
            self.write_bytes(root, "README.generated.md", b"# Generated\n")
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


if __name__ == "__main__":
    unittest.main()
