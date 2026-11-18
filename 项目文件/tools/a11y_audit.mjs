// 对比度审计：CDP 连无头 Edge，对指定选择器计算 WCAG 对比度。
// 用法：node _a11y.mjs http://127.0.0.1:8773/ [theme]
// 输出每项的前景色/背景色/对比度/是否达标（正文 4.5:1，大字与图形 3:1）
import fs from 'node:fs';

const BASE = 'http://127.0.0.1:9222';
const url = process.argv[2];
const theme = process.argv[3] || 'light';

let msgId = 0;
const pending = new Map();
const connect = (u) => new Promise((res, rej) => {
  const ws = new WebSocket(u);
  ws.onopen = () => res(ws); ws.onerror = () => rej(new Error('ws'));
});
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
await send(ws, 'Emulation.setDeviceMetricsOverride', { width: 1440, height: 1000, deviceScaleFactor: 1, mobile: false });
await send(ws, 'Page.navigate', { url: url + '?_t=' + Date.now() });
await new Promise(r => setTimeout(r, 2500));

// 强制主题：不依赖上次残留的 localStorage
await send(ws, 'Runtime.evaluate', {
  expression: "document.documentElement.dataset.theme = '" + theme + "'",
});
await new Promise(r => setTimeout(r, 400));
await send(ws, 'Runtime.evaluate', { expression: "document.getElementById('btnPlay') && document.getElementById('btnPlay').click()" });
await new Promise(r => setTimeout(r, 1500));

const expr = fs.readFileSync(new URL('./_a11y_inpage.js', import.meta.url), 'utf8');
const res = await send(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise: true });
console.log(res.result?.value || JSON.stringify(res));
ws.close();
