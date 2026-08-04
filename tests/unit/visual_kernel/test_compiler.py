from __future__ import annotations

import copy
import json
import unittest
from unittest import mock

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel import compiler as compiler_module
from skill.scripts.readme_showcase.visual_kernel.compiler import CompiledVisual, _restore_geometry, compile_visual
from skill.scripts.readme_showcase.visual_kernel.elk_backend import ElkGeometryResult

from tests.unit.visual_kernel.test_scene import EVIDENCE, _spec


def _compile_spec(kind: str, *, cjk: bool = False) -> dict[str, object]:
    return _spec(kind, swimlanes=kind == "swimlane", cjk=cjk)


class CompilerTests(unittest.TestCase):
    def test_geometry_edge_ten_is_numeric_not_lexicographic(self) -> None:
        geometry = {
            "groups": [],
            "nodes": [],
            "edges": [
                {"id": f"edge-{index}", "sections": []}
                for index in reversed(range(11))
            ],
        }
        restored = _restore_geometry(ElkGeometryResult(geometry, {}), {})
        self.assertEqual([item["id"] for item in restored["edges"]], [f"edge-{index}" for index in range(11)])

    def test_all_four_intents_have_two_independent_variants_and_cjk_is_preserved(self) -> None:
        for kind in ("architecture", "flow", "swimlane", "sequence"):
            with self.subTest(kind=kind):
                result = compile_visual(_compile_spec(kind), EVIDENCE)
                self.assertIsInstance(result, CompiledVisual)
                self.assertEqual(
                    set(result.artifacts),
                    {
                        "compiled/visual-spec.json",
                        "compiled/theme.json",
                        "compiled/inventory.json",
                        "assets/readme-showcase/en/desktop.svg",
                        "assets/readme-showcase/en/mobile.svg",
                        "compiled/scenes/en/desktop.json",
                        "compiled/scenes/en/mobile.json",
                        "compiled/gates/en/desktop.json",
                        "compiled/gates/en/mobile.json",
                        "compiled/timeline/en/desktop.json",
                        "compiled/timeline/en/mobile.json",
                        "compiled/interaction/en/desktop.json",
                        "compiled/interaction/en/mobile.json",
                    },
                )
                self.assertEqual(
                    list(result.artifacts),
                    sorted(result.artifacts, key=lambda item: item.encode("utf-8")),
                )
                desktop = json.loads(result.artifacts["compiled/scenes/en/desktop.json"])
                mobile = json.loads(result.artifacts["compiled/scenes/en/mobile.json"])
                desktop_geometry = [
                    (item["id"], item.get("x"), item.get("y"), item.get("width"), item.get("height"))
                    for item in desktop["primitives"]
                    if item["kind"] in {"group", "rect"}
                ]
                mobile_geometry = [
                    (item["id"], item.get("x"), item.get("y"), item.get("width"), item.get("height"))
                    for item in mobile["primitives"]
                    if item["kind"] in {"group", "rect"}
                ]
                self.assertNotEqual(desktop_geometry, mobile_geometry)
        cjk = compile_visual(_compile_spec("flow", cjk=True), EVIDENCE)
        cjk_scene = cjk.artifacts["compiled/scenes/zh-Hans/mobile.json"].decode("utf-8")
        cjk_svg = cjk.artifacts["assets/readme-showcase/zh-Hans/mobile.svg"].decode("utf-8")
        self.assertIn("请求", cjk_scene)
        self.assertIn("存储", cjk_scene)
        self.assertIn("请求", cjk_svg)
        self.assertIn("存储", cjk_svg)
        self.assertEqual(
            json.loads(cjk.artifacts["compiled/gates/zh-Hans/mobile.json"])["status"],
            "pass",
        )

    def test_repeated_compile_is_byte_identical_and_result_is_immutable(self) -> None:
        payload = _compile_spec("flow")
        first = compile_visual(payload, EVIDENCE)
        second = compile_visual(copy.deepcopy(payload), dict(EVIDENCE))
        self.assertEqual(first, second)
        self.assertEqual(first.inventory_sha256, json.loads(first.artifacts["compiled/inventory.json"])["inventory_sha256"])
        with self.assertRaises(TypeError):
            first.artifacts["compiled/new.json"] = b"bad"  # type: ignore[index]
        tampered_scene = dict(first.artifacts)
        tampered_scene["compiled/scenes/en/desktop.json"] = b"{}"
        with self.assertRaises(ContractError) as raised:
            CompiledVisual(tampered_scene, first.inventory_sha256)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")
        tampered_path = dict(first.artifacts)
        tampered_path["compiled/../escape"] = b"bad"
        with self.assertRaises(ContractError) as raised:
            CompiledVisual(tampered_path, first.inventory_sha256)
        self.assertEqual(raised.exception.code, "E_VISUAL_PATH")

    def test_invalid_spec_backend_omission_unsafe_svg_overlap_nondeterminism_and_size_return_no_result(self) -> None:
        payload = _compile_spec("flow")
        invalid = copy.deepcopy(payload)
        invalid["nodes"][0]["id"] = invalid["nodes"][1]["id"]  # type: ignore[index]
        with self.assertRaises(ContractError):
            compile_visual(invalid, EVIDENCE)

        with mock.patch.object(
            compiler_module,
            "render_elk_geometry",
            side_effect=ContractError("E_OUTPUT_GEOMETRY", "backend omitted geometry"),
        ):
            with self.assertRaises(ContractError) as raised:
                compile_visual(payload, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_OUTPUT_GEOMETRY")

        real_serialize = compiler_module.serialize_svg

        def unsafe_svg(scene: object, theme: object) -> bytes:
            return real_serialize(scene, theme).replace(b"<text", b"<script/><text", 1)

        with mock.patch.object(compiler_module, "serialize_svg", side_effect=unsafe_svg):
            with self.assertRaises(ContractError) as raised:
                compile_visual(payload, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SVG_SECURITY")

        real_render = compiler_module.render_elk_geometry

        def overlap(envelope: object, attempt: object) -> ElkGeometryResult:
            result = real_render(envelope, attempt)
            raw = result.as_dict()
            if len(raw["geometry"]["nodes"]) > 1:
                node_by_id = {item["id"]: item for item in raw["geometry"]["nodes"]}
                source_id = envelope["nodes"][0]["id"]  # type: ignore[index]
                target_id = envelope["nodes"][1]["id"]  # type: ignore[index]
                first, second = node_by_id[source_id], node_by_id[target_id]
                first.update({"x": 48, "width": 300})
                second.update({"x": 300, "width": 200})
                raw["geometry"]["edges"][0]["sections"][0].update(
                    {"start": {"x": 100, "y": first["y"] + 35}, "end": {"x": 480, "y": second["y"] + 35}}
                )
                if len(raw["geometry"]["edges"]) > 1:
                    raw["geometry"]["edges"][1]["sections"][0].update(
                        {"start": {"x": 480, "y": second["y"] + 10}, "end": {"x": 100, "y": first["y"] + 10}}
                    )
            return ElkGeometryResult(raw["geometry"], raw["metadata"])

        with mock.patch.object(compiler_module, "render_elk_geometry", side_effect=overlap):
            with self.assertRaises(ContractError) as raised:
                compile_visual(payload, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_OVERLAP")

        calls = 0

        def drift(envelope: object, attempt: object) -> ElkGeometryResult:
            nonlocal calls
            calls += 1
            result = real_render(envelope, attempt)
            if calls == 2:
                raw = result.as_dict()
                raw["geometry"]["canvas"]["height"] += 1
                return ElkGeometryResult(raw["geometry"], raw["metadata"])
            return result

        with mock.patch.object(compiler_module, "render_elk_geometry", side_effect=drift):
            with self.assertRaises(ContractError) as raised:
                compile_visual(payload, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        oversized = copy.deepcopy(payload)
        oversized["intent"] = dict(oversized["intent"])  # type: ignore[index]
        oversized["intent"]["label"] = "x" * (256 * 1024)  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            compile_visual(oversized, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_SIZE")


if __name__ == "__main__":
    unittest.main()
