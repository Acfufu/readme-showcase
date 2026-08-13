import json
import hashlib
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.contracts.retrieval import validate_exemplar_record_v1

# File lives at tests/unit/contracts/ -> parents[3] is the repo root
# (parents[0]=contracts, parents[1]=unit, parents[2]=tests).
EXEMPLARS = Path(__file__).resolve().parents[3] / "dataset" / "retrieval" / "exemplars"


class ExemplarDatasetTest(unittest.TestCase):
    def test_ten_curated_records_valid(self):
        records = sorted(EXEMPLARS.glob("curated-*.json"))
        self.assertGreaterEqual(len(records), 10)
        for path in records:
            record = json.loads(path.read_text(encoding="utf-8"))
            validated = validate_exemplar_record_v1(record)
            asset = EXEMPLARS / validated["asset"]["path"].rsplit("/", 1)[-1]
            self.assertTrue(asset.exists(), f"missing asset {asset}")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            self.assertEqual(digest, validated["asset"]["sha256"], f"sha mismatch {asset}")

    def test_synthetic_records_valid_train_only(self):
        records = sorted(EXEMPLARS.glob("synthetic-*.json"))
        self.assertGreaterEqual(len(records), 4)
        for path in records:
            record = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(record["split"], "train")
            self.assertEqual(record["asset"]["kind"], "synthetic")
            validated = validate_exemplar_record_v1(record)
            asset = EXEMPLARS / validated["asset"]["path"].rsplit("/", 1)[-1]
            self.assertTrue(asset.exists(), f"missing asset {asset}")
            digest = hashlib.sha256(asset.read_bytes()).hexdigest()
            self.assertEqual(digest, validated["asset"]["sha256"], f"sha mismatch {asset}")

    def test_split_ratio(self):
        records = [json.loads(p.read_text(encoding="utf-8")) for p in EXEMPLARS.glob("curated-*.json")]
        train = [r for r in records if r["split"] == "train"]
        test = [r for r in records if r["split"] == "test"]
        self.assertEqual(len(train), 8)
        self.assertEqual(len(test), 2)

    def test_screening_ledger_exists(self):
        ledger = EXEMPLARS / "SCREENING.md"
        self.assertTrue(ledger.exists())
        text = ledger.read_text(encoding="utf-8")
        for record in EXEMPLARS.glob("curated-*.json"):
            self.assertIn(record.stem, text)
