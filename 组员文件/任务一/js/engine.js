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
    ARTIFACT_RATIO: 15.0,       // 单频段伪迹门限（**倍数**：v≥15×ref 或 v≤ref/15）
                                // v3.6 真机定标（原 6.0）：与 state_engine.py 同步。四份真人 dump 实测
                                // 6× 过严——A 段闭眼静息 clean 只剩 1/2/9/22 帧，三段伪迹率 33.7%→80.5%，
                                // 四份全部 ok=false。真机 eegPower 是 1 秒瞬时 FFT，α 闭眼呈纺锤（成串
                                // 出现/消失），单频段天然在 0.1×–6× 摆，不是伪迹。15× 下三份伪迹率
                                // 10–20%，A 段 clean 恢复到 18/32/43。**改动必须与 Python 同步，否则闸门1 红。**
    ARTIFACT_RATIO_RTB: 15.0,   // v2.8 比值门限独立放宽（防真实状态切换被误杀）
    /* v3.4 伪迹参照系：稳健中位数 + 只记有效帧 + 连续全拦逃生门（与 Python 同名同值） */
    ARTIFACT_REF_WINDOW: 31,    // 参照窗口帧数
    ARTIFACT_REF_MIN: 5,        // 参照样本下限（不足则回退 smooth 种子）
    ARTIFACT_REF_RESET: 60,     // 连续判伪迹帧数上限（≈1min）→ 重建参照系
    /* v3.1 β+γ 肌电专用门（默认关；verify_hw V4 判"需启用"时改 true，与 Python 同步） */
    ARTIFACT_EMG_GATE: false,
    ARTIFACT_EMG_RATIO: 4.0,
    ARTIFACT_EMG_THETA_TOL: 0.35,
    RR_MIN_MS: 300, RR_MAX_MS: 2000,
    /* v3.4 与 Python state_engine.py 同名同值（verify_parity 常量表逐项比对） */
    SIGNAL_BAD: 200,           // 接触质量硬门：poorSignalLevel 200=电极未接触皮肤
    RR_DIFF_MAX_RATIO: 0.20,   // 异位搏动/掉搏滤波：相邻间期突变 >20% 的对不参与 RMSSD
    RMSSD_MIN_PAIRS: 6,        // 有效差分对下限（"窗口未满"语义；≈8 个 RR 值）
    EVID_REF: 1.0,             // 门控余量 → 证据的归一参考（1σ = 满证据；只服务**证据比**）
    // v3.4.1 绝对强度参考：余量达 0.5σ 视为证据充分（strength=1）。与 EVID_REF 分开——
    // 前者管"与对手比"要有区分度，后者管"本身够不够硬"。合成一个必然一边错。
    EVID_FULL: 0.5,

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
      // v3.4 接触质量（poorSignalLevel：0 最好 / 200 未接触皮肤；null=未知，不拦）
      this.signal = o.signal == null ? null : Number(o.signal);
    }
  }
  // null/空串必须走默认值：Number(null) === 0，旧实现会让"服务端明确给 null（未知）"
  // 变成 0，而不是 50/100 —— 与 Frame 注释声明的默认语义相反（SSE 的 _clean() 正是用
  // null 表达缺值）。只在 undefined/NaN 时用默认值的写法，对 null 是失效的。
  function num(v, d) {
    if (v == null || v === '') return d;
    const n = Number(v);
    return Number.isFinite(n) ? n : d;
  }

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
    constructor(state, conf, scores, gates, margins, evidence, reason) {
      this.state = state || 'neutral';
      this.conf = conf == null ? K.CONF_MIN : conf;
      this.scores = scores || {};
      this.gates = gates || {};
      // v3.4：conf = 排他性（与谁竞争），margin = 强度（σ 余量）。两者分开呈现。
      this.margins = margins || (scores && scores._margins) || {};
      this.evidence = evidence || (scores && scores._evidence) || {};
      this.reason = reason || '';    // ''=正常 / 'contact' / 'artifact'
    }
  }
  class Feedback {
    constructor(level, action, cooldown_left) {
      this.level = level || 'silent';
      this.action = action || '';
      this.cooldown_left = cooldown_left || 0;
    }
  }

  /* ------------------------------------------------------------ 预处理组件
   * ArtifactDetector：伪迹 + 接触质量判定（**唯一实现**，对应 state_engine.py 同名类）。
   * 为什么单独成类：v3.4 之前引擎的 _artifact、calibrateFromFrames 的内联块、
   * calibrationReport 的内联块是同一套规则的**三份手写实现**——改门限要么漏改、要么改一半，
   * 本项目已四次栽在"同一事实两处维护"（RMSSD 恒 nan / signal=230 / 相位表 6 vs 8）。
   * 这里收敛成一份，三处共用同一个实例语义（连参照窗口的推进顺序都不必再人工对齐）。
   *
   * 参照系（v3.4 重设计）：ref = 最近 ARTIFACT_REF_WINDOW 个**有效帧**的中位数。
   *   · 旧实现拿 EWMA 平滑值当参照 → 眨眼把参照一起抬走，"900 撞三帧后参照≈370"，
   *     越密集的伪迹越检不出来（持续肌电拦截率实测 1/40，设计稿 §四 记录的 V4 0% 即此机制）；
   *   · 伪迹帧**不记账**：若记账，窗口内污染 >50% 时中位数会翻到伪迹电平（实测 16 帧后翻），
   *     同一批伪迹反而全部放行——"自我掩护"换个形式回来；
   *   · 代价是"电平真的变了"（重戴/增益切换）会锁死，故补 ARTIFACT_REF_RESET 逃生门。
   */
  const BANDS = ['delta', 'theta', 'alpha', 'beta', 'gamma'];

  class ArtifactDetector {
    constructor(baseline, alpha) {
      this.smooth = {};                       // 信号通路平滑（I_f 积分对象；与判据锚分开）
      this.baseline = baseline || {};         // 肌电门参照（B 段 beta_ref/gamma_ref/theta_ref）
      this.alpha = alpha == null ? K.EWMA_ALPHA : alpha;
      this.frames = 0;
      this.artifact_hits = 0;
      this.contact_hits = 0;
      this._rejects = 0;
      this.reseeds = 0;
      this._refs = {};
      for (const k of BANDS.concat(['R_tb'])) this._refs[k] = [];
    }

    reset() {
      this.smooth = {};
      this.frames = 0; this.artifact_hits = 0; this.contact_hits = 0;
      this._rejects = 0; this.reseeds = 0;
      for (const k of BANDS.concat(['R_tb'])) this._refs[k] = [];
    }

    /* -- 平滑（信号通路；伪迹帧不推进）-- */
    ewma(key, x) {
      if (!(key in this.smooth)) this.smooth[key] = x;
      else this.smooth[key] = this.alpha * x + (1 - this.alpha) * this.smooth[key];
      return this.smooth[key];
    }

    update(frame) {
      for (const key of BANDS) {
        const v = frame[key];
        if (v == null || !isF(v)) continue;
        this.ewma(key, v);
      }
      const tb = safeDiv(frame.theta, frame.beta);
      if (isF(tb)) this.ewma('R_tb', tb);
    }

    /* -- 参照系（判据锚）-- */
    ref(key) {
      const d = this._refs[key];
      if (d && d.length >= K.ARTIFACT_REF_MIN) {
        const s = d.slice().sort((a, b) => a - b);
        const n = s.length;
        return n % 2 ? s[(n - 1) / 2] : 0.5 * (s[n / 2 - 1] + s[n / 2]);
      }
      const sm = this.smooth[key];   // 冷启动 / 外部播种（历史调用面直接写 eng.smooth）
      return isF(sm) && sm > 0 ? sm : null;
    }

    observe(frame) {
      // 只记有效帧（先判后记：本帧不参与自己的判据）
      for (const key of BANDS) {
        const raw = frame[key];
        if (raw == null) continue;              // null/undefined 与 Python 一致：不记账（不是 0）
        const v = Number(raw);
        if (Number.isFinite(v)) this._refs[key].push(v);
        if (this._refs[key].length > K.ARTIFACT_REF_WINDOW) this._refs[key].shift();
      }
      const tb = safeDiv(frame.theta, frame.beta);
      if (isF(tb)) {
        this._refs.R_tb.push(tb);
        if (this._refs.R_tb.length > K.ARTIFACT_REF_WINDOW) this._refs.R_tb.shift();
      }
    }

    reseed(frame) {
      // 逃生门：把 smooth 与参照窗口一起对齐到当前电平（只清 refs 不够——ref() 会回退到
      // 旧电平的 smooth，重建后第一帧又判伪迹，死循环）
      for (const k of BANDS.concat(['R_tb'])) this._refs[k] = [];
      this.smooth = {};
      this._rejects = 0;
      this.reseeds += 1;
      this.update(frame);
      this.observe(frame);
    }

    /* -- 判定 -- */
    contactBad(frame) {
      const s = frame.signal;
      return s != null && Number.isFinite(s) && s >= K.SIGNAL_BAD;
    }

    check(frame) {
      this.frames += 1;
      if (this.contactBad(frame)) { this.contact_hits += 1; return 'contact'; }
      if (this.emgArtifact(frame) || this.ratioArtifact(frame)) {
        this.artifact_hits += 1;
        this._rejects += 1;
        if (this._rejects >= K.ARTIFACT_REF_RESET) this.reseed(frame);
        return 'artifact';
      }
      this._rejects = 0;
      this.observe(frame);
      return null;
    }

    push(frame) {                    // check + 条件 update（校准回放用）
      const reason = this.check(frame);
      if (reason === null) this.update(frame);
      return reason;
    }

    emgArtifact(frame) {
      if (!K.ARTIFACT_EMG_GATE) return false;
      const br = this.baseline.beta_ref, gr = this.baseline.gamma_ref, tr = this.baseline.theta_ref;
      if (!isF(br) || !isF(gr) || !isF(tr) || br <= 0 || gr <= 0 || tr <= 0) return false;
      return frame.beta > K.ARTIFACT_EMG_RATIO * br
        && frame.gamma > K.ARTIFACT_EMG_RATIO * gr
        && Math.abs(frame.theta - tr) < K.ARTIFACT_EMG_THETA_TOL * tr;
    }

    ratioArtifact(frame) {
      for (const key of BANDS) {
        const ref = this.ref(key);
        const v = frame[key];
        if (ref == null || ref <= 0 || v == null || !isF(v)) continue;
        const r = v / ref;
        if (r > K.ARTIFACT_RATIO || r * K.ARTIFACT_RATIO < 1) return true;   // 双向：突增/塌陷
      }
      const ref = this.ref('R_tb');
      if (ref != null && ref > 0) {
        const tb = safeDiv(frame.theta, frame.beta);
        if (isF(tb) && (tb > K.ARTIFACT_RATIO_RTB * ref || tb * K.ARTIFACT_RATIO_RTB < ref)) return true;
      }
      return false;
    }

    get artifactRatio() { return this.frames ? this.artifact_hits / this.frames : 1; }
    get contactRatio() { return this.frames ? this.contact_hits / this.frames : 0; }
  }

  /* ------------------------------------------------------------ 状态引擎 */
  class StateEngine {
    constructor(opts) {
      opts = opts || {};
      this._det = new ArtifactDetector();     // v3.4：伪迹/接触判定唯一实现（smooth 挂在其中）
      this.rrBuf = [];      // [rr, batch, ts]
      // v3.13：本会话是否**曾经**收到过有效 RR。硬质头环无 PPG，rr 恒缺 ——
      // 界面据此把 RMSSD/HR 标成「无 RR」、把 stress 标成不可判，
      // 而不是让用户一直等一个永远不会来的读数。
      this.rrSeen = false;
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
      // v3.16：零样本返回 null（没有样本），不是 1（满分）；UI 用 log.total 判空
      return this.log.total ? 1 - (this.log.impulsive / this.log.total) : null;
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

    /* ---- 兼容视图（旧代码直接读写 engine.smooth；与 Python property 同语义）---- */
    get smooth() { return this._det.smooth; }
    set smooth(v) { this._det.smooth = v || {}; }

    /* ---- 预处理（薄封装，保留历史调用面）---- */
    _ewma(key, x) { return this._det.ewma(key, x); }

    /* v3.4：接触质量硬门。只挡协议明确的"无效"档（200）；细粒度阈值由适配器
       signal_max（verify_hw V3 标定）处理，两层职责不重叠。 */
    _contactBad(frame) { return this._det.contactBad(frame); }

    /* 兼容入口：true=该帧不参与判别（接触无效或伪迹）。需要区分原因时用 _det.check() */
    _artifact(frame) { return this._det.check(frame) !== null; }

    /* v3.1：β+γ 同升且 θ 不动 → 肌电伪迹（参照取 B 段，不用 smooth——会被肌电抬走）。 */
    _emgArtifact(frame) { return this._det.emgArtifact(frame); }

    /* ---- 派生同步率（指南 2.2；注意：官方无独立同步率字段，此为派生口径）---- */
    _updateSync(frame) {
      // v3.15：eSense=0 = "没有指数"，不是"指数极低"。写进趋势窗会让 60→0 的伪跃变
      // 被算作一次同向下降（双指数常同时归零），凭空抬高同步率。
      if (!(frame.attention > 0) || !(frame.meditation > 0)) {
        this.syncRatio = 0;
        return false;
      }
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

    /* ---- RMSSD（v2.6 同批内差分；v3.4 加异位搏动滤波 + 差分对下限）----
       v3.4：与 Python `_rmssd` / `_rmssd_pairs` 1:1。
         · 相邻间期突变 > RR_DIFF_MAX_RATIO(20%) 的对剔除（缺搏/异位会把 RMSSD 抬高一个量级）
         · 下限改按**有效差分对**判（原按样本数 ≥8；真机每批 3 值只产生 2 对，6 对等价） */
    static _rmssdDiffs(rrList) {
      const out = [];
      for (let i = 0; i < rrList.length - 1; i++) {
        const r0 = rrList[i], r1 = rrList[i + 1];
        if (r0[1] != null && r1[1] != null && r0[1] !== r1[1]) continue; // 跨回调批次断开
        const d = r1[0] - r0[0];
        if (r0[0] > 0 && Math.abs(d) > K.RR_DIFF_MAX_RATIO * r0[0]) continue;
        out.push(d);
      }
      return out;
    }
    static _rmssd(rrList) {
      const diffs = StateEngine._rmssdDiffs(rrList);
      if (diffs.length < K.RMSSD_MIN_PAIRS) return NaN;
      return Math.sqrt(diffs.reduce((a, d) => a + d * d, 0) / diffs.length);
    }

    /* ---- 稳健统计（与 Python StateEngine._median / _robust_stats 逐位一致）----
     * 为什么提为静态：校准与伪迹参照系都要用，而两处各写一份必然漂移（旧 JS 就是
     * 上中位数 + max(med−q1, q3−med) 与 Python 的偶样本取均值 + (q3−q1)/2 不同口径）。
     * σ 口径：半 IQR × 1.4826（正态一致），不是 MAD——旧注释写错名字，数值本身是对的。 */
    static _median(vals) {
      const xs = (vals || []).filter(v => v != null && Number.isFinite(v)).slice().sort((a, b) => a - b);
      if (!xs.length) return null;
      const n = xs.length;
      return n % 2 ? xs[(n - 1) / 2] : 0.5 * (xs[n / 2 - 1] + xs[n / 2]);
    }

    static _robustStats(vals, stat) {
      let xs = (vals || []).filter(v => v != null && Number.isFinite(v));
      if (!xs.length) return { mu: stat.mu, sigma: stat.sigma };
      if (stat.log_flag) {
        xs = xs.filter(v => v > 0).map(Math.log);       // 与 _z 的 log 变换同空间
        if (!xs.length) return { mu: stat.mu, sigma: stat.sigma };
      }
      xs = xs.sort((a, b) => a - b);
      const n = xs.length;
      const med = n % 2 ? xs[(n - 1) / 2] : 0.5 * (xs[n / 2 - 1] + xs[n / 2]);
      const q1 = xs[Math.floor(n / 4)];
      const q3 = xs[Math.floor(3 * n / 4)];
      return { mu: med, sigma: Math.max((q3 - q1) / 2 * 1.4826, stat.sigma_min) };
    }

    /* ---- 时间戳滑动窗（v2.7 #7）---- */
    _pushWindow(frame, ts) {
      this.eegBuf.push(frame);
      // v3.16：0 是合法 ts（相对时间起点），不能写 `eegBuf[0].ts || ts`；
      // 旧写法会让首帧 ts=0 的比较恒为 0 → 窗口永不裁剪（与 Python 同一处修正）。
      while (this.eegBuf.length) {
        const first = this.eegBuf[0].ts;
        if (ts - (first == null ? ts : first) <= K.WINDOW_EEG_S) break;
        this.eegBuf.shift();
      }
      // v3.3：优先整批入缓冲（与 Python _rmssd 的"同批内差分"语义对齐）。
      // 批次号用自增序号：同批内才差分，跨批直接 continue。
      if (Array.isArray(frame.rr_batch) && frame.rr_batch.length) {
        this._rrBatchSeq = (this._rrBatchSeq || 0) + 1;
        for (const rr of frame.rr_batch) {
          if (rr >= K.RR_MIN_MS && rr <= K.RR_MAX_MS) { this.rrBuf.push([rr, this._rrBatchSeq, ts]); this.rrSeen = true; }
        }
      } else if (frame.rr_ms != null) {
        // 降级路径（旧 CSV / mock 单值）：batch 记 null → 跨帧差分，与历史行为一致
        this.rrBuf.push([frame.rr_ms, null, ts]);
        if (frame.rr_ms >= K.RR_MIN_MS && frame.rr_ms <= K.RR_MAX_MS) this.rrSeen = true;
      }
      while (this.rrBuf.length && ts - this.rrBuf[0][2] > K.WINDOW_HRV_S) this.rrBuf.shift();
      this.gyroBuf.push(frame.gyro);
      this.gyroTs.push(ts);
      while (this.gyroBuf.length && ts - this.gyroTs[0] > K.WINDOW_GYRO_S) { this.gyroBuf.shift(); this.gyroTs.shift(); }
    }

    pushRR(rr, batch, ts) {
      if (!(rr >= K.RR_MIN_MS && rr <= K.RR_MAX_MS)) return;
      // v3.4：缺省改用最近帧时间戳——原调 _clock() 会让 mock 时钟被每个 RR 推进一次
      const t = (ts == null) ? (this._lastTs != null ? this._lastTs : 0) : ts;
      this.rrBuf.push([rr, batch == null ? null : batch, t]);
      this.rrSeen = true;
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

    /* ---- v3.4 门控余量（σ 单位，跨状态同量纲）+ 证据归一 ----
       与 Python state_engine._margins 1:1。合取条件取 min、析取条件（焦虑/心流双判据）取 max。
       存在的理由：置信度若直接比各状态 score，就是比不同单位（stress 是 z 线性组合 ~1–3，
       anxiety 是指数余量 ~10–20）。改成 σ 余量后 8 态同量纲，才能回答"有多强/差多少"。 */
    _margins(z, frame, taskActive) {
      const lt = (n, th) => { const v = z[n]; return isF(v) ? th - v : null; };
      const gt = (n, th) => { const v = z[n]; return isF(v) ? v - th : null; };
      const mn = (...vs) => { const a = vs.filter(v => v != null && Number.isFinite(v)); return a.length ? Math.min.apply(null, a) : 0; };
      const mx = (...vs) => { const a = vs.filter(v => v != null && Number.isFinite(v)); return a.length ? Math.max.apply(null, a) : 0; };
      const sa = this._sigmaOf('att', 15), sm = this._sigmaOf('med', 15);
      const anxAbs = mn((K.ANX_ATT_MAX - frame.attention) / sa, (K.ANX_MED_MAX - frame.meditation) / sm);
      // v3.4.1 修：焦虑 z 分支是 z **低于**阈值（za < ANX_Z），余量用 lt。用 gt 会算出相反数
      // → 余量负、证据 0、该提醒的焦虑被压成弱判别（Python 侧 verify_v26 V5 抓到）。
      const anxZ = mn(lt('att_z', K.ANX_Z), lt('med_z', K.ANX_Z));
      const flowAbs = mn((frame.attention - K.FLOW_ATT_MIN) / sa, (frame.meditation - K.FLOW_MED_MIN) / sm);
      const flowZ = mn(gt('att_z', K.FLOW_Z), gt('med_z', K.FLOW_Z));
      const ifm = K.IF_HOLD_S ? (this._thetaInt / K.IF_HOLD_S - 1) : 0;
      return {
        focus: mn(lt('R_tb', K.Z_CONC), gt('RMSSD_60', K.Z_RMSSD_NO_STRESS), lt('still', K.Z_STILL_CAP)),
        distracted: taskActive ? gt('R_tb', K.Z_DIST) : 0,
        relaxed: mn(gt('A_rel', K.Z_RELAX_A)),
        stress: mn(lt('RMSSD_60', K.Z_STRESS), lt('still', K.Z_STILL_CAP)),
        anxiety: mx(anxAbs, anxZ),
        flow: mn(mx(flowAbs, flowZ), lt('R_tb', K.Z_DIST)),
        fatigue: mn(lt('R_ba', K.Z_FATIGUE_BAR), ifm, gt('RMSSD_60', K.Z_STRESS)),
        drowsy: mn(gt('TAR', K.Z_DROWSY_TAR), gt('delta', K.Z_DROWSY_DELTA))
      };
    }
    _sigmaOf(name, dflt) {
      const st = this.baseline[name];
      if (!st || !(st instanceof BaselineStats)) return dflt;
      return Math.max(st.sigma, st.sigma_min) || dflt;
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
        // v3.4：δ 走 10s 窗均值（feats.delta），与其它特征同一时间尺度。
        // 原用 frame.delta 瞬时值 → 单帧 δ 起伏 40% 即让 z(δ) 跳 2σ，嗜睡门控随抖动翻转。
        if (st) {
          const dv = isF(feats.delta) ? feats.delta : frame.delta;
          z.delta = isF(dv) ? this._z(dv, st) : NaN;
        }
      }
      let za = NaN, zm = NaN;
      if (frame && this.baseline.att && this.baseline.med) {
        za = this._z(frame.attention, this.baseline.att);
        zm = this._z(frame.meditation, this.baseline.med);
        z.att_z = za; z.med_z = zm;     // v3.4：供 _margins 折算"个体 z 分支"的门控余量
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
        // v3.15：eSense 必须有效（att/med 都 >0）。TGAM 在信号差/未算出指数时给 0/0，
        // 旧实现把它读成"双低 → 极度焦虑"，实测 20 帧全 0 得 anxiety 0.95。
        anxiety: !!(frame && frame.attention > 0 && frame.meditation > 0)
          && ((frame.attention < K.ANX_ATT_MAX && frame.meditation < K.ANX_MED_MAX)
            || (fin(za) && fin(zm) && za < K.ANX_Z && zm < K.ANX_Z)),
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
      // v3.4：门控余量 → 证据（只对已过门控的状态有意义；未过者余量为负，证据记 0）。
      // 用 defineProperty 挂非枚举属性：Object.keys(scores) 仍只看到 8 个状态。
      if (frame) {
        const m = this._margins(z, frame, taskActive);
        const ev = {};
        for (const s of Object.keys(scores)) {
          ev[s] = isF(scores[s]) ? Math.max(0, Math.min(1, (m[s] || 0) / K.EVID_REF)) : 0;
        }
        Object.defineProperty(scores, '_margins', { value: m, enumerable: false, writable: true });
        Object.defineProperty(scores, '_evidence', { value: ev, enumerable: false, writable: true });
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

      /* v3.4 置信度：用同量纲的**证据比** e_best/(e_best+e_second)，不再比分数。
         旧式 (best−second)/(|best|+|second|) 跨状态比的是不同单位——实测
         "RMSSD z=−2 的强应激(score 2.0) + att/med=30 的焦虑(score 20)" → conf 0.31 截到 0.60，
         强应激被标"弱判别仅记录"，干预永不触发。同场景改证据比后 0.60 → 0.75（越过 WEAK_CONF）。 */
      const ev = scores._evidence || null;
      if (ev) {
        const eb = Math.max(0, ev[best] || 0);
        let e2 = 0;
        for (const s of keys) if (s !== best) e2 = Math.max(e2, Math.max(0, ev[s] || 0));
        const sep = (eb + e2) > 0 ? eb / (eb + e2) : 0;
        /* v3.4.1 绝对强度：只做 sep 会导致单状态（无对手）时 sep 恒 1 → conf 恒 CONF_MAX，
           超阈 0.001σ 与 5σ 无法区分、WEAK_CONF 永不生效（实测两者都 0.950）。
           强度优先取真实余量（σ），外部只给 evidence 时退化为 e 本身。 */
        const mg = scores._margins || null;
        const mb = mg ? mg[best] : undefined;
        const strength = (mb == null || !Number.isFinite(mb))
          ? Math.min(1, eb)
          : Math.max(0, Math.min(1, mb / K.EVID_FULL));
        const conf = K.CONF_MIN + (K.CONF_MAX - K.CONF_MIN) * sep * strength;
        return { state: best, conf: clamp(conf, K.CONF_MIN, K.CONF_MAX) };
      }
      /* 兼容路径：调用方只给了普通对象（无证据表）——UI 重算/单测会这样用，
         退回"分数差比"。主链路不走这里。 */
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
      // v3.14：上限 500 —— 该数组目前无消费方（冷静度用的是 total/impulsive），
      // 留着是为了将来导出决策序列；不设上限会在长会话里无限增长。与 Python 侧同步。
      if (this.log.decisions.length > 500) this.log.decisions.shift();
      const seg = this.abStats[this.abSegment] || this.abStats.A;
      seg.total += 1;
      if (isImpulsive) seg.impulsive += 1;
    }
    segmentCalmness(seg) {
      const s = this.abStats[seg];
      return s && s.total ? 1 - s.impulsive / s.total : null;
    }

    /* ---- 时间确认（v2.7 #8；v3.4 改按**时长**加权）----
       帧数占比在真机帧率抖动（1.2–1.5s/帧、掉帧更慢）下与时间占比不等价：同样"60% 命中"，
       10 Hz 下只覆盖 0.3s，1 Hz 下覆盖 3s。改 Σdt(命中)/Σdt(窗口) 后确认时长恒为
       DEBOUNCE_S·HIT_RATIO，与帧率解耦。 */
    _confirm(state, ts) {
      const prev = this._lastTs;
      this._lastTs = ts;
      if (state === 'neutral') return state;
      const win = clamp(this.cfg.debounceS, 0.5, 60);
      const t0 = prev == null ? ts : prev;
      // 条目存 [state, ts, prevTs]；查询时把区间裁到窗口内再累加。
      // 直接累加建条目时的 dt 会让越窗旧条目带着过期权重占分母（实测推迟确认）。
      this._hitBuf.push([state, ts, t0]);
      const lo = ts - win;
      while (this._hitBuf.length && this._hitBuf[0][1] < lo) this._hitBuf.shift();
      if (this._hitBuf.length < 2) return state;
      let total = 0, hits = 0;
      for (const x of this._hitBuf) {
        const w = Math.min(x[1], ts) - Math.max(x[2], lo);
        if (w <= 0) continue;
        total += w;
        if (x[0] === state) hits += w;
      }
      if (total <= 0) return state;
      return (hits / total >= clamp(this.cfg.hitRatio, 0, 1)) ? state : 'neutral';
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
        let v = feats[name];
        if (v == null || !isF(v)) continue;
        if (stat.log_flag) { if (v <= 0) continue; v = Math.log(v); }   // v3.4：与 _z 同空间
        let mu = stat.mu + K.ADAPT_ALPHA * (v - stat.mu);
        mu = clamp(mu, stat.mu - K.ADAPT_MAX_DRIFT * stat.sigma, stat.mu + K.ADAPT_MAX_DRIFT * stat.sigma);
        stat.mu = mu;
      }
    }

    /* ---- 主入口 ---- */
    processFrame(frame, ctx) {
      // v3.16：ts=0 合法；只有 null/空串/非有限/负数才回退内部时钟。
      const rawTs = frame.ts;
      const hasTs = rawTs != null && rawTs !== '' && Number.isFinite(Number(rawTs)) && Number(rawTs) >= 0;
      const ts = hasTs ? Number(rawTs) : this._clock();
      frame.ts = ts;
      this.frameCount += 1;

      /* v3.4：接触门 + 伪迹门合并为一次判定（与 Python process_frame 同序），
         一次 check 同时完成"判 / 记账 / 计数"，避免 _contactBad 与 _artifact 各判一次
         （旧写法 `isContact || this._artifact(frame)` 会让接触帧在计数器里记两笔）。 */
      const reason = this._det.check(frame);
      if (reason !== null) {
        this.artifactCount += 1;
        if (this._thetaRef) this._lastThetaTs = ts;
        this._lastTs = ts;             // v3.4：无效帧也推进时钟（时间确认的 dt 不能断）
        this.lastFeats = this.lastFeats || null;
        const prev = this.lastDecision || new Decision('neutral', K.CONF_MIN);
        // 保留上帧结论，但标注原因（contact=电极未接触 / artifact=幅值突变），便于现场定位
        const dec = new Decision(prev.state, prev.conf, prev.scores, prev.gates,
          prev.margins, prev.evidence, reason);
        return { decision: dec, feedback: this.lastFeedback, artifact: true,
                 contact: reason === 'contact', ts };
      }
      this._det.update(frame);
      this._pushWindow(frame, ts);

      const feats = this._extract();
      this.lastFeats = feats;

      // 疲劳积分 I_f（θ_ref 冻结基线；v2.9.1 幅度加权 + 可恢复，与 Python 逐位对齐）
      //   rate = (θ_norm−1.2)/0.3 截断到 [0, 2.0]，I_f += rate·dt；
      //   θ 回落至阈下时 I_f 半衰 600s——疲劳是"暴露累积 + 恢复"，不能单调锁存。
      //   v2.9.0 的纯时长版与本次幅度加权版均须同步 JS，否则双引擎 I_f 偏差达 11 倍（实测）。
      if (this._thetaRef && this._thetaRef > 0) {
        // v3.4：分子改用**平滑 θ**。瞬时 θ 经 max(0,·) 整流后有正偏置（Jensen）：
        // 实测 θ 均值恰在阈、σ=12 时 1800s 凭空累积 I_f=179s（0.0995/s）→ 长时盯盘假阳性疲劳。
        // EWMA σ 降为 1/3（√(α/(2−α))=0.333），偏置同比例下降。
        const thS = isF(this.smooth.theta) ? this.smooth.theta : frame.theta;
        const thetaNorm = thS / this._thetaRef;
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

    /* ---- v3.14：只清特征窗口，保留会话统计 ----
     * 用于"数据源切换但会话继续"：从真机切回演示模式时，10s EEG 窗里残留的真机帧
     * 会污染切回后的前 10 秒判别。冷静度/A·B 对照/冷却计时属于会话层面，应当保留。
     * 注意不清 rrSeen —— 那是"本会话是否见过 RR"的历史事实，清掉会让无 RR 判定重新等 40 帧。 */
    clearWindows() {
      this._det.reset();
      this.rrBuf = []; this.eegBuf = []; this.gyroBuf = []; this.gyroTs = [];
      this._hitBuf = [];
      this.attHist = []; this.medHist = [];
      this.syncRatio = 0; this.syncOk = false; this.syncLatched = false; this.flowHold = 0;
      this._thetaInt = 0; this._lastThetaTs = null; this._lastAdaptTs = -Infinity;
      this._lastTs = null;
      this.lastDecision = null; this.lastFeats = null; this.lastZ = {};
      this.artifactCount = 0; this.frameCount = 0;
    }

    reset() {
      this._det.reset(); this.rrBuf = []; this.eegBuf = []; this.gyroBuf = []; this.gyroTs = [];
      this._hitBuf = []; this._lastActionTime = {};
      this.attHist = []; this.medHist = [];
      this.syncRatio = 0; this.syncOk = false; this.syncLatched = false; this.flowHold = 0;
      this.weakCount = 0; this._lastWeakEscalate = -Infinity; this._heartEwma = null;
      this._thetaInt = 0; this._lastThetaTs = null; this._lastAdaptTs = -Infinity;
      this._lastTs = null;
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
    /* v3.16：校准与运行时必须用同一把尺子。
       运行时 `_extract` 用 10s 窗均值算 R_tb/R_ba/TAR/A_rel，旧校准却用逐帧值估 μ/σ，
       同段实测 σ 比值 2.3–3.4× → 运行时 |z| 被压缩同样倍数。这里复刻"窗口内均值"，
       只用于**估计基线**，不改变运行时的任何判据。 */
    static _windowedSeries(frames, windowS, fn) {
      const out = [];
      let j = 0;
      for (let i = 0; i < frames.length; i++) {
        const ts = Number.isFinite(frames[i].ts) ? frames[i].ts : 0;
        while (j < i) {
          const t0 = Number.isFinite(frames[j].ts) ? frames[j].ts : ts;
          if (ts - t0 <= windowS) break;
          j++;
        }
        const sub = frames.slice(j, i + 1);
        if (sub.length) out.push(fn(sub));
      }
      return out;
    }

    static _windowMeans(frames, windowS) {
      const meanOf = (sub, key) => {
        const a = sub.map(f => f[key]).filter(Number.isFinite);
        return a.length ? a.reduce((s, v) => s + v, 0) / a.length : NaN;
      };
      return StateEngine._windowedSeries(frames, windowS, sub => ({
        theta: meanOf(sub, 'theta'), alpha: meanOf(sub, 'alpha'),
        beta: meanOf(sub, 'beta'), delta: meanOf(sub, 'delta')
      }));
    }

    static calibrateFromFrames(frames) {
      const base = StateEngine.defaultBaseline();
      const seg = { A: [], B: [], C: [] };
      for (const it of (frames || [])) {
        if (!it || !it.frame) continue;
        const tag = it.tag;
        if (!seg[tag]) seg[tag] = [];
        seg[tag].push(it.frame);
      }
      // v3.4：中位数/稳健 σ 统一走 StateEngine 的静态实现（与 Python _median/_robust_stats
      // 逐位一致）。旧内联版按 s[floor(n/2)] 取上中位数、σ 用 max(med−q1, q3−med)×1.4826，
      // 与 Python 的"偶样本取均值"和"(q3−q1)/2×1.4826"在**非对称分布**下不同 → 基线漂移。
      const median = StateEngine._median;
      const robustSigma = StateEngine._robustStats;
      const safeRatio = (a, b) => (b <= 0 ? Infinity : a / b);

      /* v3.4 校准三处口径修正（与 Python calibrate_from_frames 同步，勿单边改）：
         ① δ 基线改取 B 段（任务参照）。旧实现只从 A 段取 δ——闭眼 δ 系统性偏高（实测 μ=149.7），
            而判别发生在睁眼任务（δ≈100–120）→ z(δ) 恒 −2~−3.4 → 嗜睡合取永久不成立。
            这与 v2.9.2 修的 theta_ref 误取 A 段是同一失效模式，只是换了个特征重新引入。
         ② 伪迹/接触无效帧先剔除——走与运行时**同一个** ArtifactDetector（v3.4 前这里手写了
            第三份规则，改门限要改三处，属"同一事实多处维护"）。旧实现直接统计原始帧，眨眼尖峰
            把 σ 抬高，z 判据随之变钝。
         ③ RMSSD 走与运行时相同的 `_rmssd`（含 rr_batch 批次语义 + 异位滤波），不再另写"帧→帧差分"。 */
      const det = new ArtifactDetector();   // 校准段无 B 段参照可用 → 肌电门天然关闭（同 Python）
      const clean = { A: [], B: [], C: [] };
      for (const tag of Object.keys(seg)) {
        for (const fr of seg[tag]) {
          if (det.push(fr) === null) (clean[tag] || (clean[tag] = [])).push(fr);
        }
      }

      // ①-a alpha 先算（A_rel 的分母来源，不依赖 plan 的键顺序）
      // v3.16：window means（与运行时 _extract 同时间尺度）
      const bFrames = clean.B || [];
      const bMeans = StateEngine._windowMeans(bFrames, K.WINDOW_EEG_S);
      const bAlpha = bMeans.map(m => m.alpha).filter(Number.isFinite);
      if (bAlpha.length >= 10) {
        const st = StateEngine._robustStats(bAlpha, base.alpha);
        base.alpha.mu = st.mu;
        base.alpha.sigma = st.sigma;
      }
      const alphaMu = base.alpha.mu;

      // HR：运行时用 60s 窗内 RR 均值（无 RR → NaN）；校准侧同口径，避免逐拍瞬时值撑大 σ
      const rrPts = [];
      for (const fr of bFrames) {
        const fts = Number.isFinite(fr.ts) ? fr.ts : 0;
        if (Array.isArray(fr.rr_batch) && fr.rr_batch.length) {
          for (const rr of fr.rr_batch) if (Number.isFinite(rr)) rrPts.push([rr, fts]);
        } else if (Number.isFinite(fr.rr_ms)) {
          rrPts.push([fr.rr_ms, fts]);
        }
      }
      const hrSeries = bFrames.map(fr => {
        const fts = Number.isFinite(fr.ts) ? fr.ts : 0;
        const vals = rrPts.filter(([, t]) => t <= fts + 1e-9 && fts - t <= K.WINDOW_HRV_S).map(x => x[0]);
        return vals.length ? 60000 / (vals.reduce((s, v) => s + v, 0) / vals.length) : NaN;
      });

      const series = {
        R_tb: bMeans.map(m => safeRatio(m.theta, m.beta)),
        R_ba: bMeans.map(m => safeRatio(m.beta, m.theta + m.alpha)),
        TAR: bMeans.map(m => safeRatio(m.theta, m.alpha)),
        A_rel: bMeans.map(m => alphaMu > 0 ? (m.alpha - alphaMu) / alphaMu : NaN),
        alpha: bAlpha,
        delta: bMeans.map(m => m.delta),
        HR: hrSeries,
        still: StateEngine._windowedSeries(bFrames, K.WINDOW_GYRO_S, sub =>
          sub.reduce((s, f) => s + Math.abs(f.gyro[0]) + Math.abs(f.gyro[1]) + Math.abs(f.gyro[2]), 0) / sub.length),
        att: bFrames.map(f => f.attention),
        med: bFrames.map(f => f.meditation)
      };
      for (const name of Object.keys(series)) {
        const stat = base[name];
        if (!stat || !(stat instanceof BaselineStats)) continue;
        let vals = series[name].filter(Number.isFinite);
        // eSense=0 是"没有指数"，不是"极低"——与 Python 同口径
        if (name === 'att' || name === 'med') vals = vals.filter(v => v > 0);
        if (!vals.length || (name !== 'still' && vals.length < 10)) continue;
        const st = robustSigma(vals, stat);
        stat.mu = st.mu;
        stat.sigma = st.sigma;
      }

      // ③ B 段 RMSSD：与运行时同一函数 + 同一批次语义
      const rrBuf = [];
      let bi = 0;
      for (const fr of (clean.B || [])) {
        if (Array.isArray(fr.rr_batch) && fr.rr_batch.length) {
          bi++;
          for (const rr of fr.rr_batch) {
            if (rr >= K.RR_MIN_MS && rr <= K.RR_MAX_MS) rrBuf.push([rr, bi, 0]);
          }
        } else if (fr.rr_ms != null && fr.rr_ms >= K.RR_MIN_MS && fr.rr_ms <= K.RR_MAX_MS) {
          rrBuf.push([fr.rr_ms, null, 0]);            // 单值路径：batch=null（与运行降级口径一致）
        }
      }
      const rmssd = StateEngine._rmssd(rrBuf);
      const rmssdPairs = StateEngine._rmssdDiffs(rrBuf).length;
      if (Number.isFinite(rmssd)) {
        base.RMSSD_60.mu = rmssd;
        const series = [];
        for (let i = 0; i < rrBuf.length; i++) {
          const v = StateEngine._rmssd(rrBuf.slice(0, i + 1));
          if (Number.isFinite(v)) series.push(v);
        }
        if (series.length >= 10) base.RMSSD_60.sigma = robustSigma(series, base.RMSSD_60).sigma;
      }
      base.calib_meta = {
        artifact_ratio: det.artifactRatio,
        contact_ratio: det.contactRatio,
        rmssd_pairs: rmssdPairs,
        segments: { A: (clean.A || []).length, B: (clean.B || []).length, C: (clean.C || []).length },
        estimator: 'window10'      // v3.16：与运行时同款 10s 窗均值
      };

      // θ_ref：首选 B 段（任务参照），仅 B 缺失时回退 A 段（并标记，供报告告警）
      const bTh = (seg.B || []).map(f => f.theta).filter(Number.isFinite);
      const aTh = (seg.A || []).map(f => f.theta).filter(Number.isFinite);
      if (bTh.length >= 10) { base.theta_ref = median(bTh); base.theta_ref_src = 'B'; }
      else if (aTh.length >= 10) { base.theta_ref = median(aTh); base.theta_ref_src = 'A-fallback'; }
      else { base.theta_ref_src = 'default'; }
      if (aTh.length) base.theta_rest = median(aTh);
      // v3.4：A 段 δ 只作 delta_rest 参考（判据参照已改 B 段）
      const aDl = (seg.A || []).map(f => f.delta).filter(Number.isFinite);
      if (aDl.length) base.delta_rest = median(aDl);
      base.delta_src = (seg.B || []).length >= 10 ? 'B' : 'default';

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
      /* 伪迹/接触率：**优先复用** calibrateFromFrames 回放时记下的计数（v3.4.1，与 Python 同步）。
         旧实现此处再回放一遍 = 对同一批帧跑第二个 detector，两侧配置不同时（一个带 base 进
         肌电门、一个不带）会给出两个互相矛盾的"伪迹率"。 */
      const meta = base.calib_meta || {};
      let artifactRatio, contactRatio;
      if (meta.artifact_ratio != null) {
        artifactRatio = meta.artifact_ratio;
        contactRatio = meta.contact_ratio || 0;
      } else {                                   // 外部传入旧基线（无 calib_meta）才回放
        const det = new ArtifactDetector(base);
        for (const it of (frames || [])) {
          if (it && it.frame) det.push(it.frame);
        }
        artifactRatio = det.artifactRatio;
        contactRatio = det.contactRatio;
      }
      const rmssdPairs = meta.rmssd_pairs;

      let cZ = null;
      const alphaStat = base.alpha, aStat = base.A_rel;
      if (alphaStat && aStat && alphaStat.mu > 0 && counts.C >= 10) {
        // v3.16：C 段锚点用运行时同款 10s 窗均值（与 Python 同步）
        const cFrames = (seg.C || []).filter(f => f.signal == null || f.signal < K.SIGNAL_BAD);
        const vals = StateEngine._windowedSeries(cFrames, K.WINDOW_EEG_S, sub => {
          const a = sub.map(f => f.alpha).filter(Number.isFinite);
          return a.length ? ((a.reduce((s, v) => s + v, 0) / a.length) - alphaStat.mu) / alphaStat.mu : NaN;
        }).filter(Number.isFinite);
        if (vals.length) {
          const med = StateEngine._median(vals);
          const sigma = Math.max(aStat.sigma, aStat.sigma_min);
          cZ = (med - aStat.mu) / sigma;
        }
      }
      const warnings = [];
      if (counts.B < 10) warnings.push(`B 段样本不足（${counts.B} 帧 <10），基线沿用默认值，校准无效，请重做`);
      if (counts.A < 10) warnings.push(`A 段样本不足（${counts.A} 帧 <10），δ 基线沿用默认值，嗜睡判据不可信`);
      if (counts.C < 10) warnings.push(`C 段样本不足（${counts.C} 帧 <10），无法校验放松态可判性`);
      if (artifactRatio > K.CALIB_ARTIFACT_MAX) warnings.push(`伪迹帧占比 ${(artifactRatio * 100).toFixed(0)}% 超限，请检查佩戴`);
      if (contactRatio > 0.10) warnings.push(`接触无效帧占比 ${(contactRatio * 100).toFixed(0)}%（signal≥${K.SIGNAL_BAD}）> 10%，电极贴合不良，请检查佩戴后重做校准`);
      if (rmssdPairs != null && rmssdPairs < K.RMSSD_MIN_PAIRS) warnings.push(`B 段 RMSSD 有效差分对仅 ${rmssdPairs} < ${K.RMSSD_MIN_PAIRS}，HRV 判据（stress/relaxed）不可用——检查心率是否接通`);
      if (cZ != null && cZ < K.Z_RELAX_A) warnings.push(`C 段放松锚点 z=${cZ.toFixed(2)} < ${K.Z_RELAX_A}，放松态在当前基线下不可判`);
      return {
        /* v3.4.1：ok 补上"判据不可用"两条（设计文档 §6.1 早已如此，代码此前只判前两条）。
           c_anchor_z 不纳入 ok——它反映受试者放松能力/引导强度，只告警。
           v3.6 修正（与 state_engine 同步）：RMSSD 差分对移出 ok，改为独立 hrv_ok。
           四次真机实测该设备在纯 Python 解析器下永不推 RR → pairs 恒 0 → ok 恒 false
           → 界面永远"校准未通过"，而重戴无解。ok 语义对齐为"EEG 基线可用于 7 态判别"。 */
        ok: counts.B >= 10 && counts.A >= 10 && counts.C >= 10
          && artifactRatio <= K.CALIB_ARTIFACT_MAX
          && contactRatio <= 0.10,
        hrv_ok: (rmssdPairs == null || rmssdPairs >= K.RMSSD_MIN_PAIRS),
        segments: counts, artifactRatio, theta_ref: base.theta_ref,
        theta_ref_src: base.theta_ref_src, theta_rest: base.theta_rest,
        beta_ref: base.beta_ref, gamma_ref: base.gamma_ref,
        emg_gate: !!K.ARTIFACT_EMG_GATE,
        c_anchor_z: cZ, warnings
      };
    }

    /* ---- 从 JSON（导出的基线文件）还原为基线对象 ---- */
    /* 允许的来源标记。导入的 JSON 里这个字段是自由字符串，
       不收敛的话可以伪造一份"看起来已完成正式校准"的基线（安全报告 3.5）。 */
    static get SRC_ALLOWED() { return new Set(['default', 'A-fallback', 'B']); }

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
        } else if (k === 'theta_ref_src') {
          // v3.10：来源标记收敛到枚举。非法/伪造值记为 imported-unknown，
          // 避免"随便写个 B 就显示成正式校准"。
          base[k] = StateEngine.SRC_ALLOWED.has(v) ? v : 'imported-unknown';
        } else if (typeof v === 'string') {
          base[k] = v;                                  // 其他字符串字段（当前仅 theta_ref_src，留作扩展）
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
      this._det.reset();          // 基线换了 → 平滑与伪迹参照系都要重建
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

  global.BrainState = { K, Frame, BaselineStats, Decision, Feedback, ArtifactDetector, StateEngine, clamp, safeDiv };

})(window);
