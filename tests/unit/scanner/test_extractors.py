from __future__ import annotations

import builtins
import copy
import hashlib
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.readme_showcase.contracts.common import canonical_json_bytes
from skill.scripts.readme_showcase.contracts.evidence import validate_fact
from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.evidence.adapters import adapt_verified_command_observation
from skill.scripts.readme_showcase.scanner.extractors import ExtractorService, extract_repository


FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "repositories"


class ExtractorTests(unittest.TestCase):
    def facts(self, result: dict[str, object], key: str) -> list[dict[str, object]]:
        return [fact for fact in result["facts"] if fact["semantic_key"] == key]  # type: ignore[index]

    def test_python_repository_is_deterministic_and_complete(self) -> None:
        root = FIXTURES / "python-project"
        first = extract_repository(root)
        second = extract_repository(root)
        self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
        self.assertEqual(first["warnings"], [])
        values = {(fact["semantic_key"], json.dumps(fact["value"], sort_keys=True)) for fact in first["facts"]}
        for expected in (
            ("project-name", '"demo-cli"'),
            ("requires-python", '">=3.10,<3.14"'),
            ("python-script:demo", '"demo_cli:main"'),
            ("ci-os", '["macos-latest", "ubuntu-latest"]'),
            ("ci-os-families", '["linux", "macos"]'),
            ("ci-python-versions", '["3.10", "3.11", "3.12", "3.13"]'),
            ("test-count", "1"),
        ):
            self.assertIn(expected, values)
        self.assertNotIn("decoy", json.dumps(first))
        self.assertTrue(all(validate_fact(fact) == fact for fact in first["facts"]))

    def test_actions_text_rules_require_plausible_jobs_context(self) -> None:
        cases = {
            "malformed-jobs": (
                "jobs: [\n  os: [ubuntu-latest]\n  runs-on: macos-latest\n  run: curl decoy.invalid\n",
                [],
                ["W_EXTRACT_CI_STRUCTURE"],
            ),
            "env-and-block-decoys": (
                "jobs:\n  build:\n    env:\n      os: [windows-latest]\n      runs-on: windows-latest\n"
                "    description: \"\n      runs-on: windows-latest\n      run: curl quoted.invalid\n    \"\n"
                "    runs-on: ubuntu-latest\n    steps:\n      - name: 'runs-on: windows-latest'\n"
                "      - run: |\n          echo run: curl decoy.invalid\n          run: curl decoy.invalid\n",
                ["ci-os"],
                [],
            ),
            "valid-matrix-list-scalar": (
                "jobs:\n  test:\n    strategy:\n      matrix:\n        os: [ubuntu-latest, macos-latest]\n"
                "        python-version: ['3.10', '3.11']\n    runs-on: ubuntu-latest\n"
                "    steps:\n      - run: python -m unittest\n",
                ["ci-os", "ci-os-families", "ci-python-versions", "ci-os", "ci-command:9"],
                [],
            ),
        }
        for name, (text, keys, warnings) in cases.items():
            with self.subTest(case=name):
                result = ExtractorService().extract_files([(".github/workflows/ci.yml", text.encode())])
                self.assertEqual([fact["semantic_key"] for fact in result["facts"]], keys)
                self.assertEqual([warning["code"] for warning in result["warnings"]], warnings)

    def test_readme_identity_and_documented_command_ignore_html_comment(self) -> None:
        raw = b"# Demo CLI\n\n```sh\n$ demo --help\n```\n<!-- $ decoy --secret -->\n"
        result = ExtractorService().extract_files([("README.md", raw)])
        self.assertEqual([fact["semantic_key"] for fact in result["facts"]], ["readme-identity", "readme-command:4"])
        self.assertNotIn("decoy", json.dumps(result))

    def test_node_repository_extracts_bin_scripts_engine_cli_and_tests(self) -> None:
        result = extract_repository(FIXTURES / "node-project")
        values = {(fact["semantic_key"], json.dumps(fact["value"], sort_keys=True)) for fact in result["facts"]}
        self.assertIn(("package-name", '"demo-node"'), values)
        self.assertIn(("node-bin:demo-node", '"src/cli.js"'), values)
        self.assertIn(("node-engine:node", '">=20"'), values)
        self.assertIn(("node-script:test", '"node --test"'), values)
        self.assertIn(("test-count", "1"), values)
        self.assertIn(("test-framework", '"node:test"'), values)
        self.assertIn(("javascript-shebang", '"node"'), values)
        self.assertNotIn("decoy", json.dumps(result))

    def test_javascript_test_count_ignores_string_comment_and_regex_decoys(self) -> None:
        raw = b'''import test from "node:test";\nconst one = 'test("string")';\n// test("comment", () => {});\nconst pattern = /test\\(/;\ntest("real", () => {});\n'''
        result = ExtractorService().extract_files([("test/example.test.js", raw)])
        count = next(fact["value"] for fact in result["facts"] if fact["semantic_key"] == "test-count")
        self.assertEqual(count, 1)

    def test_generic_config_redacts_secret_keys_and_source_hash_rejects_stale_bytes(self) -> None:
        raw = b'{"feature": {"enabled": true}, "api_token": "never-emit"}\n'
        result = ExtractorService().extract_files([("config/settings.json", raw)])
        self.assertEqual([fact["value"] for fact in result["facts"]], [True])
        self.assertEqual(result["warnings"], [{"code": "W_EXTRACT_SECRET_KEY", "line": 1, "path": "config/settings.json"}])
        self.assertNotIn("never-emit", json.dumps(result))
        with self.assertRaises(ContractError) as caught:
            validate_fact(result["facts"][0], source_bytes=b"changed")
        self.assertEqual(caught.exception.code, "E_SOURCE_HASH")

    def test_malformed_and_secret_inputs_warn_without_guessing_or_reading_secret(self) -> None:
        root = FIXTURES / "malformed-config"
        original_open = os.open

        def guarded_open(path: object, *args: object, **kwargs: object) -> int:
            if str(path).endswith(".env"):
                raise AssertionError("secret body read")
            return original_open(path, *args, **kwargs)

        with mock.patch.object(os, "open", side_effect=guarded_open):
            first = extract_repository(root)
            second = extract_repository(root)
        self.assertEqual(first, second)
        self.assertEqual(
            first["warnings"],
            [
                {"code": "W_EXTRACT_SECRET", "line": 1, "path": ".env"},
                {"code": "W_EXTRACT_JSON", "line": 1, "path": "package.json"},
                {"code": "W_EXTRACT_TOML", "line": 1, "path": "pyproject.toml"},
            ],
        )
        serialized = json.dumps(first)
        self.assertNotIn("must-not-be", serialized)
        self.assertNotIn("README_SHOWCASE_SECRET", serialized)

    def test_no_target_import_or_process_and_no_filesystem_side_effect(self) -> None:
        root = FIXTURES / "python-project"
        before = self.tree_hash(root)
        real_import = builtins.__import__

        def guarded_import(name: str, *args: object, **kwargs: object) -> object:
            if name.startswith("demo_cli"):
                raise AssertionError("target import")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=guarded_import), mock.patch.object(
            subprocess, "run", side_effect=AssertionError("process execution")
        ), mock.patch.object(subprocess, "Popen", side_effect=AssertionError("process execution")):
            extract_repository(root)
        self.assertEqual(self.tree_hash(root), before)

    def test_symlink_fifo_limits_unicode_order_and_mutation_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "b.json").write_text('{"z": 2}', encoding="utf-8")
            (root / "a.json").write_text('{"é": 1}', encoding="utf-8")
            (root / "escape.json").symlink_to(root / "a.json")
            os.mkfifo(root / "pipe.toml")
            result = ExtractorService(max_files=4, max_file_bytes=64, max_total_bytes=128).extract(root)
            self.assertEqual([warning["code"] for warning in result["warnings"]], ["W_EXTRACT_SYMLINK", "W_EXTRACT_SPECIAL"])
            self.assertEqual(result, ExtractorService(max_files=4, max_file_bytes=64, max_total_bytes=128).extract(root))
            self.assertEqual([fact["source"]["path"] for fact in result["facts"]], sorted([fact["source"]["path"] for fact in result["facts"]]))

            original = Path("skill/scripts/readme_showcase/scanner/extractors.py")
            with mock.patch("skill.scripts.readme_showcase.scanner.extractors._read", side_effect=RuntimeError("mutation")):
                with self.assertRaisesRegex(RuntimeError, "mutation"):
                    ExtractorService().extract(root)
            self.assertTrue(original.is_file())

    def test_command_observation_adapter_copies_only_verified_envelope(self) -> None:
        envelope = {
            "command_id": "tests",
            "command": "python -m unittest",
            "cwd": ".",
            "exit_code": 0,
            "stdout_sha256": "1" * 64,
            "stderr_sha256": "2" * 64,
            "base_sha": "3" * 40,
            "input_hashes": {"repository": "4" * 64},
            "runner": "controlled-ci",
            "verification": "verified",
        }
        raw = canonical_json_bytes(envelope)
        fact = adapt_verified_command_observation(envelope, path="evidence/tests.json", source_bytes=raw)
        self.assertEqual(fact["kind"], "command-observation")
        self.assertEqual(fact["value"], envelope)
        self.assertEqual(validate_fact(fact, source_bytes=raw), fact)
        invalid = copy.deepcopy(envelope)
        invalid["verification"] = "imported-unverified"
        with self.assertRaisesRegex(ValueError, "verified"):
            adapt_verified_command_observation(invalid, path="evidence/tests.json", source_bytes=raw)
        missing = copy.deepcopy(envelope)
        del missing["input_hashes"]
        with self.assertRaisesRegex(ValueError, "input_hashes"):
            adapt_verified_command_observation(missing, path="evidence/tests.json", source_bytes=raw)

    @staticmethod
    def tree_hash(root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted((item for item in root.rglob("*") if item.is_file()), key=lambda item: item.as_posix()):
            digest.update(path.relative_to(root).as_posix().encode())
            digest.update(path.read_bytes())
        return digest.hexdigest()


if __name__ == "__main__":
    unittest.main()
