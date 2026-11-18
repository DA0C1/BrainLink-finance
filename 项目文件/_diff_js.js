// 双引擎差分·JS 侧：确定性帧序列（无噪声），8 段对齐 output_demo 参数
// 注：flow 段为伪覆盖——确定性帧无 att/med 递增趋势，双引擎同判 focus（实际覆盖 7 态）；
//     flow 的真实覆盖由 output_demo --check 承担（att/med 递增曲线）。
const fs = require('fs'), vm = require('vm'), path = require('path');
global.window = global;
// 2026-10-22 修复：engine.js 实际位于 组员文件\任务一\js\（任务1 UI 交付权威版），
// 原 `__dirname/js/engine.js` 在项目文件\ 下不存在（ENOENT）——改绝对引用
vm.runInThisContext(fs.readFileSync(path.resolve(__dirname, '..', '组员文件', '任务一', 'js', 'engine.js'), 'utf8'));
const { StateEngine, Frame } = global.BrainState;
const rnd = () => 0.5;   // 确定性（无噪声）

// 帧参数表（与 Python 侧脚本逐位一致）[theta,alpha,beta,delta,gamma,att,med,rr0,rr1]
const P = {
  focus:      [55, 130, 100, 100, 50, 65, 55, 800, 820],
  distracted: [160, 100, 100, 100, 50, 45, 45, 770, 790],
  relaxed:    [100, 160, 100, 100, 50, 50, 70, 950, 990],
  stress:     [90, 110, 100, 100, 50, 40, 30, 680, 686],
  anxiety:    [130, 100, 100, 100, 50, 28, 26, 710, 730],
  flow:       [70, 140, 100, 100, 50, 88, 84, 910, 930],
  fatigue:    [220, 130, 45, 100, 50, 50, 55, 860, 880],
  drowsy:     [180, 60, 90, 180, 50, 45, 45, 970, 990]
};
const PLAN = [
  ['focus', 65, 1.0, true], ['distracted', 65, 1.0, true], ['relaxed', 65, 1.0, false],
  ['stress', 90, 1.0, false], ['anxiety', 65, 1.0, false], ['flow', 65, 1.0, false],
  ['fatigue', 100, 3.0, true], ['drowsy', 40, 1.0, true]
];
const eng = new StateEngine({ baseline: StateEngine.defaultBaseline() });
let ts = 1.0, out = [];
for (const [ph, n, dt, task] of PLAN) {
  const [th, al, be, dl, ga, att, med, rr0, rr1] = P[ph];
  let last = null;
  for (let i = 0; i < n; i++) {
    // v3.4.1 修（与 _diff_py.py 同步）：非 flow 段原来 k=0 会把 att/med 钉死在 52/48，
    // 使 anxiety 段实际喂的不是焦虑帧（该段曾被判 neutral 而无人发现）。
    const k = ph === 'flow' ? i / Math.max(n - 1, 1) : 1;
    const attI = 52 + (att - 52) * k;
    const medI = 48 + (med - 48) * k;
    const f = new Frame({
      theta: th, alpha: al, beta: be, delta: dl, gamma: ga,
      attention: attI, meditation: medI, rr_ms: i % 2 === 0 ? rr0 : rr1,
      gyro: [0.01, 0.01, 0.01]
    });
    f.ts = ts; ts += dt;
    last = eng.processFrame(f, { task_active: task }).decision;
  }
  // v2.9.1：末态/conf 之外增加 I_f——原差分看不到疲劳积分轨迹偏差（曾掩盖 11 倍失配）
  // v3.4.1：再加 best 状态的门控余量（与 _diff_py.py 同步）
  const mBest = (last.margins && last.margins[last.state] != null) ? last.margins[last.state] : 0;
  out.push(`${ph}|${last.state}|${last.conf.toFixed(4)}|${eng.thetaIntegral.toFixed(2)}|${mBest.toFixed(4)}`);
}
console.log(out.join('\n'));
