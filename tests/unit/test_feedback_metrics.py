from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import canonical_json_bytes
from skill.scripts.readme_showcase.contracts.feedback import build_feedback_event
from skill.scripts.readme_showcase.evaluation.feedback_metrics import (
    METRIC_NAMES,
    aggregate_feedback,
)
from skill.scripts.readme_showcase.retrieval.feedback_ranker import (
    MAX_ADVISORY_BPS,
    advisory_delta,
    apply_feedback_signal,
)
from skill.scripts.readme_showcase.retrieval.service import retrieve_patterns_v2


ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "tests/fixtures/feedback/events.jsonl"
RUN = "a" * 64
FINGERPRINT = "b" * 64
BINDINGS = {(RUN, FINGERPRINT): {"split": "train"}}


def event(event_type: str, details: dict[str, object], second: int) -> dict[str, object]:
    return build_feedback_event(
        run_id=RUN,
        fingerprint=FINGERPRINT,
        event=event_type,
        recorded_at=f"2026-08-04T12:01:{second:02d}Z",
        details=details,
    )


def base_results() -> list[dict[str, object]]:
    return [
        {"record_id": "safe-b", "score_basis_points": 5000, "source_split": "train", "eligible": True},
        {"record_id": "safe-a", "score_basis_points": 5000, "source_split": "train", "eligible": True},
        {"record_id": "unsafe", "score_basis_points": 6000, "source_split": "train", "eligible": False},
        {"record_id": "test-only", "score_basis_points": 9000, "source_split": "test", "eligible": True},
    ]


