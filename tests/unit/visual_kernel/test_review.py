import os
import unittest
from pathlib import Path

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
