from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.contracts.evidence import build_fact, compute_graph_sha256
from skill.scripts.readme_showcase.delivery.worktree import _validate_evidence_base


class EvidenceBindingTests(unittest.TestCase):
    README = b"Build the CLI.\nRun the tests.\n"

    def _graph(self) -> dict[str, object]:
        facts = [
            build_fact(
                kind="file-presence",
                path="README.md",
                locator=None,
                semantic_key="presence",
                value=True,
                source_bytes=self.README,
                confidence="observed",
            ),
            build_fact(
                kind="voice-sample",
                path="git-log",
                locator={"line_start": 1, "line_end": 2},
                semantic_key="voice-sample:commit-subjects",
                value={"script": "latin", "sentences": [3, 3], "imperative_count": 2, "term_count": 0},
                source_bytes=b"build the cli\nfix the tests\n",
                confidence="derived",
                derivation="voice features derived from the last 20 git commit subjects",
            ),
        ]
        graph: dict[str, object] = {"schema_version": 2, "facts": facts}
        graph["evidence_sha256"] = compute_graph_sha256(graph)
        return graph

    def _bind(self, worktree: Path) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            artifact_root = Path(temporary)
            graph = self._graph()
            raw = json.dumps(graph, sort_keys=True, separators=(",", ":")).encode("utf-8")
            (artifact_root / "repository-evidence.json").write_bytes(raw)
            payload = {
                "artifacts": {
                    "evidence": {
                        "path": "repository-evidence.json",
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                }
            }
            _validate_evidence_base(payload, artifact_root, worktree)

    def test_git_log_voice_source_is_not_bound_to_a_checkout_file(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worktree = Path(temporary)
            (worktree / "README.md").write_bytes(self.README)
            self._bind(worktree)

    def test_real_file_evidence_source_still_binds_to_the_checkout(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            worktree = Path(temporary)
            (worktree / "README.md").write_text("tampered prose\n", encoding="utf-8")
            with self.assertRaises(ContractError) as caught:
                self._bind(worktree)
            self.assertEqual(caught.exception.code, "E_PR_EVIDENCE")


if __name__ == "__main__":
    unittest.main()
