#!/usr/bin/env python3
"""
verify_animation_matrix.py — 双引擎 SVG 动画存活矩阵断言 (D3b 验证管线)

断言 (基于 2026-08-12 实测修正版, GitHub 真实渲染管线, Chrome 151 / Firefox 153,
      WebDriver 等 img ready 后 4 连拍 500ms; 早期 CLI 截图结论已作废 — 见 d1e/CONCLUSION.md):
    Chrome:  SMIL <animate> 播放, CSS @keyframes 播放, hybrid 双技术播放
    Firefox: SMIL <animate> 播放, CSS @keyframes 播放, hybrid 双技术播放
    Both:    static 对照恒定

依赖: Python 3.11+ + Pillow + numpy + node + 系统 Chrome + 系统 Firefox
      (node/Chrome/Firefox/geckodriver 缺失 → exit 2)
用法:
    python3 verify_animation_matrix.py [--url URL] [--chrome PATH] [--firefox PATH]
                                        [--geckodriver PATH] [--shots N] [--keep-shots DIR]
                                        [--json] [--dry-run]
    --dry-run: 只检测依赖 (node/Chrome/Firefox/geckodriver), 不启动浏览器
退出码: 0 = 矩阵符合预期 (或 dry-run 依赖齐全) · 1 = 断言失败 · 2 = 环境/依赖错误

测试仓库: https://github.com/Acfufu/readme-svg-anim-test-20260812
    (4 个 SVG: pure-smil / pure-css / hybrid / static, README 相对 <img> 引用)
"""

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

from PIL import Image

try:
    import numpy as np
except ImportError:
    print("FATAL: numpy required (pip install numpy)", file=sys.stderr)
    sys.exit(2)

# ---------------------------------------------------------------------------
# 常量 (来自 2026-08-12 实测标定)
# ---------------------------------------------------------------------------
BRAND = {
    "red":    (211, 51, 51),    # SMIL 红条
    "blue":   (0, 136, 238),    # CSS 蓝条
    "orange": (238, 136, 0),    # hybrid 橙条 (CSS 驱动)
    "green":  (0, 170, 0),      # static 绿块 (恒在, 定位锚点)
}
TOL = 60
IMG_INTERVAL = 222          # img 顶部间距 (400x200 img + 22 gap)
IMG_X_OFFSET = 10           # 色块在 img 内的 x 偏移 (方块 x=10)
BAR_REL = (10, 30, 110, 150)   # 色块在 img 内的相对区域 (x0,y0,x1,y1)
TEXT_REL = (130, 76, 260, 111) # "SMIL"/"HYBRID" 文本在 img 内的相对区域
MIN_BLOCK_PX = 3000         # 色块最小像素 (100x120 = 12000)
MIN_BLOCK_ROWS = 30         # 色块最小行数

DEFAULT_URL = "https://github.com/Acfufu/readme-svg-anim-test-20260812"
CHROME_DEFAULT = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
FIREFOX_DEFAULT = "/Applications/Firefox.app/Contents/MacOS/firefox"
GECKODRIVER_DEFAULT = "/opt/homebrew/bin/geckodriver"

# ---------------------------------------------------------------------------
# 依赖检测: node/Chrome/Firefox/geckodriver 缺失 → exit 2 (--dry-run 只走这里)
# ---------------------------------------------------------------------------

def check_deps(args):
    """检测运行依赖 (node/Chrome/Firefox/geckodriver), 返回缺失列表 (空 = 齐全)."""
    missing = []
    if not shutil.which("node"):
        missing.append("node not found in PATH (required for browser capture)")
    for name, path in (("Chrome", args.chrome), ("Firefox", args.firefox),
                       ("geckodriver", args.geckodriver)):
        if not os.path.exists(path):
            missing.append(f"{name} not found at {path}")
    return missing


# ---------------------------------------------------------------------------
# 浏览器捕获驱动: Node WebDriver 子脚本 (CDP / geckodriver), 等 img ready 后 4 连拍 500ms
# ---------------------------------------------------------------------------

def collect_frames(url, shots_dir, browser, workdir):
    """产出有效帧列表. 双浏览器均走 Node WebDriver 子脚本 (CDP / geckodriver), 等 img ready 后 4 连拍 500ms."""
    import glob
    node = shutil.which("node")
    if not node:
        print("FATAL: node required for browser capture", file=sys.stderr)
        sys.exit(2)
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), f"capture_{browser}.mjs")
    extra = ["--port", "9235", "--chrome", args.chrome] if browser == "chrome" \
        else ["--port", "4444", "--geckodriver", args.geckodriver]
    print(f"  [{browser}] node {os.path.basename(script)} ...")
    try:
        subprocess.run(
            [node, script, "--url", url, "--out", os.path.abspath(shots_dir), *extra],
            check=True, capture_output=True, text=True, timeout=180,
        )
    except subprocess.CalledProcessError as e:
        print(f"FATAL: {browser} capture failed:\n{e.stderr}", file=sys.stderr)
        sys.exit(2)
    frames = sorted(glob.glob(os.path.join(shots_dir, f"{browser}_t*.png")))
    if len(frames) < 4:
        print(f"FATAL: {browser}: expected 4 frames, got {len(frames)}", file=sys.stderr)
        sys.exit(2)
    return frames


