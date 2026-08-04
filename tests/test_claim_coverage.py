from __future__ import annotations

import copy
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import (
    ContractError,
    canonical_sha256,
    write_canonical_json_atomic,
)
from skill.scripts.readme_showcase.contracts.claims import (
    canonical_claim_map_bytes,
    validate_claim_map,
)
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.pipeline_core import validate_generated_bundle
from tests import test_bundle_contracts as bundle_contracts


REPO_ROOT = Path(__file__).resolve().parents[1]


class ClaimCoverageTests(unittest.TestCase):
    def helper(self) -> bundle_contracts.BundleContractTests:
        return bundle_contracts.BundleContractTests(methodName="runTest")

    def claim(
        self,
        claim_id: str,
        content: str,
        truth_id: str,
        evidence_sha256: str,
        *,
        language_pair_id: str | None = None,
    ) -> dict[str, object]:
        return {
            "claim_id": claim_id,
            "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
            "claim_kind": "factual",
            "evidence_sha256": evidence_sha256,
            "truth_id": truth_id,
            "language_pair_id": language_pair_id,
        }

    def write_evidence(
        self,
        root: Path,
        facts: dict[str, tuple[str, str]],
    ) -> None:
        files = []
        fact_rows = []
        for index, (fact_id, (content, digest)) in enumerate(sorted(facts.items())):
            path = f"source/{index:03d}.txt"
            files.append(
                {
                    "path": path,
                    "bytes": len(content.encode()),
                    "lines": len(content.splitlines()),
                    "sha256": digest,
                    "content": content,
                }
            )
            fact_rows.append(
                {
                    "fact_id": fact_id,
                    "kind": "repository-file",
                    "path": path,
                    "evidence_sha256": digest,
                }
            )
        write_canonical_json_atomic(
            root / "repository-evidence.json",
            {
                "schema_version": 1,
                "status": "complete",
                "target": {"name": "repository", "base_sha": "a" * 40},
                "scan_limits": {},
                "files": files,
                "facts": fact_rows,
                "warnings": [],
            },
        )

    def set_contracts(
        self,
        root: Path,
        bundle: dict[str, Any],
        markdown_claims: list[dict[str, object]],
        diagram_claims: list[dict[str, object]],
        facts: dict[str, tuple[str, str]],
    ) -> None:
        claims = {
            "schema_version": 1,
            "markdown_blocks": sorted(markdown_claims, key=lambda item: str(item["claim_id"])),
            "diagram_labels": sorted(diagram_claims, key=lambda item: str(item["claim_id"])),
        }
        claims_path = root / bundle["artifacts"]["claim_map"]["path"]
        write_canonical_json_atomic(claims_path, claims)
        bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)

        plan_path = root / bundle["artifacts"]["plan"]["path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["evidence_ids"] = sorted(facts)
        write_canonical_json_atomic(plan_path, plan)
        bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)
        self.write_evidence(root, facts)

    def monolingual_bundle(
        self,
        root: Path,
        *,
        elk: bool = False,
    ) -> dict[str, Any]:
        helper = self.helper()
        bundle, _ = helper.make_bundle(
            root,
            "asset-only" if elk else "readme",
            elk=elk,
        )
        markdown_claims: list[dict[str, object]] = []
        diagram_claims: list[dict[str, object]] = []
        facts: dict[str, tuple[str, str]] = {}
        if elk:
            semantic = json.loads(
                (root / "assets/readme/diagram.diagram.json").read_text(encoding="utf-8")
            )
            labels = [
                (semantic["accessibility_claim_id"], semantic["accessibility_title"]),
                *[(item["claim_id"], item["label"]) for item in semantic["groups"]],
                *[(item["claim_id"], item["label"]) for item in semantic["nodes"]],
                *[
                    (item["claim_id"], item["label"])
                    for item in semantic["edges"]
                    if item["label"] is not None
                ],
            ]
            for index, (claim_id, label) in enumerate(labels):
                truth_id = "file:README.md" if index == 0 else f"fact:diagram:{index}"
                source = f"target evidence for {claim_id}\n"
                digest = hashlib.sha256(source.encode()).hexdigest()
                facts[truth_id] = (source, digest)
                diagram_claims.append(
                    self.claim(claim_id, label, truth_id, digest)
                )
        else:
            readme = (root / "README.generated.md").read_text(encoding="utf-8")
            blocks = readme.strip().split("\n\n")
            for index, block in enumerate(blocks):
                truth_id = "file:README.md" if index == 2 else f"fact:markdown:{index}"
                source = f"target evidence for block {index}\n"
                digest = hashlib.sha256(source.encode()).hexdigest()
                facts[truth_id] = (source, digest)
                markdown_claims.append(
                    self.claim(
                        f"markdown:en:{index:03d}",
                        block,
                        truth_id,
                        digest,
                    )
                )
        self.set_contracts(root, bundle, markdown_claims, diagram_claims, facts)
        return bundle

    def bilingual_bundle(self, root: Path) -> dict[str, Any]:
        helper = self.helper()
        bundle, _ = helper.make_bundle(root, "readme")
        texts = {
            "en": (
                "# Demo\n\n"
                "[简体中文](README_zh.md)\n\n"
                "![Project architecture](assets/readme/diagram.svg)\n\n"
                "Evidence-bound architecture.\n"
            ),
            "zh": (
                "# 示例\n\n"
                "[English](README.md)\n\n"
                "![项目架构](assets/readme/diagram.svg)\n\n"
                "有证据支持的架构。\n"
            ),
        }
        readme_ref = helper.write_bytes(root, "README.md", texts["en"].encode())
        bundle["candidate"]["readme"] = readme_ref
        (root / "README_zh.md").write_text(texts["zh"], encoding="utf-8")
        plan_path = root / bundle["artifacts"]["plan"]["path"]
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        plan["languages"] = ["en", "zh"]
        write_canonical_json_atomic(plan_path, plan)
        bundle["artifacts"]["plan"]["sha256"] = canonical_sha256(plan)

        claims = []
        facts: dict[str, tuple[str, str]] = {}
        for index, pair in enumerate(zip(
            texts["en"].strip().split("\n\n"),
            texts["zh"].strip().split("\n\n"),
        )):
            truth_id = f"truth:pair:{index}"
            source = f"target evidence for pair {index}\n"
            digest = hashlib.sha256(source.encode()).hexdigest()
            facts[truth_id] = (source, digest)
            for language, block in zip(("en", "zh"), pair):
                claims.append(
                    self.claim(
                        f"markdown:{language}:{index:03d}",
                        block,
                        truth_id,
                        digest,
                        language_pair_id=f"pair:{index:03d}",
                    )
                )
        manifest_path = root / bundle["artifacts"]["asset_manifest"]["path"]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest["assets"][0]["truth_ids"] = ["truth:pair:3"]
        write_canonical_json_atomic(manifest_path, manifest)
        bundle["artifacts"]["asset_manifest"]["sha256"] = canonical_sha256(manifest)
        self.set_contracts(root, bundle, claims, [], facts)
        return bundle

    def assert_code(
        self,
        root: Path,
        bundle: dict[str, Any],
        code: str,
    ) -> None:
        with self.assertRaises(ContractError) as raised:
            validate_generated_bundle(bundle, root)
        self.assertEqual(raised.exception.code, code)

    def visual_spec_fixture(self) -> tuple[dict[str, Any], dict[str, Any], str, str]:
        primary = build_fact(
            kind="file-presence",
            path="README.md",
            locator=None,
            semantic_key="architecture",
            value=True,
            source_bytes=b"README architecture\n",
        )
        secondary = build_fact(
            kind="file-presence",
            path="pyproject.toml",
            locator=None,
            semantic_key="metadata",
            value=True,
            source_bytes=b"[project]\n",
        )
        graph = EvidenceGraph([primary, secondary]).to_dict()
        fact_id = str(primary["fact_id"])
        secondary_id = str(secondary["fact_id"])
        spec = {
            "schema_version": 1,
            "intent": {"kind": "flow", "label": "Architecture", "evidence_ids": [fact_id]},
            "locale": "en",
            "variants": ["desktop"],
            "nodes": [
                {"id": "client", "kind": "actor", "label": "Client", "evidence_ids": [fact_id]},
                {"id": "service", "kind": "service", "label": "Service", "evidence_ids": [fact_id]},
            ],
            "edges": [{
                "id": "request",
                "kind": "flow",
                "source": "client",
                "target": "service",
                "label": "Request",
                "evidence_ids": [fact_id],
            }],
            "groups": [],
            "lanes": [],
            "constraints": [],
        }
        return graph, spec, fact_id, secondary_id

    def v3_claim_map(
        self,
        fact_id: str,
        *,
        secondary_id: str | None = None,
    ) -> dict[str, Any]:
        markdown = [
            {
                "claim_id": "markdown:en:overview",
                "content_sha256": hashlib.sha256(b"Overview").hexdigest(),
                "claim_kind": "factual",
                "evidence_ids": [fact_id],
                "language_pair_id": "overview",
                "support_level": "direct",
            },
            {
                "claim_id": "markdown:zh-Hans:overview",
                "content_sha256": hashlib.sha256("概览".encode()).hexdigest(),
                "claim_kind": "factual",
                "evidence_ids": [fact_id],
                "language_pair_id": "overview",
                "support_level": "direct",
            },
        ]
        element_rows = []
        labels = {"client": "Client", "request": "Request", "service": "Service"}
        for element_id in ("client", "request", "service"):
            element_rows.append({
                "claim_id": f"diagram:en:{element_id}",
                "content_sha256": hashlib.sha256(labels[element_id].encode()).hexdigest(),
                "claim_kind": "factual",
                "evidence_ids": [fact_id],
                "language_pair_id": None,
                "support_level": "direct",
                "element_id": element_id,
            })
        if secondary_id is not None:
            element_rows[0]["evidence_ids"] = [secondary_id]
        return {
            "schema_version": 3,
            "markdown_blocks": markdown,
            "diagram_labels": element_rows,
        }

    def test_v3_bindings_require_visual_spec_and_leave_v2_bytes_unchanged(self) -> None:
        graph, spec, fact_id, _ = self.visual_spec_fixture()
        payload = self.v3_claim_map(fact_id)
        normalized = validate_claim_map(payload, evidence_graph=graph, visual_spec=spec)
        self.assertEqual(
            [row["element_id"] for row in normalized["diagram_labels"]],
            ["client", "request", "service"],
        )
        with self.assertRaises(ContractError) as missing_spec:
            validate_claim_map(payload, evidence_graph=graph)
        self.assertEqual(missing_spec.exception.code, "E_CLAIM_COVERAGE")

        v2 = {
            "schema_version": 2,
            "markdown_blocks": copy.deepcopy(payload["markdown_blocks"]),
            "diagram_labels": [],
        }
        before = canonical_claim_map_bytes(v2, evidence_graph=graph)
        after = canonical_claim_map_bytes(v2, evidence_graph=graph, visual_spec=spec)
        self.assertEqual(before, after)

    def test_v3_element_locale_evidence_and_decorative_failures_are_typed(self) -> None:
        graph, spec, fact_id, secondary_id = self.visual_spec_fixture()
        cases: list[tuple[str, str, Any]] = []

        missing_element = self.v3_claim_map(fact_id)
        missing_element["diagram_labels"][0].pop("element_id")
        cases.append(("missing-element", "E_CLAIM_COVERAGE", missing_element))

        duplicate_element = self.v3_claim_map(fact_id)
        duplicate_element["diagram_labels"][1]["element_id"] = "client"
        cases.append(("duplicate-element", "E_CLAIM_COVERAGE", duplicate_element))

        unknown_element = self.v3_claim_map(fact_id)
        unknown_element["diagram_labels"][0]["element_id"] = "missing"
        cases.append(("unknown-element", "E_CLAIM_COVERAGE", unknown_element))

        locale_drift = self.v3_claim_map(fact_id)
        locale_drift["diagram_labels"][0]["claim_id"] = "diagram:zh-Hans:client"
        locale_drift["diagram_labels"].sort(key=lambda item: item["claim_id"])
        cases.append(("locale-drift", "E_CLAIM_EVIDENCE", locale_drift))

        evidence_drift = self.v3_claim_map(fact_id, secondary_id=secondary_id)
        cases.append(("evidence-drift", "E_CLAIM_EVIDENCE", evidence_drift))

        content_drift = self.v3_claim_map(fact_id)
        content_drift["diagram_labels"][0]["content_sha256"] = "f" * 64
        cases.append(("content-drift", "E_CLAIM_COVERAGE", content_drift))

        uncovered = self.v3_claim_map(fact_id)
        uncovered["diagram_labels"].pop()
        cases.append(("uncovered-label", "E_CLAIM_COVERAGE", uncovered))

        decorative = self.v3_claim_map(fact_id)
        decorative["diagram_labels"][0]["claim_kind"] = "decorative"
        cases.append(("decorative-label", "E_CLAIM_COVERAGE", decorative))

        for name, expected, candidate in cases:
            with self.subTest(case=name):
                with self.assertRaises(ContractError) as raised:
                    validate_claim_map(candidate, evidence_graph=graph, visual_spec=spec)
                self.assertEqual(raised.exception.code, expected)

    def test_complete_monolingual_markdown_and_elk_labels_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            for name, elk in (("markdown", False), ("elk", True)):
                with self.subTest(name=name):
                    root = base / name
                    root.mkdir()
                    bundle = self.monolingual_bundle(root, elk=elk)
                    self.assertEqual(validate_generated_bundle(bundle, root)["status"], "pass")

    def test_valid_bilingual_pairs_pass(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.bilingual_bundle(root)
            self.assertEqual(validate_generated_bundle(bundle, root)["status"], "pass")

    def test_edit_missing_orphan_duplicate_and_non_target_evidence_fail(self) -> None:
        cases = ("edit", "missing", "orphan", "duplicate", "retrieval")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.monolingual_bundle(root)
                claims_path = root / bundle["artifacts"]["claim_map"]["path"]
                claims = json.loads(claims_path.read_text(encoding="utf-8"))
                if case == "edit":
                    readme = root / bundle["candidate"]["readme"]["path"]
                    readme.write_text(
                        readme.read_text(encoding="utf-8").replace("Generated", "Generated!"),
                        encoding="utf-8",
                    )
                    bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(
                        readme.read_bytes()
                    ).hexdigest()
                elif case == "missing":
                    claims["markdown_blocks"].pop(0)
                elif case == "orphan":
                    claims["markdown_blocks"].append(
                        self.claim("markdown:en:999", "orphan", "fact:orphan", "9" * 64)
                    )
                elif case == "duplicate":
                    duplicate = dict(claims["markdown_blocks"][0])
                    duplicate["truth_id"] = "fact:duplicate"
                    duplicate["evidence_sha256"] = "8" * 64
                    claims["markdown_blocks"].append(duplicate)
                else:
                    claims["markdown_blocks"][0]["truth_id"] = "retrieval:gold"
                claims["markdown_blocks"].sort(key=lambda item: item["claim_id"])
                write_canonical_json_atomic(claims_path, claims)
                bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)

                expected = (
                    "E_CLAIM_DUPLICATE"
                    if case == "duplicate"
                    else "E_CLAIM_EVIDENCE"
                    if case in {"orphan", "retrieval"}
                    else "E_CLAIM_COVERAGE"
                )
                self.assert_code(root, bundle, expected)

    def test_wrong_language_pair_and_missing_diagram_label_fail(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "language"
            root.mkdir()
            bundle = self.bilingual_bundle(root)
            claims_path = root / bundle["artifacts"]["claim_map"]["path"]
            claims = json.loads(claims_path.read_text(encoding="utf-8"))
            claims["markdown_blocks"][0]["language_pair_id"] = None
            write_canonical_json_atomic(claims_path, claims)
            bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
            self.assert_code(root, bundle, "E_CLAIM_LANGUAGE")

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.monolingual_bundle(root, elk=True)
            claims_path = root / bundle["artifacts"]["claim_map"]["path"]
            claims = json.loads(claims_path.read_text(encoding="utf-8"))
            claims["diagram_labels"].pop()
            write_canonical_json_atomic(claims_path, claims)
            bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
            self.assert_code(root, bundle, "E_CLAIM_COVERAGE")

    def test_cli_validates_complete_coverage(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.monolingual_bundle(root)
            bundle_path = root / "generated-readme-bundle.json"
            write_canonical_json_atomic(bundle_path, bundle)
            result = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "validate-bundle",
                    "--bundle",
                    str(bundle_path),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
