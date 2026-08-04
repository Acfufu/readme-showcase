from __future__ import annotations

import copy
import hashlib
import importlib
import importlib.metadata
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.visual_kernel.fingerprint import build_layered_fingerprint


ROOT = Path(__file__).resolve().parents[1]
SCHEMAS = ROOT / "skill" / "schemas"
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"
INDEX = FIXTURES / "index.json"


def _load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve(name: str) -> Callable[..., Any]:
    module_name, function_name = name.rsplit(".", 1)
    return getattr(importlib.import_module(module_name), function_name)


class SchemaParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = _load(INDEX)
        cls.fact = build_fact(
            kind="file-presence",
            path="source/README.md",
            locator=None,
            semantic_key="presence",
            value=True,
            source_bytes=b"source evidence\n",
        )
        cls.graph = EvidenceGraph([cls.fact]).to_dict()

    def _payload(self, value: Any) -> Any:
        return json.loads(json.dumps(value).replace("FACT_ID", self.fact["fact_id"]))

    @staticmethod
    def _asset_fixture_bytes(path: str) -> bytes:
        return f"readme-showcase-asset-manifest-v3::{path}\n".encode("utf-8")

    def _materialize_asset_manifest_v3(self, root: Path, payload: dict[str, Any]) -> None:
        """Build the minimal real artifact root required by the v3 validator.

        The fixture stores only relative references and hashes.  This adapter
        materializes deterministic regular files for those references, then
        rebuilds the canonical LayeredFingerprint inventory before invoking the
        product validator.  It intentionally does not normalize or bypass any
        v3 validation path.
        """
        compiled = payload["compiled"]
        refs: list[dict[str, Any]] = [compiled["spec"], compiled["theme"]]
        for name in ("scenes", "gates", "timelines", "interactions", "svgs"):
            refs.extend(compiled[name])
        files: dict[str, bytes] = {}
        for reference in refs:
            path = reference["path"]
            raw = self._asset_fixture_bytes(path)
            self.assertEqual(hashlib.sha256(raw).hexdigest(), reference["sha256"], path)
            files[path] = raw

        scene_records = [
            {
                "locale": reference["locale"],
                "variant": reference["variant"],
                "sha256": reference["sha256"],
                "prior_sha256": compiled["spec"]["sha256"],
            }
            for reference in compiled["scenes"]
        ]
        scene_hashes = {(record["locale"], record["variant"]): record["sha256"] for record in scene_records}

        def report_records(name: str, previous: dict[tuple[str, str], str]) -> list[dict[str, str]]:
            return [
                {
                    "locale": reference["locale"],
                    "variant": reference["variant"],
                    "sha256": reference["sha256"],
                    "prior_sha256": previous[(reference["locale"], reference["variant"])],
                }
                for reference in compiled[name]
            ]

        gate_records = report_records("gates", scene_hashes)
        gate_hashes = {(record["locale"], record["variant"]): record["sha256"] for record in gate_records}
        timeline_records = report_records("timelines", gate_hashes)
        timeline_hashes = {(record["locale"], record["variant"]): record["sha256"] for record in timeline_records}
        interaction_records = report_records("interactions", timeline_hashes)
        report_prior = hashlib.sha256(
            canonical_json_bytes(
                {"gates": gate_records, "timelines": timeline_records, "interactions": interaction_records}
            )
        ).hexdigest()
        artifact_records = [
            {"path": path, "sha256": hashlib.sha256(files[path]).hexdigest(), "prior_sha256": report_prior}
            for path in sorted(files)
        ]
        fingerprint = build_layered_fingerprint(
            compiled["spec"]["sha256"],
            scene_records,
            compiled["theme"]["sha256"],
            compiled["identities"],
            gate_records,
            timeline_records,
            interaction_records,
            artifact_records,
        )
        inventory_raw = fingerprint.canonical_bytes()
        inventory = compiled["inventory"]
        self.assertEqual(hashlib.sha256(inventory_raw).hexdigest(), inventory["sha256"])
        files[inventory["path"]] = inventory_raw
        for path, raw in files.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)

    def _python_result(
        self,
        entry: dict[str, Any],
        payload: Any,
        root: Path,
        *,
        valid: bool,
    ) -> tuple[bool, str | None]:
        validator = _resolve(entry["python_validator"])
        payload = self._payload(payload)
        try:
            if entry["adapter"] == "evidence_graph":
                if entry["schema"] == "claim-map.v3.schema.json":
                    visual_spec = self._payload(_load(FIXTURES / "visual-spec-v1.valid.json"))
                    validator(payload, evidence_graph=self.graph, visual_spec=visual_spec)
                else:
                    validator(payload, evidence_graph=self.graph)
            elif entry["adapter"] == "artifact_root":
                if valid:
                    from tests.contract.test_bundle_v2 import BundleV2ContractTests

                    generated = BundleV2ContractTests().make_bundle(root)
                    self.assertEqual(generated, payload)
                validator(payload, root)
            elif entry["adapter"] == "artifact_root_v3":
                from tests.contract.test_bundle_v3 import BundleV3ContractTests

                generated = BundleV3ContractTests().make_bundle(root)
                if valid:
                    self.assertEqual(generated, payload)
                validator(payload, root)
            elif entry["adapter"] == "asset_manifest_v3":
                valid_fixture = self._payload(_load(FIXTURES / "asset-manifest-v3.valid.json"))
                manifest_root = root / "asset-manifest-v3"
                self._materialize_asset_manifest_v3(manifest_root, valid_fixture)
                validator(payload, evidence_graph=self.graph, artifact_root=manifest_root)
            else:
                validator(payload)
        except ContractError as error:
            return False, error.code
        return True, None

    @staticmethod
    def _declared_codes(schema: dict[str, Any]) -> set[str]:
        extension = schema.get("x-python-semantic-validation", {})
        if "codes" in extension:
            return set(extension["codes"])
        return {extension["code"]} if "code" in extension else set()

    def test_index_is_complete_bounded_and_pinned(self) -> None:
        self.assertEqual(importlib.metadata.version("jsonschema"), "4.26.0")
        self.assertEqual(self.index["draft"], "https://json-schema.org/draft/2020-12/schema")
        entries = self.index["schemas"]
        self.assertEqual(len(entries), 24)
        self.assertEqual(len(list(FIXTURES.glob("*.valid.json"))), 24)
        self.assertEqual(len(list(FIXTURES.glob("*.invalid.json"))), 24)
        self.assertEqual(len(list(FIXTURES.glob("*.valid.json"))) + len(list(FIXTURES.glob("*.invalid.json"))), 48)
        self.assertEqual(INDEX.read_bytes(), canonical_json_bytes(self.index))
        self.assertEqual(
            [entry["schema"] for entry in entries],
            sorted(entry["schema"] for entry in entries),
        )
        self.assertEqual(
            {entry["schema"] for entry in entries},
            {path.name for path in SCHEMAS.glob("*.schema.json")},
        )
        for entry in entries:
            self.assertEqual(
                set(entry),
                {"adapter", "invalid_fixture", "producer", "python_validator", "schema", "valid_fixture"}
                | ({"invalid_code"} if "invalid_code" in entry else set()),
            )
            for key, parent in (("schema", SCHEMAS), ("valid_fixture", FIXTURES), ("invalid_fixture", FIXTURES)):
                value = entry[key]
                self.assertEqual(Path(value).name, value)
                self.assertTrue((parent / value).is_file())

    def test_all_valid_and_invalid_fixtures_follow_declared_routing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            for entry in self.index["schemas"]:
                schema = _load(SCHEMAS / entry["schema"])
                Draft202012Validator.check_schema(schema)
                self.assertEqual(schema["$schema"], self.index["draft"])
                draft = Draft202012Validator(schema)
                valid = self._payload(_load(FIXTURES / entry["valid_fixture"]))
                with self.subTest(schema=entry["schema"], case="valid"):
                    self.assertEqual(list(draft.iter_errors(valid)), [])
                    self.assertEqual(self._python_result(entry, valid, artifact_root, valid=True), (True, None))

                invalid = _load(FIXTURES / entry["invalid_fixture"])
                cases = invalid.get("cases") or [{
                    "name": "fixture",
                    "code": entry["invalid_code"],
                    "payload": invalid,
                }]
                declared = self._declared_codes(schema)
                for case in cases:
                    payload = self._payload(case["payload"])
                    draft_errors = list(draft.iter_errors(payload))
                    python_ok, python_code = self._python_result(entry, payload, artifact_root, valid=False)
                    with self.subTest(schema=entry["schema"], case=case["name"]):
                        self.assertFalse(python_ok)
                        self.assertEqual(python_code, case["code"])
                        if case.get("semantic") is True:
                            self.assertEqual(draft_errors, [])
                            self.assertIn(case["code"], declared)
                        else:
                            self.assertTrue(draft_errors)

    def test_unknown_semantic_routing_fails_closed(self) -> None:
        schema = _load(SCHEMAS / "readme-plan.v1.schema.json")
        declared = self._declared_codes(schema)
        self.assertNotIn("E_NOT_DECLARED", declared)
        invalid = _load(FIXTURES / "readme-plan-v1.invalid.json")
        structural = next(case for case in invalid["cases"] if case["name"] == "unknown-field")
        self.assertIsNot(structural.get("semantic"), True)
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(structural["payload"])))

    def test_generated_bundle_v3_hostile_cases_keep_schema_and_python_parity(self) -> None:
        entry = next(item for item in self.index["schemas"] if item["schema"] == "generated-bundle.v3.schema.json")
        schema = _load(SCHEMAS / entry["schema"])
        draft = Draft202012Validator(schema)
        fixture = _load(FIXTURES / entry["invalid_fixture"])
        expected = {
            "missing-compiled": ("E_SCHEMA_MISSING_FIELD", False),
            "stale-fingerprint": ("E_VISUAL_FINGERPRINT", True),
            "stage-origin-mismatch": ("E_VISUAL_PATH", False),
            "unknown-field": ("E_SCHEMA_UNKNOWN_FIELD", False),
            "v3-shaped-labeled-v2": ("E_SCHEMA_VERSION", False),
        }
        self.assertEqual({case["name"] for case in fixture["cases"]}, set(expected))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for case in fixture["cases"]:
                payload = self._payload(case["payload"])
                errors = list(draft.iter_errors(payload))
                python_ok, python_code = self._python_result(entry, payload, root, valid=False)
                expected_code, semantic = expected[case["name"]]
                with self.subTest(case=case["name"]):
                    self.assertFalse(python_ok)
                    self.assertEqual(python_code, expected_code)
                    self.assertEqual(case.get("semantic") is True, semantic)
                    if semantic:
                        self.assertEqual(errors, [])
                    else:
                        self.assertTrue(errors)

    def test_readme_plan_v1_v2_schema_and_fixture_bytes_remain_unchanged(self) -> None:
        expected = {
            "skill/schemas/readme-plan.v1.schema.json": "f9936697c6aee37ec337edd5a6e929bf230a7759323cb43512196fe41045afc9",
            "skill/schemas/readme-plan.v2.schema.json": "671d22233a76882ed7209cf6cda65bb4133c1dfbb57022777aa687868dae26b6",
            "tests/fixtures/contracts/readme-plan-v1.valid.json": "ecfd65f67dabb6ea688ddd99db9b472863ffb3a338c39e786a94cbf85648a3e6",
            "tests/fixtures/contracts/readme-plan-v1.invalid.json": "4cd8a573d310e4dd0e521e24d2ab9a5d866b3f1fa9ba8c3b8d0787f3c573b10d",
            "tests/fixtures/contracts/readme-plan-v2.valid.json": "0505e851996c2343590afdee622ab5468e382a1ff5f7aba401d12d8dc0a0993c",
            "tests/fixtures/contracts/readme-plan-v2.invalid.json": "6fb138e0ff1348f88673321104a84cf33784a56ccf347776e8636adaafe92474",
        }
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest)

    def test_claim_map_v2_schema_and_fixture_bytes_remain_unchanged(self) -> None:
        expected = {
            "skill/schemas/claim-map.v2.schema.json": "1a41b0ef2c3ad3bd7b2ec4707668cdcf52b91b36ae79efd6523245c4d51d0739",
            "tests/fixtures/contracts/claim-map-v2.valid.json": "8a8e46f5eb19ebce7934d3320d9a496c5ad3c36884335710abcfdffacb02cc4d",
            "tests/fixtures/contracts/claim-map-v2.invalid.json": "1d5fbf165d55603d879791918ea6b2990c499382c26dddabdb96b0216c0699c5",
        }
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest)

    def test_asset_manifest_v2_schema_and_fixture_bytes_remain_unchanged(self) -> None:
        expected = {
            "skill/schemas/asset-manifest.v2.schema.json": "b0b6798cde76fa25d7cc4b10a39f6c41e974d6f23eca1e86291d986ce350529a",
            "tests/fixtures/contracts/asset-manifest-v2.valid.json": "f6e3cac29897085f0541420bf66d54aadaa8df509e90841af185fe52b5c244ae",
            "tests/fixtures/contracts/asset-manifest-v2.invalid.json": "75f7b4c3439942e0b4c53fb70367c21e1af065d0da72e394acbd59bd381c1f0c",
        }
        for relative, digest in expected.items():
            with self.subTest(path=relative):
                self.assertEqual(hashlib.sha256((ROOT / relative).read_bytes()).hexdigest(), digest)

    def test_visual_scene_semantic_builder_errors_are_declared(self) -> None:
        entry = next(item for item in self.index["schemas"] if item["schema"] == "visual-scene.v1.schema.json")
        schema = _load(SCHEMAS / entry["schema"])
        declared = self._declared_codes(schema)
        valid = self._payload(_load(FIXTURES / entry["valid_fixture"]))

        def primitive(candidate: dict[str, Any], kind: str) -> dict[str, Any]:
            return next(item for item in candidate["primitives"] if item["kind"] == kind)

        cases: list[tuple[str, dict[str, Any], str]] = []
        floating = copy.deepcopy(valid)
        primitive(floating, "rect")["x"] = 1.5
        cases.append(("float-geometry", floating, "E_VISUAL_GEOMETRY"))

        outside = copy.deepcopy(valid)
        primitive(outside, "rect")["x"] = 20001
        cases.append(("out-of-bounds-geometry", outside, "E_VISUAL_GEOMETRY"))

        unknown = copy.deepcopy(valid)
        primitive(unknown, "rect")["mystery"] = True
        cases.append(("unknown-primitive-field", unknown, "E_SCHEMA_UNKNOWN_FIELD"))

        bad_source_hash = copy.deepcopy(valid)
        bad_source_hash["source_spec_sha256"] = "g" * 64
        cases.append(("bad-source-hash", bad_source_hash, "E_VISUAL_FINGERPRINT"))

        invalid_order = copy.deepcopy(valid)
        invalid_order["primitives"] = list(reversed(invalid_order["primitives"]))
        cases.append(("invalid-primitive-order", invalid_order, "E_VISUAL_DETERMINISM"))

        wrong_backend = copy.deepcopy(valid)
        wrong_backend["backend"]["package_version"] = "0.9.2"
        cases.append(("wrong-backend-identity", wrong_backend, "E_VISUAL_FINGERPRINT"))

        malformed_path = copy.deepcopy(valid)
        primitive(malformed_path, "path")["points"] = [{"x": 1}]
        cases.append(("malformed-path-point", malformed_path, "E_VISUAL_GEOMETRY"))

        with tempfile.TemporaryDirectory() as temporary:
            for name, payload, expected_code in cases:
                with self.subTest(case=name):
                    python_ok, python_code = self._python_result(entry, payload, Path(temporary), valid=False)
                    self.assertFalse(python_ok)
                    self.assertEqual(python_code, expected_code)
                    self.assertIn(expected_code, declared)


if __name__ == "__main__":
    unittest.main()