# ---------------------------------------------------------------------------
# 定位
# ---------------------------------------------------------------------------

def find_block(a, rgb, tol=TOL):
    """找品牌色最大连续块, 返回 ([y_band], x_range) 或 (None, None)."""
    mask = (abs(a[:, :, 0].astype(int) - rgb[0]) < tol) & \
           (abs(a[:, :, 1].astype(int) - rgb[1]) < tol) & \
           (abs(a[:, :, 2].astype(int) - rgb[2]) < tol)
    ys, xs = np.where(mask)
    if len(ys) < MIN_BLOCK_PX:
        return None, None
    bands = []
    for y in sorted(set(ys.tolist())):
        if bands and y - bands[-1][-1] <= 3:
            bands[-1].append(y)
        else:
            bands.append([y])
    big = [b for b in bands if len(b) >= MIN_BLOCK_ROWS]
    if not big:
        return None, None
    biggest = max(big, key=len)
    rows_mask = mask[biggest[0]:biggest[1] + 1].any(axis=0)
    xr = np.where(rows_mask)[0]
    return [(biggest[0], biggest[1])], (int(xr.min()), int(xr.max()))


def locate_regions(frames):
    """由 static 绿块定位 4 个 img 区域, 返回 region dict 或 None."""
    for p in frames:
        a = np.array(Image.open(p).convert("RGB"))
        bands, xr = find_block(a, BRAND["green"])
        if not bands:
            continue
        green_top = bands[0][0]
        # static 方块在 img 内 y 30-150 → img 顶 = green_top - 30
        img_top_static = green_top - 30
        img_x = xr[0] - 10
        tops = {
            "smil":   img_top_static - 3 * IMG_INTERVAL,
            "css":    img_top_static - 2 * IMG_INTERVAL,
            "hybrid": img_top_static - 1 * IMG_INTERVAL,
            "static": img_top_static,
        }
        return {
            "img_x": img_x,
            "bar": {name: (img_x + BAR_REL[0], tops[name] + BAR_REL[1],
                           img_x + BAR_REL[2], tops[name] + BAR_REL[3])
                    for name in tops},
            "text": {"smil": (img_x + TEXT_REL[0], tops["smil"] + TEXT_REL[1],
                              img_x + TEXT_REL[2], tops["smil"] + TEXT_REL[3]),
                     "hybrid": (img_x + TEXT_REL[0], tops["hybrid"] + TEXT_REL[1],
                                img_x + TEXT_REL[2], tops["hybrid"] + TEXT_REL[3])},
        }
    return None


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def count_px(a, box, pred):
    seg = a[box[1]:box[3], box[0]:box[2]]
    return int(pred(seg).sum())


def dark(seg):
    return (seg[:, :, 0] < 100) & (seg[:, :, 1] < 100) & (seg[:, :, 2] < 100)


def brand_pred(rgb):
    return lambda seg: (abs(seg[:, :, 0].astype(int) - rgb[0]) < TOL) & \
                       (abs(seg[:, :, 1].astype(int) - rgb[1]) < TOL) & \
                       (abs(seg[:, :, 2].astype(int) - rgb[2]) < TOL)


def analyze(frames, regions):
    """每资产每帧计数 → varies 判定. 返回 {asset: {"series": [...], "varies": bool, "max": int}}."""
    a = np.array(Image.open(frames[0]).convert("RGB"))
    img_w = a.shape[1]
    out = {}
    checks = {
        "smil_bar":  ("bar", "smil", brand_pred(BRAND["red"])),
        "smil_text": ("text", "smil", dark),
        "css_bar":   ("bar", "css", brand_pred(BRAND["blue"])),
        "hybrid_bar":("bar", "hybrid", brand_pred(BRAND["orange"])),
        "hybrid_text":("text", "hybrid", dark),
        "static_bar":("bar", "static", brand_pred(BRAND["green"])),
    }
    for name, (kind, asset, pred) in checks.items():
        box = regions[kind][asset]
        box = (max(0, box[0]), max(0, box[1]), min(img_w, box[2]), box[3])
        series = []
        for p in frames:
            series.append(count_px(np.array(Image.open(p).convert("RGB")), box, pred))
        out[name] = {"series": series, "varies": len(set(series)) > 1, "max": max(series)}
    return out


