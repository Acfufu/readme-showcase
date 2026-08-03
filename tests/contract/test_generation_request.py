from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator

from skill.scripts.pipeline_contracts import ContractError, canonical_json_bytes
from skill.scripts.readme_showcase.contracts.plan import (
    canonical_readme_plan_bytes,
    normalize_generation_text,
    validate_readme_plan,
)
from skill.scripts.readme_showcase.contracts.locale import parse_locale
from skill.scripts.readme_showcase.contracts.evidence import build_fact
from skill.scripts.readme_showcase.evidence.graph import EvidenceGraph
from skill.scripts.readme_showcase.generation.request import (
    MAX_GENERATION_REQUEST_BYTES,
    build_generation_request,
    canonical_generation_request,
    validate_generation_request,
)


ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures" / "contracts"


class GenerationRequestContractTests(unittest.TestCase):
    def test_exact_seven_locales_and_v2_explicit_output_paths(self) -> None:
        allowed = ("en", "zh-Hans", "zh-Hant", "ja", "ko", "fr", "de")
        self.assertEqual(tuple(parse_locale(tag) for tag in allowed), allowed)
        for tag in ("", "EN", "zh_CN", "zh-Hans-CN", "en-US", "es", "x-private", "en-u-hc-h12"):
            with self.subTest(tag=tag):
                self.assert_code("E_LOCALE", parse_locale, tag)

        fact = build_fact(kind="file-presence", path="README.md", locator=None, semantic_key="presence", value=True, source_bytes=b"source\n")
        evidence = EvidenceGraph([fact]).to_dict()
        plan = {
            "schema_version": 2,
            "mode": "readme",
            "locales": [
                {"tag": "en", "readme_path": "docs/primary.md"},
                {"tag": "zh-Hans", "readme_path": "localized/guide.md"},
                {"tag": "ja", "readme_path": "notes/release.md"},
            ],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": "none",
            "commands": [],
            "evidence_ids": [fact["fact_id"]],
        }
        retrieval = self.retrieval()
        retrieval["query"]["evidence_sha256"] = hashlib.sha256(canonical_json_bytes(evidence)).hexdigest()
        request = build_generation_request(
            target={"repository": "owner/demo", "base_sha": "a" * 40},
            locales=["en", "zh-Hans", "ja"],
            project_classification="developer-tool",
            plan=plan,
            retrieval_packet=retrieval,
            evidence_packet=evidence,
        )
        self.assertEqual(request["locales"], ["en", "zh-Hans", "ja"])
        self.assertEqual(
            request["output_contract"]["required_files"],
            ["asset-manifest.json", "claim-map.json", "docs/primary.md", "localized/guide.md", "notes/release.md"],
        )

    def plan(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "mode": "readme",
            "languages": ["en"],
            "sections": ["overview"],
            "visual_intent": "project-structure",
            "diagram_route": "none",
            "commands": [],
            "evidence_ids": ["file:README.md"],
        }

    def evidence(self) -> dict[str, object]:
        content = "FULL-SOURCE-SENTINEL\n"
        digest = hashlib.sha256(content.encode()).hexdigest()
        return {
            "schema_version": 1,
            "status": "complete",
            "target": {"name": "demo", "base_sha": "a" * 40},
            "scan_limits": {},
            "files": [{"path": "README.md", "bytes": len(content), "lines": 1, "sha256": digest, "content": content}],
            "facts": [{"fact_id": "file:README.md", "kind": "repository-file", "path": "README.md", "evidence_sha256": digest}],
            "warnings": [],
        }

    def retrieval(self, evidence: dict[str, object] | None = None) -> dict[str, object]:
        packet = evidence or self.evidence()
        return {
            "schema_version": 1,
            "status": "available",
            "dataset": {"dataset_id": "demo", "dataset_revision": 1, "manifest_sha256": "b" * 64},
            "query": {"evidence_sha256": hashlib.sha256(canonical_json_bytes(packet)).hexdigest(), "project_type": "developer-tool", "sections": [], "tags": []},
            "records": [
                {"record_id": "lower", "score": 10, "pattern": {"summary": "s", "structure": "t", "proof": "p"}},
                {"record_id": "higher", "score": 20, "pattern": {"summary": "S", "structure": "T", "proof": "P"}},
            ],
            "reason": None,
        }

    def request(self, **changes: object) -> dict[str, object]:
        evidence = self.evidence()
        arguments: dict[str, object] = {
            "target": {"repository": "Owner/Demo", "base_sha": "a" * 40},
            "locales": ["en"],
            "project_classification": "developer-tool",
            "plan": self.plan(),
            "retrieval_packet": self.retrieval(evidence),
            "evidence_packet": evidence,
        }
        arguments.update(changes)
        return build_generation_request(**arguments)  # type: ignore[arg-type]

    def assert_code(self, code: str, function: object, *arguments: object, **keywords: object) -> None:
        with self.assertRaises(ContractError) as raised:
            function(*arguments, **keywords)  # type: ignore[operator]
        self.assertEqual(raised.exception.code, code)

    def schema(self, name: str) -> Draft202012Validator:
        self.assertEqual(importlib.metadata.version("jsonschema"), "4.26.0")
        schema = json.loads((ROOT / "skill" / "schemas" / name).read_text(encoding="utf-8"))
        Draft202012Validator.check_schema(schema)
        return Draft202012Validator(schema)

    def test_plan_and_request_are_closed_float_free_contracts(self) -> None:
        for payload, validator in ((self.plan(), validate_readme_plan), (self.request(), validate_generation_request)):
            unknown = copy.deepcopy(payload)
            unknown["unknown"] = True
            self.assert_code("E_SCHEMA_UNKNOWN_FIELD", validator, unknown)
            floating = copy.deepcopy(payload)
            floating["schema_version"] = 1.0
            self.assert_code("E_SCHEMA_FLOAT", validator, floating)

    def test_canonical_unicode_and_reordered_objects_are_byte_identical(self) -> None:
        plan = self.plan()
        plan["visual_intent"] = "Cafe\u0301"
        reordered = dict(reversed(list(plan.items())))
        self.assertEqual(canonical_readme_plan_bytes(plan), canonical_readme_plan_bytes(reordered))
        request = self.request(plan=plan)
        self.assertEqual(canonical_generation_request(request), canonical_generation_request(dict(reversed(list(request.items())))))
        self.assertIn("Caf\u00e9".encode(), canonical_generation_request(request))

    def test_request_is_bounded_prioritized_and_contains_references_not_bodies(self) -> None:
        raw = canonical_generation_request(self.request())
        self.assertLessEqual(len(raw), MAX_GENERATION_REQUEST_BYTES)
        self.assertNotIn(b"FULL-SOURCE-SENTINEL", raw)
        self.assertNotIn(b"content", raw)
        request = json.loads(raw)
        self.assertEqual([item["record_id"] for item in request["retrieval_records"]], ["higher", "lower"])
        self.assertEqual(request["evidence_index"][0]["fact_id"], "file:README.md")

    def test_retrieval_truncation_is_stable_and_keeps_highest_priority(self) -> None:
        evidence = self.evidence()
        retrieval = self.retrieval(evidence)
        retrieval["records"] = [
            {"record_id": f"record-{index:04d}", "score": index, "pattern": {"summary": "s" * 4000, "structure": "t" * 4000, "proof": "p" * 4000}}
            for index in range(120)
        ]
        reversed_packet = copy.deepcopy(retrieval)
        reversed_packet["records"].reverse()
        left = canonical_generation_request(self.request(retrieval_packet=retrieval))
        right = canonical_generation_request(self.request(retrieval_packet=reversed_packet))
        self.assertEqual(left, right)
        records = json.loads(left)["retrieval_records"]
        self.assertLess(len(records), 120)
        self.assertEqual(records[0]["score"], 119)
        self.assertGreaterEqual(records[-1]["score"], 120 - len(records))

    def test_unknown_plan_oversize_and_absolute_or_secret_values_fail_closed(self) -> None:
        unknown = self.plan()
        unknown["surprise"] = True
        self.assert_code("E_SCHEMA_UNKNOWN_FIELD", self.request, plan=unknown)
        huge = self.plan()
        huge["sections"] = [f"section-{index:06d}-" + ("x" * 400) for index in range(4000)]
        self.assert_code("E_GENERATION_REQUEST_SIZE", self.request, plan=huge)
        absolute = self.plan()
        absolute["commands"] = ["python /tmp/tool.py"]
        self.assert_code("E_GENERATION_REQUEST_VALUE", self.request, plan=absolute)
        secret = self.plan()
        secret["commands"] = ["API_TOKEN=fixture-secret"]
        self.assert_code("E_GENERATION_REQUEST_VALUE", self.request, plan=secret)

    def test_embedded_path_leakage_is_rejected_without_blocking_urls_or_json_pointers(self) -> None:
        unsafe = (
            "/tmp/private.txt",
            "see(/tmp/private.txt)",
            "see{/tmp/private.txt}",
            "path:/tmp/private.txt",
            '"API_TOKEN"="fixture-secret"',
            "'password':'fixture-secret'",
            '{"private_key":"fixture-secret"}',
            "`API_KEY`=x",
            "`api-token`=x",
            "`AcCeSs_ToKeN`=x",
            "`AUTH-TOKEN`=x",
            "`Password`=x",
            "`PRIVATE_KEY`=x",
            "`SeCrEt`=x",
            "RFC6901 pointer=/a~b/c",
            "RFC6901 pointer=/a~2b/c",
            "x=/Users/example/secret.txt",
            "python ../outside.py",
            "open(foo/../../outside.py)",
            r"run C:\Users\example\secret.txt",
            r"run C:/Users/example/secret.txt",
            r"read \\server\share\secret.txt",
            "read //server/share/secret.txt",
        )
        for value in unsafe:
            with self.subTest(value=value):
                self.assert_code("E_GENERATION_REQUEST_VALUE", normalize_generation_text, value, "fixture")
                plan = self.plan()
                plan["commands"] = [value]
                self.assert_code("E_GENERATION_REQUEST_VALUE", self.request, plan=plan)
                retrieval = self.retrieval()
                retrieval["records"][0]["pattern"]["summary"] = value  # type: ignore[index]
                self.assert_code(
                    "E_GENERATION_RETRIEVAL", self.request, retrieval_packet=retrieval
                )

        benign = (
            "see https://example.com/docs/setup?next=/quick-start",
            "source https://github.com/owner/repo/blob/main/README.md",
            "JSON Pointer=/",
            "RFC6901 pointer=/a",
            "RFC6901 pointer=/a~1b/c~0d",
            'RFC6901 pointer=""',
            "JSON Pointer=/scripts/~1test",
            "NOT_API_TOKEN=label",
            "`NOT_API_TOKEN`=label",
            "ordinary and/or prose",
            "version 1.2.3 and section A/B",
        )
        for value in benign:
            with self.subTest(value=value):
                self.assertEqual(normalize_generation_text(value, "fixture"), value)
                plan = self.plan()
                plan["commands"] = [value]
                self.request(plan=plan)
                retrieval = self.retrieval()
                retrieval["records"][0]["pattern"]["summary"] = value  # type: ignore[index]
                self.request(retrieval_packet=retrieval)
        self.assertEqual(
            normalize_generation_text("/scripts/test", "fixture", allow_json_pointer=True),
            "/scripts/test",
        )
        self.assertEqual(normalize_generation_text("/", "fixture", allow_json_pointer=True), "/")
        self.assert_code("E_SCHEMA_TYPE", normalize_generation_text, "", "fixture")
        self.assertEqual(
            normalize_generation_text("JSON Pointer: /scripts/test", "fixture", allow_json_pointer=True),
            "JSON Pointer: /scripts/test",
        )
        pointer_retrieval = self.retrieval()
        pointer_retrieval["records"][0]["pattern"]["summary"] = "JSON Pointer: /scripts/test"  # type: ignore[index]
        self.request(retrieval_packet=pointer_retrieval)

    def test_duplicate_dangling_and_stale_evidence_fail_closed(self) -> None:
        evidence = self.evidence()
        evidence["facts"] = [*evidence["facts"], copy.deepcopy(evidence["facts"][0])]  # type: ignore[index]
        self.assert_code("E_GENERATION_EVIDENCE_DUPLICATE", self.request, evidence_packet=evidence, retrieval_packet=self.retrieval(evidence))
        dangling = self.plan()
        dangling["evidence_ids"] = ["file:missing"]
        self.assert_code("E_GENERATION_EVIDENCE_DANGLING", self.request, plan=dangling)
        retrieval = self.retrieval()
        retrieval["query"]["evidence_sha256"] = "0" * 64  # type: ignore[index]
        self.assert_code("E_GENERATION_EVIDENCE_STALE", self.request, retrieval_packet=retrieval)

    def test_malformed_packet_unknown_request_and_misleading_success_fail(self) -> None:
        malformed = self.retrieval()
        malformed["records"][0]["pattern"]["proof"] = 1  # type: ignore[index]
        self.assert_code("E_GENERATION_RETRIEVAL", self.request, retrieval_packet=malformed)
        request = self.request()
        request["status"] = "success"
        self.assert_code("E_SCHEMA_UNKNOWN_FIELD", validate_generation_request, request)
        evidence = self.evidence()
        evidence["facts"][0]["evidence_sha256"] = "0" * 64  # type: ignore[index]
        self.assert_code("E_GENERATION_EVIDENCE_STALE", self.request, evidence_packet=evidence, retrieval_packet=self.retrieval(evidence))
        evidence = self.evidence()
        evidence["target"]["base_sha"] = "c" * 40  # type: ignore[index]
        self.assert_code("E_GENERATION_EVIDENCE_STALE", self.request, evidence_packet=evidence, retrieval_packet=self.retrieval(evidence))

    def test_input_mutation_and_concurrent_builds_do_not_change_bytes(self) -> None:
        evidence, plan = self.evidence(), self.plan()
        retrieval = self.retrieval(evidence)
        before = canonical_json_bytes({"evidence": evidence, "plan": plan, "retrieval": retrieval})
        outputs: list[bytes] = []

        def build() -> None:
            outputs.append(canonical_generation_request(build_generation_request(
                target={"repository": "owner/demo", "base_sha": "a" * 40}, locales=["en"],
                project_classification="developer-tool", plan=plan, retrieval_packet=retrieval,
                evidence_packet=evidence,
            )))

        threads = [threading.Thread(target=build) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(len(set(outputs)), 1)
        self.assertEqual(canonical_json_bytes({"evidence": evidence, "plan": plan, "retrieval": retrieval}), before)

    def test_schemas_execute_and_fixtures_match_python_contracts(self) -> None:
        for stem, schema_name, validator in (
            ("readme-plan-v1", "readme-plan.v1.schema.json", validate_readme_plan),
            ("generation-request-v1", "generation-request.v1.schema.json", validate_generation_request),
        ):
            valid = json.loads((FIXTURES / f"{stem}.valid.json").read_text(encoding="utf-8"))
            invalid = json.loads((FIXTURES / f"{stem}.invalid.json").read_text(encoding="utf-8"))
            schema = self.schema(schema_name)
            self.assertEqual(list(schema.iter_errors(valid)), [])
            self.assertTrue(list(schema.iter_errors(invalid["cases"][0]["payload"])))
            self.assertEqual(validator(valid), valid)
            for case in invalid["cases"]:
                with self.subTest(stem=stem, case=case["name"]):
                    self.assert_code(case["code"], validator, case["payload"])

    def test_nonregular_plan_inputs_do_not_block_or_escape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            regular = root / "plan.json"
            regular.write_bytes(canonical_readme_plan_bytes(self.plan()))
            for kind in ("symlink", "fifo"):
                candidate = root / kind
                if kind == "symlink":
                    candidate.symlink_to(regular)
                else:
                    os.mkfifo(candidate)
                from skill.scripts.pipeline_contracts import read_json_object_bytes
                self.assert_code("E_INPUT_PATH", read_json_object_bytes, candidate)

    def test_real_cli_happy_and_failure_paths_match_manifest_and_leave_no_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "target"
            target.mkdir()
            (target / "README.md").write_text("CLI-FULL-SOURCE-SENTINEL\n", encoding="utf-8")
            (target / ".env").write_text("EXCLUDED-DIRECTORY-SENTINEL=secret\n", encoding="utf-8")
            for arguments in (
                ("init",), ("config", "user.name", "Test"), ("config", "user.email", "test@example.invalid"),
                ("add", "-f", "README.md", ".env"), ("commit", "-m", "fixture"),
            ):
                subprocess.run(["git", *arguments], cwd=target, check=True, capture_output=True)

            def run(workspace: Path, plan: Path) -> subprocess.CompletedProcess[str]:
                return subprocess.run([
                    sys.executable, str(ROOT / "skill/scripts/readme_pipeline.py"), "run",
                    "--root", str(target), "--workspace", str(workspace), "--mode", "readme",
                    "--project-type", "developer-tool", "--locale", "en", "--plan", str(plan),
                    "--stop-after", "generation-request",
                ], cwd=ROOT, capture_output=True, text=True, check=False)

            happy_workspace = root / "happy"
            happy = run(happy_workspace, FIXTURES / "readme-plan-v1.valid.json")
            self.assertEqual(happy.returncode, 0, happy.stderr)
            request_path = happy_workspace / "stages/04-generation-request/attempts/1/generation-request.json"
            raw = request_path.read_bytes()
            request = validate_generation_request(json.loads(raw))
            manifest = json.loads((happy_workspace / "run-manifest.json").read_text(encoding="utf-8"))
            expected_output = hashlib.sha256(raw).hexdigest()
            self.assertEqual(
                manifest["stages"][3]["output_sha256"],
                hashlib.sha256(canonical_json_bytes([{"path": "generation-request.json", "sha256": expected_output}])).hexdigest(),
            )
            self.assertNotIn(b"CLI-FULL-SOURCE-SENTINEL", raw)
            self.assertNotIn(b"EXCLUDED-DIRECTORY-SENTINEL", raw)
            self.assertEqual(request["evidence_index"][0]["fact_id"], "file:README.md")

            invalid = self.plan()
            invalid["unknown"] = True
            invalid_path = root / "invalid.json"
            invalid_path.write_bytes(canonical_json_bytes(invalid))
            invalid_workspace = root / "invalid-workspace"
            invalid_result = run(invalid_workspace, invalid_path)
            self.assertEqual(invalid_result.returncode, 2)
            self.assertIn("E_SCHEMA_UNKNOWN_FIELD", invalid_result.stderr)
            self.assertFalse((invalid_workspace / "stages/03-plan-import/attempts").exists())

            oversize = self.plan()
            oversize["sections"] = [f"section-{index:06d}-" + ("x" * 400) for index in range(4000)]
            oversize_path = root / "oversize.json"
            oversize_path.write_bytes(canonical_json_bytes(oversize))
            oversize_workspace = root / "oversize-workspace"
            oversize_result = run(oversize_workspace, oversize_path)
            self.assertEqual(oversize_result.returncode, 2)
            self.assertIn("E_GENERATION_REQUEST_SIZE", oversize_result.stderr)
            self.assertFalse((oversize_workspace / "stages/03-plan-import/attempts").exists())


if __name__ == "__main__":
    unittest.main()
