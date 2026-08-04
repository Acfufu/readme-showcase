from __future__ import annotations

import unittest

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import (
    build_fact,
    compute_graph_sha256,
)
from skill.scripts.readme_showcase.visual_kernel.model import (
    MAX_VISUAL_SPEC_BYTES,
    VisualIntent,
    VisualSpec,
    canonical_visual_spec_bytes,
    validate_visual_spec,
)


FACT = build_fact(
    kind="file-presence",
    path="README.md",
    locator=None,
    semantic_key="readme",
    value=True,
    source_bytes=b"README",
)
EVIDENCE_ID = str(FACT["fact_id"])


def evidence_graph() -> dict[str, object]:
    fact = FACT
    return {
        "schema_version": 2,
        "facts": [fact],
        "evidence_sha256": compute_graph_sha256({"schema_version": 2, "facts": [fact]}),
    }


def flow_spec() -> dict[str, object]:
    return {
        "schema_version": 1,
        "intent": {"kind": "flow", "label": "Request flow", "evidence_ids": [EVIDENCE_ID]},
        "locale": "en",
        "variants": ["desktop", "mobile"],
        "nodes": [
            {"id": "client", "kind": "actor", "label": "Client", "evidence_ids": [EVIDENCE_ID]},
            {"id": "service", "kind": "service", "label": "Service", "evidence_ids": [EVIDENCE_ID]},
        ],
        "edges": [
            {
                "id": "request",
                "kind": "flow",
                "source": "client",
                "target": "service",
                "label": "request",
                "evidence_ids": [EVIDENCE_ID],
            }
        ],
        "groups": [],
        "lanes": [],
        "constraints": [],
    }


