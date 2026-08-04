from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from skill.scripts.pipeline_contracts import write_canonical_json_atomic
from skill.scripts.readme_showcase.delivery.approval import create_approval_template
from tests.test_publish_gate import PublishGateTests


ROOT = Path(__file__).resolve().parents[2]
PIPELINE = ROOT / "skill/scripts/readme_pipeline.py"


class DeliveryDryRunTests(unittest.TestCase):
    def test_real_cli_dry_run_never_starts_gh_and_changes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, pr, _remote, _legacy_approval = PublishGateTests().fixture(root)
            (target / "dirty-main.txt").write_text("must remain dirty\n", encoding="utf-8")
            detached = root / "detached-candidate"
            subprocess.run(
                ["git", "-C", str(target), "worktree", "add", "--detach", str(detached), pr["target"]["base_sha"]],
                check=True,
                capture_output=True,
            )
            workspace = root / "run"
            preview_root = workspace / "output/preview"
            preview_root.mkdir(parents=True)
            (preview_root / "index.html").write_bytes(b"<!doctype html><title>preview</title>\n")
            write_canonical_json_atomic(
                preview_root / "report.json",
                {"schema_version": 1, "status": "complete"},
            )
            approval = create_approval_template(pr, workspace)
            approval["decision"] = "approve"
            bundle_path = workspace / "pr-bundle.json"
            approval_path = workspace / "approval-envelope-v2.json"
            write_canonical_json_atomic(bundle_path, pr)
            write_canonical_json_atomic(approval_path, approval)

            spy = root / "spy"
            spy.mkdir()
            gh_ledger = root / "gh-attempts"
            network_ledger = root / "network-attempts"
            gh_spy = spy / "gh"
            gh_spy.write_text(f"#!/bin/sh\nprintf x >> {gh_ledger}\nexit 99\n", encoding="utf-8")
            gh_spy.chmod(0o755)
            (spy / "sitecustomize.py").write_text(
                "import sys\n"
                f"ledger = {str(network_ledger)!r}\n"
                "def deny(event, args):\n"
                "    if event == 'socket.connect':\n"
                "        open(ledger, 'ab').write(b'x')\n"
                "        raise RuntimeError('network denied by dry-run test')\n"
                "sys.addaudithook(deny)\n",
                encoding="utf-8",
            )
            before = {
                "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
                "main_status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
                "main_index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
                "detached_status": subprocess.check_output(["git", "-C", str(detached), "status", "--porcelain=v1", "-z"]),
                "detached_index": subprocess.check_output(["git", "-C", str(detached), "ls-files", "-s", "-z"]),
                "worktrees": subprocess.check_output(["git", "-C", str(target), "worktree", "list", "--porcelain"]),
                "candidate": (workspace / approval["candidate_hashes"][0]["path"]).read_bytes(),
                "approval": approval_path.read_bytes(),
            }
            environment = dict(os.environ)
            environment["PATH"] = f"{spy}{os.pathsep}{environment['PATH']}"
            environment["PYTHONPATH"] = f"{spy}{os.pathsep}{environment.get('PYTHONPATH', '')}"
            result = subprocess.run(
                [
                    sys.executable,
                    str(PIPELINE),
                    "deliver",
                    "--transport", "gh",
                    "--dry-run",
                    "--bundle", str(bundle_path),
                    "--approval", str(approval_path),
                    "--workspace", str(workspace),
                ],
                cwd=detached,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["transport"], "gh")
            self.assertEqual(payload["action_order"], ["create-branch", "commit-files", "push-branch", "open-pull-request"])
            self.assertIsNone(payload["commit_sha"])
            self.assertIsNone(payload["pr_url"])
            self.assertIsNone(payload["pr_number"])
            self.assertIsNone(payload["observed_branch"])
            self.assertFalse(gh_ledger.exists(), "real gh spy was invoked")
            self.assertFalse(network_ledger.exists(), "a network connection was attempted")
            repeat = subprocess.run(
                result.args,
                cwd=detached,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(repeat.returncode, 0, repeat.stderr)
            self.assertEqual(repeat.stdout, result.stdout)
            after = {
                "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
                "main_status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
                "main_index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
                "detached_status": subprocess.check_output(["git", "-C", str(detached), "status", "--porcelain=v1", "-z"]),
                "detached_index": subprocess.check_output(["git", "-C", str(detached), "ls-files", "-s", "-z"]),
                "worktrees": subprocess.check_output(["git", "-C", str(target), "worktree", "list", "--porcelain"]),
                "candidate": (workspace / approval["candidate_hashes"][0]["path"]).read_bytes(),
                "approval": approval_path.read_bytes(),
            }
            self.assertEqual(after, before)

            live_denied = subprocess.run(
                [argument for argument in result.args if argument != "--dry-run"],
                cwd=detached,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(live_denied.returncode, 2)
            self.assertIn("E_GITHUB_LIVE_DISABLED", live_denied.stderr)
            self.assertFalse(gh_ledger.exists())
            self.assertFalse(network_ledger.exists())

            (preview_root / "index.html").write_bytes(b"drifted preview\n")
            drift_denied = subprocess.run(
                result.args,
                cwd=detached,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(drift_denied.returncode, 2)
            self.assertIn("E_GITHUB_AUTHORITY", drift_denied.stderr)
            self.assertFalse(gh_ledger.exists())
            self.assertFalse(network_ledger.exists())

    def test_compiled_dry_run_rechecks_fingerprint_without_external_writes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target, pr, workspace, approval = PublishGateTests().compiled_fixture(root)
            bundle_path = workspace / "pr-bundle.json"
            approval_path = workspace / "approval-envelope-v2.json"
            write_canonical_json_atomic(bundle_path, pr)
            write_canonical_json_atomic(approval_path, approval)

            spy = root / "spy"
            spy.mkdir()
            gh_ledger = root / "gh-attempts"
            network_ledger = root / "network-attempts"
            gh_spy = spy / "gh"
            gh_spy.write_text(f"#!/bin/sh\nprintf x >> {gh_ledger}\nexit 99\n", encoding="utf-8")
            gh_spy.chmod(0o755)
            (spy / "sitecustomize.py").write_text(
                "import sys\n"
                f"ledger = {str(network_ledger)!r}\n"
                "def deny(event, args):\n"
                "    if event == 'socket.connect':\n"
                "        open(ledger, 'ab').write(b'x')\n"
                "        raise RuntimeError('network denied by dry-run test')\n"
                "sys.addaudithook(deny)\n",
                encoding="utf-8",
            )
            before = {
                "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
                "status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
                "index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
                "worktrees": subprocess.check_output(["git", "-C", str(target), "worktree", "list", "--porcelain"]),
                "asset": (workspace / "assets/readme-showcase/en/desktop.svg").read_bytes(),
                "approval": approval_path.read_bytes(),
            }
            environment = dict(os.environ)
            environment["PATH"] = f"{spy}{os.pathsep}{environment['PATH']}"
            environment["PYTHONPATH"] = f"{spy}{os.pathsep}{environment.get('PYTHONPATH', '')}"
            command = [
                sys.executable,
                str(PIPELINE),
                "deliver",
                "--transport", "gh",
                "--dry-run",
                "--bundle", str(bundle_path),
                "--approval", str(approval_path),
                "--workspace", str(workspace),
            ]
            result = subprocess.run(command, cwd=target, env=environment, capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(result.returncode, 0, result.stderr)
            payload = json.loads(result.stdout)
            self.assertEqual(payload["status"], "dry-run")
            self.assertEqual(payload["transport"], "gh")
            self.assertEqual(payload["action_order"], ["create-branch", "commit-files", "push-branch", "open-pull-request"])
            self.assertFalse(gh_ledger.exists())
            self.assertFalse(network_ledger.exists())

            repeat = subprocess.run(command, cwd=target, env=environment, capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(repeat.returncode, 0, repeat.stderr)
            self.assertEqual(repeat.stdout, result.stdout)
            after = {
                "refs": subprocess.check_output(["git", "-C", str(target), "show-ref"]),
                "status": subprocess.check_output(["git", "-C", str(target), "status", "--porcelain=v1", "-z"]),
                "index": subprocess.check_output(["git", "-C", str(target), "ls-files", "-s", "-z"]),
                "worktrees": subprocess.check_output(["git", "-C", str(target), "worktree", "list", "--porcelain"]),
                "asset": (workspace / "assets/readme-showcase/en/desktop.svg").read_bytes(),
                "approval": approval_path.read_bytes(),
            }
            self.assertEqual(after, before)

            live = subprocess.run(
                [argument for argument in command if argument != "--dry-run"],
                cwd=target,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(live.returncode, 2)
            self.assertIn("E_GITHUB_LIVE_DISABLED", live.stderr)
            self.assertFalse(gh_ledger.exists())
            self.assertFalse(network_ledger.exists())

            asset = workspace / "assets/readme-showcase/en/desktop.svg"
            asset.write_bytes(asset.read_bytes() + b"drift")
            drift = subprocess.run(command, cwd=target, env=environment, capture_output=True, text=True, check=False, timeout=30)
            self.assertEqual(drift.returncode, 2)
            self.assertIn("E_GITHUB_AUTHORITY", drift.stderr)
            self.assertFalse(gh_ledger.exists())
            self.assertFalse(network_ledger.exists())


if __name__ == "__main__":
    unittest.main()
