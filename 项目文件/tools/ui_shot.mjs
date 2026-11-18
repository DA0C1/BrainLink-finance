// 截图工具：CDP 驱动无头 Edge，抓页面全图
// 用法：node _shot.mjs http://127.0.0.1:8771/ /tmp/ui.png [宽] [高]
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

const BASE = 'http://127.0.0.1:9222';
const url = process.argv[2];
const out = process.argv[3] || path.join(os.tmpdir(), 'ui.png');
const W = +(process.argv[4] || 1440);
const H = +(process.argv[5] || 1000);

let msgId = 0;
const pending = new Map();

function connect(wsUrl) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(wsUrl);
    ws.onopen = () => resolve(ws);
    ws.onerror = () => reject(new Error('ws error'));
  });
}
function send(ws, method, params = {}) {
  const id = ++msgId;
  return new Promise((resolve, reject) => {
    pending.set(id, { resolve, reject });
    ws.send(JSON.stringify({ id, method, params }));
    setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('timeout ' + method)); } }, 20000);
  });
}

const tabs = await (await fetch(BASE + '/json/list')).json();
const page = tabs.find(t => t.type === 'page') || tabs[0];
if (!page) throw new Error('没有可用 tab');
const ws = await connect(page.webSocketDebuggerUrl);
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
};

await send(ws, 'Page.enable');
await send(ws, 'Runtime.enable');
await send(ws, 'Emulation.setDeviceMetricsOverride', {
  width: W, height: H, deviceScaleFactor: 1, mobile: false,
});
await send(ws, 'Page.navigate', { url: url + (url.includes('?') ? '&' : '?') + '_t=' + Date.now() });
await new Promise(r => setTimeout(r, 3000));
const act = process.env.SHOT_ACT;
if (act) {
  await send(ws, 'Runtime.evaluate', { expression: act, awaitPromise: true });
  await new Promise(r => setTimeout(r, 1200));
}

// 收集控制台错误（UI 诊断用）
const errs = await send(ws, 'Runtime.evaluate', {
  expression: `JSON.stringify(window.__uiErrors || [])`, returnByValue: true,
}).catch(() => ({ result: { value: '[]' } }));

// SHOT_CLIP=选择器 时只截该元素（2x 缩放，用于看细节）
// SHOT_VIEWPORT=1 只截视口（fixed 抽屉/弹窗必须用这个：整页截图会把 fixed 元素
// 在视口外裁掉，露出下面的页面本体，看着像"抽屉只有半截"）
let opts = { format: 'png', captureBeyondViewport: !process.env.SHOT_VIEWPORT };
const clipSel = process.env.SHOT_CLIP;
if (clipSel) {
  const r = await send(ws, 'Runtime.evaluate', {
    expression: `(() => { const e = document.querySelector(${JSON.stringify(clipSel)});
      if (!e) return null; const b = e.getBoundingClientRect();
      return { x: b.left + scrollX, y: b.top + scrollY, width: b.width, height: b.height }; })()`,
    returnByValue: true,
  });
  const box = r.result.value;
  if (box) opts = { format: 'png', clip: { ...box, scale: 2 } };
  else console.log('⚠ 未找到元素:', clipSel);
}
const shot = await send(ws, 'Page.captureScreenshot', opts);
fs.writeFileSync(out, Buffer.from(shot.data, 'base64'));
console.log('已保存', out, fs.statSync(out).size, 'B');
console.log('页面标题:', (await send(ws, 'Runtime.evaluate', { expression: 'document.title', returnByValue: true })).result.value);
console.log('控制台错误:', errs.result?.value);
ws.close();
