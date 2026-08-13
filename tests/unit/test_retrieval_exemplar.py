import json
import tempfile
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.contracts.retrieval import (
    validate_exemplar_record_v1,
    validate_retrieval_packet_v2,
)
from skill.scripts.readme_showcase.retrieval.exemplars import load_exemplar_records
from skill.scripts.readme_showcase.retrieval.ranker import rank_records

# rank_records output copies record["source"] and record["split"]
# (ranker.py:132-133), so every test record MUST carry both keys;
# mirror the real manifest record shape (8-field source, split).
def _source(repo: str, seed: str) -> dict:
    return {
        "repository_url": f"https://github.com/example/{repo}",
        "commit": seed * 40,
        "material_sha256": f"{seed}{'0' * 63}",
        "license_spdx": "MIT",
        "license_evidence_spdx": "MIT",
        "license_evidence_url": f"https://github.com/example/{repo}/blob/{seed * 40}/LICENSE",
        "license_evidence_sha256": f"{seed}{'1' * 63}",
        "human_reviewed": True,
    }

TEXT_RECORD = {
    "record_id": "text-01",
    "project_types": ["library"],
    "section_intents": ["overview"],
    "tags": ["python"],
    "pattern": {"summary": "Concise class declarations.", "structure": "Purpose, install, declare.",
                "proof": "Deterministic object representations."},
    "source": _source("text-repo", "a"),
    "split": "train",
}

EXEMPLAR_RECORD = {
    "record_id": "exemplar-curated-01",
    "project_types": ["developer-tool"],
    "section_intents": ["hero"],
    "tags": ["cli", "terminal"],
    "pattern": {"summary": "Terminal motif hero.", "structure": "Identity left, proof right.",
                "proof": "Real command output.",
                "composition_structure": {"zones": ["identity", "metadata", "proof"],
                                          "hierarchy": ["display", "section", "supporting"],
                                          "negative_patterns": ["centered-template"]}},
    "asset": {"path": "dataset/retrieval/exemplars/curated-01.png", "sha256": "0" * 64, "kind": "curated"},
    "source": _source("exemplar-repo", "b"),
    "split": "train",
}


class RankerExemplarTest(unittest.TestCase):
    def test_exemplar_ranks_via_structure_tokens(self):
        # The exemplar's ONLY signal here is manifest_features=["metadata"],
        # which exists solely in composition_structure.zones. Before the _text
        # projection change this test must FAIL (red): the exemplar is not
        # ranked because its structure tokens are not indexed.
        query = {"project_type": "library", "sections": ["overview"], "tags": ["python"],
                 "manifest_features": ["metadata"], "evidence_sha256": "0" * 64}
        ranked = rank_records([TEXT_RECORD, EXEMPLAR_RECORD], query, k=5)
        exemplar = next(r for r in ranked if r["record_id"] == "exemplar-curated-01")
        feature_reasons = [r for r in exemplar["reasons"] if r["code"] == "retrieval.manifest-feature"]
        self.assertTrue(any("metadata" in r["matched_values"] for r in feature_reasons))

    def test_exemplar_outranks_text_for_hero_cli_query(self):
        query = {"project_type": "developer-tool", "sections": ["hero"], "tags": ["cli"],
                 "manifest_features": ["terminal"], "evidence_sha256": "0" * 64}
        ranked = rank_records([TEXT_RECORD, EXEMPLAR_RECORD], query, k=5)
        self.assertEqual(ranked[0]["record_id"], "exemplar-curated-01")

    def test_exemplar_packet_validates_after_projection(self):
        # rank_records copies the FULL pattern (composition_structure included),
        # but retrieval packet v2 accepts exactly {summary, structure, proof}
        # (strict _PATTERN_FIELDS). The ranker output projection must strip it.
        query = {"project_type": "library", "sections": ["overview"], "tags": ["python"],
                 "manifest_features": ["metadata"], "evidence_sha256": "0" * 64}
        ranked = rank_records([TEXT_RECORD, EXEMPLAR_RECORD], query, k=5)
        exemplar = next(r for r in ranked if r["record_id"] == "exemplar-curated-01")
        self.assertEqual(sorted(exemplar["pattern"]), ["proof", "structure", "summary"])
        packet = {
            "schema_version": 2, "status": "available", "mode": "production", "query": query,
            "dataset": {"dataset_id": "readme-showcase-retrieval", "dataset_revision": 3,
                        "manifest_sha256": "0" * 64},
            "records": ranked, "reason": None,
        }
        validated = validate_retrieval_packet_v2(packet)
        result = next(r for r in validated["records"] if r["record_id"] == "exemplar-curated-01")
        self.assertEqual(sorted(result["pattern"]), ["proof", "structure", "summary"])
        self.assertEqual(result["source_split"], "train")

    def test_exemplar_loader_validates_and_merges(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "exemplar-curated-01.json").write_text(
                json.dumps(EXEMPLAR_RECORD), encoding="utf-8")
            loaded = load_exemplar_records(root)
            self.assertEqual([r["record_id"] for r in loaded], ["exemplar-curated-01"])
            self.assertEqual(loaded[0]["split"], "train")
            # Loader output merges into the ranker input collection (never
            # manifest["records"]); the exemplar must rank once merged.
            query = {"project_type": "library", "sections": ["overview"], "tags": ["python"],
                     "manifest_features": ["metadata"], "evidence_sha256": "0" * 64}
            ranked = rank_records([TEXT_RECORD, *loaded], query, k=5)
            self.assertTrue(any(r["record_id"] == "exemplar-curated-01" for r in ranked))
