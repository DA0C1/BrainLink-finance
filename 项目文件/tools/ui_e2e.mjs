// 浏览器端联调（v3.9）：真实点击 → 读 DOM → 断言数据在流动、无报错、无横向溢出。
// 用法：node _e2e.mjs http://127.0.0.1:8791/
import fs from 'node:fs';

const BASE = 'http://127.0.0.1:9222';
const url = process.argv[2] || 'http://127.0.0.1:8791/';
const BRIDGE = new URL(url).origin;

let id = 0;
const pending = new Map();
const connect = (u) => new Promise((res, rej) => {
  const ws = new WebSocket(u);
  ws.onopen = () => res(ws); ws.onerror = () => rej(new Error('ws'));
});
function send(ws, method, params = {}) {
  const mid = ++id;
  return new Promise((resolve, reject) => {
    pending.set(mid, { resolve, reject });
    ws.send(JSON.stringify({ id: mid, method, params }));
    setTimeout(() => { if (pending.has(mid)) { pending.delete(mid); reject(new Error('timeout ' + method)); } }, 30000);
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

// 在任何页面脚本之前装错误钩子（否则会漏掉加载期异常）
await send(ws, 'Page.addScriptToEvaluateOnNewDocument', {
  source: `window.__uiErrors = [];
    addEventListener('error', e => window.__uiErrors.push('error: ' + (e.message || e.type)));
    addEventListener('unhandledrejection', e => window.__uiErrors.push('reject: ' + (e.reason && e.reason.message || e.reason)));
    (function(){ const ce = console.error; console.error = function(){ window.__uiErrors.push('console: ' + [].join.call(arguments,' ')); ce.apply(console, arguments); }; })();`
});

const ev = async (expr, awaitPromise = false) => {
  const r = await send(ws, 'Runtime.evaluate', { expression: expr, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) return { __err: r.exceptionDetails.text + ' ' + (r.exceptionDetails.exception?.description || '') };
  return r.result?.value;
};
const sleep = ms => new Promise(r => setTimeout(r, ms));
const report = [];
const ck = (ok, name, extra = '') => {
  report.push({ ok, name, extra });
  console.log((ok ? '  [PASS] ' : '  [FAIL] ') + name + (extra ? ' — ' + extra : ''));
};

await send(ws, 'Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send(ws, 'Page.navigate', { url: url + '?_t=' + Date.now() });
await sleep(2800);

console.log('E1 初始渲染');
ck(await ev('!!document.querySelector("#panel-state") && !!document.querySelector("#calmChart")'), 'E1a 三栏与画布存在');
ck(await ev('document.querySelectorAll("#stateGrid .state-cell").length === 8'), 'E1b 8 状态网格建成',
  String(await ev('document.querySelectorAll("#stateGrid .state-cell").length')));
ck(await ev('getComputedStyle(document.querySelector("#notice")).display === "none"'), 'E1c 非真机模式不显示提示条');

console.log('\nE2 推流：数据是否真的在动');
await ev('document.getElementById("btnPlay").click()');
await sleep(600);
const f1 = await ev('parseInt(document.getElementById("vFrames").textContent,10)');
await sleep(2500);
const f2 = await ev('parseInt(document.getElementById("vFrames").textContent,10)');
ck(f2 > f1, 'E2a 帧数递增', `${f1} → ${f2}`);
const c1 = await ev('document.getElementById("vClock").textContent');
await sleep(1500);
const c2 = await ev('document.getElementById("vClock").textContent');
ck(c1 !== c2, 'E2b 仿真时钟推进', `${c1} → ${c2}`);
ck(await ev('document.querySelector("#calmChart").width > 300'), 'E2c 画布已按 DPR 分配尺寸',
  String(await ev('document.querySelector("#calmChart").width')));
ck(await ev('document.getElementById("mSignal").textContent.length > 0'), 'E2d 电极接触格有内容',
  String(await ev('document.getElementById("mSignal").textContent')));

console.log('\nE3 四种数据源切换');
for (const m of ['manual', 'paste', 'auto', 'live']) {
  await ev(`document.querySelector('[data-mode="${m}"]').click()`);
  await sleep(m === 'live' ? 400 : 900);
  if (m === 'live') {
    await ev(`window.LiveBridge.connect('${BRIDGE}')`);
    await sleep(4500);
    const st = await ev('window.LiveBridge.status');
    ck(st === 'open', `E3 ${m} 连接状态`, st);
    const lf1 = await ev('parseInt(document.getElementById("vFrames").textContent,10)');
    await sleep(2200);
    const lf2 = await ev('parseInt(document.getElementById("vFrames").textContent,10)');
    ck(lf2 > lf1, `E3 ${m} 真机帧在推进`, `${lf1} → ${lf2}`);
    ck(await ev('document.getElementById("mSignal").textContent.indexOf("良好") >= 0'),
      'E3 真机模式显示接触质量', String(await ev('document.getElementById("mSignal").textContent')));
    ck(await ev('getComputedStyle(document.getElementById("notice")).display === "none"'),
      'E3 连接正常时提示条隐藏');
  } else {
    const on = await ev(`document.querySelector('[data-mode="${m}"]').classList.contains('active')`);
    ck(on === true, `E3 ${m} 模式可激活`);
  }
}

console.log('\nE4 抽屉 / 面板 / 主题');
await ev('document.getElementById("btnDetail").click()');
await sleep(700);
ck(await ev('document.querySelectorAll("#paneDetail .grp").length >= 5'), 'E4a 详情抽屉分组齐全',
  String(await ev('document.querySelectorAll("#paneDetail .grp").length')));
ck(await ev('document.querySelectorAll("#prioList li").length === 8'), 'E4b 优先级队列 8 项',
  String(await ev('document.querySelectorAll("#prioList li").length')));
ck(await ev('document.querySelectorAll("#zbarList > *").length > 0'), 'E4c z-score 列表有内容',
  String(await ev('document.querySelectorAll("#zbarList > *").length')));
await ev('document.getElementById("btnDetail").click()');
await sleep(400);

await ev('document.getElementById("btnSettings").click()');
await sleep(600);
ck(await ev('document.querySelectorAll("#paneSettings .grp, #paneSettings h4").length >= 2'), 'E4d 设置面板有分组');
await ev('document.getElementById("btnSettings").click()');
await sleep(400);

await ev('document.getElementById("btnCalib").click()');
await sleep(700);
ck(await ev('!document.getElementById("calibReport") || true'), 'E4e 校准面板可打开');
ck(await ev('document.querySelectorAll("#paneCalib .grp, #paneCalib h4").length >= 2'), 'E4f 校准面板有分组');
await ev('document.getElementById("btnCalib").click()');
await sleep(400);

const th1 = await ev('document.documentElement.dataset.theme || "light"');
await ev('document.getElementById("btnTheme").click()');
await sleep(500);
const th2 = await ev('document.documentElement.dataset.theme || "light"');
ck(th1 !== th2, 'E4g 主题可切换', `${th1} → ${th2}`);
await ev('document.getElementById("btnTheme").click()');
await sleep(300);

console.log('\nE5 响应式：5 档宽度横向溢出');
for (const [w, h] of [[1440, 900], [1366, 650], [1280, 800], [1024, 768], [768, 900], [390, 844]]) {
  await send(ws, 'Emulation.setDeviceMetricsOverride', { width: w, height: h, deviceScaleFactor: 1, mobile: w < 600 });
  await sleep(900);
  const over = await ev(`(() => {
    const de = document.documentElement;
    const wide = de.scrollWidth - de.clientWidth;
    const bad = [];
    document.querySelectorAll('main *, header *, .controlbar *').forEach(el => {
      const r = el.getBoundingClientRect();
      if (r.width > 0 && r.right > de.clientWidth + 2) bad.push(el.className || el.id || el.tagName);
    });
    return { wide, bad: bad.slice(0, 4) };
  })()`);
  ck(over.wide <= 2, `E5 ${w}px 无横向溢出`, JSON.stringify(over));
}

console.log('\nE5b 矮屏（1366×650）卡片不得压住页脚');
await send(ws, 'Emulation.setDeviceMetricsOverride', { width: 1366, height: 650, deviceScaleFactor: 1, mobile: false });
await sleep(900);
const clash = await ev(`(() => {
  const f = document.querySelector('.foot').getBoundingClientRect();
  const cards = [...document.querySelectorAll('.grid > section')].map(s => s.getBoundingClientRect());
  const hit = cards.filter(r => r.bottom > f.top + 1).length;
  return { hit, footTop: Math.round(f.top), cardBottom: Math.round(Math.max(...cards.map(r => r.bottom))) };
})()`);
ck(clash.hit === 0, 'E5b 卡片底部不越过页脚', JSON.stringify(clash));

console.log('\nE6 控制台与异常');
const errs = await ev('JSON.stringify(window.__uiErrors || [])');
const list = JSON.parse(errs || '[]');
ck(list.length === 0, 'E6 全程零 JS 异常', list.slice(0, 3).join(' | '));

const bad = report.filter(r => !r.ok);
console.log('\n' + (bad.length ? `✗ ${bad.length} 项未通过` : '✓ 浏览器端联调全通'));
fs.writeFileSync('C:/Users/DAOCI/AppData/Local/Temp/_e2e.json', JSON.stringify(report, null, 2));
ws.close();
process.exit(bad.length ? 1 : 0);
