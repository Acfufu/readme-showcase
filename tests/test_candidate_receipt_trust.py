from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_sha256
from skill.scripts.readme_showcase.contracts.retrieval import (
    TRUSTED_APPROVAL_ARTIFACT_SHA256,
    TRUSTED_REVIEW_PACKET_SHA256,
    TRUSTED_SOURCE_COMMIT,
    validate_retrieval_candidate_ledger_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "dataset/retrieval/manifest.json"
CANDIDATES = REPO_ROOT / "dataset/retrieval/candidates.json"
CANDIDATE_SCHEMA = REPO_ROOT / "skill/schemas/retrieval-candidate-ledger.v1.schema.json"
MANIFEST_FILE_SHA256 = "07db475c0f56022a1e6b49f7546dc6c865d8e090c38b23e980572f3cdcb50da0"
REVIEW_CLOCK = datetime(2026, 8, 3, 10, 4, 0, tzinfo=timezone.utc)


class CandidateReceiptTrustTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def ledger(self) -> dict[str, Any]:
        return json.loads(CANDIDATES.read_text(encoding="utf-8"))

    def reviewed_ledger(
        self,
        *,
        review_packet_sha256: str,
        reviewed_at: str,
    ) -> dict[str, Any]:
        payload = self.ledger()
        candidate = payload["candidates"][0]
        receipt = {
            "candidate_id": candidate["record_id"],
            "reviewer_identity": "human:acfufu",
            "reviewer_kind": "external-human",
            "reviewed_at": reviewed_at,
            "decision": "approved",
            "source_commit": TRUSTED_SOURCE_COMMIT,
            "candidate_commit": candidate["commit"],
            "review_packet_sha256": review_packet_sha256,
            "approval_artifact_sha256": TRUSTED_APPROVAL_ARTIFACT_SHA256,
            "material_sha256": candidate["material"]["sha256"],
            "license_sha256": candidate["license"]["sha256"],
        }
        receipt["receipt_sha256"] = canonical_sha256(receipt)
        candidate["review_status"] = "approved"
        candidate["approval_receipt"] = receipt
        return payload

    def assert_review_error(
        self,
        payload: dict[str, Any],
        *,
        review_time_not_after: datetime | None,
    ) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_retrieval_candidate_ledger_v1(
                payload,
                production_manifest=self.manifest(),
                production_manifest_sha256=MANIFEST_FILE_SHA256,
                review_time_not_after=review_time_not_after,
            )
        self.assertEqual(raised.exception.code, "E_DATASET_REVIEW")

    def test_self_consistent_receipt_rejects_untrusted_review_packet(self) -> None:
        payload = self.reviewed_ledger(
            review_packet_sha256="0" * 64,
            reviewed_at="2026-08-03T10:03:32Z",
        )

        self.assert_review_error(payload, review_time_not_after=REVIEW_CLOCK)

    def test_self_consistent_receipt_rejects_invalid_calendar_time(self) -> None:
        payload = self.reviewed_ledger(
            review_packet_sha256=TRUSTED_REVIEW_PACKET_SHA256,
            reviewed_at="9999-99-99T99:99:99Z",
        )

        self.assert_review_error(payload, review_time_not_after=REVIEW_CLOCK)

    def test_exact_trusted_receipt_with_real_time_is_contract_valid_only(self) -> None:
        payload = self.reviewed_ledger(
            review_packet_sha256=TRUSTED_REVIEW_PACKET_SHA256,
            reviewed_at="2026-08-03T10:03:32Z",
        )

        validated = validate_retrieval_candidate_ledger_v1(
            payload,
            production_manifest=self.manifest(),
            production_manifest_sha256=MANIFEST_FILE_SHA256,
            review_time_not_after=REVIEW_CLOCK,
        )

        self.assertEqual(validated, payload)
        persisted = self.ledger()["candidates"]
        self.assertEqual(sum(c["review_status"] == "approved" for c in persisted), 10)
        self.assertEqual(sum(c["review_status"] == "unverified" for c in persisted), 2)

    def test_receipt_rejects_missing_clock_and_times_outside_trusted_window(self) -> None:
        cases = (
            ("2026-08-03T10:03:32Z", None),
            ("2026-08-03T08:31:47Z", REVIEW_CLOCK),
            ("2026-08-03T10:04:01Z", REVIEW_CLOCK),
        )

        for reviewed_at, clock in cases:
            with self.subTest(reviewed_at=reviewed_at, clock=clock):
                payload = self.reviewed_ledger(
                    review_packet_sha256=TRUSTED_REVIEW_PACKET_SHA256,
                    reviewed_at=reviewed_at,
                )
                self.assert_review_error(payload, review_time_not_after=clock)

    def test_schema_pins_review_packet_and_real_timestamp_format(self) -> None:
        schema = json.loads(CANDIDATE_SCHEMA.read_text(encoding="utf-8"))
        receipt = schema["$defs"]["approval_receipt"]["properties"]

        self.assertEqual(
            receipt["review_packet_sha256"],
            {"const": TRUSTED_REVIEW_PACKET_SHA256},
        )
        self.assertEqual(
            receipt["approval_artifact_sha256"],
            {"const": TRUSTED_APPROVAL_ARTIFACT_SHA256},
        )
        self.assertEqual(receipt["source_commit"], {"const": TRUSTED_SOURCE_COMMIT})
        self.assertEqual(receipt["reviewed_at"]["format"], "date-time")

    def test_persisted_receipt_rejects_every_authorization_binding_drift(self) -> None:
        # given
        mutations = (
            ("reviewer_identity", "human:someone-else"),
            ("review_packet_sha256", "0" * 64),
            ("approval_artifact_sha256", "0" * 64),
            ("source_commit", "0" * 40),
            ("candidate_commit", "0" * 40),
            ("material_sha256", "0" * 64),
            ("license_sha256", "0" * 64),
            ("reviewed_at", "2026-08-03T10:04:01Z"),
            ("receipt_sha256", "0" * 64),
        )

        # when / then
        for field, value in mutations:
            with self.subTest(field=field):
                payload = self.ledger()
                receipt = payload["candidates"][0]["approval_receipt"]
                receipt[field] = value
                if field != "receipt_sha256":
                    receipt["receipt_sha256"] = canonical_sha256({
                        key: item
                        for key, item in receipt.items()
                        if key != "receipt_sha256"
                    })
                self.assert_review_error(payload, review_time_not_after=REVIEW_CLOCK)