class FeedbackMetricsTests(unittest.TestCase):
    def fixture_events(self) -> list[dict[str, object]]:
        return [json.loads(line) for line in FIXTURE.read_text(encoding="utf-8").splitlines()]

    def test_fixture_exact_count_pairs_and_canonical_shuffle_duplicate_invariance(self) -> None:
        events = self.fixture_events()
        first = aggregate_feedback(events, bindings=BINDINGS)
        second = aggregate_feedback(list(reversed(events)) + [copy.deepcopy(events[0])], bindings=BINDINGS)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["metrics"], {
            "pattern_acceptance": {"numerator": 2, "denominator": 4, "rate_bps": 5000, "status": "ok", "reasons": []},
            "section_removal": {"numerator": 1, "denominator": 2, "rate_bps": 5000, "status": "ok", "reasons": []},
            "asset_rejection": {"numerator": 2, "denominator": 2, "rate_bps": 10000, "status": "ok", "reasons": []},
            "manual_edit_distance": {"numerator": 20, "denominator": 200, "rate_bps": 1000, "status": "ok", "reasons": []},
            "pr_merge": {"numerator": 1, "denominator": 2, "rate_bps": 5000, "status": "ok", "reasons": []},
        })
        self.assertEqual(first["record_metrics"], {
            "django-docs-learning-route": {"numerator": 0, "denominator": 2, "rate_bps": 0, "status": "ok", "reasons": []},
            "rails-mvc-first-run": {"numerator": 2, "denominator": 2, "rate_bps": 10000, "status": "ok", "reasons": []},
        })
        self.assertEqual(second["ignored"], {})

    def test_zero_and_insufficient_totals_are_not_applicable(self) -> None:
        zero = aggregate_feedback([], bindings=BINDINGS)
        self.assertEqual(tuple(zero["metrics"]), METRIC_NAMES)
        self.assertTrue(all(metric["status"] == "not_applicable" and metric["reasons"] == [{"code": "zero_total"}] for metric in zero["metrics"].values()))
        one = aggregate_feedback([event("asset-rejected", {"asset_ids": ["asset:old"], "rejected_ids": ["asset:old"]}, 1)], bindings=BINDINGS)
        self.assertEqual(one["metrics"]["asset_rejection"], {"numerator": 1, "denominator": 1, "status": "not_applicable", "reasons": [{"code": "insufficient_sample"}]})
        multi_asset = event("asset-rejected", {"asset_ids": ["asset:one", "asset:two"], "rejected_ids": ["asset:one", "asset:two"]}, 12)
        huge_edit = event("candidate-edited", {"manual_edit_distance": {"changed": 0, "total": 1_000_000_000}}, 13)
        bounded = aggregate_feedback([multi_asset, huge_edit], bindings=BINDINGS)
        self.assertEqual(bounded["metrics"]["asset_rejection"]["status"], "not_applicable")
        self.assertEqual(bounded["metrics"]["manual_edit_distance"]["status"], "not_applicable")
        self.assertIsNone(advisory_delta(bounded["metrics"]))

    def test_invalid_unbound_test_and_colliding_events_are_ignored_without_partial_count(self) -> None:
        valid = event("preview-approved", {"pattern_ids": ["pattern:hero"], "accepted_ids": ["pattern:hero"]}, 2)
        malformed = copy.deepcopy(valid)
        malformed["details"] = {"accepted_ids": ["pattern:hero"], "manual_edit_distance": {"changed": True, "total": 1}}
        unbound = build_feedback_event(run_id="c" * 64, fingerprint=FINGERPRINT, event="preview-approved", recorded_at="2026-08-04T12:01:03Z", details={"pattern_ids": ["pattern:other"], "accepted_ids": ["pattern:other"]})
        split_event = build_feedback_event(run_id="d" * 64, fingerprint=FINGERPRINT, event="preview-approved", recorded_at="2026-08-04T12:01:04Z", details={"pattern_ids": ["pattern:test"], "accepted_ids": ["pattern:test"]})
        bindings = {**BINDINGS, ("d" * 64, FINGERPRINT): {"split": "test"}}
        result = aggregate_feedback([valid, copy.deepcopy(valid), malformed, unbound, split_event, copy.deepcopy(split_event)], bindings=bindings)
        self.assertEqual(result["ignored"], {"duplicate_collision": 1, "test_split_event": 1, "unbound_event": 1})
        self.assertEqual(result["metrics"]["pattern_acceptance"]["denominator"], 1)

    def test_terminal_pr_only_overlap_rejection_and_exact_large_integer_aggregation(self) -> None:
        opened = event("pr-opened", {"pr_number": 1, "pr_outcome": "opened"}, 5)
        closed = event("pr-closed", {"pr_number": 2, "pr_outcome": "closed"}, 6)
        merged = event("pr-merged", {"pr_number": 3, "pr_outcome": "merged"}, 7)
        overlap = event("preview-approved", {"pattern_ids": ["pattern:hero"], "accepted_ids": ["pattern:hero"], "rejected_ids": ["pattern:hero"]}, 8)
        huge = [event("candidate-edited", {"manual_edit_distance": {"changed": 1_000_000_000, "total": 1_000_000_000}}, second) for second in (9, 10, 11)]
        result = aggregate_feedback([opened, closed, merged, overlap, *huge], bindings=BINDINGS)
        self.assertEqual(result["metrics"]["pr_merge"], {"numerator": 1, "denominator": 2, "rate_bps": 5000, "status": "ok", "reasons": []})
        self.assertEqual(result["metrics"]["manual_edit_distance"]["denominator"], 3_000_000_000)
        self.assertEqual(result["ignored"], {"overlapping_feedback_ids": 1})

    def test_service_feedback_is_schema_safe_and_no_feedback_is_byte_exact(self) -> None:
        manifest = json.loads((ROOT / "dataset/retrieval/manifest.json").read_text(encoding="utf-8"))
        query = {"project_type": "web-framework", "sections": ["overview", "quick-start"], "tags": ["api", "observable-output"], "manifest_features": ["local server", "generated interface"], "evidence_sha256": "a" * 64}
        base = retrieve_patterns_v2(manifest, query)
        no_feedback = retrieve_patterns_v2(manifest, query, feedback_events=[], feedback_bindings=BINDINGS)
        signaled = retrieve_patterns_v2(manifest, query, feedback_events=self.fixture_events(), feedback_bindings=BINDINGS)
        self.assertEqual(canonical_json_bytes(base), canonical_json_bytes(no_feedback))
        self.assertTrue(all(set(record) == set(base["records"][0]) for record in signaled["records"]))
        self.assertTrue(all(record["source_split"] == "train" for record in signaled["records"]))
        self.assertNotEqual(canonical_json_bytes(signaled), canonical_json_bytes(base))
        base_ids = [record["record_id"] for record in base["records"]]
        signaled_ids = [record["record_id"] for record in signaled["records"]]
        self.assertLess(base_ids.index("django-docs-learning-route"), base_ids.index("rails-mvc-first-run"))
        self.assertLess(signaled_ids.index("rails-mvc-first-run"), signaled_ids.index("django-docs-learning-route"))

    def test_no_feedback_preserves_base_object_and_bytes(self) -> None:
        base = base_results()
        metrics = aggregate_feedback([], bindings=BINDINGS)["metrics"]
        ranked = apply_feedback_signal(base, metrics)
        self.assertIs(ranked, base)
        self.assertEqual(canonical_json_bytes(ranked), canonical_json_bytes(base))

    def test_bounded_advisory_preserves_evidence_and_never_promotes_unsafe_or_test_records(self) -> None:
        metrics = aggregate_feedback(self.fixture_events(), bindings=BINDINGS)["metrics"]
        base = base_results()
        ranked = apply_feedback_signal(base, metrics)
        self.assertEqual(advisory_delta(metrics), 90)
        self.assertTrue(all(item["score_basis_points"] == next(base_item["score_basis_points"] for base_item in base if base_item["record_id"] == item["record_id"]) for item in ranked))
        self.assertTrue(all(abs(item["feedback_advisory_basis_points"]) <= MAX_ADVISORY_BPS for item in ranked))
        unsafe = next(item for item in ranked if item["record_id"] == "unsafe")
        self.assertEqual((unsafe["eligible"], unsafe["feedback_advisory_basis_points"], unsafe["adjusted_score_basis_points"]), (False, 0, 6000))
        self.assertNotIn("test-only", {item["record_id"] for item in ranked})
        self.assertEqual([item["record_id"] for item in ranked], ["unsafe", "safe-a", "safe-b"])

    def test_extreme_feedback_is_capped_and_rank_order_is_deterministic(self) -> None:
        extreme = {
            name: {"numerator": 2, "denominator": 2, "rate_bps": 10000, "status": "ok", "reasons": []}
            for name in METRIC_NAMES
        }
        extreme["manual_edit_distance"] = {"numerator": 0, "denominator": 2, "rate_bps": 0, "status": "ok", "reasons": []}
        self.assertEqual(advisory_delta(extreme), MAX_ADVISORY_BPS)
        first = apply_feedback_signal(base_results(), extreme)
        second = apply_feedback_signal(list(reversed(base_results())), extreme)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual([item["record_id"] for item in first[-2:]], ["safe-a", "safe-b"])


if __name__ == "__main__":
    unittest.main()
