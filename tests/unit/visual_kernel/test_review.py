import json
import os
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.readme_showcase.visual_kernel import review
from skill.scripts.readme_showcase.visual_kernel.review import load_rubric, review_screenshots

RUBRIC = Path(__file__).resolve().parents[3] / "skill" / "references" / "visual-review-rubric.md"

# Environment isolation: these vars must not leak from the host environment,
# or the test could take the API path instead of host mode.
_REVIEW_ENV_NAMES = (
    "VISION_REVIEW_API_KEY",
    "VISION_REVIEW_MODEL",
    "VISION_REVIEW_API_BASE",
    "ANTHROPIC_MODEL",
    "OPENCODE_MODEL",
    "OPENCODE_MODEL_ID",
    "CODEX_MODEL",
)


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self._saved = {name: os.environ.get(name) for name in _REVIEW_ENV_NAMES}
        for name in _REVIEW_ENV_NAMES:
            os.environ.pop(name, None)

    def tearDown(self):
        for name, value in self._saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value

    def test_load_rubric_has_frozen_criteria(self):
        criteria = load_rubric(str(RUBRIC))
        names = [c["name"] for c in criteria]
        self.assertIn("composition_hierarchy", names)
        self.assertIn("slop_absence", names)

    def test_auto_mode_writes_host_brief_without_api_key(self):
        out = Path(__file__).resolve().parent / "review-out"
        out.mkdir(exist_ok=True)
        try:
            # api_key_env override + cleared env: host mode is guaranteed even
            # if the outer environment carries review credentials.
            report = review_screenshots(
                [str(out / "hero.png")], str(out),
                api_key_env="DEFINITELY_MISSING_KEY")
            self.assertEqual(report["status"], "host")
            self.assertEqual(report["reviewer_model"], "host-session")
            self.assertTrue((out / "vision-review-brief.md").exists())
            brief = (out / "vision-review-brief.md").read_text(encoding="utf-8")
            self.assertIn("composition_hierarchy", brief)
        finally:
            (out / "vision-review-brief.md").unlink(missing_ok=True)
            (out / "vision-review-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    def test_host_mode_carries_same_model_note(self):
        # M7: host-session review is same-model by construction -> the report
        # must carry the informational E_REVIEW_SAME_MODEL note.
        out = Path(__file__).resolve().parent / "review-out-note"
        out.mkdir(exist_ok=True)
        try:
            report = review_screenshots(
                [str(out / "hero.png")], str(out),
                api_key_env="DEFINITELY_MISSING_KEY")
            self.assertTrue(any(
                f["code"] == "E_REVIEW_SAME_MODEL" for f in report["findings"]
            ), report["findings"])
        finally:
            (out / "vision-review-brief.md").unlink(missing_ok=True)
            (out / "vision-review-report.v1.json").unlink(missing_ok=True)
            out.rmdir()

    def test_missing_screenshots_skips(self):
        report = review_screenshots([], str(Path(__file__).resolve().parent),
                                    api_key_env="DEFINITELY_MISSING_KEY")
        self.assertEqual(report["status"], "skipped")
        self.assertTrue(any("E_REVIEW_SKIPPED" in f["message"] for f in report["findings"]))

    def test_report_schema_shape(self):
        report = review_screenshots([], str(Path(__file__).resolve().parent),
                                    api_key_env="DEFINITELY_MISSING_KEY")
        self.assertEqual(report["schema_version"], 1)
        self.assertIn("criteria", report)
        self.assertIn("pairwise", report)
        self.assertIn("candidate_win_basis_points", report["pairwise"])

    def test_malformed_api_response_falls_back_to_host(self):
        # M: a 200 response without the standard "choices" shape (e.g. a
        # gateway {"error": ...}) must not block; it falls back to host mode
        # with an E_REVIEW_PARSE finding.
        out = Path(__file__).resolve().parent / "review-out-malformed"
        out.mkdir(exist_ok=True)
        png = out / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        os.environ["VISION_REVIEW_API_KEY"] = "test-key"
        try:
            with mock.patch.object(review, "_chat_completion",
                                   return_value={"error": {"message": "bad gateway"}}):
                report = review_screenshots([str(png)], str(out), model="gpt-4o")
            self.assertEqual(report["status"], "host")
            self.assertEqual(report["reviewer_model"], "host-session")
            self.assertTrue(any(
                f["code"] == "E_REVIEW_PARSE" for f in report["findings"]
            ), report["findings"])
        finally:
            os.environ.pop("VISION_REVIEW_API_KEY", None)
            (out / "vision-review-brief.md").unlink(missing_ok=True)
            (out / "vision-review-report.v1.json").unlink(missing_ok=True)
            png.unlink(missing_ok=True)
            out.rmdir()

    def test_non_list_criteria_falls_back_to_host(self):
        # Hardened extraction: a 200 response whose parsed body is not a dict
        # (or whose "criteria" is not a list) -> E_REVIEW_PARSE host fallback,
        # never an invalid report file.
        out = Path(__file__).resolve().parent / "review-out-criteria"
        out.mkdir(exist_ok=True)
        png = out / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        os.environ["VISION_REVIEW_API_KEY"] = "test-key"
        try:
            content = json.dumps({"criteria": "not-a-list"})
            with mock.patch.object(review, "_chat_completion",
                                   return_value={"choices": [{"message": {"content": content}}]}):
                report = review_screenshots([str(png)], str(out), model="gpt-4o")
            self.assertEqual(report["status"], "host")
            self.assertTrue(any(
                f["code"] == "E_REVIEW_PARSE" for f in report["findings"]
            ), report["findings"])
        finally:
            os.environ.pop("VISION_REVIEW_API_KEY", None)
            (out / "vision-review-brief.md").unlink(missing_ok=True)
            (out / "vision-review-report.v1.json").unlink(missing_ok=True)
            png.unlink(missing_ok=True)
            out.rmdir()

    def test_schema_violating_report_falls_back_to_host(self):
        # Model-controlled criteria passing the shape checks but violating the
        # closed schema (out-of-range basis points) must fall back to host mode
        # instead of writing an invalid vision-review-report.v1.json.
        out = Path(__file__).resolve().parent / "review-out-schema"
        out.mkdir(exist_ok=True)
        png = out / "hero.png"
        png.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        os.environ["VISION_REVIEW_API_KEY"] = "test-key"
        try:
            content = json.dumps({
                "criteria": [{
                    "name": "composition_hierarchy", "pass": True,
                    "score_basis_points": 99999,
                    "evidence": [{"screenshot": "hero.png", "region": "0,0,10,10"}],
                    "reason": "fine",
                }],
                "candidate_win_basis_points": None,
            })
            with mock.patch.object(review, "_chat_completion",
                                   return_value={"choices": [{"message": {"content": content}}]}):
                report = review_screenshots([str(png)], str(out), model="gpt-4o")
            self.assertEqual(report["status"], "host")
            parse_findings = [f for f in report["findings"] if f["code"] == "E_REVIEW_PARSE"]
            self.assertTrue(parse_findings, report["findings"])
            self.assertIn("schema validation", parse_findings[0]["message"])
        finally:
            os.environ.pop("VISION_REVIEW_API_KEY", None)
            (out / "vision-review-brief.md").unlink(missing_ok=True)
            (out / "vision-review-report.v1.json").unlink(missing_ok=True)
            png.unlink(missing_ok=True)
            out.rmdir()
