import unittest
from pathlib import Path

TASTE = Path(__file__).resolve().parents[2] / "skill" / "references" / "visual-taste.md"


class TasteReferenceTest(unittest.TestCase):
    def test_sections_0_to_6_present(self):
        text = TASTE.read_text(encoding="utf-8")
        for header in ("## 0. ", "## 1. ", "## 2. ", "## 3. ", "## 4. ", "## 5. ", "## 6. "):
            self.assertIn(header, text, f"missing {header}")

    def test_sources_cited(self):
        text = TASTE.read_text(encoding="utf-8")
        for source in ("design-taste-frontend", "high-end-visual-design", "hallmark",
                       "clauswilke/dataviz", "Wikipedia", "W3C WAI"):
            self.assertIn(source, text, f"missing citation {source}")

    def test_no_em_dash_in_rules(self):
        text = TASTE.read_text(encoding="utf-8")
        self.assertNotIn("\u2014", text)
