// 响应式审计：CDP 逐断点渲染 + 几何量测 + 截图
// 用法: node _rs_audit.mjs [outdir]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = process.env.CDP_URL || 'http://127.0.0.1:9222';   // 可指向别的调试端口
const URL0 = process.argv[3] || process.env.UI_URL || 'http://127.0.0.1:8771/index.html';
const OUT = process.argv[2] || path.join(os.tmpdir(), 'bs_responsive');

const BP = [
  ['1920x1080', 1920, 1080, false],
  ['1600x900', 1600, 900, false],
  ['1440x900', 1440, 900, false],
  ['1366x768', 1366, 768, false],
  ['1366x650', 1366, 650, false],
  ['1280x800', 1280, 800, false],
  ['1180x820', 1180, 820, false],
  ['1024x768', 1024, 768, false],
  ['834x1112', 834, 1112, true],
  ['768x1024', 768, 1024, true],
  ['430x932', 430, 932, true],
  ['390x844', 390, 844, true],
  ['375x667', 375, 667, true],
  ['360x740', 360, 740, true],
  ['320x568', 320, 568, true],
];

const DETECT = `(() => {
  const R = (e) => { const r = e.getBoundingClientRect(); return { x:+r.left.toFixed(1), y:+r.top.toFixed(1), w:+r.width.toFixed(1), h:+r.height.toFixed(1), r:+r.right.toFixed(1), b:+r.bottom.toFixed(1) }; };
  const vis = (e) => {
    if (!e.getClientRects().length) return false;
    const s = getComputedStyle(e);
    return s.display !== 'none' && s.visibility !== 'hidden' && +s.opacity > 0.01;
  };
  const nm = (e) => {
    if (!e) return '?';
    let s = e.tagName.toLowerCase();
    if (e.id) s += '#' + e.id;
    else if (typeof e.className === 'string' && e.className.trim()) s += '.' + e.className.trim().split(/\\s+/).slice(0,2).join('.');
    return s;
  };
  const inter = (a, b) => {
    const w = Math.min(a.r, b.r) - Math.max(a.x, b.x);
    const h = Math.min(a.b, b.b) - Math.max(a.y, b.y);
    return (w > 2 && h > 2) ? +(w * h).toFixed(0) : 0;
  };
  const out = {
    vw: innerWidth, vh: innerHeight,
    docW: document.documentElement.scrollWidth,
    docH: document.documentElement.scrollHeight,
    hOver: document.documentElement.scrollWidth - innerWidth,
    outOfView: [], overlap: [], clip: [], tiny: [], fontSize: [], blocks: {},
  };

  // ---- A. 元素越出视口右边界 / 左边界 ----
  document.querySelectorAll('body *').forEach((e) => {
    if (!vis(e)) return;
    const s = getComputedStyle(e);
    if (s.position === 'fixed') return;
    const r = e.getBoundingClientRect();
    if (r.width < 1) return;
    if (r.right > innerWidth + 1.5) out.outOfView.push({ el: nm(e), right: +r.right.toFixed(1), over: +(r.right - innerWidth).toFixed(1) });
    if (r.left < -1.5) out.outOfView.push({ el: nm(e), left: +r.left.toFixed(1) });
  });

  // ---- B. 区块重叠（三层：body 顶层 / grid 子项 / card 子项）----
  const groups = [];
  const top = [...document.body.children].filter(e => vis(e) && getComputedStyle(e).position !== 'fixed');
  groups.push(['top', top]);
  const grid = document.querySelector('main.grid');
  if (grid) groups.push(['grid', [...grid.children].filter(vis)]);
  document.querySelectorAll('.card').forEach((c, i) => {
    if (!vis(c)) return;
    groups.push(['card' + i + ':' + nm(c), [...c.children].filter(vis)]);
  });
  for (const [gname, els] of groups) {
    for (let i = 0; i < els.length; i++) {
      for (let j = i + 1; j < els.length; j++) {
        const a = els[i], b = els[j];
        if (a.contains(b) || b.contains(a)) continue;
        if (getComputedStyle(a).position === 'absolute' || getComputedStyle(b).position === 'absolute') continue;
        // 横向并列（grid 同行）不算重叠；判定用相交面积占比
        const ra = R(a), rb = R(b);
        const ov = inter(ra, rb);
        if (!ov) continue;
        const ratio = ov / Math.min(ra.w * ra.h, rb.w * rb.h);
        if (ratio > 0.02) out.overlap.push({ group: gname, a: nm(a), b: nm(b), area: ov, ratio: +ratio.toFixed(3), ra, rb });
      }
    }
  }

  // ---- C. 子元素溢出父容器 ----
  document.querySelectorAll('.card > *, .card-foot > *, .card-head > *').forEach((e) => {
    if (!vis(e)) return;
    const p = e.parentElement;
    const r = e.getBoundingClientRect(), pr = p.getBoundingClientRect();
    const ps = getComputedStyle(p);
    const padL = parseFloat(ps.paddingLeft), padR = parseFloat(ps.paddingRight);
    if (r.right > pr.right - padR + 1.5) out.clip.push({ kind: 'overflowR', el: nm(e), by: +(r.right - (pr.right - padR)).toFixed(1) });
  });

  // ---- D. 文字被裁 ----
  document.querySelectorAll('body *').forEach((e) => {
    if (!vis(e)) return;
    const s = getComputedStyle(e);
    if (e.children.length && !e.textContent.trim()) return;
    if (!e.textContent.trim()) return;
    if (s.overflowX === 'hidden' || s.overflow === 'hidden') {
      if (e.scrollWidth > e.clientWidth + 2 && e.clientWidth > 0) out.clip.push({ kind: 'textX', el: nm(e), text: e.textContent.trim().slice(0, 24), need: e.scrollWidth, have: e.clientWidth });
    }
    if (s.overflowY === 'hidden' && e.scrollHeight > e.clientHeight + 2 && e.clientHeight > 0) {
      out.clip.push({ kind: 'textY', el: nm(e), text: e.textContent.trim().slice(0, 24), need: e.scrollHeight, have: e.clientHeight });
    }
    const fs = parseFloat(s.fontSize);
    if (fs && fs < 10.5 && e.textContent.trim().length > 1) out.fontSize.push({ el: nm(e), fs, text: e.textContent.trim().slice(0, 16) });
  });

  // ---- E. 触控目标（桌面按 24，触屏按 32）----
  document.querySelectorAll('button, [role="tab"], input, select, a').forEach((e) => {
    if (!vis(e)) return;
    const r = e.getBoundingClientRect();
    const min = innerWidth <= 900 ? 32 : 24;
    if (r.width < min || r.height < min) out.tiny.push({ el: nm(e), w: +r.width.toFixed(1), h: +r.height.toFixed(1) });
  });

  // ---- F. 滚动容器：内容被藏起来的比例（>1.25 视为"看不全"，需人判断）----
  out.scrollers = [];
  document.querySelectorAll('body *').forEach((e) => {
    if (!vis(e)) return;
    const s = getComputedStyle(e);
    if (!/auto|scroll/.test(s.overflowY) && !/auto|scroll/.test(s.overflowX)) return;
    const need = e.scrollHeight, have = e.clientHeight;
    if (have > 0 && need > have * 1.25) out.scrollers.push({ el: nm(e), need, have, ratio: +(need / have).toFixed(2) });
  });

  // ---- G. 关键区块位置（供人工核对顺序与间距）----
  const key = ['.topbar', '.controlbar', 'main.grid', '#panel-state', '#panel-curve', '#panel-remind', '.foot'];
  key.forEach((sel) => { const e = document.querySelector(sel); if (e && vis(e)) out.blocks[sel] = R(e); });
  out.panels = [...document.querySelectorAll('main.grid > section')].map(e => ({ id: e.id || nm(e), ...R(e) }));

  // 去重
  const seen = new Set();
  out.outOfView = out.outOfView.filter(o => { const k = o.el + o.right + o.left; if (seen.has(k)) return false; seen.add(k); return true; });
  return out;
})()`;

