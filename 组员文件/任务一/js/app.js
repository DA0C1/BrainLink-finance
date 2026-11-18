/* =============================================================================
 * app.js — 输出/提醒层演示界面 交互主逻辑
 * -----------------------------------------------------------------------------
 * 交互清单：
 *  点击：模式切换 / 播放·单帧·重置·注入伪迹 / 8 状态网格注入 / 提醒筛选 /
 *        决策模拟 / 决策反馈 / 抽屉导航 / 主题
 *  拖拽：优先级队列排序（Pointer Events，桌面+移动）、提醒弹窗搬移、
 *        桌面角标搬移、参数滑杆、曲线十字线
 *  切换：自动轮播 / 手动注入 / 粘贴数据；A-B 段；主题；免打扰；语音；通知
 *  表单：参数输入与校验、语音音色/语速/音量、播报级别、粘贴 JSONL 解析
 *  动画：环形置信度、状态切换弹跳、提醒滑入、角标脉冲、冷却条、曲线绘制
 * ========================================================================== */
(function () {
  'use strict';

  const { K, Frame, StateEngine } = window.BrainState;
  const MOCK = window.MockSource;

  /* ============================ DOM ============================ */
  const $ = (id) => document.getElementById(id);
  const el = {
    // 控制
    segMode: $('segMode'), btnPlay: $('btnPlay'), playLabel: $('playLabel'),
    // v3.8 链路提示条 + 电极接触指标
    notice: $('notice'), noticeIco: $('noticeIco'), noticeTxt: $('noticeTxt'),
    btnNoticeRetry: $('btnNoticeRetry'), mSignal: $('mSignal'),
    // v3.13 未校准横幅 + 无 RR 说明
    calibBanner: $('calibBanner'), calibBannerTxt: $('calibBannerTxt'),
    btnCalibBanner: $('btnCalibBanner'), metricsNote: $('metricsNote'), stateHint: $('stateHint'),
    // v3.10 数据源诊断 + 校准时间戳
    diagBox: $('diagBox'), diagTitle: $('diagTitle'), diagDesc: $('diagDesc'),
    btnDiagRefresh: $('btnDiagRefresh'),
    dgSource: $('dgSource'), dgFrames: $('dgFrames'), dgClients: $('dgClients'), dgUptime: $('dgUptime'),
    cbCalibAt: $('cbCalibAt'),
    btnStep: $('btnStep'), btnReset: $('btnReset'), btnArtifact: $('btnArtifact'),
    rngStep: $('rngStep'), lblStep: $('lblStep'),
    rngSpeed: $('rngSpeed'), lblSpeed: $('lblSpeed'),
    vClock: $('vClock'), vFrames: $('vFrames'), vArtifact: $('vArtifact'),
    chkTask: $('chkTask'), chkVoice: $('chkVoice'), chkDnd: $('chkDnd'),
    // 状态区
    stateHero: $('stateHero'), heroIcon: $('heroIcon'), ringFg: $('ringFg'),
    heroRing: document.querySelector('.hero-ring'), vWeak: $('vWeak'),
    vConf: $('vConf'), vStateCn: $('vStateCn'), vStateKey: $('vStateKey'),
    vLevel: $('vLevel'), vCooldown: $('vCooldown'), vAction: $('vAction'),
    mAttMed: $('mAttMed'), mRtb: $('mRtb'), mRmssd: $('mRmssd'),
    mHr: $('mHr'), mStill: $('mStill'), mIf: $('mIf'),
    stateGrid: $('stateGrid'), prioList: $('prioList'), btnPrioReset: $('btnPrioReset'),
    // 曲线区
    vCalm: $('vCalm'), vTrend: $('vTrend'), vRoll: $('vRoll'), vCalmA: $('vCalmA'), vCalmB: $('vCalmB'),
    calmZero: $('calmZero'),
    vCntA: $('vCntA'), vCntB: $('vCntB'), abA: $('abA'), abB: $('abB'),
    abSwitch: $('abSwitch'), chart: $('calmChart'), chartTip: $('chartTip'),
    chartEmpty: $('chartEmpty'), curveRange: $('curveRange'),
    zbarList: $('zbarList'), cdList: $('cdList'),
    // 提醒区
    remindFilter: $('remindFilter'), remindStream: $('remindStream'), remindCount: $('remindCount'),
    remindStreamAll: $('remindStreamAll'),
    distRing: $('distRing'), distArcs: $('distArcs'), distLegend: $('distLegend'), distTotal: $('distTotal'),
    prevBadge: $('prevBadge'), prevPopup: $('prevPopup'),
    vTotal: $('vTotal'), vImp: $('vImp'), vIsImp: $('vIsImp'),
    // 悬浮
    badge: $('badge'), badgePanel: $('badgePanel'), bdgState: $('bdgState'),
    bdgConf: $('bdgConf'), bdgCount: $('bdgCount'), bpState: $('bpState'),
    bpLevel: $('bpLevel'), bpAction: $('bpAction'), bpConf: $('bpConf'),
    bpCool: $('bpCool'), bpClose: $('bpClose'), toastLayer: $('toastLayer'),
    // 抽屉
    drawer: $('drawer'), drawerTitle: $('drawerTitle'), drawerClose: $('drawerClose'),
    drawerTabs: $('drawerTabs'), scrim: $('scrim'),
    paneDetail: $('paneDetail'), paneSettings: $('paneSettings'), paneAbout: $('paneAbout'),
    paneCalib: $('paneCalib'),
    // 校准面板（v2.9.2）
    cbThetaRef: $('cbThetaRef'), cbSrc: $('cbSrc'), cbThetaRest: $('cbThetaRest'),
    btnCalibStart: $('btnCalibStart'), btnCalibCancel: $('btnCalibCancel'),
    btnBaselineDefault: $('btnBaselineDefault'), chkCalibFull: $('chkCalibFull'),
    calibStep: $('calibStep'), calibBar: $('calibBar'), calibReport: $('calibReport'),
    btnBaselineExport: $('btnBaselineExport'), fileBaseline: $('fileBaseline'),
    txtBridge: $('txtBridge'), btnBridgeConnect: $('btnBridgeConnect'),
    btnBridgeClose: $('btnBridgeClose'), bridgeStatus: $('bridgeStatus'),
    liveHint: $('liveHint'),
    btnDetail: $('btnDetail'), btnSettings: $('btnSettings'), btnAbout: $('btnAbout'),
    btnCalib: $('btnCalib'),
    btnTheme: $('btnTheme'), brandMark: $('brandMark'), demoTag: $('demoTag'),
    gridCap: $('gridCap'),
    // 主界面 → 抽屉的入口按钮
    btnOpenDetail: $('btnOpenDetail'), btnOpenLegend: $('btnOpenLegend'), btnOpenRemind: $('btnOpenRemind'),
    btnExportStats: $('btnExportStats'),
    // 设置控件
    sWeakConf: $('sWeakConf'), lblWeakConf: $('lblWeakConf'),
    sDebounce: $('sDebounce'), lblDebounce: $('lblDebounce'),
    sHitRatio: $('sHitRatio'), lblHitRatio: $('lblHitRatio'),
    sCdScale: $('sCdScale'), lblCdScale: $('lblCdScale'), sAdapt: $('sAdapt'),
    sDemoBoost: $('sDemoBoost'),
    btnNotifyPerm: $('btnNotifyPerm'), notifyCap: $('notifyCap'), sUseNotify: $('sUseNotify'),    sVoice: $('sVoice'), sVoiceRate: $('sVoiceRate'), lblRate: $('lblRate'),
    sVoiceVol: $('sVoiceVol'), lblVol: $('lblVol'), sVoiceSel: $('sVoiceSel'),
    voiceLevels: $('voiceLevels'), btnTestVoice: $('btnTestVoice'),
    pasteArea: $('pasteArea'), btnSample: $('btnSample'), btnLoadPaste: $('btnLoadPaste'),
    pasteMsg: $('pasteMsg'), btnFastIf: $('btnFastIf'), btnClearIf: $('btnClearIf')
  };

  /* ============================ 状态 ============================ */
  const S = {
    mode: 'auto',
    playing: false,
    hasPlayed: false,     // v3.13：区分"从未开始"与"暂停中"——按钮文案与链路提示都要用
    stepSec: 1,
    fps: 8,
    phase: 'focus',
    simClock: 0,
    frames: 0,
    tourIdx: 0,
    tourFrame: 0,
    pasteQueue: [],
    pasteIdx: 0,
    lastResponse: null,
    history: [],          // {t, calm, roll, family, state, level, conf, event}
    decisions: [],        // 每次决策是否冲动（true/false），用于滚动窗口冷静度
    remindEvents: [],     // {id, t, state, level, action, conf}
    seq: 0,
    unread: 0,
    spanLimit: 120,
    filter: 'all',
    gradedLevel: 'silent',   // v3.13：含 strong 的最终提醒等级（引擎只出前三级）
    hover: null,
    lastDecisionBump: null,
    // v3.16：来自 /health 的帧源状态。源线程死亡时 SSE 仍可能是 open，
    // 只靠"多久没帧"会把 COM 选错误诊成蓝牙卡顿，这里单独记一份。
    srcError: null,
    srcAlive: null,
    srcAge: null
  };

  let engine = null;
  let rnd = MOCK.makeRnd(20261023);

  /* ============================ 演示 / 产品形态 ============================
   * 这个页面本质是"演示界面"：手动注入、粘贴数据、单帧、格子点击注入都是演示工具。
   * 它们此前与真实控制项平铺在一起，产品视角看像是"用户可以随手改输入"。
   * 顶栏标题旁给一个「演示 / 产品」标记，点击切换（?prod=1 可直接进产品形态），
   * 选择记进 localStorage。产品形态保留完整判别与提醒链路，只收起注入类入口。 */
  let prodMode = false;
  function applyProdMode(on, persist) {
    prodMode = !!on;
    document.documentElement.dataset.prod = prodMode ? '1' : '0';
    if (el.demoTag) {
      el.demoTag.textContent = prodMode ? '产品' : '演示';
      el.demoTag.dataset.prod = prodMode ? '1' : '0';
      el.demoTag.title = prodMode
        ? '当前为产品形态：已隐藏手动注入 / 粘贴数据 / 单帧 / 格子点击注入。点击切回演示形态。'
        : '当前为演示形态：保留全部注入工具，便于答辩演示。点击切到产品形态。';
    }
    if (el.segMode) {
      el.segMode.querySelectorAll('[data-mode="manual"],[data-mode="paste"]')
        .forEach(b => { b.hidden = prodMode; });
    }
    if (el.btnStep) el.btnStep.hidden = prodMode;
    // 产品形态下不能再写"点击可注入"——那正是被收起来的功能
    if (el.gridCap) {
      el.gridCap.textContent = prodMode
        ? '亮点=门控成立 · 数字=加权得分（越高证据越强）'
        : '亮点=门控成立 · 数字=加权得分 · 点击可注入';
    }
    if (prodMode && (S.mode === 'manual' || S.mode === 'paste')) setModeSeg('auto');
    if (persist) save('prodMode', prodMode);
    syncStateGridSelection();
  }

  /* ============================ 真机校准（§六 三段） ============================
   * v2.9.2 新增。此前 UI 硬编码 defaultBaseline()、engine.js 也没有校准移植，
   * 真机基线进不了界面 → 阶段 B 无法验收。现补齐：
   *   A 闭眼静息（δ 基线）→ B 睁眼静坐（全部 z 基线 + θ_ref）→ C 闭眼放松（校验放松态可判）
   * 采集直接复用当前数据源（mock / 粘贴 / 真机桥），因此 mock 下也能完整走一遍流程自测。
   * 时长按"秒"计（每帧 S.stepSec 秒）；快速模式 15/40/40s 便于演示，完整模式按协议 30/120/120s。
   * ========================================================================== */
  const CAL = {
    active: false, seg: 'A', t: 0, buf: [],
    targets: { A: 40, B: 20, C: 40 }, full: false
  };
  const CAL_ORDER = ['A', 'B', 'C'];
  const CAL_LABEL = {
    A: 'A 段 · 闭眼静息（δ 基线）',
    B: 'B 段 · 睁眼静坐（z 基线 + θ_ref）',
    C: 'C 段 · 闭眼放松（校验放松态可判）'
  };

  function calibTargets(full) {
    // 时长必须与设计文档 §六 一致：A 30s（闭眼静息，估 δ 中位数 25 帧足够）/ B 120s
    // （睁眼任务，全部 z 基线的 μ/σ 都取自这一段，样本越薄 σ 越不可靠）/ C 120s（放松锚点）。
    // v3.2 修正：原写 A=120/B=60 把 A、B 时长对调了——A 段白采 90s，B 段只剩 50 帧建 σ。
    return full ? { A: 30, B: 120, C: 120 } : { A: 15, B: 40, C: 40 };
  }

  function startCalib() {
    CAL.active = true; CAL.seg = 'A'; CAL.t = 0; CAL.buf = [];
    CAL.full = !!(el.chkCalibFull && el.chkCalibFull.checked);
    CAL.targets = calibTargets(CAL.full);
    el.btnCalibStart.disabled = true;
    el.btnCalibCancel.hidden = false;
    el.calibReport.hidden = true;
    el.calibBar.style.width = '0%';
    if (S.mode === 'live' && LiveBridge.status !== 'open') {
      el.bridgeStatus.textContent = '未连接（将用当前数据源演示校准流程）';
    }
    setPlaying(true);
    renderCalibProgress();
  }

  function cancelCalib() {
    CAL.active = false; CAL.buf = []; CAL.t = 0;
    el.btnCalibStart.disabled = false;
    el.btnCalibCancel.hidden = true;
    el.calibBar.style.width = '0%';
    el.calibStep.textContent = '已取消。可重新开始校准。';
  }

  /** 每帧采集（由 stepOnce 调用）。
   *  v3.14 修正：计时固定为 **1 秒/帧**，不再用 S.stepSec。
   *  原写法让校准时长跟着"演示步长"走 —— 步长调到 5 时，A 段 15s 只要 3 帧就结束，
   *  A/B/C 实测 3/8/8 帧，`ok` 判 False，但基线仍被保存（见 finishCalib）。
   *  校准是方法学前置条件，不该被演示参数影响。伪迹过滤在 calibrateFromFrames 内做。 */
  function calibCollect(f) {
    CAL.buf.push({ tag: CAL.seg, frame: f });
    CAL.t += 1;
    if (CAL.t >= CAL.targets[CAL.seg]) {
      const i = CAL_ORDER.indexOf(CAL.seg);
      if (i < CAL_ORDER.length - 1) {
        CAL.seg = CAL_ORDER[i + 1];
        CAL.t = 0;
      } else {
        finishCalib();
        return;
      }
    }
    renderCalibProgress();
  }

  function renderCalibProgress() {
    const total = CAL_ORDER.reduce((s, k) => s + CAL.targets[k], 0);
    let done = 0;
    for (const k of CAL_ORDER) {
      if (k === CAL.seg) { done += Math.min(CAL.t, CAL.targets[k]); break; }
      done += CAL.targets[k];
    }
    el.calibBar.style.width = Math.min(100, (done / total) * 100).toFixed(1) + '%';
    const left = Math.max(0, Math.ceil(CAL.targets[CAL.seg] - CAL.t));
    el.calibStep.textContent = `${CAL_LABEL[CAL.seg]} · 还需 ${left}s`;
  }

  function finishCalib() {
    CAL.active = false;
    el.btnCalibStart.disabled = false;
    el.btnCalibCancel.hidden = true;
    el.calibBar.style.width = '100%';
    let base, rep;
    const prevBaseline = engine.baseline;   // 失败时回退用
    try {
      base = StateEngine.calibrateFromFrames(CAL.buf);
      rep = StateEngine.calibrationReport(CAL.buf, base);
    } catch (e) {
      el.calibStep.textContent = '校准计算失败：' + e.message;
      return;
    }
    /* v3.14：校准未通过完整性检查时**不再落盘**。
       原实现"失败也应用并保存"，后果不只是"基线可疑"：isUncalibrated() 只看
       calibBase 存不存在，于是真机模式下未校准横幅从此不再出现，界面反而宣称
       "已校准"。实测（步长 5）A/B/C=3/8/8 帧、ok=False、θ_ref 仍来自 default，
       却被当成有效基线 —— 这是方法学级别的假阳性。
       现在：失败只出报告、不保存、引擎回退原基线，横幅照常提示。 */
    const accepted = !!rep.ok;
    if (accepted) {
      engine.setBaseline(base);
      save('calibBase', StateEngine.serializeBaseline(base));
      save('calibMeta', { at: new Date().toISOString(), report: {
        segments: rep.segments, theta_ref: rep.theta_ref, theta_ref_src: rep.theta_ref_src,
        theta_rest: rep.theta_rest, c_anchor_z: rep.c_anchor_z, ok: rep.ok
      } });
    } else {
      engine.setBaseline(prevBaseline);
    }
    renderCalibInfo();
    const lines = [];
    lines.push(accepted
      ? (rep.hrv_ok
        ? '校准完成（已应用到引擎并保存到本机）'
        : '校准完成（EEG 基线可用 · 7 态；HRV 不可用 → stress 不可判，属硬件通道限制）')
      : '校准未通过完整性检查：基线未保存，仍沿用上一份。请按下列告警重做。');
    lines.push(`段样本 A/B/C = ${rep.segments.A}/${rep.segments.B}/${rep.segments.C} 帧 · 伪迹 ${(rep.artifactRatio * 100).toFixed(0)}%`);
    lines.push(`θ_ref = ${Number(rep.theta_ref).toFixed(1)}（来源 ${rep.theta_ref_src}）` +
      (rep.theta_rest != null ? ` · θ_rest = ${Number(rep.theta_rest).toFixed(1)}（闭眼参考，仅展示）` : ''));
    if (rep.c_anchor_z != null) {
      lines.push(`C 段放松锚点 z = ${rep.c_anchor_z.toFixed(2)}（门控 ${K.Z_RELAX_A}）` +
        (rep.c_anchor_z >= K.Z_RELAX_A ? ' → 放松态可判' : ' → 放松态不可判'));
    }
    if (rep.beta_ref != null) {
      lines.push(`β/γ 参照 = ${Number(rep.beta_ref).toFixed(0)} / ${Number(rep.gamma_ref).toFixed(0)}` +
        `（肌电专用门 ${rep.emg_gate ? '已启用' : '未启用'}）`);
    }
    rep.warnings.forEach(w => lines.push('• ' + w));
    el.calibStep.textContent = '校准已完成';
    el.calibReport.hidden = false;
    el.calibReport.innerHTML = lines.map(escapeHtml).join('<br>');
  }

  /** 校准面板的当前基线摘要 */
  function renderCalibInfo() {
    const b = engine && engine.baseline ? engine.baseline : {};
    const th = (typeof b.theta_ref === 'number') ? b.theta_ref : null;
    el.cbThetaRef.textContent = th != null ? th.toFixed(1) : '—';
    el.cbSrc.textContent = b.theta_ref_src || 'default';
    el.cbThetaRest.textContent = (typeof b.theta_rest === 'number') ? b.theta_rest.toFixed(1) : '—';
    el.cbSrc.title = b.theta_ref_src === 'default'
      ? '当前为 mock 默认基线：真机使用前必须校准'
      : b.theta_ref_src === 'imported-unknown'
        ? '导入文件的来源标记不可识别（已按未验证处理），请核对这份基线是否可信'
        : '来自校准采集';

    // v3.10：显示上次校准时间。calibMeta.at 从 v3.6 就在存，但界面一直没读，
    // 现场无法判断这份基线是刚校的还是三天前的。
    if (el.cbCalibAt) {
      const meta = load('calibMeta', null);
      const at = meta && meta.at ? new Date(meta.at) : null;
      if (b.theta_ref_src === 'default') {
        el.cbCalibAt.textContent = '未校准';
      } else if (!at || isNaN(at.getTime())) {
        el.cbCalibAt.textContent = '未知（旧版保存）';
      } else {
        const mm = String(at.getMonth() + 1).padStart(2, '0');
        const dd = String(at.getDate()).padStart(2, '0');
        const hh = String(at.getHours()).padStart(2, '0');
        const mi = String(at.getMinutes()).padStart(2, '0');
        const mins = Math.floor((Date.now() - at.getTime()) / 60000);
        const age = mins < 60 ? mins + ' 分钟前'
          : mins < 1440 ? Math.floor(mins / 60) + ' 小时前' : Math.floor(mins / 1440) + ' 天前';
        el.cbCalibAt.textContent = mm + '-' + dd + ' ' + hh + ':' + mi + ' · ' + age;
      }
    }
  }

  function exportBaseline() {
    const data = StateEngine.serializeBaseline(engine.baseline);
    const payload = { version: 'v2.9.2', exported_at: new Date().toISOString(), baseline: data };
    const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'baseline_' + new Date().toISOString().slice(0, 10) + '.json';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  function importBaseline(file) {
    const r = new FileReader();
    r.onload = () => {
      try {
        const obj = JSON.parse(String(r.result));
        const raw = obj && obj.baseline ? obj.baseline : obj;
        const base = StateEngine.hydrateBaseline(raw);
        engine.setBaseline(base);
        save('calibBase', StateEngine.serializeBaseline(base));
        // 导入也是"一次校准结果"，时间戳要跟着更新，否则界面会显示上一份的时间
        save('calibMeta', { at: new Date().toISOString(), imported: true });
        renderCalibInfo();
        el.calibReport.hidden = false;
        el.calibReport.textContent = '基线已导入并生效（θ_ref=' +
          (typeof base.theta_ref === 'number' ? base.theta_ref.toFixed(1) : '—') + '）。';
      } catch (e) {
        el.calibReport.hidden = false;
        el.calibReport.textContent = '导入失败：文件不是有效的基线 JSON（' + e.message + '）';
      }
    };
    r.onerror = () => {
      el.calibReport.hidden = false;
      el.calibReport.textContent = '导入失败：文件读取错误（权限/文件被占用/不是文本文件）';
    };
    r.readAsText(file);
  }

  function resetBaseline() {
    engine.setBaseline(StateEngine.defaultBaseline());
    try { localStorage.removeItem(STORE_PREFIX + 'calibBase'); } catch (e) { /* 隐私模式 */ }
    try { localStorage.removeItem(STORE_PREFIX + 'calibMeta'); } catch (e) { /* 隐私模式 */ }
    renderCalibInfo();
    el.calibReport.hidden = false;
    el.calibReport.textContent = '已恢复默认（mock）基线。真机使用前请重新校准。';
  }

  /* ---- 数据源诊断（v3.10） ----
     /health 从 v2.9 就有，但前端从未消费：现场要判断"服务是不是活着、有没有人在看、
     推了多少帧"只能另开终端 curl。这里把它接成界面里的一块读数。 */
  function fmtUptime(sec) {
    const s = Math.max(0, Math.floor(Number(sec) || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return h + ' 小时 ' + m + ' 分';
    if (m) return m + ' 分 ' + (s % 60) + ' 秒';
    return s + ' 秒';
  }

  function setDiag(state, title, desc) {
    if (!el.diagBox) return;
    el.diagBox.dataset.state = state;
    el.diagTitle.textContent = title;
    if (desc != null) el.diagDesc.textContent = desc;
  }

  async function refreshDiag() {
    if (!el.diagBox) return;
    setDiag('idle', '检测中…', '正在查询 /health');
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), 2500);
    const t0 = performance.now();
    try {
      const r = await fetch(healthUrl(), { signal: ctrl.signal, cache: 'no-store' });
      clearTimeout(timer);
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const j = await r.json();
      const ms = Math.round(performance.now() - t0);
      el.dgSource.textContent = String(j.source || '–');
      el.dgFrames.textContent = String(j.frames ?? '–');
      el.dgClients.textContent = String(j.clients ?? '–');
      el.dgUptime.textContent = fmtUptime(j.uptime);
      S.srcError = j.error || null;
      S.srcAlive = j.alive !== false;
      S.srcAge = (typeof j.last_frame_age === 'number') ? j.last_frame_age : null;
      // 在线面板数与当前这条 SSE 连接对得上才算"健康"
      if (j.error) {
        setDiag('err', '帧源已停止', j.error + ' —— 检查串口/CSV，或查看启动窗口日志');
      } else if (j.alive === false) {
        setDiag('warn', '帧源已结束', '回放播放完毕或源已退出；重新连接或切回模拟数据');
      } else if (S.srcAge != null && S.srcAge > 8) {
        setDiag('warn', '帧源无新数据', '最近一帧在 ' + Math.round(S.srcAge) + ' 秒前 —— 检查设备/串口');
      } else {
        setDiag('ok', '服务正常', '响应 ' + ms + ' ms · 已运行 ' + fmtUptime(j.uptime));
      }
    } catch (e) {
      clearTimeout(timer);
      const why = e && e.name === 'AbortError' ? '超时（>2.5s）' : (e && e.message) || '未知错误';
      el.dgSource.textContent = '–';
      el.dgFrames.textContent = '–';
      el.dgClients.textContent = '–';
      el.dgUptime.textContent = '–';
      setDiag('err', '无法连接服务', why + ' —— 确认 live_bridge 已启动，且页面是从它这里打开的');
    }
  }

  /* ---- 链路健康（v3.8） ----
     此前断线只在底栏一行小字体现，主区数字照旧不动，看起来像"还在正常监测"。
     真机上这比没有提示更危险：受试者已脱戴、界面仍显示 focus，会被当成有效数据。 */
  const LINK_STALE_S = 6;          // 设备约 1Hz，6s 无帧即判停更
  let lastFrameAt = 0;             // 最近一次收到实时帧的时刻
  let openedAt = 0;                // 本次连接建立时刻
  let lastHealthPoll = 0;          // /health 轮询节流（10s）

  /** v3.16：只更新帧源状态，不碰诊断面板（面板由 refreshDiag 负责）。
   *  为何单独轮询：源线程死亡后 SSE 仍是 open，6s 停更会把"COM 选错"说成"蓝牙卡顿"；
   *  拿到 /health 的 error/alive 才能给出正确结论。 */
  async function pollSourceHealth() {
    try {
      const r = await fetch(healthUrl(), { cache: 'no-store' });
      if (!r.ok) return;
      const j = await r.json();
      S.srcError = j.error || null;
      S.srcAlive = j.alive !== false;
      S.srcAge = (typeof j.last_frame_age === 'number') ? j.last_frame_age : null;
    } catch (e) { /* 桥本身断了由 SSE 状态处理，这里不重复报 */ }
  }

  function updateSignal(sig) {
    if (!el.mSignal) return;
    if (sig == null || !Number.isFinite(sig)) {
      el.mSignal.textContent = '–'; el.mSignal.dataset.q = 'na'; return;
    }
    // poorSignalLevel：0 最好 / 200 电极未接触（协议定义，与 K.SIGNAL_BAD 同源）
    const q = sig >= K.SIGNAL_BAD ? 'bad' : (sig <= 50 ? 'good' : 'fair');
    el.mSignal.textContent = q === 'bad' ? '未接触' : (q === 'good' ? '良好 ' : '一般 ') + sig;
    el.mSignal.dataset.q = q;
  }

  function updateLinkNotice() {
    if (!el.notice) return;
    if (S.mode !== 'live') { el.notice.hidden = true; return; }
    // v3.13：暂停 ≠ 断流。暂停时 lastFrameAt 不再更新（stepOnce 不跑），
    // 旧逻辑会在 6s 后弹出"蓝牙卡顿或设备休眠"并给「重新连接」——
    // 用户自己按的暂停，界面却在报硬件故障。这里先分流。
    if (!S.playing && S.hasPlayed) {
      el.notice.hidden = false;
      el.notice.dataset.level = 'warn';
      el.noticeIco.innerHTML = Icons.svg('pause');
      el.noticeTxt.textContent = '已暂停 —— 画面数值冻结在暂停时刻，不代表当前状态。'
        + '点右侧「继续推流」恢复（数据源仍保持连接）。';
      el.btnNoticeRetry.textContent = '继续推流';
      el.btnNoticeRetry.dataset.act = 'resume';
      el.btnNoticeRetry.hidden = false;
      return;
    }
    const st = LiveBridge.status;
    const now = performance.now();
    if (now - lastHealthPoll > 10000) { lastHealthPoll = now; pollSourceHealth(); }

    // v3.16：源线程已死（COM 错/CSV 被删/解析器异常）——不能再说"蓝牙卡顿"。
    // 这是与"暂停"、"SSE 断开"并列的第三种状态。
    if (S.srcError) {
      el.notice.hidden = false;
      el.notice.dataset.level = 'alert';
      el.noticeIco.innerHTML = Icons.svg('alert');
      el.noticeTxt.textContent = '数据源已停止：' + S.srcError
        + ' —— 下方数值已冻结，不代表当前状态。';
      el.btnNoticeRetry.textContent = '查看设置';
      el.btnNoticeRetry.dataset.act = 'settings';
      el.btnNoticeRetry.hidden = false;
      return;
    }
    if (S.srcAlive === false) {
      el.notice.hidden = false;
      el.notice.dataset.level = 'warn';
      el.noticeIco.innerHTML = Icons.svg('info');
      el.noticeTxt.textContent = '数据源已结束（回放播放完毕或源已退出）——'
        + '下方数值是最后一帧，不代表当前状态。';
      el.btnNoticeRetry.textContent = '重新连接';
      el.btnNoticeRetry.dataset.act = 'reconnect';
      el.btnNoticeRetry.hidden = false;
      return;
    }

    const stale = lastFrameAt ? (now - lastFrameAt) / 1000 : 0;
    const waiting = st === 'open' && !lastFrameAt;
    const waitS = waiting ? (now - openedAt) / 1000 : 0;

    if (st === 'open' && !waiting && stale < LINK_STALE_S) { el.notice.hidden = true; return; }

    el.notice.hidden = false;
    el.btnNoticeRetry.textContent = '重新连接';
    el.btnNoticeRetry.dataset.act = 'reconnect';
    if (st !== 'open') {
      el.notice.dataset.level = 'alert';
      el.noticeIco.innerHTML = Icons.svg('alert');
      el.noticeTxt.textContent = '数据源未连接' + (st === 'connecting' ? '（连接中…）' : '')
        + ' —— 下方数值是最后一次收到的数据，不代表当前状态。';
    } else if (waiting && waitS > 5) {
      el.notice.dataset.level = 'warn';
      el.noticeIco.innerHTML = Icons.svg('info');
      el.noticeTxt.textContent = '已连接，但 ' + Math.round(waitS) + ' 秒内没有收到任何数据帧 —— '
        + '查头环是否休眠、是否被手机 App 占用。';
    } else {
      el.notice.dataset.level = 'warn';
      el.noticeIco.innerHTML = Icons.svg('pause');
      el.noticeTxt.textContent = '数据已 ' + Math.round(stale) + ' 秒未更新 —— 数值已冻结，'
        + '不代表当前状态（蓝牙卡顿或设备休眠）。';
    }
    el.btnNoticeRetry.hidden = false;
  }

  /* ---- v3.13：未校准横幅 ----
   * 真机模式 + 本机没有**可信**校准基线时，引擎会回落到通用基线（θ_ref=55）。
   * 拿别人的脑电判你的状态，结论只能当演示 —— 但此前主界面一个字都不提，
   * 「未校准」只藏在抽屉里。这是一条方法学风险，必须在主区说出口。
   * v3.14：判据从"有没有存过 base"收紧为"这份 base 是否通过完整性检查" ——
   * 历史版本会把 ok=False 的失败校准也存下来，只看存在性会漏报。 */
  function isUncalibrated() {
    const base = load('calibBase', null);
    if (!base) return true;
    if (base.theta_ref_src === 'default') return true;      // 校准没取到 B 段参照，等于没校
    const meta = load('calibMeta', null);
    const rep = meta && meta.report;
    if (rep && rep.ok === false) return true;               // 明确判失败的基线不算数
    return false;
  }
  let calibBannerOn = null;
  let calibCheckAt = 0;
  function updateCalibBanner() {
    if (!el.calibBanner) return;
    if (S.mode !== 'live') {
      // 非真机模式不查 localStorage（isUncalibrated 里两次同步读 + JSON.parse）
      if (calibBannerOn !== false) { calibBannerOn = false; el.calibBanner.hidden = true; }
      return;
    }
    // render 每帧调，1s 节流：横幅不是高频信息，没必要 20Hz 读盘
    const now = performance.now();
    if (now - calibCheckAt < 1000 && calibBannerOn !== null) return;
    calibCheckAt = now;
    const show = isUncalibrated();
    if (calibBannerOn === show) return;
    calibBannerOn = show;
    el.calibBanner.hidden = !show;
    if (!show) return;
    el.calibBannerTxt.textContent = '未校准：当前用的是通用基线（θ_ref = 55），'
      + '判据基于他人数据，结论仅供演示。正式测量前请先做三段校准（约 4.5 分钟）。';
  }

  /* ---- 真机数据桥 ---- */
  function bridgeStatusText(st, detail) {
    const map = {
      idle: '未连接', connecting: '连接中…', open: '已连接', error: '连接异常', closed: '已断开'
    };
    el.bridgeStatus.textContent = (map[st] || st) + (detail ? ' · ' + detail : '');
    el.liveHint.hidden = st !== 'open';
    if (st === 'open') { el.liveHint.textContent = '真机/回放已连接'; openedAt = performance.now(); }
    updateLinkNotice();
  }

  function bridgeToken() {
    const q = new URLSearchParams(location.search).get('token');
    if (q) { save('bridgeToken', q); return q; }
    return load('bridgeToken', '') || '';
  }
  function healthUrl() {
    const t = bridgeToken();
    return '/health' + (t ? '?token=' + encodeURIComponent(t) : '');
  }

  function connectBridge() {
    let url = (el.txtBridge.value || '').trim();
    // 允许把 token 直接粘在桥地址里（http://host:8770/?token=xxx）；拆出来单独传给 SSE
    let tok = bridgeToken();
    const m = url.match(/[?&]token=([^&]+)/);
    if (m) {
      tok = decodeURIComponent(m[1]);
      save('bridgeToken', tok);
      url = url.replace(/[?&]token=[^&]*/, '').replace(/[?&]$/, '');
    }
    save('bridgeUrl', url);
    S.srcError = null; S.srcAlive = null; S.srcAge = null;
    lastHealthPoll = 0;
    LiveBridge.connect(url, tok);
  }


  /* ============================ 工具 ============================ */
  const FAMILY_COLOR = { good: 'good', warn: 'warn', alert: 'alert', idle: 'idle' };
  function cssVar(name) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  }
  function familyColor(fam) {
    return cssVar(FAMILY_COLOR[fam] ? '--' + FAMILY_COLOR[fam] : '--idle') || '#888';
  }
  function metaOf(state) { return K.STATE_META[state] || K.STATE_META.neutral; }

  /* ---- v3.13：无 RR 通道判定 ----
   * 硬质头环（BrainLink 单通道）没有 PPG，帧里永远不带 rr_ms。
   * 旧界面把 RMSSD/HR 长期显示为「–」或「窗口未满」，stress 格子永远不亮，
   * 用户会以为"再等等就有"——实际是硬件根本不给这个输入。
   * 判据：跑满 40 帧（约 40s）仍一帧有效 RR 都没见过，即认定无该通道。
   * 用"曾经收到过"而非"当前缓冲非空"，避免 60s 窗滚动清空后误判。 */
  const NO_RR_FRAMES = 40;
  function noRRChannel() {
    return !!engine && engine.frameCount > NO_RR_FRAMES && !engine.rrSeen;
  }
  function updateNoRRNote(noRR) {
    // 「无RR」小签只在本机确实没有该通道时出现；设备带 PPG 时它是噪声。
    document.querySelectorAll('.nr-tag').forEach(t => { t.hidden = !noRR; });
    document.querySelectorAll('.metric[data-norr]').forEach(m => m.classList.toggle('is-norr', noRR));
    // 主界面：把说明并进卡片标题行（不加高度，避免把 8 态网格顶进滚动区）
    if (el.stateHint) {
      el.stateHint.textContent = noRR
        ? '当前状态 · 置信度 · 无 RR 通道'
        : '当前状态 · 置信度 · 6 项核心指标';
      el.stateHint.classList.toggle('is-warn', noRR);
      el.stateHint.title = noRR
        ? '本机无 RR 通道（硬质头环无 PPG）：RMSSD₆₀ / HR 不可算，stress 恒不可判。'
          + '这不是「等窗口填满」，换设备也不会变。'
        : '';
    }
    // 设置面板：完整原因（这里高度不受限）
    if (el.metricsNote) {
      if (!noRR) { el.metricsNote.hidden = true; }
      else if (el.metricsNote.hidden) {
        el.metricsNote.innerHTML = '<b>本机无 RR 通道（无 PPG 传感器）</b><br>'
          + '帧里不带 <code>rr_ms</code>。RMSSD₆₀ 与 HR 依赖 RR 间期，恒不可算；'
          + '压力 <code>stress</code> 的判据依赖 HRV（RMSSD），因此恒不可判。'
          + '这是硬件限制，不是「等窗口填满」。';
        el.metricsNote.hidden = false;
      }
    }
  }






  const redraw = debounce(drawChart, 16);

  /* ============================ 引擎 ============================ */
  function newEngine() {
    const savedPrio = load('priority', null);
    const savedBase = load('calibBase', null);
    const eng = new StateEngine({
      // v2.9.2：优先用本机保存的校准基线（真机必须），无则回落到 mock 默认基线
      baseline: savedBase ? StateEngine.hydrateBaseline(savedBase) : StateEngine.defaultBaseline(),
      priority: Array.isArray(savedPrio) && savedPrio.length === K.PRIORITY.length ? savedPrio : null
    });
    // mock 虚拟时钟兜底（当帧未带 ts 时，每调用 +1s，与 Python _make_mock_clock 一致）
    let vt = 0;
    eng._clock = function () { vt += 1; return vt; };
    const cfg = eng.cfg;
    cfg.cooldownScale = load('cdScale', 1);
    cfg.debounceS = load('debounceS', K.DEBOUNCE_S);
    cfg.hitRatio = load('hitRatio', K.HIT_RATIO);
    cfg.weakConf = load('weakConf', K.WEAK_CONF);
    eng.adaptEnabled = !!load('adapt', false);
    return eng;
  }

  /** 预热：填充 60s HRV 窗口（专注态），与 Python main() 预热一致 */
  function warmup() {
    for (let i = 1; i <= 65; i++) {
      S.simClock += S.stepSec;
      const f = MOCK.makeFrame('focus', { rnd });
      f.ts = S.simClock;
      engine.processFrame(f, { task_active: true });
      S.frames++;
      snapshot();                       // 预热期也入历史，避免刚打开时曲线空白
    }
    S.lastResponse = { decision: engine.lastDecision, feedback: engine.lastFeedback };
  }

  /** 近 10 次决策的滚动冷静度（样本不足返回 null）——比全局累计值更敏感 */
  function rollCalm() {
    const w = S.decisions.slice(-10);
    return w.length ? 1 - w.filter(Boolean).length / w.length : null;
  }

  /** 记录一帧到理智度历史（供曲线绘制） */
  function snapshot() {
    const dec = engine.lastDecision || { state: 'neutral', conf: K.CONF_MIN };
    const fb = engine.lastFeedback || { level: 'silent' };
    S.history.push({
      // v3.13：零决策时冷静度记为 null（曲线断开），不写 1.00 —— 否则曲线前段
      // 会画出一条贴着顶部的"满分线"，与"尚无样本"的文案自相矛盾。
      t: S.simClock, calm: engine.log.total ? engine.calmness : null, roll: rollCalm(),
      family: metaOf(dec.state).family, state: dec.state,
      level: fb.level, conf: dec.conf, event: null
    });
    if (S.history.length > 1500) S.history.shift();
  }

  /* ============================ 数据流 ============================ */
  function nextFrame() {
    if (S.mode === 'live') {
      // 真机/回放：帧来自 live_bridge.py 的 SSE 推送（队列空则本轮不推进，由下一帧触发）
      const o = LiveBridge.shift();
      if (!o) return null;
      lastFrameAt = performance.now();          // v3.8：停更检测
      updateSignal(o.signal);
      return new Frame({
        theta: o.theta, alpha: o.alpha, beta: o.beta, delta: o.delta, gamma: o.gamma,
        attention: o.attention, meditation: o.meditation,
        rr_ms: (o.rr_ms == null ? null : o.rr_ms),
        // v3.3：这两个字段由 live_bridge 的 _clean() 透传（此前白名单里没有，服务端就丢了）。
        // rr_batch 缺失会让引擎退回跨帧差分，RMSSD 系统性高估约 59%。
        rr_batch: Array.isArray(o.rr_batch) ? o.rr_batch : null,
        heart: (o.hr == null ? null : o.hr),
        gyro: Array.isArray(o.gyro) ? o.gyro : [0, 0, 0],
        // v3.8：接触质量必须进帧。engine.js 的 contactBad() 读 frame.signal，
        // 这里不传 → signal 恒 null → 接触门在真机实时链路上从不触发（脱戴帧照样进特征窗）。
        // Python 侧由 SdkAdapter 硬门兜住，JS 侧没有第二道，这行是唯一防线。
        signal: (o.signal == null ? null : Number(o.signal))
      });
    }
    if (S.mode === 'paste') {
      if (S.pasteIdx >= S.pasteQueue.length) {
        // 数据耗尽：保持最后一帧不再推进（不报错）
        return null;
      }
      return S.pasteQueue[S.pasteIdx++];
    }
    if (S.mode === 'auto') {
      if (S.tourFrame >= MOCK.AUTO_TOUR[S.tourIdx].frames) advanceTour();
      const seg = MOCK.AUTO_TOUR[S.tourIdx];
      S.phase = seg.phase;
      S.tourFrame++;
      const opts = { rnd };
      if (seg.phase === 'flow') opts.indexBoost = Math.min(16, 0.25 * S.tourFrame);
      return MOCK.makeFrame(seg.phase, opts);
    }
    // manual
    const opts = { rnd };
    if (S.phase === 'flow') opts.indexBoost = Math.min(16, 0.25 * S.tourFrame++);
    return MOCK.makeFrame(S.phase, opts);
  }

  function taskActiveFor(phaseKey) {
    if (!el.chkTask.checked) return false;
    if (S.mode === 'auto') {
      const p = MOCK.PHASES[phaseKey];
      return p ? !!p.task : true;
    }
    return true;
  }

  /** 轮播推进到下一相位 */
  function advanceTour() {
    S.tourIdx = (S.tourIdx + 1) % MOCK.AUTO_TOUR.length;
    S.tourFrame = 0;
    onEnterPhase(MOCK.AUTO_TOUR[S.tourIdx].phase);
  }

  /** 相位进入钩子：演示加速（仅 I_f 预置，不动判据） */
  function onEnterPhase(phase) {
    if (phase !== 'fatigue') return;
    const boost = $('sDemoBoost') ? $('sDemoBoost').checked : true;
    if (!boost) return;
    // I_f 动力学（v2.9.1）：θ_norm>1.2 时按 (θ_norm−1.2)/0.3 加速累积（上限 2 等效秒/秒），
    // 现实 θ_norm≈1.5 需 300s 达阈。演示每帧模拟时间被压缩，故预置到 270 等效秒，
    // 使疲劳段内自然爬升越过 300 阈值（累积过程本身仍可见；可在设置中关闭）。
    const target = K.IF_HOLD_S * 0.9;
    if (engine.thetaIntegral >= target) return;
    engine.thetaIntegral = target;
    pushRemind({
      id: ++S.seq, t: S.simClock, state: 'fatigue', level: 'silent', sys: true,
      action: `演示加速：进入疲劳段，I_f 预置为 ${Math.round(target)}s（其余为自然累积；可在设置中关闭）`,
      conf: 0.6
    });
  }

  function stepOnce(forceArtifact) {
    let f;
    if (forceArtifact) {
      f = MOCK.makeArtifactFrame(S.phase, { rnd });
    } else {
      f = nextFrame();
      if (!f) return false;
    }
    // 真机/回放：按真实帧间隔推进时钟（BrainLink ~1.2–1.5s/帧；用固定 stepSec 会低估时间，
    // 使冷却与 I_f 积分偏快）。首帧无间隔可用时退回 stepSec。
    const step = (S.mode === 'live' && LiveBridge.lastGap) ? LiveBridge.lastGap : S.stepSec;
    S.simClock += step;
    f.ts = S.simClock;
    S.frames++;
    const ctx = { task_active: taskActiveFor(S.phase) };
    const resp = engine.processFrame(f, ctx);
    if (!forceArtifact) S.lastResponse = resp;
    if (CAL.active && !forceArtifact) calibCollect(f);   // v2.9.2：校准采集（伪迹帧不计入）
    onAfterFrame(resp, forceArtifact);
    return true;
  }

  function onAfterFrame(resp, isArtifact) {
    const dec = resp.decision, fb = resp.feedback;

    // 历史点（每帧记录，供曲线绘制）
    snapshot();

    // 提醒事件：只在非 silent 时进入提醒中心；最终等级由 gradeLevel 决定（可能升级为 strong）
    let lv = fb.level;
    if (!isArtifact && fb.level !== 'silent') {
      /* v3.14：去掉 isNoise 正则与 gateway 字段。
         三种"噪声"文案（冷却中 / 弱判别仅记录 / 数据不足）在引擎侧 level 全是 silent，
         上面这行已经把它们挡在提醒中心之外 —— 正则分支从未生效，gateway 也从无消费方。
         留着只会制造"改一句提示语就静默失效"的假防线。 */
      lv = pushRemind({
        id: ++S.seq, t: S.simClock, state: dec.state, level: fb.level,
        action: fb.action, conf: dec.conf
      });
    }
    S.gradedLevel = isArtifact ? fb.level : lv;
    if (!isArtifact && (lv === 'calm' || lv === 'strong')) {
      speak(fb.action, lv);
      notify(dec, fb);
      buzz([28, 40, 28]);
    } else if (!isArtifact && lv === 'mild') {
      speak(fb.action, lv);
      notify(dec, fb);
    }

    if (isArtifact) pulseBrand();
    render();
  }

  /* ============================ 渲染 ============================ */
  let prevStateKey = null;

  function render() {
    const dec = engine.lastDecision || { state: 'neutral', conf: K.CONF_MIN, scores: {}, gates: {} };
    const fb = engine.lastFeedback || { level: 'silent', action: '', cooldown_left: 0 };
    const m = metaOf(dec.state);
    const feats = engine.lastFeats || {};

    updateCalibBanner();

    // 主卡（v3.13：弱判别不再沿用状态族色 —— 红卡 + SILENT + "仅记录不干预"三者互斥）
    const weak = dec.conf < (engine.cfg.weakConf || 0.7);
    el.stateHero.dataset.family = weak ? 'weak' : m.family;
    if (el.heroRing) el.heroRing.classList.toggle('is-weak', weak);
    if (el.vWeak) {
      el.vWeak.hidden = !weak;
      el.vWeak.textContent = '弱判别 ' + fmt(dec.conf);
      el.vWeak.title = '置信度低于弱判别阈值 ' + fmt(engine.cfg.weakConf || 0.7)
        + '：只记录、不干预，颜色不表示状态好坏';
    }
    if (dec.state !== prevStateKey) {
      el.heroIcon.innerHTML = Icons.svg(Icons.stateIcon(dec.state));
      el.heroIcon.classList.remove('bump');
      void el.heroIcon.offsetWidth;      // 强制重排以重启动画
      el.heroIcon.classList.add('bump');
      el.badge.classList.add('pulse');
      setTimeout(() => el.badge.classList.remove('pulse'), 700);
      pulseBrand();
      prevStateKey = dec.state;
      syncStateGridSelection();
    }
    el.vStateCn.textContent = m.cn;
    el.vStateKey.textContent = dec.state;
    el.vConf.textContent = fmt(dec.conf);
    const ringLen = 226.19;
    const frac = Math.max(0, Math.min(1, (dec.conf - 0.5) / 0.45));
    el.ringFg.style.strokeDashoffset = String(ringLen * (1 - frac));
    el.ringFg.style.stroke = familyColor(m.family);
    el.vLevel.textContent = S.gradedLevel || fb.level;
    el.vLevel.dataset.level = S.gradedLevel || fb.level;
    el.vAction.textContent = fb.action || '—';

    const cd = engine.cooldownLeft(dec.state, S.simClock);
    if (cd > 0) { el.vCooldown.hidden = false; el.vCooldown.textContent = '冷却 ' + Math.ceil(cd) + 's'; }
    else el.vCooldown.hidden = true;

    // 指标（v3.13：无 RR 通道时不再写「窗口未满」——那暗示"再等等就有"，
    // 实际是设备没有 PPG，永远不会有。硬质头环场景下这是必须说清的事实。）
    const lastFr = engine.eegBuf.length ? engine.eegBuf[engine.eegBuf.length - 1] : null;
    const noRR = noRRChannel();
    el.mAttMed.textContent = lastFr ? `${lastFr.attention.toFixed(0)} / ${lastFr.meditation.toFixed(0)}` : '—';
    el.mRtb.textContent = fmt(feats.R_tb, 2);
    el.mRmssd.textContent = noRR ? '无 RR'
      : (Number.isFinite(feats.RMSSD_60) ? fmt(feats.RMSSD_60, 1) + ' ms' : '窗口未满');
    el.mHr.textContent = noRR ? '无 RR'
      : (Number.isFinite(feats.HR) ? fmt(feats.HR, 0) + ' bpm' : '—');
    el.mStill.textContent = Number.isFinite(feats.still) ? fmt(feats.still, 3) : '—';
    el.mIf.textContent = fmt(engine.thetaIntegral, 0) + ' / ' + K.IF_HOLD_S + 's';
    updateNoRRNote(noRR);

    // 8 状态网格数据
    renderStateGrid(dec);
    renderPrio(dec);
    renderZBars();
    renderCooldowns();
    renderDist();

    // 理智度（v3.13：以「决策」为唯一口径，零样本时不再显示 1.00 满分）
    const total = engine.log.total;
    const calm = total ? engine.calmness : null;
    el.vCalm.textContent = calm == null ? '—' : fmt(calm);
    el.vCalm.classList.toggle('is-empty', calm == null);
    el.vCalm.style.color = calm == null ? ''
      : (calm >= 0.8 ? familyColor('good') : (calm >= 0.5 ? familyColor('warn') : familyColor('alert')));
    el.calmZero.hidden = total > 0;
    const roll = rollCalm();
    const rollN = Math.min(10, S.decisions.length);
    el.vRoll.textContent = `样本 ${total} 次`
      + (roll == null ? ' · 近 10 次 —' : ` · 近 10 次 ${fmt(roll)}（${rollN} 次）`);
    updateTrend();
    const ca = engine.segmentCalmness('A'), cb = engine.segmentCalmness('B');
    el.vCalmA.textContent = ca == null ? '—' : fmt(ca);
    el.vCalmB.textContent = cb == null ? '—' : fmt(cb);
    el.vCntA.textContent = engine.abStats.A.total + ' 次决策';
    el.vCntB.textContent = engine.abStats.B.total + ' 次决策';
    el.abA.classList.toggle('on', engine.abSegment === 'A');
    el.abB.classList.toggle('on', engine.abSegment === 'B');

    // 决策统计
    el.vTotal.textContent = String(engine.log.total);
    el.vImp.textContent = String(engine.log.impulsive);

    // 角标
    el.badge.dataset.family = m.family;
    el.bdgState.textContent = m.cn;
    el.bdgConf.textContent = 'conf ' + fmt(dec.conf);
    el.bpState.textContent = m.cn;
    el.bpLevel.textContent = fb.level;
    el.bpLevel.dataset.level = fb.level;
    el.bpAction.textContent = fb.action || '—';
    el.bpConf.textContent = '置信度 ' + fmt(dec.conf);
    el.bpCool.textContent = cd > 0 ? '冷却 ' + Math.ceil(cd) + 's' : '冷却 —';
    el.badge.querySelector('.bdg-dot').classList.toggle('live', m.family === 'alert' || m.family === 'warn');

    // 预览
    el.prevPopup.dataset.level = fb.level;
    el.prevPopup.querySelector('.pp-body').innerHTML =
      `检测到 <code>${dec.state}</code> · 置信度 ${fmt(dec.conf)} · ${fb.level} 级输出`;
    el.prevBadge.querySelector('.pb-dot').style.background = familyColor(m.family);
    $('pbState').textContent = m.cn;
    $('pbConf').textContent = fmt(dec.conf);

    // 读数
    el.vClock.textContent = S.simClock + ' s';
    el.vFrames.textContent = String(S.frames);
    el.vArtifact.textContent = String(engine.artifactCount);

    drawChart();
  }

  function renderStateGrid(dec) {
    if (!el.stateGrid.children.length) return;
    const noRR = noRRChannel();
    const now = S.simClock;
    for (const cell of el.stateGrid.children) {
      const key = cell.dataset.key;
      const m = metaOf(key);
      const gate = !!(dec.gates && dec.gates[key]);
      const score = dec.scores ? dec.scores[key] : undefined;
      // v3.13：无 RR 时 stress 恒不可判 —— 压低存在感并给出原因，
      // 否则用户会一直盯着一个永远不会亮的格子等。
      const unavailable = noRR && key === 'stress';
      cell.toggleAttribute('data-unavailable', unavailable);
      if (unavailable) cell.title = '压力判别需要 HRV（RMSSD）输入，本机无 RR 通道，该状态恒不可判';
      else if (cell.title) cell.removeAttribute('title');
      cell.classList.toggle('active', dec.state === key);
      cell.querySelector('.sc-dot').classList.toggle('on', gate);
      cell.querySelector('.sc-score').textContent =
        (score === undefined || !Number.isFinite(score)) ? '—' : (score.toFixed(2));
      /* v3.13：底部 2px 证据条。scores 是无量纲加权和（-∞…数十），单看数字
         读者无从判断"1.71 算强吗"。evidence 是同一套分数的归一化版本（0–1，
         除以 EVID_REF 后 clamp），用它做条，数字仍保留原值供核对。 */
      const evBar = cell.querySelector('.sc-ev');
      if (evBar) {
        const ev = dec.evidence && Number.isFinite(dec.evidence[key]) ? dec.evidence[key] : 0;
        evBar.style.width = (unavailable ? 0 : Math.max(0, Math.min(1, ev)) * 100).toFixed(1) + '%';
        evBar.style.background = familyColor(m.family);
      }
      const cd = engine.cooldownLeft(key, now);
      const coolEl = cell.querySelector('.sc-cool');
      /* v3.12：冷却提示放到得分位（同一格右端）。原来挤在第二行，
         "冷却 60s" 在 149px 的格里放不下，会被 overflow:hidden 裁成"冷却"。 */
      const cooling = cd > 0;
      cell.classList.toggle('cooling', cooling);
      if (cooling) { coolEl.hidden = false; coolEl.textContent = Math.ceil(cd) + 's'; }
      else coolEl.hidden = true;
      cell.setAttribute('aria-current', dec.state === key ? 'true' : 'false');
    }
  }

  function buildStateGrid() {
    el.stateGrid.innerHTML = '';
    for (const key of K.PRIORITY.slice().sort()) {
      const m = K.STATE_META[key];
      if (!m) continue;
      const b = document.createElement('button');
      b.className = 'state-cell';
      b.type = 'button';
      b.dataset.key = key;
      b.dataset.family = m.family;      // 门控点亮时按状态族取色（alert 不能显示成良好绿）
      b.innerHTML = `<div class="sc-top"><span class="ico">${Icons.svg(Icons.stateIcon(key))}</span><b>${m.cn}</b><span class="sc-score">—</span><span class="sc-cool" hidden></span></div>
        <div class="sc-meta"><span class="sc-dot"></span><span class="sc-key">${key}</span></div>
        <i class="sc-ev" aria-hidden="true"></i>`;
      el.stateGrid.appendChild(b);
    }
    syncStateGridSelection();
  }
  function syncStateGridSelection() {
    for (const c of el.stateGrid.children) {
      c.classList.toggle('selected', S.mode === 'manual' && c.dataset.key === S.phase);
      // v3.8：只有「界面自己在注入相位」的两种模式才标「注入中」。
      // 旧写法 mode !== 'paste' 把 live 也算进去，于是真机/回放时 mock 的 S.phase(focus)
      // 仍挂着「注入中」徽标 —— 数据其实来自设备，界面在说谎。
      c.toggleAttribute('injected',
        !prodMode && (S.mode === 'auto' || S.mode === 'manual') && c.dataset.key === S.phase);
    }
  }

  /* ---- 优先级队列（可拖拽）---- */
  function buildPrioList() {
    el.prioList.innerHTML = '';
    engine.priority.forEach((key, i) => {
      const m = K.STATE_META[key] || { cn: key, icon: '•' };
      const li = document.createElement('li');
      li.className = 'prio-item';
      li.dataset.key = key;
      li.tabIndex = 0;
      li.setAttribute('role', 'listitem');
      li.setAttribute('aria-label', `第 ${i + 1} 优先：${m.cn}，可用方向键调整`);
      li.innerHTML = `<span class="prio-rank">${i + 1}</span><span class="grip">≡≡</span>
        <span class="pn">${Icons.svg(Icons.stateIcon(key))}${m.cn}</span><span class="pk">${key}</span>`;
      el.prioList.appendChild(li);
    });
  }
  function renderPrio(dec) {
    let idx = 0;
    for (const li of el.prioList.children) {
      const key = li.dataset.key;
      li.classList.toggle('winner', dec.state === key);
      const gate = !!(dec.gates && dec.gates[key]);
      li.querySelector('.pk').style.color = gate ? familyColor('good') : '';
      li.querySelector('.prio-rank').textContent = String(++idx);
    }
  }
  function commitPrio() {
    engine.priority = Array.from(el.prioList.children).map(li => li.dataset.key);
    save('priority', engine.priority);
    // 用当前帧数据即时重算，展示重排后的仲裁结果（不推进状态机）
    const snap = engine.lastDecision;
    if (snap) {
      const arb = engine.arbitrate(snap.scores, snap.gates);
      showRecalcHint(arb.state, snap.state);
    }
  }
  function showRecalcHint(newState, oldState) {
    if (newState === oldState) return;
    const msg = `优先级重排：当前证据下仲裁结果 ${oldState} → ${newState}（下一帧生效于状态机）`;
    pushRemind({ id: ++S.seq, t: S.simClock, state: newState, level: 'silent', action: msg, conf: 0.6, sys: true });
  }

  /* ---- z-score 条 ---- */
  const ZBARS = [
    ['R_tb', 'R_tb'], ['R_ba', 'R_ba'], ['TAR', 'TAR'], ['A_rel', 'A_rel'],
    ['HR', 'HR'], ['RMSSD_60', 'RMSSD60'], ['still', 'still'], ['delta', 'δ']
  ];
  function buildZBars() {
    el.zbarList.innerHTML = ZBARS.map(([k, label]) => `
      <div class="zbar" data-key="${k}">
        <span class="zk">${label}</span>
        <span class="zt">
          <i class="guide" style="left:${50 - 25}%"></i><i class="guide" style="left:${50 + 25}%"></i>
          <i class="guide" style="left:${50 - 16.667}%"></i><i class="guide" style="left:${50 + 16.667}%"></i>
          <span class="zf"></span>
        </span>
        <span class="zv">—</span>
      </div>`).join('');
  }
  function renderZBars() {
    for (const row of el.zbarList.children) {
      const z = engine.lastZ[row.dataset.key];
      const bar = row.querySelector('.zf');
      const val = row.querySelector('.zv');
      if (!Number.isFinite(z)) {
        bar.style.width = '0%'; val.textContent = '窗口未满';
        continue;
      }
      const zc = Math.max(-3, Math.min(3, z));
      const w = Math.abs(zc) / 3 * 50;
      bar.style.width = w + '%';
      bar.style.left = (zc >= 0 ? 50 : 50 - w) + '%';
      bar.style.background = z > 0 ? familyColor('alert') : familyColor('good');
      val.textContent = (z >= 0 ? '+' : '') + z.toFixed(2) + 'σ';
      val.style.color = Math.abs(z) >= 1.5 ? familyColor('alert') : (Math.abs(z) >= 1 ? familyColor('warn') : '');
    }
  }

  /* ---- 冷却监视 ---- */
  function buildCooldowns() {
    el.cdList.innerHTML = K.PRIORITY.map(k => {
      const m = K.STATE_META[k];
      return `<div class="cd-item" data-key="${k}">
        <div class="cd-top"><span class="cd-n">${Icons.svg(Icons.stateIcon(k))}${m.cn}</span><span class="cd-v">—</span></div>
        <div class="cd-bar"><i></i></div></div>`;
    }).join('');
  }
  function renderCooldowns() {
    for (const item of el.cdList.children) {
      const key = item.dataset.key;
      const total = engine.cooldownOf(key);
      const left = engine.cooldownLeft(key, S.simClock);
      const bar = item.querySelector('i');
      const v = item.querySelector('.cd-v');
      if (!total) { bar.style.width = '100%'; bar.classList.add('done'); v.textContent = '无冷却'; continue; }
      const pct = Math.max(0, Math.min(100, (1 - left / total) * 100));
      bar.style.width = pct + '%';
      bar.classList.toggle('done', left <= 0);
      v.textContent = left > 0 ? Math.ceil(left) + 's / ' + Math.round(total) + 's' : '就绪';
    }
  }

  /* ---- 理智度趋势 ---- */
  function updateTrend() {
    // v3.13：零样本帧的 calm 为 null（不是 1.00），求和前必须剔除，
    // 否则 null 被当 0 参与平均，"没有数据"会算成"冷静度大幅下滑"。
    const pts = S.history.filter(p => p.calm != null);
    if (pts.length < 6) { el.vTrend.textContent = '— 持平'; el.vTrend.dataset.dir = 'flat'; return; }
    const n = pts.length;
    const win = Math.min(24, n);
    const seg = pts.slice(n - win);
    const half = Math.floor(win / 2);
    const a = seg.slice(0, half).reduce((s, p) => s + p.calm, 0) / half;
    const b = seg.slice(half).reduce((s, p) => s + p.calm, 0) / (win - half);
    const d = b - a;
    el.vTrend.dataset.dir = d > 0.01 ? 'up' : (d < -0.01 ? 'down' : 'flat');
    el.vTrend.textContent = (d > 0.01 ? '↑ 回升 ' : d < -0.01 ? '↓ 下滑 ' : '— 持平 ')
      + Math.abs(d * 100).toFixed(1) + 'pt / 近 ' + win + ' 帧';
  }

  /* ============================ 曲线绘制 ============================ */
  const RING_LEN = 226.19;

  function drawChart() { if (window.ChartView) ChartView.drawChart(); }



  /* ============================ 提醒中心 ============================ */
  /* ---- v3.13：第 4 级提醒 strong ----
   * 设计文档与 Python 侧 Feedback 注释都写着 silent/mild/calm/strong 四级，
   * 但引擎实际只产出前三级 —— 页脚写「4 级提醒」、语音设置里勾着 strong，
   * 都属于"承诺了没实现"。这里在**输出层**补齐，不动引擎判据，因此不影响
   * 与 state_engine.py 的双引擎对拍。
   * 规则：同一状态在 STRONG_WINDOW_S 内第 STRONG_AFTER 次 calm 提醒起升级为
   * strong（配合 60s 冷却，等于"提醒了三次仍未恢复"）。状态变化即重置。 */
  const STRONG_AFTER = 3;
  const STRONG_WINDOW_S = 600;
  let strongTrack = { state: null, t: -1e9, n: 0 };
  function gradeLevel(state, level, t) {
    if (level !== 'calm') {
      /* v3.14：任何非 calm 都重置连击。
         原写法只重置 silent，于是 stress(calm)×2 → fatigue(mild) → stress(calm)
         会被算成第 3 次并升级为 strong —— 中间明明已经换了状态，
         语义上不是"同一状态提醒三次仍未恢复"。 */
      strongTrack = { state: null, t: -1e9, n: 0 };
      return level;
    }
    const gap = t - strongTrack.t;
    if (strongTrack.state === state && gap <= STRONG_WINDOW_S) strongTrack.n += 1;
    else strongTrack = { state, t, n: 1 };
    strongTrack.t = t;
    return strongTrack.n >= STRONG_AFTER ? 'strong' : 'calm';
  }

  function pushRemind(ev) {
    /* v3.14：防御性拦截 silent（系统消息除外）。
       调用方（onAfterFrame）本已判断 level !== 'silent'，这里再挡一次是因为
       pushRemind 是公开出口，将来若有人从别处调用，不该把静默记录当提醒塞进中心。
       取代原来的中文文案正则 —— 那种写法在引擎改一个字后就默默失效。 */
    if (ev.level === 'silent' && !ev.sys) return ev.level;
    ev.level = gradeLevel(ev.state, ev.level, ev.t);
    S.remindEvents.unshift(ev);
    if (S.remindEvents.length > 200) S.remindEvents.pop();
    renderRemind();
    el.badge.dispatchEvent(new Event('remind'));
    // 角标计数
    S.unread++;
    updateBadgeCount();
    // 弹窗
    if (ev.level !== 'silent' && !el.chkDnd.checked && !ev.sys) {
      showToast(ev);
    }
    return ev.level;
  }

  // v3.9：空态文案与结构只此一处，避免 index.html / 两个列表三份各写一遍
  // v3.11：空态插图改为 ComfyUI 生成的透明线稿（见 assets/manifest.json），不再用 emoji
  const EMPTY_SLOT = '<div class="empty-slot"><span class="es-art" aria-hidden="true"></span><b>暂无提醒事件</b>'
    + '<span class="es-hint">触发 mild / calm / strong 级别时会出现在这里（silent 只记入统计、不打扰）</span></div>';

  function renderRemind() {
    const list = S.filter === 'all' ? S.remindEvents : S.remindEvents.filter(e => e.level === S.filter);
    el.remindCount.textContent = S.remindEvents.length + ' 条';
    // 主界面：最新 5 条；详情面板：全量。两者共用同一份数据与同一筛选条件。
    const latest = list.slice(0, 5);
    el.remindStream.innerHTML = latest.length
      ? latest.map(itemHtml).join('')
      : EMPTY_SLOT;
    el.remindStreamAll.innerHTML = list.length
      ? list.slice(0, 60).map(itemHtml).join('')
      : EMPTY_SLOT;
  }
  function itemHtml(e) {
    const m = metaOf(e.state);
    return `<div class="remind-item" data-level="${e.level}">
      <div class="ri-top">
        <span class="ri-ico">${Icons.svg(e.sys ? 'route' : Icons.stateIcon(e.state))}</span>
        <span class="ri-state">${e.sys ? '系统' : m.cn}</span>
        <span class="badge-level" data-level="${e.level}">${e.level}</span>
        <span class="ri-time">t=${e.t}s</span>
      </div>
      <p class="ri-body">${escapeHtml(e.action)}<br>
        <span class="tcode">state=${e.state} · conf=${fmt(e.conf)}</span></p>
    </div>`;
  }

  /* ---- v3.13：会话统计导出 ----
   * 答辩时被问"这段数据说明了什么"，靠翻界面答不上来。导出一份 CSV：
   * 概览 → 8 状态占比 → 提醒事件明细，可直接贴进报告或做表。 */
  function exportStats() {
    const rows = [];
    const add = (k, v) => rows.push([k, v]);
    const total = engine.log.total;
    add('会话时长(s)', Math.round(S.simClock));
    add('帧数', S.frames);
    add('伪迹帧', engine.artifactCount);
    add('决策次数', total);
    add('冲动决策', engine.log.impulsive);
    add('冷静度(1-冲动/总)', total ? (engine.calmness).toFixed(4) : '—（无决策样本）');
    add('提醒事件数', S.remindEvents.length);
    add('数据源模式', S.mode);
    add('校准基线', isUncalibrated() ? 'default（未校准）' : 'individual（已校准）');
    add('RR 通道', engine.rrSeen ? '可用' : '无（无 PPG）');
    add('导出时间', new Date().toISOString());

    rows.push([]);
    rows.push(['状态', '帧数', '占比']);
    const cnt = {};
    for (const p of S.history) cnt[p.state] = (cnt[p.state] || 0) + 1;
    const n = S.history.length || 1;
    for (const key of K.PRIORITY) {
      const c = cnt[key] || 0;
      if (!c) continue;
      rows.push([metaOf(key).cn + ' / ' + key, c, (c / n * 100).toFixed(1) + '%']);
    }

    rows.push([]);
    rows.push(['提醒事件', '时刻(s)', '状态', '级别', '说明']);
    for (const e of S.remindEvents.slice(0, 200)) {
      rows.push([e.id, e.t, e.state, e.level, e.action || '']);
    }

    const esc = (v) => '"' + String(v == null ? '' : v).replace(/"/g, '""') + '"';
    const csv = '\ufeff' + rows.map(r => r.map(esc).join(',')).join('\r\n');
    const blob = new Blob([csv], { type: 'text/csv;charset=utf-8' });
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'session_stats_' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '') + '.csv';
    document.body.appendChild(a);
    a.click();
    setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 0);
  }

  /* ---- v3.13：状态占比环形图 ----
   * 数据源就是 S.history（每帧一条），和曲线同源，不需要另开统计口径。
   * 只画出现过的状态，按 PRIORITY 顺序排，图例与弧段同序，最多 6 项 + "其他"。 */
  const DIST_C = 2 * Math.PI * 40;   // r=40 的周长
  let distCache = '';
  let distAt = -1e9;   // 不能用 0：页面加载后 500ms 内 performance.now() 还很小，会一直被判为"刚更新过"
  function renderDist() {
    if (!el.distRing || !el.distLegend) return;
    // 500ms 节流：历史最多 1500 条，逐帧全量统计会白烧 CPU
    const now = performance.now();
    if (now - distAt < 500) return;
    distAt = now;
    const cnt = {};
    for (const p of S.history) cnt[p.state] = (cnt[p.state] || 0) + 1;
    const n = S.history.length;
    if (el.distTotal) el.distTotal.textContent = n + ' 帧';
    // 无数据时保持空环 + 一行说明
    if (!n) {
      if (distCache !== 'empty') {
        distCache = 'empty';
        el.distArcs.innerHTML = '';
        el.distLegend.innerHTML = '<li class="dist-empty">开始推流后统计</li>';
      }
      return;
    }
    const items = K.PRIORITY
      .map(k => ({ key: k, c: cnt[k] || 0, m: metaOf(k) }))
      .filter(o => o.c > 0)
      .sort((a, b) => b.c - a.c);
    const sig = items.map(o => o.key + o.c).join('|') + n;
    if (sig === distCache) return;
    distCache = sig;

    let off = 0;
    el.distArcs.innerHTML = items.map((o) => {
      const len = (o.c / n) * DIST_C;
      const seg = `<circle cx="50" cy="50" r="40" fill="none" stroke="${familyColor(o.m.family)}"
        stroke-width="12" stroke-linecap="butt"
        stroke-dasharray="${len.toFixed(2)} ${(DIST_C - len).toFixed(2)}"
        stroke-dashoffset="${(-off).toFixed(2)}"></circle>`;
      off += len;
      return seg;
    }).join('');

    el.distLegend.innerHTML = items.slice(0, 6).map(o => `
      <li>
        <i class="dl-dot" style="background:${familyColor(o.m.family)}"></i>
        <span class="dl-name">${o.m.cn}</span>
        <span class="dl-pct">${(o.c / n * 100).toFixed(0)}%</span>
      </li>`).join('');
  }

  /* ---- v3.13：设置面板分组折叠 ----
   * 20 余条设置平铺时，320 宽要滚 4.5 屏。把每个 <h4 class="grp"> 及其后续兄弟
   * 包进 <details>，演示类分组默认收起。不改 HTML 结构，初始化时就地包装。 */
  function foldGroups(root, closedPattern) {
    if (!root) return;
    const heads = [...root.querySelectorAll(':scope > h4.grp')];
    for (const h of heads) {
      const d = document.createElement('details');
      d.className = 'fold';
      d.open = !(closedPattern && closedPattern.test(h.textContent));
      const s = document.createElement('summary');
      s.className = 'grp';
      while (h.firstChild) s.appendChild(h.firstChild);
      // 必须先记下 h 的后继节点：replaceWith 之后 h 已脱离文档，s 也被 append 进 d，
      // 此时 s.nextSibling 恒为 null，组内的 20 多条设置就全留在外面（实测 folds 高度 21px）。
      const first = h.nextSibling;
      h.replaceWith(d);
      d.appendChild(s);
      let n = first;
      while (n && !(n.nodeType === 1 && n.matches('h4.grp'))) {
        const next = n.nextSibling;
        d.appendChild(n);
        n = next;
      }
    }
  }



  function updateBadgeCount() {
    if (S.unread > 0) { el.bdgCount.hidden = false; el.bdgCount.textContent = S.unread > 99 ? '99+' : String(S.unread); }
    else el.bdgCount.hidden = true;
  }

  /* ---- 提醒弹窗（可拖拽）---- */
  const TOAST_LIFE = { mild: 8000, calm: 12000, strong: 15000, silent: 6000 };

  function showToast(ev) {
    const m = metaOf(ev.state);
    const life = TOAST_LIFE[ev.level] || 8000;
    const node = document.createElement('div');
    node.className = 'toast';
    node.dataset.level = ev.level;
    node.setAttribute('role', 'alert');
    node.innerHTML = `
      <div class="t-bar"></div>
      <div class="toast-head">
        <span class="toast-ico">${Icons.svg(ev.level === 'calm' || ev.level === 'strong' ? 'alert' : ev.level === 'mild' ? 'bell' : 'info')}</span>
        <span class="th-txt"><b>${escapeHtml(ev.action)}</b><span>${Icons.svg(Icons.stateIcon(ev.state))}${m.cn} · ${ev.state} · conf ${fmt(ev.conf)}</span></span>
        <button class="toast-close" aria-label="关闭">${Icons.svg('close')}</button>
      </div>
      <div class="toast-body">${escapeHtml(K.ADVICE[ev.state] || '')}
        <br><span class="tcode">t=${ev.t}s · 冷却 ${Math.round(engine.cooldownOf(ev.state))}s${el.chkDnd.checked ? ' · 已静音播报' : ''}</span></div>
      <div class="toast-foot">
        <span class="toast-prog"><i></i></span>
        <button class="ghost-btn tiny t-snooze">稍后</button>
        <button class="primary-btn tiny t-act">开始干预</button>
      </div>`;
    el.toastLayer.appendChild(node);
    /* v3.14：超量移除时必须停掉被移除节点的 rAF 进度循环。
       原来直接 removeChild，节点脱离文档但 tick() 仍在跑，一直到 life 到期（8–15s）才停；
       连续来提醒时会同时存在多个僵尸循环。用 __stop 句柄显式收尾。 */
    while (el.toastLayer.children.length > 3) {
      const old = el.toastLayer.firstChild;
      if (old && typeof old.__stop === 'function') old.__stop();
      el.toastLayer.removeChild(old);
    }

    const prog = node.querySelector('.toast-prog i');
    let start = Date.now();
    const st = { raf: null };
    function tick() {
      const r = (Date.now() - start) / life;
      prog.style.width = Math.max(0, 100 - r * 100) + '%';
      if (r >= 1) closeToast(); else st.raf = requestAnimationFrame(tick);
    }
    st.raf = requestAnimationFrame(tick);
    node.__stop = () => cancelAnimationFrame(st.raf);
    function closeToast() {
      cancelAnimationFrame(st.raf);
      node.classList.add('leaving');
      setTimeout(() => node.remove(), 300);
    }
    node.querySelector('.toast-close').addEventListener('click', closeToast);
    node.querySelector('.t-snooze').addEventListener('click', () => {
      closeToast();
      pushRemind({ id: ++S.seq, t: S.simClock, state: ev.state, level: 'silent', action: '已选择「稍后提醒」（下次冷却结束后再次触发）', conf: ev.conf, sys: true });
    });
    node.querySelector('.t-act').addEventListener('click', () => {
      engine.log.interventions.push({ t: S.simClock, state: ev.state, level: ev.level });
      const dot = el.heroIcon;
      dot.classList.remove('bump'); void dot.offsetWidth; dot.classList.add('bump');
      speak('好的，现在开始三次深呼吸，吸气四秒，呼气六秒。', ev.level);
      closeToast();
    });
    makeDraggable(node, {
      ignore: 'button',
      // 一旦开始搬动就取消自动关闭，避免用户摆放位置过程中弹窗突然消失
      onStart: () => { cancelAnimationFrame(st.raf); prog.style.width = '100%'; }
    });
  }

  /* ============================ 语音 / 通知 / 振动 ============================ */
  function voiceSupported() { return 'speechSynthesis' in window && typeof window.SpeechSynthesisUtterance === 'function'; }
  /** 播报。level 为空时不作级别过滤（试听场景）；否则仅播报被勾选的级别。 */
  function speak(text, level) {
    if (!el.chkVoice.checked || !voiceSupported() || !text) return;
    if (level) {
      const allowed = Array.from(el.voiceLevels.querySelectorAll('input:checked')).map(i => i.value);
      if (allowed.length && allowed.indexOf(level) < 0) return;   // 该级别未勾选 → 不播报
    }
    try {
      const u = new SpeechSynthesisUtterance(text);
      const vname = el.sVoiceSel.value;
      if (vname) {
        const vs = window.speechSynthesis.getVoices();
        const v = vs.find(x => x.name === vname);
        if (v) u.voice = v;
      }
      u.lang = 'zh-CN';
      u.rate = Number(el.sVoiceRate.value) || 1;
      u.volume = Number(el.sVoiceVol.value);
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
    } catch (e) { /* 静默降级：浏览器不支持或被策略拦截 */ }
  }
  function notify(dec, fb) {
    if (!el.sUseNotify || !el.sUseNotify.checked) return;
    if (!('Notification' in window) || Notification.permission !== 'granted') return;
    try {
      new Notification(`${metaOf(dec.state).cn} · ${fb.level}`, {
        body: fb.action, tag: 'bs-' + dec.state, renotify: true
      });
    } catch (e) { /* file:// 下部分浏览器不支持 */ }
  }
  function buzz(pattern) {
    try { if (navigator.vibrate) navigator.vibrate(pattern); } catch (e) { /* noop */ }
  }

  /* ============================ 拖拽工具 ============================ */
  /** 通用可拖动（translate 位移），ignore 选择器内的元素不触发 */
  function makeDraggable(node, opt) {
    opt = opt || {};
    const handle = opt.handle || node;
    let dragging = false, sx = 0, sy = 0, ox = 0, oy = 0, moved = false;

    handle.addEventListener('pointerdown', (e) => {
      if (opt.ignore && e.target.closest(opt.ignore)) return;
      if (e.button != null && e.button !== 0) return;
      dragging = true; moved = false;
      sx = e.clientX; sy = e.clientY;
      const t = currentTranslate(node);
      ox = t.x; oy = t.y;
      node.setPointerCapture && node.setPointerCapture(e.pointerId);
      node.classList.add('dragging');
      if (opt.onStart) opt.onStart();
    });
    node.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      const dx = e.clientX - sx, dy = e.clientY - sy;
      if (Math.abs(dx) + Math.abs(dy) > 4) moved = true;
      const nx = clampBox(ox + dx, node, 'x');
      const ny = clampBox(oy + dy, node, 'y');
      node.style.transform = `translate(${nx}px, ${ny}px)`;
    });
    const end = () => {
      if (!dragging) return;
      dragging = false;
      node.classList.remove('dragging');
      if (opt.onDrop) opt.onDrop(moved);
    };
    node.addEventListener('pointerup', end);
    node.addEventListener('pointercancel', end);
    node._dragMoved = () => moved;
  }
  function currentTranslate(node) {
    const t = node.style.transform || '';
    const m = t.match(/translate\(([-0-9.]+)px,\s*([-0-9.]+)px\)/);
    return m ? { x: parseFloat(m[1]), y: parseFloat(m[2]) } : { x: 0, y: 0 };
  }
  function clampBox(v, node, axis) {
    const r = node.getBoundingClientRect();
    const base = currentTranslate(node);
    const cur = axis === 'x' ? r.left - (node.style.transform ? base.x : 0) : r.top - (node.style.transform ? base.y : 0);
    const size = axis === 'x' ? r.width : r.height;
    const min = axis === 'x' ? -cur + 8 : -cur + 8;
    const max = axis === 'x' ? window.innerWidth - cur - size - 8 : window.innerHeight - cur - size - 8;
    return Math.max(min, Math.min(max, v));
  }

  /* ---- 角标：拖动 + 点击（阈值区分）---- */
  function initBadge() {
    let moved = false;
    el.badge.addEventListener('pointerdown', () => { moved = false; });
    makeDraggable(el.badge, { ignore: 'button', onDrop: (m) => { moved = m; } });
    el.badge.addEventListener('click', () => {
      if (moved) return;
      openBadgePanel(el.badgePanel.hidden);
    });
    el.badge.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openBadgePanel(el.badgePanel.hidden); }
    });
    $('bpClose').addEventListener('click', () => openBadgePanel(false));
    document.addEventListener('click', (e) => {
      if (!el.badgePanel.hidden && !el.badgePanel.contains(e.target) && !el.badge.contains(e.target)) {
        openBadgePanel(false);
      }
    });
  }
  function openBadgePanel(open) {
    el.badgePanel.hidden = !open;
    if (open) { S.unread = 0; updateBadgeCount(); }
  }

  /* ============================ 播放循环 ============================ */
  let lastTs = 0, acc = 0;

  function loop(ts) {
    if (S.playing) {
      if (!lastTs) lastTs = ts;
      acc += (ts - lastTs);
      lastTs = ts;
      const interval = 1000 / S.fps;
      let budget = 6;                       // 单帧最多补 6 次，防止后台标签页回来后暴走
      while (acc >= interval && budget-- > 0) {
        acc -= interval;
        if (!stepOnce(false)) {
          if (S.mode === 'live') break;     // 真机/回放：队列暂空，等下一帧到达，不停止推流
          setPlaying(false); acc = 0; break;
        }
      }
      if (acc > interval) acc = interval;
    } else { lastTs = 0; acc = 0; }
    requestAnimationFrame(loop);
  }

  function setPlaying(on) {
    S.playing = on;
    if (on) S.hasPlayed = true;
    el.btnPlay.classList.toggle('playing', on);
    // v3.13：单帧只在暂停时有意义（推流中每帧都在自动推进）。
    if (el.btnStep) {
      el.btnStep.disabled = on;
      el.btnStep.title = on ? '推流中自动推进，暂停后可用单帧逐帧检查' : '推进一帧（暂停状态下逐帧检查）';
    }
    // v3.13：暂停过就写「继续推流」。原来一律写「开始推流」，
    // 读者会以为是"从头开始"，不敢点（实际是从当前时刻接着跑）。
    el.playLabel.textContent = on ? '暂停推流' : (S.hasPlayed ? '继续推流' : '开始推流');
    const playIco = el.btnPlay.querySelector('.ico');
    if (playIco) playIco.innerHTML = Icons.svg(on ? 'pause' : 'play');
    if (on && S.mode === 'paste' && S.pasteIdx >= S.pasteQueue.length) {
      // 粘贴数据播完后再点播放：重新载入
      if (loadPaste(true)) return;
      setPlaying(false);
    }
  }

  /* ============================ 交互绑定 ============================ */
  function bindAll() {
    /* --- 链路提示条：按 act 分流（暂停时是"继续推流"，不是"重新连接"）--- */
    if (el.btnNoticeRetry) {
      el.btnNoticeRetry.addEventListener('click', () => {
        if (el.btnNoticeRetry.dataset.act === 'resume') { setPlaying(true); updateLinkNotice(); return; }
        if (el.btnNoticeRetry.dataset.act === 'settings') { toggleDrawer('settings'); refreshDiag(); return; }
        lastFrameAt = 0; openedAt = performance.now();
        connectBridge();
      });
    }
    // 停更检测必须走定时器：断流后没有帧驱动渲染，loop 里检测不到
    setInterval(updateLinkNotice, 1000);
    /* --- 模式 --- */
    el.segMode.addEventListener('click', (e) => {
      const b = e.target.closest('button'); if (!b) return;
      const prev = S.mode, next = b.dataset.mode;
      /* v3.14：统一走 setModeSeg —— 原来这里手写 active + S.mode，绕过了 setModeSeg
         里的「接触质量」刷新，于是从真机切到粘贴/手动后，指标格仍显示真机的
         signal 读数（如"良好 30"），而数据其实已换成模拟值。 */
      setModeSeg(next);
      S.tourIdx = 0; S.tourFrame = 0;
      if (next === 'auto') onEnterPhase(MOCK.AUTO_TOUR[0].phase);
      if (next === 'paste') setPlaying(false);
      if (next === 'live') {
        // v2.9.2：真机/回放——清引擎缓冲（不预热），确保判别只由真机数据驱动
        // v3.15：同时清 SSE 积压。停在自动模式时桥仍在收帧（上限 300 帧≈5 分钟），
        // 不清的话切回真机先判几百帧过期数据，曲线和提醒会"时光倒流"。
        LiveBridge.flush();
        // 新数据源：旧的 alive/error 不能沿用，否则会短暂显示上一源的"已结束"
        S.srcError = null; S.srcAlive = null; S.srcAge = null;
        lastHealthPoll = 0;
        resetAll(true);
        if (LiveBridge.status !== 'open') connectBridge();
        setPlaying(true);
      } else if (prev === 'live') {
        // v3.14：从真机切回演示，清特征窗防残留帧污染前 10 秒判别（统计保留）
        engine.clearWindows();
      }
      if (next === 'paste' && !S.pasteQueue.length) {
        el.pasteMsg.className = 'paste-msg';
        el.pasteMsg.textContent = '请先在「设置 → 粘贴帧数据」中载入数据';
        openDrawer('settings');
      }
      syncStateGridSelection();
      render();
    });

    /* --- 校准（v2.9.2） --- */
    if (el.btnCalibStart) el.btnCalibStart.addEventListener('click', startCalib);
    if (el.btnCalibCancel) el.btnCalibCancel.addEventListener('click', cancelCalib);
    if (el.btnBaselineDefault) el.btnBaselineDefault.addEventListener('click', resetBaseline);
    if (el.btnBaselineExport) el.btnBaselineExport.addEventListener('click', exportBaseline);
    if (el.fileBaseline) {
      el.fileBaseline.addEventListener('change', () => {
        const f = el.fileBaseline.files && el.fileBaseline.files[0];
        if (f) importBaseline(f);
        el.fileBaseline.value = '';
      });
    }
    /* --- 真机数据桥 --- */
    if (el.btnBridgeConnect) el.btnBridgeConnect.addEventListener('click', connectBridge);
    if (el.btnBridgeClose) el.btnBridgeClose.addEventListener('click', () => {
      LiveBridge.close();
      if (S.mode === 'live') setPlaying(false);
    });
    if (el.txtBridge) {
      el.txtBridge.addEventListener('keydown', (e) => { if (e.key === 'Enter') connectBridge(); });
    }

    /* --- 播放控制 --- */
    el.btnPlay.addEventListener('click', () => setPlaying(!S.playing));
    if (el.demoTag) el.demoTag.addEventListener('click', () => applyProdMode(!prodMode, true));
    el.btnStep.addEventListener('click', () => { setPlaying(false); stepOnce(false); });
    el.btnReset.addEventListener('click', () => {
      setPlaying(false);
      resetAll();
    });
    el.btnArtifact.addEventListener('click', () => {
      stepOnce(true);
      flash(el.vArtifact);
    });

    /* --- 滑杆 --- */
    el.rngStep.addEventListener('input', () => {
      S.stepSec = Number(el.rngStep.value);
      el.lblStep.textContent = S.stepSec + ' s/帧';
      save('step', S.stepSec);
    });
    el.rngSpeed.addEventListener('input', () => {
      S.fps = Number(el.rngSpeed.value);
      el.lblSpeed.textContent = S.fps + ' 帧/秒';
      save('fps', S.fps);
    });

    /* --- 开关 --- */
    el.chkTask.addEventListener('change', () => render());
    el.chkVoice.addEventListener('change', () => {
      el.sVoice.checked = el.chkVoice.checked;
      save('voice', el.chkVoice.checked);
      if (el.chkVoice.checked && !voiceSupported()) {
        flash(el.chkVoice);
      }
    });
    el.chkDnd.addEventListener('change', () => {
      save('dnd', el.chkDnd.checked);
      if (el.chkDnd.checked) Array.from(el.toastLayer.children).forEach(t => t.remove());
    });

    /* --- 8 状态网格：注入（产品形态禁用）--- */
    el.stateGrid.addEventListener('click', (e) => {
      const cell = e.target.closest('.state-cell'); if (!cell) return;
      if (prodMode) {
        /* v3.14：产品形态下明确拒绝，而不是静默 return。
           静默会被读成"点了没反应/坏了"；这里闪一次告警色边框，配合 title 说明原因。 */
        cell.classList.add('deny');
        setTimeout(() => cell.classList.remove('deny'), 380);
        return;
      }
      if (cell.hasAttribute('data-unavailable')) return;   // 恒不可判的格子点它也没意义
      S.phase = cell.dataset.key;
      S.tourFrame = 0;
      setModeSeg('manual');
      syncStateGridSelection();
      render();
      flash(cell);
    });

    /* --- 优先级拖拽 --- */
    bindPrioDrag();
    el.btnPrioReset.addEventListener('click', () => {
      engine.priority = K.PRIORITY.slice();
      save('priority', engine.priority);
      buildPrioList(); render();
      flash(el.prioList);
    });

    /* --- 曲线：十字线 + 视图范围 --- */
    bindChart();
    document.querySelectorAll('.head-tools .chip').forEach(c => {
      c.addEventListener('click', () => {
        document.querySelectorAll('.head-tools .chip').forEach(x => x.classList.toggle('active', x === c));
        S.spanLimit = Number(c.dataset.span);
        save('span', S.spanLimit);
        drawChart();
      });
    });

    /* --- 提醒筛选 --- */
    el.remindFilter.addEventListener('click', (e) => {
      const c = e.target.closest('.chip'); if (!c) return;
      Array.from(el.remindFilter.children).forEach(x => x.classList.toggle('active', x === c));
      S.filter = c.dataset.lv;
      renderRemind();
    });

    /* --- 决策模拟 --- */
    document.querySelectorAll('.trade').forEach(b => {
      b.addEventListener('click', () => {
        const dec = engine.lastDecision || { state: 'neutral', conf: 0.6 };
        const weak = engine.cfg.weakConf;
        const impulsive = K.IMPULSIVE_STATES.indexOf(dec.state) >= 0 && dec.conf >= weak;
        engine.rationalityLog(impulsive, dec);
        S.decisions.push(impulsive);
        if (S.decisions.length > 500) S.decisions.shift();   // v3.14：长会话不无限增长，只用于近 10 次滚动
        if (S.history.length) {
          // v3.13：把最近一帧的冷静度补上 —— 否则"暂停时点一次决策"后，
          // 冷静度数字变了、曲线却仍停在空态，两者自相矛盾。
          S.history[S.history.length - 1].calm = engine.calmness;
          S.history[S.history.length - 1].event = impulsive ? 'impulsive' : 'normal';
        }
        el.vIsImp.textContent = impulsive ? '是（冲动）' : '否';
        el.vIsImp.style.color = impulsive ? familyColor('alert') : familyColor('good');
        flash(b);
        render();
      });
    });
    el.abSwitch.addEventListener('click', (e) => {
      const c = e.target.closest('.chip'); if (!c) return;
      Array.from(el.abSwitch.children).forEach(x => x.classList.toggle('active', x === c));
      engine.abSegment = c.dataset.seg;
      render();
    });

    /* --- 抽屉：打开 / 关闭 --- */
    el.btnDetail.addEventListener('click', () => toggleDrawer('detail'));
    el.btnSettings.addEventListener('click', () => toggleDrawer('settings'));
    if (el.btnDiagRefresh) el.btnDiagRefresh.addEventListener('click', refreshDiag);
    el.btnAbout.addEventListener('click', () => toggleDrawer('about'));
    // 真机场景下"校准"是开跑前必做的一步，顶栏给直达入口（避免翻抽屉才发现）
    if (el.btnCalib) el.btnCalib.addEventListener('click', () => { toggleDrawer('calib'); renderCalibInfo(); });
    if (el.btnCalibBanner) {
      el.btnCalibBanner.addEventListener('click', () => { toggleDrawer('calib'); renderCalibInfo(); });
    }
    if (el.btnExportStats) el.btnExportStats.addEventListener('click', exportStats);
    if (el.drawerTabs) {
      el.drawerTabs.addEventListener('click', (e) => {
        const b = e.target.closest('.chip'); if (!b) return;
        openDrawer(b.dataset.pane);
      });
    }
    el.drawerClose.addEventListener('click', () => closeDrawer());
    el.scrim.addEventListener('click', () => closeDrawer());
    document.addEventListener('keydown', (e) => {
      if (e.key !== 'Escape') return;
      if (!el.drawer.hidden) { e.preventDefault(); closeDrawer(); }
    });
    // 主界面三个「查看更多」入口
    if (el.btnOpenDetail) el.btnOpenDetail.addEventListener('click', () => openDrawer('detail'));
    if (el.btnOpenLegend) el.btnOpenLegend.addEventListener('click', () => openDrawer('detail'));
    if (el.btnOpenRemind) el.btnOpenRemind.addEventListener('click', () => openDrawer('detail'));

    /* --- 主题 --- */
    el.btnTheme.addEventListener('click', () => {
      const cur = document.documentElement.dataset.theme;
      const next = cur === 'dark' ? 'light' : 'dark';
      document.documentElement.dataset.theme = next;
      save('theme', next);
      render();
    });

    /* --- 设置表单 --- */
    bindSettings();

    /* --- 窗口 --- */
    window.addEventListener('resize', debounce(drawChart, 60));
    if (window.ResizeObserver) new ResizeObserver(redraw).observe(el.chart.parentElement);
  }

  function setModeSeg(mode) {
    S.mode = mode;
    /* v3.12：自动/手动/粘贴三种模式没有真机接触质量数据，此前这一格永远显示「–」，
       看起来像渲染坏了。明确写「模拟」并给出说明，比一个破折号诚实。 */
    if (el.mSignal) {
      if (mode === 'live') {
        el.mSignal.textContent = '–';
        el.mSignal.dataset.q = 'na';
        el.mSignal.title = '等待设备上报 poorSignalLevel';
      } else {
        el.mSignal.textContent = '模拟';
        el.mSignal.dataset.q = 'na';
        el.mSignal.title = '演示模式没有接触质量数据；切到「真机/回放」才有效';
      }
    }
    Array.from(el.segMode.children).forEach(x => x.classList.toggle('active', x.dataset.mode === mode));
  }

  /* ---- 优先级列表拖拽 ----
   * 采用「浮层克隆 + 原位 DOM 换位」方案：克隆体用 fixed 定位跟随指针（只需累加位移，
   * 无需反复重算 transform 基线），原节点真实换位即时反映插入效果。
   * 桌面鼠标与移动端触摸统一走 Pointer Events，并额外支持方向键（可访问性）。 */
  function bindPrioDrag() {
    let dragEl = null, clone = null, grabDY = 0;

    function cleanup() {
      if (clone && clone.parentNode) clone.remove();
      if (dragEl) dragEl.classList.remove('ghost');
      Array.from(el.prioList.children).forEach(x => x.classList.remove('over'));
      clone = null;
    }

    el.prioList.addEventListener('pointerdown', (e) => {
      const item = e.target.closest('.prio-item');
      if (!item || e.button === 2) return;
      dragEl = item;
      const r = item.getBoundingClientRect();
      grabDY = e.clientY - r.top;
      clone = item.cloneNode(true);
      clone.classList.add('floating');
      clone.style.cssText += `position:fixed;left:${r.left}px;top:${r.top}px;width:${r.width}px;`
        + `pointer-events:none;margin:0;z-index:999;box-shadow:var(--shadow-2);cursor:grabbing;`;
      document.body.appendChild(clone);
      item.classList.add('ghost');
      try { if (item.setPointerCapture) item.setPointerCapture(e.pointerId); } catch (err) { /* noop */ }
      e.preventDefault();
    });

    el.prioList.addEventListener('pointermove', (e) => {
      if (!dragEl || !clone) return;
      clone.style.top = (e.clientY - grabDY) + 'px';
      // 依据指针相对各兄弟中线决定插入位置（真实换位，视觉即时生效）
      const others = Array.from(el.prioList.children).filter(x => x !== dragEl);
      let ref = null;
      for (const s of others) {
        const r = s.getBoundingClientRect();
        if (e.clientY < r.top + r.height / 2) { ref = s; break; }
      }
      Array.from(el.prioList.children).forEach(x => x.classList.remove('over'));
      if (ref) { el.prioList.insertBefore(dragEl, ref); ref.classList.add('over'); }
      else { el.prioList.appendChild(dragEl); }
    });

    const finish = () => {
      if (!dragEl) return;
      cleanup();
      dragEl = null;
      commitPrio();
      buildPrioList();
      render();
    };
    el.prioList.addEventListener('pointerup', finish);
    el.prioList.addEventListener('pointercancel', finish);
    document.addEventListener('pointerup', () => { if (dragEl) finish(); });   // 指针移出列表也收尾
    el.prioList.addEventListener('keydown', (e) => {
      const item = e.target.closest('.prio-item'); if (!item) return;
      if (e.key !== 'ArrowUp' && e.key !== 'ArrowDown') return;
      e.preventDefault();
      const prev = e.target.previousElementSibling, next = e.target.nextElementSibling;
      if (e.key === 'ArrowUp' && prev) el.prioList.insertBefore(e.target, prev);
      else if (e.key === 'ArrowDown' && next) el.prioList.insertBefore(next, e.target);
      else return;
      commitPrio(); buildPrioList();
      const again = el.prioList.querySelector(`[data-key="${item.dataset.key}"]`);
      if (again) again.focus();
      render();
    });
  }

  /* ---- 曲线交互 ---- */
  function bindChart() {
    const cv = el.chart;
    function idxFromEvent(e) {
      const data = S.spanLimit > 0 ? S.history.slice(-S.spanLimit) : S.history;
      if (!data.length) return null;
      const rect = cv.getBoundingClientRect();
      const pad = { l: 38, r: 12 };
      const iw = rect.width - pad.l - pad.r;
      const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left - pad.l) / iw));
      return Math.round(ratio * (data.length - 1));
    }
    function move(e) {
      const data = S.spanLimit > 0 ? S.history.slice(-S.spanLimit) : S.history;
      const idx = idxFromEvent(e);
      if (idx == null || !data[idx]) { ChartView.setHover(null); el.chartTip.hidden = true; drawChart(); return; }
      ChartView.setHover(idx);
      const p = data[idx];
      el.chartTip.hidden = false;
      el.chartTip.innerHTML = `<span class="tt-t">t = ${p.t}s · ${metaOf(p.state).cn}</span>
        <span class="tt-c">${fmt(p.calm)}</span>
        <span class="tt-s"><i class="lg" style="width:8px;height:8px;border-radius:2px;background:${familyColor(p.family)}"></i>
        ${p.state} · ${p.level} · conf ${fmt(p.conf)}</span>
        ${p.roll != null ? `<span class="tt-s">滚动10次 ${fmt(p.roll)}</span>` : ''}
        ${p.event ? `<span class="tt-s">${p.event === 'impulsive' ? Icons.svg('close') + '冲动操作' : Icons.svg('check') + '冷静操作'}</span>` : ''}`;
      const rect = cv.getBoundingClientRect();
      const pad = { l: 38, r: 12 };
      const iw = rect.width - pad.l - pad.r;
      const x = pad.l + (data.length === 1 ? iw / 2 : (idx / (data.length - 1)) * iw);
      el.chartTip.style.left = Math.max(80, Math.min(rect.width - 80, x)) + 'px';
      el.chartTip.style.top = '40px';
      drawChart();
    }
    cv.addEventListener('pointerdown', move);
    cv.addEventListener('pointermove', move);
    cv.addEventListener('pointerleave', () => { ChartView.setHover(null); el.chartTip.hidden = true; drawChart(); });
    cv.addEventListener('pointercancel', () => { ChartView.setHover(null); el.chartTip.hidden = true; drawChart(); });
  }

  /* ---- 设置表单 ---- */
  function bindSettings() {
    el.sWeakConf.addEventListener('input', () => {
      const v = Number(el.sWeakConf.value);
      engine.cfg.weakConf = v; save('weakConf', v);
      el.lblWeakConf.textContent = v.toFixed(2);
    });
    el.sDebounce.addEventListener('input', () => {
      const v = Number(el.sDebounce.value);
      engine.cfg.debounceS = v; save('debounceS', v);
      el.lblDebounce.textContent = v + ' s';
    });
    el.sHitRatio.addEventListener('input', () => {
      const v = Number(el.sHitRatio.value);
      engine.cfg.hitRatio = v; save('hitRatio', v);
      el.lblHitRatio.textContent = v.toFixed(2);
    });
    el.sCdScale.addEventListener('input', () => {
      const v = Number(el.sCdScale.value);
      engine.cfg.cooldownScale = v; save('cdScale', v);
      el.lblCdScale.textContent = v.toFixed(2) + ' ×';
    });
    el.sAdapt.addEventListener('change', () => {
      engine.adaptEnabled = el.sAdapt.checked; save('adapt', el.sAdapt.checked);
    });
    el.sDemoBoost.addEventListener('change', () => save('demoBoost', el.sDemoBoost.checked));

    // 通知
    el.btnNotifyPerm.addEventListener('click', () => {
      if (!('Notification' in window)) {
        el.notifyCap.textContent = '当前权限：该浏览器/环境不支持 Notification（file:// 下常见）';
        return;
      }
      const done = (perm) => { el.notifyCap.textContent = '当前权限：' + permName(perm); };
      try {
        const p = Notification.requestPermission(function (r) { done(r); });
        if (p && p.then) p.then(done).catch(() => done(Notification.permission));
      } catch (e) {
        done(Notification.permission);
      }
      setTimeout(() => { el.notifyCap.textContent = '当前权限：' + permName(Notification.permission); }, 600);
    });
    el.sUseNotify.addEventListener('change', () => save('useNotify', el.sUseNotify.checked));

    // 语音（两处开关互相同步）
    el.sVoice.addEventListener('change', () => { el.chkVoice.checked = el.sVoice.checked; save('voice', el.sVoice.checked); });
    el.sVoiceRate.addEventListener('input', () => { el.lblRate.textContent = Number(el.sVoiceRate.value).toFixed(1); save('rate', el.sVoiceRate.value); });
    el.sVoiceVol.addEventListener('input', () => { el.lblVol.textContent = Number(el.sVoiceVol.value).toFixed(2); save('vol', el.sVoiceVol.value); });
    el.sVoiceSel.addEventListener('change', () => save('voiceName', el.sVoiceSel.value));
    el.btnTestVoice.addEventListener('click', () => {
      if (!voiceSupported()) { flash(el.btnTestVoice); return; }
      const prev = el.chkVoice.checked;
      el.chkVoice.checked = true;
      speak(K.ADVICE.stress, 'calm');
      el.chkVoice.checked = prev;
    });
    el.voiceLevels.addEventListener('change', () => {
      const v = Array.from(el.voiceLevels.querySelectorAll('input')).map(i => ({ v: i.value, c: i.checked }));
      save('voiceLevels', v);
    });
    loadVoices();
    if (voiceSupported()) window.speechSynthesis.onvoiceschanged = loadVoices;

    // 粘贴数据
    el.btnSample.addEventListener('click', () => { el.pasteArea.value = MOCK.SAMPLE_JSONL; validatePaste(); });
    el.btnLoadPaste.addEventListener('click', () => {
      if (loadPaste(false)) { setModeSeg('paste'); closeDrawer(); setPlaying(true); }
    });
    el.pasteArea.addEventListener('input', debounce(validatePaste, 220));

    // 疲劳工具
    el.btnFastIf.addEventListener('click', () => {
      engine.thetaIntegral = engine.thetaIntegral + 300;
      el.mIf.textContent = fmt(engine.thetaIntegral, 0) + ' / ' + K.IF_HOLD_S + 's';
      flash(el.mIf);
      pushRemind({ id: ++S.seq, t: S.simClock, state: 'fatigue', level: 'silent', action: '演示工具：I_f 疲劳积分 +300s（跳过累积等待）', conf: 0.6, sys: true });
    });
    el.btnClearIf.addEventListener('click', () => {
      engine.thetaIntegral = 0;
      el.mIf.textContent = fmt(engine.thetaIntegral, 0) + ' / ' + K.IF_HOLD_S + 's';
      flash(el.mIf);
    });
  }

  function permName(p) {
    return p === 'granted' ? '已授权' : p === 'denied' ? '已拒绝' : '未询问（default）';
  }

  function loadVoices() {
    if (!voiceSupported()) return;
    let vs = [];
    try { vs = window.speechSynthesis.getVoices() || []; } catch (e) { return; }
    if (!vs.length) return;
    const cur = load('voiceName', '');
    el.sVoiceSel.innerHTML = '<option value="">默认（系统）</option>' +
      vs.map(v => `<option value="${escapeHtml(v.name)}">${escapeHtml(v.name)} · ${escapeHtml(v.lang || '')}</option>`).join('');
    if (cur) el.sVoiceSel.value = cur;
  }

  function validatePaste() {
    const text = (el.pasteArea.value || '').trim();
    el.pasteArea.classList.remove('invalid');
    if (!text) { el.pasteMsg.className = 'paste-msg'; el.pasteMsg.textContent = ''; return true; }
    const lines = text.split(/\r?\n/).filter(s => s.trim());
    let ok = 0, bad = 0, firstErr = '';
    for (let i = 0; i < lines.length; i++) {
      let o;
      try { o = JSON.parse(lines[i]); }
      catch (e) { bad++; if (!firstErr) firstErr = `第 ${i + 1} 行不是合法 JSON`; continue; }
      if (!o || typeof o !== 'object') { bad++; if (!firstErr) firstErr = `第 ${i + 1} 行不是对象`; continue; }
      ok++;
    }
    if (bad) {
      el.pasteArea.classList.add('invalid');
      el.pasteMsg.className = 'paste-msg err';
      el.pasteMsg.textContent = `${firstErr}（共 ${bad} 行无效，将被跳过；有效 ${ok} 行）`;
      return false;
    }
    el.pasteMsg.className = 'paste-msg ok';
    el.pasteMsg.textContent = `校验通过：${ok} 帧可载入`;
    return true;
  }

  function loadPaste(silent) {
    if (!validatePaste()) { if (silent) return false; flash(el.pasteArea); return false; }
    const lines = (el.pasteArea.value || '').split(/\r?\n/).filter(s => s.trim());
    const q = [];
    lines.forEach((ln, i) => {
      let o; try { o = JSON.parse(ln); } catch (e) { return; }
      if (!o || typeof o !== 'object') return;
      const f = new Frame({
        attention: pick(o.attention, 50), meditation: pick(o.meditation, 50),
        delta: pick(o.delta, 100), theta: pick(o.theta, 100), alpha: pick(o.alpha, 100),
        beta: pick(o.beta, 100), gamma: pick(o.gamma, 50),
        rr_ms: sanitizeRR(o.rr_ms),
        signal: (o.signal == null ? null : Number(o.signal)),   // v3.8：粘贴真实 dump 同样要带接触质量
        gyro: Array.isArray(o.gyro) ? o.gyro : [0, 0, 0]
      });
      if (!Number.isFinite(f.theta) || f.theta <= 0) return;   // 非法帧丢弃
      q.push(f);
    });
    if (!q.length) {
      el.pasteMsg.className = 'paste-msg err';
      el.pasteMsg.textContent = '无有效帧：至少需要 theta > 0';
      return false;
    }
    S.pasteQueue = q; S.pasteIdx = 0;
    el.pasteMsg.className = 'paste-msg ok';
    el.pasteMsg.textContent = `已载入 ${q.length} 帧${lines.length > q.length ? `（跳过 ${lines.length - q.length} 行无效）` : ''}`;
    return true;
  }
  function pick(v, dflt) {
    // 与 engine.js 的 num() 同口径：null / 空串 = 缺值 → 默认值。
    // Number(null) 是 0，旧写法会把"这一行没写 attention"读成 0 → 焦虑门控误命中。
    if (v == null || v === '') return dflt;
    const n = Number(v);
    return Number.isFinite(n) ? n : dflt;
  }
  /** v2.8.5 输入自净：RR 必须落在生理范围 [300,2000]ms，否则置 null（回放路径与 pushRR 过滤对齐） */
  function sanitizeRR(v) {
    const n = Number(v);
    return Number.isFinite(n) && n >= K.RR_MIN_MS && n <= K.RR_MAX_MS ? n : null;
  }
  /** v2.8.5 输入自净：localStorage 坏值回落默认（clamp 值域） */


  /* ---- 抽屉（详情 / 设置 / 说明）----
   * 三种关闭方式：✕ 按钮、点击遮罩、ESC 键 —— 全部走 closeDrawer()，
   * 关闭后主界面照常显示核心数据（不清空、不重置，仅在关闭时刷新一次渲染）。
   * DOM 用 hidden 属性控制显隐，CSS 中 [hidden]{display:none!important} 兜底，
   * 否则 .drawer{display:flex} 会盖掉浏览器默认的 [hidden] 规则导致"关不掉"。 */
  const DRAWER_TITLE = { detail: '详细数据与判别', settings: '设置', calib: '真机校准与数据源', about: '技术说明（模型 / 应用 / 软件）' };
  let curPane = null;

  function openDrawer(which) {
    if (which === 'settings') refreshDiag();   // 打开即查，避免看到上次的陈旧读数
    curPane = which;
    el.drawer.hidden = false; el.scrim.hidden = false;
    el.paneDetail.hidden = which !== 'detail';
    el.paneSettings.hidden = which !== 'settings';
    el.paneCalib.hidden = which !== 'calib';
    el.paneAbout.hidden = which !== 'about';
    el.drawerTitle.textContent = DRAWER_TITLE[which] || '面板';
    Array.from(el.drawerTabs.children).forEach(b =>
      b.classList.toggle('active', b.dataset.pane === which));
    // 详情面板里有 canvas 以外的布局依赖，打开时重算一次曲线尺寸
    drawChart();
  }
  function closeDrawer() {
    curPane = null;
    el.drawer.hidden = true; el.scrim.hidden = true;
    Array.from(el.drawerTabs.children).forEach(b => b.classList.remove('active'));
    render();                       // 回到主界面后刷新核心数据
  }
  /** 顶栏按钮：再点一次同一个面板 = 关闭（切换语义） */
  function toggleDrawer(which) {
    if (!el.drawer.hidden && curPane === which) closeDrawer();
    else openDrawer(which);
  }

  /* ---- 小动效 ---- */
  function pulseBrand() {
    el.brandMark.classList.remove('pulse');
    void el.brandMark.offsetWidth;
    el.brandMark.classList.add('pulse');
  }
  function flash(node) {
    if (!node) return;
    node.style.transition = 'none';
    node.style.filter = 'brightness(1.35)';
    setTimeout(() => {
      node.style.transition = 'filter .4s ease';
      node.style.filter = '';
    }, 120);
  }

  /* ============================ 初始化 ============================ */
  function restorePrefs() {
    const t = load('theme', 'light');
    document.documentElement.dataset.theme = t;
    // v3.14：恢复范围必须与滑块 min/max 一致 —— 原来允许 0.1–60 / 1–60，
    // 若 localStorage 里是越界值（旧版本遗留/手改），滑块显示被 clamp 到 5/20，
    // 变量却保留 60，于是"显示的步长"和"实际推进的步长"不是同一个数。
    S.stepSec = numIn(load('step', 1), 1, 5, 1); el.rngStep.value = S.stepSec; el.lblStep.textContent = S.stepSec + ' s/帧';
    S.fps = numIn(load('fps', 8), 1, 20, 8); el.rngSpeed.value = S.fps; el.lblSpeed.textContent = S.fps + ' 帧/秒';
    el.chkTask.checked = load('task', true);
    el.chkVoice.checked = load('voice', false); el.sVoice.checked = el.chkVoice.checked;
    el.chkDnd.checked = load('dnd', false);
    S.spanLimit = numIn(load('span', 120), 2, 100000, 120);
    document.querySelectorAll('.head-tools .chip').forEach(c =>
      c.classList.toggle('active', Number(c.dataset.span) === S.spanLimit));
    el.sAdapt.checked = !!load('adapt', false);
    el.sDemoBoost.checked = load('demoBoost', true);
    el.sUseNotify.checked = load('useNotify', true);
    el.sVoiceRate.value = numIn(load('rate', 1), 0.5, 2, 1); el.lblRate.textContent = Number(el.sVoiceRate.value).toFixed(1);
    el.sVoiceVol.value = numIn(load('vol', 0.9), 0, 1, 0.9); el.lblVol.textContent = Number(el.sVoiceVol.value).toFixed(2);
    const lv = load('voiceLevels', null);
    if (Array.isArray(lv)) {
      lv.forEach(item => {
        const box = el.voiceLevels.querySelector(`input[value="${item.v}"]`);
        if (box) box.checked = !!item.c;
      });
    }
    const cfg = engine.cfg;
    el.sWeakConf.value = cfg.weakConf; el.lblWeakConf.textContent = cfg.weakConf.toFixed(2);
    el.sDebounce.value = cfg.debounceS; el.lblDebounce.textContent = cfg.debounceS + ' s';
    el.sHitRatio.value = cfg.hitRatio; el.lblHitRatio.textContent = cfg.hitRatio.toFixed(2);
    el.sCdScale.value = cfg.cooldownScale; el.lblCdScale.textContent = cfg.cooldownScale.toFixed(2) + ' ×';
    if ('Notification' in window) {
      el.notifyCap.textContent = '当前权限：' + permName(Notification.permission);
    } else {
      el.notifyCap.textContent = '当前权限：该浏览器/环境不支持 Notification（file:// 下常见）';
    }
  }

  function resetAll(skipWarmup) {
    /* v3.14：重置前先收掉进行中的校准。
       否则 CAL.buf 会继续收集新数据源的帧，最终基线是「旧源 A/B 段 + 新源 C 段」
       拼出来的 —— 一份跨源混合的基线，比没有基线更危险。 */
    if (CAL.active) {
      cancelCalib();
      el.calibStep.textContent = '数据源已切换，校准已取消（基线不跨数据源拼接）';
    }
    engine = newEngine();
    S.simClock = 0; S.frames = 0; S.tourIdx = 0; S.tourFrame = 0;
    S.history = []; S.remindEvents = []; S.decisions = []; S.seq = 0; S.unread = 0;
    S.pasteIdx = 0; S.hover = null;
    ChartView.setHover(null); prevStateKey = null;
    el.prioList.innerHTML = ''; buildPrioList();
    rnd = MOCK.makeRnd(20261023);
    // 真机模式不预热：mock 预热帧会污染真机判别（假 focus 填满窗口），且真机首帧应如实显示 neutral
    if (!skipWarmup) warmup();
    if (S.mode === 'auto') { S.tourIdx = 0; S.tourFrame = 0; onEnterPhase(MOCK.AUTO_TOUR[0].phase); }
    renderRemind(); render(); updateBadgeCount(); renderCalibInfo();
    Array.from(el.toastLayer.children).forEach(t => t.remove());
    el.chartTip.hidden = true;
    el.vIsImp.textContent = '—'; el.vIsImp.style.color = '';
  }

  function init() {
    // v3.16：曲线绘制已拆到 js/chart.js；这里注入共享上下文（S 用 getter，engine 不入）
    if (window.ChartView) ChartView.configure({
      el, familyColor, cssVar, hexA, fmtT,
      get S() { return S; }
    });
    engine = newEngine();
    buildStateGrid();
    buildPrioList();    buildZBars();
    buildCooldowns();
    // v3.13：设置面板按分组折叠（演示类默认收起），restorePrefs 之前做，保证控件引用有效
    // 默认只展开「数据源诊断」「数据流控制」——诊断是现场排障第一入口，滑块是最常调的；
    // 判据参数与提醒通道属于一次性配置，收起后 320 宽下的滚动从 4.5 屏降到 2 屏内。
    foldGroups(el.paneSettings, /判别参数|提醒通道|粘贴帧数据|疲劳演示工具/);
    restorePrefs();
    bindAll();
    initBadge();
    makeDraggable(el.prevPopup, { ignore: 'button' });
    // v2.9.2：数据桥状态与地址
    // v3.16：页面由数据桥自己托管时，默认连「当前这个源」。否则 8771（真机）页会沿用写死的
    //        8770（模拟）地址——现场看着在动，其实是模拟数据。file:// 打开时仍回落到 8770。
    const bridgeDefault = /^https?:$/.test(location.protocol) ? location.origin : 'http://127.0.0.1:8770';
    if (el.txtBridge) el.txtBridge.value = load('bridgeUrl', bridgeDefault);
    if (window.LiveBridge) {
      LiveBridge.onstatus = bridgeStatusText;
      bridgeStatusText('idle', '');
    }
    renderCalibInfo();
    // v3.13：形态切换要在 bindAll 之后（依赖 segMode 里的按钮）、warmup 之前
    applyProdMode(/[?&]prod=1\b/.test(location.search) || !!load('prodMode', false), false);
    warmup();
    render();
    renderRemind();
    requestAnimationFrame(loop);

    /* v3.13：调试出口（仅在 ?debug=1 时挂载）。
       验收"无 RR 通道""未校准横幅"这类依赖硬件条件的界面时，
       没有它就只能在真机上碰运气。正式使用不暴露任何内部状态。 */
    if (/[?&]debug=1\b/.test(location.search)) {
      window.__bs = {
        eng: () => engine, S, K, render, drawChart: () => redraw(),
        setNoRR: (v) => { engine.rrSeen = !v; engine.frameCount = v ? 999 : 0; render(); },
        pushRemind, gradeLevel,
        // 校准流程对 e2e 可见（?debug=1 才挂）：三段自动喂帧需要读/改 targets
        CAL, startCalib, cancelCalib,
      };
    }
  }

  // 兜底：模块缺失时给出可见提示而非白屏
  if (!window.BrainState || !window.MockSource) {
    document.body.insertAdjacentHTML('afterbegin',
      '<div style="padding:14px;background:#dc3b4e;color:#fff">脚本加载失败：请确认 js/engine.js、js/mock.js、js/app.js 与 index.html 同级目录结构完整。</div>');
    return;
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();

})();
