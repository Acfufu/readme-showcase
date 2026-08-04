from __future__ import annotations

import copy
import random
import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel.normalize import (
    Plan,
    canonical_plan_sha256,
    normalize_visual_spec,
)


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"evidence-{index}.md",
        locator=None,
        semantic_key=f"evidence-{index}",
        value=True,
        source_bytes=f"evidence-{index}".encode(),
    )
    for index in range(1, 5)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(fact["fact_id"] for fact in FACTS)


def _label(value: str, evidence_id: str = EVIDENCE_IDS[0]) -> dict[str, object]:
    return {"label": value, "evidence_ids": [evidence_id]}


def spec(kind: str) -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": kind, **_label(f"{kind.title()} diagram")},
        "locale": "en",
        "variants": ["desktop", "mobile"],
        "nodes": [
            {"id": "actor", "kind": "actor", **_label("Actor", EVIDENCE_IDS[0]), "group_id": "runtime", "lane_id": "request"},
            {"id": "service", "kind": "service", **_label("Service", EVIDENCE_IDS[1]), "group_id": "runtime", "lane_id": "request"},
            {"id": "store", "kind": "store", **_label("Store", EVIDENCE_IDS[2]), "group_id": "runtime", "lane_id": "storage"},
        ],
        "edges": [
            {"id": "a-forward", "kind": "flow", "source": "actor", "target": "service", **_label("call", EVIDENCE_IDS[0])},
            {"id": "b-forward", "kind": "data", "source": "service", "target": "store", **_label("write", EVIDENCE_IDS[1])},
            {"id": "c-cycle", "kind": "flow", "source": "store", "target": "actor", **_label("retry", EVIDENCE_IDS[2])},
            {"id": "d-declared-back", "kind": "back", "source": "service", "target": "actor", **_label("return", EVIDENCE_IDS[3])},
        ],
        "groups": [{"id": "runtime", **_label("Runtime", EVIDENCE_IDS[0])}],
        "lanes": [
            {"id": "request", **_label("Request", EVIDENCE_IDS[1])},
            {"id": "storage", **_label("Storage", EVIDENCE_IDS[2])},
        ],
        "constraints": [{"target": "actor", "order": 0}, {"target": "service", "rank": 1}],
    }


def shuffled_mappings(value: object, rng: random.Random) -> object:
    if isinstance(value, dict):
        items = list(value.items())
        rng.shuffle(items)
        return {key: shuffled_mappings(item, rng) for key, item in items}
    if isinstance(value, list):
        return [shuffled_mappings(item, rng) for item in value]
    return value


class VisualPlanNormalizationTests(unittest.TestCase):
    def test_all_intents_normalize_to_immutable_lossless_plans(self) -> None:
        for kind in ("architecture", "flow", "swimlane", "sequence"):
            with self.subTest(kind=kind):
                plan = normalize_visual_spec(spec(kind), EVIDENCE)
                self.assertIsInstance(plan, Plan)
                self.assertEqual(plan.intent.kind, kind)
                self.assertEqual(plan.intent.label, f"{kind.title()} diagram")
                self.assertEqual(plan.variants, ("desktop", "mobile"))
                self.assertEqual([node.id for node in plan.nodes], ["actor", "service", "store"])
                self.assertEqual([edge.id for edge in plan.edges], ["a-forward", "b-forward", "c-cycle", "d-declared-back"])
                self.assertEqual([edge.label for edge in plan.edges], ["call", "write", "retry", "return"])
                self.assertEqual(plan.nodes[0].group.id, "runtime")
                self.assertEqual(plan.nodes[2].lane.id, "storage")
                self.assertEqual(plan.nodes[1].evidence_ids, (EVIDENCE_IDS[1],))
                self.assertEqual(plan.groups[0].evidence_ids, (EVIDENCE_IDS[0],))

                with self.assertRaises(AttributeError):
                    plan.nodes = ()  # type: ignore[misc]
                projection = plan.as_dict()
                projection["nodes"][0]["label"] = "mutated"  # type: ignore[index]
                self.assertEqual(plan.nodes[0].label, "Actor")

    def test_back_edges_are_derived_without_dropping_ids(self) -> None:
        plan = normalize_visual_spec(spec("flow"), EVIDENCE)
        self.assertEqual(
            [(edge.id, edge.is_back_edge) for edge in plan.edges],
            [("a-forward", False), ("b-forward", False), ("c-cycle", True), ("d-declared-back", True)],
        )
        self.assertEqual(
            {node.id for node in plan.nodes} | {edge.id for edge in plan.edges}
            | {group.id for group in plan.groups} | {lane.id for lane in plan.lanes},
            {"actor", "service", "store", "a-forward", "b-forward", "c-cycle", "d-declared-back", "runtime", "request", "storage"},
        )

    def test_mapping_insertion_order_does_not_change_plan_hash(self) -> None:
        original = spec("architecture")
        expected = canonical_plan_sha256(original, EVIDENCE)
        for seed in range(20):
            candidate = shuffled_mappings(original, random.Random(seed))
            self.assertEqual(canonical_plan_sha256(candidate, EVIDENCE), expected)

    def test_missing_membership_or_evidence_fails_before_plan_output(self) -> None:
        with self.assertRaises(ContractError) as raised:
            normalize_visual_spec(spec("flow"))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")
        with self.assertRaises(ContractError) as raised:
            normalize_visual_spec(spec("flow"), None)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        missing_group = spec("flow")
        missing_group["nodes"][0]["group_id"] = "unknown"  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            normalize_visual_spec(missing_group, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        missing_lane = spec("swimlane")
        missing_lane["nodes"][0]["lane_id"] = "unknown"  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            normalize_visual_spec(missing_lane, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        missing_evidence = spec("sequence")
        missing_evidence["nodes"][0]["evidence_ids"] = ["file:" + "f" * 64]  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            normalize_visual_spec(missing_evidence, EVIDENCE)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

    def test_input_and_plan_are_isolated(self) -> None:
        payload = spec("architecture")
        plan = normalize_visual_spec(payload, EVIDENCE)
        payload["nodes"][0]["label"] = "changed"  # type: ignore[index]
        payload["edges"].clear()  # type: ignore[union-attr]
        self.assertEqual(plan.nodes[0].label, "Actor")
        self.assertEqual(len(plan.edges), 4)
        self.assertEqual(plan.sha256(), canonical_plan_sha256(spec("architecture"), EVIDENCE))


if __name__ == "__main__":
    unittest.main()
