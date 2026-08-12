#!/usr/bin/env node
// capture_chrome.mjs — CDP 驱动: 导航 → 等 img 加载 → 4 连拍 500ms 间隔
// 用法: node capture_chrome.mjs --url URL --out DIR [--port N] [--chrome PATH]
import { spawn } from 'node:child_process';
import { writeFileSync, mkdirSync } from 'node:fs';
import { setTimeout as sleep } from 'node:timers/promises';

const argv = Object.fromEntries(process.argv.slice(2).map((v, i, a) => i % 2 === 0 ? [v.slice(2), a[i + 1]] : []));
const URL_TARGET = argv.url;
const OUT = argv.out;
const PORT = Number(argv.port || 9235);
const CHROME = argv.chrome || '/Applications/Google Chrome.app/Contents/MacOS/Google Chrome';

mkdirSync(OUT, { recursive: true });
const prof = `${OUT}/.ch-prof-${process.pid}`;

const chrome = spawn(CHROME, [
  '--headless=new', `--remote-debugging-port=${PORT}`, `--user-data-dir=${prof}`,
  '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
  '--window-size=1000,2600', 'about:blank',
], { stdio: ['ignore', 'pipe', 'pipe'] });
let cerr = '';
chrome.stderr.on('data', d => { cerr += d; });

async function endpoint() {
  for (let i = 0; i < 150; i++) {
    try { const r = await fetch(`http://127.0.0.1:${PORT}/json/version`); if (r.ok) return r.json(); } catch {}
    await sleep(100);
  }
  throw new Error('chrome not up: ' + cerr.slice(-800));
}

class CDP {
  constructor(url) {
    this.ws = new WebSocket(url); this.id = 0; this.pending = new Map(); this.events = new Map();
    this.ws.onmessage = (ev) => {
      const m = JSON.parse(ev.data);
      if (m.id) { const p = this.pending.get(m.id); if (p) { this.pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); } }
      else { const hs = this.events.get(m.method); if (hs) hs.forEach(h => h(m.params)); }
    };
  }
  async open() {
    if (this.ws.readyState === 1) return;
    await new Promise((resolve, reject) => {
      const to = setTimeout(() => reject(new Error('ws open timeout')), 8000);
      this.ws.onopen = () => { clearTimeout(to); resolve(); };
      this.ws.onerror = () => { clearTimeout(to); reject(new Error('ws error')); };
    });
  }
  send(method, params = {}, timeoutMs = 15000) {
    const id = ++this.id;
    return new Promise((resolve, reject) => {
      const to = setTimeout(() => { this.pending.delete(id); reject(new Error(`timeout ${method}`)); }, timeoutMs);
      this.pending.set(id, { resolve: (v) => { clearTimeout(to); resolve(v); }, reject: (e) => { clearTimeout(to); reject(e); } });
      this.ws.send(JSON.stringify({ id, method, params }));
    });
  }
  on(method, h) { if (!this.events.has(method)) this.events.set(method, []); this.events.get(method).push(h); }
  close() { this.ws.close(); }
}

const info = await endpoint();
const target = await (await fetch(`http://127.0.0.1:${PORT}/json/new?${encodeURIComponent(URL_TARGET)}`, { method: 'PUT' })).json();
const cdp = new CDP(target.webSocketDebuggerUrl);
await cdp.open();
await cdp.send('Page.enable');
await cdp.send('Emulation.setDeviceMetricsOverride', { width: 1000, height: 2600, deviceScaleFactor: 1, mobile: false });
const loaded = new Promise(res => cdp.on('Page.loadEventFired', res));
await cdp.send('Page.navigate', { url: URL_TARGET });
await loaded;

let ok = false;
for (let i = 0; i < 40; i++) {
  const r = await cdp.send('Runtime.evaluate', {
    expression: `Array.from(document.querySelectorAll('article img')).filter(x => x.naturalWidth > 0).length`,
    returnByValue: true,
  });
  if (r.result.value >= 5) { ok = true; break; }
  await sleep(500);
}
if (!ok) { console.error('WARN: imgs not ready'); process.exit(2); }

for (const t of [0, 500, 1000, 1500]) {
  if (t > 0) await sleep(500);
  const shot = await cdp.send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(`${OUT}/chrome_t${t}.png`, Buffer.from(shot.data, 'base64'));
}
cdp.close();
chrome.kill('SIGTERM');
console.log('chrome frames saved to', OUT);
