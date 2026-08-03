from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.scan import (
    adapt_v1_scan,
    canonical_v1_scan_bytes,
    scan_allows_publish,
    validate_repository_scan_v2,
)
from skill.scripts.readme_showcase.scanner import service
from skill.scripts.readme_showcase.scanner.service import ScanLimits, scan_repository_v2


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"


class RepositoryScanV2ContractTests(unittest.TestCase):
    def git(self, root: Path, *arguments: str) -> None:
        subprocess.run(["git", "-C", str(root), *arguments], check=True, capture_output=True)

    def make_cli(self, base: Path, *, late: bytes = b"late\n") -> Path:
        root = base / "target"
        root.mkdir()
        files = {
            "bin/demo": b"#!/bin/sh\n",
            "package.json": b'{"bin":{"demo":"bin/demo"}}\n',
            "tests/test_demo.py": b"assert True\n",
            "zzz.txt": late,
        }
        for name, raw in files.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        self.git(root, "init", "-q")
        self.git(root, "config", "user.email", "scan@example.invalid")
        self.git(root, "config", "user.name", "Scan Test")
        self.git(root, "add", "--all")
        self.git(root, "commit", "-qm", "fixture")
        return root

    def assert_code(self, code: str, function: object, *arguments: object, **keywords: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*arguments, **keywords)  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)

    def test_late_count_file_total_and_time_caps_retain_minimum_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_cli(Path(temporary), late=b"x" * 50)
            baseline = scan_repository_v2(root, "cli")
            first_three = baseline["files"][:3]
            retained_bytes = sum(item["bytes"] for item in first_three)
            cases = (
                (ScanLimits(files=3), None, "file-count-limit"),
                (ScanLimits(file_bytes=40), None, "file-size-limit"),
                (ScanLimits(total_bytes=retained_bytes), None, "total-size-limit"),
                (ScanLimits(seconds=1), mock.Mock(side_effect=[0, 0, 0, 0, 2]), "time-limit"),
            )
            for limits, clock, reason in cases:
                with self.subTest(reason=reason):
                    result = scan_repository_v2(root, "cli", limits=limits, clock=clock or service.time.monotonic)
                    self.assertEqual(result["status"], "partial")
                    self.assertEqual(result["files"], first_three)
                    self.assertEqual(result["facts"], baseline["facts"][:3])
                    self.assertEqual(result["skipped"][-1]["reason"], reason)
                    self.assertEqual(result["coverage"]["content_bytes"], retained_bytes)
                    self.assertEqual(
                        result["coverage"]["selected_files"],
                        result["coverage"]["content_files"] + result["coverage"]["skipped_files"],
                    )

    def test_cap_before_minimum_is_incomplete_with_required_skips(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_cli(Path(temporary))
            result = scan_repository_v2(root, "cli", limits=ScanLimits(files=1))
            self.assertEqual(result["status"], "incomplete")
            self.assertEqual([item["path"] for item in result["files"]], ["bin/demo"])
            self.assertEqual(result["policy"]["missing_evidence"], ["manifest", "usage-or-test"])
            self.assertTrue(any(skip["required_for_generation"] for skip in result["skipped"]))
            validate_repository_scan_v2(result)

    def test_minimum_policy_fixture_covers_every_project_type(self) -> None:
        cases = json.loads((FIXTURES / "scanner" / "minimum-policy-cases.json").read_text(encoding="utf-8"))
        for case in cases["cases"]:
            with self.subTest(case=case["name"]), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary) / "target"
                root.mkdir()
                for name in case["paths"]:
                    path = root / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    path.write_text("evidence\n", encoding="utf-8")
                result = scan_repository_v2(root, case["project_type"])
                self.assertEqual(result["status"], case["status"])
                self.assertEqual(result["policy"]["missing_evidence"], case["missing_evidence"])

    def test_schema_and_python_validator_cover_fixtures_and_equations(self) -> None:
        self.assertEqual(importlib.metadata.version("jsonschema"), "4.26.0")
        schema = json.loads((ROOT / "skill" / "schemas" / "repository-scan.v2.schema.json").read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        validator = Draft202012Validator(schema)
        valid = json.loads((FIXTURES / "contracts" / "repository-scan-v2.valid.json").read_text(encoding="utf-8"))
        invalid = json.loads((FIXTURES / "contracts" / "repository-scan-v2.invalid.json").read_text(encoding="utf-8"))
        self.assertEqual(list(validator.iter_errors(valid)), [])
        self.assertEqual(validate_repository_scan_v2(valid), valid)
        for case in invalid["cases"]:
            with self.subTest(case=case["name"]):
                self.assert_code(case["code"], validate_repository_scan_v2, case["payload"])

    def test_impossible_coverage_and_capability_packets_cannot_publish(self) -> None:
        valid = json.loads((FIXTURES / "contracts" / "repository-scan-v2.valid.json").read_text(encoding="utf-8"))
        impossible = copy.deepcopy(valid)
        impossible["coverage"]["tracked_files"] = 1
        impossible["coverage"]["indexed_files"] = 4
        self.assert_code("E_SCAN_COVERAGE", validate_repository_scan_v2, impossible)
        self.assert_code("E_SCAN_COVERAGE", scan_allows_publish, impossible)

        complete = copy.deepcopy(valid)
        complete["status"] = "complete"
        complete["skipped"] = []
        complete["coverage"]["skipped_files"] = 0
        complete["coverage"]["selected_files"] = 3
        complete["policy"]["publish_eligible"] = True
        complete["policy"]["allowed_consumers"] = ["audit", "publish", "readme"]
        complete["coverage"]["tracked_files"] = 99
        self.assert_code("E_SCAN_COVERAGE", validate_repository_scan_v2, complete)
        self.assert_code("E_SCAN_COVERAGE", scan_allows_publish, complete)

        forged_partial = copy.deepcopy(valid)
        forged_partial["policy"]["publish_eligible"] = True
        forged_partial["policy"]["allowed_consumers"] = ["audit", "publish", "readme"]
        schema = json.loads((ROOT / "skill" / "schemas" / "repository-scan.v2.schema.json").read_text(encoding="utf-8"))
        self.assertTrue(list(Draft202012Validator(schema).iter_errors(forged_partial)))
        self.assert_code("E_SCAN_POLICY", scan_allows_publish, forged_partial)

    def test_partial_and_incomplete_never_authorize_publish(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_cli(Path(temporary))
            complete = scan_repository_v2(root, "cli")
            partial = scan_repository_v2(root, "cli", limits=ScanLimits(files=3))
            incomplete = scan_repository_v2(root, "cli", limits=ScanLimits(files=1))
            self.assertTrue(scan_allows_publish(complete))
            self.assertFalse(scan_allows_publish(partial))
            self.assertFalse(scan_allows_publish(incomplete))
            self.assertNotIn("publish", partial["policy"]["allowed_consumers"])
            self.assertEqual(adapt_v1_scan(partial)["status"], "incomplete")
            self.assertEqual(adapt_v1_scan(partial)["files"], [])

    def test_invalid_config_and_explicit_path_fail_before_body_read(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "target"
            root.mkdir()
            (root / ".env").write_text("TOKEN=protected-body", encoding="utf-8")
            (root / ".readme-showcase.json").write_text('{"scanner":{"unknown":true}}', encoding="utf-8")
            with mock.patch.object(service, "_read", side_effect=AssertionError("body read")) as read:
                self.assert_code("E_SCANNER_CONFIG", scan_repository_v2, root, "unknown")
            read.assert_not_called()
            with mock.patch.object(service, "_read", side_effect=AssertionError("body read")) as read:
                self.assert_code("E_SCAN_ROOT", scan_repository_v2, root / "missing", "unknown")
            read.assert_not_called()

    def test_race_secret_binary_symlink_and_fifo_are_skipped_without_unsafe_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "target"
            root.mkdir()
            (root / "README.md").write_text("safe\n", encoding="utf-8")
            (root / "package.json").write_text("{}\n", encoding="utf-8")
            (root / ".env").write_text("TOKEN=body-sentinel", encoding="utf-8")
            (root / "binary.dat").write_bytes(b"\0binary-sentinel")
            (root / "linked").symlink_to(base / "outside")
            os.mkfifo(root / "pipe")
            original = service._read

            def race(path: Path, expected: os.stat_result, relative: str, maximum: int) -> bytes:
                if relative == "README.md":
                    path.write_text("changed-untrusted\n", encoding="utf-8")
                return original(path, expected, relative, maximum)

            with mock.patch.object(service, "_read", side_effect=race) as read:
                result = scan_repository_v2(root, "unknown")
            serialized = json.dumps(result)
            self.assertNotIn("body-sentinel", serialized)
            self.assertNotIn("binary-sentinel", serialized)
            self.assertNotIn("changed-untrusted", serialized)
            self.assertEqual(
                {item["reason"] for item in result["skipped"]},
                {"binary", "race", "required-evidence-missing", "secret", "special-file", "symlink"},
            )
            self.assertNotIn(".env", [call.args[2] for call in read.call_args_list])

    def test_tracked_nested_parent_symlink_is_skipped_without_outside_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = self.make_cli(base)
            nested = root / "nested"
            nested.mkdir()
            (nested / "tracked.txt").write_text("committed-safe\n", encoding="utf-8")
            self.git(root, "add", "nested/tracked.txt")
            self.git(root, "commit", "-qm", "nested fixture")

            outside = base / "outside"
            outside.mkdir()
            (outside / "tracked.txt").write_text("OUTSIDE_SENTINEL_BYTES\n", encoding="utf-8")
            nested.rename(root / "nested-real")
            nested.symlink_to(outside, target_is_directory=True)

            result = scan_repository_v2(root, "cli")
            serialized = json.dumps(result)

            self.assertNotIn("OUTSIDE_SENTINEL_BYTES", serialized)
            self.assertNotIn("nested/tracked.txt", [item["path"] for item in result["files"]])
            self.assertEqual(result["status"], "partial")
            self.assertEqual(
                next(item for item in result["skipped"] if item["path"] == "nested/tracked.txt")["reason"],
                "race",
            )
            validate_repository_scan_v2(result)

    def test_v1_adapter_preserves_input_bytes_order_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_cli(Path(temporary))
            legacy = service.scan_repository_v1(root)
            before = canonical_json_bytes(legacy)
            self.assertEqual(canonical_v1_scan_bytes(legacy), before)
            self.assertEqual(canonical_json_bytes(adapt_v1_scan(copy.deepcopy(legacy))), before)
            self.assertEqual(canonical_json_bytes(legacy), before)
            self.assert_code("E_SCAN_ROOT", service.scan_repository_v1, root / "missing")
            self.assertEqual(hashlib.sha256(canonical_v1_scan_bytes(legacy)).hexdigest(), hashlib.sha256(before).hexdigest())

    def test_cli_v2_complete_partial_incomplete_and_fail_fast_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            complete = base / "complete"
            complete.mkdir()
            (complete / "README.md").write_text("# Demo\n", encoding="utf-8")
            (complete / "package.json").write_text("{}\n", encoding="utf-8")
            complete_output = base / "complete.json"
            complete_run = self.cli(complete, complete_output, "--schema-version", "2", "--project-type", "unknown")
            self.assertEqual(complete_run.returncode, 0, complete_run.stderr)
            self.assertEqual(json.loads(complete_output.read_text(encoding="utf-8"))["status"], "complete")

            partial_base = base / "partial"
            partial_base.mkdir()
            partial = self.make_cli(partial_base)
            (partial / ".readme-showcase.json").write_text(
                json.dumps({"scanner": {"include": ["bin/**", "package.json", "tests/**", "zzz.txt"], "limits": {"max_content_files": 3}}}),
                encoding="utf-8",
            )
            partial_output = base / "partial.json"
            partial_run = self.cli(partial, partial_output, "--schema-version", "2", "--project-type", "cli")
            self.assertEqual(partial_run.returncode, 0, partial_run.stderr)
            self.assertEqual(json.loads(partial_output.read_text(encoding="utf-8"))["status"], "partial")

            incomplete = base / "incomplete"
            incomplete.mkdir()
            (incomplete / "README.md").write_text("# Only docs\n", encoding="utf-8")
            incomplete_output = base / "incomplete.json"
            incomplete_run = self.cli(incomplete, incomplete_output, "--schema-version", "2", "--project-type", "cli")
            self.assertEqual(incomplete_run.returncode, 1, incomplete_run.stderr)
            self.assertEqual(json.loads(incomplete_output.read_text(encoding="utf-8"))["status"], "incomplete")

            invalid = base / "invalid"
            invalid.mkdir()
            (invalid / ".readme-showcase.json").write_text('{"scanner":{"unknown":true}}', encoding="utf-8")
            absent = base / "absent.json"
            invalid_run = self.cli(invalid, absent, "--schema-version", "2", "--project-type", "unknown")
            self.assertEqual(invalid_run.returncode, 2)
            self.assertIn("E_SCANNER_CONFIG", invalid_run.stderr)
            self.assertFalse(absent.exists())
            previous = base / "previous.json"
            previous.write_bytes(b"last-good\n")
            invalid_again = self.cli(invalid, previous, "--schema-version", "2", "--project-type", "unknown")
            self.assertEqual(invalid_again.returncode, 2)
            self.assertEqual(previous.read_bytes(), b"last-good\n")
            missing_output = base / "missing-root.json"
            missing_run = self.cli(base / "does-not-exist", missing_output, "--schema-version", "2", "--project-type", "unknown")
            self.assertEqual(missing_run.returncode, 2)
            self.assertIn("E_SCAN_ROOT", missing_run.stderr)
            self.assertFalse(missing_output.exists())

    def test_cli_default_and_explicit_v1_are_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            root = base / "target"
            root.mkdir()
            (root / "README.md").write_text("# Demo\n", encoding="utf-8")
            default_output = base / "default.json"
            explicit_output = base / "explicit.json"
            default = self.cli(root, default_output)
            explicit = self.cli(root, explicit_output, "--schema-version", "1")
            self.assertEqual(default.returncode, 0, default.stderr)
            self.assertEqual(explicit.returncode, 0, explicit.stderr)
            self.assertEqual(default.stdout, explicit.stdout)
            self.assertEqual(default_output.read_bytes(), explicit_output.read_bytes())

    def cli(self, root: Path, output: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                os.sys.executable,
                "skill/scripts/readme_pipeline.py",
                "scan",
                "--root",
                str(root),
                "--output",
                str(output),
                *arguments,
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )


if __name__ == "__main__":
    unittest.main()
