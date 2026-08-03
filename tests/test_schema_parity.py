from __future__ import annotations

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
                validator(payload, evidence_graph=self.graph)
            elif entry["adapter"] == "artifact_root":
                if valid:
                    from tests.contract.test_bundle_v2 import BundleV2ContractTests

                    generated = BundleV2ContractTests().make_bundle(root)
                    self.assertEqual(generated, payload)
                validator(payload, root)
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
        self.assertEqual(len(entries), 16)
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


if __name__ == "__main__":
    unittest.main()
