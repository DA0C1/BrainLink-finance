/* engine.test.cjs — 前端引擎纯函数单测（node --test，无需浏览器）
 *
 * 用法：
 *   node --test 项目文件/tools/
 *
 * 覆盖：Frame 空值语义、ts=0 窗口边界、接触门、RMSSD 批次语义、校准估计量。
 * 这些点都曾在真机上出过问题，且黑盒 e2e 抓不到。
 */
'use strict';
const test = require('node:test');
const assert = require('node:assert');
const path = require('node:path');

global.window = global;
require(path.join(__dirname, '..', '..', '组员文件', '任务一', 'js', 'engine.js'));
const { StateEngine, Frame } = global.BrainState;

function mk(o) {
  return new Frame(Object.assign({
    theta: 100, alpha: 100, beta: 100, delta: 100, gamma: 50,
    attention: 60, meditation: 60, gyro: [0.01, 0.01, 0.01]
  }, o));
}

test('Frame：null / 空串走默认值，不是 0', () => {
  const f = new Frame({ attention: null, meditation: '', theta: null, beta: '' });
  assert.equal(f.attention, 50);
  assert.equal(f.meditation, 50);
  assert.equal(f.theta, 100);
  assert.equal(f.beta, 100);
  assert.equal(f.gamma, 50);
});

test('零决策时 calmness 为 null（不是 1.0）', () => {
  const eng = new StateEngine({ baseline: StateEngine.defaultBaseline() });
  assert.equal(eng.calmness, null);
});

test('ts=0 合法：100 帧 1.2s 后 10s 窗收敛到 9 帧', () => {
  const eng = new StateEngine({ baseline: StateEngine.defaultBaseline() });
  for (let i = 0; i < 100; i++) {
    eng.processFrame(mk({ ts: i * 1.2 }), { task_active: true });
  }
  assert.equal(eng.eegBuf.length, 9);
  assert.ok(eng.eegBuf[0].ts > 0, '首帧应已出窗');
});

test('接触门：signal=200 判 contact，signal=0 正常入窗', () => {
  const eng = new StateEngine({ baseline: StateEngine.defaultBaseline() });
  const a = eng.processFrame(mk({ signal: 200, ts: 1 }), { task_active: true });
  assert.equal(a.contact, true);
  assert.equal(eng.eegBuf.length, 0);
  eng.processFrame(mk({ signal: 0, ts: 2 }), { task_active: true });
  assert.equal(eng.eegBuf.length, 1);
});

test('RMSSD：只对同批做差分（跨批跳过）', () => {
  const diffs = StateEngine._rmssdDiffs([[800, 1, 0], [806, 1, 0], [810, 2, 0], [812, 2, 0]]);
  assert.deepEqual(diffs, [6, 2]);
  // 补齐 6 对后 _rmssd 才可算（RMSSD_MIN_PAIRS）
  const buf = [];
  for (let b = 1; b <= 4; b++) {
    buf.push([800 + b, b, 0], [806 + b, b, 0], [810 + b, b, 0]);
  }
  assert.ok(Number.isFinite(StateEngine._rmssd(buf)));
});

test('校准 σ 用 10s 窗估计量（与运行时同尺度）', () => {
  const frames = [];
  const n = 150;
  for (let i = 0; i < n; i++) {
    const th = 100 + 25 * Math.sin(i * 1.2);
    const al = 100 + 30 * Math.cos(i * 1.1);
    const be = 100 + 18 * Math.sin(i * 1.3);
    const dl = 100 + 45 * Math.cos(i * 1.4);
    frames.push({ tag: 'B', frame: mk({ theta: th, alpha: al, beta: be, delta: dl, ts: 1 + i * 1.2 }) });
  }
  for (let i = 0; i < 30; i++) {
    frames.push({ tag: 'A', frame: mk({ theta: 120, alpha: 120, beta: 80, delta: 120, ts: 1 + i * 1.2 }) });
    frames.push({ tag: 'C', frame: mk({ theta: 95, alpha: 160, beta: 70, delta: 100, ts: 1 + i * 1.2 }) });
  }
  const b = StateEngine.calibrateFromFrames(frames);
  assert.equal(b.calib_meta.estimator, 'window10');

  // 逐帧 R_tb 的稳健 σ 作为对照
  const per = frames.filter(x => x.tag === 'B')
    .map(x => x.frame.theta / x.frame.beta);
  const st = StateEngine.defaultBaseline().R_tb;
  const perSigma = StateEngine._robustStats(per, st).sigma;
  assert.ok(b.R_tb.sigma < perSigma * 0.75,
    `窗口 σ=${b.R_tb.sigma} 应明显小于逐帧 σ=${perSigma}`);
});
