from __future__ import annotations

import hashlib
import importlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


_CORE = importlib.import_module("skill.scripts.pipeline_core")
scan_repository = _CORE.scan_repository
REPO_ROOT = Path(__file__).resolve().parents[1]


class RepositoryScannerTests(unittest.TestCase):
    def make_root(self, base: Path) -> Path:
        root = base / "target"
        root.mkdir()
        (root / "README.md").write_text("# Demo\n\nObservable proof.\n", encoding="utf-8")
        source = root / "src"
        source.mkdir()
        (source / "main.py").write_text("print('ok')\n", encoding="utf-8")
        return root

    def test_scan_is_deterministic_and_never_exposes_absolute_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(Path(temporary))

            first = scan_repository(root)
            second = scan_repository(root / ".." / "target")

            self.assertEqual(first, second)
            self.assertEqual(first["status"], "complete")
            self.assertEqual([item["path"] for item in first["files"]], ["README.md", "src/main.py"])
            self.assertNotIn(str(root), json.dumps(first))

    def test_symlink_submodule_secret_binary_and_invalid_utf8_are_excluded(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self.make_root(base)
            sentinel = base / "sentinel.txt"
            sentinel.write_text("must-not-leak", encoding="utf-8")
            (root / "escape").symlink_to(sentinel)
            (root / ".env").write_text("TOKEN=secret\n", encoding="utf-8")
            (root / "binary.bin").write_bytes(b"\x00secret")
            (root / "invalid.txt").write_bytes(b"\xff\xfe")
            nested = root / "external"
            nested.mkdir()
            (nested / ".git").mkdir()
            (nested / "README.md").write_text("submodule secret", encoding="utf-8")

            result = scan_repository(root)
            serialized = json.dumps(result)

            self.assertEqual(result["status"], "complete")
            self.assertNotIn("must-not-leak", serialized)
            self.assertNotIn("TOKEN=secret", serialized)
            self.assertNotIn("submodule secret", serialized)
            self.assertEqual(
                {warning["code"] for warning in result["warnings"]},
                {
                    "W_SCAN_BINARY",
                    "W_SCAN_INVALID_UTF8",
                    "W_SCAN_SECRET",
                    "W_SCAN_SUBMODULE",
                    "W_SCAN_SYMLINK",
                },
            )

    def test_every_safety_limit_returns_empty_incomplete_packet(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(Path(temporary))
            cases = (
                ("MAX_FILES", 1, "E_SCAN_FILE_COUNT"),
                ("MAX_DIRECTORIES", 0, "E_SCAN_DIRECTORY_COUNT"),
                ("MAX_FILE_BYTES", 2, "E_SCAN_FILE_SIZE"),
                ("MAX_TOTAL_BYTES", 2, "E_SCAN_TOTAL_SIZE"),
                ("MAX_DEPTH", 0, "E_SCAN_DEPTH"),
            )
            for constant, value, code in cases:
                with self.subTest(limit=constant), mock.patch.object(_CORE, constant, value):
                    result = scan_repository(root)
                    self.assertEqual(result["status"], "incomplete")
                    self.assertEqual(result["files"], [])
                    self.assertEqual(result["facts"], [])
                    self.assertEqual(result["warnings"][0]["code"], code)

            with mock.patch.object(
                _CORE.time,
                "monotonic",
                side_effect=[0.0, 99.0, 99.0, 99.0],
            ):
                result = scan_repository(root)
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual(result["files"], [])
            self.assertEqual(result["warnings"][0]["code"], "E_SCAN_TIME")

    def test_cli_writes_atomic_evidence_and_reports_hash(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self.make_root(base)
            output = base / "run" / "repository-evidence.json"

            result = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "scan",
                    "--root",
                    str(root),
                    "--output",
                    str(output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stderr, "")
            summary = json.loads(result.stdout)
            self.assertEqual(summary["status"], "complete")
            self.assertEqual(
                summary["evidence_sha256"],
                hashlib.sha256(output.read_bytes()).hexdigest(),
            )
            self.assertEqual(json.loads(output.read_text(encoding="utf-8"))["status"], "complete")
            self.assertFalse(any(path.suffix == ".tmp" for path in output.parent.iterdir()))


if __name__ == "__main__":
    unittest.main()
