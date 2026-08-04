from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel import swimlane as swimlane_module
from skill.scripts.readme_showcase.visual_kernel.graph import compile_graph
from skill.scripts.readme_showcase.visual_kernel.normalize import PlanLane, normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.swimlane import SwimlanePlan, plan_swimlanes


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"swimlane-evidence-{index}.md",
        locator=None,
        semantic_key=f"swimlane-evidence-{index}",
        value=True,
        source_bytes=f"swimlane-evidence-{index}".encode(),
    )
    for index in range(1, 7)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(str(item["fact_id"]) for item in FACTS)

DESKTOP_METRICS = {
    "canvas": 24,
    "section": 32,
    "node": 16,
    "lane": 20,
    "label": 12,
    "width": 1200,
    "min_font_size": 16,
}
MOBILE_METRICS = {
    "canvas": 16,
    "section": 20,
    "node": 12,
    "lane": 14,
    "label": 10,
    "width": 720,
    "min_font_size": 24,
}


def _label(value: str, index: int = 0) -> dict[str, object]:
    return {"label": value, "evidence_ids": [EVIDENCE_IDS[index]]}


def _spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": "swimlane", **_label("Bounded flow")},
        "locale": "en",
        "variants": ["desktop", "mobile"],
        "nodes": [
            {"id": "a1", "kind": "process", **_label("Collect", 0), "lane_id": "lane-a"},
            {"id": "a2", "kind": "service", **_label("Validate", 1), "lane_id": "lane-a", "group_id": "boundary"},
            {"id": "b1", "kind": "store", **_label("Persist", 2), "lane_id": "lane-b"},
            {"id": "b2", "kind": "note", **_label("Review", 3), "lane_id": "lane-b"},
            {"id": "out", "kind": "actor", **_label("Publish", 4)},
        ],
        "edges": [
            {"id": "a1-a2", "kind": "flow", "source": "a1", "target": "a2"},
            {"id": "a2-b1", "kind": "flow", "source": "a2", "target": "b1"},
            {"id": "b1-b2", "kind": "flow", "source": "b1", "target": "b2"},
            {"id": "b2-a1", "kind": "back", "source": "b2", "target": "a1"},
            {"id": "b2-out", "kind": "flow", "source": "b2", "target": "out"},
        ],
        "groups": [{"id": "boundary", **_label("Boundary", 5)}],
        "lanes": [
            {"id": "lane-a", **_label("Source lane", 0)},
            {"id": "lane-b", **_label("Storage lane", 1)},
        ],
        "constraints": [],
    }


def _plan_and_graph(spec: dict[str, object] | None = None):
    plan = normalize_visual_spec(spec or _spec(), EVIDENCE)
    return plan, compile_graph(plan)


