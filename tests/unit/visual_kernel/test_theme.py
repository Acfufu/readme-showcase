from __future__ import annotations

import inspect
import json
import subprocess
import sys
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel import theme as theme_module
from skill.scripts.readme_showcase.visual_kernel.theme import Theme, resolve_theme


class ThemeResolutionTests(unittest.TestCase):
    def test_resolve_theme_signature_has_no_repository_tokens(self) -> None:
        self.assertNotIn("repository_tokens", inspect.signature(resolve_theme).parameters)
        theme = resolve_theme()
        self.assertIsInstance(theme, Theme)
        self.assertEqual(theme.as_dict()["schema_version"], 1)
        self.assertEqual(dict(theme.as_dict()["colors"]), dict(theme_module._COLOR_DEFAULTS))

    def test_public_surface_and_default_policy_are_closed(self) -> None:
        self.assertEqual(theme_module.__all__, ["Theme", "resolve_theme"])
        self.assertIsInstance(resolve_theme(), Theme)
        self.assertEqual(resolve_theme().schema_version, 1)
        self.assertEqual(resolve_theme().variants["desktop"], {"width": 1200, "render_width": 900, "min_font_size": 16})
        self.assertEqual(resolve_theme().variants["mobile"], {"width": 720, "render_width": 360, "min_font_size": 24})
        with self.assertRaises((AttributeError, TypeError)):
            resolve_theme().colors["accent"] = "#ffffff"  # type: ignore[index]

    def test_default_theme_is_canonical_and_repeated_resolution_is_stable(self) -> None:
        first = resolve_theme()
        second = resolve_theme()
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_theme_validation_fails_closed(self) -> None:
        defaults = resolve_theme()
        base = {
            "schema_version": defaults.schema_version,
            "colors": dict(defaults.colors),
            "spacing": dict(defaults.spacing),
            "strokes": dict(defaults.strokes),
            "text": dict(defaults.text),
            "variants": {name: dict(policy) for name, policy in defaults.variants.items()},
        }
        cases = (
            ("colors", {"text": "#121212"}, "E_SCHEMA_VALUE"),
            ("colors", {"accent": "https://example.invalid/a.svg"}, "E_VISUAL_PATH"),
            ("colors", {"unknown": "#22c55e"}, "E_SCHEMA_UNKNOWN_FIELD"),
            ("spacing", {"font": "system-ui"}, "E_VISUAL_RESOURCE"),
            ("colors", {"accent": "../accent.svg"}, "E_VISUAL_PATH"),
            ("spacing", {"coordinates": {"desktop": {"x": 1}}}, "E_VISUAL_GEOMETRY"),
        )
        for group, tokens, code in cases:
            with self.subTest(group=group, tokens=tokens):
                payload = dict(base)
                payload[group] = {**base[group], **tokens}
                with self.assertRaises(ContractError) as raised:
                    Theme(**payload)
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
        result = resolve_theme()
        self.assertEqual(json.loads(result.canonical_bytes()), result.as_dict())
        code = (
            "from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme; "
            "print(resolve_theme().canonical_bytes().decode(), end='')"
        )
        environment = {"PYTHONDONTWRITEBYTECODE": "1"}
        first = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment)
        second = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
