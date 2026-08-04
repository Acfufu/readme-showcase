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
from skill.scripts.readme_showcase.evidence.adapters import adapt_v1_repository_evidence
from skill.scripts.pipeline_contracts import write_canonical_json_atomic
from tests import test_pipeline_contracts
from tests.test_pr_bundle import PrBundleTests


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
        return self.cli_at(REPO_ROOT, *arguments)

    def cli_at(
        self,
        cwd: Path,
        *arguments: str,
        env: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(PIPELINE), *arguments],
            cwd=cwd,
            env=env,
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

    def install_compiled_candidate(self, workspace: Path) -> tuple[dict[str, object], dict[str, bytes]]:
        plan, candidate, _, _ = test_pipeline_contracts.BundleAssembleStageTests._compiled_inputs_with_v1_evidence()
        write_canonical_json_atomic(self.root / "readme-plan-v3.json", plan)
        destination = workspace / "stages/05-candidate"
        for relative, raw in candidate.items():
            path = destination / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        return plan, candidate

    def materialize_compiled_delivery_root(self, workspace: Path) -> Path:
        """Project the immutable stage inputs/outputs into one delivery root."""
        source = self.root / "delivery"
        source.mkdir()
        files = {
            "repository-evidence.json": workspace / "stages/01-scan/attempts/1/repository-evidence.json",
            "retrieval-packet.json": workspace / "stages/02-retrieve/attempts/1/retrieval-packet.json",
            "readme-plan.json": workspace / "stages/03-plan-import/attempts/1/readme-plan.json",
            "claim-map.json": workspace / "stages/05-candidate/claim-map.json",
            "visual-spec.json": workspace / "stages/05-candidate/visual-spec.json",
            "asset-manifest.json": workspace / "stages/06-bundle-assemble/attempts/1/asset-manifest.json",
            "generated-readme-bundle.json": workspace / "stages/06-bundle-assemble/attempts/1/generated-readme-bundle.json",
            "evaluation-report.json": workspace / "stages/08-evaluation/attempts/1/evaluation-report.json",
        }
        for relative, path in files.items():
            destination = source / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if relative == "repository-evidence.json":
                evidence = json.loads(path.read_text(encoding="utf-8"))
                if evidence.get("schema_version") == 1:
                    write_canonical_json_atomic(destination, adapt_v1_repository_evidence(evidence))
                    continue
            shutil.copyfile(path, destination)
        for relative in ("README.md", "README_zh.md"):
            candidate = workspace / "stages/05-candidate" / relative
            if candidate.is_file():
                shutil.copyfile(candidate, source / relative)
        for relative in ("compiled", "assets"):
            shutil.copytree(
                workspace / "stages/06-bundle-assemble/attempts/1" / relative,
                source / relative,
            )
        shutil.copytree(workspace / "output/preview", source / "output/preview")
        return source

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

    def test_nested_attempt_output_projection_matches_append_hash(self) -> None:
        workspace = RunWorkspace(self.workspace, self.target)
        workspace.initialize(
            repository="local/repository",
            base_sha="a" * 40,
            configuration={
                "mode": "readme",
                "project_type": "developer-tool",
                "locales": ["en"],
                "scanner_profile": "balanced",
            },
            clock=lambda: "2026-08-05T00:00:00Z",
        )
        files = {
            "compiled/visual-spec.json": b'{"schema_version":1}\n',
            "compiled/scenes/en/desktop.json": b'{"scene":"desktop"}\n',
            "assets/readme-showcase/en/desktop.svg": b"<svg/>\n",
        }
        workspace.append_attempt(6, "bundle-assemble", files)
        expected = workspace.read_manifest()["stages"][5]["output_sha256"]
        self.assertEqual(workspace.attempt_output_sha256(5, 1), expected)
        self.assertEqual(workspace.attempt_output_sha256(5, 1), expected)

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

    def test_default_state_is_central_resumable_and_leaves_target_clean(self) -> None:
        codex_home = self.root / "codex-home"
        temp_root = self.root / "tmp"
        codex_home.mkdir()
        temp_root.mkdir()
        codex_home = codex_home.resolve()
        temp_root = temp_root.resolve()
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(codex_home), "TMPDIR": str(temp_root)})
        parent_before = sorted(path.name for path in self.root.iterdir())
        status_before = self.git("status", "--porcelain")

        started = self.cli_at(
            self.target,
            "run",
            "--root",
            str(self.target),
            "--mode",
            "readme",
            "--project-type",
            "developer-tool",
            "--locale",
            "en",
            "--plan",
            str(FIXTURES / "v1-plan.json"),
            "--stop-after",
            "generation-request",
            "--verbosity",
            "debug",
            env=environment,
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        started_payload = json.loads(started.stdout)
        workspace = Path(started_payload["workspace"])
        self.assertTrue(workspace.is_relative_to(codex_home / "state/readme-showcase"))
        self.assertEqual(started_payload["status"], "running")
        self.assertEqual(parent_before, sorted(path.name for path in self.root.iterdir()))
        self.assertFalse(any(path.name.startswith(".readme-showcase-run-") for path in self.root.iterdir()))
        self.assertEqual(status_before, self.git("status", "--porcelain"))

        destination = workspace / "stages/05-candidate"
        shutil.copytree(FIXTURES / "v1-candidate", destination, dirs_exist_ok=True)
        nested = self.target / "nested"
        nested.mkdir()
        completed = self.cli_at(nested, "resume", env=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["status"], "complete")
        self.assertNotIn(str(codex_home), completed.stdout)

        status = self.cli_at(nested, "status", env=environment)
        root_status = self.cli_at(self.root, "status", "--root", str(self.target), env=environment)
        explained = self.cli_at(nested, "explain", "--format", "json", env=environment)
        previewed = self.cli_at(nested, "preview", env=environment)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(root_status.returncode, 0, root_status.stderr)
        self.assertEqual(explained.returncode, 0, explained.stderr)
        self.assertEqual(previewed.returncode, 0, previewed.stderr)
        status_payload = json.loads(status.stdout)
        explained_payload = json.loads(explained.stdout)
        self.assertEqual(status_payload["run_id"], explained_payload["run_id"])
        self.assertEqual(status_payload, json.loads(root_status.stdout))
        self.assertNotIn("workspace", status_payload)
        self.assertNotIn(str(codex_home), status.stdout)
        self.assertNotIn(str(codex_home), explained.stdout)
        self.assertNotIn(str(codex_home), previewed.stdout)
        self.assertTrue((workspace / "output/preview/index.html").is_file())

        debug = self.cli_at(nested, "status", "--verbosity", "debug", env=environment)
        self.assertEqual(debug.returncode, 0, debug.stderr)
        self.assertEqual(json.loads(debug.stdout)["workspace"], str(workspace))
        self.assertEqual(status_before, self.git("status", "--porcelain"))
        self.assertFalse(any(path.name == "venv" for path in workspace.rglob("*")))
        self.assertEqual(list(temp_root.iterdir()), [])
        self.assertEqual(list((workspace / "output").glob(".preview.*.tmp")), [])
        for marker in (workspace / ".lock", workspace / ".runner.lock"):
            if not marker.exists():
                continue
            descriptor = os.open(marker, os.O_RDWR)
            try:
                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(descriptor, fcntl.LOCK_UN)
            finally:
                os.close(descriptor)

    def test_default_state_rejects_unsafe_codex_home(self) -> None:
        arguments = (
            "run", "--root", str(self.target), "--mode", "readme",
            "--project-type", "developer-tool", "--locale", "en",
        )
        for value, code in (("relative", "E_RUN_STATE_ROOT"), (str(self.target / "state-home"), "E_RUN_PATH")):
            with self.subTest(codex_home=value):
                environment = os.environ.copy()
                environment["CODEX_HOME"] = value
                result = self.cli_at(self.target, *arguments, env=environment)
                self.assertEqual(result.returncode, 2)
                self.assertIn(code, result.stderr)
                self.assertEqual(self.git("status", "--porcelain"), "")
        linked_home = self.root / "linked-codex-home"
        real_home = self.root / "real-codex-home"
        real_home.mkdir()
        linked_home.symlink_to(real_home, target_is_directory=True)
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(linked_home)
        linked = self.cli_at(self.target, *arguments, env=environment)
        self.assertEqual(linked.returncode, 2)
        self.assertIn("E_RUN_STATE_ROOT", linked.stderr)
        self.assertEqual(self.git("status", "--porcelain"), "")
        self.assertFalse((self.target / "state-home").exists())

    def test_compiled_default_state_cli_lifecycle_is_resumable_and_clean(self) -> None:
        helper = PrBundleTests(methodName="runTest")
        compiled_root = self.root / "compiled-target"
        compiled_root.mkdir()
        target, _ = helper.target(compiled_root)
        (target / "README.md").write_text("# Demo\n", encoding="utf-8")
        helper.git(target, "add", "README.md")
        helper.git(target, "commit", "-m", "compiled demo")
        (target / "nested").mkdir()

        codex_home = (self.root / "codex-home").resolve()
        temp_root = (self.root / "tmp").resolve()
        codex_home.mkdir()
        temp_root.mkdir()
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(codex_home), "TMPDIR": str(temp_root)})
        plan, _, _, _ = test_pipeline_contracts.BundleAssembleStageTests._compiled_inputs_with_v1_evidence()
        plan_path = self.root / "readme-plan-v3.json"
        write_canonical_json_atomic(plan_path, plan)

        def git_snapshot() -> dict[str, bytes]:
            return {
                "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
                "status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
                "index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
            }

        before = git_snapshot()
        started = self.cli_at(
            target,
            "run",
            "--root", str(target),
            "--mode", "readme",
            "--project-type", "developer-tool",
            "--locale", "en",
            "--plan", str(plan_path),
            "--verbosity", "debug",
            env=environment,
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        started_payload = json.loads(started.stdout)
        self.assertEqual(started_payload["status"], "waiting-for-candidate")
        workspace = Path(started_payload["workspace"])
        self.assertTrue(workspace.is_relative_to(codex_home / "state/readme-showcase"))
        self.assertNotIn(str(target), started.stderr)

        self.install_compiled_candidate(workspace)
        resumed = self.cli_at(target / "nested", "resume", env=environment)
        self.assertEqual(resumed.returncode, 0, resumed.stderr)
        resumed_payload = json.loads(resumed.stdout)
        self.assertEqual(resumed_payload["status"], "complete")
        self.assertNotIn(str(codex_home), resumed.stdout)

        manifest = json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["stages"]), 8)
        self.assertEqual(
            [stage["name"] for stage in manifest["stages"]],
            list(test_pipeline_contracts.STAGE_NAMES),
        )
        self.assertEqual([stage["attempt"] for stage in manifest["stages"]], [1, 1, 1, 1, 0, 1, 1, 1])
        self.assertTrue((workspace / "stages/06-bundle-assemble/attempts/1/compiled/inventory.json").is_file())
        self.assertTrue((workspace / "stages/06-bundle-assemble/attempts/1/assets/readme-showcase/en/desktop.svg").is_file())

        status = self.cli_at(target / "nested", "status", env=environment)
        debug_status = self.cli_at(target / "nested", "status", "--verbosity", "debug", env=environment)
        explain = self.cli_at(target / "nested", "explain", "--format", "json", env=environment)
        preview = self.cli_at(target / "nested", "preview", env=environment)
        self.assertEqual(status.returncode, 0, status.stderr)
        self.assertEqual(debug_status.returncode, 0, debug_status.stderr)
        self.assertEqual(explain.returncode, 0, explain.stderr)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertEqual(json.loads(status.stdout)["run_id"], manifest["run_id"])
        self.assertNotIn("workspace", json.loads(status.stdout))
        self.assertEqual(json.loads(debug_status.stdout)["workspace"], str(workspace))
        self.assertEqual(json.loads(explain.stdout), manifest)
        self.assertNotIn(str(codex_home), status.stdout + explain.stdout + preview.stdout)
        self.assertTrue((workspace / "output/preview/index.html").is_file())

        delivery = self.materialize_compiled_delivery_root(workspace)
        pr_bundle = delivery / "pr-bundle.json"
        built = self.cli_at(
            target,
            "build-pr-bundle",
            "--bundle", str(delivery / "generated-readme-bundle.json"),
            "--evaluation", str(delivery / "evaluation-report.json"),
            "--output", str(pr_bundle),
            env=environment,
        )
        self.assertEqual(built.returncode, 0, built.stderr)
        built_payload = json.loads(built.stdout)
        self.assertEqual(built_payload["schema_version"], 2)
        self.assertEqual(built_payload["status"], "ready")
        generated_bundle = json.loads((delivery / "generated-readme-bundle.json").read_text(encoding="utf-8"))
        self.assertEqual(generated_bundle["compiled"]["retention"], "manual")

        repeat = self.cli_at(target / "nested", "resume", env=environment)
        self.assertEqual(repeat.returncode, 0, repeat.stderr)
        self.assertEqual(json.loads(repeat.stdout), resumed_payload)
        after = git_snapshot()
        self.assertEqual(after, before)
        self.assertFalse(any(path.name == "venv" for path in workspace.rglob("*")))
        self.assertEqual(list(temp_root.iterdir()), [])

    def test_compiled_failures_preserve_last_good_and_resume_immutably(self) -> None:
        helper = PrBundleTests(methodName="runTest")
        compiled_root = self.root / "compiled-failure-target"
        compiled_root.mkdir()
        target, _ = helper.target(compiled_root)
        (target / "README.md").write_text("# Demo\n", encoding="utf-8")
        helper.git(target, "add", "README.md")
        helper.git(target, "commit", "-m", "compiled failure fixture")
        nested = target / "nested"
        nested.mkdir()

        codex_home = (self.root / "failure-codex-home").resolve()
        temp_root = (self.root / "failure-tmp").resolve()
        codex_home.mkdir()
        temp_root.mkdir()
        environment = os.environ.copy()
        environment.update({"CODEX_HOME": str(codex_home), "TMPDIR": str(temp_root)})
        plan, _, _, _ = test_pipeline_contracts.BundleAssembleStageTests._compiled_inputs_with_v1_evidence()
        plan_path = self.root / "failure-plan-v3.json"
        write_canonical_json_atomic(plan_path, plan)
        started = self.cli_at(
            target,
            "run",
            "--root", str(target),
            "--mode", "readme",
            "--project-type", "developer-tool",
            "--locale", "en",
            "--plan", str(plan_path),
            "--verbosity", "debug",
            env=environment,
        )
        self.assertEqual(started.returncode, 0, started.stderr)
        workspace = Path(json.loads(started.stdout)["workspace"])
        self.install_compiled_candidate(workspace)
        completed = self.cli_at(nested, "resume", env=environment)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        stage6 = workspace / "stages/06-bundle-assemble"
        last_good = {
            path.relative_to(stage6).as_posix(): path.read_bytes()
            for path in stage6.rglob("*")
            if path.is_file()
        }
        target_before = {
            "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
            "status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
            "index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
        }

        oversized = workspace / "stages/05-candidate/assets/oversized.bin"
        oversized.parent.mkdir(parents=True, exist_ok=True)
        oversized.write_bytes(b"x" * (16 * 1024 * 1024 + 1))
        rejected_size = self.cli_at(nested, "resume", env=environment)
        self.assertEqual(rejected_size.returncode, 2)
        self.assertIn("E_INPUT_SIZE", rejected_size.stderr)
        oversized.unlink()
        self.assertEqual(last_good, {
            path.relative_to(stage6).as_posix(): path.read_bytes()
            for path in stage6.rglob("*")
            if path.is_file()
        })

        spec_path = workspace / "stages/05-candidate/visual-spec.json"
        original_spec = spec_path.read_bytes()
        invalid_spec = json.loads(original_spec)
        invalid_spec["nodes"][0]["label"] = "x" * 121
        write_canonical_json_atomic(spec_path, invalid_spec)
        failed_compile = self.cli_at(nested, "resume", env=environment)
        self.assertEqual(failed_compile.returncode, 2)
        self.assertIn("E_VISUAL_TEXT_FIT", failed_compile.stderr)
        self.assertEqual(last_good, {
            path.relative_to(stage6).as_posix(): path.read_bytes()
            for path in stage6.rglob("*")
            if path.is_file()
        })
        failed_manifest = json.loads((workspace / "run-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(failed_manifest["status"], "failed")
        self.assertEqual(failed_manifest["stages"][5]["attempt"], 1)
        self.assertFalse((stage6 / "attempts/2").exists())
        self.assertEqual(target_before["refs"], subprocess.check_output(["git", "-C", str(target), "show-ref"]))
        self.assertEqual(target_before["status"], subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]))
        self.assertEqual(target_before["index"], subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]))

        spec_path.write_bytes(original_spec)
        recovered = self.cli_at(nested, "resume", env=environment)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["status"], "complete")
        self.assertTrue((stage6 / "attempts/2").is_dir())
        self.assertEqual(
            {key: value for key, value in last_good.items() if key.startswith("attempts/1/")},
            {
                path.relative_to(stage6).as_posix(): path.read_bytes()
                for path in (stage6 / "attempts/1").rglob("*")
                if path.is_file()
            },
        )
        self.assertEqual(json.loads((stage6 / "current.json").read_text(encoding="utf-8"))["attempt"], 2)


if __name__ == "__main__":
    unittest.main()
