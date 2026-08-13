#!/usr/bin/env node
// Render a README as GitHub does — with GitHub's own theme-fragment CSS
// injected — and capture a screenshot per theme. The input is an HTML
// fixture (NOT markdown: escaping would destroy <picture>/<source>/<img>).
// Usage: node capture_readme_theme.mjs <readme.html> <out-dir> <light|dark>

import { mkdir, readFile, writeFile } from "node:fs/promises";
import { basename, join } from "node:path";

// GitHub's private stylesheet rules for theme fragments (github.com and the
// mobile apps); github-markdown-css does NOT include these.
const THEME_CSS = `
.gh-light-mode-only { display: none !important; }
.gh-dark-mode-only { display: none !important; }
[data-color-mode="light"] .gh-light-mode-only { display: initial !important; }
[data-color-mode="dark"] .gh-dark-mode-only { display: initial !important; }
.markdown-body a[href$="#gh-light-mode-only"], .markdown-body img[src$="#gh-light-mode-only"] { display: none !important; }
.markdown-body a[href$="#gh-dark-mode-only"], .markdown-body img[src$="#gh-dark-mode-only"] { display: none !important; }
[data-color-mode="light"] .markdown-body a[href$="#gh-light-mode-only"],
[data-color-mode="light"] .markdown-body img[src$="#gh-light-mode-only"] { display: initial !important; }
[data-color-mode="dark"] .markdown-body a[href$="#gh-dark-mode-only"],
[data-color-mode="dark"] .markdown-body img[src$="#gh-dark-mode-only"] { display: initial !important; }
`;

async function main() {
  const [readmePath, outDir, theme] = process.argv.slice(2);
  if (!readmePath || !outDir || !["light", "dark"].includes(theme)) {
    console.error("usage: node capture_readme_theme.mjs <readme.html> <out-dir> <light|dark>");
    process.exit(2);
  }
  let chromium;
  try {
    ({ chromium } = await import("playwright"));
  } catch {
    console.error("E_PLAYWRIGHT_MISSING: playwright not installed; theme capture skipped");
    process.exit(3);
  }
  const body = await readFile(readmePath, "utf-8");
  const stem = basename(readmePath).replace(/\.html?$/, "");
  await mkdir(outDir, { recursive: true });
  const pagePath = join(outDir, `${stem}-${theme}.html`);
  const wrapped = `<!doctype html><html data-color-mode="${theme}"><head>
<meta charset="utf-8">
<style>body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;max-width:980px;margin:0 auto;padding:16px;}
.markdown-body img{max-width:100%;}${THEME_CSS}</style></head>
<body class="markdown-body"><article>${body}</article></body></html>`;
  await writeFile(pagePath, wrapped, "utf-8");

  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 980, height: 1200 } });
  await page.emulateMedia({ colorScheme: theme });
  // file:// URL: relative img/srcset URLs resolve against the HTML file's
  // directory, so local fixture images load (broken=false).
  await page.goto(`file://${pagePath}`, { waitUntil: "load" });
  // fragmentLabel is inlined (NOT referenced from Node scope): playwright
  // serializes this callback with String(fn) and re-evaluates it in the
  // browser context, where closures over outer-scope functions do not exist.
  const images = await page.$$eval("img", (imgs) =>
    imgs.map((img) => {
      const src = img.getAttribute("src") || "";
      const fragment = src.split("#")[1] ?? "";
      const label =
        fragment === "gh-light-mode-only"
          ? "light-only"
          : fragment === "gh-dark-mode-only"
            ? "dark-only"
            : "both"; // no fragment: shown in both themes
      return {
        src,
        fragment: label,
        displayed: window.getComputedStyle(img).display === "none" ? "hidden" : "visible",
        broken: img.complete && img.naturalWidth === 0,
      };
    })
  );
  const outFile = join(outDir, `${stem}-${theme}.png`);
  await page.screenshot({ path: outFile, fullPage: false });
  await browser.close();
  console.log(JSON.stringify({ ok: true, theme, images, screenshot: outFile }));
}

main().catch((error) => {
  console.error(String(error));
  process.exit(1);
});
