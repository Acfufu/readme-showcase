from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import unittest
from pathlib import Path
from typing import Any

_CONTRACTS = importlib.import_module("skill.scripts.pipeline_contracts")
_CORE = importlib.import_module("skill.scripts.pipeline_core")
ContractError = _CONTRACTS.ContractError
validate_dataset_manifest = _CORE.validate_dataset_manifest


REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "tests/fixtures/dataset"


class DatasetValidationTests(unittest.TestCase):
    def fixture(self, name: str = "manifest-valid.json") -> dict[str, Any]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def assert_code(self, payload: dict[str, Any], code: str) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_dataset_manifest(payload)
        self.assertEqual(raised.exception.code, code)

    def test_valid_manifest_reports_split_counts(self) -> None:
        result = validate_dataset_manifest(self.fixture())

        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["record_count"], 2)
        self.assertEqual(result["split_counts"], {"test": 1, "train": 1})

    def test_duplicate_ids_and_source_identity_leakage_fail(self) -> None:
        duplicate = self.fixture()
        duplicate["records"][1]["record_id"] = duplicate["records"][0][
            "record_id"
        ]
        self.assert_code(duplicate, "E_DATASET_DUPLICATE_ID")

        leakage = self.fixture()
        leakage["records"][1]["source"] = copy.deepcopy(
            leakage["records"][0]["source"]
        )
        self.assert_code(leakage, "E_DATASET_SPLIT_LEAK")

    def test_mutable_commit_and_license_failures_are_rejected(self) -> None:
        mutable = self.fixture()
        mutable["records"][0]["source"]["commit"] = "main"
        self.assert_code(mutable, "E_DATASET_COMMIT")

        unknown = self.fixture()
        unknown["records"][0]["source"]["license_spdx"] = "NOASSERTION"
        unknown["records"][0]["source"][
            "license_evidence_spdx"
        ] = "NOASSERTION"
        self.assert_code(unknown, "E_DATASET_LICENSE")

        conflict = self.fixture()
        conflict["records"][0]["source"][
            "license_evidence_spdx"
        ] = "Apache-2.0"
        self.assert_code(conflict, "E_DATASET_LICENSE_CONFLICT")

    def test_unknown_fields_and_embedded_content_are_rejected(self) -> None:
        unknown = self.fixture()
        unknown["records"][0]["repo_readme"] = "# copied"
        self.assert_code(unknown, "E_SCHEMA_UNKNOWN_FIELD")

        embedded = self.fixture("manifest-invalid-embedded-content.json")
        self.assert_code(embedded, "E_DATASET_EMBEDDED_CONTENT")

    def test_cli_validates_bootstrap_manifest(self) -> None:
        result = subprocess.run(
            [
                sys.executable,
                "skill/scripts/readme_pipeline.py",
                "validate-dataset",
                "--manifest",
                "dataset/retrieval/manifest.json",
            ],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr, "")
        self.assertEqual(json.loads(result.stdout)["status"], "pass")


if __name__ == "__main__":
    unittest.main()
