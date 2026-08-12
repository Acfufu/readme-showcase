"""Tests for opt-in demo recording (asciinema cast + agg GIF).

No real asciinema/agg required: dependency detection is simulated by patching
shutil.which and subprocess.run, so the suite is safe in CI without the tools.
Determinism logic is exercised with mocked render outputs.
"""

from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

from skill.scripts import record_demo as rd
from skill.scripts.pipeline_contracts import ContractError

REPO_ROOT = Path(__file__).resolve().parents[1]
MOTION_PRODUCTION = REPO_ROOT / "skill/references/motion-production.md"
SKILL_MD = REPO_ROOT / "skill/SKILL.md"

FAKE_TOOLS = rd.DemoTools(asciinema="/usr/bin/asciinema", agg="/usr/bin/agg")


def fake_cast_bytes(version: int = 2) -> bytes:
    header = json.dumps({"version": version, "width": 80, "height": 24})
    return f'{header}\n[1.0, "o", "hello\\n"]\n'.encode("utf-8")


class RecordDemoDependencyTests(unittest.TestCase):
    """依赖检测契约: asciinema/agg 缺失 → 明确错误 + opt-in 提示; 不自动安装."""

    def setUp(self) -> None:
        self._which = mock.patch.object(rd.shutil, "which", return_value=None)
        self._which.start()
        self.addCleanup(self._which.stop)

    def test_missing_asciinema_raises_with_opt_in_hint(self) -> None:
        def which(name: str) -> str | None:
            return None if name == "asciinema" else "/usr/bin/agg"

        with mock.patch.object(rd.shutil, "which", side_effect=which):
            with self.assertRaises(rd.DemoDependencyError) as raised:
                rd.require_tools()
        message = str(raised.exception)
        self.assertIn("asciinema", message)
        self.assertIn("opt-in", message.lower())
        self.assertNotIn("installing automatically", message.lower())

    def test_missing_agg_raises_with_opt_in_hint(self) -> None:
        def which(name: str) -> str | None:
            return "/usr/bin/asciinema" if name == "asciinema" else None

        with mock.patch.object(rd.shutil, "which", side_effect=which):
            with self.assertRaises(rd.DemoDependencyError) as raised:
                rd.require_tools()
        message = str(raised.exception)
        self.assertIn("agg", message)
        self.assertIn("opt-in", message.lower())

    def test_missing_both_reports_first_tool(self) -> None:
        with self.assertRaises(rd.DemoDependencyError) as raised:
            rd.require_tools()
        self.assertIn("asciinema", str(raised.exception))

    def test_require_tools_returns_paths_when_present(self) -> None:
        with mock.patch.object(
            rd.shutil,
            "which",
            side_effect=lambda name: {  # noqa: ARG005
                "asciinema": "/usr/bin/asciinema",
                "agg": "/usr/bin/agg",
            }[name],
        ):
            tools = rd.require_tools()
        self.assertEqual(tools, FAKE_TOOLS)


