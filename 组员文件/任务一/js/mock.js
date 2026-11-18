/* =============================================================================
 * mock.js — 模拟数据源（对应 02_brain_sim_tb.py 的帧结构 / _mock_frame()）
 * -----------------------------------------------------------------------------
 * 说明：
 *  1) focus / distracted / relaxed / stress / anxiety / flow 六项的 tb·al·hr·jitter
 *     与 01_state_engine.py::_mock_frame() 的 base 表完全一致（未改动）。
 *  2) drowsy / fatigue 为对本文件的**演示参数补充**（原 mock 未覆盖 v2.8 新增的
 *     两个 P1 状态），并额外补充 delta / beta 系数以支撑 δ 佐证与 BAR 判据。
 *     仅补数值，不改任何阈值/话术/判据。
 *  3) rr_jitter：相邻 RR 抖动幅度(ms)，越大 HRV 越高 → relaxed/anxiety 大、
 *     stress 小（RMSSD 骤降），与 engine 的 Stress 判据相呼应。
 * ========================================================================== */
(function (global) {
  'use strict';

  // phaseKey: {中文名, theta 系数 tb, alpha 系数 al, beta 系数 bf, delta 系数 dl,
  //            心率 hr, RR 抖动 jitter, 官方指数 att/med, 是否任务态}
  const PHASES = {
    focus:      { cn: '专注', tb: 0.55, al: 1.30, bf: 1.00, dl: 1.00, hr: 75, jitter: 30, att: 65, med: 55, task: true  },
    flow:       { cn: '心流', tb: 0.70, al: 1.40, bf: 1.00, dl: 1.00, hr: 66, jitter: 42, att: 72, med: 68, task: true  },
    distracted: { cn: '分心', tb: 1.60, al: 1.00, bf: 1.00, dl: 1.00, hr: 78, jitter: 30, att: 45, med: 45, task: true  },
    // 演示补充参数说明：
    //  fatigue 提高 tb（θ 主导更强）以缩短 I_f 累积到 300s 的等待；al 保持较高使 BAR 显著下降。
    //  drowsy 的 att/med 刻意取 48/52 —— 低于专注区但**不满足**焦虑双低判据
    //    （绝对值 <40 或 z(att/med)<-1.5），否则会被 PRIORITY 更高的 anxiety 抢占。
    fatigue:    { cn: '疲劳', tb: 2.60, al: 1.10, bf: 0.55, dl: 1.00, hr: 70, jitter: 40, att: 42, med: 48, task: true  },
    stress:     { cn: '压力', tb: 0.90, al: 1.10, bf: 1.00, dl: 1.00, hr: 88, jitter: 10, att: 40, med: 30, task: true  },
    anxiety:    { cn: '焦虑', tb: 1.30, al: 1.00, bf: 1.00, dl: 1.00, hr: 84, jitter: 30, att: 28, med: 26, task: true  },
    drowsy:     { cn: '嗜睡', tb: 2.50, al: 0.85, bf: 1.00, dl: 1.80, hr: 62, jitter: 45, att: 48, med: 52, task: false },
    relaxed:    { cn: '放松', tb: 1.00, al: 1.60, bf: 1.00, dl: 1.00, hr: 62, jitter: 50, att: 50, med: 70, task: false }
  };

  const RR_MIN = 300, RR_MAX = 2000;

  function makeRnd(seed) {
    // 可复现的伪随机（mulberry32），保证演示可重放
    let t = seed >>> 0;
    return function () {
      t += 0x6D2B79F5;
      let r = t;
      r = Math.imul(r ^ (r >>> 15), r | 1);
      r ^= r + Math.imul(r ^ (r >>> 7), r | 61);
      return ((r ^ (r >>> 14)) >>> 0) / 4294967296;
    };
  }

  function clamp(v, lo, hi) { return Math.min(hi, Math.max(lo, v)); }

  /* 单帧生成（与 _mock_frame 同构） */
  function makeFrame(phase, opts) {
    opts = opts || {};
    const p = PHASES[phase] || PHASES.focus;
    const rnd = opts.rnd || Math.random;
    const r = rnd;

    let att = p.att, med = p.med;
    if (opts.indexBoost) {  // 演示用心流双指数协同上升（指南 2.2 趋势）
      att = Math.min(88, p.att + opts.indexBoost);
      med = Math.min(84, p.med + opts.indexBoost);
    }
    const rr = clamp(60000 / p.hr + p.jitter * (r() - 0.5), RR_MIN, RR_MAX);

    return new global.BrainState.Frame({
      theta: Math.max(1, 100 * (p.tb + 0.2 * (r() - 0.5))),
      beta: Math.max(1, 100 * (p.bf + 0.2 * (r() - 0.5))),
      alpha: Math.max(1, 100 * (p.al + 0.2 * (r() - 0.5))),
      delta: Math.max(1, 100 * (p.dl + 0.2 * (r() - 0.5))),
      gamma: Math.max(1, 50 * (1 + 0.2 * (r() - 0.5))),
      rr_ms: rr,
      gyro: [0.02 * r(), 0.02 * r(), 0.02 * r()],
      attention: clamp(att, 0, 100),
      meditation: clamp(med, 0, 100)
    });
  }

  /* 偶发伪迹帧（真机日志中突发几十万~数百万；演示用于验证"打标不硬判"）
   * 放大 100×：确保相对平滑值的偏差稳定越过 8× 单频段门限（阈值在 [8,15] 区间），
   * 不受前一相位平滑值大小影响。 */
  function makeArtifactFrame(phase, opts) {
    const f = makeFrame(phase, opts);
    f.theta *= 100;   // 单频段突变 → 伪迹分支
    return f;
  }

  /* 自动轮播序列：按"由好到坏"编排，便于观察优先级仲裁与提醒分级。
   * 帧数按各状态的**窗口语义**配置：
   *   - stress 需 60s HRV 窗重新填满才从旧相位脱离（设计文档 §七 已明示），故给 70+ 帧；
   *   - fatigue 需 I_f 积分累积到 300s：dI/dt=(θ_norm−1.2)−I/300（触发阈值 1.2），时间常数 300s，
   *     纯自然累积需 ~10min。给 110 帧（配合演示预置 270s + θ_ref=55 对齐后快速越过阈值）；
   *   - anxiety 走官方指数绝对判据，帧级即时生效，32 帧足够。
   */
  const AUTO_TOUR = [
    { phase: 'focus', frames: 26 },
    { phase: 'flow', frames: 32 },
    { phase: 'distracted', frames: 26 },
    { phase: 'relaxed', frames: 24 },
    { phase: 'stress', frames: 72 },
    { phase: 'anxiety', frames: 32 },
    { phase: 'fatigue', frames: 110 },
    { phase: 'drowsy', frames: 28 }
  ];

  /* 供"粘贴数据"模式使用的示例帧（JSON Lines 格式说明） */
  const SAMPLE_JSONL = [
    { "attention": 65, "meditation": 55, "theta": 55, "beta": 100, "alpha": 130, "delta": 100, "gamma": 50, "rr_ms": 800 },
    { "attention": 63, "meditation": 54, "theta": 58, "beta": 102, "alpha": 128, "delta": 98,  "gamma": 51, "rr_ms": 812 },
    { "attention": 66, "meditation": 57, "theta": 52, "beta": 99,  "alpha": 133, "delta": 102, "gamma": 49, "rr_ms": 795 },
    { "attention": 30, "meditation": 26, "theta": 130, "beta": 100, "alpha": 100, "delta": 100, "gamma": 50, "rr_ms": 715 },
    { "attention": 29, "meditation": 25, "theta": 134, "beta": 98,  "alpha": 102, "delta": 99,  "gamma": 50, "rr_ms": 705 }
  ].map(o => JSON.stringify(o)).join('\n');

  global.MockSource = {
    PHASES, AUTO_TOUR, SAMPLE_JSONL,
    makeFrame, makeArtifactFrame, makeRnd, clamp,
    PHASE_KEYS: Object.keys(PHASES)
  };

})(window);
