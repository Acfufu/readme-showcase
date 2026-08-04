from __future__ import annotations

import copy
import json
import random
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel.graph import CompiledGraph, compile_graph
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"graph-evidence-{index}.md",
        locator=None,
        semantic_key=f"graph-evidence-{index}",
        value=True,
        source_bytes=f"graph-evidence-{index}".encode(),
    )
    for index in range(1, 5)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(fact["fact_id"] for fact in FACTS)


def _label(value: str, evidence_id: str = EVIDENCE_IDS[0]) -> dict[str, object]:
    return {"label": value, "evidence_ids": [evidence_id]}


def spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": "flow", **_label("Graph fixture")},
        "locale": "en",
        "variants": ["desktop"],
        "nodes": [
            {"id": "a", "kind": "actor", **_label("A", EVIDENCE_IDS[0])},
            {"id": "b", "kind": "service", **_label("B", EVIDENCE_IDS[1])},
            {"id": "c", "kind": "store", **_label("C", EVIDENCE_IDS[2])},
            {"id": "d", "kind": "note", **_label("D", EVIDENCE_IDS[3])},
        ],
        "edges": [
            {"id": "a-b", "kind": "flow", "source": "a", "target": "b"},
            {"id": "a-d", "kind": "data", "source": "a", "target": "d"},
            {"id": "b-c", "kind": "flow", "source": "b", "target": "c"},
            {"id": "c-a", "kind": "back", "source": "c", "target": "a"},
        ],
        "groups": [{"id": "runtime", **_label("Runtime", EVIDENCE_IDS[0])}],
        "lanes": [],
        "constraints": [
            {"target": "a", "rank": 0, "order": 0, "pin": 0},
            {"target": "b", "rank": 1},
        ],
    }


def shuffled(value: object, rng: random.Random) -> object:
    if isinstance(value, dict):
        entries = list(value.items())
        rng.shuffle(entries)
        return {key: shuffled(item, rng) for key, item in entries}
    if isinstance(value, list):
        return [shuffled(item, rng) for item in value]
    return value


def node_options(result: dict[str, object]) -> dict[str, dict[str, str]]:
    found: dict[str, dict[str, str]] = {}

    def visit(items: object) -> None:
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict):
                continue
            identifier = item.get("id")
            if isinstance(identifier, str):
                options = item.get("layoutOptions", {})
                if isinstance(options, dict):
                    found[identifier] = {str(key): str(value) for key, value in options.items()}
            visit(item.get("children"))

    visit(result.get("children"))
    return found


class GraphCompilerTests(unittest.TestCase):
    def test_dag_skip_and_cycle_preserve_ids_without_coordinates(self) -> None:
        plan = normalize_visual_spec(spec(), EVIDENCE)
        compiled = compile_graph(plan)
        self.assertIsInstance(compiled, CompiledGraph)
        self.assertEqual(compiled.back_edges, ("c-a",))
        result = compiled.as_dict()
        self.assertEqual(result["id"], "root")
        self.assertEqual(
            {child["id"] for child in result["children"]}
            | {item["id"] for child in result["children"] for item in child.get("children", [])},
            {"a", "b", "c", "d", "runtime"},
        )
        self.assertEqual({item["id"] for item in result["edges"]}, {"a-b", "b-c", "a-d", "c-a"})
        self.assertEqual(
            result["edges"][-1]["layoutOptions"],
            {"elk.layered.feedbackEdges": "true"},
        )
        options = node_options(result)
        self.assertEqual(options["a"]["elk.layered.layering.layerId"], "0")
        self.assertEqual(options["b"]["elk.layered.layering.layerId"], "1")
        self.assertEqual(options["c"]["elk.layered.layering.layerId"], "2")
        self.assertEqual(options["d"]["elk.layered.layering.layerId"], "1")
        self.assertNotIn("x", json.dumps(result))
        self.assertNotIn("width", json.dumps(result))
        self.assertNotIn("height", json.dumps(result))
        result["children"].clear()  # type: ignore[union-attr]
        self.assertEqual(compiled.as_dict()["id"], "root")
        with self.assertRaises(AttributeError):
            compiled.ranks = ()  # type: ignore[misc]

        grouped = copy.deepcopy(spec())
        grouped["nodes"][0]["group_id"] = "runtime"  # type: ignore[index]
        grouped_value = compile_graph(normalize_visual_spec(grouped, EVIDENCE)).as_dict()
        runtime = next(item for item in grouped_value["children"] if item["id"] == "runtime")
        self.assertEqual([item["id"] for item in runtime["children"]], ["a"])

    def test_two_sweeps_and_mapping_permutations_are_byte_stable(self) -> None:
        plan = normalize_visual_spec(spec(), EVIDENCE)
        expected = compile_graph(plan).canonical_bytes()
        permuted_plan = replace(plan, nodes=tuple(reversed(plan.nodes)), edges=tuple(reversed(plan.edges)))
        self.assertEqual(compile_graph(permuted_plan).canonical_bytes(), expected)
        for seed in range(12):
            candidate = shuffled(copy.deepcopy(spec()), random.Random(seed))
            # The validator owns canonical input ordering; graph compilation must
            # therefore see the same normalized Plan for every source mapping.
            self.assertEqual(
                compile_graph(normalize_visual_spec(candidate, EVIDENCE)).canonical_bytes(),
                expected,
            )

    def test_self_edge_and_contradictory_or_unsatisfied_constraints_fail(self) -> None:
        self_edge = copy.deepcopy(spec())
        self_edge["edges"] = [*self_edge["edges"], {"id": "z-self", "kind": "flow", "source": "a", "target": "a"}]  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            compile_graph(normalize_visual_spec(self_edge, EVIDENCE))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        contradictory = copy.deepcopy(spec())
        contradictory["constraints"] = [
            {"target": "a", "rank": 0},
            {"target": "a", "rank": 2},
        ]
        with self.assertRaises(ContractError) as raised:
            compile_graph(normalize_visual_spec(contradictory, EVIDENCE))
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        unsatisfied = copy.deepcopy(spec())
        unsatisfied["constraints"] = [{"target": "b", "rank": 0}]  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            compile_graph(normalize_visual_spec(unsatisfied, EVIDENCE))
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        unsupported = copy.deepcopy(spec())
        unsupported["lanes"] = [{"id": "lane", **_label("Lane", EVIDENCE_IDS[0])}]  # type: ignore[index]
        unsupported["constraints"] = [{"target": "lane", "order": 0}]  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            compile_graph(normalize_visual_spec(unsupported, EVIDENCE))
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")

        edge_target = copy.deepcopy(spec())
        edge_target["constraints"] = [{"target": "a-b", "order": 0}]  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            compile_graph(normalize_visual_spec(edge_target, EVIDENCE))
        self.assertEqual(raised.exception.code, "E_VISUAL_DETERMINISM")


if __name__ == "__main__":
    unittest.main()
