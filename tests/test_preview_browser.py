"""Tests for the optional human-triggered `preview --browser` dual-engine check.

The browser check is opt-in: a plain `preview` prints a hint line instead of
launching any browser, and `preview --browser` invokes the matrix verifier as a
subprocess. subprocess.run is mocked, so the suite needs no real browsers.
"""

from __future__ import annotations

import io
import sys
import unittest
from argparse import Namespace
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from subprocess import TimeoutExpired
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "skill/scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import readme_pipeline as rp  # noqa: E402

HINT = "可选: `preview --browser` 双引擎渲染检查"
VERIFY_SCRIPT = str(SCRIPT_DIR / "verify_animation_matrix.py")


class PreviewBrowserHintTests(unittest.TestCase):
    """不带 flag: 输出含提示行, 且绝不调用矩阵脚本."""

    def invoke_preview(self, browser: bool) -> tuple[str, str, dict[str, object]]:
        args = Namespace(workspace=None, root=None, browser=browser)
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(rp._RUNNER, "preview_run", return_value={"ok": True}), \
                redirect_stdout(out), redirect_stderr(err):
            result = rp._preview(args)
        return out.getvalue(), err.getvalue(), result

    def test_without_browser_prints_hint_line(self) -> None:
        _, captured, _ = self.invoke_preview(browser=False)
        self.assertIn(HINT, captured)

    def test_hint_never_prints_to_stdout(self) -> None:
        stdout, _, _ = self.invoke_preview(browser=False)
        self.assertEqual(stdout, "")

    def test_without_browser_never_invokes_matrix_script(self) -> None:
        with mock.patch.object(rp, "subprocess") as subprocess_mock:
            self.invoke_preview(browser=False)
        subprocess_mock.run.assert_not_called()

    def test_with_browser_invokes_matrix_verifier(self) -> None:
        with mock.patch.object(rp, "subprocess") as subprocess_mock:
            subprocess_mock.run.return_value = mock.Mock(returncode=0)
            _, _, result = self.invoke_preview(browser=True)
        subprocess_mock.run.assert_called_once()
        self.assertEqual(subprocess_mock.run.call_args.kwargs["timeout"], 300)
        command = subprocess_mock.run.call_args.args[0]
        self.assertEqual(command[:2], [sys.executable, VERIFY_SCRIPT])
        self.assertIn("browser_check", result)

    def test_with_browser_failure_raises_contract_error(self) -> None:
        with mock.patch.object(rp, "subprocess") as subprocess_mock:
            subprocess_mock.run.return_value = mock.Mock(returncode=2)
            with self.assertRaises(rp.ContractError) as raised:
                self.invoke_preview(browser=True)
        self.assertEqual(raised.exception.code, "E_BROWSER_CHECK")

    def test_with_browser_timeout_raises_contract_error(self) -> None:
        with mock.patch.object(rp.subprocess, "run") as run_mock:
            run_mock.side_effect = TimeoutExpired(cmd="verify_animation_matrix.py", timeout=300)
            with self.assertRaises(rp.ContractError) as raised:
                self.invoke_preview(browser=True)
        self.assertEqual(raised.exception.code, "E_BROWSER_CHECK")


class PreviewBrowserParserTests(unittest.TestCase):
    """--browser 是 store_true 可选项; 不带 flag 默认 False."""

    def test_parser_defaults_browser_false(self) -> None:
        parsed = rp.build_parser().parse_args(["preview"])
        self.assertFalse(parsed.browser)

    def test_parser_accepts_browser_flag(self) -> None:
        parsed = rp.build_parser().parse_args(["preview", "--browser"])
        self.assertTrue(parsed.browser)


if __name__ == "__main__":
    unittest.main()
