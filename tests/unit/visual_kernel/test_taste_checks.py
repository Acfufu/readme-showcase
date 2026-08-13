import tempfile
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

    def test_density_cap_not_applied_to_non_hero_svg(self):
        # The density cap is scoped to the 1200x360 hero (visual-taste.md §5):
        # a workflow/diagram SVG with many labels but different dimensions
        # must not fire the density finding.
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir) / "non-hero-dense.svg"
            texts = "\n".join(
                f'  <text x="20" y="{10 + i * 20}" font-size="{16 + i}" '
                f'fill="#ffffff">Label {i}</text>'
                for i in range(8)
            )
            tmp.write_text(
                '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="900" '
                'viewBox="0 0 1600 900" role="img">'
                '<rect width="1600" height="900" fill="#0a1f44"/>\n'
                + texts + "\n</svg>",
                encoding="utf-8")
            findings = check_taste_rules(str(tmp))
            self.assertFalse(any("density" in f for f in findings), findings)
        # The hero fixture (1200x360) still fires the cap.
        hero_findings = check_taste_rules(str(FIXTURES / "hero-dense.svg"))
        self.assertTrue(any("density" in f for f in hero_findings))
