from __future__ import annotations

import copy
import importlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Callable, cast


_CONTRACTS = importlib.import_module("skill.scripts.pipeline_contracts")
_CORE = importlib.import_module("skill.scripts.pipeline_core")
ContractError = _CONTRACTS.ContractError
canonical_json_bytes = _CONTRACTS.canonical_json_bytes
scan_repository = _CORE.scan_repository
retrieve_patterns: Callable[..., dict[str, object]] | None = getattr(
    _CORE,
    "retrieve_patterns",
    None,
)
REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = REPO_ROOT / "dataset/retrieval/manifest.json"


class RetrievalTests(unittest.TestCase):
    def manifest(self) -> dict[str, Any]:
        return json.loads(MANIFEST.read_text(encoding="utf-8"))

    def evidence(self, root: Path) -> dict[str, object]:
        (root / "README.md").write_text("# Fixture\n", encoding="utf-8")
        return scan_repository(root)

    def retrieve(
        self,
        evidence: dict[str, object],
        manifest: dict[str, Any] | None,
        *,
        mode: str = "production",
        sections: list[str] | None = None,
        tags: list[str] | None = None,
    ) -> dict[str, object]:
        self.assertIsNotNone(retrieve_patterns)
        assert retrieve_patterns is not None
        return retrieve_patterns(
            evidence,
            manifest,
            project_type="web-framework",
            sections=sections or ["quick-start", "overview"],
            tags=tags or ["observable-output", "api"],
            mode=mode,
        )

    def test_fixed_score_top_five_and_order_normalization(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self.evidence(Path(temporary))
            manifest = self.manifest()
            packet = self.retrieve(evidence, manifest)
            shuffled = copy.deepcopy(manifest)
            shuffled["records"].reverse()
            reordered = self.retrieve(
                evidence,
                shuffled,
                sections=["overview", "quick-start"],
                tags=["api", "observable-output"],
            )

        self.assertEqual(canonical_json_bytes(packet), canonical_json_bytes(reordered))
        records = cast(list[dict[str, Any]], packet["records"])
        self.assertLessEqual(len(records), 5)
        self.assertTrue(all(record["score"] > 0 for record in records))
        self.assertEqual(records[0]["record_id"], "fastapi-proof-first-overview")
        self.assertEqual(records[0]["score"], 180)
        self.assertEqual(
            records[0]["components"],
            {
                "project_type_match": 1,
                "section_overlap_count": 2,
                "tag_overlap_count": 2,
            },
        )

    def test_test_split_is_unreachable_and_missing_mode_behavior_is_exact(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            evidence = self.evidence(Path(temporary))
            manifest = self.manifest()
            test_record = next(record for record in manifest["records"] if record["split"] == "test")
            test_record["pattern"]["summary"] = "GOLD-SENTINEL"
            packet = self.retrieve(evidence, manifest)
            self.assertNotIn("GOLD-SENTINEL", canonical_json_bytes(packet).decode("utf-8"))

            unavailable = self.retrieve(evidence, None)
            self.assertEqual(unavailable["status"], "unavailable")
            self.assertEqual(unavailable["records"], [])
            with self.assertRaises(ContractError) as raised:
                self.retrieve(evidence, None, mode="benchmark")
            self.assertEqual(raised.exception.code, "E_RETRIEVAL_MANIFEST")

    def test_cli_writes_canonical_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence_path = root / "evidence.json"
            evidence_path.write_bytes(canonical_json_bytes(self.evidence(root)))
            output = root / "retrieval.json"
            result = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "retrieve",
                    "--evidence",
                    str(evidence_path),
                    "--manifest",
                    str(MANIFEST),
                    "--project-type",
                    "web-framework",
                    "--section",
                    "overview",
                    "--tag",
                    "api",
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            response = json.loads(result.stdout)
            packet = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(response["status"], "available")
            self.assertEqual(response["retrieval_sha256"], _CONTRACTS.canonical_sha256(packet))


if __name__ == "__main__":
    unittest.main()
