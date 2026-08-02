from __future__ import annotations

import hashlib
import json
import fcntl
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill.scripts.readme_showcase.orchestration.workspace import RunWorkspace
from skill.scripts.pipeline_contracts import write_canonical_json_atomic


REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE = REPO_ROOT / "skill/scripts/readme_pipeline.py"
FIXTURES = REPO_ROOT / "tests/fixtures/run-workspaces"


class ResumablePipelineTests(unittest.TestCase):
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

    def git(self, *arguments: str) -> str:
        result = subprocess.run(
            ["git", *arguments], cwd=self.target, capture_output=True, text=True, check=True
        )
        return result.stdout.strip()

    def cli(self, *arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

    def run_arguments(self, *extra: str) -> tuple[str, ...]:
        return (
            "run", "--root", str(self.target), "--workspace", str(self.workspace),
            "--mode", "readme", "--project-type", "developer-tool", "--locale", "en",
            *extra,
        )

    def install_candidate(self) -> None:
        destination = self.workspace / "stages/05-candidate"
        shutil.copytree(FIXTURES / "v1-candidate", destination, dirs_exist_ok=True)

    def manifest(self) -> dict[str, object]:
        return json.loads((self.workspace / "run-manifest.json").read_text(encoding="utf-8"))

    def test_help_and_both_waiting_states(self) -> None:
        help_result = self.cli("--help")
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        for command in ("validate-dataset", "scan", "retrieve", "validate-bundle", "evaluate", "import-benchmark", "build-pr-bundle", "check-publish-gate", "run", "resume", "status", "explain"):
            self.assertIn(command, help_result.stdout)

        waiting_plan = self.cli(*self.run_arguments("--log-format", "json"))
        self.assertEqual(waiting_plan.returncode, 0, waiting_plan.stderr)
        self.assertEqual(json.loads(waiting_plan.stdout)["status"], "waiting-for-plan")

        shutil.copyfile(FIXTURES / "v1-plan.json", self.workspace / "inputs/readme-plan.json")
        waiting_candidate = self.cli("resume", "--workspace", str(self.workspace), "--log-format", "json")
        self.assertEqual(waiting_candidate.returncode, 0, waiting_candidate.stderr)
        self.assertEqual(json.loads(waiting_candidate.stdout)["status"], "waiting-for-candidate")
        request = self.workspace / "stages/04-generation-request/attempts/1/generation-request.json"
        self.assertTrue(request.is_file())

    def test_resume_skip_candidate_invalidation_and_explain(self) -> None:
        started = self.cli(*self.run_arguments("--plan", str(FIXTURES / "v1-plan.json"), "--stop-after", "generation-request"))
        self.assertEqual(started.returncode, 0, started.stderr)
        self.install_candidate()
        completed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(completed.returncode, 0, completed.stderr)
        before = self.manifest()
        attempt_bytes = {
            path: path.read_bytes()
            for path in self.workspace.glob("stages/*/attempts/*/*")
            if path.is_file()
        }

        unchanged = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(unchanged.returncode, 0, unchanged.stderr)
        self.assertEqual(before, self.manifest())
        self.assertTrue(all(path.read_bytes() == raw for path, raw in attempt_bytes.items()))

        readme = self.workspace / "stages/05-candidate/README.md"
        readme.write_text("# Changed\n", encoding="utf-8")
        claim_map = self.workspace / "stages/05-candidate/claim-map.json"
        claims = json.loads(claim_map.read_text(encoding="utf-8"))
        claims["markdown_blocks"][0]["content_sha256"] = hashlib.sha256(b"# Changed").hexdigest()
        claim_map.write_text(json.dumps(claims, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        changed = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(changed.returncode, 0, changed.stderr)
        after = self.manifest()
        self.assertEqual([stage["attempt"] for stage in after["stages"][:5]], [stage["attempt"] for stage in before["stages"][:5]])
        self.assertEqual([stage["attempt"] for stage in after["stages"][5:]], [stage["attempt"] + 1 for stage in before["stages"][5:]])

        status = self.cli("status", "--workspace", str(self.workspace))
        explain = self.cli("explain", "--workspace", str(self.workspace), "--format", "json")
        self.assertEqual(json.loads(status.stdout)["run_id"], after["run_id"])
        self.assertEqual(json.loads(explain.stdout), after)

    def test_json_logs_lock_and_bad_options_are_safe(self) -> None:
        run = self.cli(*self.run_arguments("--log-format", "json", "--verbosity", "debug"))
        self.assertEqual(run.returncode, 0, run.stderr)
        allowed = {"event", "run_id", "stage", "status", "duration_ms", "input_sha256", "output_sha256"}
        records = [json.loads(line) for line in run.stderr.splitlines()]
        self.assertTrue(records)
        self.assertTrue(all(set(record) == allowed for record in records))
        self.assertNotIn("target repository evidence", run.stderr)
        self.assertNotIn("SECRET-FIXTURE-SENTINEL", run.stderr)

        manifest_before = (self.workspace / "run-manifest.json").read_bytes()
        target = Path(self.manifest()["target"]["root"])
        with RunWorkspace(self.workspace, target).lock():
            locked = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(locked.returncode, 2)
        self.assertIn("E_RUN_LOCKED", locked.stderr)
        malformed = self.cli("resume", "--workspace", str(self.workspace), "--log-format", "xml")
        self.assertEqual(malformed.returncode, 2)
        self.assertEqual((self.workspace / "run-manifest.json").read_bytes(), manifest_before)

    def test_interrupted_stage_retries_without_rewriting_attempt(self) -> None:
        started = self.cli(*self.run_arguments("--plan", str(FIXTURES / "v1-plan.json"), "--stop-after", "generation-request"))
        self.assertEqual(started.returncode, 0, started.stderr)
        first = self.workspace / "stages/04-generation-request/attempts/1/generation-request.json"
        first_bytes = first.read_bytes()
        manifest = self.manifest()
        manifest["status"] = "running"
        manifest["current_stage"] = "generation-request"
        manifest["stages"][3]["status"] = "running"
        manifest["stages"][3]["completed_at"] = None
        write_canonical_json_atomic(self.workspace / "run-manifest.json", manifest)

        retried = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(retried.returncode, 0, retried.stderr)
        self.assertEqual(json.loads(retried.stdout)["status"], "waiting-for-candidate")
        self.assertEqual(first.read_bytes(), first_bytes)
        self.assertTrue((self.workspace / "stages/04-generation-request/attempts/2/generation-request.json").is_file())

    def test_runner_lock_and_symlink_candidate_fail_closed(self) -> None:
        started = self.cli(*self.run_arguments("--plan", str(FIXTURES / "v1-plan.json"), "--stop-after", "generation-request"))
        self.assertEqual(started.returncode, 0, started.stderr)
        runner_lock = os.open(self.workspace / ".runner.lock", os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(runner_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            concurrent = self.cli("resume", "--workspace", str(self.workspace))
        finally:
            os.close(runner_lock)
        self.assertEqual(concurrent.returncode, 2)
        self.assertIn("E_RUN_LOCKED", concurrent.stderr)

        self.install_candidate()
        outside = self.root / "outside-claim-map.json"
        outside.write_bytes((self.workspace / "stages/05-candidate/claim-map.json").read_bytes())
        (self.workspace / "stages/05-candidate/claim-map.json").unlink()
        (self.workspace / "stages/05-candidate/claim-map.json").symlink_to(outside)
        linked = self.cli("resume", "--workspace", str(self.workspace))
        self.assertEqual(linked.returncode, 2)
        self.assertEqual(outside.read_bytes(), (FIXTURES / "v1-candidate/claim-map.json").read_bytes())


if __name__ == "__main__":
    unittest.main()