# ---------------------------------------------------------------------------
# 断言矩阵 (实测标定: Chrome 151 / Firefox 153, 2026-08-12 修正版)
# ---------------------------------------------------------------------------

def assert_matrix(chrome, firefox):
    """双引擎一致断言: SMIL + CSS + hybrid 全动, static 恒.
    注: 早期 CLI 截图 (img 未等加载) 曾误判 FF 不播 SMIL; WebDriver 等 img ready 后实测双引擎均播."""
    failures = []
    for browser, res in (("CHROME", chrome), ("FIREFOX", firefox)):
        for name in ("smil_bar", "smil_text", "css_bar", "hybrid_bar", "hybrid_text"):
            if not res[name]["varies"]:
                failures.append(f"{browser} {name}: expected ANIMATES, got static (series={res[name]['series']})")
        if res["static_bar"]["varies"]:
            failures.append(f"{browser} static_bar: expected constant, got varies (series={res['static_bar']['series']})")
    return (not failures), failures


def render_table(chrome, firefox):
    rows = []
    for name in ("smil_bar", "smil_text", "css_bar", "hybrid_bar", "hybrid_text", "static_bar"):
        c = "ANIMATES" if chrome[name]["varies"] else "static"
        f = "ANIMATES" if firefox[name]["varies"] else "static"
        rows.append((name, c, f, f"chrome={chrome[name]['series']}", f"ff={firefox[name]['series']}"))
    w1 = max(len(r[0]) for r in rows) + 2
    w2 = max(len(r[1]) for r in rows) + 2
    w3 = max(len(r[2]) for r in rows) + 2
    head = f"{'asset':<{w1}}{'Chrome':<{w2}}{'Firefox':<{w3}}series"
    lines = [head, "-" * len(head)]
    for name, c, f, cs, fs in rows:
        lines.append(f"{name:<{w1}}{c:<{w2}}{f:<{w3}}{cs}  {fs}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------

def main():
    global args
    ap = argparse.ArgumentParser(description="双引擎 SMIL/CSS 动画存活矩阵断言")
    ap.add_argument("--url", default=DEFAULT_URL)
    ap.add_argument("--chrome", default=CHROME_DEFAULT)
    ap.add_argument("--firefox", default=FIREFOX_DEFAULT)
    ap.add_argument("--geckodriver", default=GECKODRIVER_DEFAULT)
    ap.add_argument("--keep-shots", default=None, help="保留截图目录 (默认临时)")
    ap.add_argument("--json", action="store_true", help="输出 JSON 结果")
    ap.add_argument("--dry-run", action="store_true", help="只检测依赖 (node/Chrome/Firefox/geckodriver), 不启动浏览器")
    args = ap.parse_args()

    missing = check_deps(args)
    if missing:
        print("FATAL: missing dependencies:", file=sys.stderr)
        for m in missing:
            print(f"  - {m}", file=sys.stderr)
        sys.exit(2)

    if args.dry_run:
        print("DRY-RUN: dependencies OK (node + Chrome + Firefox + geckodriver); no capture performed")
        sys.exit(0)

    workdir = tempfile.mkdtemp(prefix="anim-matrix-")
    shots_dir = args.keep_shots or os.path.join(workdir, "shots")
    os.makedirs(shots_dir, exist_ok=True)
    results = {"url": args.url, "matrix": {}}
    all_ok = True
    exit_code = 0

    for browser in ("chrome", "firefox"):
        print(f"== {browser}: capturing 4 frames ...")
        frames = collect_frames(args.url, shots_dir, browser, workdir)
        if len(frames) < 3:
            print(f"FATAL: {browser}: only {len(frames)} valid frames (<3)", file=sys.stderr)
            sys.exit(2)
        regions = locate_regions(frames)
        if not regions:
            print(f"FATAL: {browser}: could not locate assets", file=sys.stderr)
            sys.exit(2)
        results["matrix"][browser] = analyze(frames, regions)

    chrome = results["matrix"]["chrome"]
    firefox = results["matrix"]["firefox"]
    print()
    print(render_table(chrome, firefox))

    ok, failures = assert_matrix(chrome, firefox)
    print()
    if ok:
        print("MATRIX: PASS — 双引擎行为符合预期 (Chrome + Firefox: SMIL 与 CSS @keyframes 均播放, static 对照恒定)")
        exit_code = 0
    else:
        print("MATRIX: FAIL")
        for f in failures:
            print(f"  - {f}")
        exit_code = 1

    results["pass"] = ok
    results["failures"] = failures
    if args.json:
        print("\n" + json.dumps(results, indent=2))
    if not args.keep_shots:
        shutil.rmtree(workdir, ignore_errors=True)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
