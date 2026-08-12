"""Smoke tests for the dual-engine SVG animation matrix verifier.

No real browsers are required: dependency checks are simulated by patching
module-level defaults (CHROME_DEFAULT/FIREFOX_DEFAULT/GECKODRIVER_DEFAULT)
and shutil.which, so the suite is safe in CI without Chrome/Firefox/geckodriver.
"""

from __future__ import annotations

import io
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = REPO_ROOT / "skill/scripts"

sys.path.insert(0, str(SCRIPT_DIR))
import verify_animation_matrix as vam  # noqa: E402

ANIMATED = ("smil_bar", "smil_text", "css_bar", "hybrid_bar", "hybrid_text", "smil_linear_bar")
ALL_ASSETS = (*ANIMATED, "static_bar")


def make_results(animated=ANIMATED, static=("static_bar",)):
    """构造 analyze() 形状的结果 dict; animated 资产 varies, static 资产恒定."""
    res = {}
    for name in ALL_ASSETS:
        if name in animated and name not in static:
            res[name] = {"series": [12000, 0, 12000, 0], "varies": True, "max": 12000}
        else:
            res[name] = {"series": [5000, 5000, 5000, 5000], "varies": False, "max": 5000}
    return res


class MatrixVerifierDependencyTests(unittest.TestCase):
    """依赖检测契约: node/Chrome/Firefox/geckodriver 缺失 → exit 2 + 清晰错误."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="anim-matrix-test-")
        self._chrome = os.path.join(self._tmp, "chrome")
        self._firefox = os.path.join(self._tmp, "firefox")
        self._gecko = os.path.join(self._tmp, "geckodriver")
        for p in (self._chrome, self._firefox, self._gecko):
            Path(p).touch()
        self._orig = (vam.CHROME_DEFAULT, vam.FIREFOX_DEFAULT, vam.GECKODRIVER_DEFAULT)
        vam.CHROME_DEFAULT = self._chrome
        vam.FIREFOX_DEFAULT = self._firefox
        vam.GECKODRIVER_DEFAULT = self._gecko
        self._which = mock.patch.object(vam.shutil, "which", return_value="/usr/bin/node")
        self._which.start()
        self.addCleanup(self._which.stop)

    def tearDown(self):
        vam.CHROME_DEFAULT, vam.FIREFOX_DEFAULT, vam.GECKODRIVER_DEFAULT = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def invoke(self, argv_extra, *, node=True, chrome=None, firefox=None, gecko=None):
        """运行 main() 带模拟依赖环境, 返回 (exit_code, stdout, stderr).

        node=True → 模拟存在; node=False → 模拟缺失 (which 返回 None).
        """
        if not node:
            patch = mock.patch.object(vam.shutil, "which", return_value=None)
            patch.start()
            self.addCleanup(patch.stop)
        if chrome is not None:
            vam.CHROME_DEFAULT = chrome
        if firefox is not None:
            vam.FIREFOX_DEFAULT = firefox
        if gecko is not None:
            vam.GECKODRIVER_DEFAULT = gecko
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["verify_animation_matrix.py", *argv_extra]), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                vam.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()

    def test_missing_node_exits_2(self):
        code, _out, err = self.invoke([], node=False)
        self.assertEqual(code, 2)
        self.assertIn("node", err)

    def test_missing_chrome_exits_2(self):
        code, _out, err = self.invoke([], chrome="/nonexistent/chrome")
        self.assertEqual(code, 2)
        self.assertIn("Chrome", err)
        self.assertIn("/nonexistent/chrome", err)

    def test_missing_firefox_exits_2(self):
        code, _out, err = self.invoke([], firefox="/nonexistent/firefox")
        self.assertEqual(code, 2)
        self.assertIn("Firefox", err)

    def test_missing_geckodriver_exits_2(self):
        code, _out, err = self.invoke([], gecko="/nonexistent/geckodriver")
        self.assertEqual(code, 2)
        self.assertIn("geckodriver", err)

    def test_missing_chrome_reported_before_any_capture(self):
        """缺失依赖时不触碰 tempfile/捕获路径 — 只报依赖错误."""
        code, _out, err = self.invoke([], chrome="/nonexistent/chrome")
        self.assertEqual(code, 2)
        self.assertIn("missing dependencies", err)


class MatrixVerifierDryRunTests(unittest.TestCase):
    """--dry-run: 只检依赖; 齐全 → exit 0, 缺失 → exit 2; 永不启动浏览器."""

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="anim-matrix-test-")
        self._chrome = os.path.join(self._tmp, "chrome")
        self._firefox = os.path.join(self._tmp, "firefox")
        self._gecko = os.path.join(self._tmp, "geckodriver")
        for p in (self._chrome, self._firefox, self._gecko):
            Path(p).touch()
        self._orig = (vam.CHROME_DEFAULT, vam.FIREFOX_DEFAULT, vam.GECKODRIVER_DEFAULT)
        vam.CHROME_DEFAULT = self._chrome
        vam.FIREFOX_DEFAULT = self._firefox
        vam.GECKODRIVER_DEFAULT = self._gecko
        self._which = mock.patch.object(vam.shutil, "which", return_value="/usr/bin/node")
        self._which.start()
        self._collect = mock.patch.object(vam, "collect_frames",
                                          side_effect=AssertionError("dry-run must not capture"))
        self._collect.start()
        self.addCleanup(self._which.stop)
        self.addCleanup(self._collect.stop)

    def tearDown(self):
        vam.CHROME_DEFAULT, vam.FIREFOX_DEFAULT, vam.GECKODRIVER_DEFAULT = self._orig
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_dry_run_all_deps_ok_exits_0(self):
        code, out, _err = self.invoke(["--dry-run"])
        self.assertEqual(code, 0)
        self.assertIn("DRY-RUN", out)

    def test_dry_run_still_checks_deps(self):
        """dry-run 不是无脑 0: 依赖缺失时同样 exit 2."""
        code, _out, err = self.invoke(["--dry-run"], chrome="/nonexistent/chrome")
        self.assertEqual(code, 2)
        self.assertIn("Chrome", err)

    def invoke(self, argv_extra, **deps):
        if "chrome" in deps:
            vam.CHROME_DEFAULT = deps["chrome"]
        if "firefox" in deps:
            vam.FIREFOX_DEFAULT = deps["firefox"]
        if "gecko" in deps:
            vam.GECKODRIVER_DEFAULT = deps["gecko"]
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "argv", ["verify_animation_matrix.py", *argv_extra]), \
                redirect_stdout(out), redirect_stderr(err):
            try:
                vam.main()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, out.getvalue(), err.getvalue()


class MatrixVerifierAssertionTableTests(unittest.TestCase):
    """断言表守卫: 修正版契约 = 双引擎 SMIL+CSS+hybrid 全动, static 恒定.

    早期 (作废) 表格期望 FF SMIL 静止 — 若回归, 本组测试必须失败.
    """

    def test_dual_engine_full_motion_passes(self):
        ok, failures = vam.assert_matrix(make_results(), make_results())
        self.assertTrue(ok, f"expected PASS, got: {failures}")
        self.assertEqual(failures, [])

    def test_firefox_smil_static_fails(self):
        """旧 (作废) FF SMIL 静止表必然 FAIL — 证明断言表未回退."""
        ff = make_results()
        ff["smil_bar"] = {"series": [0, 0, 0, 0], "varies": False, "max": 0}
        ok, failures = vam.assert_matrix(make_results(), ff)
        self.assertFalse(ok)
        self.assertTrue(any("FIREFOX smil_bar" in f for f in failures))

    def test_chrome_css_static_fails(self):
        chrome = make_results()
        chrome["css_bar"] = {"series": [100, 100, 100, 100], "varies": False, "max": 100}
        ok, failures = vam.assert_matrix(chrome, make_results())
        self.assertFalse(ok)
        self.assertTrue(any("CHROME css_bar" in f for f in failures))

    def test_static_bar_variation_fails(self):
        chrome = make_results()
        chrome["static_bar"] = {"series": [5000, 100, 5000, 100], "varies": True, "max": 5000}
        ok, failures = vam.assert_matrix(chrome, make_results())
        self.assertFalse(ok)
        self.assertTrue(any("static_bar" in f for f in failures))

    def test_smil_linear_bar_static_fails(self):
        """smil-linear 断言固化: 紫条 (calcMode=linear) 必须播; 静态序列 → FAIL.

        若断言表丢掉 smil_linear_bar (矩阵回归到 4 资产), 本测试必须失败.
        """
        chrome = make_results()
        chrome["smil_linear_bar"] = {"series": [5000, 5000, 5000, 5000], "varies": False, "max": 5000}
        ok, failures = vam.assert_matrix(chrome, make_results())
        self.assertFalse(ok)
        self.assertTrue(any("CHROME smil_linear_bar" in f for f in failures))

    def test_dual_engine_smil_linear_motion_passes(self):
        """双引擎 (含 smil-linear) 全动 + static 恒 → PASS."""
        ok, failures = vam.assert_matrix(make_results(), make_results())
        self.assertTrue(ok, f"expected PASS, got: {failures}")

    def test_render_table_marks_animation_state(self):
        table = vam.render_table(make_results(), make_results())
        self.assertIn("ANIMATES", table)
        self.assertIn("static_bar", table)
        self.assertIn("smil_linear_bar", table)


class MatrixVerifierLocateRegionTests(unittest.TestCase):
    """locate_regions 布局契约: 仓库 README 顺序 smil → css → hybrid → smil-linear → static.

    合成一张 5-img 布局截图 (static 绿块在底, smil-linear 紫块在其上 1 格),
    验证区域定位只依赖 green 锚点 + IMG_INTERVAL, 不依赖浏览器.
    """

    def setUp(self):
        self._tmp = tempfile.mkdtemp(prefix="anim-matrix-loc-")
        self.addCleanup(lambda: shutil.rmtree(self._tmp, ignore_errors=True))

    def _synthesize(self):
        import numpy as np
        from PIL import Image
        W, INTERVAL = 1000, vam.IMG_INTERVAL
        static_top = 4 * INTERVAL  # smil img 顶 = 0
        h = static_top + 200
        a = np.zeros((h, W, 3), dtype=np.uint8)
        # 绿块 (static, 定位锚点): img 内 x 10-110, y 30-150
        a[static_top + 30:static_top + 150, 10:110] = vam.BRAND["green"]
        # 紫块 (smil-linear, static 上方 1 格)
        a[static_top - INTERVAL + 30:static_top - INTERVAL + 150, 10:110] = vam.BRAND["purple"]
        p = os.path.join(self._tmp, "synthetic.png")
        Image.fromarray(a).save(p)
        return p

    def test_locate_regions_smil_linear_slot(self):
        regions = vam.locate_regions([self._synthesize()])
        self.assertIsNotNone(regions)
        self.assertEqual(regions["bar"]["static"], (10, 4 * vam.IMG_INTERVAL + 30,
                                                    110, 4 * vam.IMG_INTERVAL + 150))
        self.assertEqual(regions["bar"]["smil_linear"], (10, 3 * vam.IMG_INTERVAL + 30,
                                                         110, 3 * vam.IMG_INTERVAL + 150))
        self.assertEqual(regions["bar"]["smil"], (10, 30, 110, 150))
        self.assertEqual(regions["bar"]["hybrid"], (10, 2 * vam.IMG_INTERVAL + 30,
                                                    110, 2 * vam.IMG_INTERVAL + 150))
        self.assertEqual(regions["bar"]["css"], (10, vam.IMG_INTERVAL + 30,
                                                 110, vam.IMG_INTERVAL + 150))


if __name__ == "__main__":
    unittest.main()
