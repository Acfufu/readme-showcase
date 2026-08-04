from __future__ import annotations

import json
import subprocess
import sys
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel import text as text_module
from skill.scripts.readme_showcase.visual_kernel.text import TextFitResult, fit_text


class DeterministicTextFitTests(unittest.TestCase):
    def test_public_surface_is_only_fit_function_and_result(self) -> None:
        self.assertEqual(text_module.__all__, ["TextFitResult", "fit_text"])
        self.assertFalse(hasattr(text_module, "measure_text"))
        self.assertFalse(hasattr(text_module, "normalize_label"))
        self.assertFalse(hasattr(text_module, "grapheme_clusters"))

    def test_nfc_and_whitespace_normalization_is_explicit(self) -> None:
        result = fit_text("  Cafe\u0301\t  API\nflow  ", width=1000, role="core", variant="desktop")
        self.assertEqual(result.text, "Café API flow")

    def test_latin_exact_fit_is_stable(self) -> None:
        label = "Request"
        measured = fit_text(label, width=1000, role="core", variant="desktop")
        width = measured.widths[0]
        first = fit_text(label, width=width, role="core", variant="desktop")
        second = fit_text(label, width=width, role="core", variant="desktop")
        self.assertIsInstance(first, TextFitResult)
        self.assertEqual(first.status, "fit")
        self.assertEqual(first.lines, (label,))
        self.assertEqual(first.widths, (width,))
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())

    def test_cjk_and_punctuation_use_conservative_widths(self) -> None:
        latin = fit_text("API", width=1000, role="node", variant="desktop")
        cjk = fit_text("请求", width=1000, role="node", variant="desktop")
        punctuation = fit_text("：", width=1000, role="node", variant="desktop")
        self.assertGreater(cjk.widths[0], latin.widths[0] // 2)
        self.assertGreater(punctuation.widths[0], 0)
        cjk_label = fit_text("请求：完成", width=1000, role="node", variant="mobile")
        self.assertEqual(
            fit_text("请求：完成", width=cjk_label.widths[0], role="node", variant="mobile").lines,
            ("请求：完成",),
        )

    def test_combining_and_zwj_sequences_are_not_split(self) -> None:
        label = "Cafe\u0301 👩\u200d💻"
        width = fit_text("Café", width=1000, role="edge", variant="desktop").widths[0]
        result = fit_text(label, width=width, role="edge", variant="desktop")
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.text, "Café 👩\u200d💻")
        self.assertIn("👩\u200d💻", result.lines)
        self.assertNotIn("👩", [line for line in result.lines if line != "👩\u200d💻"])
        self.assertNotIn("…", "\n".join(result.lines))

    def test_word_boundaries_are_preferred_before_grapheme_splits(self) -> None:
        label = "long service a"
        width = fit_text("service a", width=1000, role="core", variant="desktop").widths[0]
        result = fit_text(label, width=width, role="core", variant="desktop")
        self.assertEqual(result.status, "fit")
        self.assertEqual(result.lines, ("long", "service a"))
        self.assertEqual(len(result.lines), 2)

    def test_desktop_and_mobile_keep_variant_minimums(self) -> None:
        desktop = fit_text("label", width=100, role="node", variant="desktop")
        mobile = fit_text("label", width=100, role="node", variant="mobile")
        self.assertEqual(desktop.status, "fit")
        self.assertEqual(mobile.status, "fit")
        self.assertEqual(desktop.font_size, 16)
        self.assertEqual(mobile.font_size, 24)
        desktop_wide = fit_text("label", width=1000, role="node", variant="desktop")
        mobile_wide = fit_text("label", width=1000, role="node", variant="mobile")
        self.assertGreater(mobile_wide.widths[0], desktop_wide.widths[0])

    def test_long_unbreakable_core_label_fails_without_ellipsis(self) -> None:
        label = "X" * 500
        result = fit_text(label, width=40, role="core", variant="desktop")
        self.assertEqual(result.status, "fail")
        self.assertEqual(result.error_code, "E_VISUAL_TEXT_FIT")
        self.assertNotIn("…", "\n".join(result.lines))
        self.assertEqual("".join(result.lines), label)

    def test_invalid_role_variant_width_and_font_raise_contract_error(self) -> None:
        cases = (
            ("unknown role", {"role": "unknown", "variant": "desktop"}, "E_SCHEMA_VALUE"),
            ("unknown variant", {"role": "node", "variant": "tablet"}, "E_SCHEMA_VALUE"),
            ("negative width", {"role": "node", "variant": "desktop", "width": -1}, "E_VISUAL_TEXT_FIT"),
            ("small mobile font", {"role": "node", "variant": "mobile", "font_size": 16}, "E_VISUAL_TEXT_FIT"),
        )
        for name, options, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(ContractError) as raised:
                    fit_text("label", **({"width": 100, **options} if "width" not in options else options))
                self.assertEqual(raised.exception.code, code)

    def test_result_is_immutable_and_has_closed_json_surface(self) -> None:
        result = fit_text("alpha beta", width=100, role="edge", variant="desktop")
        self.assertIsInstance(result, TextFitResult)
        for alias in ("fits", "fit", "ok", "code"):
            self.assertFalse(hasattr(result, alias))
        with self.assertRaises((AttributeError, TypeError)):
            result.lines += ("changed",)  # type: ignore[misc]
        projection = result.as_dict()
        self.assertEqual(
            set(projection),
            {
                "schema_version",
                "status",
                "text",
                "lines",
                "widths",
                "max_width",
                "role",
                "variant",
                "font_size",
                "line_budget",
                "error_code",
                "message",
            },
        )
        self.assertEqual(json.loads(result.canonical_bytes()), projection)

    def test_fresh_process_surface_is_repeatable(self) -> None:
        code = (
            "from skill.scripts.readme_showcase.visual_kernel.text import fit_text; "
            "import json; print(json.dumps(fit_text('请求 flow', width=120, role='core', variant='mobile').as_dict(), "
            "ensure_ascii=False, sort_keys=True, separators=(',', ':')))"
        )
        env = {"PYTHONDONTWRITEBYTECODE": "1"}
        first = subprocess.check_output([sys.executable, "-c", code], text=True, env=env).strip()
        second = subprocess.check_output([sys.executable, "-c", code], text=True, env=env).strip()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
