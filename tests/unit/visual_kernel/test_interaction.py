from __future__ import annotations

import copy
import random
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel import interaction as interaction_module
from skill.scripts.readme_showcase.visual_kernel.interaction import (
    InteractionGraph,
    derive_interaction,
)
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"interaction-evidence-{index}.md",
        locator=None,
        semantic_key=f"interaction-evidence-{index}",
        value=True,
        source_bytes=f"interaction-evidence-{index}".encode(),
    )
    for index in range(1, 7)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(fact["fact_id"] for fact in EVIDENCE["facts"])


def _label(value: str, index: int = 0) -> dict[str, object]:
    return {"label": value, "evidence_ids": [EVIDENCE_IDS[index]]}


def _spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": "flow", **_label("Interaction fixture")},
        "locale": "en",
        "variants": ["desktop", "mobile"],
        "nodes": [
            {"id": "a", "kind": "actor", **_label("Actor", 0), "group_id": "runtime", "lane_id": "request"},
            {"id": "b", "kind": "service", **_label("Service", 1), "group_id": "runtime", "lane_id": "request"},
            {"id": "c", "kind": "store", **_label("Store", 2), "lane_id": "storage"},
        ],
        "edges": [
            {"id": "a-b", "kind": "flow", "source": "a", "target": "b", **_label("call", 3)},
            {"id": "b-c", "kind": "data", "source": "b", "target": "c"},
        ],
        "groups": [{"id": "runtime", **_label("Runtime", 4)}],
        "lanes": [
            {"id": "request", **_label("Request", 5)},
            {"id": "storage", **_label("Storage", 0)},
        ],
        "constraints": [],
    }


def _shuffled(value: object, rng: random.Random) -> object:
    if isinstance(value, dict):
        entries = list(value.items())
        rng.shuffle(entries)
        return {key: _shuffled(item, rng) for key, item in entries}
    if isinstance(value, list):
        return [_shuffled(item, rng) for item in value]
    return value


class InteractionGraphTests(unittest.TestCase):
    def test_public_surface_and_data_projection_are_closed(self) -> None:
        self.assertEqual(interaction_module.__all__, ["InteractionGraph", "derive_interaction"])
        result = derive_interaction(normalize_visual_spec(_spec(), EVIDENCE))
        self.assertIsInstance(result, InteractionGraph)
        self.assertEqual(
            result.focus_order,
            ("request", "storage", "runtime", "a", "b", "c"),
        )
        self.assertEqual(set(result.focus_order), {"a", "b", "c", "runtime", "request", "storage"})
        self.assertEqual(result.evidence_links["a"], (EVIDENCE_IDS[0],))
        self.assertEqual(result.evidence_links["a-b"], (EVIDENCE_IDS[3],))
        self.assertEqual(result.evidence_links["b-c"], ())
        self.assertEqual(result.adjacency["a"], ("b",))
        self.assertEqual(result.adjacency["b"], ("a", "c"))
        self.assertEqual(result.adjacency["c"], ("b",))
        self.assertEqual(result.group_navigation["runtime"], ("a", "b"))
        self.assertEqual(result.lane_navigation["request"], ("a", "b"))
        self.assertEqual(result.lane_navigation["storage"], ("c",))

        projection = result.as_dict()
        self.assertEqual(
            set(projection),
            {
                "schema_version",
                "focus_order",
                "evidence_links",
                "adjacency",
                "group_navigation",
                "lane_navigation",
            },
        )
        projection["focus_order"].append("mutated")  # type: ignore[union-attr]
        projection["evidence_links"]["a"].append("mutated")  # type: ignore[index]
        self.assertNotIn("mutated", result.focus_order)
        self.assertEqual(result.evidence_links["a"], (EVIDENCE_IDS[0],))
        with self.assertRaises((AttributeError, TypeError)):
            result.evidence_links["a"] = ()  # type: ignore[index]

    def test_adjacency_is_symmetric_and_never_transitive(self) -> None:
        plan = normalize_visual_spec(_spec(), EVIDENCE)
        result = derive_interaction(plan)
        self.assertNotIn("c", result.adjacency["a"])
        self.assertNotIn("a", result.adjacency["c"])

        back = replace(plan.edges[0], kind="back", is_back_edge=True)
        result = derive_interaction(replace(plan, edges=(back, plan.edges[1])))
        self.assertIn("b", result.adjacency["a"])
        self.assertIn("a", result.adjacency["b"])

        self_loop = replace(plan.edges[1], source="b", target="b", kind="back", is_back_edge=True)
        result = derive_interaction(replace(plan, edges=(plan.edges[0], self_loop)))
        self.assertEqual(result.adjacency["b"], ("a", "b"))

    def test_mapping_and_plan_order_permutations_are_byte_identical(self) -> None:
        expected = derive_interaction(normalize_visual_spec(_spec(), EVIDENCE)).canonical_bytes()
        for seed in range(12):
            candidate = _shuffled(copy.deepcopy(_spec()), random.Random(seed))
            actual = derive_interaction(normalize_visual_spec(candidate, EVIDENCE)).canonical_bytes()
            self.assertEqual(actual, expected)

        plan = normalize_visual_spec(_spec(), EVIDENCE)
        reversed_plan = replace(
            plan,
            nodes=tuple(reversed(plan.nodes)),
            edges=tuple(reversed(plan.edges)),
            groups=tuple(reversed(plan.groups)),
            lanes=tuple(reversed(plan.lanes)),
        )
        self.assertEqual(derive_interaction(reversed_plan).canonical_bytes(), expected)

    def test_duplicate_missing_evidence_and_undeclared_references_fail_closed(self) -> None:
        plan = normalize_visual_spec(_spec(), EVIDENCE)

        duplicate = replace(plan, nodes=(plan.nodes[0], plan.nodes[0], *plan.nodes[1:]))
        with self.assertRaises(ContractError) as raised:
            derive_interaction(duplicate)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")

        missing_evidence = replace(plan.nodes[0], evidence_ids=())
        with self.assertRaises(ContractError) as raised:
            derive_interaction(replace(plan, nodes=(missing_evidence, *plan.nodes[1:])))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        undeclared_edge = replace(plan.edges[0], target="missing")
        with self.assertRaises(ContractError) as raised:
            derive_interaction(replace(plan, edges=(undeclared_edge, plan.edges[1:])))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        undeclared_group = replace(plan.nodes[0], group=replace(plan.groups[0], id="missing"))
        with self.assertRaises(ContractError) as raised:
            derive_interaction(replace(plan, nodes=(undeclared_group, *plan.nodes[1:])))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        with self.assertRaises(ContractError) as raised:
            derive_interaction(_spec())  # type: ignore[arg-type]
        self.assertEqual(raised.exception.code, "E_SCHEMA_TYPE")

    def test_manually_constructed_result_revalidates_closed_references(self) -> None:
        result = derive_interaction(normalize_visual_spec(_spec(), EVIDENCE))

        with self.assertRaises(ContractError) as raised:
            replace(result, evidence_links={**result.evidence_links, "a": ()})
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        with self.assertRaises(ContractError) as raised:
            replace(result, adjacency={**result.adjacency, "a": ("b", "missing")})
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        with self.assertRaises(ContractError) as raised:
            replace(result, adjacency={**result.adjacency, "a": ("b", "b")})
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        with self.assertRaises(ContractError) as raised:
            replace(result, adjacency={**result.adjacency, "a": ("b",), "b": ("a",)})
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        with self.assertRaises(ContractError) as raised:
            replace(result, group_navigation={**result.group_navigation, "missing": ()})
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")


if __name__ == "__main__":
    unittest.main()
