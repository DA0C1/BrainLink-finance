/* verify_calib_e2e.mjs — 校准流程端到端（失败不落盘 / 成功落盘）
 *
 * 用法：
 *   1) 起静态服务：python -m http.server 8771 --bind 127.0.0.1  （在 组员文件/任务一 下）
 *   2) 起无头浏览器：Edge/Chrome --remote-debugging-port=9222
 *   3) node tools/verify_calib_e2e.mjs
 *
 * 覆盖两个历史 P0：
 *   · 校准失败仍保存基线 → 之后"未校准"横幅永久消失；
 *   · 校准时长跟随演示步长（步长 5 时 A 段 3 帧）——现在固定 1s/帧，这里用 targets 控制。
 */
import process from 'node:process';

const BASE = 'http://127.0.0.1:9222';
const URL0 = process.env.UI_URL || 'http://127.0.0.1:8771/index.html?debug=1';
let id = 0;
const pending = new Map();
const tabs = await (await fetch(BASE + '/json/list')).json();
const page = tabs.find(t => t.type === 'page') || tabs[0];
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise(r => { ws.onopen = r; });
ws.onmessage = e => {
  const m = JSON.parse(e.data);
  if (m.id && pending.has(m.id)) {
    const p = pending.get(m.id); pending.delete(m.id);
    m.error ? p.reject(m.error) : p.resolve(m.result);
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const i = ++id; pending.set(i, { resolve, reject });
  ws.send(JSON.stringify({ id: i, method, params }));
});
const ev = async (expression, awaitPromise = false) => {
  const r = await send('Runtime.evaluate', { expression, returnByValue: true, awaitPromise });
  if (r.exceptionDetails) throw new Error(r.exceptionDetails.text || 'JS error');
  return r.result.value;
};
const wait = ms => new Promise(r => setTimeout(r, ms));
let fails = 0;
const ck = (ok, name, detail = '') => {
  console.log(`  [${ok ? 'PASS' : 'FAIL'}] ${name}${detail ? ' — ' + detail : ''}`);
  if (!ok) fails++;
};

await send('Page.enable');
await send('Runtime.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Emulation.setDeviceMetricsOverride', { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
await send('Page.navigate', { url: URL0 + '&_t=' + Date.now() });
await wait(2200);

await ev(`localStorage.removeItem('bsui:calibBase'); localStorage.removeItem('bsui:calibMeta');`);
console.log('C1 失败校准不得落盘');
const failRes = await ev(`(async()=>{
  const b = window.__bs; if (!b) return {noDebug:true};
  b.startCalib();
  await new Promise(r=>setTimeout(r,80));
  document.getElementById('btnPlay').click();      // 暂停，手动逐帧
  b.CAL.targets = {A:3, B:3, C:3};                 // 强制样本不足
  for (let i=0;i<9;i++){ document.getElementById('btnStep').click(); await new Promise(r=>setTimeout(r,8)); }
  await new Promise(r=>setTimeout(r,120));
  return {active:b.CAL.active,
    hasBase: !!localStorage.getItem('bsui:calibBase'),
    report: (document.getElementById('calibReport').textContent||'').slice(0,80),
    step: document.getElementById('calibStep').textContent};
})()`, true);
ck(!failRes.noDebug, '调试钩子可用');
ck(failRes.active === false, '失败后校准已结束', String(failRes.active));
ck(failRes.hasBase === false, '失败不写 calibBase', String(failRes.hasBase));
ck(/未通过|失败|告警/.test(failRes.report + failRes.step), '界面给出失败提示',
  (failRes.report || failRes.step || '').slice(0, 60));

console.log('\nC2 成功校准必须落盘且 ok=true');
const okRes = await ev(`(async()=>{
  const b = window.__bs;
  localStorage.removeItem('bsui:calibBase'); localStorage.removeItem('bsui:calibMeta');
  b.startCalib();
  await new Promise(r=>setTimeout(r,80));
  document.getElementById('btnPlay').click();
  b.CAL.targets = {A:15, B:40, C:40};
  for (let i=0;i<95;i++){ document.getElementById('btnStep').click(); await new Promise(r=>setTimeout(r,6)); }
  await new Promise(r=>setTimeout(r,200));
  const meta = JSON.parse(localStorage.getItem('bsui:calibMeta')||'null');
  return {hasBase: !!localStorage.getItem('bsui:calibBase'),
    ok: meta && meta.report ? meta.report.ok : null,
    segs: meta && meta.report ? meta.report.segments : null,
    report: (document.getElementById('calibReport').textContent||'').slice(0,80)};
})()`, true);
ck(okRes.hasBase === true, '成功写 calibBase', String(okRes.hasBase));
ck(okRes.ok === true, 'calibMeta.report.ok=true', String(okRes.ok));
ck(okRes.segs && okRes.segs.A === 15 && okRes.segs.B === 40 && okRes.segs.C === 40,
  '三段帧数正确', JSON.stringify(okRes.segs));

await ev(`localStorage.removeItem('bsui:calibBase'); localStorage.removeItem('bsui:calibMeta');`);
ws.close();
console.log(fails ? `\n失败 ${fails} 项` : '\n校准 e2e 全通');
process.exit(fails ? 1 : 0);
