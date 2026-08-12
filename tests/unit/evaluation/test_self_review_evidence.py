from __future__ import annotations

import unittest
from typing import Any

from skill.scripts.readme_showcase.evaluation.self_review import (
    MAX_SELF_REVIEW_REVISIONS,
    collect_evidence_facts,
    self_review_check,
)


def _fact(fact_id: str) -> dict[str, Any]:
    return {
        "fact_id": fact_id,
        "kind": "file-presence",
        "source": {"path": "README.md"},
        "semantic_key": "presence",
        "value": True,
        "source_sha256": "a" * 64,
        "evidence_sha256": "b" * 64,
        "confidence": "observed",
    }


def _claim(claim_id: str, *, evidence_ids: list[str], claim_kind: str = "factual") -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "content_sha256": "c" * 64,
        "claim_kind": claim_kind,
        "evidence_ids": evidence_ids,
        "language_pair_id": None,
        "support_level": "direct",
    }


class SelfReviewEvidenceTests(unittest.TestCase):
    """Self-review must compare candidate claims against repository-evidence
    facts, never against the candidate README itself."""

    def test_claim_referencing_unknown_fact_fails(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {"markdown_blocks": [_claim("markdown:en:overview", evidence_ids=["file:" + "2" * 64])], "diagram_labels": []}
        result = self_review_check(claims, facts)
        self.assertFalse(result["pass"])
        self.assertEqual(result["uncovered"], ["markdown:en:overview"])
        self.assertIn("markdown:en:overview", result["evidence"])

    def test_claim_without_any_evidence_entry_fails(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {"markdown_blocks": [_claim("markdown:en:overview", evidence_ids=[])], "diagram_labels": []}
        result = self_review_check(claims, facts)
        self.assertFalse(result["pass"])
        self.assertEqual(result["uncovered"], ["markdown:en:overview"])

    def test_claim_with_matching_evidence_fact_passes(self) -> None:
        fact_id = "file:" + "1" * 64
        facts = collect_evidence_facts({"facts": [_fact(fact_id)]})
        claims = {"markdown_blocks": [_claim("markdown:en:overview", evidence_ids=[fact_id])], "diagram_labels": []}
        result = self_review_check(claims, facts)
        self.assertTrue(result["pass"])
        self.assertEqual(result["uncovered"], [])

    def test_diagram_label_claims_are_reviewed_against_evidence(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {"markdown_blocks": [], "diagram_labels": [_claim("diagram:en:hero", evidence_ids=["file:" + "9" * 64])]}
        result = self_review_check(claims, facts)
        self.assertFalse(result["pass"])
        self.assertEqual(result["uncovered"], ["diagram:en:hero"])

    def test_decorative_claims_need_no_evidence_correspondence(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {"markdown_blocks": [_claim("markdown:en:hero", evidence_ids=[], claim_kind="decorative")], "diagram_labels": []}
        result = self_review_check(claims, facts)
        self.assertTrue(result["pass"])

    def test_instruction_claims_are_reviewed_against_evidence(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {"markdown_blocks": [_claim("markdown:en:usage", evidence_ids=[], claim_kind="instruction")], "diagram_labels": []}
        result = self_review_check(claims, facts)
        self.assertFalse(result["pass"])

    def test_uncovered_ids_are_sorted_and_deterministic(self) -> None:
        facts = collect_evidence_facts({"facts": [_fact("file:" + "1" * 64)]})
        claims = {
            "markdown_blocks": [
                _claim("markdown:en:beta", evidence_ids=["file:" + "3" * 64]),
                _claim("markdown:en:alpha", evidence_ids=["file:" + "2" * 64]),
            ],
            "diagram_labels": [],
        }
        result = self_review_check(claims, facts)
        self.assertEqual(result["uncovered"], ["markdown:en:alpha", "markdown:en:beta"])

    def test_collect_evidence_facts_indexes_by_fact_id_and_skips_non_objects(self) -> None:
        fact_id = "file:" + "1" * 64
        facts = collect_evidence_facts({"facts": [_fact(fact_id), "not-an-object", {"fact_id": fact_id}]})
        self.assertEqual(facts, {fact_id: _fact(fact_id)})

    def test_missing_claims_pass_open_with_explanation(self) -> None:
        result = self_review_check({}, {})
        self.assertTrue(result["pass"])
        self.assertEqual(result["uncovered"], [])
        self.assertTrue(result["evidence"])

    def test_auto_revision_cap_is_one(self) -> None:
        # A failed self-review may auto-revise exactly once; the final
        # verdict then stands without further auto-revision.
        self.assertEqual(MAX_SELF_REVIEW_REVISIONS, 1)


if __name__ == "__main__":
    unittest.main()
