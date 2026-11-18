// ui_capture_report.mjs — 生成汇报用界面截图（v3.16）
//
// 为什么单独写一个：ui_shot.mjs 是"单张、固定等待"，出汇报图需要
// 「等数据填满 → 点格子 → 等弹窗 → 切抽屉」这样有顺序的一组动作，
// 每步等待时间不同，且要保证截到的是有数据的成熟状态而不是空壳。
//
// 用法（先起 live_bridge mock + 无头 Edge，见文件末尾说明）：
//   node 项目文件/tools/ui_capture_report.mjs <输出目录> [UI 根地址] [CDP 地址]
import fs from 'node:fs';
import path from 'node:path';

const OUT = process.argv[2] || 'report_shots';
const UI = process.argv[3] || 'http://127.0.0.1:8780';
const CDP = process.argv[4] || 'http://127.0.0.1:9233';
const W = 1440, H = 1000;   // 高 1000：左卡 8 态网格可完整显示，不必滚动

fs.mkdirSync(OUT, { recursive: true });

let msgId = 0;
const pending = new Map();
const tabs = await (await fetch(CDP + '/json/list')).json();
const page = tabs.find(t => t.type === 'page') || tabs[0];
if (!page) throw new Error('没有可用 tab');
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((res, rej) => { ws.onopen = res; ws.onerror = rej; });
ws.onmessage = (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) {
    const { resolve, reject } = pending.get(m.id);
    pending.delete(m.id);
    m.error ? reject(new Error(JSON.stringify(m.error))) : resolve(m.result);
  }
};
const send = (method, params = {}) => new Promise((resolve, reject) => {
  const id = ++msgId;
  pending.set(id, { resolve, reject });
  ws.send(JSON.stringify({ id, method, params }));
  setTimeout(() => { if (pending.has(id)) { pending.delete(id); reject(new Error('timeout ' + method)); } }, 30000);
});
const sleep = (ms) => new Promise(r => setTimeout(r, ms));

await send('Page.enable');
await send('Runtime.enable');
await send('Network.enable');
await send('Network.setCacheDisabled', { cacheDisabled: true });
await send('Emulation.setDeviceMetricsOverride', { width: W, height: H, deviceScaleFactor: 2, mobile: false });

await send('Page.navigate', { url: `${UI}/index.html?debug=1&_t=${Date.now()}` });
await sleep(3000);

// 让数据窗填满（10s 窗 + 曲线有点），确保不是空壳
await send('Runtime.evaluate', { expression: 'window.__bs && (window.__bs.S.playing = true)' });
await sleep(7000);

// 关键：曲线画的是 history 里的 calm 值，而 calm 在零决策时是 null（设计如此）。
// 不先记录决策，截图就是"尚无决策样本"的空白卡片 —— 汇报图不能是这样。
//
// 并且要**造出曲线起伏**才有讲解价值：
//   ① 先在初始（专注/心流）状态记 2 次决策 → 不冲动 → calm = 1.00
//   ② 再切"分心"，等判出后在分心状态记 2 次决策 → 记为冲动 → calm 掉到 0.50
// 这样截图能直接讲"分心时下单被标记为冲动，冷静度从 1.00 掉到 0.50"。
const click = async (sel) => {
  const r = await send('Runtime.evaluate', {
    returnByValue: true,
    expression: `(()=>{const e=document.querySelector('${sel}'); if(!e) return 'miss'; e.click(); return 'ok';})()`,
  });
  return r.result.value;
};

await click('.trade.buy'); await sleep(4500);
await click('.trade.sell'); await sleep(4500);

// 切「分心」并等判出（实测 7–12 秒）
await click('.state-cell[data-key="distracted"]');
await sleep(9000);    // 刚判出即可，待久了会按冷却周期反复提醒
await click('.trade.buy'); await sleep(3500);
await click('.trade.hold'); await sleep(3500);

