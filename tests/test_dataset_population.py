from __future__ import annotations

import copy
import hashlib
import importlib
import json
import os
import tempfile
import unittest
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes


_CORE = importlib.import_module("skill.scripts.pipeline_core")
_RETRIEVAL_CONTRACT = importlib.import_module(
    "skill.scripts.readme_showcase.contracts.retrieval"
)
validate_dataset_manifest = _CORE.validate_dataset_manifest
load_retrieval_candidate_ledger_v1 = (
    _RETRIEVAL_CONTRACT.load_retrieval_candidate_ledger_v1
)
validate_retrieval_candidate_ledger_v1 = (
    _RETRIEVAL_CONTRACT.validate_retrieval_candidate_ledger_v1
)
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "dataset/retrieval/manifest.json"
CANDIDATES = REPO_ROOT / "dataset/retrieval/candidates.json"
CANDIDATE_SCHEMA = (
    REPO_ROOT / "skill/schemas/retrieval-candidate-ledger.v1.schema.json"
)
VALID_FIXTURE = (
    REPO_ROOT
    / "tests/fixtures/contracts/retrieval-candidate-ledger-v1.valid.json"
)
INVALID_FIXTURE = (
    REPO_ROOT
    / "tests/fixtures/contracts/retrieval-candidate-ledger-v1.invalid.json"
)
MANIFEST_FILE_SHA256 = "07db475c0f56022a1e6b49f7546dc6c865d8e090c38b23e980572f3cdcb50da0"
REVIEW_CLOCK = datetime(2026, 8, 3, 10, 4, 0, tzinfo=timezone.utc)
APPROVED_IDS = {
    "attrs-class-protocols", "bun-all-in-one-runtime", "click-cli-composable", "django-docs-learning-route", "gin-json-api-first-run",
    "node-release-toolchain", "nushell-structured-shell", "pre-commit-hook-framework", "rails-mvc-first-run", "starship-shell-prompt-onboarding",
}


class DatasetPopulationTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def ledger(self) -> dict[str, Any]:
        return json.loads(CANDIDATES.read_text(encoding="utf-8"))

    def assert_candidate_error(
        self,
        code: str,
        payload: dict[str, Any],
    ) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_retrieval_candidate_ledger_v1(
                payload,
                production_manifest=self.manifest(),
                production_manifest_sha256=MANIFEST_FILE_SHA256,
                review_time_not_after=REVIEW_CLOCK,
            )
        self.assertEqual(raised.exception.code, code)

    def test_pending_candidate_ledger_contract_exists(self) -> None:
        # given
        required_validator = "validate_retrieval_candidate_ledger_v1"

        # when / then
        self.assertTrue(CANDIDATES.is_file())
        self.assertTrue(CANDIDATE_SCHEMA.is_file())
        self.assertTrue(hasattr(_RETRIEVAL_CONTRACT, required_validator))

    def test_candidate_ledger_is_canonical_with_exact_authorized_subset(self) -> None:
        # given
        manifest = self.manifest()
        payload = self.ledger()

        # when
        validated = load_retrieval_candidate_ledger_v1(
            CANDIDATES,
            production_manifest=manifest,
            production_manifest_sha256=MANIFEST_FILE_SHA256,
            review_time_not_after=REVIEW_CLOCK,
        )

        # then
        candidates = validated["candidates"]
        self.assertEqual(CANDIDATES.read_bytes(), canonical_json_bytes(payload))
        self.assertEqual(len(candidates), 12)
        self.assertEqual(
            [candidate["record_id"] for candidate in candidates],
            sorted(candidate["record_id"] for candidate in candidates),
        )
        self.assertEqual(
            {candidate["record_id"] for candidate in candidates if candidate["review_status"] == "approved"},
            APPROVED_IDS,
        )
        self.assertEqual(
            {candidate["record_id"] for candidate in candidates if candidate["review_status"] == "unverified"},
            {"cpython-build-runtime", "marshmallow-schema-serialization"},
        )
        self.assertTrue(all(candidate["intended_split"] == "train" for candidate in candidates))
        self.assertEqual(
            {candidate["project_type"] for candidate in candidates},
            {"developer-tool", "library", "runtime-toolchain", "web-framework"},
        )
        self.assertEqual(
            {
                key
                for candidate in candidates
                for key in candidate["metadata"]
            },
            {
                "project_size", "user_role", "install_method", "has_ui",
                "multi_package", "primary_readme_goal",
            },
        )
        self.assertEqual(len(manifest["records"]), 22)

    def test_candidate_fixture_conclusions_match_python_contract(self) -> None:
        # given
        valid = json.loads(VALID_FIXTURE.read_text(encoding="utf-8"))
        invalid = json.loads(INVALID_FIXTURE.read_text(encoding="utf-8"))

        # when / then
        self.assertEqual(
            validate_retrieval_candidate_ledger_v1(valid),
            valid,
        )
        with self.assertRaises(ContractError) as raised:
            validate_retrieval_candidate_ledger_v1(invalid)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

    def test_candidate_contract_rejects_provenance_review_and_type_drift(self) -> None:
        # given
        cases: list[tuple[str, str, dict[str, Any]]] = []
        unknown = self.ledger()
        unknown["candidates"][0]["human_reviewed"] = True
        cases.append(("self-attestation", "E_SCHEMA_UNKNOWN_FIELD", unknown))
        floating = self.ledger()
        floating["candidates"][0]["metadata"]["has_ui"] = 0.0
        cases.append(("float", "E_SCHEMA_FLOAT", floating))
        missing = self.ledger()
        del missing["candidates"][0]["license"]["sha256"]
        cases.append(("missing-license-hash", "E_SCHEMA_MISSING_FIELD", missing))
        mutable = self.ledger()
        mutable["candidates"][0]["commit"] = "main"
        cases.append(("mutable-ref", "E_DATASET_PROVENANCE", mutable))
        traversal = self.ledger()
        traversal["candidates"][0]["material"]["path"] = "../README.md"
        cases.append(("traversal", "E_DATASET_PROVENANCE", traversal))
        drift = self.ledger()
        drift["candidates"][0]["source_identity"]["material_sha256"] = "0" * 64
        cases.append(("hash-drift", "E_DATASET_PROVENANCE", drift))
        split = self.ledger()
        split["candidates"][0]["intended_split"] = "test"
        cases.append(("split-alias", "E_DATASET_SPLIT_LEAK", split))
        project_type = self.ledger()
        project_type["candidates"][0]["project_type"] = "new-project-type"
        cases.append(("new-project-type", "E_DATASET_PROVENANCE", project_type))
        metadata = self.ledger()
        metadata["candidates"][0]["metadata"]["project_size"] = "huge"
        cases.append(("invalid-metadata", "E_DATASET_PROVENANCE", metadata))
        generated = self.ledger()
        generated_candidate = generated["candidates"][0]
        generated_candidate["review_status"] = "approved"
        generated_candidate["approval_receipt"] = {
            "candidate_id": generated_candidate["record_id"],
            "reviewer_identity": "agent:generated-reviewer",
            "reviewer_kind": "generated-agent",
            "reviewed_at": "2026-08-03T08:00:00Z",
            "decision": "approved",
            "source_commit": "e6e0a38e6ca8d0ce2996544c427312533561d5c2",
            "candidate_commit": generated_candidate["commit"],
            "review_packet_sha256": "1" * 64,
            "approval_artifact_sha256": "f" * 64,
            "material_sha256": generated_candidate["material"]["sha256"],
            "license_sha256": generated_candidate["license"]["sha256"],
            "receipt_sha256": "2" * 64,
        }
        cases.append(("generated-reviewer", "E_DATASET_REVIEW", generated))

        # when / then
        for name, code, payload in cases:
            with self.subTest(name=name):
                self.assert_candidate_error(code, payload)

    def test_candidate_contract_rejects_duplicate_and_production_identities(self) -> None:
        # given
        duplicate = self.ledger()
        duplicate_candidate = copy.deepcopy(duplicate["candidates"][3])
        duplicate_candidate["record_id"] = "duplicate-source-identity"
        duplicate["candidates"][-1] = duplicate_candidate
        duplicate["candidates"].sort(key=lambda candidate: candidate["record_id"])
        overlap = self.ledger()
        overlap_candidate = overlap["candidates"][0]
        overlap_candidate["review_status"] = "unverified"
        overlap_candidate["approval_receipt"] = None

        # when / then
        self.assert_candidate_error("E_DATASET_SOURCE_DUPLICATE", duplicate)
        self.assert_candidate_error("E_DATASET_REVIEW", overlap)

    def test_candidate_file_loader_rejects_noncanonical_symlink_and_fifo(self) -> None:
        # given
        payload = self.ledger()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "candidate.json"
            canonical.write_bytes(canonical_json_bytes(payload))
            noncanonical = root / "noncanonical.json"
            noncanonical.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            linked = root / "linked.json"
            linked.symlink_to(canonical)
            fifo = root / "candidate.fifo"
            os.mkfifo(fifo)

            # when / then
            self.assertEqual(
                load_retrieval_candidate_ledger_v1(
                    canonical,
                    production_manifest=self.manifest(),
                    production_manifest_sha256=MANIFEST_FILE_SHA256,
                    review_time_not_after=REVIEW_CLOCK,
                ),
                payload,
            )
            for path, code in (
                (noncanonical, "E_DATASET_PROVENANCE"),
                (linked, "E_INPUT_PATH"),
                (fifo, "E_INPUT_PATH"),
            ):
                with self.subTest(path=path.name), self.assertRaises(ContractError) as raised:
                    load_retrieval_candidate_ledger_v1(
                        path,
                        review_time_not_after=REVIEW_CLOCK,
                    )
                self.assertEqual(raised.exception.code, code)

    def test_manifest_has_reviewed_variety_without_copied_payloads(self) -> None:
        payload = self.manifest()
        result = validate_dataset_manifest(payload)
        records = payload["records"]

        self.assertEqual(
            result["manifest_sha256"],
            "eb01d97e93c448ce893095bb7b2389db63bc449aab803e2c5f7c92f319be7c73",
        )
        self.assertEqual(result["record_count"], 22)
        self.assertEqual(result["split_counts"], {"test": 2, "train": 20})
        self.assertEqual(
            Counter(record["project_types"][0] for record in records if record["split"] == "train"),
            {"developer-tool": 5, "library": 5, "runtime-toolchain": 5, "web-framework": 5},
        )
        self.assertEqual(
            {record["record_id"] for record in records if record["split"] == "test"},
            {"nextjs-route-map", "pytest-outcome-first"},
        )
        self.assertTrue(APPROVED_IDS <= {record["record_id"] for record in records})
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
        self.assertEqual(hashlib.sha256(MANIFEST.read_bytes()).hexdigest(), MANIFEST_FILE_SHA256)


if __name__ == "__main__":
    unittest.main()
