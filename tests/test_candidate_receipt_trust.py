from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_sha256
from skill.scripts.readme_showcase.contracts.retrieval import (
    TRUSTED_REVIEW_PACKET_SHA256,
    validate_retrieval_candidate_ledger_v1,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "dataset/retrieval/manifest.json"
CANDIDATES = REPO_ROOT / "dataset/retrieval/candidates.json"
CANDIDATE_SCHEMA = REPO_ROOT / "skill/schemas/retrieval-candidate-ledger.v1.schema.json"
MANIFEST_FILE_SHA256 = "96726edefe61d23ebb37ecc1212ab6ff722cc39fc3b70254cbf89825a074375f"
REVIEW_CLOCK = datetime(2026, 8, 3, 10, 0, 0, tzinfo=timezone.utc)


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
            "reviewer_identity": "human:independent-reviewer-01",
            "reviewer_kind": "external-human",
            "reviewed_at": reviewed_at,
            "decision": "approved",
            "source_commit": candidate["commit"],
            "review_packet_sha256": review_packet_sha256,
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
            reviewed_at="2026-08-03T09:00:00Z",
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
            reviewed_at="2026-08-03T09:00:00Z",
        )

        validated = validate_retrieval_candidate_ledger_v1(
            payload,
            production_manifest=self.manifest(),
            production_manifest_sha256=MANIFEST_FILE_SHA256,
            review_time_not_after=REVIEW_CLOCK,
        )

        self.assertEqual(validated, payload)
        self.assertTrue(
            all(
                candidate["review_status"] == "unverified"
                and candidate["approval_receipt"] is None
                for candidate in self.ledger()["candidates"]
            )
        )

    def test_receipt_rejects_missing_clock_and_times_outside_trusted_window(self) -> None:
        cases = (
            ("2026-08-03T09:00:00Z", None),
            ("2026-08-03T08:31:47Z", REVIEW_CLOCK),
            ("2026-08-03T10:00:01Z", REVIEW_CLOCK),
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
        self.assertEqual(receipt["reviewed_at"]["format"], "date-time")
