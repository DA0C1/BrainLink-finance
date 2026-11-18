/* =============================================================================
 * engine.js — 状态判别引擎（state_engine.py v2.8 的 JavaScript 移植）
 * -----------------------------------------------------------------------------
 * 本文件是《状态判别建模设计 v2.8》/ 01_state_engine.py 的前端等效实现，
 * 仅做语言翻译与 UI 侧只读快照暴露（lastZ / lastFeats / cooldownLeft），
 * **不修改任何判据、阈值、话术与优先级**——话术改动须经组长确认（任务卡纪律）。
 *
 * 管道：原始帧 → 伪迹剔除 → EWMA 平滑 → 时间戳滑动窗 → 特征提取
 *      → z-score 个体偏离 → 线性加权评分 → 门控 → 优先级仲裁 → 时间确认
 *      → 反馈策略（含冷却/弱判别升级）→ 理智度记录
 * ========================================================================== */
(function (global) {
  'use strict';

  /* ------------------------------------------------------------------ 常量 */
  const K = {
    EWMA_ALPHA: 0.2,
    ARTIFACT_RATIO: 8.0,        // 单频段伪迹门限
    ARTIFACT_RATIO_RTB: 15.0,   // v2.8 比值门限独立放宽（防真实状态切换被误杀）
    /* v3.1 β+γ 肌电专用门（默认关；verify_hw V4 判"需启用"时改 true，与 Python 同步） */
    ARTIFACT_EMG_GATE: false,
    ARTIFACT_EMG_RATIO: 4.0,
    ARTIFACT_EMG_THETA_TOL: 0.35,
    RR_MIN_MS: 300, RR_MAX_MS: 2000,

    Z_CONC: -1.0,               // 专注 z(R_tb)
    Z_RMSSD_NO_STRESS: -1.0,    // 专注排除法（v2.7.1）
    Z_DIST: 1.0,                // 分心
    Z_RELAX_A: 1.5,             // 放松（v2.7）
    Z_STRESS: -1.5,             // 压力（R6 / Stephenson 2021）
    Z_STILL_CAP: 1.0,           // 体动平静门控

    WEAK_CONF: 0.7,             // 弱判别（S4）
    WEAK_ESCALATE_FRAMES: 60,   // 弱判别持续帧数升级
    WEAK_ESCALATE_CD: 300,      // 升级冷却（s）

    ANX_ATT_MAX: 40.0, ANX_MED_MAX: 40.0, ANX_Z: -1.5,
    FLOW_ATT_MIN: 60.0, FLOW_MED_MIN: 60.0, FLOW_HOLD_FRAMES: 5, FLOW_Z: 1.5,
    SYNC_WIN: 10, SYNC_MIN: 0.6, MIN_SYNC_FRAMES: 5,

    IF_THETA_NORM: 1.2, IF_HOLD_S: 300,
    // v2.9.1 疲劳积分口径（与 Python state_engine.py 逐位对齐；旧 FATIGUE_HALF_LIFE_S 已废弃）
    IF_REF_EXCESS: 0.3,       // θ_norm 超阈 0.3 倍 → 1.0 等效秒/秒
    IF_RATE_MAX: 2.0,         // 单帧累积速率上限
    IF_RECOVER_HALF_S: 600,   // θ 回落阈下时 I_f 半衰期
    IF_MAX_DT_S: 60,          // 单帧积分时长上限（断帧/休眠恢复防"一帧刷满"）
    // S6 迁移时间窗：设计稿原定 60s，实现由 PRIORITY（drowsy 先于 fatigue）等效达成，
    // 本常量保留为接口预留（与 Python 同名同值，供 verify_parity 常量表比对）
    SLEEP_MIGRATE_S: 60,

    Z_FATIGUE_BAR: -1.0, Z_DROWSY_TAR: 1.5, Z_DROWSY_DELTA: 1.0,

    ADAPT_ALPHA: 0.001, ADAPT_MAX_DRIFT: 0.5, ADAPT_EVAL_S: 60,
    CALIB_ARTIFACT_MAX: 0.30,   // v2.9.2 校准伪迹率上限（§六 第 5 条：超限判校准失败）

    WINDOW_EEG_S: 10, WINDOW_HRV_S: 60, WINDOW_GYRO_S: 30,
    HIT_RATIO: 0.6, DEBOUNCE_S: 5.0,
    CONF_MIN: 0.6, CONF_MAX: 0.95,

    COOL_DOWN: {
      stress: 60, anxiety: 60, distracted: 60, fatigue: 60, drowsy: 60,
      focus: 0, flow: 0, relaxed: 0, neutral: 0
    },

    // 优先级仲裁（可由 UI 拖拽重排）
    PRIORITY: ['stress', 'anxiety', 'drowsy', 'fatigue', 'distracted', 'flow', 'focus', 'relaxed'],

    // 状态元数据（中文名/图标/默认反馈级别）——纯展示层，不影响判别
    STATE_META: {
      stress:     { cn: '压力',     icon: '⚡', level: 'calm',   family: 'alert'  },
      anxiety:    { cn: '焦虑',     icon: '🌫️', level: 'calm',   family: 'alert'  },
      drowsy:     { cn: '嗜睡',     icon: '😴', level: 'calm',   family: 'alert'  },
      fatigue:    { cn: '疲劳',     icon: '🪫', level: 'mild',   family: 'warn'   },
      distracted: { cn: '分心',     icon: '💭', level: 'mild',   family: 'warn'   },
      flow:       { cn: '心流',     icon: '🌊', level: 'silent', family: 'good'   },
      focus:      { cn: '专注',     icon: '🎯', level: 'silent', family: 'good'   },
      relaxed:    { cn: '放松',     icon: '🍃', level: 'silent', family: 'good'   },
      neutral:    { cn: '中性/未确认', icon: '⏳', level: 'silent', family: 'idle' }
    },

    // 反馈话术表（v2.8 原文，一字不改）
    ADVICE: {
      focus:      '维持当前节奏',
      flow:       '心流状态，保持当前节奏',
      relaxed:    '无干预',
      neutral:    '数据不足/无判别，等待窗口填充',
      distracted: '回到任务，注意关键信息',
      anxiety:    '情绪扰动高：暂缓交易决策，先做 3 次深呼吸（Laborde 2022）',
      stress:     '3 次深呼吸（Laborde 2022）',
      fatigue:    '疲劳累积：建议短暂休息（含眼部放松）后继续决策',
      drowsy:     '嗜睡迹象：建议起身活动或暂停交易决策（清醒后再回）'
    },

    // 冲动操作口径（演示用）：非冷静态下的决策记为冲动
    IMPULSIVE_STATES: ['stress', 'anxiety', 'drowsy', 'fatigue', 'distracted']
  };

  /* ---------------------------------------------------------------- 工具 */
  function clamp(v, lo, hi) {
    if (!Number.isFinite(v)) return lo;
    return Math.min(hi, Math.max(lo, v));
  }
  function safeDiv(a, b) { return (b <= 0) ? Infinity : a / b; }
  function isF(v) { return Number.isFinite(v); }

  /* ------------------------------------------------------------ 数据结构 */
  class Frame {
    constructor(o) {
      o = o || {};
      this.attention = num(o.attention, 50);
      this.meditation = num(o.meditation, 50);
      this.delta = num(o.delta, 100);
      this.theta = num(o.theta, 100);
      this.alpha = num(o.alpha, 100);
      this.beta = num(o.beta, 100);
      this.gamma = num(o.gamma, 50);
      this.rr_ms = o.rr_ms == null ? null : Number(o.rr_ms);
      // v3.3：一次 onRR 的 3 个连续间期。缺这个字段时只能拿 rr_ms 单值跨帧差分，
      // 实测 RMSSD 由 24.14ms 虚高到 38.38ms（+59%）→ z(RMSSD) 偏高 → stress 更难判出。
      this.rr_batch = Array.isArray(o.rr_batch)
        ? o.rr_batch.map(Number).filter(Number.isFinite)
        : null;
      this.heart = o.heart == null ? null : Number(o.heart);
      this.gyro = Array.isArray(o.gyro) ? o.gyro.slice(0, 3) : [0, 0, 0];
      this.ts = o.ts == null ? null : Number(o.ts);
    }
  }
  function num(v, d) { const n = Number(v); return Number.isFinite(n) ? n : d; }

  class BaselineStats {
    constructor(o) {
      o = o || {};
      this.mu = num(o.mu, 0);
      this.sigma = num(o.sigma, 1);
      this.sigma_min = num(o.sigma_min, 0.1);
      this.log_flag = !!o.log_flag;
    }
    clone() { return new BaselineStats(this); }
  }

  class Decision {
    constructor(state, conf, scores, gates) {
      this.state = state || 'neutral';
      this.conf = conf == null ? K.CONF_MIN : conf;
      this.scores = scores || {};
      this.gates = gates || {};
    }
  }
  class Feedback {
    constructor(level, action, cooldown_left) {
      this.level = level || 'silent';
      this.action = action || '';
      this.cooldown_left = cooldown_left || 0;
    }
  }

  /* ------------------------------------------------------------ 状态引擎 */
  class StateEngine {
    constructor(opts) {
      opts = opts || {};
      this.smooth = {};
      this.rrBuf = [];      // [rr, batch, ts]
      this.eegBuf = [];
      this.gyroBuf = [];
      this.gyroTs = [];

      this.baseline = {};
      const base = opts.baseline || StateEngine.defaultBaseline();
      for (const k in base) {
        this.baseline[k] = (base[k] instanceof BaselineStats) ? base[k].clone()
          : (typeof base[k] === 'number' ? base[k] : base[k]);
      }
      this._assertBaseline();

      this.W = opts.weights || StateEngine.defaultWeights();
      this.priority = (opts.priority && opts.priority.length === K.PRIORITY.length)
        ? opts.priority.slice() : K.PRIORITY.slice();

      let vt = 0;
      this._clock = opts.clock || (function () { vt += 1; return vt; });

      this.lastDecision = null;
      this.lastFeedback = new Feedback();
      this.lastFeats = null;
      this.lastZ = {};

      this._hitBuf = [];
      this._lastActionTime = {};
      this.attHist = []; this.medHist = [];
      this.syncRatio = 0; this.syncOk = false; this.syncLatched = false;
      this.flowHold = 0;
      this.weakCount = 0;
      this._lastWeakEscalate = -Infinity;
      this._heartEwma = null;
      this._rrBatch = 0;

      this._thetaRef = null;
      if (typeof this.baseline.theta_ref === 'number' && Number.isFinite(this.baseline.theta_ref)) {
        this._thetaRef = this.baseline.theta_ref;
      }
      this._thetaInt = 0;
      this._lastThetaTs = null;

      this.adaptEnabled = false;     // v2.8 #9 默认关闭
      this._lastAdaptTs = -Infinity;

      this.artifactCount = 0;
      this.frameCount = 0;

      this.log = { decisions: [], interventions: [], impulsive: 0, total: 0 };
      // A/B 对照分组（演示用，与引擎判据无关）
      this.abStats = { A: { total: 0, impulsive: 0 }, B: { total: 0, impulsive: 0 } };
      this.abSegment = 'A';
    }

    /* ---- 可调参数（UI 拖拽/滑块可改，仅影响实例）---- */
    get cfg() { return this._cfg || (this._cfg = { cooldownScale: 1, debounceS: K.DEBOUNCE_S, hitRatio: K.HIT_RATIO, weakConf: K.WEAK_CONF }); }

    get calmness() {
      return this.log.total ? 1 - (this.log.impulsive / this.log.total) : 1;
    }
    get thetaIntegral() { return this._thetaInt; }
    set thetaIntegral(v) { this._thetaInt = Math.max(0, num(v, 0)); }
    get thetaRef() { return this._thetaRef; }

    cooldownLeft(state, now) {
      const last = this._lastActionTime[state] || 0;
      const cd = (K.COOL_DOWN[state] || 0) * (this.cfg.cooldownScale || 1);
      if (!cd || !last) return 0;
      return Math.max(0, cd - (now - last));
    }
    cooldownOf(state) { return (K.COOL_DOWN[state] || 0) * (this.cfg.cooldownScale || 1); }

    /* ---- 基线必填校验（v2.8.2）---- */
    _assertBaseline() {
      const need = ['R_tb', 'R_ba', 'alpha', 'A_rel', 'HR', 'RMSSD_60', 'still', 'delta'];
      const missing = need.filter(n => !(this.baseline[n] instanceof BaselineStats));
      if (missing.length) {
        throw new Error('基线缺失关键特征: ' + missing.join(', ') + '（校准协议必填，含 delta——嗜睡 δ 佐证依赖）');
      }
    }

    /* ---- 预处理 ---- */
    _ewma(key, x) {
      if (!(key in this.smooth)) this.smooth[key] = x;
      else this.smooth[key] = K.EWMA_ALPHA * x + (1 - K.EWMA_ALPHA) * this.smooth[key];
      return this.smooth[key];
    }

    _artifact(frame) {
      if (this._emgArtifact(frame)) return true;   // v3.1 可选 β+γ 肌电门（默认关）
      const keys = ['delta', 'theta', 'alpha', 'beta', 'gamma'];
      for (const key of keys) {
        const s = this.smooth[key];
        if (isF(s) && s > 0) {
          if (Math.abs(frame[key] / s - 1.0) > K.ARTIFACT_RATIO) return true;
        }
      }
      const stb = this.smooth.R_tb;
      if (isF(stb) && stb > 0) {
        const tb = safeDiv(frame.theta, frame.beta);
        if (Math.abs(tb / stb - 1.0) > K.ARTIFACT_RATIO_RTB) return true;
      }
      return false;
    }

    /* v3.1：β+γ 同升且 θ 不动 → 肌电伪迹。参照取 B 段（calibrateFromFrames 产出），
       不用 smooth——平滑值会被持续肌电抬走，以它为基线永远检不出来。 */
    _emgArtifact(frame) {
      if (!K.ARTIFACT_EMG_GATE) return false;
      const br = this.baseline.beta_ref, gr = this.baseline.gamma_ref, tr = this.baseline.theta_ref;
      if (!isF(br) || !isF(gr) || !isF(tr) || br <= 0 || gr <= 0 || tr <= 0) return false;
      return frame.beta > K.ARTIFACT_EMG_RATIO * br
        && frame.gamma > K.ARTIFACT_EMG_RATIO * gr
        && Math.abs(frame.theta - tr) < K.ARTIFACT_EMG_THETA_TOL * tr;
    }

    /* ---- 派生同步率（指南 2.2；注意：官方无独立同步率字段，此为派生口径）---- */
    _updateSync(frame) {
      this.attHist.push(frame.attention);
      this.medHist.push(frame.meditation);
      if (this.attHist.length > K.SYNC_WIN) { this.attHist.shift(); this.medHist.shift(); }
      let same = 0, total = 0;
      for (let i = 1; i < this.attHist.length; i++) {
        const da = this.attHist[i] - this.attHist[i - 1];
        const dm = this.medHist[i] - this.medHist[i - 1];
        if (da !== 0 && dm !== 0) {
          total++;
          if ((da > 0) === (dm > 0)) same++;
        }
      }
      this.syncRatio = total > 0 ? same / total : 0;
      return this.attHist.length >= K.SYNC_WIN && total >= K.MIN_SYNC_FRAMES && this.syncRatio >= K.SYNC_MIN;
    }

    /* ---- 心流双判据（v2.6）---- */
    _indexHigh(frame) {
      if (frame.attention >= K.FLOW_ATT_MIN && frame.meditation >= K.FLOW_MED_MIN) return true;
      if (this.baseline.att && this.baseline.med) {
        const za = this._z(frame.attention, this.baseline.att);
        const zm = this._z(frame.meditation, this.baseline.med);
        if (isF(za) && isF(zm) && za > K.FLOW_Z && zm > K.FLOW_Z) return true;
      }
      return false;
    }

    /* ---- RMSSD（v2.6 同批内差分；不足 8 返回 NaN）---- */
    static _rmssd(rrList) {
      if (rrList.length < 8) return NaN;
      const diffs = [];
      for (let i = 0; i < rrList.length - 1; i++) {
        const r0 = rrList[i], r1 = rrList[i + 1];
        if (r0[1] != null && r1[1] != null && r0[1] !== r1[1]) continue; // 跨回调批次断开
        diffs.push(r1[0] - r0[0]);
      }
      if (!diffs.length) return NaN;
      return Math.sqrt(diffs.reduce((a, d) => a + d * d, 0) / diffs.length);
    }

    /* ---- 时间戳滑动窗（v2.7 #7）---- */
    _pushWindow(frame, ts) {
      this.eegBuf.push(frame);
      while (this.eegBuf.length && ts - (this.eegBuf[0].ts || ts) > K.WINDOW_EEG_S) this.eegBuf.shift();
      // v3.3：优先整批入缓冲（与 Python _rmssd 的"同批内差分"语义对齐）。
      // 批次号用自增序号：同批内才差分，跨批直接 continue。
      if (Array.isArray(frame.rr_batch) && frame.rr_batch.length) {
        this._rrBatchSeq = (this._rrBatchSeq || 0) + 1;
        for (const rr of frame.rr_batch) {
          if (rr >= K.RR_MIN_MS && rr <= K.RR_MAX_MS) this.rrBuf.push([rr, this._rrBatchSeq, ts]);
        }
      } else if (frame.rr_ms != null) {
        // 降级路径（旧 CSV / mock 单值）：batch 记 null → 跨帧差分，与历史行为一致
        this.rrBuf.push([frame.rr_ms, null, ts]);
      }
      while (this.rrBuf.length && ts - this.rrBuf[0][2] > K.WINDOW_HRV_S) this.rrBuf.shift();
      this.gyroBuf.push(frame.gyro);
      this.gyroTs.push(ts);
      while (this.gyroBuf.length && ts - this.gyroTs[0] > K.WINDOW_GYRO_S) { this.gyroBuf.shift(); this.gyroTs.shift(); }
    }

    pushRR(rr, batch, ts) {
      if (!(rr >= K.RR_MIN_MS && rr <= K.RR_MAX_MS)) return;
      const t = (ts == null) ? this._clock() : ts;
      this.rrBuf.push([rr, batch == null ? null : batch, t]);
      while (this.rrBuf.length && t - this.rrBuf[0][2] > K.WINDOW_HRV_S) this.rrBuf.shift();
    }

    /* ---- 特征提取 ---- */
    _extract() {
      const f = { R_tb: 0, R_ba: 0, TAR: 0, A_rel: 0, HR: NaN, RMSSD_60: NaN, still: NaN, alpha: NaN, delta: NaN };
      const n = this.eegBuf.length;
      if (!n) return f;
      let th = 0, al = 0, be = 0, dl = 0;
      for (const x of this.eegBuf) { th += x.theta; al += x.alpha; be += x.beta; dl += x.delta; }
      th /= n; al /= n; be /= n; dl /= n;

      f.R_tb = safeDiv(th, be);
      f.R_ba = safeDiv(be, th + al);
      f.TAR = safeDiv(th, al);
      f.alpha = al; f.delta = dl;   // v2.8.3：补 alpha/delta 特征字段，使 _maybeAdapt 与 Python 覆盖范围一致（9 项）

      const baseAlpha = this.baseline.alpha ? this.baseline.alpha.mu : 0;
      f.A_rel = baseAlpha ? (al - baseAlpha) / baseAlpha : 0;

      const last = this.eegBuf[n - 1];
      const lastHeart = (last && last.heart != null && isF(last.heart)) ? last.heart : null;
      if (lastHeart != null) {
        this._heartEwma = (this._heartEwma == null) ? lastHeart
          : K.EWMA_ALPHA * lastHeart + (1 - K.EWMA_ALPHA) * this._heartEwma;
        f.HR = this._heartEwma;
      } else if (this.rrBuf.length) {
        const meanRR = this.rrBuf.reduce((a, x) => a + x[0], 0) / this.rrBuf.length;
        f.HR = meanRR ? 60000 / meanRR : NaN;
      }

      f.RMSSD_60 = this.rrBuf.length ? StateEngine._rmssd(this.rrBuf) : NaN;

      if (this.gyroBuf.length) {
        let s = 0;
        for (const g of this.gyroBuf) s += Math.abs(g[0]) + Math.abs(g[1]) + Math.abs(g[2]);
        f.still = s / this.gyroBuf.length;
      }
      return f;
    }

    /* ---- z-score（含 σ_min 下限保护）---- */
    _z(x, stat) {
      if (!(stat instanceof BaselineStats)) return NaN;
      const v = (stat.log_flag && x > 0) ? Math.log(x) : x;
      const sigma = Math.max(stat.sigma, stat.sigma_min);
      return sigma ? (v - stat.mu) / sigma : 0;
    }

    /* ---- 评分 + 门控 ---- */
    _scores(feats, ctx, frame) {
      const W = this.W, z = {};
      const names = ['R_tb', 'R_ba', 'TAR', 'A_rel', 'HR', 'RMSSD_60', 'still'];
      for (const nme of names) {
        const stat = this.baseline[nme];
        if (!stat) continue;
        const v = feats[nme];
        z[nme] = isF(v) ? this._z(v, stat) : NaN;
      }
      if (frame) {
        const st = this.baseline.delta;
        if (st) z.delta = isF(frame.delta) ? this._z(frame.delta, st) : NaN;
      }
      let za = NaN, zm = NaN;
      if (frame && this.baseline.att && this.baseline.med) {
        za = this._z(frame.attention, this.baseline.att);
        zm = this._z(frame.meditation, this.baseline.med);
      }
      const taskActive = !!(ctx && ctx.task_active);
      const gz = (nme, dflt) => { const v = z[nme]; return (v === undefined || Number.isNaN(v)) ? dflt : v; };
      const fin = (v) => isF(v);

      const gates = {
        focus: gz('R_tb', 0) < K.Z_CONC
          && (!fin(gz('RMSSD_60', 0)) || gz('RMSSD_60', 0) > K.Z_RMSSD_NO_STRESS)
          && (!fin(gz('still', 0)) || gz('still', 0) < K.Z_STILL_CAP),
        distracted: gz('R_tb', 0) > K.Z_DIST && taskActive,
        relaxed: gz('A_rel', 0) > K.Z_RELAX_A,
        stress: gz('RMSSD_60', 0) < K.Z_STRESS
          && (!fin(gz('still', 0)) || gz('still', 0) < K.Z_STILL_CAP),
        anxiety: !!(frame && frame.attention < K.ANX_ATT_MAX && frame.meditation < K.ANX_MED_MAX)
          || (fin(za) && fin(zm) && za < K.ANX_Z && zm < K.ANX_Z),
        flow: !!frame && this._indexHigh(frame) && this.flowHold >= K.FLOW_HOLD_FRAMES
          && this.syncLatched && (!fin(gz('R_tb', 0)) || gz('R_tb', 0) < K.Z_DIST),
        fatigue: gz('R_ba', 0) < K.Z_FATIGUE_BAR && this._thetaInt >= K.IF_HOLD_S
          && (!fin(gz('RMSSD_60', 0)) || gz('RMSSD_60', 0) > K.Z_STRESS),
        drowsy: gz('TAR', 0) > K.Z_DROWSY_TAR && gz('delta', 0) > K.Z_DROWSY_DELTA
      };

      const nz = (v) => isF(v) ? v : 0;   // nan 置 0 不阻断（v2.6.2）
      const scores = {
        focus: W.focus.R_tb * z.R_tb + W.focus.R_ba * z.R_ba,
        distracted: W.distracted.R_tb * z.R_tb
          + W.distracted.A_rel * nz(z.A_rel) + W.distracted.still * nz(z.still),
        relaxed: W.relaxed.A_rel * z.A_rel
          + W.relaxed.RMSSD_60 * nz(z.RMSSD_60) + W.relaxed.still * nz(z.still),
        stress: W.stress.RMSSD_60 * z.RMSSD_60 + W.stress.HR * nz(z.HR),
        anxiety: Math.max(
          frame ? (W.anxiety.att * (K.ANX_ATT_MAX - frame.attention)
            + W.anxiety.med * (K.ANX_MED_MAX - frame.meditation)) : -Infinity,
          (fin(za) && fin(zm)) ? (W.anxiety.att * (-K.ANX_Z - za) + W.anxiety.med * (-K.ANX_Z - zm)) : -Infinity
        ),
        flow: frame ? (W.flow.att * (frame.attention - K.FLOW_ATT_MIN)
          + W.flow.med * (frame.meditation - K.FLOW_MED_MIN)
          + W.flow.sync * (this.syncRatio - K.SYNC_MIN)) : -Infinity,
        fatigue: W.fatigue.R_ba * z.R_ba + W.fatigue.I_f * Math.min(this._thetaInt / K.IF_HOLD_S, 1.0),
        drowsy: W.drowsy.TAR * z.TAR + W.drowsy.delta * nz(z.delta)
      };

      for (const s of Object.keys(scores)) {
        if (!gates[s] || !isF(scores[s])) scores[s] = -Infinity;
      }
      this.lastZ = z;
      return { scores, gates };
    }

    /* ---- 纯仲裁（UI 可重算：优先级拖拽后即时预览）---- */
    arbitrate(scores, gates) {
      const finite = {};
      for (const s of Object.keys(scores)) if (scores[s] !== -Infinity) finite[s] = scores[s];
      const keys = Object.keys(finite);
      if (!keys.length) return { state: 'neutral', conf: K.CONF_MIN };
      let best = null;
      for (const s of this.priority) if (keys.indexOf(s) >= 0) { best = s; break; }
      if (best == null) best = keys.reduce((a, b) => finite[b] > finite[a] ? b : a);

      if (best === 'fatigue' || best === 'drowsy') {
        const wsum = Object.keys(this.W[best]).reduce((a, k) => a + Math.abs(this.W[best][k]), 0);
        if (wsum > 0) {
          const conf = K.CONF_MIN + 0.35 * (finite[best] / wsum);
          return { state: best, conf: clamp(conf, K.CONF_MIN, K.CONF_MAX) };
        }
      }
      const others = keys.filter(s => s !== best).map(s => finite[s]).sort((a, b) => b - a);
      const second = others.length ? others[0] : 0;
      const conf = K.CONF_MIN + 0.35 * (finite[best] - second)
        / (Math.abs(finite[best]) + Math.abs(second) + 1e-9);
      return { state: best, conf: clamp(conf, K.CONF_MIN, K.CONF_MAX) };
    }

    /* ---- 反馈策略（含冷却 / 弱判别升级）---- */
    feedbackPolicy(state, conf, now) {
      const weakConf = clamp(this.cfg.weakConf, K.CONF_MIN, K.CONF_MAX);
      const nowv = isF(now) ? now : this._clock();

      if (conf < weakConf) {
        if (state === 'neutral') {
          return new Feedback('silent', '数据不足/无判别，等待窗口填充');
        }
        this.weakCount += 1;
        if (this.weakCount >= K.WEAK_ESCALATE_FRAMES) {
          if ((nowv - this._lastWeakEscalate) >= K.WEAK_ESCALATE_CD) {
            this._lastWeakEscalate = nowv;
            this.weakCount = 0;
            return new Feedback('mild', `状态趋近 ${state}（置信度低，持续观察中），建议短暂关注`);
          }
          this.weakCount = 0;
        }
        return new Feedback('silent', '弱判别，仅记录状态');
      }
      this.weakCount = 0;

      const last = this._lastActionTime[state] || 0;
      const cd = this.cooldownOf(state);
      if (cd && last && (nowv - last) < cd) {
        return new Feedback('silent', '冷却中', cd - (nowv - last));
      }
      this._lastActionTime[state] = nowv;
      const table = {
        focus: new Feedback('silent', K.ADVICE.focus),
        flow: new Feedback('silent', K.ADVICE.flow),
        relaxed: new Feedback('silent', K.ADVICE.relaxed),
        neutral: new Feedback('silent', K.ADVICE.neutral),
        distracted: new Feedback('mild', K.ADVICE.distracted),
        anxiety: new Feedback('calm', K.ADVICE.anxiety),
        stress: new Feedback('calm', K.ADVICE.stress),
        fatigue: new Feedback('mild', K.ADVICE.fatigue),
        drowsy: new Feedback('calm', K.ADVICE.drowsy)
      };
      return table[state] || new Feedback('silent', '');
    }

    /* ---- 理智度记录 ---- */
    rationalityLog(isImpulsive, decision) {
      this.log.total += 1;
      if (isImpulsive) this.log.impulsive += 1;
      this.log.decisions.push(decision ? decision.state : 'neutral');
      const seg = this.abStats[this.abSegment] || this.abStats.A;
      seg.total += 1;
      if (isImpulsive) seg.impulsive += 1;
    }
    segmentCalmness(seg) {
      const s = this.abStats[seg];
      return s && s.total ? 1 - s.impulsive / s.total : null;
    }

    /* ---- 时间确认（v2.7 #8）---- */
    _confirm(state, ts) {
      if (state === 'neutral') return state;
      this._hitBuf.push([state, ts]);
      const win = clamp(this.cfg.debounceS, 0.5, 60);
      while (this._hitBuf.length && ts - this._hitBuf[0][1] > win) this._hitBuf.shift();
      if (this._hitBuf.length < 2) return state;
      const hits = this._hitBuf.reduce((a, x) => a + (x[0] === state ? 1 : 0), 0);
      return (hits / this._hitBuf.length >= clamp(this.cfg.hitRatio, 0, 1)) ? state : 'neutral';
    }

    /* ---- 基线自适应（默认关闭）---- */
    _maybeAdapt(feats, gates, ts) {
      if (!this.adaptEnabled || !gates) return;
      if (ts - this._lastAdaptTs < K.ADAPT_EVAL_S) return;
      if (Object.keys(gates).some(k => gates[k])) return;
      this._lastAdaptTs = ts;
      for (const name of Object.keys(this.baseline)) {
        const stat = this.baseline[name];
        if (!(stat instanceof BaselineStats)) continue;
        if (stat.sigma <= 0 || name === 'att' || name === 'med') continue;
        const v = feats[name];
        if (v == null || !isF(v)) continue;
        let mu = stat.mu + K.ADAPT_ALPHA * (v - stat.mu);
        mu = clamp(mu, stat.mu - K.ADAPT_MAX_DRIFT * stat.sigma, stat.mu + K.ADAPT_MAX_DRIFT * stat.sigma);
        stat.mu = mu;
      }
    }

    /* ---- 主入口 ---- */
    processFrame(frame, ctx) {
      const ts = (frame.ts != null && frame.ts > 0) ? frame.ts : this._clock();
      if (frame.ts == null || frame.ts <= 0) frame.ts = ts;
      this.frameCount += 1;

      if (this._artifact(frame)) {
        this.artifactCount += 1;
        if (this._thetaRef) this._lastThetaTs = ts;
        this.lastFeats = this.lastFeats || null;
        return { decision: this.lastDecision || new Decision('neutral', K.CONF_MIN),
          feedback: this.lastFeedback, artifact: true, ts };
      }
      ['delta', 'theta', 'alpha', 'beta', 'gamma'].forEach(k => this._ewma(k, frame[k]));
      this._pushWindow(frame, ts);

      const feats = this._extract();
      this.lastFeats = feats;
      if (isF(feats.R_tb)) this._ewma('R_tb', feats.R_tb);

      // 疲劳积分 I_f（θ_ref 冻结基线；v2.9.1 幅度加权 + 可恢复，与 Python 逐位对齐）
      //   rate = (θ_norm−1.2)/0.3 截断到 [0, 2.0]，I_f += rate·dt；
      //   θ 回落至阈下时 I_f 半衰 600s——疲劳是"暴露累积 + 恢复"，不能单调锁存。
      //   v2.9.0 的纯时长版与本次幅度加权版均须同步 JS，否则双引擎 I_f 偏差达 11 倍（实测）。
      if (this._thetaRef && this._thetaRef > 0) {
        const thetaNorm = frame.theta / this._thetaRef;
        let dt = ts - (this._lastThetaTs != null ? this._lastThetaTs : ts);
        if (dt > K.IF_MAX_DT_S) dt = K.IF_MAX_DT_S;   // 断帧保护（与 Python 一致）
        if (dt > 0) {
          const rate = (thetaNorm - K.IF_THETA_NORM) / K.IF_REF_EXCESS;
          if (rate > 0) this._thetaInt += Math.min(rate, K.IF_RATE_MAX) * dt;
          else if (this._thetaInt > 0) {
            this._thetaInt *= Math.exp(-Math.LN2 * dt / K.IF_RECOVER_HALF_S);
            if (this._thetaInt < 1e-6) this._thetaInt = 0;
          }
        }
        this._lastThetaTs = ts;
      }

      this.syncOk = this._updateSync(frame);
      this.flowHold = this._indexHigh(frame) ? this.flowHold + 1 : 0;
      if (this.syncOk) this.syncLatched = true;
      else if (this.flowHold === 0) this.syncLatched = false;

      const { scores, gates } = this._scores(feats, ctx, frame);
      this._maybeAdapt(feats, gates, ts);

      const arb = this.arbitrate(scores, gates);
      let state = this._confirm(arb.state, ts);
      let conf = arb.conf;
      if (state === 'neutral') conf = K.CONF_MIN;

      const dec = new Decision(state, conf, scores, gates);
      this.lastDecision = dec;
      const fb = this.feedbackPolicy(state, conf, ts);
      this.lastFeedback = fb;
      return { decision: dec, feedback: fb, feats, artifact: false, ts };
    }

    reset() {
      this.smooth = {}; this.rrBuf = []; this.eegBuf = []; this.gyroBuf = []; this.gyroTs = [];
      this._hitBuf = []; this._lastActionTime = {};
      this.attHist = []; this.medHist = [];
      this.syncRatio = 0; this.syncOk = false; this.syncLatched = false; this.flowHold = 0;
      this.weakCount = 0; this._lastWeakEscalate = -Infinity; this._heartEwma = null;
      this._thetaInt = 0; this._lastThetaTs = null; this._lastAdaptTs = -Infinity;
      this.lastDecision = null; this.lastFeedback = new Feedback();
      this.lastFeats = null; this.lastZ = {};
      this.artifactCount = 0; this.frameCount = 0;
      this.log = { decisions: [], interventions: [], impulsive: 0, total: 0 };
      this.abStats = { A: { total: 0, impulsive: 0 }, B: { total: 0, impulsive: 0 } };
    }

    /* ---- 默认基线（calibrate_from_mock 等价）---- */
    static defaultBaseline() {
      return {
        R_tb: new BaselineStats({ mu: 1.0, sigma: 0.3, sigma_min: 0.1 }),
        R_ba: new BaselineStats({ mu: 0.5, sigma: 0.2, sigma_min: 0.05 }),
        TAR: new BaselineStats({ mu: 0.8, sigma: 0.3, sigma_min: 0.1 }),
        alpha: new BaselineStats({ mu: 100.0, sigma: 20.0, sigma_min: 5.0 }),
        A_rel: new BaselineStats({ mu: 0.0, sigma: 0.3, sigma_min: 0.06 }),
        HR: new BaselineStats({ mu: 75.0, sigma: 6.0, sigma_min: 1.0 }),
        RMSSD_60: new BaselineStats({ mu: 15.0, sigma: 5.0, sigma_min: 1.0, log_flag: false }),
        still: new BaselineStats({ mu: 0.5, sigma: 0.2, sigma_min: 0.05 }),
        delta: new BaselineStats({ mu: 100.0, sigma: 20.0, sigma_min: 5.0 }),
        att: new BaselineStats({ mu: 60.0, sigma: 15.0, sigma_min: 3.0 }),
        med: new BaselineStats({ mu: 60.0, sigma: 15.0, sigma_min: 3.0 }),
        theta_ref: 55.0,       // v2.8.5 对齐 Python calibrate_from_mock（A 段 θ 中位数≈55，focus 场景）——此前 100 致 I_f 积分速率差 ~2 倍
        theta_ref_src: 'default',
        /* v3.1 β/γ 参照（肌电专用门用）；未校准时为 null，门自动失效 */
        beta_ref: null,
        gamma_ref: null
      };
    }

    /* ---- v2.9.2 §六 校准协议（与 Python calibrate_from_frames 1:1）----
       输入 frames: [{ tag: 'A'|'B'|'C', frame }]
         A 闭眼静息 2min：δ 基线（嗜睡佐证）+ theta_rest（仅供参考）
         B 任务参照 60s：R_tb/R_ba/TAR/alpha/A_rel/HR/RMSSD/still 基线 + **theta_ref**
         C 放松 2min：仅用于校准质量校验（放松锚点可分性），不覆盖阈值
       θ_ref 必须取 B 段：A 段闭眼 θ 系统性高于睁眼任务，取 A 会让 θ_norm 恒 <1、
       I_f 永不积分（校准后疲劳不可达——Python 侧 v2.9.2 已修，此处同步，勿回退）。 */
    static calibrateFromFrames(frames) {
      const base = StateEngine.defaultBaseline();
      const seg = { A: [], B: [], C: [] };
      for (const it of (frames || [])) {
        if (!it || !it.frame) continue;
        const tag = it.tag;
        if (!seg[tag]) seg[tag] = [];
        seg[tag].push(it.frame);
      }
      const median = (arr) => {
        if (!arr.length) return null;
        const s = arr.slice().sort((a, b) => a - b);
        return s[Math.floor(s.length / 2)];
      };
      const robustSigma = (vals, stat) => {
        const s = vals.slice().sort((a, b) => a - b);
        const q1 = s[Math.floor(s.length / 4)];
        const q3 = s[Math.floor(s.length * 3 / 4)];
        const med = s[Math.floor(s.length / 2)];
        const mad = Math.max(med - q1, q3 - med);
        return Math.max(mad * 1.4826, stat.sigma_min);
      };
      const safeRatio = (a, b) => (b <= 0 ? Infinity : a / b);

      const plan = {
        B: ['R_tb', 'R_ba', 'TAR', 'alpha', 'A_rel', 'HR', 'RMSSD_60', 'still'],
        A: ['delta']
      };
      for (const tag of Object.keys(plan)) {
        const fs = seg[tag] || [];
        if (fs.length < 10) continue;                 // 段样本不足 → 保留默认（报告会告警）
        for (const name of plan[tag]) {
          if (name === 'RMSSD_60') continue;          // 需批内/相邻帧差分，单独算
          const stat = base[name];
          if (!stat || !(stat instanceof BaselineStats)) continue;
          const vals = [];
          for (const fr of fs) {
            let v;
            if (name === 'R_tb') v = safeRatio(fr.theta, fr.beta);
            else if (name === 'R_ba') v = safeRatio(fr.beta, fr.theta + fr.alpha);
            else if (name === 'TAR') v = safeRatio(fr.theta, fr.alpha);
            else if (name === 'A_rel') v = base.alpha.mu > 0 ? (fr.alpha - base.alpha.mu) / base.alpha.mu : NaN;
            else v = fr[name];
            if (Number.isFinite(v)) vals.push(v);
          }
          if (!vals.length) continue;
          stat.mu = median(vals);
          stat.sigma = robustSigma(vals, stat);
        }
      }

      // B 段 RMSSD：相邻帧 RR 差分（真机 1.2–1.5s/帧，帧内通常只有单值）
      const bs = seg.B || [];
      const diffs = [];
      for (let i = 1; i < bs.length; i++) {
        const a = bs[i - 1].rr_ms, b = bs[i].rr_ms;
        if (!Number.isFinite(a) || !Number.isFinite(b)) continue;
        if (a < K.RR_MIN_MS || a > K.RR_MAX_MS || b < K.RR_MIN_MS || b > K.RR_MAX_MS) continue;
        diffs.push(Math.abs(b - a));
      }
      if (diffs.length >= 8) {
        const rms = Math.sqrt(diffs.reduce((s, d) => s + d * d, 0) / diffs.length);
        const st = base.RMSSD_60;
        st.mu = rms;
        st.sigma = robustSigma(diffs, st);
      }

      // θ_ref：首选 B 段（任务参照），仅 B 缺失时回退 A 段（并标记，供报告告警）
      const bTh = (seg.B || []).map(f => f.theta).filter(Number.isFinite);
      const aTh = (seg.A || []).map(f => f.theta).filter(Number.isFinite);
      if (bTh.length >= 10) { base.theta_ref = median(bTh); base.theta_ref_src = 'B'; }
      else if (aTh.length >= 10) { base.theta_ref = median(aTh); base.theta_ref_src = 'A-fallback'; }
      else { base.theta_ref_src = 'default'; }
      if (aTh.length) base.theta_rest = median(aTh);

      // v3.1 β/γ 参照（肌电专用门用；与 theta_ref 同段，参照系一致）
      for (const band of ['beta', 'gamma']) {
        const bv = (seg.B || []).map(f => f[band]).filter(v => Number.isFinite(v) && v > 0);
        const av = (seg.A || []).map(f => f[band]).filter(v => Number.isFinite(v) && v > 0);
        const mv = bv.length >= 10 ? median(bv) : (av.length >= 10 ? median(av) : null);
        if (mv) base[band + '_ref'] = mv;
      }
      return base;
    }

    /* ---- 校准质量报告（§六 第 5 条：失败须提示重戴，不静默进入判别）---- */
    static calibrationReport(frames, base) {
      base = base || StateEngine.calibrateFromFrames(frames);
      const seg = { A: [], B: [], C: [] };
      for (const it of (frames || [])) {
        if (!it || !it.frame) continue;
        if (!seg[it.tag]) seg[it.tag] = [];
        seg[it.tag].push(it.frame);
      }
      const counts = { A: (seg.A || []).length, B: (seg.B || []).length, C: (seg.C || []).length };
      // 伪迹率（与引擎同判据：单频段 8× / R_tb 15×，伪迹帧不更新平滑）
      let smooth = {}, art = 0, n = 0;
      for (const it of (frames || [])) {
        const fr = it && it.frame;
        if (!fr) continue;
        n++;
        let hit = false;
        // v3.1 β+γ 肌电门（与引擎 _emgArtifact 同判据）
        if (K.ARTIFACT_EMG_GATE && base.beta_ref > 0 && base.gamma_ref > 0 && base.theta_ref > 0) {
          hit = fr.beta > K.ARTIFACT_EMG_RATIO * base.beta_ref
            && fr.gamma > K.ARTIFACT_EMG_RATIO * base.gamma_ref
            && Math.abs(fr.theta - base.theta_ref) < K.ARTIFACT_EMG_THETA_TOL * base.theta_ref;
        }
        for (const key of ['delta', 'theta', 'alpha', 'beta', 'gamma']) {
          if (hit) break;
          const prev = smooth[key] || 0;
          const v = fr[key];
          if (!Number.isFinite(v) || prev <= 0) continue;
          if (Math.abs(v / prev - 1) > K.ARTIFACT_RATIO) { hit = true; break; }
        }
        if (!hit) {
          const prevR = smooth.R_tb || 0;
          const tb = fr.beta > 0 ? fr.theta / fr.beta : Infinity;
          if (prevR > 0 && Number.isFinite(tb) && Math.abs(tb / prevR - 1) > K.ARTIFACT_RATIO_RTB) hit = true;
        }
        if (hit) { art++; continue; }
        for (const key of ['delta', 'theta', 'alpha', 'beta', 'gamma']) {
          const v = fr[key];
          if (!Number.isFinite(v)) continue;
          smooth[key] = (smooth[key] == null)
            ? v : K.EWMA_ALPHA * v + (1 - K.EWMA_ALPHA) * smooth[key];
        }
        const tb = fr.beta > 0 ? fr.theta / fr.beta : NaN;
        if (Number.isFinite(tb)) {
          smooth.R_tb = (smooth.R_tb == null)
            ? tb : K.EWMA_ALPHA * tb + (1 - K.EWMA_ALPHA) * smooth.R_tb;
        }
      }
      const artifactRatio = n ? art / n : 1;

      let cZ = null;
      const alphaStat = base.alpha, aStat = base.A_rel;
      if (alphaStat && aStat && alphaStat.mu > 0 && counts.C >= 10) {
        const vals = (seg.C || []).map(f => (f.alpha - alphaStat.mu) / alphaStat.mu).filter(Number.isFinite);
        if (vals.length) {
          const s = vals.slice().sort((a, b) => a - b);
          const med = s[Math.floor(s.length / 2)];
          const sigma = Math.max(aStat.sigma, aStat.sigma_min);
          cZ = (med - aStat.mu) / sigma;
        }
      }
      const warnings = [];
      if (counts.B < 10) warnings.push(`B 段样本不足（${counts.B} 帧 <10），基线沿用默认值，校准无效，请重做`);
      if (counts.A < 10) warnings.push(`A 段样本不足（${counts.A} 帧 <10），δ 基线沿用默认值，嗜睡判据不可信`);
      if (counts.C < 10) warnings.push(`C 段样本不足（${counts.C} 帧 <10），无法校验放松态可判性`);
      if (artifactRatio > K.CALIB_ARTIFACT_MAX) warnings.push(`伪迹帧占比 ${(artifactRatio * 100).toFixed(0)}% 超限，请检查佩戴`);
      if (cZ != null && cZ < K.Z_RELAX_A) warnings.push(`C 段放松锚点 z=${cZ.toFixed(2)} < ${K.Z_RELAX_A}，放松态在当前基线下不可判`);
      return {
        ok: counts.B >= 10 && counts.A >= 10 && counts.C >= 10 && artifactRatio <= K.CALIB_ARTIFACT_MAX,
        segments: counts, artifactRatio, theta_ref: base.theta_ref,
        theta_ref_src: base.theta_ref_src, theta_rest: base.theta_rest,
        beta_ref: base.beta_ref, gamma_ref: base.gamma_ref,
        emg_gate: !!K.ARTIFACT_EMG_GATE,
        c_anchor_z: cZ, warnings
      };
    }

    /* ---- 从 JSON（导出的基线文件）还原为基线对象 ---- */
    static hydrateBaseline(obj) {
      const base = StateEngine.defaultBaseline();
      if (!obj || typeof obj !== 'object') return base;
      for (const k of Object.keys(base)) {
        const v = obj[k];
        if (base[k] instanceof BaselineStats) {
          if (v && typeof v === 'object') {
            base[k] = new BaselineStats({
              mu: Number.isFinite(v.mu) ? v.mu : base[k].mu,
              sigma: Number.isFinite(v.sigma) ? v.sigma : base[k].sigma,
              sigma_min: Number.isFinite(v.sigma_min) ? v.sigma_min : base[k].sigma_min,
              log_flag: !!v.log_flag
            });
          }
        } else if (typeof v === 'number' && Number.isFinite(v)) {
          base[k] = v;                                  // theta_ref / theta_rest
        } else if (typeof v === 'string') {
          base[k] = v;                                  // theta_ref_src
        }
      }
      return base;
    }

    /* ---- 基线序列化（导出 JSON 用；BaselineStats → 纯对象）---- */
    static serializeBaseline(base) {
      const out = {};
      for (const k of Object.keys(base || {})) {
        const v = base[k];
        out[k] = (v instanceof BaselineStats)
          ? { mu: v.mu, sigma: v.sigma, sigma_min: v.sigma_min, log_flag: !!v.log_flag }
          : v;
      }
      return out;
    }

    /* ---- 热替换基线（校准后不重建引擎：清派生状态，保留日志/统计）---- */
    setBaseline(base) {
      const src = base || StateEngine.defaultBaseline();
      this.baseline = {};
      for (const k of Object.keys(src)) {
        this.baseline[k] = (src[k] instanceof BaselineStats) ? src[k].clone() : src[k];
      }
      this._assertBaseline();
      this._thetaRef = (typeof this.baseline.theta_ref === 'number' && Number.isFinite(this.baseline.theta_ref))
        ? this.baseline.theta_ref : null;
      this._thetaInt = 0;
      this._lastThetaTs = null;
      this.smooth = {};
      this.abStats = { A: { total: 0, impulsive: 0 }, B: { total: 0, impulsive: 0 } };
      return this.baseline;
    }

    /* ---- 默认权重矩阵（§四 初值）---- */
    static defaultWeights() {
      return {
        focus: { R_tb: -1.0, R_ba: +0.5 },
        distracted: { R_tb: +1.0, A_rel: +0.3, still: +0.5 },
        relaxed: { A_rel: +1.0, RMSSD_60: +0.7, still: -0.5 },
        stress: { RMSSD_60: -1.0, HR: +0.7 },
        anxiety: { att: +1.0, med: +1.0 },
        flow: { att: +1.0, med: +1.0, sync: +2.0 },
        fatigue: { R_ba: -1.0, I_f: +1.0 },
        drowsy: { TAR: +1.0, delta: +0.5 }
      };
    }
  }

  global.BrainState = { K, Frame, BaselineStats, Decision, Feedback, StateEngine, clamp, safeDiv };

})(window);
