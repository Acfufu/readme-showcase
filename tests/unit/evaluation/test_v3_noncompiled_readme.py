"""Regression tests for the v3 non-compiled readme pipeline path.

Covers the fixes that made Plan v3 (animated route) + bilingual readme mode
run end-to-end through the ordinary pipeline:

- plan.py: validate_readme_plan_v2 accepts v2 and v3 plans
- stages.py candidate_files: v3 readme candidates use plan locales (was a
  KeyError on the legacy plan["languages"] branch)
- audit_readme.py: SMIL animation tags are GitHub-safe per the documented
  animated route and must not be flagged unsafe
- metrics.py evaluate_v2_advisory: accepts v3 plans
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.contracts.plan import validate_readme_plan_v2
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.evaluation.metrics import evaluate_v2_advisory
from skill.scripts.readme_showcase.orchestration.stages import candidate_files

ROOT = Path(__file__).resolve().parents[3]


def v3_animated_plan(evidence_id: str) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "mode": "readme",
        "locales": [
            {"tag": "en", "readme_path": "README_en.md"},
            {"tag": "zh-Hans", "readme_path": "README.md"},
        ],
        "sections": ["overview", "quick-start"],
        "visual_intent": "animated workflow",
        "diagram_route": "animated",
        "static_frame": True,
        "commands": [],
        "evidence_ids": [evidence_id],
    }


class PlanV2ProducerAcceptsV3(unittest.TestCase):
    def test_validate_readme_plan_v2_accepts_v3_animated(self) -> None:
        fact = build_fact(kind="file-presence", path="README.md", locator=None, semantic_key="presence", value=True, source_bytes=b"# zh\n")
        plan = v3_animated_plan(fact["fact_id"])
        self.assertEqual(validate_readme_plan_v2(plan)["diagram_route"], "animated")

    def test_validate_readme_plan_v2_still_rejects_v1(self) -> None:
        plan = {
            "schema_version": 1,
            "mode": "readme",
            "languages": ["en"],
            "sections": ["overview"],
            "visual_intent": "hero",
            "diagram_route": "static",
            "commands": [],
            "evidence_ids": [],
        }
        with self.assertRaises(ContractError) as raised:
            validate_readme_plan_v2(plan)
        self.assertEqual(raised.exception.code, "E_SCHEMA_VERSION")


class CandidateFilesV3ReadmePaths(unittest.TestCase):
    """candidate_files must use plan locales for v3 readme candidates.

    Regression: the v3 branch previously fell through to the legacy
    ``plan["languages"]`` lookup and raised KeyError.
    """

    def _context(self, root: Path, plan: dict[str, Any]) -> Any:
        stage_plan = root / "stages/03-plan-import/attempts/1"
        stage_plan.mkdir(parents=True)
        (stage_plan / "readme-plan.json").write_bytes(canonical_json_bytes(plan))
        candidate = root / "stages/05-candidate"
        candidate.mkdir(parents=True)
        (candidate / "claim-map.json").write_bytes(canonical_json_bytes(
            {"schema_version": 2, "markdown_blocks": [], "diagram_labels": []}))
        (candidate / "asset-manifest.json").write_bytes(canonical_json_bytes(
            {"schema_version": 2, "assets": []}))
        (candidate / "README.md").write_text("# zh\n", encoding="utf-8")
        (candidate / "README_en.md").write_text("# en\n", encoding="utf-8")
        (candidate / "README_zh.md").write_text("# zh\n", encoding="utf-8")

        workspace_root = root

        class Workspace:
            root = workspace_root

        class Context:
            workspace = Workspace()
            manifest = {"configuration": {"mode": "readme"}}

            def attempt_file(self, index: int, name: str) -> Path:
                names = ["scan", "retrieve", "plan-import"]
                return workspace_root / "stages" / f"{index + 1:02d}-{names[index]}" / "attempts" / "1" / name

        return Context()

    def test_v3_readme_candidate_requires_locale_readme_paths(self) -> None:
        fact = build_fact(kind="file-presence", path="README.md", locator=None, semantic_key="presence", value=True, source_bytes=b"# zh\n")
        with tempfile.TemporaryDirectory() as td:
            context = self._context(Path(td), v3_animated_plan(fact["fact_id"]))
            files = candidate_files(context)
            self.assertIsNotNone(files)
            names = [name for name, _ in files or []]
            self.assertIn("README.md", names)
            self.assertIn("README_en.md", names)

    def test_v1_readme_candidate_still_uses_legacy_pair(self) -> None:
        plan = {
            "schema_version": 1,
            "mode": "readme",
            "languages": ["en", "zh"],
            "sections": ["overview"],
            "visual_intent": "hero",
            "diagram_route": "static",
            "commands": [],
            "evidence_ids": [],
        }
        with tempfile.TemporaryDirectory() as td:
            context = self._context(Path(td), plan)
            files = candidate_files(context)
            self.assertIsNotNone(files)
            names = [name for name, _ in files or []]
            self.assertIn("README.md", names)
            self.assertIn("README_zh.md", names)


class AuditAllowsSmilAnimation(unittest.TestCase):
    def test_animated_svg_is_not_flagged_unsafe(self) -> None:
        from skill.scripts.audit_readme import audit_readme

        svg = """<svg xmlns="http://www.w3.org/2000/svg" width="400" height="300" viewBox="0 0 400 300" role="img" aria-labelledby="title desc">
  <title id="title">Flow</title><desc id="desc">Animated flow</desc>
  <rect width="400" height="300" fill="#081B30"/>
  <g id="step">
    <animate attributeName="opacity" values="0;0;1" keyTimes="0;0.1;0.3" dur="2s" fill="freeze"/>
    <animateTransform attributeName="transform" type="translate" values="0,10;0,10;0,0" keyTimes="0;0.1;0.3" dur="2s" fill="freeze"/>
    <rect x="20" y="20" width="100" height="60" rx="8" fill="#1B3A5F"/>
    <text x="70" y="60" font-family="sans-serif" font-size="20" fill="#F0F6FD" text-anchor="middle">Step</text>
  </g>
