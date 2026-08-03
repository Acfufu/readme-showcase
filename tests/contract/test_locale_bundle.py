from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256, write_canonical_json_atomic
from skill.scripts.readme_showcase.contracts.assets import validate_asset_manifest
from skill.scripts.readme_showcase.contracts.claims import validate_claim_map
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.contracts.plan import validate_readme_plan
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.generation.assembler import assemble_generated_bundle, validate_generated_bundle_v2


ROOT = Path(__file__).resolve().parents[2]


class LocaleBundleContractTests(unittest.TestCase):
    def assert_code(self, code: str, function: Any, *args: Any, **kwargs: Any) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*args, **kwargs)
        self.assertEqual(raised.exception.code, code)

    def write_json(self, root: Path, path: str, payload: dict[str, Any]) -> dict[str, str]:
        write_canonical_json_atomic(root / path, payload)
        return {"path": path, "sha256": canonical_sha256(payload)}

    def make_trilingual_bundle(self, root: Path) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        source = b"locale source evidence\n"
        (root / "source").mkdir()
        (root / "source" / "README.md").write_bytes(source)
        facts = EvidenceGraph([
            build_fact(kind="file-presence", path="source/README.md", locator=None, semantic_key="source", value=True, source_bytes=source),
            build_fact(kind="file-presence", path="source/README.md", locator=None, semantic_key="locale", value="explicit", source_bytes=source),
        ]).to_dict()
        evidence_ids = [fact["fact_id"] for fact in facts["facts"]]

        readmes = {
            "docs/overview.md": b"# English overview\n",
            "localized/not-chinese.md": "# 中文概览\n".encode(),
            "release/not-japanese.md": "# 日本語の概要\n".encode(),
        }
        for path, raw in readmes.items():
            destination = root / path
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_bytes(raw)
        plan = {
            "schema_version": 2,
            "mode": "readme",
            "locales": [
                {"tag": "en", "readme_path": "docs/overview.md"},
                {"tag": "zh-Hans", "readme_path": "localized/not-chinese.md"},
                {"tag": "ja", "readme_path": "release/not-japanese.md"},
            ],
            "sections": ["overview"],
            "visual_intent": "hero",
            "diagram_route": "static",
            "commands": [],
            "evidence_ids": evidence_ids,
        }
        claims = {
            "schema_version": 2,
            "markdown_blocks": [
                {
                    "claim_id": f"markdown:{tag}:landing",
                    "content_sha256": hashlib.sha256(readmes[path]).hexdigest(),
                    "claim_kind": "factual",
                    "evidence_ids": evidence_ids,
                    "language_pair_id": "landing-copy",
                    "support_level": "composed",
                }
                for tag, path in (("en", "docs/overview.md"), ("ja", "release/not-japanese.md"), ("zh-Hans", "localized/not-chinese.md"))
            ],
            "diagram_labels": [],
        }
        (root / "assets").mkdir()
        localized = b"localized bytes\n"
        neutral = b"neutral bytes\n"
        (root / "assets" / "actually-en.png").write_bytes(localized)
        (root / "assets" / "common-zh.png").write_bytes(neutral)
        source_hash = hashlib.sha256(source).hexdigest()
        assets = {
            "schema_version": 2,
            "assets": [
                {
                    "asset_id": "localized",
                    "path": "assets/actually-en.png",
                    "locale": "en",
                    "language_neutral": False,
                    "provenance": {"kind": "derived", "path": "source/README.md", "sha256": source_hash},
                    "artifact_sha256": hashlib.sha256(localized).hexdigest(),
                    "candidate_sha256": hashlib.sha256(localized).hexdigest(),
                    "evidence_ids": evidence_ids,
                },
                {
                    "asset_id": "neutral",
                    "path": "assets/common-zh.png",
                    "language_neutral": True,
                    "provenance": {"kind": "derived", "path": "source/README.md", "sha256": source_hash},
                    "artifact_sha256": hashlib.sha256(neutral).hexdigest(),
                    "candidate_sha256": hashlib.sha256(neutral).hexdigest(),
                    "evidence_ids": evidence_ids,
                },
            ],
        }
        candidate = {
            "readme": {"path": "docs/overview.md", "sha256": hashlib.sha256(readmes["docs/overview.md"]).hexdigest()},
            "assets": [
                {"path": "assets/actually-en.png", "sha256": hashlib.sha256(localized).hexdigest()},
                {"path": "assets/common-zh.png", "sha256": hashlib.sha256(neutral).hexdigest()},
            ],
        }
        evaluation = {"schema_version": 2, "status": "pass", "candidate_sha256": canonical_sha256(candidate)}
        artifacts = {
            "plan": self.write_json(root, "readme-plan.json", plan),
            "retrieval": self.write_json(root, "retrieval-packet.json", {"schema_version": 1, "status": "unavailable", "records": []}),
            "evidence": self.write_json(root, "repository-evidence.json", facts),
            "claim_map": self.write_json(root, "claim-map.json", claims),
            "asset_manifest": self.write_json(root, "asset-manifest.json", assets),
            "evaluation": self.write_json(root, "evaluation.json", evaluation),
        }
        bundle = assemble_generated_bundle(
            root,
            mode="readme",
            target={"repository": "owner/repo", "base_sha": "a" * 40},
            candidate=candidate,
            artifacts=artifacts,
        )
        return bundle, assets, claims

    def test_explicit_trilingual_bundle_is_canonical_and_filename_independent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, assets, claims = self.make_trilingual_bundle(root)
            self.assertEqual(validate_generated_bundle_v2(first, root)["status"], "pass")
            self.assertEqual(validate_asset_manifest(assets, evidence_graph=json.loads((root / "repository-evidence.json").read_text(encoding="utf-8")))["assets"][1]["language_neutral"], True)
            self.assertEqual(validate_claim_map(claims, evidence_graph=json.loads((root / "repository-evidence.json").read_text(encoding="utf-8")))["markdown_blocks"][2]["language_pair_id"], "landing-copy")
            second = assemble_generated_bundle(
                root,
                mode=first["mode"],
                target=first["target"],
                candidate={key: first["candidate"][key] for key in ("readme", "assets")},
                artifacts=first["artifacts"],
            )
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))

    def test_locale_asset_and_pair_regressions_fail_without_filename_inference(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            _, assets, claims = self.make_trilingual_bundle(root)
            graph = json.loads((root / "repository-evidence.json").read_text(encoding="utf-8"))
            conflict = copy.deepcopy(assets)
            conflict["assets"][1]["locale"] = "zh-Hans"
            self.assert_code("E_ASSET_LOCALE", validate_asset_manifest, conflict, evidence_graph=graph)
            changed_order = copy.deepcopy(claims)
            changed_order["markdown_blocks"][2]["evidence_ids"] = list(reversed(changed_order["markdown_blocks"][2]["evidence_ids"]))
            self.assert_code("E_CLAIM_LANGUAGE", validate_claim_map, changed_order, evidence_graph=graph)

    def test_v1_bilingual_plan_and_readmes_remain_readable_without_rewrite(self) -> None:
        candidate = ROOT / "tests" / "fixtures" / "run-workspaces" / "v1-candidate"
        before = {name: (candidate / name).read_bytes() for name in ("README.md", "README_zh.md")}
        plan = json.loads((ROOT / "tests" / "fixtures" / "run-workspaces" / "v1-plan.json").read_text(encoding="utf-8"))
        self.assertEqual(validate_readme_plan(plan), plan)
        self.assertEqual({name: (candidate / name).read_bytes() for name in before}, before)
        self.assertTrue(before["README.md"].decode("utf-8").strip())
        self.assertTrue(before["README_zh.md"].decode("utf-8").strip())


if __name__ == "__main__":
    unittest.main()