class VisualSpecTests(unittest.TestCase):
    def assert_code(self, code: str, payload: object, *, evidence: object | None = None) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_visual_spec(payload, evidence_graph=evidence)
        self.assertEqual(raised.exception.code, code)

    def test_two_node_evidence_bound_flow_is_canonical_and_isolated(self) -> None:
        payload = flow_spec()
        payload["nodes"][0]["label"] = "Cafe\u0301"  # type: ignore[index]
        validated = validate_visual_spec(payload)
        self.assertEqual(validated.variants, ("desktop", "mobile"))
        self.assertEqual([node.id for node in validated.nodes], ["client", "service"])
        self.assertEqual(validated.nodes[0].label, "Café")
        self.assertEqual(canonical_visual_spec_bytes(validated), canonical_visual_spec_bytes(validate_visual_spec(validated)))

        payload["nodes"] = []
        self.assertEqual([node.id for node in validated.nodes], ["client", "service"])
        isolated = validated.as_dict()
        isolated["nodes"][0]["label"] = "changed"  # type: ignore[index]
        self.assertEqual(validated.nodes[0].label, "Café")
        self.assertEqual(payload["nodes"], [])

        with self.assertRaises(AttributeError):
            validated.nodes = ()  # type: ignore[misc]

    def test_duplicate_id_dangling_edge_and_missing_evidence_are_hard_failures(self) -> None:
        duplicate = flow_spec()
        duplicate["nodes"] = [*duplicate["nodes"], {"id": "client", "kind": "note", "label": "Other", "evidence_ids": [EVIDENCE_ID]}]
        self.assert_code("E_VISUAL_SPEC_ID", duplicate)

        dangling = flow_spec()
        dangling["edges"][0]["target"] = "missing"  # type: ignore[index]
        self.assert_code("E_VISUAL_SPEC_EDGE", dangling)

        missing = flow_spec()
        missing["nodes"][0]["evidence_ids"] = []  # type: ignore[index]
        self.assert_code("E_VISUAL_SPEC_EVIDENCE", missing)

    def test_evidence_graph_membership_is_checked(self) -> None:
        payload = flow_spec()
        unknown = "file:" + "b" * 64
        payload["nodes"][0]["evidence_ids"] = [unknown]  # type: ignore[index]
        self.assert_code("E_VISUAL_SPEC_EVIDENCE", payload, evidence=evidence_graph())

    def test_group_lane_membership_and_constraints_reference_declared_ids(self) -> None:
        payload = flow_spec()
        payload["groups"] = [{"id": "boundary", "label": "Boundary", "evidence_ids": [EVIDENCE_ID]}]
        payload["lanes"] = [{"id": "server", "label": "Server", "evidence_ids": [EVIDENCE_ID]}]
        payload["nodes"][1]["group_id"] = "boundary"  # type: ignore[index]
        payload["nodes"][1]["lane_id"] = "server"  # type: ignore[index]
        payload["constraints"] = [{"target": "service", "rank": 1}]
        spec = validate_visual_spec(payload)
        self.assertEqual(spec.nodes[1].group_id, "boundary")
        self.assertEqual(spec.nodes[1].lane_id, "server")
        self.assertEqual(spec.constraints[0].rank, 1)

        invalid = flow_spec()
        invalid["nodes"][0]["group_id"] = "missing"  # type: ignore[index]
        self.assert_code("E_VISUAL_SPEC_EDGE", invalid)

        no_hint = flow_spec()
        no_hint["constraints"] = [{"target": "client"}]
        self.assert_code("E_SCHEMA_VALUE", no_hint)

    def test_visible_labels_require_evidence_but_structural_edges_may_be_unlabeled(self) -> None:
        unlabeled = flow_spec()
        unlabeled["edges"][0].pop("label")  # type: ignore[index]
        unlabeled["edges"][0].pop("evidence_ids")  # type: ignore[index]
        self.assertEqual(validate_visual_spec(unlabeled).edges[0].label, None)

        missing_node_label = flow_spec()
        missing_node_label["nodes"][0].pop("label")  # type: ignore[index]
        missing_node_label["nodes"][0].pop("evidence_ids")  # type: ignore[index]
        self.assert_code("E_SCHEMA_MISSING_FIELD", missing_node_label)

        edge_evidence_without_label = flow_spec()
        edge_evidence_without_label["edges"][0].pop("label")  # type: ignore[index]
        self.assert_code("E_VISUAL_SPEC_EVIDENCE", edge_evidence_without_label)

    def test_public_visual_values_are_revalidated_at_the_boundary(self) -> None:
        valid = validate_visual_spec(flow_spec())
        self.assertEqual(
            canonical_visual_spec_bytes(valid),
            canonical_visual_spec_bytes(validate_visual_spec(valid)),
        )

        invalid_version = VisualSpec(
            99,
            valid.intent,
            valid.locale,
            valid.variants,
            valid.nodes,
            valid.edges,
            valid.groups,
            valid.lanes,
            valid.constraints,
        )
        self.assert_code("E_SCHEMA_VERSION", invalid_version)

        missing_evidence = VisualSpec(
            valid.schema_version,
            VisualIntent(valid.intent.kind, valid.intent.label, ("file:" + "b" * 64,)),
            valid.locale,
            valid.variants,
            valid.nodes,
            valid.edges,
            valid.groups,
            valid.lanes,
            valid.constraints,
        )
        self.assert_code("E_VISUAL_SPEC_EVIDENCE", missing_evidence, evidence=evidence_graph())

    def test_float_unknown_resource_and_unsafe_path_fail_closed(self) -> None:
        float_value = flow_spec()
        float_value["constraints"] = [{"target": "client", "order": 1.0}]
        self.assert_code("E_SCHEMA_FLOAT", float_value)

        unknown = flow_spec()
        unknown["nodes"][0]["asset"] = "icon"  # type: ignore[index]
        self.assert_code("E_VISUAL_RESOURCE", unknown)

        path_field = flow_spec()
        path_field["nodes"][0]["path"] = "../icon.svg"  # type: ignore[index]
        self.assert_code("E_VISUAL_PATH", path_field)

        url = flow_spec()
        url["nodes"][0]["label"] = "https://example.invalid"  # type: ignore[index]
        self.assert_code("E_VISUAL_PATH", url)

        unknown_field = flow_spec()
        unknown_field["mystery"] = True
        self.assert_code("E_SCHEMA_UNKNOWN_FIELD", unknown_field)

        unsorted = flow_spec()
        unsorted["nodes"] = list(reversed(unsorted["nodes"]))
        self.assert_code("E_VISUAL_SPEC_ID", unsorted)

    def test_canonical_size_is_bounded(self) -> None:
        oversized = flow_spec()
        oversized["intent"]["label"] = "x" * MAX_VISUAL_SPEC_BYTES  # type: ignore[index]
        with self.assertRaises(ContractError) as raised:
            validate_visual_spec(oversized)
        self.assertEqual(raised.exception.code, "E_VISUAL_SPEC_SIZE")


if __name__ == "__main__":
    unittest.main()
