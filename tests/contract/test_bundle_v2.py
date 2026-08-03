from __future__ import annotations

import copy
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any, Callable

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.assets import validate_asset_manifest
from skill.scripts.readme_showcase.contracts.claims import adapt_v1_claim_map, validate_claim_map
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.contracts.plan import validate_readme_plan
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.generation.assembler import (
    assemble_generated_bundle,
    canonical_markdown_blocks,
    validate_generated_bundle_v2,
    write_generated_bundle_atomic,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class BundleV2ContractTests(unittest.TestCase):
    def test_explicit_plan_asset_and_generalized_pair_contracts(self) -> None:
        plan = {
            "schema_version": 2,
            "mode": "readme",
            "locales": [
                {"tag": "en", "readme_path": "docs/start.md"},
                {"tag": "ja", "readme_path": "docs/not-zh.md"},
            ],
            "sections": ["overview"],
            "visual_intent": "hero",
            "diagram_route": "static",
            "commands": [],
            "evidence_ids": ["file:" + "a" * 64],
        }
        self.assertEqual(validate_readme_plan(plan)["locales"], plan["locales"])

        fact = self.fact()
        graph = self.graph(fact)
        claim = self.claim_map(fact["fact_id"])
        first = claim["markdown_blocks"][0]
        first["language_pair_id"] = "overview"
        second = copy.deepcopy(first)
        second["claim_id"] = "markdown:ja:overview"
        second["content_sha256"] = "1" * 64
        claim["markdown_blocks"].append(second)
        validate_claim_map(claim, evidence_graph=graph)

        neutral = {
            "schema_version": 2,
            "assets": [{
                "asset_id": "hero",
                "path": "assets/deceptive-zh.png",
                "language_neutral": True,
                "provenance": {"kind": "derived", "path": "source/README.md", "sha256": hashlib.sha256(b"source evidence\n").hexdigest()},
                "artifact_sha256": "2" * 64,
                "candidate_sha256": "2" * 64,
                "evidence_ids": [fact["fact_id"]],
            }],
        }
        self.assertTrue(validate_asset_manifest(neutral, evidence_graph=graph)["assets"][0]["language_neutral"])

    def assert_code(self, code: str, function: Callable[..., Any], *args: object, **kwargs: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def schema(self, name: str) -> Draft202012Validator:
        payload = json.loads((ROOT / "skill" / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(payload)
        return Draft202012Validator(payload)

    def fact(self, *, path: str = "source/README.md", source: bytes = b"source evidence\n") -> dict[str, Any]:
        return build_fact(
            kind="file-presence",
            path=path,
            locator=None,
            semantic_key="presence",
            value=True,
            source_bytes=source,
        )

    def graph(self, fact: dict[str, Any] | None = None) -> dict[str, Any]:
        return EvidenceGraph([fact or self.fact()]).to_dict()

    def claim_map(self, fact_id: str) -> dict[str, Any]:
        return {
            "schema_version": 2,
            "markdown_blocks": [
                {
                    "claim_id": "markdown:en:overview",
                    "content_sha256": "0" * 64,
                    "claim_kind": "factual",
                    "evidence_ids": [fact_id],
                    "language_pair_id": None,
                    "support_level": "direct",
                }
            ],
            "diagram_labels": [],
        }

    def test_plan_claim_asset_fixtures_have_schema_python_parity(self) -> None:
        graph = self.graph()
        fact_id = graph["facts"][0]["fact_id"]
        validators = {
            "readme-plan-v2": lambda value: validate_readme_plan(value),
            "claim-map-v2": lambda value: validate_claim_map(value, evidence_graph=graph),
            "asset-manifest-v2": lambda value: validate_asset_manifest(value, evidence_graph=graph),
        }
        for stem, validator in validators.items():
            valid = json.loads((FIXTURES / f"{stem}.valid.json").read_text(encoding="utf-8"))
            valid = json.loads(json.dumps(valid).replace("FACT_ID", str(fact_id)))
            self.assertEqual(list(self.schema(f"{stem.removesuffix('-v2')}.v2.schema.json").iter_errors(valid)), [])
            self.assertEqual(validator(valid), valid)
            invalid = json.loads((FIXTURES / f"{stem}.invalid.json").read_text(encoding="utf-8"))
            for case in invalid["cases"]:
                payload = json.loads(json.dumps(case["payload"]).replace("FACT_ID", str(fact_id)))
                with self.subTest(stem=stem, case=case["name"]):
                    self.assertTrue(list(self.schema(f"{stem.removesuffix('-v2')}.v2.schema.json").iter_errors(payload)) or case.get("semantic"))
                    self.assert_code(case["code"], validator, payload)

    def test_claim_cardinality_dangling_and_bilingual_semantics(self) -> None:
        first, second = self.fact(), self.fact(path="pyproject.toml", source=b"[project]\n")
        graph = EvidenceGraph([first, second]).to_dict()
        first_id, second_id = [item["fact_id"] for item in graph["facts"]]
        claim_map = self.claim_map(first_id)
        validate_claim_map(claim_map, evidence_graph=graph)
        zero = copy.deepcopy(claim_map)
        zero["markdown_blocks"][0]["evidence_ids"] = []
        self.assert_code("E_CLAIM_EVIDENCE", validate_claim_map, zero, evidence_graph=graph)
        composed = copy.deepcopy(claim_map)
        composed["markdown_blocks"][0]["support_level"] = "composed"
        self.assert_code("E_CLAIM_EVIDENCE", validate_claim_map, composed, evidence_graph=graph)
        dangling = copy.deepcopy(claim_map)
        dangling["markdown_blocks"][0]["evidence_ids"] = ["config:" + "f" * 64]
        self.assert_code("E_CLAIM_EVIDENCE", validate_claim_map, dangling, evidence_graph=graph)
        bilingual = copy.deepcopy(claim_map)
        english = bilingual["markdown_blocks"][0]
        english["language_pair_id"] = "overview"
        chinese = copy.deepcopy(english)
        chinese["claim_id"] = "markdown:zh-Hans:overview"
        chinese["content_sha256"] = hashlib.sha256("概览".encode()).hexdigest()
        bilingual["markdown_blocks"].append(chinese)
        validate_claim_map(bilingual, evidence_graph=graph)
        chinese["evidence_ids"] = [second_id]
        self.assert_code("E_CLAIM_LANGUAGE", validate_claim_map, bilingual, evidence_graph=graph)

    def test_v1_claim_adapter_is_read_only_and_v2_only(self) -> None:
        legacy = {
            "schema_version": 1,
            "markdown_blocks": [{
                "claim_id": "markdown:en:overview", "content_sha256": "a" * 64,
                "claim_kind": "factual", "truth_id": "file:README.md",
                "evidence_sha256": "b" * 64, "language_pair_id": None,
            }],
            "diagram_labels": [],
        }
        before = canonical_json_bytes(legacy)
        adapted = adapt_v1_claim_map(legacy)
        self.assertEqual(canonical_json_bytes(legacy), before)
        self.assertEqual(adapted["schema_version"], 2)
        self.assertNotIn("truth_id", adapted["markdown_blocks"][0])
        self.assertNotIn("evidence_sha256", adapted["markdown_blocks"][0])

    def make_bundle(self, root: Path, *, bilingual: bool = False) -> dict[str, Any]:
        source = b"source evidence\n"
        (root / "source").mkdir()
        (root / "source" / "README.md").write_bytes(source)
        fact = self.fact(source=source)
        facts = [fact]
        if bilingual:
            second_source = b"[project]\n"
            (root / "source" / "pyproject.toml").write_bytes(second_source)
            facts.append(self.fact(path="source/pyproject.toml", source=second_source))
        graph = EvidenceGraph(facts).to_dict()
        fact_id = fact["fact_id"]
        asset_raw = b"asset bytes\n"
        (root / "assets").mkdir()
        (root / "assets" / "hero.png").write_bytes(asset_raw)
        readme_raw = b"# Overview\n"
        (root / "README.generated.md").write_bytes(readme_raw)
        if bilingual:
            (root / "README_zh.generated.md").write_bytes("# 概览\n".encode())
        plan = {
            "schema_version": 2,
            "mode": "readme",
            "locales": (
                [{"tag": "en", "readme_path": "README.generated.md"}, {"tag": "zh-Hans", "readme_path": "README_zh.generated.md"}]
                if bilingual else [{"tag": "en", "readme_path": "README.generated.md"}]
            ),
            "sections": ["overview"], "visual_intent": "hero", "diagram_route": "static",
            "commands": [], "evidence_ids": [item["fact_id"] for item in graph["facts"]],
        }
        claims = self.claim_map(fact_id)
        if bilingual:
            english = claims["markdown_blocks"][0]
            english["language_pair_id"] = "overview"
            chinese = copy.deepcopy(english)
            chinese["claim_id"] = "markdown:zh-Hans:overview"
            chinese["content_sha256"] = hashlib.sha256("# 概览".encode()).hexdigest()
            claims["markdown_blocks"].append(chinese)
        assets = {
            "schema_version": 2,
            "assets": [{
                "asset_id": "hero", "path": "assets/hero.png", "locale": "en", "language_neutral": False,
                "provenance": {"kind": "derived", "path": "source/README.md", "sha256": hashlib.sha256(source).hexdigest()},
                "artifact_sha256": hashlib.sha256(asset_raw).hexdigest(),
                "candidate_sha256": hashlib.sha256(asset_raw).hexdigest(),
                "evidence_ids": [fact_id],
            }],
        }
        retrieval = {"schema_version": 1, "status": "unavailable", "records": []}
        candidate = {
            "readme": {"path": "README.generated.md", "sha256": hashlib.sha256(readme_raw).hexdigest()},
            "assets": [{"path": "assets/hero.png", "sha256": hashlib.sha256(asset_raw).hexdigest()}],
        }
        evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": canonical_sha256(candidate)}
        values = {"plan": plan, "retrieval": retrieval, "evidence": graph, "claim_map": claims, "asset_manifest": assets, "evaluation": evaluation}
        paths = {"plan": "readme-plan.json", "retrieval": "retrieval-packet.json", "evidence": "repository-evidence.json", "claim_map": "claim-map.json", "asset_manifest": "asset-manifest.json", "evaluation": "evaluation.json"}
        artifacts: dict[str, object] = {}
        for name, value in values.items():
            write_canonical_json_atomic(root / paths[name], value)
            artifacts[name] = {"path": paths[name], "sha256": canonical_sha256(value)}
        return assemble_generated_bundle(root, mode="readme", target={"repository": "owner/repo", "base_sha": "a" * 40}, candidate=candidate, artifacts=artifacts)

    def rewrite_candidate(self, root: Path, bundle: dict[str, Any], raw: bytes) -> None:
        (root / "README.generated.md").write_bytes(raw)
        bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(raw).hexdigest()
        candidate = {key: bundle["candidate"][key] for key in ("readme", "assets")}
        bundle["candidate"]["candidate_sha256"] = canonical_sha256(candidate)
        evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": bundle["candidate"]["candidate_sha256"]}
        write_canonical_json_atomic(root / "evaluation.json", evaluation)
        bundle["artifacts"]["evaluation"]["sha256"] = canonical_sha256(evaluation)

    def make_two_block_bundle(self, root: Path) -> dict[str, Any]:
        bundle = self.make_bundle(root)
        self.rewrite_candidate(root, bundle, b"# Overview\n\nDetails\n")
        claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
        claims["markdown_blocks"].append({
            **copy.deepcopy(claims["markdown_blocks"][0]),
            "claim_id": "markdown:en:details",
            "content_sha256": "1" * 64,
        })
        claims["markdown_blocks"].sort(key=lambda claim: claim["claim_id"])
        write_canonical_json_atomic(root / "claim-map.json", claims)
        bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
        return assemble_generated_bundle(
            root,
            mode=bundle["mode"],
            target=bundle["target"],
            candidate={key: bundle["candidate"][key] for key in ("readme", "assets")},
            artifacts=bundle["artifacts"],
        )

    def test_bundle_exact_hashes_security_atomicity_and_concurrency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            fixture = json.loads((FIXTURES / "generated-bundle-v2.valid.json").read_text(encoding="utf-8"))
            self.assertEqual(bundle, fixture)
            self.assertEqual(list(self.schema("generated-bundle.v2.schema.json").iter_errors(fixture)), [])
            invalid = json.loads((FIXTURES / "generated-bundle-v2.invalid.json").read_text(encoding="utf-8"))
            for case in invalid["cases"]:
                self.assertTrue(list(self.schema("generated-bundle.v2.schema.json").iter_errors(case["payload"])))
                self.assert_code(case["code"], validate_generated_bundle_v2, case["payload"], root)
            self.assertEqual(bundle["schema_version"], 2)
            self.assertNotIn("truth_id", canonical_json_bytes(bundle).decode())
            self.assertEqual(validate_generated_bundle_v2(bundle, root)["status"], "pass")
            mutated = copy.deepcopy(bundle)
            mutated["candidate"]["assets"][0]["sha256"] = "0" * 64
            self.assert_code("E_BUNDLE_HASH", validate_generated_bundle_v2, mutated, root)
            traversal = copy.deepcopy(bundle)
            traversal["candidate"]["assets"][0]["path"] = "../secret"
            self.assert_code("E_PATH", validate_generated_bundle_v2, traversal, root)
            destination = root / "generated-readme-bundle.json"
            write_generated_bundle_atomic(destination, bundle, artifact_root=root)
            self.assertEqual(destination.read_bytes(), canonical_json_bytes(bundle))
            outputs: list[bytes] = []
            threads = [threading.Thread(target=lambda: outputs.append(canonical_json_bytes(validate_generated_bundle_v2(bundle, root)))) for _ in range(8)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
            self.assertEqual(len(set(outputs)), 1)

    def test_readme_claim_content_binding_rejects_self_consistent_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_two_block_bundle(root)
            self.rewrite_candidate(root, bundle, b"# Unrelated\n")
            self.assert_code("E_BUNDLE_HASH", validate_generated_bundle_v2, bundle, root)

    def test_readme_claim_content_binding_preserves_pure_coverage_errors(self) -> None:
        with self.subTest(case="missing block with matching prefix"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_two_block_bundle(root)
                self.rewrite_candidate(root, bundle, b"# Overview\n")
                self.assert_code("E_CLAIM_COVERAGE", validate_generated_bundle_v2, bundle, root)
        with self.subTest(case="extra block with matching prefix"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_bundle(root)
                self.rewrite_candidate(root, bundle, b"# Overview\n\nDetails\n")
                self.assert_code("E_CLAIM_COVERAGE", validate_generated_bundle_v2, bundle, root)

    def test_readme_claim_content_binding_is_canonical_bilingual_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = self.make_bundle(root, bilingual=True)
            emitted = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
            self.assertNotEqual(emitted["markdown_blocks"][0]["content_sha256"], "0" * 64)
            second = assemble_generated_bundle(
                root,
                mode=first["mode"],
                target=first["target"],
                candidate={key: first["candidate"][key] for key in ("readme", "assets")},
                artifacts=first["artifacts"],
            )
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))["markdown_blocks"]
            expected = []
            for path in (root / "README.generated.md", root / "README_zh.generated.md"):
                expected.extend(hashlib.sha256(block).hexdigest() for block in canonical_markdown_blocks(path.read_bytes()))
            self.assertEqual([claim["content_sha256"] for claim in claims], expected)
            self.assertEqual(validate_generated_bundle_v2(first, root)["status"], "pass")

    def test_readme_claim_derivation_preserves_diagram_label_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
            diagram_hash = "f" * 64
            claims["diagram_labels"].append({
                **copy.deepcopy(claims["markdown_blocks"][0]),
                "claim_id": "diagram:en:hero",
                "content_sha256": diagram_hash,
            })
            write_canonical_json_atomic(root / "claim-map.json", claims)
            bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
            assembled = assemble_generated_bundle(
                root,
                mode=bundle["mode"],
                target=bundle["target"],
                candidate={key: bundle["candidate"][key] for key in ("readme", "assets")},
                artifacts=bundle["artifacts"],
            )
            emitted = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
            self.assertEqual(emitted["diagram_labels"][0]["content_sha256"], diagram_hash)
            self.assertEqual(validate_generated_bundle_v2(assembled, root)["status"], "pass")

    def test_readme_claim_content_binding_rejects_orphan_duplicate_missing_and_reordered(self) -> None:
        def rewrite_json(root: Path, bundle: dict[str, Any], name: str, value: dict[str, Any]) -> None:
            write_canonical_json_atomic(root / bundle["artifacts"][name]["path"], value)
            bundle["artifacts"][name]["sha256"] = canonical_sha256(value)

        with self.subTest(case="orphan claim"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_bundle(root)
                claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
                claims["markdown_blocks"] = []
                rewrite_json(root, bundle, "claim_map", claims)
                self.assert_code("E_CLAIM_COVERAGE", validate_generated_bundle_v2, bundle, root)
        with self.subTest(case="ambiguous duplicate blocks"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_bundle(root)
                replacement = b"# Overview\n\n# Overview\n"
                (root / "README.generated.md").write_bytes(replacement)
                bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(replacement).hexdigest()
                candidate = {key: bundle["candidate"][key] for key in ("readme", "assets")}
                bundle["candidate"]["candidate_sha256"] = canonical_sha256(candidate)
                evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": bundle["candidate"]["candidate_sha256"]}
                rewrite_json(root, bundle, "evaluation", evaluation)
                self.assert_code("E_BUNDLE_CLAIM", validate_generated_bundle_v2, bundle, root)
        with self.subTest(case="missing locale companion"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_bundle(root, bilingual=True)
                (root / "README_zh.generated.md").unlink()
                self.assert_code("E_CLAIM_COVERAGE", validate_generated_bundle_v2, bundle, root)
        with self.subTest(case="reordered blocks"):
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                bundle = self.make_bundle(root)
                original = b"# Overview\n\nDetails\n"
                (root / "README.generated.md").write_bytes(original)
                bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(original).hexdigest()
                candidate = {key: bundle["candidate"][key] for key in ("readme", "assets")}
                bundle["candidate"]["candidate_sha256"] = canonical_sha256(candidate)
                evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": bundle["candidate"]["candidate_sha256"]}
                rewrite_json(root, bundle, "evaluation", evaluation)
                claims = json.loads((root / "claim-map.json").read_text(encoding="utf-8"))
                claims["markdown_blocks"].append({
                    **copy.deepcopy(claims["markdown_blocks"][0]),
                    "claim_id": "markdown:en:details",
                    "content_sha256": hashlib.sha256(b"Details").hexdigest(),
                })
                claims["markdown_blocks"].sort(key=lambda claim: claim["claim_id"])
                rewrite_json(root, bundle, "claim_map", claims)
                bundle = assemble_generated_bundle(
                    root,
                    mode=bundle["mode"],
                    target=bundle["target"],
                    candidate=candidate,
                    artifacts=bundle["artifacts"],
                )
                self.assertEqual(validate_generated_bundle_v2(bundle, root)["status"], "pass")
                replacement = b"Details\n\n# Overview\n"
                (root / "README.generated.md").write_bytes(replacement)
                bundle["candidate"]["readme"]["sha256"] = hashlib.sha256(replacement).hexdigest()
                candidate = {key: bundle["candidate"][key] for key in ("readme", "assets")}
                bundle["candidate"]["candidate_sha256"] = canonical_sha256(candidate)
                evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": bundle["candidate"]["candidate_sha256"]}
                rewrite_json(root, bundle, "evaluation", evaluation)
                self.assert_code("E_BUNDLE_HASH", validate_generated_bundle_v2, bundle, root)

    def test_symlink_and_special_asset_fail_before_success(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root)
            asset = root / "assets" / "hero.png"
            raw = asset.read_bytes()
            asset.unlink()
            asset.symlink_to(root / "source" / "README.md")
            self.assert_code("E_PATH", validate_generated_bundle_v2, bundle, root)
            asset.unlink()
            os.mkfifo(asset)
            self.assertTrue(stat.S_ISFIFO(asset.lstat().st_mode))
            self.assert_code("E_PATH", validate_generated_bundle_v2, bundle, root)
            asset.unlink()
            asset.write_bytes(raw)
            source = root / "source" / "README.md"
            source_raw = source.read_bytes()
            source.unlink()
            (root / "real-source.md").write_bytes(source_raw)
            source.symlink_to(root / "real-source.md")
            self.assert_code("E_PATH", validate_generated_bundle_v2, bundle, root)

    def test_public_cli_accepts_v2_and_rejects_language_drift(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle = self.make_bundle(root, bilingual=True)
            bundle_path = root / "generated-readme-bundle.json"
            write_canonical_json_atomic(bundle_path, bundle)
            command = [sys.executable, "skill/scripts/readme_pipeline.py", "validate-bundle", "--bundle", str(bundle_path)]
            result = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "pass")
            claim_path = root / "claim-map.json"
            claims = json.loads(claim_path.read_text(encoding="utf-8"))
            evidence = json.loads((root / "repository-evidence.json").read_text(encoding="utf-8"))
            original_id = claims["markdown_blocks"][0]["evidence_ids"][0]
            claims["markdown_blocks"][1]["evidence_ids"] = [next(item["fact_id"] for item in evidence["facts"] if item["fact_id"] != original_id)]
            write_canonical_json_atomic(claim_path, claims)
            bundle["artifacts"]["claim_map"]["sha256"] = canonical_sha256(claims)
            write_canonical_json_atomic(bundle_path, bundle)
            failed = subprocess.run(command, cwd=ROOT, text=True, capture_output=True, check=False)
            self.assertEqual(failed.returncode, 2)
            self.assertIn("E_CLAIM_LANGUAGE", failed.stderr)


if __name__ == "__main__":
    unittest.main()
