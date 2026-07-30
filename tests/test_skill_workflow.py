from __future__ import annotations

import re
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL = REPO_ROOT / "skill/SKILL.md"


class SkillWorkflowTests(unittest.TestCase):
    def test_one_agent_order_modes_and_routes_are_explicit(self) -> None:
        text = SKILL.read_text(encoding="utf-8")
        flat = " ".join(text.split())
        self.assertIn("name: readme-showcase", text)
        self.assertIn("one README Agent", flat)
        self.assertEqual(
            set(re.findall(r"\\*\\*(README|Asset-only|Audit-only) mode\\*\\*", text)),
            {"README", "Asset-only", "Audit-only"},
        )
        ordered_commands = (
            "validate-dataset",
            " scan ",
            " retrieve ",
            "render_glyphic.mjs",
            "validate-bundle",
            " evaluate ",
            "build-pr-bundle",
            "check-publish-gate",
        )
        positions = [flat.index(command) for command in ordered_commands]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("| `static` |", text)
        self.assertIn("| `glyphic` |", text)
        self.assertIn("raw SVG", flat)
        self.assertIn("last-known-good", flat)
        self.assertIn("Retrieval patterns are not target facts", flat)
        self.assertIn("Never delegate README truth or writing", flat)
        self.assertIn("Never publish from evaluation success alone", flat)


if __name__ == "__main__":
    unittest.main()
