from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.common import canonical_json_bytes, read_source_bytes
from skill.scripts.readme_showcase.contracts.evidence import (
    build_fact,
    compute_evidence_sha256,
    compute_fact_id,
    validate_claim_support,
    validate_evidence_graph,
    validate_fact,
)
from skill.scripts.readme_showcase.evidence.adapters import adapt_v1_file_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class EvidenceV2ContractTests(unittest.TestCase):
    def fact(self, **changes: object) -> dict[str, object]:
        arguments: dict[str, object] = {
            "kind": "file-snippet",
            "path": "README.md",
            "locator": {"line_start": 1, "line_end": 2},
            "semantic_key": "overview",
            "value": "Project overview",
            "source_bytes": b"# Demo\nProject overview\n",
            "confidence": "observed",
        }
        arguments.update(changes)
        return build_fact(**arguments)

    def assert_code(self, code: str, function: object, *arguments: object, **keywords: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*arguments, **keywords)  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)

    def test_exact_hash_formulas_match_independent_stdlib(self) -> None:
        fact = self.fact()
        identity = {
            "kind": "file-snippet",
            "locator": {"line_end": 2, "line_start": 1},
            "path": "README.md",
            "semantic_key": "overview",
        }
        identity_bytes = (json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.assertEqual(fact["fact_id"], f"snippet:{hashlib.sha256(identity_bytes).hexdigest()}")
        semantics = {key: value for key, value in fact.items() if key != "evidence_sha256"}
        semantic_bytes = (json.dumps(semantics, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self.assertEqual(fact["evidence_sha256"], hashlib.sha256(semantic_bytes).hexdigest())
        self.assertEqual(compute_fact_id("file-snippet", "README.md", identity["locator"], "overview"), fact["fact_id"])
        self.assertEqual(compute_evidence_sha256(fact), fact["evidence_sha256"])

    def test_locators_are_exclusive_closed_and_normative(self) -> None:
        self.assert_code(
            "E_EVIDENCE_LOCATOR",
            build_fact,
            kind="file-snippet",
            path="README.md",
            locator={"line_start": 2, "line_end": 1},
            semantic_key="x",
            value=True,
            source_bytes=b"x",
        )
        self.assert_code(
            "E_EVIDENCE_LOCATOR",
            build_fact,
            kind="code-symbol",
            path="src/main.py",
            locator={"symbol": "demo.main", "line_start": 1, "line_end": 1},
            semantic_key="main",
            value=True,
            source_bytes=b"def main(): pass\n",
        )
        self.assert_code(
            "E_EVIDENCE_LOCATOR",
            build_fact,
            kind="config-value",
            path="package.json",
            locator={"json_pointer": "/scripts/~2build"},
            semantic_key="build",
            value="npm test",
            source_bytes=b"{}\n",
        )
        pointer = build_fact(
            kind="config-value",
            path="package.json",
            locator={"json_pointer": "/a~1b/~0name"},
            semantic_key="escaped",
            value=True,
            source_bytes=b"{}\n",
        )
        self.assertEqual(pointer["source"]["json_pointer"], "/a~1b/~0name")  # type: ignore[index]

    def test_file_presence_is_only_locator_free_kind(self) -> None:
        present = build_fact(
            kind="file-presence",
            path="README.md",
            locator=None,
            semantic_key="presence",
            value=True,
            source_sha256="0" * 64,
        )
        self.assertEqual(present["source"], {"path": "README.md"})
        self.assert_code(
            "E_EVIDENCE_LOCATOR",
            build_fact,
            kind="command-observation",
            path="evidence/test.txt",
            locator=None,
            semantic_key="tests",
            value={"exit_code": 0},
            source_bytes=b"ok\n",
        )

    def test_semantic_and_source_mutations_fail_closed(self) -> None:
        fact = self.fact()
        semantic_mutation = copy.deepcopy(fact)
        semantic_mutation["value"] = "misleading success"
        self.assert_code("E_EVIDENCE_HASH", validate_fact, semantic_mutation)
        self.assert_code("E_SOURCE_HASH", validate_fact, fact, source_bytes=b"changed\n")

    def test_duplicate_and_collision_insertions_fail_in_any_order(self) -> None:
        first = self.fact()
        collision = copy.deepcopy(first)
        collision["value"] = "collision"
        stale_collision = copy.deepcopy(collision)
        collision["evidence_sha256"] = compute_evidence_sha256(collision)
        for left, right in ((first, first), (first, stale_collision), (first, collision), (collision, first)):
            graph = EvidenceGraph()
            graph.add(left)
            self.assert_code("E_FACT_DUPLICATE", graph.add, right)

    def test_confidence_and_claim_support_are_strict(self) -> None:
        self.assert_code(
            "E_EVIDENCE_DERIVATION",
            build_fact,
            kind="code-symbol",
            path="src/main.py",
            locator={"symbol": "demo.main"},
            semantic_key="main",
            value=True,
            source_bytes=b"def main(): pass\n",
            confidence="derived",
        )
        documented = self.fact(confidence="documented")
        self.assert_code("E_EVIDENCE_CONFIDENCE", validate_claim_support, documented, observed_behavior=True)

    def test_v1_adapter_is_deterministic_and_does_not_mutate_legacy_bytes(self) -> None:
        legacy = {
            "fact_id": "file:README.md",
            "kind": "repository-file",
            "path": "README.md",
            "evidence_sha256": "0" * 64,
        }
        before = canonical_json_bytes(legacy)
        first = adapt_v1_file_fact(legacy)
        second = adapt_v1_file_fact(copy.deepcopy(legacy))
        self.assertEqual(first, second)
        self.assertEqual(canonical_json_bytes(legacy), before)
        self.assertEqual(first["kind"], "file-presence")
        self.assertEqual(first["confidence"], "observed")
        self.assertEqual(first["source_sha256"], legacy["evidence_sha256"])

    def test_path_unicode_normalization_and_traversal_rejection(self) -> None:
        decomposed = "docs/Cafe\u0301.md"
        fact = build_fact(
            kind="file-presence",
            path=decomposed,
            locator=None,
            semantic_key="pre\u0301sence",
            value=True,
            source_sha256="0" * 64,
        )
        self.assertEqual(fact["source"]["path"], "docs/Caf\u00e9.md")  # type: ignore[index]
        for path in ("../secret", "a/../../secret", "/absolute", "a\\b", "~/secret"):
            self.assert_code(
                "E_EVIDENCE_PATH",
                build_fact,
                kind="file-presence",
                path=path,
                locator=None,
                semantic_key="presence",
                value=True,
                source_sha256="0" * 64,
            )

    def test_source_reader_rejects_symlinks_and_unbounded_reads(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "source.txt").write_bytes(b"safe\n")
            (root / "linked.txt").symlink_to(root / "source.txt")
            self.assertEqual(read_source_bytes(root, "source.txt"), b"safe\n")
            self.assert_code("E_EVIDENCE_PATH", read_source_bytes, root, "linked.txt")
            self.assert_code("E_EVIDENCE_LIMIT", read_source_bytes, root, "source.txt", maximum=4)
            self.assert_code("E_EVIDENCE_LIMIT", read_source_bytes, root, "source.txt", maximum=9 * 1024 * 1024)

    def test_graph_order_and_hash_are_insertion_independent(self) -> None:
        line = self.fact()
        symbol = build_fact(
            kind="code-symbol",
            path="src/main.py",
            locator={"symbol": "demo.main"},
            semantic_key="entrypoint",
            value="main",
            source_bytes=b"def main(): pass\n",
        )
        left = EvidenceGraph([line, symbol]).to_dict()
        right = EvidenceGraph([symbol, line]).to_dict()
        self.assertEqual(left, right)
        self.assertEqual(validate_evidence_graph(left), left)

    def test_schema_and_named_invalid_fixtures_have_validator_parity(self) -> None:
        valid = json.loads((FIXTURES / "repository-evidence-v2.valid.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_evidence_graph(valid), valid)
        invalid = json.loads((FIXTURES / "repository-evidence-v2.invalid.json").read_text(encoding="utf-8"))
        for case in invalid["cases"]:
            with self.subTest(case=case["name"]):
                self.assert_code(case["code"], validate_evidence_graph, case["payload"])

    def test_validation_has_no_filesystem_side_effect(self) -> None:
        packet = EvidenceGraph([self.fact()]).to_dict()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            before = list(root.iterdir())
            validate_evidence_graph(packet)
            self.assertEqual(list(root.iterdir()), before)


if __name__ == "__main__":
    unittest.main()
