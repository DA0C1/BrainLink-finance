// 双引擎 RR 通路探针（JS 侧）
// 用途：验证 live_bridge → SSE JSON → app.js Frame → engine.js rrBuf 这条链上，
//       一次 onRR 的 3 个间期是否**真的**进到了缓冲、批次号是否非空。
// 背景：FRAME_KEYS 白名单曾漏掉 rr_batch，导致浏览器每帧只剩 rr_ms 单值、
//       批次恒 null → 跨帧差分 → RMSSD 由 24.14ms 虚高到 38.38ms（+59%）。
// 入参：stdin JSON 数组，元素同 live_bridge._clean() 的输出（含 rr_batch / hr）
// 出参：stdout JSON { rrBufLen, batchCount, nullBatch, rmssd, hr, state }
// 调用方：verify_rrpath.py（勿单独删改）
const fs = require('fs'), vm = require('vm'), path = require('path');

global.window = global;
vm.runInThisContext(fs.readFileSync(
  path.resolve(__dirname, '..', '组员文件', '任务一', 'js', 'engine.js'), 'utf8'));
const { StateEngine, Frame } = global.BrainState;

const frames = JSON.parse(fs.readFileSync(0, 'utf8'));
const eng = new StateEngine({ baseline: StateEngine.defaultBaseline() });

let last = null;
for (const o of frames) {
  // 与 app.js nextFrame() 的 live 分支保持同一映射口径
  const f = new Frame({
    theta: o.theta, alpha: o.alpha, beta: o.beta, delta: o.delta, gamma: o.gamma,
    attention: o.attention, meditation: o.meditation,
    rr_ms: o.rr_ms, rr_batch: o.rr_batch, heart: o.hr,
    gyro: o.gyro || [0, 0, 0], ts: o.ts,
  });
  last = eng.processFrame(f, { task_active: true }).decision;
}

const feats = eng._extract();
const batches = new Set(eng.rrBuf.map(x => x[1]));
process.stdout.write(JSON.stringify({
  rrBufLen: eng.rrBuf.length,
  batchCount: batches.size,
  nullBatch: eng.rrBuf.filter(x => x[1] == null).length,
  rmssd: Number.isFinite(feats.RMSSD_60) ? feats.RMSSD_60 : null,
  hr: Number.isFinite(feats.HR) ? feats.HR : null,
  state: last ? last.state : null,
}));