class SwimlanePlanningTests(unittest.TestCase):
    def test_one_lane_projection_keeps_all_nodes_and_edges(self) -> None:
        one_lane_spec = copy.deepcopy(_spec())
        one_lane_spec["lanes"] = [one_lane_spec["lanes"][0]]  # type: ignore[index]
        for node in one_lane_spec["nodes"]:  # type: ignore[union-attr]
            node["lane_id"] = "lane-a"
        plan, graph = _plan_and_graph(one_lane_spec)
        result = plan_swimlanes(plan, graph, DESKTOP_METRICS, "desktop")
        projection = result.as_dict()
        self.assertEqual([item["id"] for item in projection["children"]], ["lane-a"])
        self.assertEqual(
            {item["id"] for item in projection["edges"]},
            {item.id for item in plan.edges},
        )

    def test_public_surface_and_nested_groups_are_closed(self) -> None:
        self.assertEqual(swimlane_module.__all__, ["SwimlanePlan", "plan_swimlanes"])
        plan, graph = _plan_and_graph()
        result = plan_swimlanes(plan, graph, DESKTOP_METRICS, "desktop")
        self.assertIsInstance(result, SwimlanePlan)
        value = result.as_dict()
        self.assertEqual([item["id"] for item in value["children"][:2]], ["lane-a", "lane-b"])
        lane_a = value["children"][0]
        lane_b = value["children"][1]
        self.assertEqual(
            [
                lane_a["layoutOptions"]["elk.layered.crossingMinimization.positionId"],
                lane_b["layoutOptions"]["elk.layered.crossingMinimization.positionId"],
            ],
            ["0", "1"],
        )
        self.assertEqual([item["id"] for item in lane_a["children"]], ["boundary", "a1"])
        self.assertEqual(lane_a["properties"]["header_padding"], "[top=132,left=20,bottom=132,right=20]")
        self.assertIn("body_padding", lane_a["properties"])
        self.assertNotIn('"x"', json.dumps(value))
        self.assertNotIn('"y"', json.dumps(value))
        self.assertNotIn('"height"', json.dumps(value))

    def test_cross_lane_and_loop_channels_preserve_every_edge(self) -> None:
        plan, graph = _plan_and_graph()
        result = plan_swimlanes(plan, graph, DESKTOP_METRICS, "desktop")
        self.assertEqual({kind for kind, _, _ in result.channels}, {"loop", "skip"})
        self.assertEqual(
            {edge_id for _, edge_id, _ in result.channels},
            {"a2-b1", "b2-a1", "b2-out"},
        )
        projection = result.as_dict()
        self.assertEqual(
            {item["id"] for item in projection["edges"]},
            {item.id for item in plan.edges},
        )
        channels = {item["id"]: item.get("properties", {}).get("channel") for item in projection["edges"]}
        self.assertEqual(channels["b2-a1"], "loop")
        self.assertEqual(channels["a2-b1"], "skip")

    def test_content_and_mobile_density_change_only_constraints(self) -> None:
        plan, graph = _plan_and_graph()
        desktop = plan_swimlanes(plan, graph, DESKTOP_METRICS, "desktop")
        mobile = plan_swimlanes(plan, graph, MOBILE_METRICS, "mobile")
        self.assertNotEqual(desktop.canonical_bytes(), mobile.canonical_bytes())
        self.assertEqual(desktop.as_dict()["properties"]["canvas_width"], "1200")
        self.assertEqual(mobile.as_dict()["properties"]["canvas_width"], "720")

        expanded_spec = copy.deepcopy(_spec())
        expanded_spec["lanes"][0]["label"] = "Source lane with a longer evidence-bound title"  # type: ignore[index]
        expanded_spec["nodes"].insert(  # type: ignore[union-attr]
            2,
            {"id": "a3", "kind": "process", **_label("Another bounded step", 0), "lane_id": "lane-a"},
        )
        expanded_plan, expanded_graph = _plan_and_graph(expanded_spec)
        expanded = plan_swimlanes(expanded_plan, expanded_graph, DESKTOP_METRICS, "desktop")
        expanded_lane = expanded.as_dict()["children"][0]["properties"]
        desktop_lane = desktop.as_dict()["children"][0]["properties"]
        self.assertNotEqual(expanded_lane["header_padding"], desktop_lane["header_padding"])
        self.assertNotEqual(expanded_lane["body_padding"], desktop_lane["body_padding"])

    def test_repeated_projection_is_byte_stable(self) -> None:
        plan, graph = _plan_and_graph()
        first = plan_swimlanes(plan, graph, DESKTOP_METRICS, "desktop")
        second = plan_swimlanes(plan, graph, dict(reversed(tuple(DESKTOP_METRICS.items()))), "desktop")
        self.assertEqual(first.canonical_bytes(), second.canonical_bytes())
        self.assertEqual(first.sha256(), second.sha256())

    def test_unknown_lane_and_endpoint_typo_fail_closed(self) -> None:
        plan, graph = _plan_and_graph()
        unknown_lane = replace(plan.nodes[0], lane=PlanLane("missing", "Missing", EVIDENCE_IDS[:1]))
        invalid_plan = replace(plan, nodes=(unknown_lane, *plan.nodes[1:]))
        with self.assertRaises(ContractError) as raised:
            plan_swimlanes(invalid_plan, graph, DESKTOP_METRICS, "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        typo = replace(plan.edges[0], target="not-a-node")
        invalid_edges = (typo, *plan.edges[1:])
        with self.assertRaises(ContractError) as raised:
            plan_swimlanes(replace(plan, edges=invalid_edges), graph, DESKTOP_METRICS, "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

    def test_metrics_are_exact_and_variant_bounded(self) -> None:
        plan, graph = _plan_and_graph()
        with self.assertRaises(ContractError) as raised:
            plan_swimlanes(plan, graph, {**DESKTOP_METRICS, "extra": 1}, "desktop")
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")
        with self.assertRaises(ContractError) as raised:
            plan_swimlanes(plan, graph, {**DESKTOP_METRICS, "min_font_size": 15}, "desktop")
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")
        with self.assertRaises(ContractError) as raised:
            plan_swimlanes(plan, graph, {**MOBILE_METRICS, "width": 721}, "mobile")
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")


if __name__ == "__main__":
    unittest.main()
