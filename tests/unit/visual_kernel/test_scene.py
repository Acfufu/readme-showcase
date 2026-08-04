from __future__ import annotations

import copy
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel.elk_backend import ElkGeometryResult
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.scene import (
    SCENE_LAYERS,
    Scene,
    build_scene,
    validate_visual_scene,
)
from skill.scripts.readme_showcase.visual_kernel import scene as scene_module
from skill.scripts.readme_showcase.visual_kernel.text import fit_text
from skill.scripts.readme_showcase.visual_kernel.theme import resolve_theme


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"scene-evidence-{index}.md",
        locator=None,
        semantic_key=f"scene-evidence-{index}",
        value=True,
        source_bytes=f"scene-evidence-{index}".encode(),
    )
    for index in range(1, 8)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(str(item["fact_id"]) for item in FACTS)
ENGINE = {
    "engine_kind": "elk",
    "package_name": "elkjs",
    "package_version": "0.9.3",
    "package_sha256": "fb9bb80b980c72022fb4540b38aa0545242b4eb67b82250aeae2f0beb67eea25",
    "module_sha256": "b0745abd7f23cd91690a1587e377edbe19fd7233c783300290936720546216d4",
    "node_version": "22.22.3",
    "renderer_sha256": "c" * 64,
}


def _label(value: str, index: int = 0) -> dict[str, object]:
    return {"label": value, "evidence_ids": [EVIDENCE_IDS[index]]}


def _spec(kind: str, *, swimlanes: bool = False, cjk: bool = False) -> dict[str, object]:
    first = "请求" if cjk else "Request"
    second = "存储" if cjk else "Storage"
    nodes: list[dict[str, object]] = [
        {"id": "a", "kind": "actor", **_label(first, 1)},
        {"id": "b", "kind": "service", **_label(second, 2)},
    ]
    groups: list[dict[str, object]] = [{"id": "runtime", **_label("Runtime", 3)}]
    lanes: list[dict[str, object]] = []
    if swimlanes:
        groups = []
        lanes = [
            {"id": "request", **_label("Request lane", 3)},
            {"id": "storage", **_label("Storage lane", 4)},
        ]
        nodes[0]["lane_id"] = "request"
        nodes[1]["lane_id"] = "storage"
    else:
        nodes[0]["group_id"] = "runtime"
        nodes[1]["group_id"] = "runtime"
    return {
        "schema_version": 1,
        "intent": {"kind": kind, **_label(f"{kind.title()} diagram", 0)},
        "locale": "zh-Hans" if cjk else "en",
        "variants": ["desktop", "mobile"],
        "nodes": nodes,
        "edges": [
            {"id": "a-b", "kind": "flow", "source": "a", "target": "b", **_label("call", 5)},
            {"id": "b-a", "kind": "back", "source": "b", "target": "a"},
        ],
        "groups": groups,
        "lanes": lanes,
        "constraints": [],
    }


def _geometry(plan, variant: str) -> dict[str, object]:
    width = 1200 if variant == "desktop" else 720
    if plan.lanes:
        groups = [
            {"id": "request", "parent_id": None, "x": 40, "y": 40, "width": 540, "height": 120},
            {"id": "storage", "parent_id": None, "x": 40, "y": 200, "width": 540, "height": 120},
        ]
        nodes = [
            {"id": "a", "parent_id": "request", "x": 80, "y": 70, "width": 160, "height": 60},
            {"id": "b", "parent_id": "storage", "x": 80, "y": 230, "width": 160, "height": 60},
        ]
    else:
        groups = [{"id": "runtime", "parent_id": None, "x": 40, "y": 40, "width": 540, "height": 240}]
        nodes = [
            {"id": "a", "parent_id": "runtime", "x": 80, "y": 100, "width": 160, "height": 60},
            {"id": "b", "parent_id": "runtime", "x": 360, "y": 100, "width": 160, "height": 60},
        ]
    start = {"x": 240, "y": 130 if not plan.lanes else 100}
    end = {"x": 360 if not plan.lanes else 240, "y": 130 if not plan.lanes else 260}
    back_start = {"x": 360 if not plan.lanes else 80, "y": 160 if not plan.lanes else 260}
    back_end = {"x": 240 if not plan.lanes else 80, "y": 160 if not plan.lanes else 100}
    return {
        "schema_version": 1,
        "engine": ENGINE,
        "canvas": {"width": width, "height": 360},
        "groups": sorted(groups, key=lambda item: item["id"]),
        "nodes": sorted(nodes, key=lambda item: item["id"]),
        "ports": [],
        "edges": [
            {"id": "edge-0", "sections": [{"start": start, "bends": [], "end": end}]},
            {"id": "edge-1", "sections": [{"start": back_start, "bends": [{"x": 300, "y": 320}], "end": back_end}]},
        ],
    }


