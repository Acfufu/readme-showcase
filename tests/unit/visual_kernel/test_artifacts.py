from __future__ import annotations

import hashlib
import json
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.orchestration import workspace as workspace_module
from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace
from skill.scripts.readme_showcase.visual_kernel import artifacts as artifacts_module
from skill.scripts.readme_showcase.visual_kernel.artifacts import (
    build_compiled_artifacts,
    promote_compiled_artifacts,
)
from skill.scripts.readme_showcase.visual_kernel.diagnostics import VisualGateReport
from skill.scripts.readme_showcase.visual_kernel.fingerprint import build_layered_fingerprint
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec


IDENTITIES = {name: hashlib.sha256(name.encode("utf-8")).hexdigest() for name in ("kernel", "elk", "renderer")}


def forge_self_consistent_svg_inventory(artifacts: dict[str, bytes]) -> dict[str, bytes]:
    """Replace one SVG with active content and consistently re-sign every projection."""

    forged = dict(artifacts)
    inventory = json.loads(forged["compiled/inventory.json"])
    layers = inventory["layers"]
    svg_record = next(
        item for item in layers[7]["records"]
        if item["path"].startswith("assets/readme-showcase/")
    )
    svg_path = svg_record["path"]
    parts = svg_path.split("/")
    locale, variant = parts[2], parts[3][:-4]
    forged[svg_path] = forged[svg_path].replace(b"<svg ", b'<svg onload="alert(1)" ', 1)
    svg_sha256 = hashlib.sha256(forged[svg_path]).hexdigest()

    gate_path = f"compiled/gates/{locale}/{variant}.json"
    gate = json.loads(forged[gate_path])
    gate["svg_sha256"] = svg_sha256
    forged[gate_path] = canonical_json_bytes(gate)
    gate_sha256 = hashlib.sha256(forged[gate_path]).hexdigest()
    gate_record = next(
        item for item in layers[4]["records"]
        if (item["locale"], item["variant"]) == (locale, variant)
    )
    gate_record["sha256"] = gate_sha256
    next(
        item for item in layers[5]["records"]
        if (item["locale"], item["variant"]) == (locale, variant)
    )["prior_sha256"] = gate_sha256

    report_prior = hashlib.sha256(canonical_json_bytes({
        "gates": layers[4]["records"],
        "timelines": layers[5]["records"],
        "interactions": layers[6]["records"],
    })).hexdigest()
    for record in layers[7]["records"]:
        record["prior_sha256"] = report_prior
        record["sha256"] = hashlib.sha256(forged[record["path"]]).hexdigest()
    rebuilt = build_layered_fingerprint(
        layers[0]["sha256"],
        layers[1]["records"],
        layers[2]["sha256"],
        layers[3]["values"],
        layers[4]["records"],
        layers[5]["records"],
        layers[6]["records"],
        layers[7]["records"],
    )
    forged["compiled/inventory.json"] = rebuilt.canonical_bytes()
    return forged


def forge_authoritative_svg_attempt(
    root: Path,
    manifest: dict[str, object],
    bundle: dict[str, object],
) -> dict[str, bytes]:
    inventory = json.loads((root / "compiled/inventory.json").read_bytes())
    paths = [record["path"] for record in inventory["layers"][-1]["records"]]
    paths.append("compiled/inventory.json")
    forged = forge_self_consistent_svg_inventory({path: (root / path).read_bytes() for path in paths})
    for path, raw in forged.items():
        (root / path).write_bytes(raw)

    compiled = manifest["compiled"]
    assert isinstance(compiled, dict)
    for name in ("spec", "theme", "inventory"):
        ref = compiled[name]
        assert isinstance(ref, dict)
        ref["sha256"] = hashlib.sha256(forged[ref["path"]]).hexdigest()
    for name in ("scenes", "gates", "timelines", "interactions", "svgs"):
        refs = compiled[name]
        assert isinstance(refs, list)
        for ref in refs:
            ref["sha256"] = hashlib.sha256(forged[ref["path"]]).hexdigest()
    assets = manifest["assets"]
    assert isinstance(assets, list)
    for asset in assets:
        key = (asset["locale"], asset["variant"])
        asset["artifact_sha256"] = next(
            ref["sha256"] for ref in compiled["svgs"]
            if (ref["locale"], ref["variant"]) == key
        )
        asset["gate_sha256"] = next(
            ref["sha256"] for ref in compiled["gates"]
            if (ref["locale"], ref["variant"]) == key
        )
    manifest_raw = canonical_json_bytes(manifest)
    (root / "asset-manifest.json").write_bytes(manifest_raw)

    bundle_compiled = bundle["compiled"]
    bundle_artifacts = bundle["artifacts"]
    assert isinstance(bundle_compiled, dict) and isinstance(bundle_artifacts, dict)
    bundle_compiled["inventory"] = copy.deepcopy(compiled["inventory"])
    bundle_compiled["fingerprint"] = json.loads(forged["compiled/inventory.json"])["inventory_sha256"]
    bundle_artifacts["asset_manifest"]["sha256"] = hashlib.sha256(manifest_raw).hexdigest()
    candidate = bundle.get("candidate")
    if isinstance(candidate, dict):
        candidate_assets = candidate.get("assets")
        if isinstance(candidate_assets, list):
            svg_hashes = {ref["path"]: ref["sha256"] for ref in compiled["svgs"]}
            for ref in candidate_assets:
                ref["sha256"] = svg_hashes[ref["path"]]
            candidate["candidate_sha256"] = hashlib.sha256(canonical_json_bytes({
                "readmes": candidate["readmes"],
                "assets": candidate_assets,
            })).hexdigest()
    return forged


