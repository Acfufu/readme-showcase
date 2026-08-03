from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from skill.scripts.pipeline_contracts import canonical_json_bytes
from skill.scripts.pipeline_contracts import ContractError
from skill.scripts.readme_showcase.orchestration import runner as runner_module
from skill.scripts.readme_showcase.generation.request import (
    MAX_REVISION_ATTEMPTS,
    canonical_revision_request,
    validate_revision_request,
)


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
FIXTURES = REPO_ROOT / "tests/fixtures/run-workspaces"


class RevisionLoopTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.target = self.root / "target"
        self.workspace = self.root / "workspace"
        self.target.mkdir()
        (self.target / "README.md").write_text("target repository evidence\n", encoding="utf-8")
        self.git("init")
        self.git("config", "user.name", "Test")
        self.git("config", "user.email", "test@example.invalid")
        self.git("add", "README.md")
        self.git("commit", "-m", "fixture")
        self.plan = json.loads((FIXTURES / "v1-plan.json").read_text(encoding="utf-8"))
        self.plan["commands"] = ["python -m demo"]
        self.plan_path = self.root / "readme-plan.json"
        self.plan_path.write_bytes(canonical_json_bytes(self.plan))

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", *arguments], cwd=self.target, capture_output=True, text=True, check=True
        ).stdout.strip()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def start(self) -> None:
        result = self.cli(
            "run", "--root", str(self.target), "--workspace", str(self.workspace),
            "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
            "--plan", str(self.plan_path), "--stop-after", "generation-request",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        shutil.copytree(
            FIXTURES / "revision-candidate",
            self.workspace / "stages/05-candidate",
            dirs_exist_ok=True,
        )

    def manifest(self) -> dict[str, object]:
        return json.loads((self.workspace / "run-manifest.json").read_text(encoding="utf-8"))

    def revisions(self) -> Path:
        return self.workspace / "stages/04-generation-request/revisions"

    def mutate_candidate(self, heading: str) -> None:
        readme = self.workspace / "stages/05-candidate/README.md"
        readme.write_text(f"# {heading}\n", encoding="utf-8")
        claim_path = self.workspace / "stages/05-candidate/claim-map.json"
        claim_map = json.loads(claim_path.read_text(encoding="utf-8"))
        claim_map["markdown_blocks"][0]["content_sha256"] = hashlib.sha256(
            f"# {heading}".encode()
        ).hexdigest()
        claim_path.write_bytes(canonical_json_bytes(claim_map))

    def request(self, attempt: int) -> tuple[bytes, dict[str, object]]:
        path = self.revisions() / str(attempt) / "revision-request.json"
        raw = path.read_bytes()
        value = json.loads(raw)
        self.assertEqual(raw, canonical_revision_request(value))
        self.assertEqual(validate_revision_request(value), value)
        return raw, value

    def test_three_attempts_are_immutable_and_fourth_requires_manual_review(self) -> None:
        self.start()
        upstream = [stage["output_sha256"] for stage in self.manifest()["stages"][:5]]
        snapshots: dict[int, bytes] = {}
        for attempt, heading in enumerate(("Generated", "Revision two", "Revision three"), 1):
            if attempt > 1:
                self.mutate_candidate(heading)
            result = self.cli("resume", "--workspace", str(self.workspace), "--log-format", "json")
            self.assertEqual(result.returncode, 1, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "manual-review-required")
            raw, request = self.request(attempt)
            snapshots[attempt] = raw
            self.assertEqual(request["attempt"], attempt)
            self.assertIn("original_request_sha256", request)
            self.assertIn("before_candidate_sha256", request)
            self.assertIn("after_candidate_sha256", request)
            self.assertEqual(request["allowed_files"], ["README.md", "asset-manifest.json", "claim-map.json"])
            self.assertTrue(request["reasons"])
            self.assertNotIn("target repository evidence", raw.decode())
            self.assertTrue(all(self.request(index)[0] == snapshots[index] for index in snapshots))

        self.assertEqual(MAX_REVISION_ATTEMPTS, 3)
        self.mutate_candidate("Revision four")
        fourth = self.cli("resume", "--workspace", str(self.workspace), "--log-format", "json")
        self.assertEqual(fourth.returncode, 1, fourth.stderr)
        self.assertEqual(json.loads(fourth.stdout)["status"], "manual-review-required")
        self.assertFalse((self.revisions() / "4").exists())
        pointer = json.loads((self.revisions() / "revision-manifest.json").read_text())
        self.assertEqual(pointer, {"current": "3/revision-request.json"})
        after = [stage["output_sha256"] for stage in self.manifest()["stages"][:5]]
        self.assertEqual(after[:4], upstream[:4])

        self.mutate_candidate("python -m demo")
        repaired = self.cli("resume", "--workspace", str(self.workspace), "--log-format", "json")
        self.assertEqual(repaired.returncode, 0, repaired.stderr)
        self.assertEqual(json.loads(repaired.stdout)["status"], "complete")
        self.assertEqual(
            [stage["output_sha256"] for stage in self.manifest()["stages"][:4]],
            upstream[:4],
        )
        self.assertFalse((self.revisions() / "4").exists())
        self.assertTrue(all(self.request(index)[0] == snapshots[index] for index in snapshots))

    def test_existing_attempt_collision_is_fail_closed(self) -> None:
        self.start()
        collision = self.revisions() / "1"
        collision.mkdir(parents=True)
        sentinel = collision / "sentinel"
        sentinel.write_bytes(b"immutable\n")
        before = (self.workspace / "run-manifest.json").read_bytes()
        result = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(result.returncode, 2)
        self.assertIn("E_REVISION_EXISTS", result.stderr)
        self.assertEqual(sentinel.read_bytes(), b"immutable\n")
        self.assertFalse((self.revisions() / "revision-manifest.json").exists())
        self.assertNotEqual((self.workspace / "run-manifest.json").read_bytes(), before)

    def test_plan_drift_creates_generation_request_not_revision(self) -> None:
        self.start()
        first = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(first.returncode, 1, first.stderr)
        revision_before = self.request(1)[0]
        pointer_before = (self.revisions() / "revision-manifest.json").read_bytes()
        changed = dict(self.plan)
        changed["commands"] = ["python -m changed"]
        self.plan_path.write_bytes(canonical_json_bytes(changed))
        drift = self.cli(
            "resume", "--workspace", str(self.workspace), "--plan", str(self.plan_path)
        )
        self.assertEqual(drift.returncode, 1, drift.stderr)
        self.assertTrue(
            (self.workspace / "stages/04-generation-request/attempts/2/generation-request.json").is_file()
        )
        self.assertEqual(self.request(1)[0], revision_before)
        self.assertEqual((self.revisions() / "revision-manifest.json").read_bytes(), pointer_before)
        self.assertFalse((self.revisions() / "2").exists())

    def test_retry_race_stale_temp_and_revision_root_symlink_are_safe(self) -> None:
        self.start()
        stale = self.revisions() / ".1.0123456789abcdef.tmp"
        stale.mkdir(parents=True)
        (stale / "revision-request.json").write_bytes(b"partial\n")
        commands = [
            sys.executable, str(PIPELINE), "resume", "--workspace", str(self.workspace)
        ]
        processes = [
            subprocess.Popen(commands, cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            for _ in range(2)
        ]
        results = [process.communicate(timeout=20) + (process.returncode,) for process in processes]
        self.assertTrue(all(result[2] in {1, 2} for result in results), results)
        self.assertTrue(any(result[2] == 1 for result in results), results)
        self.assertFalse(stale.exists())
        self.request(1)
        self.assertFalse((self.revisions() / "2").exists())

        other_workspace = self.root / "linked-workspace"
        other = self.root / "outside-revisions"
        other.mkdir()
        shutil.copytree(self.workspace, other_workspace)
        linked = other_workspace / "stages/04-generation-request/revisions"
        shutil.rmtree(linked)
        linked.symlink_to(other, target_is_directory=True)
        linked_result = self.cli("resume", "--workspace", str(other_workspace))
        self.assertEqual(linked_result.returncode, 2)
        self.assertIn("E_RUN_PATH", linked_result.stderr)
        self.assertEqual(list(other.iterdir()), [])

    def test_fifo_collision_and_history_mutation_fail_without_overwrite(self) -> None:
        self.start()
        fifo = self.revisions() / "1"
        fifo.parent.mkdir(parents=True)
        os.mkfifo(fifo)
        collision = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(collision.returncode, 2)
        self.assertIn("E_REVISION_EXISTS", collision.stderr)
        self.assertTrue(fifo.exists())

        fifo.unlink()
        retry = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(retry.returncode, 1, retry.stderr)
        request_path = self.revisions() / "1/revision-request.json"
        pointer_before = (self.revisions() / "revision-manifest.json").read_bytes()
        request_path.write_bytes(request_path.read_bytes() + b" ")
        mutated_before = request_path.read_bytes()
        self.mutate_candidate("Mutation probe")
        mutated = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(mutated.returncode, 2)
        self.assertIn("E_REVISION_MUTATED", mutated.stderr)
        self.assertEqual(request_path.read_bytes(), mutated_before)
        self.assertEqual((self.revisions() / "revision-manifest.json").read_bytes(), pointer_before)
        self.assertFalse((self.revisions() / "2").exists())

    def test_revision_contract_rejects_attempt_four_and_unsafe_or_secret_paths(self) -> None:
        self.start()
        first = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(first.returncode, 1, first.stderr)
        _, request = self.request(1)
        for field, value, code in (
            ("attempt", 4, "E_SCHEMA_VALUE"),
            ("allowed_files", ["../outside"], "E_GENERATION_REQUEST_VALUE"),
            ("forbidden_paths", ["/tmp/private"], "E_GENERATION_REQUEST_VALUE"),
            ("allowed_files", ["API_TOKEN=fixture-secret"], "E_GENERATION_REQUEST_VALUE"),
        ):
            changed = json.loads(json.dumps(request))
            changed[field] = value
            with self.subTest(field=field, value=value), self.assertRaises(ContractError) as raised:
                validate_revision_request(changed)
            self.assertEqual(raised.exception.code, code)

    def test_pointer_commit_failure_removes_only_new_attempt(self) -> None:
        self.start()
        first = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(first.returncode, 1, first.stderr)
        first_raw, request = self.request(1)
        pointer_path = self.revisions() / "revision-manifest.json"
        pointer_raw = pointer_path.read_bytes()
        second = dict(request)
        second["attempt"] = 2
        second["before_candidate_sha256"] = request["after_candidate_sha256"]
        second["after_candidate_sha256"] = "f" * 64
        normalized = validate_revision_request(second)
        atomic_write = runner_module.write_canonical_json_atomic

        def fail_pointer(path: Path, value: object) -> None:
            if path == pointer_path:
                raise ContractError("E_TEST_POINTER", "injected pointer failure")
            atomic_write(path, value)

        with mock.patch.object(
            runner_module, "write_canonical_json_atomic", side_effect=fail_pointer
        ):
            with self.assertRaises(ContractError) as raised:
                runner_module._append_revision(self.revisions(), normalized, pointer_raw)
        self.assertEqual(raised.exception.code, "E_TEST_POINTER")
        self.assertEqual(self.request(1)[0], first_raw)
        self.assertEqual(pointer_path.read_bytes(), pointer_raw)
        self.assertFalse((self.revisions() / "2").exists())
        self.assertFalse(any(path.name.endswith(".tmp") for path in self.revisions().iterdir()))


if __name__ == "__main__":
    unittest.main()
