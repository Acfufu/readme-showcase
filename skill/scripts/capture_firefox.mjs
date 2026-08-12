#!/usr/bin/env node
// capture_firefox.mjs — geckodriver (标准 WebDriver): 导航 → 等 img 加载 → 4 连拍 500ms
// 用法: node capture_firefox.mjs --url URL --out DIR [--port N] [--geckodriver PATH]
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const argv = Object.fromEntries(process.argv.slice(2).map((v, i, a) => i % 2 === 0 ? [v.slice(2), a[i + 1]] : []));
const URL_TARGET = argv.url;
const OUT = argv.out;
const PORT = Number(argv.port || 4444);
const GD = argv.geckodriver || '/opt/homebrew/bin/geckodriver';

mkdirSync(OUT, { recursive: true });

const gd = spawn(GD, ['--port', String(PORT), '--log', 'fatal'], { stdio: ['ignore', 'ignore', 'pipe'] });

async function wait_up() {
  for (let i = 0; i < 100; i++) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/status`);
      if (r.ok) return;
    } catch {}
    await sleep(100);
  }
  throw new Error('geckodriver not up');
}

async function api(method, path, body) {
  const r = await fetch(`http://127.0.0.1:${PORT}${path}`, {
    method, headers: { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(`${method} ${path}: ${r.status} ${JSON.stringify(j)}`);
  return j;
}

await wait_up();
const sess = await api('POST', '/session', {
  capabilities: {
    alwaysMatch: {
      'moz:firefoxOptions': { args: ['--headless'] },
    },
  },
});
const sid = sess.value.sessionId;

await api('POST', `/session/${sid}/window/rect`, { width: 1000, height: 2600 });

await api('POST', `/session/${sid}/url`, { url: URL_TARGET });

let ok = false;
for (let i = 0; i < 60; i++) {
  const r = await api('POST', `/session/${sid}/execute/sync`, {
    script: `return Array.from(document.querySelectorAll('article img')).filter(x => x.naturalWidth > 0).length;`,
    args: [],
  });
  if (r.value >= 4) { ok = true; break; }
  await sleep(500);
}
if (!ok) { console.error('WARN: imgs not ready'); process.exit(2); }

for (const t of [0, 500, 1000, 1500]) {
  if (t > 0) await sleep(500);
  const r = await api('GET', `/session/${sid}/screenshot`);
  const b64 = String(r.value).replace(/^data:image\/png;base64,/, '');
  writeFileSync(`${OUT}/firefox_t${t}.png`, Buffer.from(b64, 'base64'));
}
gd.kill('SIGTERM');
console.log('firefox frames saved to', OUT);
