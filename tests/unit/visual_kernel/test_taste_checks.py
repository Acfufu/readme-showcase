import unittest
from pathlib import Path

from skill.scripts.readme_showcase.visual_kernel.checks import check_taste_rules

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "visual"


class TasteChecksTest(unittest.TestCase):
    def test_em_dash_found(self):
        findings = check_taste_rules(str(FIXTURES / "hero-em-dash.svg"))
        self.assertTrue(any("em-dash" in f for f in findings))

    def test_density_cap_violation_found(self):
        findings = check_taste_rules(str(FIXTURES / "hero-dense.svg"))
        self.assertTrue(any("density" in f for f in findings))

    def test_ok_svg_passes(self):
        self.assertEqual(check_taste_rules(str(FIXTURES / "hero-ok.svg")), [])

    def test_radius_inconsistency_found(self):
        findings = check_taste_rules(str(FIXTURES / "hero-radius-mix.svg"))
        self.assertTrue(any("radius" in f for f in findings))