let msgId = 0;
const pending = new Map();
let ws;
async function send(method, params = {}) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('timeout ' + method)); } }, 30000);
  });
}
async function evalJs(expr) {
  const r = await send('Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
  if (r.exceptionDetails) throw new Error(JSON.stringify(r.exceptionDetails).slice(0, 400));
  return r.result.value;
}

fs.mkdirSync(OUT, { recursive: true });
const tabs = await (await fetch(BASE + '/json/list')).json();
const page = tabs.find(t => t.type === 'page') || tabs[0];
if (!page) throw new Error('无 tab');
ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = () => rej(new Error('ws')); });
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id); pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
};
await send('Page.enable');
await send('Runtime.enable');
await send('Network.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });

const report = [];
// 先清一次 localStorage：否则上一次点过主题按钮，截出来全是深色，
// 与文档里的浅色基准对不上（状态注入同理）。
await send('Page.navigate', { url: URL0 }).catch(() => { });
await new Promise(r => setTimeout(r, 1200));
await evalJs('localStorage.clear()').catch(() => { });
for (const [name, W, H, mobile] of BP) {
  await send('Emulation.setDeviceMetricsOverride', {
    width: W, height: H, deviceScaleFactor: 1, mobile,
    screenWidth: W, screenHeight: H,
  });
  await send('Page.navigate', { url: URL0 + '?_t=' + Date.now() });
  await new Promise(r => setTimeout(r, 2200));
  const ok = await evalJs(`!!document.querySelector('main.grid') && !!document.querySelector('#panel-state')`);
  if (!ok) throw new Error(`${name}: 页面未正常加载（不是应用页）—— 检查 8771 服务`);
  const d = await evalJs(DETECT);
  d.bp = name;
  report.push(d);

  const shot = await send('Page.captureScreenshot', { format: 'png', captureBeyondViewport: true });
  fs.writeFileSync(path.join(OUT, name + '.png'), Buffer.from(shot.data, 'base64'));
}

fs.writeFileSync(path.join(OUT, 'report.json'), JSON.stringify(report, null, 1));

// 控制台摘要
for (const d of report) {
  const flags = [];
  if (d.hOver > 1) flags.push(`横溢出 ${d.hOver}px`);
  if (d.outOfView.length) flags.push(`越界 ${d.outOfView.length}`);
  if (d.overlap.length) flags.push(`重叠 ${d.overlap.length}`);
  if (d.clip.length) flags.push(`裁切 ${d.clip.length}`);
  if (d.tiny.length) flags.push(`小目标 ${d.tiny.length}`);
  if (d.fontSize.length) flags.push(`小字 ${d.fontSize.length}`);
  if (d.scrollers.length) flags.push(`藏内容 ${d.scrollers.map(s => s.el + ' ' + s.need + '/' + s.have).join(',')}`);
  console.log(`${d.bp.padEnd(10)} doc ${String(d.docW).padStart(4)}x${String(d.docH).padStart(4)}  ${flags.length ? '⚠ ' + flags.join(' | ') : '✓ 干净'}`);
}
console.log('\n截图目录:', OUT);
