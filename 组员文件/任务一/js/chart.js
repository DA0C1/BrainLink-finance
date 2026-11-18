/* =============================================================================
 * chart.js — 理智度曲线绘制（v3.16 从 app.js 拆出）
 *
 * 通过 configure(ctx) 注入 el / S(getter) / 颜色与格式化函数，本文件不直接触碰
 * 应用状态；hover 由 ChartView.setHover(idx) 控制。保持 classic script、无构建。
 * ========================================================================== */
(function (global) {
  'use strict';
  let C = null;
  let hoverIdx = null;

  function configure(ctx) { C = ctx; }
  function setHover(i) { hoverIdx = i; }

  function drawChart() {
    const cv = C.el.chart, ctx = cv.getContext('2d');
    const dpr = window.devicePixelRatio || 1;
    const rect = cv.getBoundingClientRect();
    const w = Math.max(240, rect.width), h = Math.max(160, rect.height || 230);
    if (cv.width !== Math.round(w * dpr) || cv.height !== Math.round(h * dpr)) {
      cv.width = Math.round(w * dpr); cv.height = Math.round(h * dpr);
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);

    const data = C.S.spanLimit > 0 ? C.S.history.slice(-C.S.spanLimit) : C.S.history;
    /* v3.13：只有"有帧但都没决策"时也要显示空态 —— 否则会画出一张 Y 轴 0–1、
       折线贴底的空网格，读者会理解成"冷静度掉到 0"。 */
    const anyCalm = data.some(p => p.calm != null);
    C.el.chartEmpty.style.opacity = anyCalm ? '0' : '1';
    // 空态文案要分两种情形：一帧都没有 vs 有帧但还没有决策样本
    C.el.chartEmpty.textContent = data.length
      ? '尚无决策样本 · 记录一次操作后开始绘制'
      : '暂无数据 · 点击「开始推流」后曲线实时绘制';
    // v3.13：量词统一 —— 曲线上的每个点是"一帧"（1 帧 ≈ 1.2s 真机、1s 仿真），
    // 决策是"次"，提醒是"条"。原写作"点"，和另外两个混在一起。
    C.el.curveRange.textContent = '最近 ' + data.length + ' 帧';
    if (!data.length || !anyCalm) return;   // 没有可画的点就只留空态文字，不画 0–1 空网格

    const pad = { l: 38, r: 12, t: 12, b: 22 };
    const iw = w - pad.l - pad.r, ih = h - pad.t - pad.b;
    const X = (i) => pad.l + (data.length === 1 ? iw / 2 : (i / (data.length - 1)) * iw);
    /* v3.7：Y 轴自适应。原来固定 0–1 —— calmness 长期高位时折线贴顶、面积填充从顶铺到底，
       整张图变成一块纯色板（mock 恒 1.00 时最刺眼）。自适应带最小跨度 MIN_SPAN，
       避免"数据几乎不动"时把微抖动放大成剧烈起伏。 */
    /* v3.13：calm 可能为 null（尚未记录任何决策）。旧写法直接 map 后取 min/max，
       null 被 Number() 成 0 → Y 轴被拽到 0–1，视觉上像是"冷静度崩了"。
       这里先剔除 null：全空则固定 0–1 画网格，不画折线。 */
    const calmVals = data.filter(p => p.calm != null).map(p => Math.max(0, Math.min(1, p.calm)));
    const hasCalm = calmVals.length > 0;
    let yLo = hasCalm ? Math.min(...calmVals) : 0;
    let yHi = hasCalm ? Math.max(...calmVals) : 1;
    const MIN_SPAN = 0.20;
    if (yHi - yLo < MIN_SPAN) {
      const mid = (yHi + yLo) / 2;
      yLo = Math.max(0, mid - MIN_SPAN / 2);
      yHi = Math.min(1, mid + MIN_SPAN / 2);
      if (yHi - yLo < MIN_SPAN) yLo = Math.max(0, yHi - MIN_SPAN);
    }
    const Y = (v) => pad.t + (1 - (Math.max(yLo, Math.min(yHi, v)) - yLo) / (yHi - yLo)) * ih;

    // 状态着色带
    let i = 0;
    while (i < data.length) {
      let j = i;
      while (j + 1 < data.length && data[j + 1].family === data[i].family) j++;
      const x0 = X(i), x1 = X(j);
      ctx.fillStyle = C.familyColor(data[i].family);
      /* v3.8：0.14 会让"整段同族"的历史把绘图区铺成一块纯色板（看起来像渲染故障）。
         缩到 0.06：仍能一眼看出状态分段，但不压过折线与网格。 */
      /* v3.12：整列染色 → 底部 5px 状态带。
         整列染色在"长时间同一族"时会把整个绘图区铺成一块淡色板（看起来像渲染故障）；
         底部色带同样能读出状态分段，却不占用数据区，也更接近监护仪的写法。 */
      ctx.globalAlpha = data[i].family === 'idle' ? 0.16 : 0.70;
      ctx.fillRect(x0, pad.t + ih - 5, Math.max(1, x1 - x0), 5);
      ctx.globalAlpha = 1;
      i = j + 1;
    }

    // 网格 + 轴标
    ctx.font = '10px "Bahnschrift", "DIN Alternate", system-ui, sans-serif';
    ctx.textBaseline = 'middle';
    for (let k = 0; k <= 4; k++) {
      const v = yLo + (yHi - yLo) * (k / 4);
      const y = Y(v);
      ctx.strokeStyle = C.cssVar('--border') || '#ddd';
      ctx.lineWidth = 1;
      ctx.setLineDash(k === 2 ? [3, 3] : []);
      ctx.beginPath(); ctx.moveTo(pad.l, y); ctx.lineTo(w - pad.r, y); ctx.stroke();
      ctx.setLineDash([]);
      ctx.fillStyle = C.cssVar('--text-3') || '#888';
      ctx.textAlign = 'right';
      ctx.fillText(v.toFixed(2), pad.l - 6, y);
    }

    // 面积渐变 + 折线
    const lineColor = C.familyColor(data[data.length - 1].family);
    /* v3.8：只在真有波动时铺面积。
       值恒定时（mock 的 calmness 长期 1.00、或受试者长时间稳定）折线贴在绘图区顶部，
       填充从折线一路铺到底 → 整张图变成一块纯色板，看着像渲染故障而不是"曲线"。
       极差 < 0.015 视为平稳，只画折线 + 端点。 */
    const calmSpan = hasCalm ? Math.max(...calmVals) - Math.min(...calmVals) : 0;
    if (hasCalm && calmSpan >= 0.015) {
      const grad = ctx.createLinearGradient(0, pad.t, 0, pad.t + ih);
      grad.addColorStop(0, C.hexA(lineColor, 0.15));
      grad.addColorStop(1, C.hexA(lineColor, 0.0));
      ctx.beginPath();
      const fi = data.findIndex(p => p.calm != null);
      ctx.moveTo(X(fi), pad.t + ih);
      data.forEach((p, k) => { if (p.calm != null) ctx.lineTo(X(k), Y(p.calm)); });
      ctx.lineTo(X(data.length - 1), pad.t + ih);
      ctx.closePath();
      ctx.fillStyle = grad; ctx.fill();
    }

    // v3.13：段内遇 null 就断开重开路径，不把"无数据"连成一条假线
    if (hasCalm) {
      ctx.beginPath();
      let started = false;
      data.forEach((p, k) => {
        if (p.calm == null) { started = false; return; }
        if (!started) { ctx.moveTo(X(k), Y(p.calm)); started = true; }
        else ctx.lineTo(X(k), Y(p.calm));
      });
      ctx.strokeStyle = lineColor; ctx.lineWidth = 2.5; ctx.lineJoin = 'round'; ctx.stroke();
    }

    // v3.7：末端点 —— 值长期贴顶时折线压在上边框看不清，给个当前值锚点
    const lastP = data[data.length - 1];
    if (lastP.calm != null) {
      const endX = X(data.length - 1), endY = Y(lastP.calm);
      ctx.beginPath(); ctx.arc(endX, endY, 3.6, 0, Math.PI * 2);
      ctx.fillStyle = lineColor; ctx.fill();
      ctx.strokeStyle = C.cssVar('--surface') || '#fff'; ctx.lineWidth = 1.6; ctx.stroke();
    }

    // 近 10 次决策滚动冷静度（虚线，更敏感）
    let started = false;
    ctx.setLineDash([4, 4]); ctx.lineWidth = 1.6;
    ctx.strokeStyle = C.hexA(C.cssVar('--warn') || '#d98218', 0.95);
    ctx.beginPath();
    data.forEach((p, k) => {
      if (p.roll == null) return;
      if (!started) { ctx.moveTo(X(k), Y(p.roll)); started = true; }
      else ctx.lineTo(X(k), Y(p.roll));
    });
    if (started) ctx.stroke();
    ctx.setLineDash([]);

    // 决策事件标记
    data.forEach((p, k) => {
      if (!p.event || p.calm == null) return;
      ctx.strokeStyle = p.event === 'impulsive' ? (C.cssVar('--alert') || '#e11') : (C.cssVar('--good') || '#0a0');
      ctx.lineWidth = 2;
      const x = X(k), y = Y(p.calm);
      if (p.event === 'impulsive') {
        ctx.beginPath();
        ctx.moveTo(x - 4, y - 4); ctx.lineTo(x + 4, y + 4);
        ctx.moveTo(x + 4, y - 4); ctx.lineTo(x - 4, y + 4);
        ctx.stroke();
      } else {
        ctx.beginPath(); ctx.arc(x, y, 3.2, 0, Math.PI * 2); ctx.stroke();
      }
    });

    // 时间轴
    ctx.fillStyle = C.cssVar('--text-3') || '#888';
    ctx.textAlign = 'center';
    ctx.fillText(C.fmtT(data[0].t) , pad.l + 12, h - 8);
    ctx.fillText(C.fmtT(data[data.length - 1].t), w - pad.r - 14, h - 8);

    // 十字线
    if (hoverIdx != null && hoverIdx >= 0 && hoverIdx < data.length) {
      const p = data[hoverIdx], x = X(hoverIdx);
      ctx.strokeStyle = C.hexA(C.cssVar('--text') || '#000', 0.28);
      ctx.setLineDash([2, 3]); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, pad.t); ctx.lineTo(x, pad.t + ih); ctx.stroke();
      ctx.setLineDash([]);
      if (p.calm != null) {
        const y = Y(p.calm);
        ctx.beginPath(); ctx.arc(x, y, 4.5, 0, Math.PI * 2);
        ctx.fillStyle = C.familyColor(p.family); ctx.fill();
        ctx.strokeStyle = '#fff'; ctx.lineWidth = 1.5; ctx.stroke();
      }
    }
  }

  global.ChartView = { configure, drawChart, setHover, get hoverIdx() { return hoverIdx; } };
})(window);