class CompiledArtifactTests(unittest.TestCase):
    def _inputs(self) -> tuple[object, object, list[dict[str, object]]]:
        payload = _spec("flow")
        plan = normalize_visual_spec(payload, EVIDENCE)
        theme = resolve_theme()
        timeline = derive_timeline(plan)
        interaction_payload = copy.deepcopy(payload)
        interaction_payload["edges"] = interaction_payload["edges"][:1]  # type: ignore[index]
        interaction = derive_interaction(normalize_visual_spec(interaction_payload, EVIDENCE))
        spec_sha256 = hashlib.sha256(validate_visual_spec(payload, evidence_graph=EVIDENCE).canonical_bytes()).hexdigest()
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
        return payload, theme, records

    def test_happy_build_is_immutable_sorted_and_closes_inventory(self) -> None:
        spec, theme, records = self._inputs()
        first = build_compiled_artifacts(spec, theme, records, IDENTITIES, evidence_graph=EVIDENCE)
        second = build_compiled_artifacts(spec, theme, list(reversed(records)), dict(reversed(tuple(IDENTITIES.items()))), evidence_graph=EVIDENCE)
        self.assertEqual(first, second)
        self.assertEqual(list(first), sorted(first, key=lambda item: item.encode("utf-8")))
        with self.assertRaises(TypeError):
            first["new"] = b"bad"  # type: ignore[index]
        inventory = json.loads(first["compiled/inventory.json"])
        self.assertEqual(
            [layer["name"] for layer in inventory["layers"]],
            ["spec", "scenes", "theme", "identities", "gates", "timelines", "interactions", "artifacts"],
        )
        self.assertEqual(inventory["schema_version"], 1)
        self.assertEqual(len(inventory["inventory_sha256"]), 64)
        layers = inventory["layers"]
        reconstructed = build_layered_fingerprint(
            layers[0]["sha256"],
            layers[1]["records"],
            layers[2]["sha256"],
            layers[3]["values"],
            layers[4]["records"],
            layers[5]["records"],
            layers[6]["records"],
            layers[7]["records"],
        )
        self.assertEqual(reconstructed.canonical_bytes(), first["compiled/inventory.json"])
        self.assertEqual(inventory["inventory_sha256"], reconstructed.inventory_sha256)
        paths = [item["path"] for item in inventory["layers"][-1]["records"]]
        self.assertEqual(paths, sorted(paths, key=lambda item: item.encode("utf-8")))
        self.assertNotIn("compiled/inventory.json", paths)
        for item in inventory["layers"][-1]["records"]:
            data = first[item["path"]]
            self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(len(item["prior_sha256"]), 64)
        self.assertEqual(
            [path for path in first if path.startswith("assets/readme-showcase/")],
            [
                "assets/readme-showcase/en/desktop.svg",
                "assets/readme-showcase/en/mobile.svg",
                "assets/readme-showcase/zh-Hans/desktop.svg",
                "assets/readme-showcase/zh-Hans/mobile.svg",
            ],
        )

    def test_single_layer_identity_and_path_drift_changes_or_rejects_fingerprint(self) -> None:
        spec, theme, records = self._inputs()
        baseline = build_compiled_artifacts(spec, theme, records, IDENTITIES, evidence_graph=EVIDENCE)
        changed_identity = dict(IDENTITIES)
        changed_identity["renderer"] = hashlib.sha256(b"renderer-v2").hexdigest()
        changed = build_compiled_artifacts(spec, theme, records, changed_identity, evidence_graph=EVIDENCE)
        self.assertNotEqual(baseline["compiled/inventory.json"], changed["compiled/inventory.json"])

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            path_tampered = dict(baseline)
            path_inventory = json.loads(path_tampered["compiled/inventory.json"])
            path_inventory["layers"][-1]["records"][0]["path"] = "assets/readme-showcase/en/../desktop.svg"
            path_tampered["compiled/inventory.json"] = artifacts_module.canonical_json_bytes(path_inventory)
            with self.assertRaises(ContractError):
                promote_compiled_artifacts(workspace, path_tampered)

            layer_tampered = dict(baseline)
            layer_inventory = json.loads(layer_tampered["compiled/inventory.json"])
            layer_inventory["layers"][2]["sha256"] = hashlib.sha256(b"tampered-theme").hexdigest()
            projection = dict(layer_inventory)
            del projection["inventory_sha256"]
            layer_inventory["inventory_sha256"] = hashlib.sha256(artifacts_module.canonical_json_bytes(projection)).hexdigest()
            layer_tampered["compiled/inventory.json"] = artifacts_module.canonical_json_bytes(layer_inventory)
            with self.assertRaises(ContractError) as raised:
                promote_compiled_artifacts(workspace, layer_tampered)
            self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_self_consistent_inventory_cannot_omit_required_base_or_svg(self) -> None:
        spec, theme, records = self._inputs()
        baseline = build_compiled_artifacts(spec, theme, records[:2], IDENTITIES, evidence_graph=EVIDENCE)

        def without(path: str) -> dict[str, bytes]:
            inventory = json.loads(baseline["compiled/inventory.json"])
            layers = inventory["layers"]
            layers[-1]["records"] = [item for item in layers[-1]["records"] if item["path"] != path]
            rebuilt = build_layered_fingerprint(
                layers[0]["sha256"],
                layers[1]["records"],
                layers[2]["sha256"],
                layers[3]["values"],
                layers[4]["records"],
                layers[5]["records"],
                layers[6]["records"],
                layers[7]["records"],
            )
            result = dict(baseline)
            del result[path]
            result["compiled/inventory.json"] = rebuilt.canonical_bytes()
            return result

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            for path in ("compiled/visual-spec.json", "assets/readme-showcase/en/desktop.svg"):
                with self.subTest(path=path), self.assertRaises(ContractError) as raised:
                    promote_compiled_artifacts(workspace, without(path))
                self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_self_consistent_inventory_cannot_authorize_malicious_svg(self) -> None:
        spec, theme, records = self._inputs()
        baseline = dict(build_compiled_artifacts(spec, theme, records[:1], IDENTITIES, evidence_graph=EVIDENCE))
        forged = forge_self_consistent_svg_inventory(baseline)

        with self.assertRaises(ContractError) as direct:
            artifacts_module._preflight_files(forged, require_inventory=True)
        self.assertEqual(direct.exception.code, "E_VISUAL_SVG_SECURITY")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            with self.assertRaises(ContractError) as promoted:
                promote_compiled_artifacts(workspace, forged)
            self.assertEqual(promoted.exception.code, "E_VISUAL_SVG_SECURITY")
            self.assertFalse((workspace.root / "stages/06-bundle-assemble/attempts/1").exists())

    def test_closed_records_duplicate_and_path_drift_fail_before_promotion(self) -> None:
        spec, theme, records = self._inputs()
        duplicate = [*records, dict(records[0])]
        with self.assertRaises(ContractError) as raised:
            build_compiled_artifacts(spec, theme, duplicate, IDENTITIES, evidence_graph=EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")
        malformed = dict(records[0])
        malformed["extra"] = True
        with self.assertRaises(ContractError) as raised:
            build_compiled_artifacts(spec, theme, [malformed], IDENTITIES, evidence_graph=EVIDENCE)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")
        malformed_identity = dict(IDENTITIES)
        malformed_identity["kernel"] = "bad"
        with self.assertRaises(ContractError) as raised:
            build_compiled_artifacts(spec, theme, records, malformed_identity, evidence_graph=EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_promotion_uses_stage6_nested_attempt_and_never_target(self) -> None:
        spec, theme, records = self._inputs()
        artifacts = build_compiled_artifacts(spec, theme, records[:2], IDENTITIES, evidence_graph=EVIDENCE)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            caller_mapping = dict(artifacts)
            captured: dict[str, object] = {}
            append = workspace.append_attempt

            def mutate_after_preflight(stage_number: int, stage_name: str, files: object, *, attempt: int | None = None):
                captured["files"] = files
                caller_mapping["compiled/theme.json"] = b"caller mutation"
                return append(stage_number, stage_name, files, attempt=attempt)  # type: ignore[arg-type]

            with mock.patch.object(workspace, "append_attempt", side_effect=mutate_after_preflight):
                attempt = promote_compiled_artifacts(workspace, caller_mapping)
            self.assertEqual(attempt.parent.name, "attempts")
            self.assertTrue((attempt / "compiled/inventory.json").is_file())
            self.assertIsNot(captured["files"], caller_mapping)
            self.assertNotEqual((attempt / "compiled/theme.json").read_bytes(), b"caller mutation")
            self.assertEqual(list(target.iterdir()), [])

    def test_preflight_and_mid_write_fail_closed_without_replacing_current(self) -> None:
        spec, theme, records = self._inputs()
        artifacts = build_compiled_artifacts(spec, theme, records[:1], IDENTITIES, evidence_graph=EVIDENCE)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            first = promote_compiled_artifacts(workspace, artifacts)
            before = (first / "compiled/inventory.json").read_bytes()
            oversized = dict(artifacts)
            oversized["../escape"] = b"bad"
            with self.assertRaises(ContractError) as raised:
                promote_compiled_artifacts(workspace, oversized)
            self.assertEqual(raised.exception.code, "E_VISUAL_PATH")
            real_write = workspace_module.os.write
            writes = 0

            def fail_after_first(fd: int, data: bytes) -> int:
                nonlocal writes
                writes += 1
                if writes > 1:
                    raise OSError("forced mid-write")
                return real_write(fd, data)

            with mock.patch.object(workspace_module.os, "write", side_effect=fail_after_first):
                with self.assertRaises(ContractError):
                    promote_compiled_artifacts(workspace, artifacts)
            self.assertEqual((first / "compiled/inventory.json").read_bytes(), before)
            self.assertFalse((workspace.root / "stages/06-bundle-assemble/attempts/2").exists())

    def test_inventory_drift_and_file_or_aggregate_overflow_are_rejected_before_append(self) -> None:
        spec, theme, records = self._inputs()
        artifacts = build_compiled_artifacts(spec, theme, records[:1], IDENTITIES, evidence_graph=EVIDENCE)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            tampered_inventory = json.loads(artifacts["compiled/inventory.json"])
            tampered_inventory["layers"][-1]["records"][0]["sha256"] = "a" * 64
            tampered = dict(artifacts)
            tampered["compiled/inventory.json"] = artifacts_module.canonical_json_bytes(tampered_inventory)
            with self.assertRaises(ContractError) as raised:
                promote_compiled_artifacts(workspace, tampered)
            self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

            oversized = dict(artifacts)
            oversized["compiled/scenes/en/desktop.json"] = b"x" * (artifacts_module.MAX_SCENE_BYTES + 1)
            with self.assertRaises(ContractError) as raised:
                promote_compiled_artifacts(workspace, oversized)
            self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")

            with mock.patch.object(artifacts_module, "MAX_COMPILED_BYTES", 1):
                with self.assertRaises(ContractError) as raised:
                    promote_compiled_artifacts(workspace, artifacts)
            self.assertEqual(raised.exception.code, "E_VISUAL_RESOURCE")
            self.assertFalse((workspace.root / "stages/06-bundle-assemble/attempts/1").exists())

    def test_symlinked_stage_attempt_ancestor_cannot_escape_run_root(self) -> None:
        spec, theme, records = self._inputs()
        artifacts = build_compiled_artifacts(spec, theme, records[:1], IDENTITIES, evidence_graph=EVIDENCE)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            outside = root / "outside"
            outside.mkdir()
            workspace = RunWorkspace(root / "run", target)
            workspace.initialize(
                repository="owner/repo",
                base_sha="a" * 40,
                configuration={"mode": "readme", "project_type": "tool", "locales": ["en"], "scanner_profile": "balanced"},
                clock=lambda: "2026-08-04T00:00:00Z",
            )
            attempts = workspace.root / "stages/06-bundle-assemble/attempts"
            attempts.symlink_to(outside, target_is_directory=True)
            with self.assertRaises(ContractError) as raised:
                promote_compiled_artifacts(workspace, artifacts)
            self.assertEqual(raised.exception.code, "E_RUN_PATH")
            self.assertEqual(list(outside.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
