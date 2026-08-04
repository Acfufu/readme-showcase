from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path

from skill.scripts.audit_readme import audit_readme, audit_svg_bytes
from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.scene import Scene
from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme

from tests.unit.visual_kernel.test_scene import _build


SVG_NAMESPACE = "http://www.w3.org/2000/svg"


def _elements(raw: bytes) -> list[ET.Element]:
    return list(ET.fromstring(raw).iter())


class SvgSerializationTests(unittest.TestCase):
    def test_both_variants_are_static_accessible_and_cover_all_primitives(self) -> None:
        for variant in ("desktop", "mobile"):
            with self.subTest(variant=variant):
                scene = _build("flow", variant, cjk=True)
                raw = serialize_svg(scene, resolve_theme())
                root = ET.fromstring(raw)
                self.assertEqual(root.tag, f"{{{SVG_NAMESPACE}}}svg")
                self.assertEqual(root.attrib["width"], str(resolve_theme().variants[variant]["render_width"]))
                self.assertEqual(root.attrib["viewBox"], "0 0 1200 360" if variant == "desktop" else "0 0 720 360")
                self.assertEqual(root.attrib["role"], "img")
                self.assertIn(root.attrib["aria-labelledby"], {item.attrib.get("id") for item in _elements(raw)})
                self.assertIn(root.attrib["aria-describedby"], {item.attrib.get("id") for item in _elements(raw)})
                tags = {item.tag.rsplit("}", 1)[-1] for item in _elements(raw)}
                self.assertTrue({"g", "rect", "line", "path", "text"} <= tags)
                labels = [item.text for item in scene.primitives if item.kind == "text"]
                self.assertEqual(audit_svg_bytes(raw, expected_title="Flow diagram", expected_labels=labels), [])
                self.assertNotRegex(raw.decode("utf-8"), r"<(?:script|style|foreignObject|image|animate)\b")
                self.assertNotRegex(raw.decode("utf-8"), r"\son[a-z]+\s*=")
                self.assertNotIn("https://", raw.decode("utf-8"))

    def test_ids_are_safe_unique_and_scene_ids_are_preserved(self) -> None:
        scene = _build("flow", "desktop")
        raw = serialize_svg(scene, resolve_theme())
        root = ET.fromstring(raw)
        ids = [item.attrib["id"] for item in root.iter() if "id" in item.attrib]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(len(identifier) <= 128 for identifier in ids))
        primitive_ids = {
            item.attrib["data-scene-id"]
            for item in root.iter()
            if "data-scene-id" in item.attrib
        }
        self.assertEqual(primitive_ids, {item.id for item in scene.primitives})

    def test_xml_hostile_text_is_escaped_without_markup(self) -> None:
        scene = _build("flow", "desktop")
        source = next(item for item in scene.primitives if item.kind == "text" and item.source_id == "a")
        hostile = "<script>alert(\"x\") & \"quoted\"</script>"
        altered = replace(source, text=hostile, lines=(hostile,))
        primitives = tuple(altered if item.id == source.id else item for item in scene.primitives)
        altered_scene = Scene(
            scene.schema_version,
            scene.locale,
            scene.variant,
            scene.view_box,
            scene.source_spec_sha256,
            scene.theme_sha256,
            scene.backend,
            scene.layers,
            primitives,
        )
        raw = serialize_svg(altered_scene, resolve_theme())
        text = raw.decode("utf-8")
        self.assertIn("&lt;script&gt;alert(\"x\") &amp; \"quoted\"&lt;/script&gt;", text)
        self.assertNotIn("<script>", text)
        self.assertEqual(audit_svg_bytes(raw), [])

    def test_external_reference_like_label_fails_closed(self) -> None:
        scene = _build("flow", "desktop")
        source = next(item for item in scene.primitives if item.kind == "text" and item.source_id == "a")
        hostile = "url(https://example.invalid/asset.svg)"
        altered = replace(source, text=hostile, lines=(hostile,))
        altered_scene = Scene(
            scene.schema_version,
            scene.locale,
            scene.variant,
            scene.view_box,
            scene.source_spec_sha256,
            scene.theme_sha256,
            scene.backend,
            scene.layers,
            tuple(altered if item.id == source.id else item for item in scene.primitives),
        )
        with self.assertRaises(ContractError) as raised:
            serialize_svg(altered_scene, resolve_theme())
        self.assertEqual(raised.exception.code, "E_VISUAL_SVG_SECURITY")

    def test_theme_hash_and_reserved_title_are_hard_bindings(self) -> None:
        scene = _build("flow", "desktop")
        with self.assertRaises(ContractError) as raised:
            serialize_svg(scene, resolve_theme({"colors": {"accent": "#22c55e"}}))
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

        title = next(item for item in scene.primitives if item.source_id == "__scene_intent__")
        missing_title = Scene(
            scene.schema_version,
            scene.locale,
            scene.variant,
            scene.view_box,
            scene.source_spec_sha256,
            scene.theme_sha256,
            scene.backend,
            scene.layers,
            tuple(item for item in scene.primitives if item.id != title.id),
        )
        with self.assertRaises(ContractError) as raised:
            serialize_svg(missing_title, resolve_theme())
        self.assertEqual(raised.exception.code, "E_VISUAL_TEXT_FIT")

    def test_serialization_is_byte_identical_and_audits_inside_readme(self) -> None:
        scene = _build("flow", "desktop")
        theme = resolve_theme()
        first = serialize_svg(scene, theme)
        second = serialize_svg(scene, theme)
        self.assertEqual(first, second)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "diagram.svg").write_bytes(first)
            readme = root / "README.md"
            readme.write_text("# Demo\n\n![Architecture](diagram.svg)\n", encoding="utf-8")
            warnings, checked, links = audit_readme(readme)
            self.assertEqual(warnings, [])
            self.assertEqual((checked, links), (1, 0))

    def test_fresh_process_serialization_is_byte_identical(self) -> None:
        code = (
            "import hashlib; "
            "from tests.unit.visual_kernel.test_scene import _build; "
            "from skill.scripts.readme_showcase.visual_kernel.svg import serialize_svg; "
            "from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme; "
            "print(hashlib.sha256(serialize_svg(_build('flow', 'mobile'), resolve_theme())).hexdigest())"
        )
        environment = {"PYTHONDONTWRITEBYTECODE": "1"}
        first = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment).strip()
        second = subprocess.check_output([sys.executable, "-c", code], text=True, env=environment).strip()
        self.assertEqual(len(first), hashlib.sha256().digest_size * 2)
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
