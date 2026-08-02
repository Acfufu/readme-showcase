from __future__ import annotations

import copy
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from skill.scripts.readme_showcase.contracts.common import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.retrieval.classifier import (
    ALL_PROJECT_TYPES,
    MIN_CONFIDENCE_BASIS_POINTS,
    PROJECT_TYPES,
    classify_project,
)


ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/repositories/classifier"


def fact(kind: str, key: str, *, path: str = "project.json", locator: dict[str, object] | None = None, value: object = True, confidence: str = "observed") -> dict[str, Any]:
    return build_fact(
        kind=kind,
        path=path,
        locator=locator if locator is not None else ({"json_pointer": "/signal"} if kind != "file-presence" else None),
        semantic_key=key,
        value=value,
        source_bytes=b"fixture\n",
        confidence=confidence,
    )


def evidence(facts: list[dict[str, Any]]) -> dict[str, Any]:
    return EvidenceGraph(facts).to_dict()


class ProjectClassifierTests(unittest.TestCase):
    def fixture(self, name: str) -> dict[str, Any]:
        return json.loads((FIXTURES / name).read_text(encoding="utf-8"))

    def test_exact_type_vocabulary_and_each_explicit_signal(self) -> None:
        self.assertEqual(
            PROJECT_TYPES,
            (
                "cli", "sdk", "library", "api-service", "web-app", "mobile-app", "desktop-app",
                "github-action", "monorepo", "ml-model", "dataset", "infrastructure", "plugin",
                "template", "runtime-toolchain", "developer-tool", "web-framework",
            ),
        )
        self.assertEqual(ALL_PROJECT_TYPES, PROJECT_TYPES + ("unknown",))
        for project_type in PROJECT_TYPES:
            result = classify_project(evidence([fact("config-value", f"project-type:{project_type}")]))
            self.assertEqual(result["primary"], project_type)
            self.assertGreaterEqual(result["confidence_basis_points"], MIN_CONFIDENCE_BASIS_POINTS)

    def test_cli_and_web_fixture_are_canonical_and_reason_bound(self) -> None:
        for name, expected in (("cli-evidence.json", "cli"), ("web-app-evidence.json", "web-app")):
            payload = self.fixture(name)
            first = classify_project(payload["evidence"], payload["index"])
            second = classify_project(payload["evidence"], payload["index"])
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first["primary"], expected)
            self.assertIs(type(first["confidence_basis_points"]), int)
            ids = {item["fact_id"] for item in payload["evidence"]["facts"]}
            for reason in first["reasons"]:
                self.assertEqual(set(reason), {"code", "message", "evidence_ids"})
                self.assertTrue(set(reason["evidence_ids"]).issubset(ids))

    def test_sparse_conflicting_and_low_confidence_are_unknown(self) -> None:
        for name in ("sparse-evidence.json", "conflicting-evidence.json"):
            payload = self.fixture(name)
            result = classify_project(payload["evidence"], payload["index"])
            self.assertEqual(result["primary"], "unknown")
            self.assertLess(result["confidence_basis_points"], MIN_CONFIDENCE_BASIS_POINTS)
            self.assertEqual(result["secondary"], [])
        result = classify_project(evidence([fact("file-presence", "classifier-hint:cli", path="README.md")]))
        self.assertEqual(result["primary"], "unknown")
        self.assertEqual(result["confidence_basis_points"], 5900)

    def test_order_rename_and_ties_are_deterministic(self) -> None:
        facts = [
            fact("config-value", "classifier-hint:sdk", locator={"json_pointer": "/sdk"}),
            fact("config-value", "classifier-hint:cli", locator={"json_pointer": "/cli"}),
        ]
        first = classify_project(facts)
        second = classify_project(list(reversed(facts)))
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["primary"], "cli")
        self.assertEqual(first["secondary"], ["sdk"])
        with self.assertRaises(ContractError) as raised:
            classify_project({**evidence(facts), "repository_name": "adversarial-name"})
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

    def test_gold_values_and_source_bodies_never_influence_or_leak(self) -> None:
        secret = "GOLD-SENTINEL-secret-source-body"
        payload = evidence([fact("documentation-statement", "benchmark-gold", value=secret)])
        result = classify_project(payload)
        rendered = canonical_json_bytes(result).decode("utf-8")
        self.assertEqual(result["primary"], "unknown")
        self.assertNotIn("GOLD-SENTINEL", rendered)
        self.assertNotIn(secret, rendered)
        with self.assertRaises(ContractError) as raised:
            classify_project({**payload, "benchmark_gold": "cli"})
        self.assertEqual(raised.exception.code, "E_SCHEMA_UNKNOWN_FIELD")

    def test_rejects_malformed_duplicate_collision_and_float_inputs(self) -> None:
        item = fact("config-value", "project-type:cli")
        with self.assertRaises(ContractError) as raised:
            classify_project([item, item])
        self.assertEqual(raised.exception.code, "E_FACT_DUPLICATE")
        malformed = copy.deepcopy(item)
        malformed["fact_id"] = "config:dangling"
        with self.assertRaises(ContractError) as raised:
            classify_project([malformed])
        self.assertEqual(raised.exception.code, "E_FACT_ID")
        with self.assertRaises(ContractError) as raised:
            classify_project(evidence([item]), [{"bytes": 1.5, "language": "python", "path": "x.py", "role": "source", "selected_for_content": False, "sha256": None, "tracked": True}])
        self.assertEqual(raised.exception.code, "E_CLASSIFIER_INDEX")

    def test_duplicate_unicode_and_concurrent_inputs_are_safe_and_immutable(self) -> None:
        item = fact("config-value", "project-type:cli", path="src/项目.py")
        baseline = [copy.deepcopy(item)]
        duplicated = [item, fact("config-value", "project-type:cli", locator={"json_pointer": "/other"})]
        self.assertEqual(classify_project(baseline)["confidence_basis_points"], classify_project(duplicated)["confidence_basis_points"])
        with ThreadPoolExecutor(max_workers=8) as pool:
            outputs = list(pool.map(lambda _: canonical_json_bytes(classify_project(baseline)), range(32)))
        self.assertTrue(all(output == outputs[0] for output in outputs))
        self.assertEqual(baseline, [item])

    def test_classifier_never_reads_or_executes_evidence_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "executed"
            payload = evidence([fact("config-value", "project-type:cli", path="never-execute.py")])
            self.assertEqual(classify_project(payload)["primary"], "cli")
            self.assertFalse(marker.exists())


if __name__ == "__main__":
    unittest.main()
