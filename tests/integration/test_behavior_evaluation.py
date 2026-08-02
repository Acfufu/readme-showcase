from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes, canonical_sha256, write_canonical_json_atomic
from skill.scripts.pipeline_core import evaluate_generated_bundle
from skill.scripts.readme_showcase.contracts.evaluation import (
    read_command_observation,
    validate_command_observation,
    validate_evaluation_report_v2,
)
from skill.scripts.readme_showcase.evaluation.behavior import evaluate_behavior
from tests import test_evaluation


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"


class BehaviorEvaluationTests(unittest.TestCase):
    def fixture(self) -> dict[str, object]:
        return json.loads((FIXTURES / "command-observation-v1.valid.json").read_text())

    def test_schema_is_honestly_layered_with_python_binding_supplement(self) -> None:
        schema = json.loads((ROOT / "skill/schemas/command-observation.v1.schema.json").read_text())
        Draft202012Validator.check_schema(schema)
        self.assertIn("Python validation supplements", schema["$comment"])
        validator = Draft202012Validator(schema)
        valid = self.fixture()
        invalid = json.loads((FIXTURES / "command-observation-v1.invalid.json").read_text())
        self.assertEqual(list(validator.iter_errors(valid)), [])
        self.assertTrue(list(validator.iter_errors(invalid)))
        self.assertEqual(validate_command_observation(valid), valid)
        with self.assertRaises(ContractError):
            validate_command_observation(invalid)

        report_schema = json.loads((ROOT / "skill/schemas/evaluation-report.v2.schema.json").read_text())
        Draft202012Validator.check_schema(report_schema)
        self.assertIn("Python validation supplements", report_schema["$comment"])
        report_validator = Draft202012Validator(report_schema)
        valid_report = json.loads((FIXTURES / "evaluation-report-v2.valid.json").read_text())
        invalid_report = json.loads((FIXTURES / "evaluation-report-v2.invalid.json").read_text())
        self.assertEqual(list(report_validator.iter_errors(valid_report)), [])
        self.assertTrue(list(report_validator.iter_errors(invalid_report)))
        self.assertEqual(validate_evaluation_report_v2(valid_report), valid_report)
        with self.assertRaises(ContractError):
            validate_evaluation_report_v2(invalid_report)

    def test_payload_verified_is_untrusted_without_exact_out_of_band_receipt(self) -> None:
        observation = self.fixture()
        arguments = {
            "commands": [observation["command"]],
            "observations": [observation],
            "base_sha": observation["observed_at_base_sha"],
            "input_hashes": observation["input_hashes"],
        }
        untrusted = evaluate_behavior(**arguments)
        self.assertEqual(untrusted["status"], "unverified")
        self.assertEqual(untrusted["observable_commands"], 0)
        self.assertEqual(untrusted["commands"][0]["verification"], "imported-unverified")

        wrong_receipt = evaluate_behavior(**arguments, trusted_observation_sha256s=frozenset({"f" * 64}))
        self.assertEqual(wrong_receipt["status"], "unverified")
        trusted = evaluate_behavior(
            **arguments,
            trusted_observation_sha256s=frozenset({canonical_sha256(observation)}),
        )
        self.assertEqual(trusted["status"], "pass")
        self.assertEqual(trusted["observable_commands"], 1)

        human = copy.deepcopy(observation)
        human["runner"] = "human-import"
        human["verification"] = "imported-unverified"
        human_trusted = evaluate_behavior(
            commands=[human["command"]], observations=[human],
            base_sha=human["observed_at_base_sha"], input_hashes=human["input_hashes"],
            trusted_observation_sha256s=frozenset({canonical_sha256(human)}),
        )
        self.assertEqual(human_trusted["status"], "unverified")

    def test_missing_and_binding_drift_never_pass(self) -> None:
        observation = self.fixture()
        missing = evaluate_behavior(
            [observation["command"]], [],
            base_sha=observation["observed_at_base_sha"], input_hashes=observation["input_hashes"],
        )
        self.assertEqual(missing["status"], "not-observed")
        mutations = {
            "base": ("observed_at_base_sha", "b" * 40),
            "command": ("command", "python -m changed"),
            "cwd": ("cwd", "subdir"),
            "input": ("input_hashes", {"plan": "b" * 64}),
        }
        for name, (field, value) in mutations.items():
            changed = copy.deepcopy(observation)
            changed[field] = value
            with self.subTest(name=name), self.assertRaises(ContractError) as raised:
                evaluate_behavior(
                    [observation["command"]], [changed],
                    base_sha=observation["observed_at_base_sha"],
                    input_hashes=observation["input_hashes"],
                )
            self.assertEqual(raised.exception.code, "E_OBSERVATION_BINDING")

    def test_original_forged_payload_and_command_mismatch_fail(self) -> None:
        observation = self.fixture()
        forged = copy.deepcopy(observation)
        forged["verification"] = "verified"
        forged["runner"] = "controlled-ci"
        result = evaluate_behavior(
            [forged["command"]], [forged], base_sha=forged["observed_at_base_sha"],
            input_hashes=forged["input_hashes"],
        )
        self.assertEqual(result["status"], "unverified")
        self.assertNotEqual(result["status"], "pass")
        with self.assertRaises(ContractError) as raised:
            evaluate_behavior(
                ["python -m expected"], [forged], base_sha=forged["observed_at_base_sha"],
                input_hashes=forged["input_hashes"],
                trusted_observation_sha256s=frozenset({canonical_sha256(forged)}),
            )
        self.assertEqual(raised.exception.code, "E_OBSERVATION_BINDING")

    def test_import_and_evaluation_never_execute_subprocess(self) -> None:
        observation = self.fixture()
        observation["command"] = "/bin/sh -c 'touch /tmp/readme-showcase-pwned'"
        with mock.patch.object(subprocess, "run", side_effect=AssertionError("executed")), \
             mock.patch.object(subprocess, "Popen", side_effect=AssertionError("executed")), \
             mock.patch.object(os, "system", side_effect=AssertionError("executed")), \
             mock.patch.object(os, "execv", side_effect=AssertionError("executed")), \
             mock.patch.object(os, "execve", side_effect=AssertionError("executed")):
            result = evaluate_behavior(
                [observation["command"]], [observation],
                base_sha=observation["observed_at_base_sha"], input_hashes=observation["input_hashes"],
            )
        self.assertEqual(result["status"], "unverified")

    def test_reader_rejects_symlink_fifo_and_noncanonical_json(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            canonical = root / "observation.json"
            canonical.write_bytes((FIXTURES / "command-observation-v1.valid.json").read_bytes())
            self.assertEqual(read_command_observation(canonical)["schema_version"], 1)
            linked = root / "linked.json"
            linked.symlink_to(canonical)
            with self.assertRaises(ContractError):
                read_command_observation(linked)
            pretty = root / "pretty.json"
            pretty.write_text(json.dumps(self.fixture(), indent=2), encoding="utf-8")
            with self.assertRaises(ContractError):
                read_command_observation(pretty)

    def bundle_and_observation(self, root: Path) -> tuple[dict[str, object], dict[str, object]]:
        bundle = test_evaluation.EvaluationTests(methodName="runTest").v2_bundle(root, "high")
        artifacts = bundle["artifacts"]
        plan = json.loads((root / artifacts["plan"]["path"]).read_text())
        observation = {
            "schema_version": 1,
            "command_id": "quick-start:demo",
            "command": plan["commands"][0],
            "cwd": ".",
            "exit_code": 0,
            "stdout_sha256": "1" * 64,
            "stderr_sha256": "0" * 64,
            "observed_at_base_sha": bundle["target"]["base_sha"],
            "input_hashes": {name: reference["sha256"] for name, reference in sorted(artifacts.items())},
            "runner": "controlled-ci",
            "verification": "verified",
        }
        return bundle, observation

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "skill/scripts/readme_pipeline.py", *arguments], cwd=ROOT,
            text=True, capture_output=True, check=False,
        )

    def test_cli_import_defaults_untrusted_trusted_receipt_passes_and_drift_is_atomic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            bundle, observation = self.bundle_and_observation(root)
            bundle_path = root / "bundle.json"
            observation_path = root / "observation.json"
            output = root / "report.json"
            write_canonical_json_atomic(bundle_path, bundle)
            write_canonical_json_atomic(observation_path, observation)

            untrusted = self.cli("evaluate", "--bundle", str(bundle_path), "--observation", str(observation_path), "--output", str(output))
            self.assertEqual(untrusted.returncode, 1, untrusted.stderr)
            self.assertEqual(json.loads(untrusted.stdout)["behavior"]["status"], "unverified")
            receipt = canonical_sha256(observation)
            trusted = self.cli(
                "evaluate", "--bundle", str(bundle_path), "--observation", str(observation_path),
                "--trusted-observation-sha256", receipt, "--output", str(output),
            )
            self.assertEqual(trusted.returncode, 0, trusted.stderr)
            report = json.loads(trusted.stdout)
            self.assertEqual(report["schema_version"], 2)
            self.assertEqual(report["behavior"]["status"], "pass")
            self.assertEqual(report["advisory"]["observable_commands"]["covered"], 1)
            last_good = output.read_bytes()

            drift = copy.deepcopy(observation)
            drift["observed_at_base_sha"] = "b" * 40
            write_canonical_json_atomic(observation_path, drift)
            failed = self.cli("evaluate", "--bundle", str(bundle_path), "--observation", str(observation_path), "--output", str(output))
            self.assertEqual(failed.returncode, 2)
            self.assertIn("E_OBSERVATION_BINDING", failed.stderr)
            self.assertEqual(failed.stdout, "")
            self.assertEqual(output.read_bytes(), last_good)

            legacy = self.cli("evaluate", "--bundle", str(bundle_path), "--output", str(root / "legacy.json"))
            self.assertEqual(legacy.returncode, 0, legacy.stderr)
            self.assertEqual(json.loads(legacy.stdout)["schema_version"], 1)


if __name__ == "__main__":
    unittest.main()
