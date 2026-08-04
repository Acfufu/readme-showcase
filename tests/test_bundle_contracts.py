from __future__ import annotations

import copy
import hashlib
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest import mock


_CONTRACTS = importlib.import_module("skill.scripts.pipeline_contracts")
_CORE = importlib.import_module("skill.scripts.pipeline_core")
ContractError = _CONTRACTS.ContractError
canonical_sha256 = _CONTRACTS.canonical_sha256
canonical_json_bytes = _CONTRACTS.canonical_json_bytes
write_canonical_json_atomic = _CONTRACTS.write_canonical_json_atomic
validate_generated_bundle = _CORE.validate_generated_bundle
REPO_ROOT = Path(__file__).resolve().parents[1]

from skill.scripts.readme_showcase.contracts.assets import (
    ASSET_MANIFEST_SCHEMA_VERSION,
    canonical_asset_manifest_bytes,
    read_asset_manifest,
    validate_asset_manifest,
)
from skill.scripts.readme_showcase.contracts.claims import (
    CLAIM_MAP_SCHEMA_VERSION,
    adapt_v1_claim_map,
    canonical_claim_map_bytes,
)
from skill.scripts.readme_showcase.contracts.plan import README_PLAN_V2_SCHEMA_VERSION
from skill.scripts.readme_showcase.generation.assembler import GENERATED_BUNDLE_SCHEMA_VERSION
from skill.scripts.readme_showcase.visual_kernel.artifacts import build_compiled_artifacts
from skill.scripts.readme_showcase.visual_kernel.diagnostics import VisualGateReport
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec


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

    def make_compiled_asset_manifest(
        self,
        root: Path,
    ) -> tuple[dict[str, Any], list[dict[str, str]], dict[str, Any]]:
        """Materialize a truthful stage-6 output and its v3 manifest."""

        payload = _spec("flow")
        plan = normalize_visual_spec(payload, EVIDENCE)
        theme = resolve_theme()
        timeline = derive_timeline(plan)
        interaction_payload = copy.deepcopy(payload)
        interaction_payload["edges"] = interaction_payload["edges"][:1]  # type: ignore[index]
        interaction = derive_interaction(normalize_visual_spec(interaction_payload, EVIDENCE))
        spec_sha256 = hashlib.sha256(
            validate_visual_spec(payload, evidence_graph=EVIDENCE).canonical_bytes()
        ).hexdigest()
        identities = {
            name: hashlib.sha256(name.encode("utf-8")).hexdigest()
            for name in ("kernel", "elk", "renderer")
        }
        records: list[dict[str, object]] = []
        for locale in ("en", "zh-Hans"):
            for variant in ("desktop", "mobile"):
                scene = replace(_build("flow", variant), locale=locale)
                svg = serialize_svg(scene, theme)
                gate = VisualGateReport.build(
                    spec_sha256,
                    hashlib.sha256(scene.canonical_bytes()).hexdigest(),
                    hashlib.sha256(svg).hexdigest(),
                )
                records.append(
                    {
                        "locale": locale,
                        "variant": variant,
                        "scene": scene,
                        "svg": svg,
                        "gate": gate,
                        "timeline": timeline,
                        "interaction": interaction,
                    }
                )
        artifacts = build_compiled_artifacts(
            payload,
            theme,
            records,
            identities,
            evidence_graph=EVIDENCE,
        )
        for path, raw in artifacts.items():
            self.write_bytes(root, path, raw)
        for fact in EVIDENCE["facts"]:
            source = fact["source"]
            self.write_bytes(root, source["path"], str(fact["semantic_key"]).encode("utf-8"))

        inventory = json.loads(artifacts["compiled/inventory.json"].decode("utf-8"))
        layers = {layer["name"]: layer for layer in inventory["layers"]}
        artifact_hashes = {
            record["path"]: record["sha256"] for record in layers["artifacts"]["records"]
        }

        def single(path: str) -> dict[str, str]:
            return {"path": path, "sha256": artifact_hashes[path]}

        def variants(layer_name: str, pattern: str) -> list[dict[str, str]]:
            return [
                {
                    "locale": record["locale"],
                    "variant": record["variant"],
                    "path": pattern.format(locale=record["locale"], variant=record["variant"]),
                    "sha256": artifact_hashes[
                        pattern.format(locale=record["locale"], variant=record["variant"])
                    ],
                }
                for record in layers[layer_name]["records"]
            ]

        compiled = {
            "spec": single("compiled/visual-spec.json"),
            "theme": single("compiled/theme.json"),
            "inventory": {
                "path": "compiled/inventory.json",
                "sha256": hashlib.sha256(artifacts["compiled/inventory.json"]).hexdigest(),
            },
            "scenes": variants("scenes", "compiled/scenes/{locale}/{variant}.json"),
            "gates": variants("gates", "compiled/gates/{locale}/{variant}.json"),
            "timelines": variants("timelines", "compiled/timeline/{locale}/{variant}.json"),
            "interactions": variants("interactions", "compiled/interaction/{locale}/{variant}.json"),
            "svgs": [
                {
                    "locale": record["path"].split("/")[2],
                    "variant": record["path"].split("/")[3][:-4],
                    "path": record["path"],
                    "sha256": record["sha256"],
                }
                for record in layers["artifacts"]["records"]
                if record["path"].startswith("assets/readme-showcase/")
            ],
            "identities": layers["identities"]["values"],
        }
        scene_by_key = {(item["locale"], item["variant"]): item for item in compiled["scenes"]}
        gate_by_key = {(item["locale"], item["variant"]): item for item in compiled["gates"]}
        assets = []
        for svg in compiled["svgs"]:
            key = (svg["locale"], svg["variant"])
            scene = scene_by_key[key]
            gate = gate_by_key[key]
            assets.append(
                {
                    "asset_id": f"diagram-{svg['locale']}-{svg['variant']}",
                    "path": svg["path"],
                    "artifact_sha256": svg["sha256"],
                    "evidence_ids": [EVIDENCE["facts"][0]["fact_id"]],
                    "role": "diagram",
                    "locale": svg["locale"],
                    "variant": svg["variant"],
                    "scene_sha256": scene["sha256"],
                    "gate_sha256": gate["sha256"],
                    "provenance": {
                        "kind": "generated",
                        "path": scene["path"],
                        "sha256": scene["sha256"],
                    },
                }
            )
        manifest = {"schema_version": 3, "assets": assets, "compiled": compiled}
        candidates = [{"path": "visual-spec.json", "sha256": compiled["spec"]["sha256"]}]
        return manifest, candidates, EVIDENCE

    def make_bundle(
        self,
        root: Path,
        mode: str,
        *,
        elk: bool = False,
        production_kind: str = "static",
        diagram_route: str | None = None,
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
                "diagram_route": diagram_route or ("elk" if elk else "static"),
                "commands": [],
                "evidence_ids": ["file:README.md"],
            },
        )
        retrieval = self.write_json(
            root,
            "retrieval-packet.json",
            {"schema_version": 1, "status": "unavailable", "records": []},
        )
        assets: list[dict[str, object]] = []
        candidate_assets: list[dict[str, str]] = []
        semantic_value: dict[str, Any] | None = None
        if mode != "audit-only":
            semantic_value = (
                json.loads(
                    (REPO_ROOT / "tests/fixtures/elk/architecture.json").read_text(
                        encoding="utf-8",
                    )
                )
                if elk
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
                "engine_kind": "elk" if elk else "hand-authored",
                "production_kind": production_kind,
                "alt": "Project architecture",
                "caption": "Evidence-bound architecture.",
                "truth_ids": ["file:README.md"],
            }
            if elk:
                assert semantic_value is not None
                semantic = self.write_json(
                    root,
                    "assets/readme/diagram.diagram.json",
                    semantic_value,
                )
                fallback = self.write_bytes(
                    root,
                    "assets/readme/diagram.static.svg",
                    self.valid_svg("Static architecture fallback"),
                )
                metadata_value = {
                    "schema_version": 1,
                    "engine_kind": "elk",
                    "package_name": "elkjs",
                    "package_version": "0.9.3",
                    "package_integrity": (
                        "sha512-f/ZeWvW/BCXbhGEf1Ujp29EASo/lk1FDnETgNKwJrsVvGZhUWCZyg3xLJjAsxf"
                        "Omt8KjswHmI5EwCQcPMpOYhQ=="
                    ),
                    "package_sha256": "fb9bb80b980c72022fb4540b38aa0545242b4eb67b82250aeae2f0beb67eea25",
                    "module_sha256": "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4",
                    "license_spdx": "EPL-2.0",
                    "license_sha256": "89591d4578fb1ebd91501312a3d25f021bd865a2e436641c1cf7b1bc7e3c1617",
                    "node_version": "22.22.3",
                    "platform": "darwin",
                    "architecture": "arm64",
                    "input_sha256": semantic["sha256"],
                    "renderer_sha256": "5" * 64,
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
        evidence_content = "target repository evidence\n"
        evidence_sha256 = hashlib.sha256(evidence_content.encode()).hexdigest()
        self.write_json(
            root,
            "repository-evidence.json",
            {
                "schema_version": 1,
                "status": "complete",
                "target": {"name": "repository", "base_sha": "a" * 40},
                "scan_limits": {},
                "files": [
                    {
                        "path": "source/README.md",
                        "bytes": len(evidence_content.encode()),
                        "lines": len(evidence_content.splitlines()),
                        "sha256": evidence_sha256,
                        "content": evidence_content,
                    }
                ],
                "facts": [
                    {
                        "fact_id": "file:README.md",
                        "kind": "repository-file",
                        "path": "source/README.md",
                        "evidence_sha256": evidence_sha256,
                    }
                ],
                "warnings": [],
            },
        )
        markdown_claims: list[dict[str, object]] = []
        if readme is not None:
            readme_text = (root / readme["path"]).read_text(encoding="utf-8")
            markdown_claims = [
                {
                    "claim_id": f"markdown:en:{index:03d}",
                    "content_sha256": hashlib.sha256(block.encode()).hexdigest(),
                    "claim_kind": "factual",
                    "evidence_sha256": evidence_sha256,
                    "truth_id": "file:README.md",
                    "language_pair_id": None,
                }
                for index, block in enumerate(_CORE.segment_markdown_blocks(readme_text))
            ]
        diagram_claims: list[dict[str, object]] = []
        if elk and semantic_value is not None:
            labels = [
                (
                    semantic_value["accessibility_claim_id"],
                    semantic_value["accessibility_title"],
                ),
                *[
                    (item["claim_id"], item["label"])
                    for item in semantic_value["groups"]
                ],
                *[
                    (item["claim_id"], item["label"])
                    for item in semantic_value["nodes"]
                ],
                *[
                    (item["claim_id"], item["label"])
                    for item in semantic_value["edges"]
                    if item["label"] is not None
                ],
            ]
            diagram_claims = [
                {
                    "claim_id": claim_id,
                    "content_sha256": hashlib.sha256(label.encode()).hexdigest(),
                    "claim_kind": "factual",
                    "evidence_sha256": evidence_sha256,
                    "truth_id": "file:README.md",
                    "language_pair_id": None,
                }
                for claim_id, label in labels
            ]
        claims = self.write_json(
            root,
            "claim-map.json",
            {
                "schema_version": 1,
                "markdown_blocks": sorted(
                    markdown_claims,
                    key=lambda item: str(item["claim_id"]),
                ),
                "diagram_labels": sorted(
                    diagram_claims,
                    key=lambda item: str(item["claim_id"]),
                ),
            },
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
        if asset["engine_kind"] == "elk":
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
            for mode, elk in (
                ("readme", False),
                ("asset-only", True),
                ("audit-only", False),
            ):
                with self.subTest(mode=mode):
                    root = base / mode
                    root.mkdir()
                    bundle, _ = self.make_bundle(root, mode, elk=elk)
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

    def test_artifact_read_is_bounded_before_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "readme")
            oversized = b"# Demo\n" + b"x" * 64
            bundle["candidate"]["readme"] = self.write_bytes(
                root,
                "README.generated.md",
                oversized,
            )

            with mock.patch.object(_CORE, "MAX_ARTIFACT_BYTES", 32):
                self.assert_code(root, bundle, "E_BUNDLE_SIZE")

    def test_traversal_missing_pair_and_engine_mismatch_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
            bundle["candidate"]["assets"][0]["path"] = "../diagram.svg"
            self.assert_code(root, bundle, "E_PATH")

            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            del manifest["assets"][0]["semantic"]
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_SCHEMA_MISSING_FIELD")

            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
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

            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
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

    def test_generic_svg_safety_and_elk_visible_text_are_hard_gates(self) -> None:
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

            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
            semantic = json.loads(
                (
                    root
                    / "assets/readme/diagram.diagram.json"
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

            bundle, _ = self.make_bundle(root, "asset-only", elk=True)
            manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            fallback = manifest["assets"][0]["fallback"]
            (root / fallback["path"]).write_bytes(unsafe)
            fallback["sha256"] = __import__("hashlib").sha256(unsafe).hexdigest()
            write_canonical_json_atomic(manifest_path, manifest)
            bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
            self.assert_code(root, bundle, "E_SVG_UNSAFE")

    def test_compiled_asset_manifest_v3_closes_inventory_and_truthful_asset_rows(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, candidates, evidence = self.make_compiled_asset_manifest(root)
            normalized = validate_asset_manifest(
                manifest,
                evidence_graph=evidence,
                artifact_root=root,
                candidate_assets=candidates,
            )
            self.assertEqual(normalized, manifest)
            self.assertEqual(
                canonical_asset_manifest_bytes(
                    manifest,
                    evidence_graph=evidence,
                    artifact_root=root,
                    candidate_assets=candidates,
                ),
                canonical_json_bytes(manifest),
            )
            self.assertEqual(len(normalized["compiled"]["svgs"]), 4)
            self.assertEqual(len(normalized["assets"]), 4)

    def test_compiled_asset_manifest_v3_rejects_inventory_provenance_and_candidate_drift(self) -> None:
        cases = (
            ("stale-scene-ref", "E_VISUAL_FINGERPRINT"),
            ("missing-gate-ref", "E_VISUAL_FINGERPRINT"),
            ("duplicate-scene-ref", "E_VISUAL_FINGERPRINT"),
            ("identity-drift", "E_VISUAL_FINGERPRINT"),
            ("artifact-byte-drift", "E_BUNDLE_HASH"),
            ("evidence-drift", "E_CLAIM_EVIDENCE"),
            ("candidate-drift", "E_BUNDLE_HASH"),
            ("wrong-role", "E_BUNDLE_ASSET"),
        )
        for case, expected_code in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                manifest, candidates, evidence = self.make_compiled_asset_manifest(root)
                candidate = copy.deepcopy(manifest)
                candidate_assets = copy.deepcopy(candidates)
                if case == "stale-scene-ref":
                    candidate["compiled"]["scenes"][0]["sha256"] = "0" * 64
                elif case == "missing-gate-ref":
                    candidate["compiled"]["gates"].pop()
                elif case == "duplicate-scene-ref":
                    candidate["compiled"]["scenes"].append(copy.deepcopy(candidate["compiled"]["scenes"][0]))
                elif case == "identity-drift":
                    candidate["compiled"]["identities"]["renderer"] = "1" * 64
                elif case == "artifact-byte-drift":
                    (root / candidate["compiled"]["svgs"][0]["path"]).write_bytes(b"tampered")
                elif case == "evidence-drift":
                    candidate["assets"][0]["evidence_ids"] = ["file:" + "f" * 64]
                elif case == "candidate-drift":
                    candidate_assets[0]["sha256"] = "0" * 64
                elif case == "wrong-role":
                    candidate["assets"][0]["role"] = "hero"
                with self.assertRaises(ContractError) as raised:
                    validate_asset_manifest(
                        candidate,
                        evidence_graph=evidence,
                        artifact_root=root,
                        candidate_assets=candidate_assets,
                    )
                self.assertEqual(raised.exception.code, expected_code)

    def test_asset_manifest_v3_rejects_symlink_and_extra_compiled_ref(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, candidates, evidence = self.make_compiled_asset_manifest(root)
            outside = root.parent / "asset-manifest-v3-outside"
            outside.write_bytes(b"outside")
            scene_path = root / manifest["compiled"]["scenes"][0]["path"]
            scene_path.unlink()
            scene_path.symlink_to(outside)
            with self.assertRaises(ContractError) as raised:
                validate_asset_manifest(
                    manifest,
                    evidence_graph=evidence,
                    artifact_root=root,
                    candidate_assets=candidates,
                )
            self.assertEqual(raised.exception.code, "E_PATH")
            outside.unlink()

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest, candidates, evidence = self.make_compiled_asset_manifest(root)
            extra = {
                "locale": "zh-Hant",
                "variant": "desktop",
                "path": "compiled/scenes/zh-Hant/desktop.json",
                "sha256": "0" * 64,
            }
            manifest["compiled"]["scenes"].append(extra)
            manifest["compiled"]["scenes"].sort(
                key=lambda item: (item["locale"].encode("utf-8"), item["variant"].encode("utf-8"))
            )
            with self.assertRaises(ContractError) as raised:
                validate_asset_manifest(
                    manifest,
                    evidence_graph=evidence,
                    artifact_root=root,
                    candidate_assets=candidates,
                )
            self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_legacy_asset_manifest_hashes_and_v1_adapter_remain_unchanged(self) -> None:
        valid_fixture = REPO_ROOT / "tests/fixtures/contracts/asset-manifest-v2.valid.json"
        invalid_fixture = REPO_ROOT / "tests/fixtures/contracts/asset-manifest-v2.invalid.json"
        self.assertEqual(
            hashlib.sha256(valid_fixture.read_bytes()).hexdigest(),
            "f6e3cac29897085f0541420bf66d54aadaa8df509e90841af185fe52b5c244ae",
        )
        self.assertEqual(
            hashlib.sha256(invalid_fixture.read_bytes()).hexdigest(),
            "75f7b4c3439942e0b4c53fb70367c21e1af065d0da72e394acbd59bd381c1f0c",
        )
        legacy = {"schema_version": 1, "assets": []}
        self.assertEqual(read_asset_manifest(legacy), legacy)

    def test_legacy_claim_asset_bundle_canonical_bytes_and_default_producers_are_pinned(self) -> None:
        fixture_hashes = {
            "claim-map-v2.valid.json": "8a8e46f5eb19ebce7934d3320d9a496c5ad3c36884335710abcfdffacb02cc4d",
            "claim-map-v2.invalid.json": "1d5fbf165d55603d879791918ea6b2990c499382c26dddabdb96b0216c0699c5",
            "asset-manifest-v2.valid.json": "f6e3cac29897085f0541420bf66d54aadaa8df509e90841af185fe52b5c244ae",
            "asset-manifest-v2.invalid.json": "75f7b4c3439942e0b4c53fb70367c21e1af065d0da72e394acbd59bd381c1f0c",
            "generated-bundle-v2.valid.json": "a0504bf5023b732464e4dc999665e4a3a264a7fd99e3c3b0ad0dc9ccca4788bb",
            "generated-bundle-v2.invalid.json": "9eb5c7b9a129a8ddfcb955a9ed85b7bba749829c82fe4b31c094983426f38791",
        }
        for name, expected in fixture_hashes.items():
            with self.subTest(fixture=name):
                raw = (REPO_ROOT / "tests/fixtures/contracts" / name).read_bytes()
                self.assertEqual(hashlib.sha256(raw).hexdigest(), expected)

        evidence_token = b"file:" + b"a" * 64
        claim_fixture = (
            REPO_ROOT / "tests/fixtures/contracts/claim-map-v2.valid.json"
        ).read_bytes()
        claim_v2 = json.loads(claim_fixture)
        claim_v2["markdown_blocks"][0]["evidence_ids"] = [evidence_token.decode()]
        claim_v2_bytes = canonical_claim_map_bytes(claim_v2)
        self.assertEqual(claim_v2_bytes, claim_fixture.replace(b"FACT_ID", evidence_token))
        self.assertEqual(
            hashlib.sha256(claim_v2_bytes).hexdigest(),
            "442207910324c8532c454ce8e226bf01dad16cacf104d14fb25e240ab96289d0",
        )

        legacy_claim = {
            "schema_version": 1,
            "markdown_blocks": [
                {
                    "claim_id": "markdown:en:overview",
                    "content_sha256": "d4b1ea5708dd532930a85188b45aff6f0a3ed458500c7577e0127a538eb0d100",
                    "claim_kind": "factual",
                    "truth_id": "file:README.md",
                    "evidence_sha256": "b" * 64,
                    "language_pair_id": None,
                }
            ],
            "diagram_labels": [],
        }
        legacy_claim_bytes = canonical_json_bytes(legacy_claim)
        self.assertEqual(
            legacy_claim_bytes,
            (
                '{"diagram_labels":[],"markdown_blocks":[{"claim_id":"markdown:en:overview",'
                '"claim_kind":"factual","content_sha256":"d4b1ea5708dd532930a85188b45aff6f0a3ed458500c7577e0127a538eb0d100",'
                '"evidence_sha256":"' + "b" * 64 + '","language_pair_id":null,"truth_id":"file:README.md"}],'
                '"schema_version":1}\n'
            ).encode("utf-8"),
        )
        self.assertEqual(
            hashlib.sha256(legacy_claim_bytes).hexdigest(),
            "f912db0de2f0e7dad37e9df9fa959bc5efb5001d78a6b7292e1c2350cca66ee1",
        )
        adapted_claim = adapt_v1_claim_map(legacy_claim)
        self.assertEqual(adapted_claim["schema_version"], CLAIM_MAP_SCHEMA_VERSION)
        self.assertEqual(
            hashlib.sha256(canonical_json_bytes(adapted_claim)).hexdigest(),
            "64b7ebdd3cabc71003caaa8f2d1bd4a26005c0cc876e3b28f345ae483b799f54",
        )

        asset_fixture = (
            REPO_ROOT / "tests/fixtures/contracts/asset-manifest-v2.valid.json"
        ).read_bytes()
        asset_v2 = json.loads(asset_fixture)
        for asset in asset_v2["assets"]:
            asset["evidence_ids"] = [evidence_token.decode()]
        asset_v2_bytes = canonical_asset_manifest_bytes(asset_v2)
        self.assertEqual(asset_v2_bytes, asset_fixture.replace(b"FACT_ID", evidence_token))
        self.assertEqual(
            hashlib.sha256(asset_v2_bytes).hexdigest(),
            "193f6975f21e337ce2e9f8935012703be6c3e0682685e8ffa1cd2a6c213b704e",
        )

        legacy_asset = {"schema_version": 1, "assets": []}
        legacy_asset_bytes = canonical_json_bytes(legacy_asset)
        self.assertEqual(legacy_asset_bytes, b'{"assets":[],"schema_version":1}\n')
        self.assertEqual(
            hashlib.sha256(legacy_asset_bytes).hexdigest(),
            "e246f5b102ee86fa516321a3b2e90ed018a0a94dbd991092347f2f245882a6ac",
        )
        self.assertEqual(read_asset_manifest(legacy_asset), legacy_asset)

        bundle_v2_path = REPO_ROOT / "tests/fixtures/contracts/generated-bundle-v2.valid.json"
        bundle_v2_bytes = bundle_v2_path.read_bytes()
        self.assertEqual(bundle_v2_bytes, canonical_json_bytes(json.loads(bundle_v2_bytes)))
        self.assertEqual(
            hashlib.sha256(bundle_v2_bytes).hexdigest(),
            "a0504bf5023b732464e4dc999665e4a3a264a7fd99e3c3b0ad0dc9ccca4788bb",
        )

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle_v1, bundle_path = self.make_bundle(root, "readme")
            bundle_v1_bytes = canonical_json_bytes(bundle_v1)
            self.assertEqual(bundle_path.read_bytes(), bundle_v1_bytes)
            self.assertEqual(
                hashlib.sha256(bundle_v1_bytes).hexdigest(),
                "10d639772ef352aa06707768cff9585fe17564ce1821932f21d3e61d8c3cf8aa",
            )
            self.assertEqual(validate_generated_bundle(bundle_v1, root)["status"], "pass")

        # Legacy default producers remain v2; v3 is opt-in and must not become
        # the implicit output version as the compiled route lands.
        self.assertEqual(README_PLAN_V2_SCHEMA_VERSION, 2)
        self.assertEqual(CLAIM_MAP_SCHEMA_VERSION, 2)
        self.assertEqual(ASSET_MANIFEST_SCHEMA_VERSION, 2)
        self.assertEqual(GENERATED_BUNDLE_SCHEMA_VERSION, 2)


if __name__ == "__main__":
    unittest.main()
