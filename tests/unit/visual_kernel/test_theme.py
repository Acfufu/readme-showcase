from __future__ import annotations

import json
import subprocess
import sys
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel import theme as theme_module
from skill.scripts.readme_showcase.visual_kernel.theme import Theme, resolve_theme


class ThemeResolutionTests(unittest.TestCase):
    def test_public_surface_and_default_policy_are_closed(self) -> None:
        self.assertEqual(theme_module.__all__, ["Theme", "resolve_theme"])
        self.assertIsInstance(resolve_theme(), Theme)
        self.assertEqual(resolve_theme().schema_version, 1)
        self.assertEqual(resolve_theme().variants["desktop"], {"width": 1200, "render_width": 900, "min_font_size": 16})
        self.assertEqual(resolve_theme().variants["mobile"], {"width": 720, "render_width": 360, "min_font_size": 24})
        with self.assertRaises((AttributeError, TypeError)):
            resolve_theme().colors["accent"] = "#ffffff"  # type: ignore[index]

    def test_safe_project_color_override_is_canonical_and_does_not_mutate_input(self) -> None:
        tokens = {"colors": {"accent": "#22C55E"}}
        first = resolve_theme(tokens)
        tokens["colors"]["accent"] = "#000000"
        second = resolve_theme({"colors": {"accent": "#22c55e"}})
        self.assertEqual(first.colors["accent"], "#22c55e")
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.as_dict()["colors"]["accent"], "#22c55e")

    def test_contrast_and_trust_boundaries_fail_closed(self) -> None:
        cases = (
            ({"colors": {"text": "#121212"}}, "E_SCHEMA_VALUE"),
            ({"colors": {"accent": "https://example.invalid/a.svg"}}, "E_VISUAL_PATH"),
            ({"colors": {"unknown": "#22c55e"}}, "E_SCHEMA_UNKNOWN_FIELD"),
            ({"font": "system-ui"}, "E_VISUAL_RESOURCE"),
            ({"colors": {"accent": "../accent.svg"}}, "E_VISUAL_PATH"),
            ({"coordinates": {"desktop": {"x": 1}}}, "E_VISUAL_GEOMETRY"),
        )
        for tokens, code in cases:
            with self.subTest(tokens=tokens):
                with self.assertRaises(ContractError) as raised:
                    resolve_theme(tokens)
                self.assertEqual(raised.exception.code, code)

    def test_variant_policy_rejects_reused_desktop_coordinates(self) -> None:
        desktop = {"width": 1200, "render_width": 900, "min_font_size": 16}
        mobile = dict(desktop)
        with self.assertRaises(ContractError) as raised:
            Theme(1, resolve_theme().colors, resolve_theme().spacing, resolve_theme().strokes, resolve_theme().text, {"desktop": desktop, "mobile": mobile})
        self.assertEqual(raised.exception.code, "E_VISUAL_GEOMETRY")

    def test_variant_minimum_applies_to_every_text_role(self) -> None:
        theme = resolve_theme()
        for variant in ("desktop", "mobile"):
            minimum = theme.variants[variant]["min_font_size"]
            required = 16 if variant == "desktop" else 24
            self.assertGreaterEqual(minimum, required)
            for role, base_size in theme.text.items():
                self.assertGreaterEqual(max(base_size, minimum), required, (variant, role))

    def test_canonical_projection_is_json_and_fresh_process_stable(self) -> None:
        result = resolve_theme({"colors": {"accent": "#22c55e"}})
        self.assertEqual(json.loads(result.canonical_bytes()), result.as_dict())
        code = (
            "from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme; "
            "print(resolve_theme({'colors': {'accent': '#22c55e'}}).canonical_bytes().decode(), end='')"
        )
        environment = {"PYTHONDONTWRITEBYTECODE": "1"}
        first = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment)
        second = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
