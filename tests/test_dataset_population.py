from __future__ import annotations

import importlib
import json
import unittest
from pathlib import Path


_CORE = importlib.import_module("skill.scripts.pipeline_core")
validate_dataset_manifest = _CORE.validate_dataset_manifest
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "dataset/retrieval/manifest.json"


class DatasetPopulationTests(unittest.TestCase):
    def test_manifest_has_reviewed_variety_without_copied_payloads(self) -> None:
        payload = json.loads(MANIFEST.read_text(encoding="utf-8"))
        result = validate_dataset_manifest(payload)
        records = payload["records"]

        self.assertEqual(
            result["manifest_sha256"],
            "45aa6396def1954f52b8d12c96627039664fac39ab5ec24ac2858c6dbdde7486",
        )
        self.assertEqual(result["record_count"], 12)
        self.assertEqual(result["split_counts"], {"test": 2, "train": 10})
        self.assertEqual(
            {project_type for record in records for project_type in record["project_types"]},
            {"developer-tool", "library", "runtime-toolchain", "web-framework"},
        )
        self.assertGreaterEqual(
            len({intent for record in records for intent in record["section_intents"]}),
            8,
        )
        self.assertTrue(all(record["source"]["human_reviewed"] is True for record in records))
        self.assertTrue(
            all(
                len(text) <= 200
                for record in records
                for text in record["pattern"].values()
            )
        )


if __name__ == "__main__":
    unittest.main()
