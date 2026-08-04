from __future__ import annotations

import copy
import random
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import build_graph
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from skill.scripts.readme_showcase.visual_kernel.timeline import Timeline, derive_timeline


FACTS = tuple(
    build_fact(
        kind="file-presence",
        path=f"timeline-evidence-{index}.md",
        locator=None,
        semantic_key=f"timeline-evidence-{index}",
        value=True,
        source_bytes=f"timeline-evidence-{index}".encode(),
    )
    for index in range(1, 7)
)
EVIDENCE = build_graph(FACTS)
EVIDENCE_IDS = tuple(fact["fact_id"] for fact in FACTS)


def _label(value: str, evidence_id: str = EVIDENCE_IDS[0]) -> dict[str, object]:
    return {"label": value, "evidence_ids": [evidence_id]}


def spec(kind: str = "flow") -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": kind, **_label("Timeline fixture")},
        "locale": "en",
        "variants": ["desktop", "mobile"],
        "nodes": [
            {"id": "actor", "kind": "actor", **_label("Actor", EVIDENCE_IDS[0]), "lane_id": "request"},
            {"id": "service", "kind": "service", **_label("Service", EVIDENCE_IDS[1]), "lane_id": "request"},
            {"id": "store", "kind": "store", **_label("Store", EVIDENCE_IDS[2]), "lane_id": "storage"},
        ],
        "edges": [
            {"id": "a-forward", "kind": "flow", "source": "actor", "target": "service"},
            {"id": "b-forward", "kind": "data", "source": "service", "target": "store"},
            {"id": "c-loop", "kind": "back", "source": "store", "target": "actor"},
        ],
        "groups": [],
        "lanes": [
            {"id": "request", **_label("Request", EVIDENCE_IDS[3])},
            {"id": "storage", **_label("Storage", EVIDENCE_IDS[4])},
        ],
        "constraints": [
            {"target": "actor", "rank": 0, "order": 0},
            {"target": "service", "rank": 1, "order": 0},
            {"target": "store", "rank": 2, "order": 0},
        ],
    }


class TimelineTests(unittest.TestCase):
    def test_sequential_and_swimlane_loop_are_bounded_and_closed(self) -> None:
        plan = normalize_visual_spec(spec("flow"), EVIDENCE)
        result = derive_timeline(plan)
        self.assertIsInstance(result, Timeline)
        expected_targets = tuple(
            sorted(
                {node.id for node in plan.nodes}
                | {edge.id for edge in plan.edges}
                | {lane.id for lane in plan.lanes}
            )
        )
        self.assertEqual(result.targets, expected_targets)
        self.assertEqual(result.reduced_motion, expected_targets)
        self.assertEqual(result.as_dict()["reduced_motion"], {"mode": "static", "visible": list(expected_targets)})
        self.assertGreater(result.duration_ms, 0)
        self.assertLessEqual(result.duration_ms, 30_000)
        previous_end = 0
        for operation in result.operations:
            self.assertIn(operation.kind, {"reveal", "emphasis"})
            self.assertIn(operation.target, expected_targets)
            self.assertGreaterEqual(operation.start_ms, previous_end)
            self.assertGreater(operation.end_ms, operation.start_ms)
            previous_end = operation.end_ms
        self.assertEqual(previous_end, result.duration_ms)
        self.assertEqual(
            [operation.target for operation in result.operations if operation.kind == "emphasis"][-1],
            "c-loop",
        )

    def test_layers_and_mapping_permutations_produce_identical_bytes(self) -> None:
        plan = normalize_visual_spec(spec(), EVIDENCE)
        expected = derive_timeline(plan).canonical_bytes()
        permuted = replace(plan, nodes=tuple(reversed(plan.nodes)), edges=tuple(reversed(plan.edges)))
        self.assertEqual(derive_timeline(permuted).canonical_bytes(), expected)
        payload = spec()
        for seed in range(12):
            candidate = _shuffle(copy.deepcopy(payload), random.Random(seed))
            self.assertEqual(derive_timeline(normalize_visual_spec(candidate, EVIDENCE)).canonical_bytes(), expected)

        self.assertEqual(result_bytes(derive_timeline(plan)), result_bytes(derive_timeline(plan)))

    def test_direct_timeline_boundary_rejects_unknown_duplicate_negative_and_overlap(self) -> None:
        targets = ("a", "b")
        def operation(identifier: str, target: str, start: int, end: int) -> dict[str, object]:
            return {"id": identifier, "kind": "reveal", "target": target, "start_ms": start, "end_ms": end}

        cases = (
            ("unknown", (operation("r", "x", 0, 10),), 10, "E_VISUAL_SPEC_EDGE"),
            ("duplicate", (operation("r", "a", 0, 5), operation("r", "b", 5, 10)), 10, "E_VISUAL_SPEC_ID"),
            ("target-duplicate", (operation("r1", "a", 0, 5), operation("r2", "a", 5, 10)), 10, "E_VISUAL_SPEC_ID"),
            ("negative", (operation("r", "a", -1, 5),), 5, "E_VISUAL_DETERMINISM"),
            ("overlap", (operation("r1", "a", 0, 6), operation("r2", "b", 5, 10)), 10, "E_VISUAL_DETERMINISM"),
            ("target-missing", (operation("r", "a", 0, 5),), 5, "E_VISUAL_DETERMINISM"),
        )
        for name, operations, duration, code in cases:
            with self.subTest(name=name):
                with self.assertRaises(ContractError) as raised:
                    Timeline(targets, duration, operations, targets)
                self.assertEqual(raised.exception.code, code)

    def test_plan_boundary_rejects_dangling_and_duplicate_ids(self) -> None:
        plan = normalize_visual_spec(spec(), EVIDENCE)
        dangling = replace(plan, edges=(replace(plan.edges[0], target="missing"), *plan.edges[1:]))
        with self.assertRaises(ContractError) as raised:
            derive_timeline(dangling)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EDGE")

        duplicate = replace(plan, nodes=(plan.nodes[0], plan.nodes[0], *plan.nodes[1:]))
        with self.assertRaises(ContractError) as raised:
            derive_timeline(duplicate)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")

    def test_projection_is_fresh_and_timeline_is_frozen(self) -> None:
        result = derive_timeline(normalize_visual_spec(spec(), EVIDENCE))
        projection = result.as_dict()
        projection["targets"].clear()  # type: ignore[union-attr]
        projection["reduced_motion"]["visible"].clear()  # type: ignore[index]
        self.assertTrue(result.targets)
        self.assertTrue(result.reduced_motion)
        with self.assertRaises(AttributeError):
            result.duration_ms = 9  # type: ignore[misc]


def result_bytes(value: Timeline) -> bytes:
    return value.canonical_bytes()


def _shuffle(value: object, rng: random.Random) -> object:
    if isinstance(value, dict):
        entries = list(value.items())
        rng.shuffle(entries)
        return {key: _shuffle(item, rng) for key, item in entries}
    if isinstance(value, list):
        return [_shuffle(item, rng) for item in value]
    return value


if __name__ == "__main__":
    unittest.main()
