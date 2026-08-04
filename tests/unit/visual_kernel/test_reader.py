from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.visual_kernel.artifacts import build_compiled_artifacts
from skill.scripts.readme_showcase.visual_kernel.compiler import CompiledVisual
from skill.scripts.readme_showcase.visual_kernel.fingerprint import build_layered_fingerprint
from skill.scripts.readme_showcase.visual_kernel.reader import load_compiled_visual
from tests.unit.visual_kernel import test_artifacts as artifacts_test
from tests.unit.visual_kernel.test_scene import EVIDENCE


class CompiledVisualReaderTests(unittest.TestCase):
    def _attempt(self) -> tuple[Path, dict[str, object], dict[str, bytes], dict[str, object]]:
        spec, theme, records = artifacts_test.CompiledArtifactTests()._inputs()
        artifacts = dict(
            build_compiled_artifacts(
                spec,
                theme,
                records,
                artifacts_test.IDENTITIES,
                evidence_graph=EVIDENCE,
            )
        )
        inventory = json.loads(artifacts["compiled/inventory.json"])
        layers = inventory["layers"]
        compiled: dict[str, object] = {
            "spec": {
                "path": "compiled/visual-spec.json",
                "sha256": hashlib.sha256(artifacts["compiled/visual-spec.json"]).hexdigest(),
            },
            "theme": {
                "path": "compiled/theme.json",
                "sha256": hashlib.sha256(artifacts["compiled/theme.json"]).hexdigest(),
            },
            "inventory": {
                "path": "compiled/inventory.json",
                "sha256": hashlib.sha256(artifacts["compiled/inventory.json"]).hexdigest(),
            },
            "scenes": [],
            "gates": [],
            "timelines": [],
            "interactions": [],
            "svgs": [],
            "identities": dict(layers[3]["values"]),
        }
        for name, directory, layer_index in (
            ("scenes", "scenes", 1),
            ("gates", "gates", 4),
            ("timelines", "timeline", 5),
            ("interactions", "interaction", 6),
        ):
            refs = compiled[name]
            assert isinstance(refs, list)
            for record in layers[layer_index]["records"]:
                path = f"compiled/{directory}/{record['locale']}/{record['variant']}.json"
                refs.append(
                    {
                        "locale": record["locale"],
                        "variant": record["variant"],
                        "path": path,
                        "sha256": record["sha256"],
                    }
                )
        svg_refs = compiled["svgs"]
        assert isinstance(svg_refs, list)
        for record in layers[7]["records"]:
            path = record["path"]
            if not path.startswith("assets/readme-showcase/"):
                continue
            parts = path.split("/")
            svg_refs.append(
                {
                    "locale": parts[2],
                    "variant": parts[3][:-4],
                    "path": path,
                    "sha256": record["sha256"],
                }
            )

        fact_id = EVIDENCE["facts"][0]["fact_id"]
        assets: list[dict[str, object]] = []
        for svg in svg_refs:
            assert isinstance(svg, dict)
            key = (svg["locale"], svg["variant"])
            scenes = compiled["scenes"]
            gates = compiled["gates"]
            assert isinstance(scenes, list) and isinstance(gates, list)
            scene = next(item for item in scenes if (item["locale"], item["variant"]) == key)
            gate = next(item for item in gates if (item["locale"], item["variant"]) == key)
            assets.append(
                {
                    "asset_id": f"diagram-{svg['locale']}-{svg['variant']}",
                    "path": svg["path"],
                    "artifact_sha256": svg["sha256"],
                    "evidence_ids": [fact_id],
                    "role": "diagram",
                    "locale": svg["locale"],
                    "variant": svg["variant"],
                    "scene_sha256": scene["sha256"],
                    "gate_sha256": gate["sha256"],
                }
            )
        manifest: dict[str, object] = {"schema_version": 3, "assets": assets, "compiled": compiled}
        manifest_raw = canonical_json_bytes(manifest)
        root = Path(tempfile.mkdtemp(prefix="visual-reader-"))
        for path, data in artifacts.items():
            destination = root.joinpath(*path.split("/"))
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(data)
        (root / "asset-manifest.json").write_bytes(manifest_raw)
        bundle: dict[str, object] = {
            "schema_version": 3,
            "compiled": {
                "inventory": compiled["inventory"],
                "fingerprint": inventory["inventory_sha256"],
                "retention": "manual",
            },
            "artifacts": {
                "asset_manifest": {
                    "path": "asset-manifest.json",
                    "sha256": hashlib.sha256(manifest_raw).hexdigest(),
                }
            },
        }
        return root, bundle, artifacts, inventory

    def _assert_code(self, root: Path, bundle: dict[str, object], code: str) -> None:
        try:
            load_compiled_visual(root, bundle)
        except ContractError as exc:
            self.assertEqual(exc.code, code)
            self.assertNotIn(str(root), str(exc))
        else:
            self.fail(f"expected {code}")

    def test_happy_nested_attempt_is_repeatable_immutable_and_redacted(self) -> None:
        root, bundle, artifacts, inventory = self._attempt()
        try:
            first = load_compiled_visual(root, bundle)
            second = load_compiled_visual(root, bundle)
            self.assertIsInstance(first, CompiledVisual)
            self.assertEqual(first, second)
            self.assertEqual(first.artifacts, second.artifacts)
            self.assertNotEqual(
                first.inventory_sha256,
                hashlib.sha256(artifacts["compiled/inventory.json"]).hexdigest(),
            )
            self.assertEqual(first.inventory_sha256, inventory["inventory_sha256"])
            with self.assertRaises(TypeError):
                first.artifacts["compiled/extra.json"] = b"no"  # type: ignore[index]
            self.assertNotIn(str(root), repr(first))
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()

    def test_hash_size_and_prior_layer_drift_fail_without_repair(self) -> None:
        root, bundle, artifacts, inventory = self._attempt()
        try:
            theme = root / "compiled/theme.json"
            theme.write_bytes(theme.read_bytes() + b"drift")
            self._assert_code(root, bundle, "E_VISUAL_FINGERPRINT")
            theme.write_bytes(artifacts["compiled/theme.json"])

            oversized_spec = root / "compiled/visual-spec.json"
            oversized_spec.write_bytes(b"x" * (256 * 1024 + 1))
            self._assert_code(root, bundle, "E_VISUAL_FINGERPRINT")
            oversized_spec.write_bytes(artifacts["compiled/visual-spec.json"])

            stale = copy.deepcopy(inventory)
            stale["layers"][1]["records"][0]["prior_sha256"] = "0" * 64
            stale_raw = canonical_json_bytes(stale)
            (root / "compiled/inventory.json").write_bytes(stale_raw)
            stale_bundle = copy.deepcopy(bundle)
            stale_bundle["compiled"]["inventory"]["sha256"] = hashlib.sha256(stale_raw).hexdigest()  # type: ignore[index]
            self._assert_code(root, stale_bundle, "E_VISUAL_FINGERPRINT")

            identity_drift = copy.deepcopy(inventory)
            identity_drift["layers"][3]["values"]["kernel"] = hashlib.sha256(b"identity-drift").hexdigest()
            identity_layers = identity_drift["layers"]
            rebuilt = build_layered_fingerprint(
                identity_layers[0]["sha256"],
                identity_layers[1]["records"],
                identity_layers[2]["sha256"],
                identity_layers[3]["values"],
                identity_layers[4]["records"],
                identity_layers[5]["records"],
                identity_layers[6]["records"],
                identity_layers[7]["records"],
            )
            identity_raw = rebuilt.canonical_bytes()
            (root / "compiled/inventory.json").write_bytes(identity_raw)
            identity_bundle = copy.deepcopy(bundle)
            identity_bundle["compiled"]["inventory"]["sha256"] = hashlib.sha256(identity_raw).hexdigest()  # type: ignore[index]
            identity_bundle["compiled"]["fingerprint"] = rebuilt.inventory_sha256  # type: ignore[index]
            self._assert_code(root, identity_bundle, "E_VISUAL_FINGERPRINT")
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()

    def test_extra_file_and_symlinked_artifact_fail_with_path_code(self) -> None:
        root, bundle, _, _ = self._attempt()
        try:
            extra = root / "compiled/extra.json"
            extra.write_bytes(b"extra")
            self._assert_code(root, bundle, "E_VISUAL_FINGERPRINT")
            extra.unlink()
            target = root / "outside.svg"
            target.write_bytes(b"outside")
            svg = next((root / "assets/readme-showcase").rglob("*.svg"))
            svg.unlink()
            svg.symlink_to(target)
            self._assert_code(root, bundle, "E_VISUAL_PATH")
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()

    def test_retention_version_and_alias_shaped_inputs_are_rejected(self) -> None:
        root, bundle, _, _ = self._attempt()
        try:
            missing = copy.deepcopy(bundle)
            del missing["compiled"]["retention"]  # type: ignore[index]
            self._assert_code(root, missing, "E_VISUAL_FINGERPRINT")

            wrong_version = copy.deepcopy(bundle)
            wrong_version["schema_version"] = 2
            self._assert_code(root, wrong_version, "E_VISUAL_FINGERPRINT")

            alias = copy.deepcopy(bundle)
            alias["asset_manifest"] = alias["artifacts"].pop("asset_manifest")  # type: ignore[index]
            self._assert_code(root, alias, "E_VISUAL_FINGERPRINT")

            fingerprint_alias = copy.deepcopy(bundle)
            fingerprint_alias["compiled"]["fingerprint"] = {"inventory_sha256": bundle["compiled"]["fingerprint"]}  # type: ignore[index]
            self._assert_code(root, fingerprint_alias, "E_VISUAL_FINGERPRINT")
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()

    def test_absolute_bundle_path_is_rejected_without_path_leak(self) -> None:
        root, bundle, _, _ = self._attempt()
        try:
            malformed = copy.deepcopy(bundle)
            malformed["compiled"]["inventory"]["path"] = str(root / "compiled/inventory.json")  # type: ignore[index]
            self._assert_code(root, malformed, "E_VISUAL_PATH")
        finally:
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()


if __name__ == "__main__":
    unittest.main()
