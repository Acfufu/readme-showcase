from __future__ import annotations

import json
import unittest
from dataclasses import replace

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.preview.interaction import (
    InteractionPreview,
    project_interaction_preview,
)
from skill.scripts.readme_showcase.visual_kernel.interaction import InteractionGraph, derive_interaction
from skill.scripts.readme_showcase.visual_kernel.normalize import normalize_visual_spec
from tests.unit.visual_kernel.test_interaction import EVIDENCE, _spec


def _interaction() -> InteractionGraph:
    return derive_interaction(normalize_visual_spec(_spec(), EVIDENCE))


def _labels(graph: InteractionGraph) -> dict[str, str]:
    return {identifier: identifier for identifier in graph.focus_order} | {
        "a": "<script>alert(1)</script>"
    }


class InteractionPreviewTests(unittest.TestCase):
    def test_public_surface_and_canonical_inert_projection(self) -> None:
        graph = _interaction()
        result = project_interaction_preview(graph, EVIDENCE, labels=_labels(graph))

        self.assertIsInstance(result, InteractionPreview)
        payload = result.as_dict()
        self.assertEqual(
            set(payload),
            {"schema_version", "interaction_sha256", "focus", "evidence", "adjacency", "fallback_order"},
        )
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["fallback_order"], list(graph.focus_order))
        self.assertEqual(
            [item["element_id"] for item in payload["focus"]],  # type: ignore[index]
            list(graph.focus_order),
        )
        self.assertEqual(
            next(item["label"] for item in payload["focus"] if item["element_id"] == "a"),  # type: ignore[index]
            "&lt;script&gt;alert(1)&lt;/script&gt;",
        )
        self.assertNotIn("<script>", result.canonical_bytes().decode("utf-8"))
        self.assertEqual(json.loads(result.canonical_bytes()), payload)
        self.assertEqual(result.sha256(), result.sha256())

    def test_permuted_input_maps_have_identical_canonical_bytes(self) -> None:
        graph = _interaction()
        labels = _labels(graph)
        first = project_interaction_preview(graph, EVIDENCE, labels=labels)
        reordered = InteractionGraph(
            graph.focus_order,
            dict(reversed(tuple(graph.evidence_links.items()))),
            dict(reversed(tuple(graph.adjacency.items()))),
            dict(reversed(tuple(graph.group_navigation.items()))),
            dict(reversed(tuple(graph.lane_navigation.items()))),
        )
        self.assertEqual(
            first.canonical_bytes(),
            project_interaction_preview(
                reordered,
                dict(reversed(tuple(EVIDENCE.items()))),
                labels=dict(reversed(tuple(labels.items()))),
            ).canonical_bytes(),
        )

    def test_duplicate_unknown_and_stale_bindings_fail_closed(self) -> None:
        graph = _interaction()
        raw = graph.as_dict()
        raw["focus_order"] = [graph.focus_order[0], graph.focus_order[0]]
        with self.assertRaises(ContractError) as raised:
            project_interaction_preview(raw, EVIDENCE, labels=_labels(graph))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_ID")

        unknown = replace(graph, evidence_links={**graph.evidence_links, "a": ("file:" + "f" * 64,)})
        with self.assertRaises(ContractError) as raised:
            project_interaction_preview(unknown, EVIDENCE, labels=_labels(graph))
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_EVIDENCE")

        result = project_interaction_preview(graph, EVIDENCE, labels=_labels(graph))
        element_hashes = {
            item["element_id"]: item["element_sha256"]  # type: ignore[index]
            for item in result.as_dict()["focus"]  # type: ignore[index]
        }
        element_hashes[graph.focus_order[0]] = "0" * 64
        with self.assertRaises(ContractError) as raised:
            project_interaction_preview(graph, EVIDENCE, labels=_labels(graph), element_hashes=element_hashes)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

        with self.assertRaises(ContractError) as raised:
            project_interaction_preview(graph, EVIDENCE, labels=_labels(graph), expected_interaction_sha256="0" * 64)
        self.assertEqual(raised.exception.code, "E_VISUAL_FINGERPRINT")

    def test_result_is_immutable_and_fallback_covers_every_focus_target(self) -> None:
        graph = _interaction()
        result = project_interaction_preview(graph, EVIDENCE, labels=_labels(graph))
        self.assertEqual(set(result.as_dict()["fallback_order"]), set(graph.focus_order))
        with self.assertRaises((AttributeError, TypeError)):
            result.payload = {}  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
