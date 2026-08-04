from __future__ import annotations

import hashlib
import json
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.orchestration import workspace as workspace_module
from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace
from skill.scripts.readme_showcase.visual_kernel import artifacts as artifacts_module
from skill.scripts.readme_showcase.visual_kernel.artifacts import (
    build_compiled_artifacts,
    promote_compiled_artifacts,
)
from skill.scripts.readme_showcase.visual_kernel.diagnostics import VisualGateReport
from skill.scripts.readme_showcase.visual_kernel.interaction import derive_interaction
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.model import validate_visual_spec
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme
from skill.scripts.readme_showcase.visual_kernel.timeline import derive_timeline
from tests.unit.visual_kernel.test_scene import EVIDENCE, _build, _spec


IDENTITIES = {name: hashlib.sha256(name.encode("utf-8")).hexdigest() for name in ("kernel", "elk", "renderer")}


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
        paths = [item["path"] for item in inventory["artifacts"]]
        self.assertEqual(paths, sorted(paths, key=lambda item: item.encode("utf-8")))
        self.assertNotIn("compiled/inventory.json", paths)
        for item in inventory["artifacts"]:
            data = first[item["path"]]
            self.assertEqual(item["sha256"], hashlib.sha256(data).hexdigest())
            self.assertEqual(item["size"], len(data))
        self.assertEqual(
            [path for path in first if path.startswith("assets/readme-showcase/")],
            [
                "assets/readme-showcase/en/desktop.svg",
                "assets/readme-showcase/en/mobile.svg",
                "assets/readme-showcase/zh-Hans/desktop.svg",
                "assets/readme-showcase/zh-Hans/mobile.svg",
            ],
        )

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
            tampered_inventory["artifacts"][0]["size"] += 1
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