// 关掉累积的弹窗，避免遮挡提醒中心与曲线（点 X，不是删 DOM）
await send('Runtime.evaluate', {
  expression: `document.querySelectorAll('.toast .toast-close').forEach(b=>b.click())`,
});
await sleep(2500);
await send('Runtime.evaluate', {
  expression: `document.querySelectorAll('.toast .toast-close').forEach(b=>b.click())`,
});
await sleep(1200);

async function shot(name, clip) {
  const opts = { format: 'png', captureBeyondViewport: false };
  if (clip) opts.clip = { ...clip, scale: 2 };
  const { data } = await send('Page.captureScreenshot', opts);
  const p = path.join(OUT, name);
  fs.writeFileSync(p, Buffer.from(data, 'base64'));
  console.log('saved', p);
}

// ① 主界面全貌（有数据、有曲线、8 态网格点亮）
await shot('01_主界面_运行中.png');

// ② 点「焦虑」→ 等判出与弹窗（实测 10–15s），
//    再在焦虑状态下记 1 次决策 → 记为冲动 → calm 继续下降，曲线不会是一条平线
await click('.state-cell[data-key="anxiety"]');
await sleep(13000);
await click('.trade.sell');
await sleep(5000);
await shot('02_提醒弹窗_焦虑calm.png');

// ③ 8 态网格特写（讲证据条 / 弱判别 / 无 RR 标记用）
const box = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(()=>{const r=document.querySelector('#panel-state').getBoundingClientRect();
    return {x:Math.round(r.x),y:Math.round(r.y),width:Math.round(r.width),height:Math.round(r.height)};})()`,
});
await shot('03_状态卡特写.png', box.result.value);

// ④ 校准报告：不能只截"未校准"的空面板 —— 那讲不了 θ_ref 来源、伪迹率、c_anchor_z。
//    跑一次快速协议（A15/B40/C40 = 95 帧，主循环约 12s），拿真实报告再截。
await send('Runtime.evaluate', {
  expression: `document.getElementById('btnCalib').click()`,
});
await sleep(1200);
await send('Runtime.evaluate', {
  expression: `(()=>{const b=window.__bs;
    if(!b){return 'no-debug';}
    b.startCalib();
    b.CAL.targets = { A: 15, B: 40, C: 40 };   // 快速协议，判据不变
    return 'started';})()`,
});
await sleep(22000);                            // 95 帧 + 段间切换余量
const rep = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(()=>({step: (document.getElementById('calibStep')||{}).textContent||'',
    report: (document.getElementById('calibReport')||{}).textContent||''}))()`,
});
console.log('校准结果:', (rep.result.value.step || '').slice(0, 40),
  '|', (rep.result.value.report || '').slice(0, 60).replace(/\s+/g, ' '));
// 04：抽屉上半部（当前基线 + 校准完成 + 段样本）——PPT 主图，信息密度合适
const drawer = await send('Runtime.evaluate', {
  returnByValue: true,
  expression: `(()=>{const r=document.getElementById('drawer').getBoundingClientRect();
    return {x:Math.round(r.x),y:0,width:Math.round(r.width),height:640};})()`,
});
await shot('04_校准报告.png', drawer.result.value);

// 05：完整抽屉（含"哪些判据不可用"的告警行）——主动交底时翻出来
await shot('05_校准报告_完整.png');

console.log('\n完成。用途对应：');
console.log('  01 → PPT P11 演示页 / 开场     02 → 讲"calm 级提醒"');
console.log('  03 → 讲 8 态判据与证据条        04 → 讲校准协议与个体基线');
console.log('\n复现命令：');
console.log('  python 项目文件/live_bridge.py --source mock --port 8780 --interval 1');
console.log('  msedge --headless=new --remote-debugging-port=9233 --window-size=1440,900 about:blank');
console.log('  node 项目文件/tools/ui_capture_report.mjs 我的文件（组长）/汇报截图_v316');
ws.close();
