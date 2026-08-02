from __future__ import annotations

import copy
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
from pathlib import Path

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.evaluation import (
    validate_command_observation,
    validate_evaluation_report_v2,
    read_command_observation,
)
from skill.scripts.readme_showcase.evaluation.behavior import (
    CommandPolicy,
    evaluate_behavior,
    observe_command,
)
from skill.scripts.readme_showcase.evaluation.report import build_evaluation_report_v2


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests/fixtures/contracts"
ZERO = "0" * 64


def digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


class BehaviorEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.epoch = lambda: "2026-08-03T00:00:00Z"

    def target(self, root: Path) -> tuple[Path, list[dict[str, str]], dict[str, str]]:
        target = root / "target"
        target.mkdir()
        source = target / "source.txt"
        source.write_bytes(b"bound input\n")
        inputs = [{"path": "source.txt", "sha256": digest(source.read_bytes())}]
        provenance = {"path": "source.txt", "sha256": inputs[0]["sha256"]}
        return target, inputs, provenance

    def policy(self, code: str, *, timeout_ms: int = 2_000, max_output_bytes: int = 4_096) -> CommandPolicy:
        executable = str(Path(sys.executable).resolve())
        return CommandPolicy(
            command_id="quick-start:fixture",
            argv=(executable, "-I", "-c", code),
            cwd=".",
            timeout_ms=timeout_ms,
            max_output_bytes=max_output_bytes,
        )

    def test_absent_and_imported_observations_never_pass(self) -> None:
        command = self.policy("print('ok')")
        absent = evaluate_behavior(
            policies=[command], observations=[], base_sha="a" * 40,
            input_hashes=[], source_provenance=None,
        )
        self.assertEqual(absent["status"], "not-observed")
        self.assertEqual(absent["reasons"], ["observation-missing:quick-start:fixture"])

        imported = json.loads((FIXTURES / "command-observation-v1.valid.json").read_text())
        command = CommandPolicy(
            imported["command_id"], tuple(imported["argv"]), imported["cwd"],
            imported["timeout_ms"], imported["max_output_bytes"],
        )
        imported["verification"] = "imported-unverified"
        imported["runner"] = {
            "clean_environment": False, "controlled": False,
            "id": "human-import", "network": "unknown",
        }
        result = evaluate_behavior(
            policies=[command], observations=[imported],
            base_sha=imported["observed_at_base_sha"],
            input_hashes=imported["input_hashes"],
            source_provenance=imported["source_provenance"],
        )
        self.assertEqual(result["status"], "unverified")
        self.assertNotEqual(result["status"], "pass")

    def test_controlled_observation_binds_every_field_and_is_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, inputs, provenance = self.target(Path(temporary))
            policy = self.policy("print('safe fixture')")
            first = observe_command(
                policy, target_root=target, base_sha="a" * 40,
                input_hashes=inputs, source_provenance=provenance, clock=self.epoch,
            )
            second = observe_command(
                policy, target_root=target, base_sha="a" * 40,
                input_hashes=inputs, source_provenance=provenance, clock=self.epoch,
            )
            self.assertEqual(canonical_json_bytes(first), canonical_json_bytes(second))
            self.assertEqual(first["verification"], "verified")
            self.assertEqual(first["exit_code"], 0)
            self.assertEqual(first["stdout_sha256"], digest(b"safe fixture\n"))
            result = evaluate_behavior(
                policies=[policy], observations=[first], base_sha="a" * 40,
                input_hashes=inputs, source_provenance=provenance,
            )
            self.assertEqual(result["status"], "pass")
            self.assertEqual(result["observable_commands"], 1)

    def test_binding_drift_and_forged_verified_fail_closed(self) -> None:
        valid = json.loads((FIXTURES / "command-observation-v1.valid.json").read_text())
        policy = CommandPolicy(
            command_id=valid["command_id"], argv=tuple(valid["argv"]),
            cwd=valid["cwd"], timeout_ms=valid["timeout_ms"],
            max_output_bytes=valid["max_output_bytes"],
        )
        mutations = {
            "base-drift": ("observed_at_base_sha", "b" * 40),
            "command-drift": ("command", "python -c 'changed'"),
            "cwd-drift": ("cwd", "subdir"),
            "input-drift": ("input_hashes", [{"path": "source.txt", "sha256": "b" * 64}]),
            "source-drift": ("source_provenance", {"path": "source.txt", "sha256": "b" * 64}),
        }
        for reason, (field, value) in mutations.items():
            with self.subTest(reason=reason):
                changed = copy.deepcopy(valid)
                changed[field] = value
                if reason == "command-drift":
                    changed["argv"][-1] = "print('changed')"
                    changed["command"] = shlex.join(changed["argv"])
                result = evaluate_behavior(
                    policies=[policy], observations=[changed],
                    base_sha=valid["observed_at_base_sha"],
                    input_hashes=valid["input_hashes"],
                    source_provenance=valid["source_provenance"],
                )
                self.assertEqual(result["status"], "unsupported")
                self.assertIn(f"observation-{reason}:{valid['command_id']}", result["reasons"])

        forged = copy.deepcopy(valid)
        forged["runner"]["controlled"] = False
        with self.assertRaises(ContractError) as raised:
            validate_command_observation(forged)
        self.assertEqual(raised.exception.code, "E_OBSERVATION_BINDING")

    def test_cwd_executable_allowlist_timeout_output_and_environment_controls(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, inputs, provenance = self.target(root)
            for cwd in ("../escape", "/tmp"):
                with self.subTest(cwd=cwd), self.assertRaises(ContractError) as raised:
                    observe_command(
                        CommandPolicy("probe", (str(Path(sys.executable).resolve()), "-I", "-c", "pass"), cwd),
                        target_root=target, base_sha="a" * 40, input_hashes=inputs,
                        source_provenance=provenance, clock=self.epoch,
                    )
                self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")

            linked = target / "linked"
            linked.symlink_to(root, target_is_directory=True)
            with self.assertRaises(ContractError) as raised:
                observe_command(
                    CommandPolicy("probe", (str(Path(sys.executable).resolve()), "-I", "-c", "pass"), "linked"),
                    target_root=target, base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")
            linked.unlink()

            executable_link = target / "python-link"
            executable_link.symlink_to(Path(sys.executable).resolve())
            with self.assertRaises(ContractError) as raised:
                observe_command(
                    CommandPolicy("probe", (str(executable_link), "-I", "-c", "pass"), "."),
                    target_root=target, base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")
            executable_link.unlink()

            os.environ["README_SHOWCASE_SECRET_SENTINEL"] = "must-not-leak"
            try:
                clean = observe_command(
                    self.policy("import os; print(os.getenv('README_SHOWCASE_SECRET_SENTINEL', 'clean'))"),
                    target_root=target, base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            finally:
                del os.environ["README_SHOWCASE_SECRET_SENTINEL"]
            self.assertEqual(clean["stdout_sha256"], digest(b"clean\n"))

            with self.assertRaises(ContractError) as timeout:
                observe_command(
                    self.policy("import time; time.sleep(10)", timeout_ms=50),
                    target_root=target, base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(timeout.exception.code, "E_OBSERVATION_TIMEOUT")
            with self.assertRaises(ContractError) as output:
                observe_command(
                    self.policy("print('x' * 100000)", max_output_bytes=128),
                    target_root=target, base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(output.exception.code, "E_OBSERVATION_OUTPUT")

    def test_shell_metacharacters_are_data_and_obvious_network_commands_are_disallowed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            target, inputs, provenance = self.target(Path(temporary))
            marker = target / "pwned"
            policy = self.policy("import sys; print(sys.argv[1])")
            policy = CommandPolicy(
                policy.command_id, (*policy.argv, f"; touch {marker}"), policy.cwd,
                policy.timeout_ms, policy.max_output_bytes,
            )
            observed = observe_command(
                policy, target_root=target, base_sha="a" * 40,
                input_hashes=inputs, source_provenance=provenance, clock=self.epoch,
            )
            self.assertEqual(observed["exit_code"], 0)
            self.assertFalse(marker.exists())
            for argv in (("curl", "https://example.com"), ("python", "-m", "pip", "install", "x")):
                with self.subTest(argv=argv), self.assertRaises(ContractError) as raised:
                    observe_command(
                        CommandPolicy("probe", argv, "."), target_root=target,
                        base_sha="a" * 40, input_hashes=inputs,
                        source_provenance=provenance, clock=self.epoch,
                    )
                self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")
            with self.assertRaises(ContractError) as raised:
                observe_command(
                    self.policy("print('token=super-secret')"), target_root=target,
                    base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")

    def test_contract_fixtures_and_report_are_strict_and_canonical(self) -> None:
        valid_observation = json.loads((FIXTURES / "command-observation-v1.valid.json").read_text())
        invalid_observation = json.loads((FIXTURES / "command-observation-v1.invalid.json").read_text())
        self.assertEqual(validate_command_observation(valid_observation), valid_observation)
        with self.assertRaises(ContractError):
            validate_command_observation(invalid_observation)

        behavior = evaluate_behavior(
            policies=[CommandPolicy(
                valid_observation["command_id"], tuple(valid_observation["argv"]),
                valid_observation["cwd"], valid_observation["timeout_ms"],
                valid_observation["max_output_bytes"],
            )],
            observations=[valid_observation],
            base_sha=valid_observation["observed_at_base_sha"],
            input_hashes=valid_observation["input_hashes"],
            source_provenance=valid_observation["source_provenance"],
        )
        advisory = {
            name: {"covered": 0, "reasons": [], "status": "not-applicable", "total": 0}
            for name in (
                "claim_coverage", "diagram_label_coverage", "evidence_sources",
                "language_truth_pairs", "observable_commands", "section_intents",
                "visual_provenance",
            )
        }
        advisory["observable_commands"] = {
            "basis_points": 0, "covered": 0,
            "reasons": ["command-not-observed:/usr/bin/python3 -I -c fixture"],
            "status": "measured", "total": 1,
        }
        report = build_evaluation_report_v2(
            bundle_sha256=ZERO, hard_gate={"status": "pass", "findings": []},
            advisory=advisory, behavior=behavior, behavior_required=True,
        )
        self.assertEqual(validate_evaluation_report_v2(report), report)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["advisory"]["observable_commands"]["covered"], 1)
        self.assertEqual(canonical_json_bytes(report), canonical_json_bytes(copy.deepcopy(report)))

        hard_failure = build_evaluation_report_v2(
            bundle_sha256=ZERO,
            hard_gate={"status": "fail", "findings": [{"code": "E_README_AUDIT", "message": "broken"}]},
            advisory=advisory, behavior=behavior, behavior_required=False,
        )
        self.assertEqual(hard_failure["status"], "fail")
        self.assertEqual(hard_failure["hard_gate"]["findings"][0]["code"], "E_README_AUDIT")

        valid_report = json.loads((FIXTURES / "evaluation-report-v2.valid.json").read_text())
        invalid_report = json.loads((FIXTURES / "evaluation-report-v2.invalid.json").read_text())
        self.assertEqual(validate_evaluation_report_v2(valid_report), valid_report)
        with self.assertRaises(ContractError):
            validate_evaluation_report_v2(invalid_report)

    def test_draft_2020_12_schemas_execute_when_ci_validator_is_available(self) -> None:
        try:
            from jsonschema import Draft202012Validator
        except ModuleNotFoundError:
            for name in ("command-observation.v1.schema.json", "evaluation-report.v2.schema.json"):
                schema = json.loads((ROOT / "skill/schemas" / name).read_text())
                self.assertEqual(schema["$schema"], "https://json-schema.org/draft/2020-12/schema")
            return
        cases = (
            ("command-observation.v1.schema.json", "command-observation-v1.valid.json", "command-observation-v1.invalid.json"),
            ("evaluation-report.v2.schema.json", "evaluation-report-v2.valid.json", "evaluation-report-v2.invalid.json"),
        )
        for schema_name, valid_name, invalid_name in cases:
            schema = json.loads((ROOT / "skill/schemas" / schema_name).read_text())
            Draft202012Validator.check_schema(schema)
            validator = Draft202012Validator(schema)
            valid = json.loads((FIXTURES / valid_name).read_text())
            invalid = json.loads((FIXTURES / invalid_name).read_text())
            self.assertEqual(list(validator.iter_errors(valid)), [])
            self.assertTrue(list(validator.iter_errors(invalid)))

    def test_import_reader_rejects_symlink_fifo_and_noncanonical_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = FIXTURES / "command-observation-v1.valid.json"
            canonical = root / "observation.json"
            canonical.write_bytes(source.read_bytes())
            self.assertEqual(read_command_observation(canonical)["verification"], "verified")
            linked = root / "linked.json"
            linked.symlink_to(canonical)
            with self.assertRaises(ContractError) as raised:
                read_command_observation(linked)
            self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")
            noncanonical = root / "pretty.json"
            noncanonical.write_text(json.dumps(json.loads(source.read_text()), indent=2), encoding="utf-8")
            with self.assertRaises(ContractError) as raised:
                read_command_observation(noncanonical)
            self.assertEqual(raised.exception.code, "E_OBSERVATION_SCHEMA")
            if hasattr(os, "mkfifo"):
                fifo = root / "observation.fifo"
                os.mkfifo(fifo)
                with self.assertRaises(ContractError) as raised:
                    read_command_observation(fifo)
                self.assertEqual(raised.exception.code, "E_OBSERVATION_UNSAFE")

    def test_process_group_cleanup_write_denial_and_concurrent_determinism(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, inputs, provenance = self.target(root)
            marker = root / "child-leaked"
            child_code = (
                "import pathlib,time; time.sleep(.4); "
                f"pathlib.Path({str(marker)!r}).write_text('leaked')"
            )
            parent_code = (
                "import subprocess,sys,time; "
                f"subprocess.Popen([sys.executable,'-I','-c',{child_code!r}]); time.sleep(10)"
            )
            created: list[subprocess.Popen[bytes]] = []
            original_popen = subprocess.Popen
            def recording_popen(*args: object, **kwargs: object) -> subprocess.Popen[bytes]:
                process = original_popen(*args, **kwargs)  # type: ignore[arg-type]
                created.append(process)
                return process
            with mock.patch(
                "skill.scripts.readme_showcase.evaluation.behavior.subprocess.Popen",
                side_effect=recording_popen,
            ), self.assertRaises(ContractError) as raised:
                observe_command(
                    self.policy(parent_code, timeout_ms=50), target_root=target,
                    base_sha="a" * 40, input_hashes=inputs,
                    source_provenance=provenance, clock=self.epoch,
                )
            self.assertEqual(raised.exception.code, "E_OBSERVATION_TIMEOUT")
            self.assertEqual(len(created), 1)
            with self.assertRaises(ProcessLookupError):
                os.killpg(created[0].pid, 0)
            time.sleep(0.5)
            self.assertFalse(marker.exists())

            mutation = self.policy("from pathlib import Path; Path('mutated').write_text('bad')")
            observed = observe_command(
                mutation, target_root=target, base_sha="a" * 40,
                input_hashes=inputs, source_provenance=provenance, clock=self.epoch,
            )
            self.assertNotEqual(observed["exit_code"], 0)
            self.assertFalse((target / "mutated").exists())

            policy = self.policy("print('parallel')")
            outputs: list[bytes] = []
            failures: list[BaseException] = []
            def run() -> None:
                try:
                    value = observe_command(
                        policy, target_root=target, base_sha="a" * 40,
                        input_hashes=inputs, source_provenance=provenance, clock=self.epoch,
                    )
                    outputs.append(canonical_json_bytes(value))
                except BaseException as exc:
                    failures.append(exc)
            threads = [threading.Thread(target=run) for _ in range(8)]
            before_temp = set(Path(tempfile.gettempdir()).glob("readme-showcase-observation-*"))
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            after_temp = set(Path(tempfile.gettempdir()).glob("readme-showcase-observation-*"))
            self.assertEqual(failures, [])
            self.assertEqual(len(outputs), 8)
            self.assertEqual(len(set(outputs)), 1)
            self.assertEqual(after_temp, before_temp)


if __name__ == "__main__":
    unittest.main()