def _fits(plan, variant: str) -> dict[str, object]:
    values: dict[str, object] = {}
    if plan.intent.label is not None:
        values[scene_module._INTENT_KEY] = fit_text(plan.intent.label, width=1000, role="title", variant=variant)
    for item in (*plan.groups, *plan.lanes, *plan.nodes, *plan.edges):
        if item.label is not None:
            values[item.id] = fit_text(item.label, width=1000, role="node", variant=variant)
    return values


def _build(kind: str, variant: str = "desktop", *, cjk: bool = False) -> Scene:
    payload = _spec(kind, swimlanes=kind == "swimlane", cjk=cjk)
    plan = normalize_visual_spec(payload, EVIDENCE)
    return build_scene(plan, resolve_theme(), _fits(plan, variant), _geometry(plan, variant), variant)


class SceneTests(unittest.TestCase):
    def test_all_approved_intents_compile_for_both_variants(self) -> None:
        for kind in ("architecture", "flow", "swimlane", "sequence"):
            for variant in ("desktop", "mobile"):
                with self.subTest(kind=kind, variant=variant):
                    scene = _build(kind, variant)
                    self.assertIsInstance(scene, Scene)
                    self.assertEqual(scene.locale, "en")
                    self.assertEqual(scene.variant, variant)
                    self.assertEqual(scene.source_spec_sha256, _build(kind, "desktop").source_spec_sha256)
                    self.assertEqual(scene.layers, SCENE_LAYERS)
                    self.assertEqual(validate_visual_scene(scene), scene)
                    self.assertTrue(scene.primitives)
                    self.assertEqual(scene.canonical_bytes(), _build(kind, variant).canonical_bytes())

    def test_cjk_and_provenance_are_preserved_without_svg(self) -> None:
        scene = _build("flow", "mobile", cjk=True)
        labels = {item.source_id: item for item in scene.primitives if item.kind == "text"}
        self.assertEqual(labels["a"].text, "请求")
        self.assertEqual(labels["a"].evidence_ids, (EVIDENCE_IDS[1],))
        self.assertEqual(labels[scene_module._INTENT_KEY].evidence_ids, (EVIDENCE_IDS[0],))
        self.assertNotIn("svg", scene.as_dict())

    def test_float_missing_out_of_range_and_id_drift_fail_closed(self) -> None:
        payload = _spec("flow")
        plan = normalize_visual_spec(payload, EVIDENCE)
        good = _geometry(plan, "desktop")
        cases = []
        missing = copy.deepcopy(good)
        del missing["nodes"][0]["x"]  # type: ignore[index]
        cases.append((missing, "E_VISUAL_GEOMETRY"))
        floating = copy.deepcopy(good)
        floating["nodes"][0]["x"] = 1.5  # type: ignore[index]
        cases.append((floating, "E_VISUAL_GEOMETRY"))
        outside = copy.deepcopy(good)
        outside["nodes"][0]["x"] = 20001  # type: ignore[index]
        cases.append((outside, "E_VISUAL_GEOMETRY"))
        mismatch = copy.deepcopy(good)
        mismatch["nodes"][0]["id"] = "missing"  # type: ignore[index]
        cases.append((mismatch, "E_VISUAL_GEOMETRY"))
        for candidate, code in cases:
            with self.subTest(code=code):
                with self.assertRaises(ContractError) as raised:
                    build_scene(plan, resolve_theme(), _fits(plan, "desktop"), candidate, "desktop")
                self.assertEqual(raised.exception.code, code)

    def test_edge_count_and_edge_n_order_are_rejected_without_repair(self) -> None:
        payload = _spec("flow")
        plan = normalize_visual_spec(payload, EVIDENCE)
        for mutator in (
            lambda value: value["edges"].pop(),  # type: ignore[index]
            lambda value: value["edges"].__setitem__(0, {**value["edges"][0], "id": "edge-1"}),  # type: ignore[index]
        ):
            candidate = copy.deepcopy(_geometry(plan, "desktop"))
            mutator(candidate)
            with self.assertRaises(ContractError) as raised:
                build_scene(plan, resolve_theme(), _fits(plan, "desktop"), candidate, "desktop")
            self.assertEqual(raised.exception.code, "E_VISUAL_GEOMETRY")

    def test_wrong_variant_stale_text_and_unbound_evidence_fail(self) -> None:
        payload = _spec("flow")
        plan = normalize_visual_spec(payload, EVIDENCE)
        with self.assertRaises(ContractError) as raised:
            build_scene(plan, resolve_theme(), _fits(plan, "desktop"), _geometry(plan, "desktop"), "mobile")
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

        bad_text = _fits(plan, "desktop")
        bad_text["a"] = fit_text("Request", width=1000, role="node", variant="mobile")  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            build_scene(plan, resolve_theme(), bad_text, _geometry(plan, "desktop"), "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

        bad_plan = replace(plan, nodes=(replace(plan.nodes[0], evidence_ids=()), plan.nodes[1]))
        with self.assertRaises(ContractError) as raised:
            build_scene(bad_plan, resolve_theme(), _fits(plan, "desktop"), _geometry(plan, "desktop"), "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        bad_engine = copy.deepcopy(_geometry(plan, "desktop"))
        bad_engine["engine"]["package_version"] = "0.9.2"  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            build_scene(plan, resolve_theme(), _fits(plan, "desktop"), bad_engine, "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

        bad_font = _build("flow", "mobile").as_dict()
        text = next(item for item in bad_font["primitives"] if item["kind"] == "text")
        text["font_size"] = 16
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(bad_font)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_validate_visual_scene_direct_boundary_rejects_unknown_fields_order_and_fingerprint(self) -> None:
        scene = _build("architecture", "desktop")
        raw = scene.as_dict()
        raw["unknown"] = True
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

        raw = scene.as_dict()
        raw["primitives"] = list(reversed(raw["primitives"]))
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        raw = scene.as_dict()
        raw["variant"] = "mobile"
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_direct_boundary_cannot_rewire_provenance_or_activate_inactive_fields(self) -> None:
        scene = _build("flow", "desktop")
        raw = scene.as_dict()
        group = next(item for item in raw["primitives"] if item["kind"] == "group")
        group["evidence_ids"] = []
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        raw = scene.as_dict()
        rect = next(item for item in raw["primitives"] if item["kind"] == "rect")
        rect["source_id"] = "rewired"
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")

        raw = scene.as_dict()
        label = next(item for item in raw["primitives"] if item["kind"] == "text" and item["source_id"] == "a")
        label["evidence_ids"] = [EVIDENCE_IDS[-1]]
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        edge = next(item for item in scene.primitives if item.kind == "line")
        with self.assertRaises(ContractError) as raised:
            replace(edge, x=1)
        self.assertEqual(raised.exception.code, "E_SCHEMA_VALUE")

        raw = scene.as_dict()
        path = next(item for item in raw["primitives"] if item["kind"] == "path")
        path["points"] = [{"x": 1}]
        with self.assertRaises(ContractError) as raised:
            validate_visual_scene(raw)
        self.assertEqual(raised.exception.code, "E_VISUAL_GEOMETRY")


if __name__ == "__main__":
    unittest.main()
