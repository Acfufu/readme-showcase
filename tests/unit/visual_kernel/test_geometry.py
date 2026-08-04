from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.visual_kernel.geometry import _segments_intersect, validate_visual_geometry
from skill.scripts.readme_showcase.visual_kernel.scene import Scene, ScenePrimitive

from tests.unit.visual_kernel.test_scene import _build


_LAYER_INDEX = {"containers": 0, "edges": 1, "nodes": 2, "labels": 3}


def _rebuild(scene: Scene, mutator) -> Scene:
    values = tuple(mutator(item) for item in scene.primitives)
    values = tuple(sorted(values, key=lambda item: (_LAYER_INDEX[item.layer], item.z, item.id.encode("utf-8"))))
    return Scene(
        scene.schema_version,
        scene.locale,
        scene.variant,
        scene.view_box,
        scene.source_spec_sha256,
        scene.theme_sha256,
        scene.backend,
        scene.layers,
        values,
    )


def _with_node(scene: Scene, identifier: str, x: int, y: int, width: int, height: int) -> Scene:
    node = next(item for item in scene.primitives if item.kind == "rect")
    evidence_ids = node.evidence_ids
    extra = ScenePrimitive(
        "rect",
        identifier,
        identifier,
        evidence_ids,
        "nodes",
        2,
        x=x,
        y=y,
        width=width,
        height=height,
    )

    def mutate(item: ScenePrimitive) -> ScenePrimitive:
        if item.kind == "group":
            return replace(item, children=tuple(sorted((*item.children, identifier))))
        return item

    values = [mutate(item) for item in scene.primitives]
    values.append(extra)
    values.sort(key=lambda item: (_LAYER_INDEX[item.layer], item.z, item.id.encode("utf-8")))
    return Scene(
        scene.schema_version,
        scene.locale,
        scene.variant,
        scene.view_box,
        scene.source_spec_sha256,
        scene.theme_sha256,
        scene.backend,
        scene.layers,
        tuple(values),
    )


class GeometryGateTests(unittest.TestCase):
    def test_dense_desktop_mobile_all_primitives_and_cjk_pass(self) -> None:
        for variant in ("desktop", "mobile"):
            with self.subTest(variant=variant):
                scene = _build("flow", variant, cjk=True)
                self.assertIs(validate_visual_geometry(scene), scene)
                self.assertEqual({item.kind for item in scene.primitives}, {"group", "rect", "line", "path", "text"})

    def test_touching_nodes_are_not_overlap(self) -> None:
        scene = _build("flow", "desktop")
        scene = _with_node(scene, "c", 240, 100, 120, 60)
        # Remove edges so the shared boundary is not also an ambiguous edge endpoint.
        scene = Scene(
            scene.schema_version,
            scene.locale,
            scene.variant,
            scene.view_box,
            scene.source_spec_sha256,
            scene.theme_sha256,
            scene.backend,
            scene.layers,
            tuple(
                item
                for item in scene.primitives
                if item.kind not in {"line", "path"}
                and not (item.kind == "text" and item.source_id in {"a-b", "b-a"})
            ),
        )
        self.assertIs(validate_visual_geometry(scene), scene)

    def test_one_pixel_node_overlap_fails(self) -> None:
        scene = _with_node(_build("flow", "desktop"), "c", 239, 100, 120, 60)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(scene)
        self.assertEqual(raised.exception.code, "E_VISUAL_OVERLAP")

    def test_declared_group_escape_fails(self) -> None:
        raw = _build("flow", "desktop").as_dict()
        node = next(item for item in raw["primitives"] if item["kind"] == "rect")
        node["x"] = 500
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_GEOMETRY")

    def test_missing_float_negative_and_large_direct_coordinates_are_geometry_errors(self) -> None:
        scene = _build("flow", "desktop")
        cases = []
        missing = copy.deepcopy(scene.as_dict())
        del next(item for item in missing["primitives"] if item["kind"] == "rect")["x"]
        cases.append(missing)
        floating = copy.deepcopy(scene.as_dict())
        next(item for item in floating["primitives"] if item["kind"] == "rect")["x"] = 1.5
        cases.append(floating)
        negative = copy.deepcopy(scene.as_dict())
        next(item for item in negative["primitives"] if item["kind"] == "rect")["x"] = -1
        cases.append(negative)
        large = copy.deepcopy(scene.as_dict())
        next(item for item in large["primitives"] if item["kind"] == "rect")["x"] = 20_001
        cases.append(large)
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ContractError) as raised:
                    validate_visual_geometry(candidate)
                self.assertEqual(raised.exception.code, "E_VISUAL_GEOMETRY")

    def test_endpoint_entry_is_allowed_but_missing_or_ambiguous_endpoint_fails(self) -> None:
        scene = _build("flow", "desktop")
        self.assertIs(validate_visual_geometry(scene), scene)

        missing = _rebuild(scene, lambda item: replace(item, x1=260, y1=40) if item.kind == "line" else item)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(missing)
        self.assertEqual(raised.exception.code, "E_VISUAL_EDGE_INTERSECTION")

        ambiguous = _with_node(scene, "c", 240, 100, 120, 60)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(ambiguous)
        self.assertEqual(raised.exception.code, "E_VISUAL_EDGE_INTERSECTION")

    def test_non_endpoint_crossing_and_boundary_contact_fail(self) -> None:
        scene = _with_node(_build("flow", "desktop"), "c", 280, 100, 40, 60)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(scene)
        self.assertEqual(raised.exception.code, "E_VISUAL_EDGE_INTERSECTION")

    def test_collinear_segments_outside_each_other_do_not_intersect(self) -> None:
        self.assertFalse(_segments_intersect((0, 0), (10, 0), (20, 0), (30, 0)))

        scene = _with_node(_build("flow", "desktop"), "c", 300, 130, 20, 20)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(scene)
        self.assertEqual(raised.exception.code, "E_VISUAL_EDGE_INTERSECTION")

    def test_role_budget_and_owner_width_are_text_fit_errors(self) -> None:
        scene = _build("flow", "desktop")
        label = next(item for item in scene.primitives if item.kind == "text" and item.source_id == "a")
        overflow = _rebuild(scene, lambda item: replace(item, lines=("one", "two", "three", "four"), widths=(10, 10, 10, 10)) if item.id == label.id else item)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(overflow)
        self.assertEqual(raised.exception.code, "E_VISUAL_TEXT_FIT")

        too_wide = _rebuild(scene, lambda item: replace(item, widths=(160,)) if item.id == label.id else item)
        with self.assertRaises(ContractError) as raised:
            validate_visual_geometry(too_wide)
        self.assertEqual(raised.exception.code, "E_VISUAL_TEXT_FIT")


if __name__ == "__main__":
    unittest.main()
