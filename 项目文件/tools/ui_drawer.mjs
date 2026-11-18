// 抽屉/弹窗在窄屏的适配审计
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = 'http://127.0.0.1:9222';
const URL0 = process.argv[3] || process.env.UI_URL || 'http://127.0.0.1:8771/index.html';
const OUT = process.argv[2] || path.join(os.tmpdir(), 'bs_drawer');
const BP = [[1024, 768, false], [834, 1112, true], [390, 844, true], [320, 568, true]];

const DETECT = `(() => {
  const R = e => { const r = e.getBoundingClientRect(); return { x:+r.left.toFixed(0), w:+r.width.toFixed(0), r:+r.right.toFixed(0) }; };
  const nm = e => { let s = e.tagName.toLowerCase(); if (e.id) s += '#'+e.id; else if (typeof e.className==='string' && e.className.trim()) s += '.'+e.className.trim().split(/\\s+/).slice(0,2).join('.'); return s; };
  const d = document.getElementById('drawer');
  if (!d || d.hidden) return { err: 'drawer 未打开' };
  const db = d.getBoundingClientRect();
  const out = { drawer: { w:+db.width.toFixed(0), h:+db.height.toFixed(0), l:+db.left.toFixed(0) },
    body: null, overflow: [], wrap: [], tiny: [], tabs: null, scrollRatio: 0 };

  const body = d.querySelector('.drawer-body');
  out.body = { scrollH: body.scrollHeight, clientH: body.clientHeight };
  out.scrollRatio = +(body.scrollHeight / body.clientHeight).toFixed(2);

  // 超出抽屉右边界（允许 1px 误差 + 滚动条 8px）
  d.querySelectorAll('*').forEach(e => {
    if (!e.getClientRects().length) return;
    const s = getComputedStyle(e);
    if (s.position === 'fixed' || s.position === 'absolute') return;
    const r = e.getBoundingClientRect();
    if (r.width < 1) return;
    if (r.right > db.right - 2 && s.overflowX !== 'hidden') {
      out.overflow.push({ el: nm(e), over: +(r.right - db.right).toFixed(1) });
    }
  });

  // 触控目标
  d.querySelectorAll('button, input, select, a').forEach(e => {
    if (!e.getClientRects().length) return;
    const r = e.getBoundingClientRect();
    if (r.height < 30 || r.width < 30) out.tiny.push({ el: nm(e), w:+r.width.toFixed(0), h:+r.height.toFixed(0) });
  });

  // 标签与控件是否挤在同一行导致折行混乱：取每行 .row 的高度与子项数
  out.rows = [...d.querySelectorAll('.row, .set-row, .ctrl-line, .form-row')].slice(0, 20).map(e => ({
    cls: nm(e), h: +e.getBoundingClientRect().height.toFixed(0), kids: e.children.length,
  }));

  const tabs = d.querySelector('.drawer-tabs');
  if (tabs) out.tabs = { w: +tabs.getBoundingClientRect().width.toFixed(0), h: +tabs.getBoundingClientRect().height.toFixed(0) };

  // 去重
  const seen = new Set();
  out.overflow = out.overflow.filter(o => { if (seen.has(o.el + o.over)) return false; seen.add(o.el + o.over); return true; });
  return out;
})()`;

let msgId = 0; const pending = new Map(); let ws;
async function send(method, params = {}) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('timeout ' + method)); } }, 30000);
  });
}

fs.mkdirSync(OUT, { recursive: true });
const tabs = await (await fetch(BASE + '/json/list')).json();
const page = tabs.find(t => t.type === 'page') || tabs[0];
ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws')); });
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { const p = pending.get(m.id); pending.delete(m.id); m.error ? p.reject(new Error(JSON.stringify(m.error))) : p.resolve(m.result); }
};
await send('Page.enable'); await send('Runtime.enable');

const report = [];
for (const [W, H, mobile] of BP) {
  await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 1, mobile });
  await send('Page.navigate', { url: URL0 + '?_t=' + Date.now() });
  await new Promise(r => setTimeout(r, 1800));
  await send('Runtime.evaluate', { expression: `document.getElementById('btnSettings').click()` });
  await new Promise(r => setTimeout(r, 700));
  const r = await send('Runtime.evaluate', { expression: DETECT, returnByValue: true });
  const d = r.result.value; d.bp = W + 'x' + H;
  report.push(d);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  fs.writeFileSync(`${OUT}/drawer_${W}x${H}.png`, Buffer.from(shot.data, 'base64'));
}
fs.writeFileSync(`${OUT}/report.json`, JSON.stringify(report, null, 1));

for (const d of report) {
  if (d.err) { console.log(d.bp, '⚠', d.err); continue; }
  const f = [];
  if (d.overflow.length) f.push('溢出 ' + d.overflow.length + ' ' + JSON.stringify(d.overflow.slice(0, 3)));
  if (d.tiny.length) f.push('小控件 ' + d.tiny.length);
  console.log(`${d.bp.padEnd(10)} drawer ${d.drawer.w}x${d.drawer.h}  body ${d.body.clientH}/${d.body.scrollH} (${d.scrollRatio}×)  ${f.length ? '⚠ ' + f.join(' | ') : '✓ 干净'}`);
}
