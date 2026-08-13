from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.contracts.evaluation import validate_evaluation_report_v3
from skill.scripts.readme_showcase.contracts.evidence import build_fact, validate_fact
from skill.scripts.readme_showcase.evaluation.voice import (
    collect_voice_samples,
    evaluate_voice_match,
    voice_match_check,
)
from skill.scripts.readme_showcase.scanner.voice import extract_voice_samples
from skill.scripts.pipeline_contracts import ContractError


CLI_VOICE = (
    "Build the CLI. Run the tests. Install from source. Ship it fast. "
    "Write clean code. Keep it simple. Add more features. Fix the bugs. "
    "Repeat the process. Stay focused. Test everything. Ship weekly."
)
MARKETING_VOICE = (
    "This is the future of productivity, a revolutionary platform that transforms the way you work forever. "
    "It delivers an unparalleled experience that elevates every workflow to new heights of excellence. "
    "Our solution empowers teams with cutting-edge technology that redefines what is possible today. "
    "The journey begins with a seamless onboarding designed around the needs of every single user. "
    "We have crafted an ecosystem where innovation meets reliability at every point of the journey. "
    "You will discover a new standard of performance that exceeds every expectation you have. "
    "Every feature is engineered with passion to delight customers across the entire globe. "
    "Your organization deserves the best, and we are here to deliver it every single day."
)


def _git(root: Path, *arguments: str) -> None:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    environment.update(GIT_OPTIONAL_LOCKS="0", LC_ALL="C", GIT_AUTHOR_NAME="test", GIT_AUTHOR_EMAIL="test@example.com", GIT_COMMITTER_NAME="test", GIT_COMMITTER_EMAIL="test@example.com")
    result = subprocess.run(
        ["git", "-C", str(root), *arguments],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
        env=environment,
    )
    if result.returncode:
        raise AssertionError(f"git {arguments[0]} failed: {result.stderr.decode('utf-8', 'replace')}")


class VoiceFeatureTests(unittest.TestCase):
    def test_marketing_candidate_fails_against_cli_voice_samples(self) -> None:
        result = voice_match_check(MARKETING_VOICE, _cli_samples())
        self.assertFalse(result["pass"])
        self.assertLess(result["score"], 0.5)
        self.assertTrue(result["evidence"])

    def test_cli_candidate_passes_against_cli_voice_samples(self) -> None:
        result = voice_match_check(CLI_VOICE, _cli_samples())
        self.assertTrue(result["pass"])
        # score is 1 / (1 + distance); a pass stays above the 2-sigma boundary.
        self.assertGreater(result["score"], 1 / 3)
        self.assertTrue(result["evidence"])

    def test_non_native_locale_downgrades_failed_match_to_advisory(self) -> None:
        hard = evaluate_voice_match(MARKETING_VOICE, _cli_samples(), "en")
        self.assertFalse(hard["pass"])
        advisory = evaluate_voice_match(MARKETING_VOICE, _cli_samples(), "zh-Hans")
        self.assertTrue(advisory["pass"])
        self.assertIn("advisory", advisory["evidence"])
        self.assertLess(advisory["score"], 0.5)

    def test_insufficient_voice_samples_never_hard_fail(self) -> None:
        sparse = {
            "script": "latin",
            "lengths": [3, 4],
            "imperative_count": 2,
            "term_count": 0,
            "sentence_count": 2,
            "sources": ["voice-sample:readme-prose"],
        }
        result = evaluate_voice_match(MARKETING_VOICE, sparse, "en")
        self.assertTrue(result["pass"])
        self.assertIn("insufficient", result["evidence"])

    def test_cjk_skipped_marker_passes_with_explicit_evidence(self) -> None:
        samples = {"script": "cjk", "skipped": True, "sentence_count": 0}
        result = evaluate_voice_match("任何候选文本", samples, "zh-Hans")
        self.assertTrue(result["pass"])
        self.assertIn("cjk voice matching skipped", result["evidence"])

    def test_collect_voice_samples_propagates_cjk_skipped_marker(self) -> None:
        fact = build_fact(
            kind="voice-sample",
            path="README.md",
            locator={"line_start": 1, "line_end": 2},
            semantic_key="voice-sample:readme-prose",
            value={
                "script": "cjk",
                "sentences": [],
                "imperative_count": 0,
                "term_count": 0,
                "skipped": True,
            },
            source_bytes="这是一个中文项目。\n".encode("utf-8"),
            confidence="derived",
            derivation="voice features derived from README prose",
        )
        graph = {
            "schema_version": 2,
            "facts": [fact],
        }
        from skill.scripts.readme_showcase.contracts.evidence import compute_graph_sha256

        graph["evidence_sha256"] = compute_graph_sha256(graph)
        collected = collect_voice_samples(graph)
        self.assertEqual(collected.get("skipped"), True)
        self.assertEqual(collected.get("script"), "cjk")
        self.assertEqual(collected.get("sentence_count"), 0)

    def test_voice_match_is_evidence_driven_without_repository_access(self) -> None:
        samples = _cli_samples()
        facts = [
            build_fact(
                kind="voice-sample",
                path="README.md",
                locator={"line_start": 1, "line_end": 12},
                semantic_key="voice-sample:readme-prose",
                value={
                    "script": samples["script"],
                    "sentences": samples["lengths"],
                    "imperative_count": samples["imperative_count"],
                    "term_count": samples["term_count"],
                },
                source_bytes=CLI_VOICE.encode("utf-8"),
                confidence="derived",
                derivation="voice features derived from README prose",
            )
        ]
        graph = {
            "schema_version": 2,
            "facts": facts,
        }
        from skill.scripts.readme_showcase.contracts.evidence import compute_graph_sha256

        graph["evidence_sha256"] = compute_graph_sha256(graph)
        collected = collect_voice_samples(graph)
        self.assertEqual(collected["sentence_count"], samples["sentence_count"])
        self.assertEqual(collected["script"], "latin")
        result = evaluate_voice_match(MARKETING_VOICE, collected, "en")
        self.assertFalse(result["pass"])