class RecordDemoOrchestrationTests(unittest.TestCase):
    """record_demo 编排: 脚本 → asciinema cast → agg GIF, mock subprocess."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="record-demo-test-")
        self.root = Path(self._temp.name)
        self.demo = self.root / "demo"
        self.demo.mkdir()
        self.assets = self.root / "assets" / "readme-showcase" / "en"
        self.assets.mkdir(parents=True)
        self.script = self.demo / "demo.sh"
        self.script.write_text("#!/usr/bin/env bash\necho hello\n", encoding="utf-8")
        self.out_cast = self.assets / "demo.cast"
        self.out_gif = self.assets / "demo.gif"
        self.probe: list[list[str]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _mock_subprocess(self, cast_bytes: bytes | None = None) -> None:
        """subprocess.run writes cast for asciinema and gif bytes for agg."""

        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            self.probe.append(list(command))
            if command[1] == "rec":
                Path(command[2]).write_bytes(cast_bytes or fake_cast_bytes())
            else:
                Path(command[-1]).write_bytes(b"GIF-DEMO")

        patcher = mock.patch.object(rd.subprocess, "run", side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_sh_script_records_cast_then_renders_gif(self) -> None:
        self._mock_subprocess()
        artifacts = rd.record_demo(self.script, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

        self.assertEqual(len(self.probe), 2)
        record_call, agg_call = self.probe
        self.assertEqual(Path(record_call[0]).name, "asciinema")
        self.assertEqual(record_call[2], str(self.out_cast))
        self.assertIn("--command", record_call)
        self.assertEqual(record_call[record_call.index("--command") + 1], f"bash {self.script}")
        self.assertEqual(Path(agg_call[0]).name, "agg")
        self.assertEqual(agg_call[-2], str(self.out_cast))
        self.assertEqual(agg_call[-1], str(self.out_gif))
        self.assertEqual(artifacts.cast_path, self.out_cast)
        self.assertEqual(artifacts.gif_path, self.out_gif)
        self.assertEqual(artifacts.cast_sha256, hashlib.sha256(fake_cast_bytes()).hexdigest())
        self.assertEqual(artifacts.gif_sha256, hashlib.sha256(b"GIF-DEMO").hexdigest())

    def test_cast_input_skips_recording_and_renders_only(self) -> None:
        cast = self.demo / "demo.cast"
        cast.write_bytes(fake_cast_bytes())
        self._mock_subprocess(cast_bytes=cast.read_bytes())

        artifacts = rd.record_demo(cast, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

        self.assertEqual(len(self.probe), 1)
        self.assertEqual(Path(self.probe[0][0]).name, "agg")
        self.assertEqual(self.out_cast.read_bytes(), cast.read_bytes())
        self.assertEqual(artifacts.cast_sha256, hashlib.sha256(cast.read_bytes()).hexdigest())

    def test_cast_input_rejects_non_cast_bytes(self) -> None:
        cast = self.demo / "demo.cast"
        cast.write_text("not json\n", encoding="utf-8")
        with self.assertRaises(rd.DemoScriptError):
            rd.record_demo(cast, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

    def test_approval_check_invoked_with_script_before_recording(self) -> None:
        self._mock_subprocess()
        seen: list[Path] = []

        def approval(script: Path) -> None:
            seen.append(script)

        rd.record_demo(self.script, self.out_cast, self.out_gif, approval_check=approval, tools=FAKE_TOOLS)
        self.assertEqual(seen, [self.script])

    def test_approval_check_abort_skips_execution(self) -> None:
        def approval(script: Path) -> None:  # noqa: ARG001
            raise RuntimeError("demo script not approved")

        with self.assertRaises(RuntimeError):
            rd.record_demo(self.script, self.out_cast, self.out_gif, approval_check=approval, tools=FAKE_TOOLS)
        self.assertEqual(self.probe, [])

    def test_missing_script_raises(self) -> None:
        missing = self.demo / "missing.sh"
        with self.assertRaises(rd.DemoScriptError):
            rd.record_demo(missing, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

    def test_script_outside_demo_archive_raises(self) -> None:
        outside = self.root / "outside.sh"
        outside.write_text("echo hi\n", encoding="utf-8")
        with self.assertRaises(rd.DemoScriptError) as raised:
            rd.record_demo(outside, self.out_cast, self.out_gif, tools=FAKE_TOOLS)
        self.assertIn("demo/", str(raised.exception))

    def test_script_with_unsupported_suffix_raises(self) -> None:
        weird = self.demo / "demo.py"
        weird.write_text("print('hi')\n", encoding="utf-8")
        with self.assertRaises(rd.DemoScriptError):
            rd.record_demo(weird, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

    def test_out_cast_outside_locale_assets_raises(self) -> None:
        self._mock_subprocess()
        with self.assertRaises(rd.DemoOutputError) as raised:
            rd.record_demo(self.script, self.root / "demo.cast", self.out_gif, tools=FAKE_TOOLS)
        self.assertIn("assets/readme-showcase", str(raised.exception))

    def test_out_gif_with_non_gif_suffix_raises(self) -> None:
        self._mock_subprocess()
        with self.assertRaises(rd.DemoOutputError):
            rd.record_demo(self.script, self.out_cast, self.assets / "demo.svg", tools=FAKE_TOOLS)

    def test_recording_failure_raises_execution_error(self) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(rd.subprocess, "run", side_effect=run):
            with self.assertRaises(rd.DemoExecutionError):
                rd.record_demo(self.script, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

    def test_agg_failure_raises_execution_error(self) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            if command[1] == "rec":
                Path(command[2]).write_bytes(fake_cast_bytes())
            else:
                raise subprocess.CalledProcessError(1, command)

        with mock.patch.object(rd.subprocess, "run", side_effect=run):
            with self.assertRaises(rd.DemoExecutionError):
                rd.record_demo(self.script, self.out_cast, self.out_gif, tools=FAKE_TOOLS)

    def test_recorded_cast_validation_failure_raises(self) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            if command[1] == "rec":
                Path(command[2]).write_text("not a cast\n", encoding="utf-8")
            else:
                Path(command[-1]).write_bytes(b"GIF-DEMO")

        with mock.patch.object(rd.subprocess, "run", side_effect=run):
            with self.assertRaises(rd.DemoExecutionError):
                rd.record_demo(self.script, self.out_cast, self.out_gif, tools=FAKE_TOOLS)


class RecordDemoEnvelopeGateTests(unittest.TestCase):
    """demo-envelope.v1 执行门: 分层授权 + 权限放行兼容 + 逐条审批兜底."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="record-demo-env-")
        self.root = Path(self._temp.name)
        self.demo = self.root / "demo"
        self.demo.mkdir()
        self.assets = self.root / "assets" / "readme-showcase" / "en"
        self.assets.mkdir(parents=True)
        self.script = self.demo / "demo.sh"
        self.script.write_text("#!/usr/bin/env bash\necho hello\n", encoding="utf-8")
        self.out_cast = self.assets / "demo.cast"
        self.out_gif = self.assets / "demo.gif"
        self.probe: list[list[str]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _mock_subprocess(self) -> None:
        """subprocess.run writes cast for asciinema and gif bytes for agg."""

        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            self.probe.append(list(command))
            if command[1] == "rec":
                Path(command[2]).write_bytes(fake_cast_bytes())
            else:
                Path(command[-1]).write_bytes(b"GIF-DEMO")

        patcher = mock.patch.object(rd.subprocess, "run", side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _envelope(
        self,
        granularity: str = "full",
        *,
        auto_approved: list[str] | None = None,
        sandbox_dir: str | None = None,
        sha256: str | None = None,
        path: str | None = None,
    ) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema_version": 1,
            "demo_script": {
                "path": path or "demo/demo.sh",
                "sha256": sha256 or hashlib.sha256(self.script.read_bytes()).hexdigest(),
            },
            "granularity": granularity,
        }
        if auto_approved is not None:
            payload["auto_approved"] = auto_approved
        if sandbox_dir is not None:
            payload["sandbox_dir"] = sandbox_dir
        return payload

    def test_envelope_missing_granularity_rejected_before_execution(self) -> None:
        self._mock_subprocess()
        envelope = self._envelope()
        del envelope["granularity"]
        with self.assertRaises(rd.DemoApprovalError) as raised:
            rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)
        self.assertIn("granularity", str(raised.exception))
        self.assertEqual(self.probe, [])

    def test_permission_allowed_commands_skip_envelope_but_script_archived(self) -> None:
        """客户端权限系统已放行的命令跳过信封; 录制仍进行, 脚本归档."""
        self._mock_subprocess()
        envelope = self._envelope("read-only-auto", auto_approved=["ls"])
        artifacts = rd.record_demo(
            self.script,
            self.out_cast,
            self.out_gif,
            demo_envelope=envelope,
            permitted_commands=["echo"],
            tools=FAKE_TOOLS,
        )
        self.assertEqual(len(self.probe), 2)
        self.assertEqual(Path(self.probe[0][0]).name, "asciinema")
        self.assertEqual(artifacts.cast_path, self.out_cast)
        self.assertEqual(self.out_cast.read_bytes(), fake_cast_bytes())

    def test_unapproved_command_routes_to_per_command_approval(self) -> None:
        """未批准命令 → 逐条审批 (Codex 权限兜底): 先拒后放行."""
        self._mock_subprocess()
        envelope = self._envelope("read-only-auto", auto_approved=["ls"])
        with self.assertRaises(rd.DemoApprovalError) as raised:
            rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)
        self.assertIn("echo", str(raised.exception))
        self.assertEqual(self.probe, [])
        artifacts = rd.record_demo(
            self.script,
            self.out_cast,
            self.out_gif,
            demo_envelope=envelope,
            permitted_commands=["echo"],
            tools=FAKE_TOOLS,
        )
        self.assertEqual(len(self.probe), 2)
        self.assertEqual(artifacts.cast_sha256, hashlib.sha256(fake_cast_bytes()).hexdigest())

    def test_full_tier_approves_entire_script(self) -> None:
        self._mock_subprocess()
        envelope = self._envelope("full")
        artifacts = rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)
        self.assertEqual(len(self.probe), 2)
        self.assertEqual(artifacts.gif_path, self.out_gif)

    def test_sandbox_tier_requires_approval_for_unlisted_commands(self) -> None:
        self._mock_subprocess()
        envelope = self._envelope("sandbox", sandbox_dir="demo/sandbox")
        with self.assertRaises(rd.DemoApprovalError):
            rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)
        self.assertEqual(self.probe, [])
        artifacts = rd.record_demo(
            self.script,
            self.out_cast,
            self.out_gif,
            demo_envelope=envelope,
            permitted_commands=["echo"],
            tools=FAKE_TOOLS,
        )
        self.assertEqual(len(self.probe), 2)
        self.assertEqual(artifacts.gif_path, self.out_gif)

    def test_envelope_sha256_drift_rejected(self) -> None:
        envelope = self._envelope("full", sha256="0" * 64)
        with self.assertRaises(rd.DemoApprovalError) as raised:
            rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)
        self.assertIn("sha256", str(raised.exception).lower())

    def test_envelope_path_name_mismatch_rejected(self) -> None:
        envelope = self._envelope("full", path="demo/other.sh")
        with self.assertRaises(rd.DemoApprovalError):
            rd.record_demo(self.script, self.out_cast, self.out_gif, demo_envelope=envelope, tools=FAKE_TOOLS)


