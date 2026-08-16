"""Exemplars index integrity, distributability, and tamper-resistance.

The index (dataset/retrieval/exemplars_index.json) is the single trust
anchor for the 14 retrieval exemplar assets.  The suite here pins:

1. completeness - 14 records, per-file coupling with the record JSONs,
   asset-kind/ext split enums, sha256 match against real bytes, README
   distribution coupling, and the manifest boundary (exemplars never enter
   manifest["records"]);
2. distributability - npm pack ships all 14 asset paths (PNG + SVG);
3. path safety - traversal rejection and symlink-escape rejection
   (deepsec#9);
4. validation order - tampered assets with stale hashes are rejected before
   any consumer could use them;
5. index self-tamper anchor (deepsec#3 extension) - mutating the index's
   path or sha fails validation, and the self-anchor is verified before any
   asset is touched.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.retrieval import (
    load_exemplars_index_v1,
    validate_exemplars_index_v1,
)


ROOT = Path(__file__).resolve().parents[1]
DATASET_DIR = ROOT / "dataset"
EXEMPLARS_DIR = DATASET_DIR / "retrieval" / "exemplars"
INDEX_PATH = DATASET_DIR / "retrieval" / "exemplars_index.json"
MANIFEST_PATH = DATASET_DIR / "retrieval" / "manifest.json"
README_PATH = ROOT / "README.md"

EXPECTED_COUNT = 14
EXPECTED_CURATED = 10
EXPECTED_SYNTHETIC = 4


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _tamper_index(payload: dict[str, Any], mutate: Callable[[dict[str, Any]], None]) -> dict[str, Any]:
    """Return a copy of the index with a mutation but a STALE self-anchor."""
    import copy

    tampered = copy.deepcopy(payload)
    mutate(tampered)
    return tampered

class ExemplarsIndexCompletenessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.index = _load(INDEX_PATH)
        cls.records = cls.index["records"]
        cls.manifest = _load(MANIFEST_PATH)
        cls.record_files = sorted(EXEMPLARS_DIR.glob("*.json"))

    def test_index_file_exists_and_has_fourteen_records(self) -> None:
        self.assertTrue(INDEX_PATH.is_file(), "exemplars_index.json is missing")
        self.assertEqual(len(self.records), EXPECTED_COUNT)

    def test_index_couples_per_record_file(self) -> None:
        by_id = {record["record_id"]: record for record in self.records}
        self.assertEqual(len(by_id), EXPECTED_COUNT)
        self.assertEqual(len(self.record_files), EXPECTED_COUNT)
        for record_file in self.record_files:
            record = _load(record_file)
            indexed = by_id[record["record_id"]]
            self.assertEqual(indexed["asset"], record["asset"])
            self.assertEqual(indexed["split"], record["split"])

    def test_split_and_kind_enums_and_distribution(self) -> None:
        for record in self.records:
            self.assertIn(record["split"], {"train", "test"})
            self.assertIn(record["asset"]["kind"], {"curated", "synthetic"})
            kind = record["asset"]["kind"]
            path = record["asset"]["path"]
            if kind == "curated":
                self.assertTrue(path.endswith(".png"), path)
            else:
                self.assertTrue(path.endswith(".svg"), path)
        curated = [record for record in self.records if record["asset"]["kind"] == "curated"]
        synthetic = [record for record in self.records if record["asset"]["kind"] == "synthetic"]
        self.assertEqual(len(curated), EXPECTED_CURATED)
        self.assertEqual(len(synthetic), EXPECTED_SYNTHETIC)
        self.assertEqual(
            [record["split"] for record in curated].count("train"), 8,
            "curated split distribution must be 8 train / 2 test",
        )
        self.assertEqual(
            [record["split"] for record in curated].count("test"), 2,
            "curated split distribution must be 8 train / 2 test",
        )
        self.assertEqual(
            {record["split"] for record in synthetic}, {"train"},
            "synthetic exemplars must be train-only",
        )

    def test_indexed_sha_matches_actual_asset_bytes(self) -> None:
        for record in self.records:
            asset = ROOT / record["asset"]["path"]
            with self.subTest(path=record["asset"]["path"]):
                self.assertTrue(asset.is_file(), f"indexed asset is missing: {asset}")
                digest = hashlib.sha256(asset.read_bytes()).hexdigest()
                self.assertEqual(digest, record["asset"]["sha256"])

    def test_readme_distribution_coupling(self) -> None:
        readme = README_PATH.read_text(encoding="utf-8")
        self.assertIn("10 curated records (8", readme)
        self.assertIn("4 synthetic train-only", readme)

    def test_manifest_boundary_keeps_exemplars_outside_records(self) -> None:
        self.assertEqual(self.manifest["purpose"], "retrieval-only")
        manifest_ids = {record["record_id"] for record in self.manifest["records"]}
        exemplar_ids = {record["record_id"] for record in self.records}
        self.assertEqual(len(manifest_ids & exemplar_ids), 0)


class ExemplarsIndexValidatorTests(unittest.TestCase):
    def test_valid_index_passes_validation_with_dataset_root(self) -> None:
        payload = _load(INDEX_PATH)
        validated = validate_exemplars_index_v1(payload, dataset_root=ROOT)
        self.assertEqual(validated["dataset_id"], "readme-showcase-exemplars")
        loaded = load_exemplars_index_v1(INDEX_PATH, dataset_root=ROOT)
        self.assertEqual(len(loaded["records"]), EXPECTED_COUNT)

    def test_path_safety_rejects_traversal(self) -> None:
        payload = _tamper_index(
            _load(INDEX_PATH),
            lambda index: index["records"][0]["asset"].update(path="../../secrets.json"),
        )
        with self.assertRaises(ContractError) as ctx:
            validate_exemplars_index_v1(payload, dataset_root=ROOT)
        self.assertEqual(ctx.exception.code, "E_EXEMPLARS_INDEX_PATH")

    def test_symlink_escape_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(DATASET_DIR, root / "dataset")
            target = root / "dataset" / "retrieval" / "exemplars" / "curated-01.png"
            external = root / "outside-asset.png"
            shutil.copyfile(DATASET_DIR / "retrieval" / "exemplars" / "curated-01.png", external)
            target.unlink()
            target.symlink_to(external)
            payload = _load(root / "dataset" / "retrieval" / "exemplars_index.json")
            with self.assertRaises(ContractError) as ctx:
                validate_exemplars_index_v1(payload, dataset_root=root)
            self.assertEqual(ctx.exception.code, "E_EXEMPLARS_INDEX_PATH")

    def test_tampered_asset_with_stale_hash_rejected_before_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(DATASET_DIR, root / "dataset")
            asset = root / "dataset" / "retrieval" / "exemplars" / "curated-02.png"
            asset.write_bytes(asset.read_bytes() + b"tampered")
            payload = _load(root / "dataset" / "retrieval" / "exemplars_index.json")
            with self.assertRaises(ContractError) as ctx:
                validate_exemplars_index_v1(payload, dataset_root=root)
            self.assertEqual(ctx.exception.code, "E_EXEMPLARS_INDEX_SHA")


class ExemplarsIndexSelfTamperTests(unittest.TestCase):
    """deepsec#3 extension: the index carries a self-anchor (index_sha256).

    Mutating path or sha in any record must fail the anchor, and the anchor
    must be verified BEFORE any asset file is consulted.
    """

    def _expect_anchor_rejection(self, payload: dict[str, Any], dataset_root: Path) -> None:
        with self.assertRaises(ContractError) as ctx:
            validate_exemplars_index_v1(payload, dataset_root=dataset_root)
        self.assertEqual(ctx.exception.code, "E_EXEMPLARS_INDEX_ANCHOR")

    def test_tampered_path_fails_anchor(self) -> None:
        payload = _tamper_index(
            _load(INDEX_PATH),
            lambda index: index["records"][0]["asset"].update(
                path="dataset/retrieval/exemplars/curated-99.png",
            ),
        )
        self._expect_anchor_rejection(payload, ROOT)

    def test_tampered_sha_fails_anchor(self) -> None:
        payload = _tamper_index(
            _load(INDEX_PATH),
            lambda index: index["records"][0]["asset"].update(
                sha256="0" * 64,
            ),
        )
        self._expect_anchor_rejection(payload, ROOT)

    def test_anchor_verified_before_any_asset_use(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copytree(DATASET_DIR, root / "dataset")
            payload = _tamper_index(
                _load(root / "dataset" / "retrieval" / "exemplars_index.json"),
                lambda index: index["records"][3]["asset"].update(sha256="0" * 64),
            )
            # Every asset is deleted: if the anchor were checked after asset
            # resolution this would fail with a path error instead.
            for record_file in (root / "dataset" / "retrieval" / "exemplars").glob("*.png"):
                record_file.unlink()
            for record_file in (root / "dataset" / "retrieval" / "exemplars").glob("*.svg"):
                record_file.unlink()
            self._expect_anchor_rejection(payload, root)


@unittest.skipIf(
    os.environ.get("README_SHOWCASE_SKIP_NODE") == "1",
    "npm package test runs in isolated Node lane",
)
class ExemplarsIndexDistributableTests(unittest.TestCase):
    def test_npm_pack_ships_all_fourteen_assets(self) -> None:
        dry_run = subprocess.run(
            ["npm", "pack", "--dry-run", "--json"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(dry_run.returncode, 0, dry_run.stderr)
        packed = json.loads(dry_run.stdout)
        package_record = next(iter(packed.values())) if isinstance(packed, dict) else packed[0]
        listed = {item["path"] for item in package_record["files"]}
        expected = {
            "dataset/retrieval/exemplars/" + path.name
            for path in EXEMPLARS_DIR.iterdir()
            if path.suffix in {".png", ".svg"}
        }
        self.assertEqual(len(expected), EXPECTED_COUNT)
        missing = sorted(expected - listed)
        self.assertEqual(missing, [], f"npm package omitted exemplar assets: {missing}")


if __name__ == "__main__":
    unittest.main()