</svg>
"""
        readme = "![animated flow](animated.svg)\n"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "animated.svg").write_text(svg, encoding="utf-8")
            (root / "README.md").write_text(readme, encoding="utf-8")
            issues, _, _ = audit_readme(root / "README.md", root=root)
            self.assertEqual(issues, [])

    def test_script_is_still_unsafe(self) -> None:
        from skill.scripts.audit_readme import audit_readme

        svg = """<svg xmlns="http://www.w3.org/2000/svg" width="100" height="100" viewBox="0 0 100 100" role="img" aria-labelledby="title desc">
  <title id="title">X</title><desc id="desc">X</desc><script>alert(1)</script>
</svg>
"""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "bad.svg").write_text(svg, encoding="utf-8")
            (root / "README.md").write_text("![bad](bad.svg)\n", encoding="utf-8")
            issues, _, _ = audit_readme(root / "README.md", root=root)
            self.assertTrue(any("E_SVG_UNSAFE" in str(item) for item in issues))


class EvaluateV2AdvisoryAcceptsV3Plan(unittest.TestCase):
    def test_v3_animated_plan_bundle_evaluates(self) -> None:
        fact = build_fact(kind="file-presence", path="README.md", locator=None, semantic_key="presence", value=True, source_bytes=b"# zh\n")
        graph = EvidenceGraph([fact]).to_dict()
        fact_id = fact["fact_id"]
        plan = v3_animated_plan(fact_id)
        claim = {
            "schema_version": 2,
            "markdown_blocks": [
                {
                    "claim_id": "markdown:en:overview",
                    "content_sha256": "0" * 64,
                    "claim_kind": "factual",
                    "evidence_ids": [fact_id],
                    "language_pair_id": None,
                    "support_level": "direct",
                },
                {
                    "claim_id": "markdown:zh-Hans:overview",
                    "content_sha256": "1" * 64,
                    "claim_kind": "factual",
                    "evidence_ids": [fact_id],
                    "language_pair_id": None,
                    "support_level": "direct",
                },
            ],
            "diagram_labels": [],
        }
        retrieval = {"schema_version": 1, "status": "available", "records": []}
        manifest = {"schema_version": 2, "assets": []}
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "readme-plan.json").write_bytes(canonical_json_bytes(plan))
            (root / "repository-evidence.json").write_bytes(canonical_json_bytes(graph))
            (root / "retrieval-packet.json").write_bytes(canonical_json_bytes(retrieval))
            (root / "claim-map.json").write_bytes(canonical_json_bytes(claim))
            (root / "asset-manifest.json").write_bytes(canonical_json_bytes(manifest))
            (root / "README.md").write_text("# zh\n", encoding="utf-8")
            (root / "README_en.md").write_text("# en\n", encoding="utf-8")
            bundle = {
                "schema_version": 2,
                "mode": "readme",
                "target": {"repository": "acfufu/example", "base_sha": "0" * 40},
                "candidate": {
                    "readme": {"path": "README_en.md", "sha256": hashlib.sha256(b"# en\n").hexdigest()},
                    "candidate_sha256": "0" * 64,
                    "assets": [],
                },
                "artifacts": {
                    "plan": {"path": "readme-plan.json", "sha256": hashlib.sha256(canonical_json_bytes(plan)).hexdigest()},
                    "retrieval": {"path": "retrieval-packet.json", "sha256": hashlib.sha256(canonical_json_bytes(retrieval)).hexdigest()},
                    "evidence": {"path": "repository-evidence.json", "sha256": hashlib.sha256(canonical_json_bytes(graph)).hexdigest()},
                    "claim_map": {"path": "claim-map.json", "sha256": hashlib.sha256(canonical_json_bytes(claim)).hexdigest()},
                    "asset_manifest": {"path": "asset-manifest.json", "sha256": hashlib.sha256(canonical_json_bytes(manifest)).hexdigest()},
                },
            }
            advisory = evaluate_v2_advisory(bundle, root)
            self.assertEqual(advisory["claim_coverage"]["status"], "measured")


if __name__ == "__main__":
    unittest.main()
