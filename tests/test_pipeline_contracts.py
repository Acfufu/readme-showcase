from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import (
    MAX_JSON_BYTES,
    ContractError,
    canonical_json_bytes,
    canonical_sha256,
    read_json_object,
    validate_contract,
    write_bytes_atomic,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


class ExistingAuditCompatibilityTests(unittest.TestCase):
    def test_audit_without_readme_preserves_usage_contract(self) -> None:
        result = subprocess.run(
            [sys.executable, "skill/scripts/audit_readme.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertEqual(
            result.stderr,
            "usage: audit_readme.py /path/to/README.md\n",
        )
        self.assertEqual(result.stdout, "")


class PipelineContractTests(unittest.TestCase):
    def test_canonical_json_is_stable_utf8_and_lf_terminated(self) -> None:
        first = {"z": "证据", "schema_version": 1, "a": [3, 2, 1]}
        second = {"a": [3, 2, 1], "schema_version": 1, "z": "证据"}

        expected = (
            '{"a":[3,2,1],"schema_version":1,"z":"证据"}\n'.encode()
        )
        self.assertEqual(canonical_json_bytes(first), expected)
        self.assertEqual(canonical_json_bytes(second), expected)
        self.assertEqual(
            canonical_sha256(first),
            hashlib.sha256(expected).hexdigest(),
        )

    def test_schema_version_and_unknown_fields_fail_with_stable_codes(self) -> None:
        with self.assertRaises(ContractError) as version_error:
            validate_contract(
                {"schema_version": 2},
                required={"schema_version"},
                optional=set(),
                context="fixture",
            )
        self.assertEqual(version_error.exception.code, "E_SCHEMA_VERSION")

        with self.assertRaises(ContractError) as field_error:
            validate_contract(
                {"schema_version": 1, "extra": True},
                required={"schema_version"},
                optional=set(),
                context="fixture",
            )
        self.assertEqual(
            field_error.exception.code,
            "E_SCHEMA_UNKNOWN_FIELD",
        )

    def test_atomic_write_failure_preserves_previous_destination(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "result.json"
            destination.write_bytes(b"previous\n")

            with mock.patch.object(
                os,
                "replace",
                side_effect=OSError("forced replacement failure"),
            ):
                with self.assertRaises(OSError):
                    write_bytes_atomic(destination, b"candidate\n")

            self.assertEqual(destination.read_bytes(), b"previous\n")
            self.assertEqual(
                list(destination.parent.glob(f".{destination.name}.*.tmp")),
                [],
            )

    def test_json_reader_rejects_symlinks_and_oversized_files(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            target = root / "target.json"
            target.write_text('{"schema_version":1}\n', encoding="utf-8")
            link = root / "link.json"
            link.symlink_to(target)

            with self.assertRaises(ContractError) as linked:
                read_json_object(link)
            self.assertEqual(linked.exception.code, "E_INPUT_PATH")

            oversized = root / "oversized.json"
            oversized.write_bytes(b" " * (MAX_JSON_BYTES + 1))
            with self.assertRaises(ContractError) as bounded:
                read_json_object(oversized)
            self.assertEqual(bounded.exception.code, "E_INPUT_SIZE")

    def test_atomic_write_rejects_symlinked_parent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            outside = root / "outside"
            outside.mkdir()
            linked_parent = root / "run"
            linked_parent.symlink_to(outside, target_is_directory=True)
            destination = linked_parent / "result.json"

            with self.assertRaises(ContractError) as raised:
                write_bytes_atomic(destination, b"candidate\n")

            self.assertEqual(raised.exception.code, "E_OUTPUT_PATH")
            self.assertFalse((outside / "result.json").exists())


class PipelineCliTests(unittest.TestCase):
    def run_cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "skill/scripts/readme_pipeline.py", *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_help_lists_all_approved_subcommands(self) -> None:
        result = self.run_cli("--help")

        self.assertEqual(result.returncode, 0)
        for subcommand in (
            "validate-dataset",
            "scan",
            "retrieve",
            "validate-bundle",
            "evaluate",
            "import-benchmark",
            "build-pr-bundle",
            "check-publish-gate",
        ):
            self.assertIn(subcommand, result.stdout)

    def test_invalid_schema_diagnostics_use_stderr_and_exit_two(self) -> None:
        fixtures = (
            ({"schema_version": 2}, "E_SCHEMA_VERSION"),
            (
                {"schema_version": 1, "unexpected": True},
                "E_SCHEMA_UNKNOWN_FIELD",
            ),
        )
        with tempfile.TemporaryDirectory() as temporary_directory:
            for index, (payload, code) in enumerate(fixtures):
                with self.subTest(code=code):
                    bundle = Path(temporary_directory) / f"bundle-{index}.json"
                    bundle.write_text(json.dumps(payload), encoding="utf-8")

                    result = self.run_cli(
                        "validate-bundle",
                        "--bundle",
                        str(bundle),
                    )

                    self.assertEqual(result.returncode, 2)
                    self.assertEqual(result.stdout, "")
                    self.assertIn(code, result.stderr)


if __name__ == "__main__":
    unittest.main()
