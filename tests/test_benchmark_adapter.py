from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from skill.scripts.benchmark_adapter import (
    DATASET_ID,
    DATASET_REVISION,
    import_benchmark,
)
from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.pipeline_core import scan_repository


REPO_ROOT = Path(__file__).resolve().parents[1]
ROWS = [
    {
        "repo_name": "example/alpha",
        "repo_commit": "1" * 40,
        "repo_content": "def alpha():\n    return 1\n",
        "repo_readme": "# Alpha\n",
    },
    {
        "repo_name": "example/beta",
        "repo_commit": "2" * 40,
        "repo_content": "def beta():\n    return 2\n",
        "repo_readme": "# Beta\n",
    },
]


def write_input(path: Path, rows: list[dict[str, str]]) -> None:
    if path.suffix == ".jsonl":
        path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
            encoding="utf-8",
        )
    else:
        path.write_bytes(canonical_json_bytes(rows))


def write_sidecar(
    path: Path,
    input_path: Path,
    *,
    revision: str = DATASET_REVISION,
    input_sha256: str | None = None,
    licenses: list[dict[str, Any]] | None = None,
) -> None:
    if licenses is None:
        licenses = [
            {
                "repo_name": row["repo_name"],
                "repo_commit": row["repo_commit"],
                "license_spdx": "MIT",
                "license_evidence_url": (
                    f"https://github.com/{row['repo_name']}/blob/"
                    f"{row['repo_commit']}/LICENSE"
                ),
                "license_evidence_sha256": "a" * 64,
                "human_reviewed": True,
            }
            for row in ROWS
        ]
    payload = {
        "schema_version": 1,
        "dataset_id": DATASET_ID,
        "dataset_revision": revision,
        "dataset_license_spdx": "Apache-2.0",
        "split": "test",
        "input_format": input_path.suffix.removeprefix("."),
        "input_sha256": input_sha256
        or hashlib.sha256(input_path.read_bytes()).hexdigest(),
        "licenses": licenses,
    }
    path.write_bytes(canonical_json_bytes(payload))


class BenchmarkAdapterTests(unittest.TestCase):
    def test_json_and_jsonl_import_two_licensed_rows_atomically(self) -> None:
        for suffix in (".json", ".jsonl"):
            with self.subTest(suffix=suffix), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_path = root / f"rows{suffix}"
                sidecar = root / "licenses.json"
                output = root / "run" / "evaluation-only"
                write_input(input_path, ROWS)
                write_sidecar(sidecar, input_path)

                result = import_benchmark(input_path, sidecar, output)

                self.assertEqual(result["status"], "imported")
                self.assertEqual(result["row_count"], 2)
                manifest = json.loads(
                    (output / "manifest.json").read_text(encoding="utf-8")
                )
                self.assertEqual(manifest["dataset_revision"], DATASET_REVISION)
                self.assertEqual(manifest["purpose"], "evaluation-only")
                self.assertEqual(manifest["coverage"], "generator-filtered-python-files")
                self.assertEqual(len(list((output / "rows").glob("*.json"))), 2)
                self.assertFalse(any(".tmp-" in path.name for path in root.rglob("*")))

    def test_revision_hash_license_schema_and_path_fail_without_output(self) -> None:
        cases = ("revision", "hash", "license", "schema", "path")
        for case in cases:
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_path = root / "rows.json"
                sidecar = root / "licenses.json"
                output = root / "run" / "evaluation-only"
                rows = [dict(row) for row in ROWS]
                if case == "schema":
                    rows[0]["unexpected"] = "blocked"
                write_input(input_path, rows)
                licenses = None
                if case == "license":
                    licenses = []
                write_sidecar(
                    sidecar,
                    input_path,
                    revision="main" if case == "revision" else DATASET_REVISION,
                    input_sha256="0" * 64 if case == "hash" else None,
                    licenses=licenses,
                )
                if case == "path":
                    output = REPO_ROOT / "evaluation-only"

                with self.assertRaises(ContractError):
                    import_benchmark(input_path, sidecar, output)

                self.assertFalse(output.exists())

    def test_duplicate_gold_identity_and_missing_license_leave_no_partial_output(
        self,
    ) -> None:
        for case in ("duplicate", "missing-license"):
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                input_path = root / "rows.json"
                sidecar = root / "licenses.json"
                output = root / "evaluation-only"
                rows = ROWS + ([ROWS[0]] if case == "duplicate" else [])
                write_input(input_path, rows)
                licenses: list[dict[str, Any]] | None = None
                if case == "missing-license":
                    licenses = [
                        {
                            "repo_name": ROWS[0]["repo_name"],
                            "repo_commit": ROWS[0]["repo_commit"],
                            "license_spdx": "MIT",
                            "license_evidence_url": (
                                "https://github.com/example/alpha/blob/"
                                f"{ROWS[0]['repo_commit']}/LICENSE"
                            ),
                            "license_evidence_sha256": "a" * 64,
                            "human_reviewed": True,
                        }
                    ]
                write_sidecar(sidecar, input_path, licenses=licenses)

                with self.assertRaises(ContractError):
                    import_benchmark(input_path, sidecar, output)

                self.assertFalse(output.exists())

    def test_cli_import_and_scanner_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            input_path = root / "rows.jsonl"
            sidecar = root / "licenses.json"
            output = root / "evaluation-only"
            write_input(input_path, ROWS)
            write_sidecar(sidecar, input_path)

            result = subprocess.run(
                [
                    sys.executable,
                    "skill/scripts/readme_pipeline.py",
                    "import-benchmark",
                    "--input",
                    str(input_path),
                    "--license-sidecar",
                    str(sidecar),
                    "--output-dir",
                    str(output),
                ],
                cwd=REPO_ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["row_count"], 2)
            (root / "README.md").write_text("# Public\n", encoding="utf-8")
            evidence = scan_repository(root)
            self.assertEqual(
                [
                    item["path"]
                    for item in cast(list[dict[str, object]], evidence["files"])
                ],
                ["README.md", "licenses.json", "rows.jsonl"],
            )


if __name__ == "__main__":
    unittest.main()