class RecordDemoDeterminismTests(unittest.TestCase):
    """agg 确定性: 同一 cast 两次渲染 → SHA-256 对比."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="record-demo-det-")
        self.root = Path(self._temp.name)
        self.cast = self.root / "demo.cast"
        self.cast.write_bytes(fake_cast_bytes())
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._temp.cleanup()

    def _patch_renders(self, contents: list[bytes]) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            self.calls.append(list(command))
            Path(command[-1]).write_bytes(contents[len(self.calls) - 1])

        patcher = mock.patch.object(rd.subprocess, "run", side_effect=run)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_identical_renders_verify_deterministic(self) -> None:
        self._patch_renders([b"GIF-A", b"GIF-A"])
        proof = rd.verify_determinism(self.cast, workspace=self.root, agg_path="/usr/bin/agg")

        self.assertTrue(proof.identical)
        self.assertEqual(proof.first_sha256, hashlib.sha256(b"GIF-A").hexdigest())
        self.assertEqual(proof.second_sha256, proof.first_sha256)
        self.assertEqual(proof.cast_sha256, hashlib.sha256(fake_cast_bytes()).hexdigest())
        self.assertEqual(len(self.calls), 2)
        for call in self.calls:
            self.assertEqual(Path(call[0]).name, "agg")
            self.assertEqual(call[-2], str(self.cast))

    def test_divergent_renders_raise_determinism_error(self) -> None:
        self._patch_renders([b"GIF-A", b"GIF-B"])
        with self.assertRaises(rd.DemoDeterminismError):
            rd.verify_determinism(self.cast, workspace=self.root, agg_path="/usr/bin/agg")


class RecordDemoCliTests(unittest.TestCase):
    """CLI 契约: 依赖缺失 → exit 2 + opt-in 提示; --verify 打印确定性证明."""

    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory(prefix="record-demo-cli-")
        self.root = Path(self._temp.name)
        self.demo = self.root / "demo"
        self.demo.mkdir()
        self.assets = self.root / "assets" / "readme-showcase" / "en"
        self.assets.mkdir(parents=True)
        self.script = self.demo / "demo.sh"
        self.script.write_text("echo hello\n", encoding="utf-8")
        self.out_cast = self.assets / "demo.cast"
        self.out_gif = self.assets / "demo.gif"

    def tearDown(self) -> None:
        self._temp.cleanup()

    def test_missing_dependency_exits_2_with_hint(self) -> None:
        with mock.patch.object(rd.shutil, "which", return_value=None):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    rd.main([str(self.script), str(self.out_cast), str(self.out_gif)])
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("asciinema", stderr.getvalue())
        self.assertIn("opt-in", stderr.getvalue().lower())

    def test_verify_flag_prints_determinism_proof(self) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            if command[1] == "rec":
                Path(command[2]).write_bytes(fake_cast_bytes())
            else:
                Path(command[-1]).write_bytes(b"GIF-DEMO")

        with mock.patch.object(rd.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(rd.subprocess, "run", side_effect=run):
            stdout = io.StringIO()
            with mock.patch.object(sys, "stdout", stdout):
                rd.main([str(self.script), str(self.out_cast), str(self.out_gif), "--verify"])
        output = stdout.getvalue()
        self.assertIn("identical", output)
        self.assertIn(hashlib.sha256(b"GIF-DEMO").hexdigest(), output)

    def test_invalid_envelope_exits_2_with_hint(self) -> None:
        envelope = self.root / "envelope.json"
        envelope.write_text("{not json\n", encoding="utf-8")
        with mock.patch.object(rd.shutil, "which", return_value="/usr/bin/tool"):
            stderr = io.StringIO()
            with redirect_stderr(stderr):
                with self.assertRaises(SystemExit) as raised:
                    rd.main(
                        [
                            str(self.script),
                            str(self.out_cast),
                            str(self.out_gif),
                            "--envelope",
                            str(envelope),
                        ]
                    )
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("envelope", stderr.getvalue().lower())

    def test_full_tier_envelope_records_via_cli(self) -> None:
        def run(command: list[str], **kwargs: object) -> None:  # noqa: ARG001
            if command[1] == "rec":
                Path(command[2]).write_bytes(fake_cast_bytes())
            else:
                Path(command[-1]).write_bytes(b"GIF-DEMO")

        envelope = self.root / "envelope.json"
        envelope.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "demo_script": {
                        "path": "demo/demo.sh",
                        "sha256": hashlib.sha256(self.script.read_bytes()).hexdigest(),
                    },
                    "granularity": "full",
                }
            ),
            encoding="utf-8",
        )
        with mock.patch.object(rd.shutil, "which", return_value="/usr/bin/tool"), \
                mock.patch.object(rd.subprocess, "run", side_effect=run):
            stdout = io.StringIO()
            with mock.patch.object(sys, "stdout", stdout):
                rd.main(
                    [
                        str(self.script),
                        str(self.out_cast),
                        str(self.out_gif),
                        "--envelope",
                        str(envelope),
                    ]
                )
        output = stdout.getvalue()
        self.assertIn("CAST:", output)
        self.assertIn("GIF:", output)


class RecordDemoDocContractTests(unittest.TestCase):
    """Doc contracts: motion-production.md demo section + SKILL.md opt-in note."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.motion = MOTION_PRODUCTION.read_text(encoding="utf-8")
        cls.skill = SKILL_MD.read_text(encoding="utf-8")

    def test_motion_doc_documents_demo_recording(self) -> None:
        lowered = self.motion.lower()
        self.assertIn("demo recording", lowered)
        self.assertIn("asciinema", lowered)
        self.assertIn("agg", lowered)
        self.assertIn("determin", lowered)
        self.assertIn("record_demo", self.motion)

    def test_motion_doc_documents_demo_approval_envelope(self) -> None:
        lowered = self.motion.lower()
        self.assertIn("demo-envelope", lowered)
        self.assertIn("granularity", lowered)
        self.assertIn("full", lowered)
        self.assertIn("read-only-auto", lowered)
        self.assertIn("sandbox", lowered)

    def test_skill_doc_declares_opt_in_dependencies(self) -> None:
        lowered = self.skill.lower()
        self.assertIn("asciinema", lowered)
        self.assertIn("agg", lowered)


if __name__ == "__main__":
    unittest.main()
