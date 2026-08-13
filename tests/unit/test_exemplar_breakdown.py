import unittest
from pathlib import Path

from skill.scripts.render_exemplar_breakdown import render_breakdown_svg

OUT = Path(__file__).resolve().parent / "breakdown-test.svg"


class BreakdownTest(unittest.TestCase):
    def tearDown(self):
        OUT.unlink(missing_ok=True)

    def test_renders_annotated_svg(self):
        render_breakdown_svg(
            {"zones": ["identity", "proof", "metadata"],
             "hierarchy": ["display", "section", "supporting"],
             "negative_patterns": ["centered-template"]},
            str(OUT),
        )
        text = OUT.read_text(encoding="utf-8")
        self.assertIn("identity", text)
        self.assertIn("negative", text)
        self.assertIn("1200", text)