class VoiceScanExtractionTests(unittest.TestCase):
    def test_scan_extracts_voice_sample_facts_from_readme_changelog_and_git_log(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text(
                "Build the CLI.\nRun the tests.\n\nInstall from source.\nShip it fast.\n", encoding="utf-8"
            )
            (root / "CHANGELOG.md").write_text(
                "## 1.0.0\n- Add the CLI.\n- Fix the tests.\n\n## 0.9.0\n- Ship faster.\n", encoding="utf-8"
            )
            _git(root, "init", "-b", "main")
            _git(root, "add", "README.md", "CHANGELOG.md")
            _git(root, "commit", "-m", "add the cli")
            _git(root, "commit", "--allow-empty", "-m", "fix the tests")

            facts = extract_voice_samples(root)
            keys = sorted(fact["semantic_key"] for fact in facts)
            self.assertIn("voice-sample:readme-prose", keys)
            self.assertIn("voice-sample:changelog", keys)
            self.assertIn("voice-sample:commit-subjects", keys)
            for fact in facts:
                self.assertEqual(validate_fact(fact), fact)
                self.assertEqual(fact["kind"], "voice-sample")
                self.assertEqual(fact["confidence"], "derived")
                self.assertIn("derivation", fact)
                value = fact["value"]
                self.assertEqual(value["script"], "latin")
                self.assertTrue(all(type(item) is int for item in value["sentences"]))
                self.assertIn("imperative_count", value)
                self.assertIn("term_count", value)
            prose = next(fact for fact in facts if fact["semantic_key"] == "voice-sample:readme-prose")
            self.assertGreaterEqual(sum(prose["value"]["sentences"]), 4)

    def test_scan_emits_skipped_fact_for_cjk_readme(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text(
                "这是一个中文项目。\n它提供了完整的命令行工具。\n", encoding="utf-8"
            )
            facts = extract_voice_samples(root)
            self.assertEqual([fact["semantic_key"] for fact in facts], ["voice-sample:readme-prose"])
            fact = facts[0]
            self.assertEqual(validate_fact(fact), fact)
            value = fact["value"]
            self.assertEqual(value["script"], "cjk")
            self.assertEqual(value["sentences"], [])
            self.assertIs(value["skipped"], True)

    def test_scan_without_git_still_extracts_file_based_voice_facts(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "README.md").write_text(
                "Build the CLI.\nRun the tests.\n\nInstall from source.\nShip it fast.\n", encoding="utf-8"
            )
            facts = extract_voice_samples(root)
            keys = [fact["semantic_key"] for fact in facts]
            self.assertEqual(keys, ["voice-sample:readme-prose"])
            self.assertTrue(all(validate_fact(fact) == fact for fact in facts))


class VoiceReportContractTests(unittest.TestCase):
    def _report(self, voice_match: dict[str, object]) -> dict[str, object]:
        with open("tests/fixtures/contracts/evaluation-report-v3.valid.json", encoding="utf-8") as handle:
            report = json.load(handle)
        report["voice_match"] = voice_match
        return report

    def test_valid_report_accepts_bounded_integer_score(self) -> None:
        report = self._report({"pass": True, "score": 10000, "evidence": "fixture voice match"})
        self.assertEqual(validate_evaluation_report_v3(report)["voice_match"]["score"], 10000)

    def test_report_rejects_out_of_range_score(self) -> None:
        report = self._report({"pass": True, "score": 15000, "evidence": "fixture voice match"})
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")

    def test_report_rejects_float_anywhere_including_voice_match(self) -> None:
        report = self._report({"pass": True, "score": 1.0, "evidence": "fixture voice match"})
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_SCHEMA_FLOAT")
        report = self._report({"pass": True, "score": 10000, "evidence": "fixture voice match"})
        report["advisory"]["claim_coverage"]["covered"] = 1.5
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_SCHEMA_FLOAT")

    def test_passing_report_requires_voice_match_pass(self) -> None:
        report = self._report({"pass": False, "score": 2000, "evidence": "fixture voice mismatch"})
        with self.assertRaises(ContractError) as caught:
            validate_evaluation_report_v3(report)
        self.assertEqual(caught.exception.code, "E_EVALUATION_REPORT")


def _cli_samples() -> dict[str, object]:
    """Voice samples consistent with a terse CLI-voice repository."""
    lengths = [3, 3, 4, 3, 3, 4, 4, 3, 4, 3, 4, 3]
    return {
        "script": "latin",
        "lengths": lengths,
        "imperative_count": 10,
        "term_count": 2,
        "sentence_count": len(lengths),
        "sources": ["voice-sample:readme-prose"],
    }


if __name__ == "__main__":
    unittest.main()
