from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256
from skill.scripts.readme_showcase.contracts.assets import validate_asset_manifest_v3
from skill.scripts.readme_showcase.generation.assembler import (
    assemble_generated_bundle_v3,
    canonical_markdown_blocks,
    validate_generated_bundle_v3,
)
from skill.scripts.readme_showcase.visual_kernel.artifacts import build_compiled_artifacts
from skill.scripts.readme_showcase.visual_kernel.diagnostics import VisualGateReport
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec
from tests.unit.visual_kernel.test_artifacts import forge_authoritative_svg_attempt


class BundleV3ContractTests(unittest.TestCase):
    """Contract and provenance tests for the opt-in compiled bundle route."""

    @staticmethod
    def _write_bytes(root: Path, path: str, value: bytes) -> dict[str, str]:
        destination = root.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(value)
        return {"path": path, "sha256": hashlib.sha256(value).hexdigest()}

    @staticmethod
    def _write_json(root: Path, path: str, value: object) -> dict[str, str]:
        raw = canonical_json_bytes(value)
        destination = root.joinpath(*path.split("/"))
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(raw)
        return {"path": path, "sha256": hashlib.sha256(raw).hexdigest()}

    def _compiled(self, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = _spec("flow")
        normalized = normalize_visual_spec(payload, EVIDENCE)
        theme = resolve_theme()
        timeline = derive_timeline(normalized)
        interaction_payload = copy.deepcopy(payload)
        interaction_payload["edges"] = interaction_payload["edges"][:1]  # type: ignore[index]
        interaction = derive_interaction(normalize_visual_spec(interaction_payload, EVIDENCE))
        spec_hash = hashlib.sha256(validate_visual_spec(payload, evidence_graph=EVIDENCE).canonical_bytes()).hexdigest()
        identities = {
            name: hashlib.sha256(name.encode("utf-8")).hexdigest()
            for name in ("kernel", "elk", "renderer")
        }
        records: list[dict[str, Any]] = []
        # Task 25 emits one Visual Spec locale with its declared variants;
        # README Plan v3 may still carry multiple localized READMEs.
        for locale in ("en",):
            for variant in ("desktop", "mobile"):
                scene = replace(_build("flow", variant), locale=locale)
                svg = serialize_svg(scene, theme)
                gate = VisualGateReport.build(
                    spec_hash,
                    hashlib.sha256(scene.canonical_bytes()).hexdigest(),
                    hashlib.sha256(svg).hexdigest(),
                )
                records.append({
                    "locale": locale,
                    "variant": variant,
                    "scene": scene,
                    "svg": svg,
                    "gate": gate,
                    "timeline": timeline,
                    "interaction": interaction,
                })
        generated = dict(build_compiled_artifacts(payload, theme, records, identities, evidence_graph=EVIDENCE))
        for path, raw in generated.items():
            self._write_bytes(root, path, raw)
        inventory = json.loads(generated["compiled/inventory.json"])
        layers = {layer["name"]: layer for layer in inventory["layers"]}
        artifact_hashes = {record["path"]: record["sha256"] for record in layers["artifacts"]["records"]}

        def single(path: str) -> dict[str, str]:
            return {"path": path, "sha256": artifact_hashes[path]}

        def variants(layer: str, pattern: str) -> list[dict[str, str]]:
            return [
                {
                    "locale": record["locale"],
                    "variant": record["variant"],
                    "path": pattern.format(locale=record["locale"], variant=record["variant"]),
                    "sha256": artifact_hashes[pattern.format(locale=record["locale"], variant=record["variant"])],
                }
                for record in layers[layer]["records"]
            ]

        compiled = {
            "spec": single("compiled/visual-spec.json"),
            "theme": single("compiled/theme.json"),
            "inventory": {
                "path": "compiled/inventory.json",
                "sha256": hashlib.sha256(generated["compiled/inventory.json"]).hexdigest(),
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
        manifest_assets = []
        for svg in compiled["svgs"]:
            key = (svg["locale"], svg["variant"])
            scene = scene_by_key[key]
            gate = gate_by_key[key]
            manifest_assets.append({
                "asset_id": f"diagram-{svg['locale']}-{svg['variant']}",
                "path": svg["path"],
                "artifact_sha256": svg["sha256"],
                "evidence_ids": [EVIDENCE["facts"][0]["fact_id"]],
                "role": "diagram",
                "locale": svg["locale"],
                "variant": svg["variant"],
                "scene_sha256": scene["sha256"],
                "gate_sha256": gate["sha256"],
                "provenance": {"kind": "generated", "path": scene["path"], "sha256": scene["sha256"]},
            })
        return {"schema_version": 3, "assets": manifest_assets, "compiled": compiled}, payload

    def _claim_map(self, spec_payload: dict[str, Any], readmes: dict[str, bytes]) -> dict[str, Any]:
        spec = validate_visual_spec(spec_payload, evidence_graph=EVIDENCE)
        evidence_id = EVIDENCE["facts"][0]["fact_id"]
        markdown: list[dict[str, Any]] = []
        for locale, raw in readmes.items():
            blocks = canonical_markdown_blocks(raw)
            for ordinal, block_raw in enumerate(blocks):
                name = "overview" if ordinal == 0 else "details"
                markdown.append({
                    "claim_id": f"markdown:{locale}:{name}",
                    # Claim Map collections are claim-id sorted; the bundle
                    # contract binds each locale's sorted claim ordinal to
                    # the corresponding canonical README block.
                    "content_sha256": "0" * 64,
                    "claim_kind": "factual",
                    "evidence_ids": [evidence_id],
                    "language_pair_id": name,
                    "support_level": "direct",
                })
        for locale in readmes:
            locale_claims = sorted((
                claim for claim in markdown
                if claim["claim_id"].split(":", 2)[1] == locale
            ), key=lambda claim: claim["claim_id"])
            for claim, block in zip(locale_claims, canonical_markdown_blocks(readmes[locale]), strict=True):
                claim["content_sha256"] = hashlib.sha256(block).hexdigest()
        labels: list[dict[str, Any]] = []
        for collection in (spec.nodes, spec.edges, spec.groups, spec.lanes):
            for element in collection:
                if element.label is None:
                    continue
                labels.append({
                    "claim_id": f"diagram:{spec.locale}:{element.id}",
                    "content_sha256": hashlib.sha256(element.label.encode("utf-8")).hexdigest(),
                    "claim_kind": "factual",
                    "evidence_ids": list(element.evidence_ids),
                    "language_pair_id": None,
                    "support_level": "direct",
                    "element_id": element.id,
                })
        return {
            "schema_version": 3,
            "markdown_blocks": sorted(markdown, key=lambda item: item["claim_id"]),
            "diagram_labels": sorted(labels, key=lambda item: item["claim_id"]),
        }

    def make_bundle(self, root: Path) -> dict[str, Any]:
        manifest, spec_payload = self._compiled(root)
        readmes = {"en": b"# Overview\n\nDetails\n", "zh-Hans": "# 概览\n\n详情\n".encode()}
        for path, raw in (("README.md", readmes["en"]), ("README_zh.md", readmes["zh-Hans"])):
            self._write_bytes(root, path, raw)
        plan = {
            "schema_version": 3,
            "mode": "readme",
            "locales": [
                {"tag": "en", "readme_path": "README.md"},
                {"tag": "zh-Hans", "readme_path": "README_zh.md"},
            ],
            "sections": ["overview"],
            "visual_intent": "project structure",
            "diagram_route": "compiled",
            "commands": [],
            "evidence_ids": [fact["fact_id"] for fact in EVIDENCE["facts"]],
        }
        retrieval = {"schema_version": 1, "status": "unavailable", "records": []}
        claims = self._claim_map(spec_payload, readmes)
        self._write_json(root, "readme-plan.json", plan)
        self._write_json(root, "retrieval-packet.json", retrieval)
        self._write_json(root, "repository-evidence.json", EVIDENCE)
        self._write_json(root, "claim-map.json", claims)
        self._write_json(root, "visual-spec.json", spec_payload)
        self._write_json(root, "asset-manifest.json", manifest)
        candidate = {
            "readmes": [
                {"path": "README.md", "sha256": hashlib.sha256(readmes["en"]).hexdigest()},
                {"path": "README_zh.md", "sha256": hashlib.sha256(readmes["zh-Hans"]).hexdigest()},
            ],
            "assets": [
                {"path": asset["path"], "sha256": asset["artifact_sha256"]}
                for asset in manifest["assets"]
            ],
        }
        artifacts = {
            name: {
                "path": path,
                "sha256": hashlib.sha256((root / path).read_bytes()).hexdigest(),
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
        compiled = {
            "inventory": manifest["compiled"]["inventory"],
            "fingerprint": json.loads((root / "compiled/inventory.json").read_bytes())["inventory_sha256"],
            "retention": "manual",
        }
        return assemble_generated_bundle_v3(
            root,
            mode="readme",
            target={"repository": "owner/repo", "base_sha": "a" * 40},
            candidate=candidate,
            artifacts=artifacts,
            compiled=compiled,
        )

    @staticmethod
    def assert_code(test: unittest.TestCase, code: str, payload: Any, root: Path) -> None:
        with test.assertRaises(ContractError) as raised:
            validate_generated_bundle_v3(payload, root)
        test.assertEqual(raised.exception.code, code)

    def test_two_locales_two_variants_and_repeatable_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_bundle(root)
            second = assemble_generated_bundle_v3(
                root,
                mode=first["mode"],
                target=first["target"],
                candidate={key: first["candidate"][key] for key in ("readmes", "assets")},
                artifacts=first["artifacts"],
                compiled=first["compiled"],
            )
            self.assertEqual(first, second)
            report = validate_generated_bundle_v3(first, root)
            self.assertEqual(report["status"], "pass")
            self.assertEqual(report["candidate_count"], 4)
            self.assertEqual(first["compiled"]["retention"], "manual")

    def test_forged_self_consistent_svg_inventory_fails_manifest_and_bundle_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            manifest = json.loads((root / "asset-manifest.json").read_bytes())
            forge_authoritative_svg_attempt(root, manifest, bundle)

            with self.assertRaises(ContractError) as asset_rejected:
                validate_asset_manifest_v3(manifest, artifact_root=root)
            self.assertEqual(asset_rejected.exception.code, "E_VISUAL_SVG_SECURITY")
            with self.assertRaises(ContractError) as bundle_rejected:
                validate_generated_bundle_v3(bundle, root)
            self.assertEqual(bundle_rejected.exception.code, "E_VISUAL_SVG_SECURITY")

    def test_mode_projection_keeps_internal_compiled_validation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)

            # asset-only publishes the exact SVG set but no README targets;
            # markdown claims remain author-stage data and are not promoted.
            plan = json.loads((root / "readme-plan.json").read_bytes())
            plan["mode"] = "asset-only"
            self._write_json(root, "readme-plan.json", plan)
            claims = json.loads((root / "claim-map.json").read_bytes())
            claims["markdown_blocks"] = []
            self._write_json(root, "claim-map.json", claims)
            bundle["mode"] = "asset-only"
            bundle["candidate"]["readmes"] = []
            bundle["artifacts"]["plan"]["sha256"] = hashlib.sha256(
                (root / "readme-plan.json").read_bytes()
            ).hexdigest()
            bundle["artifacts"]["claim_map"]["sha256"] = hashlib.sha256(
                (root / "claim-map.json").read_bytes()
            ).hexdigest()
            body = {"readmes": [], "assets": bundle["candidate"]["assets"]}
            bundle["candidate"]["candidate_sha256"] = canonical_sha256(body)
            asset_report = validate_generated_bundle_v3(bundle, root)
            self.assertEqual(asset_report["status"], "pass")
            self.assertEqual(asset_report["candidate_count"], 2)

            # audit-only promotes neither README nor SVG, while the same
            # compiled inventory, manifest, and gate closure remains required.
            plan["mode"] = "audit-only"
            self._write_json(root, "readme-plan.json", plan)
            bundle["mode"] = "audit-only"
            bundle["candidate"]["assets"] = []
            bundle["artifacts"]["plan"]["sha256"] = hashlib.sha256(
                (root / "readme-plan.json").read_bytes()
            ).hexdigest()
            bundle["candidate"]["candidate_sha256"] = canonical_sha256(
                {"readmes": [], "assets": []}
            )
            audit_report = validate_generated_bundle_v3(bundle, root)
            self.assertEqual(audit_report["status"], "pass")
            self.assertEqual(audit_report["candidate_count"], 0)

    def test_stage_origin_swaps_and_internal_spec_candidate_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            swapped = copy.deepcopy(bundle)
            swapped["candidate"]["assets"].append(swapped["artifacts"]["visual_spec"])
            body = {"readmes": swapped["candidate"]["readmes"], "assets": swapped["candidate"]["assets"]}
            swapped["candidate"]["candidate_sha256"] = canonical_sha256(body)
            self.assert_code(self, "E_BUNDLE_ASSET", swapped, root)

            unsafe = copy.deepcopy(bundle)
            unsafe["candidate"]["assets"][0]["path"] = "../outside.svg"
            body = {"readmes": unsafe["candidate"]["readmes"], "assets": unsafe["candidate"]["assets"]}
            unsafe["candidate"]["candidate_sha256"] = canonical_sha256(body)
            self.assert_code(self, "E_PATH", unsafe, root)

            swapped = copy.deepcopy(bundle)
            swapped["artifacts"]["visual_spec"] = swapped["artifacts"]["asset_manifest"]
            self.assert_code(self, "E_VISUAL_PATH", swapped, root)

            swapped = copy.deepcopy(bundle)
            swapped["artifacts"]["evaluation"] = {"path": "evaluation.json", "sha256": "0" * 64}
            self.assert_code(self, "E_SCHEMA_UNKNOWN_FIELD", swapped, root)

    def test_stale_fingerprint_missing_gate_extra_asset_and_locale_drift_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)

            stale = copy.deepcopy(bundle)
            stale["compiled"]["fingerprint"] = "0" * 64
            self.assert_code(self, "E_VISUAL_FINGERPRINT", stale, root)

            missing_gate = copy.deepcopy(bundle)
            manifest = json.loads((root / "asset-manifest.json").read_bytes())
            manifest["compiled"]["gates"].pop()
            self._write_json(root, "asset-manifest.json", manifest)
            missing_gate["artifacts"]["asset_manifest"]["sha256"] = hashlib.sha256((root / "asset-manifest.json").read_bytes()).hexdigest()
            self.assert_code(self, "E_VISUAL_FINGERPRINT", missing_gate, root)

            root_missing_scene = Path(temporary) / "missing-scene"
            root_missing_scene.mkdir()
            missing_scene = self.make_bundle(root_missing_scene)
            scene_manifest = json.loads((root_missing_scene / "asset-manifest.json").read_bytes())
            scene_manifest["compiled"]["scenes"].pop()
            self._write_json(root_missing_scene, "asset-manifest.json", scene_manifest)
            missing_scene["artifacts"]["asset_manifest"]["sha256"] = hashlib.sha256(
                (root_missing_scene / "asset-manifest.json").read_bytes()
            ).hexdigest()
            self.assert_code(self, "E_VISUAL_FINGERPRINT", missing_scene, root_missing_scene)

            root2 = Path(temporary) / "extra"
            root2.mkdir()
            fresh = self.make_bundle(root2)
            extra = copy.deepcopy(fresh)
            extra_asset_path = "assets/readme-showcase/zh-Hans/desktop.svg"
            self._write_bytes(
                root2,
                extra_asset_path,
                (root2 / "assets/readme-showcase/en/desktop.svg").read_bytes(),
            )
            extra["candidate"]["assets"].append({
                "path": extra_asset_path,
                "sha256": hashlib.sha256((root2 / extra_asset_path).read_bytes()).hexdigest(),
            })
            body = {"readmes": extra["candidate"]["readmes"], "assets": extra["candidate"]["assets"]}
            extra["candidate"]["candidate_sha256"] = canonical_sha256(body)
            self.assert_code(self, "E_BUNDLE_ASSET", extra, root2)

            locale = copy.deepcopy(fresh)
            locale["candidate"]["readmes"].reverse()
            body = {"readmes": locale["candidate"]["readmes"], "assets": locale["candidate"]["assets"]}
            locale["candidate"]["candidate_sha256"] = canonical_sha256(body)
            self.assert_code(self, "E_CLAIM_LANGUAGE", locale, root2)

    def test_secondary_readme_drift_and_symlink_fail_without_acceptance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            (root / "README_zh.md").write_bytes("# 漂移\n\n详情\n".encode())
            self.assert_code(self, "E_BUNDLE_HASH", bundle, root)

            root2 = Path(temporary) / "symlink"
            root2.mkdir()
            linked = self.make_bundle(root2)
            outside = Path(temporary) / "outside.md"
            outside.write_bytes((root2 / "README.md").read_bytes())
            (root2 / "README.md").unlink()
            (root2 / "README.md").symlink_to(outside)
            self.assert_code(self, "E_PATH", linked, root2)


if __name__ == "__main__":
    unittest.main()
