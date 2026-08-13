import unittest

from skill.scripts.readme_showcase.contracts.retrieval import (
    EXEMPLAR_KINDS,
    validate_exemplar_record_v1,
)
from skill.scripts.pipeline_contracts import ContractError


def valid_exemplar() -> dict:
    return {
        "record_id": "exemplar-curated-01",
        "project_types": ["developer-tool"],
        "section_intents": ["hero"],
        "tags": ["cli", "terminal"],
        "pattern": {
            "summary": "Terminal motif hero with command rhythm.",
            "structure": "Identity block left, proof right, metadata strip bottom.",
            "proof": "Real command output as the proof block.",
            "composition_structure": {
                "zones": ["identity", "metadata", "proof"],
                "hierarchy": ["display", "section", "supporting"],
                "negative_patterns": ["centered-template", "three-equal"]
            }
        },
        "asset": {
            "path": "dataset/retrieval/exemplars/curated-01.png",
            "sha256": "0" * 64,
            "kind": "curated"
        },
        "source": {
            "repository_url": "https://github.com/example/repo",
            "commit": "0" * 40,
            "material_sha256": "0" * 64,
            "license_spdx": "MIT",
            "license_evidence_spdx": "MIT",
            "license_evidence_url": "https://github.com/example/repo/blob/0/LICENSE",
            "license_evidence_sha256": "0" * 64,
            "human_reviewed": True
        },
        "split": "train"
    }


class ExemplarRecordTest(unittest.TestCase):
    def test_valid_record_passes(self):
        record = validate_exemplar_record_v1(valid_exemplar())
        self.assertEqual(record["pattern"]["composition_structure"]["zones"][0], "identity")

    def test_missing_composition_structure_fails(self):
        record = valid_exemplar()
        del record["pattern"]["composition_structure"]
        with self.assertRaises(ContractError):
            validate_exemplar_record_v1(record)

    def test_missing_asset_fails(self):
        record = valid_exemplar()
        del record["asset"]
        with self.assertRaises(ContractError):
            validate_exemplar_record_v1(record)

    def test_bad_kind_fails(self):
        record = valid_exemplar()
        record["asset"]["kind"] = "scraped"
        with self.assertRaises(ContractError):
            validate_exemplar_record_v1(record)

    def test_unknown_top_level_field_fails(self):
        record = valid_exemplar()
        record["extra_field"] = "not in schema"
        with self.assertRaises(ContractError):
            validate_exemplar_record_v1(record)

    def test_kind_constant(self):
        self.assertEqual(EXEMPLAR_KINDS, ("curated", "synthetic"))
