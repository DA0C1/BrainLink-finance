/* 交易页：只展示已经发生的行情；服务端权威时钟逐秒推送。 */
(function () {
  const W = window.WatchLab;
  const sessionId = W.qs("session");
  const token = W.qs("token");

  const el = {
    subject: document.getElementById("subject-chip"),
    round: document.getElementById("top-round"),
    clock: document.getElementById("clock-chip"),
    dot: document.getElementById("status-dot"),
    status: document.getElementById("status-text"),
    record: document.getElementById("record-chip"),
    banner: document.getElementById("mode-banner"),
    price: document.getElementById("price-now"),
    change: document.getElementById("price-change"),
    tickMeta: document.getElementById("tick-meta"),
    cash: document.getElementById("acct-cash"),
    total: document.getElementById("acct-total"),
    pos: document.getElementById("acct-pos"),
    avg: document.getElementById("acct-avg"),
    float: document.getElementById("acct-float"),
    real: document.getElementById("acct-real"),
    seg: document.getElementById("seg"),
    buy: document.getElementById("btn-buy"),
    sell: document.getElementById("btn-sell"),
    msg: document.getElementById("trade-msg"),
    stateLine: document.getElementById("state-line"),
    stateText: document.getElementById("state-text"),
    eventStack: document.getElementById("event-stack"),
    feedbackStack: document.getElementById("feedback-stack"),
    overlay: document.getElementById("overlay"),
    ovTitle: document.getElementById("ov-title"),
    ovDesc: document.getElementById("ov-desc"),
    ovCount: document.getElementById("ov-count"),
    ovAction: document.getElementById("ov-action"),
    refreshHint: document.getElementById("refresh-hint"),
  };

  const S = {
    session: null,
    status: "created",
    tick: -1,
    prices: [],
    events: [],
    account: null,
    ratio: 0.5,
    basePrice: null,
    trades: [],
    busy: false,
    ended: false,
    yMin: null,
    yMax: null,
  };

  let chart = null;
  let ws = null;
  let retryTimer = null;
  let retryCount = 0;

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

  // ---------------------------------------------------------------- time

  // 服务端按 tick 推送，前端统一换算成“真实秒”展示，避免把 tick 当秒
  const tickMs = () => Number((S.session && S.session.tick_ms) || 2000);
  const secOf = (tick) => Number(tick) * tickMs() / 1000;
  const totalSec = () => Number((S.session && S.session.duration) || 0);

  // ---------------------------------------------------------------- chart

  function initChart() {
    if (!window.echarts) return;
    const box = document.getElementById("chart");
    chart = echarts.init(box);
    window.addEventListener("resize", () => chart && chart.resize());
    // 演示横幅出现/消失会改变图表容器高度，需同步重算画布，否则底部刻度被裁掉
    if (window.ResizeObserver) {
      new ResizeObserver(() => chart && chart.resize()).observe(box);
    }
    drawChart();
  }

  // 纵轴锁定：首次按已见价格定区间，之后只在价格真正越界时按 2 元一档外扩，
  // 不再每来一个新点就重算一次，避免刻度持续跳动（不使用未来价格）。
  function lockYAxis(values) {
    if (!values.length) return null;
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const STEP = 2;
    if (S.yMin == null) {
      const pad = Math.max(1, (hi - lo) * 0.2);
      S.yMin = Math.floor((lo - pad) / STEP) * STEP;
      S.yMax = Math.ceil((hi + pad) / STEP) * STEP;
    } else {
      if (lo < S.yMin + 0.6) S.yMin = Math.floor((lo - 1) / STEP) * STEP;
      if (hi > S.yMax - 0.6) S.yMax = Math.ceil((hi + 1) / STEP) * STEP;
    }
    return { min: S.yMin, max: S.yMax };
  }

  function drawChart() {
    if (!chart) return;
    const prices = S.prices;
    const bounds = lockYAxis(prices.map((p) => p.price));
    const x = prices.map((p) => p.tick);
    const y = prices.map((p) => p.price);
    const avg = [];
    let sum = 0;
    prices.forEach((p, i) => { sum += p.price; avg.push(sum / (i + 1)); });
    const eventMarks = S.events
      .filter((e) => e.tick <= S.tick)
      .map((e) => ({ xAxis: e.tick, name: e.event_type }));
    const trades = S.trades.filter((t) => t.tick <= S.tick);
    const spanSec = tickMs() / 1000;

    const option = {
      animation: false,
      backgroundColor: "transparent",
      grid: { left: 58, right: 26, top: 30, bottom: 34 },
      tooltip: {
        trigger: "axis",
        backgroundColor: "rgba(10,13,17,.94)",
        borderColor: "#2a3138",
        textStyle: { color: "#e6ebf0", fontSize: 12 },
        formatter: (params) => {
          const p = params && params[0];
          if (!p) return "";
          return `${W.clock(secOf(p.axisValue))}<br/>` +
            `价格 <b style="color:#f5c542">${Number(p.data).toFixed(2)}</b>`;
        },
      },
      legend: {
        show: prices.length > 0,
        top: 2, right: 12, itemWidth: 14, itemHeight: 2,
        textStyle: { color: "#78838f", fontSize: 11 },
        data: ["价格", "均价"],
      },
      xAxis: {
        type: "category",
        data: x,
        boundaryGap: false,
        axisLine: { lineStyle: { color: "#232a31" } },
        axisTick: { show: false },
        axisLabel: {
          color: "#78838f",
          formatter: (v) => W.clock(Number(v) * spanSec),
          hideOverlap: true,
        },
        splitLine: { show: true, lineStyle: { color: "#141a20" } },
      },
      yAxis: {
        type: "value",
        min: bounds ? bounds.min : null,
        max: bounds ? bounds.max : null,
        splitNumber: 5,
        axisLabel: { color: "#78838f", formatter: (v) => Number(v).toFixed(2) },
        splitLine: { lineStyle: { color: "#171d24" } },
      },
      series: [
        {
          name: "价格",
          type: "line",
          data: y,
          showSymbol: false,
          lineStyle: { color: "#eef1f4", width: 1.6 },
          itemStyle: { color: "#eef1f4" },
          areaStyle: {
            color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
              { offset: 0, color: "rgba(238,241,244,.16)" },
              { offset: 1, color: "rgba(238,241,244,0)" },
            ]),
          },
          markLine: eventMarks.length
            ? {
                symbol: "none",
                silent: true,
                lineStyle: { type: "dashed", color: "#f5c542", width: 1, opacity: .8 },
                label: {
                  formatter: (p) => p.name,
                  color: "#f5c542",
                  fontSize: 10,
                  position: "insideEndTop",
                },
                data: eventMarks,
              }
            : undefined,
          markPoint: trades.length
            ? {
                symbol: "pin",
                symbolSize: 30,
                data: trades.map((t) => ({
                  coord: [t.tick, t.price],
                  value: t.direction === "买入" ? "B" : "S",
                  itemStyle: {
                    color: t.direction === "买入" ? "#f04444" : "#00c9b7",
                  },
                  label: { color: "#fff", fontSize: 10, fontWeight: "bold" },
                })),
              }
            : undefined,
        },
        {
          name: "均价",
          type: "line",
          data: avg,
          showSymbol: false,
          smooth: true,
          lineStyle: { color: "#f5c542", width: 1.2 },
          itemStyle: { color: "#f5c542" },
        },
      ],
    };
    chart.setOption(option, true);
  }

  // ---------------------------------------------------------------- views

  function setStatus(status) {
    S.status = status;
    const running = status === "running";
    el.dot.classList.toggle("off", !running);
    const labels = {
      created: "待开始",
      running: "进行中",
      finished: "已结束",
      interrupted: "已中断",
      error: "异常",
    };
    el.status.textContent = labels[status] || status;
    syncButtons();
  }

  function syncButtons() {
    const running = S.status === "running" && !S.ended;
    el.buy.disabled = !running || S.busy;
    el.sell.disabled = !running || S.busy
      || !S.account || Number(S.account.position || 0) <= 1e-9;
  }

  function applyMeta() {
    const s = S.session || {};
    el.subject.textContent = `受试者 ${s.subject_id || "—"}`;
    const roundText = `第 ${s.round_index || 1} 轮`;
    el.round.textContent = roundText;
    document.title = `${roundText} · 模拟盯盘 · WatchLab`;

    const mode = s.mode || "experiment";
    if (mode === "demo") {
      el.banner.textContent = "演示模式 · 自动成交 · 不采集数据";
      el.banner.classList.remove("hidden");
      el.record.textContent = "演示模式";
    } else if (mode === "test") {
      el.banner.textContent = "测试模式 · 非正式实验，请勿用于受试者";
      el.banner.classList.remove("hidden");
      el.record.textContent = "测试模式";
    } else {
      el.banner.classList.add("hidden");
      el.record.textContent = "数据记录中";
    }
    if (el.refreshHint) {
      const step = tickMs() / 1000;
      el.refreshHint.textContent = `行情每 ${step >= 1 ? String(step).replace(/\.0$/, "") : step} 秒更新一次`;
    }
    el.stateText.textContent = S.status === "running"
      ? "本轮进行中，请保持坐姿"
      : "等待开始";
  }

  function renderPrice() {
    const last = S.prices.length ? S.prices[S.prices.length - 1] : null;
    if (!last) {
      el.price.textContent = "--";
      el.change.textContent = "--";
      el.change.className = "price-change";
      return;
    }
    if (S.basePrice == null) S.basePrice = last.price;
    const pct = ((last.price / S.basePrice - 1) * 100);
    el.price.textContent = W.num(last.price, 2);
    el.change.textContent = `${pct >= 0 ? "+" : ""}${pct.toFixed(2)}%`;
    el.change.className = "price-change " + (pct > 0.0001 ? "up" : pct < -0.0001 ? "down" : "");
    const elapsed = Math.min((S.tick + 1) * tickMs() / 1000, totalSec() || Infinity);
    el.tickMeta.textContent = `已走 ${W.clock(elapsed)} / 共 ${W.clock(totalSec())}`;
    const remaining = Math.max(0, totalSec() - elapsed);
    el.clock.textContent = `剩余 ${W.clock(remaining)}`;
  }

  function refreshAccount() {
    const acc = S.account;
    if (!acc) return;
    const last = S.prices.length ? S.prices[S.prices.length - 1] : null;
    const pos = Number(acc.position || 0);
    const avg = Number(acc.avg_price || 0);
    const cash = Number(acc.cash || 0);
    const floating = last ? pos * (last.price - avg) : Number(acc.floating_pnl || 0);
    const total = last ? cash + pos * last.price : cash + pos * avg;

    el.cash.textContent = W.money(cash);
    el.total.textContent = W.money(total);
    el.pos.textContent = W.num(pos, 2);
    el.avg.textContent = avg > 0 ? W.num(avg, 2) : "--";
    el.float.textContent = W.money(floating);
    el.float.style.color = floating > 0.005 ? "var(--up)" : floating < -0.005 ? "var(--down)" : "";
    el.real.textContent = W.money(acc.realized_pnl || 0);
    el.real.style.color = Number(acc.realized_pnl) > 0.005
      ? "var(--up)" : Number(acc.realized_pnl) < -0.005 ? "var(--down)" : "";
    syncButtons();
  }

  function applyAccount(acc) {
    S.account = acc;
    refreshAccount();
  }

  // ---------------------------------------------------------------- toasts

  const EVENT_CLASS = { 追涨诱导: "chase", 突发消息: "news", 波动冲击: "shock" };

  function showEvent(msg) {
    const cls = EVENT_CLASS[msg.event_type] || "news";
    const node = document.createElement("div");
    node.className = "toast " + cls;
    node.innerHTML =
      `<div class="t">${W.escape(msg.event_type)}</div>` +
      `<div class="b">${W.escape(msg.text)}</div>` +
      `<div class="sub">市场快讯 · 自动消失</div>`;
    el.eventStack.appendChild(node);
    setTimeout(() => node.remove(), Math.max(3, Number(msg.auto_close_s) || 8) * 1000);
  }

  function showFeedback(msg) {
    while (el.feedbackStack.children.length >= 2) {
      el.feedbackStack.firstElementChild.remove();
    }
    const lvl = msg.level === "calm" ? "calm" : "mild";
    const node = document.createElement("div");
    node.className = "toast feedback " + lvl;
    node.innerHTML =
      `<div class="t">状态提醒 · ${W.escape(msg.state_label || "")}</div>` +
      `<div class="b">${W.escape(msg.action || "")}</div>` +
      `<div class="sub">本提醒由设备状态自动触发</div>`;
    el.feedbackStack.appendChild(node);
    setTimeout(() => node.remove(), 12000);
    W.speak(msg.action || "");
  }

  // ---------------------------------------------------------------- messages

  function applyTick(msg) {
    const tick = Number(msg.tick);
    if (tick > S.tick) {
      S.tick = tick;
      S.prices.push({ tick, price: Number(msg.price) });
      S.events = S.events; // 事件单独推送
      renderPrice();
      refreshAccount();
      drawChart();
    }
    if (msg.status && msg.status !== S.status) setStatus(msg.status);
  }

  function handleSnapshot(msg) {
    S.session = msg.session;
    S.status = msg.session.status;
    S.tick = Number(msg.tick);
    S.prices = (msg.prices || []).map((p) => ({ tick: Number(p.tick), price: Number(p.price) }));
    S.events = msg.events || [];
    S.account = msg.account || null;
    S.basePrice = S.prices.length ? S.prices[0].price : null;
    applyMeta();
    setStatus(S.status);
    renderPrice();
    refreshAccount();
    drawChart();
    if (S.status === "running") hideOverlay();
    if (S.status === "finished") showEnd();
  }

  function handleMessage(msg) {
    switch (msg.type) {
      case "snapshot":
        handleSnapshot(msg);
        break;
      case "tick":
        applyTick(msg);
        break;
      case "event":
        S.events.push(msg);
        showEvent(msg);
        drawChart();
        break;
      case "feedback":
        showFeedback(msg);
        break;
      case "account":
        if (msg.trade) {
          const t = msg.trade;
          const dup = S.trades.some((x) => x.tick === t.tick && x.direction === t.direction);
          if (!dup) S.trades.push({ tick: t.tick, price: t.price, direction: t.direction });
          drawChart();
        }
        applyAccount(msg.account);
        break;
      case "end":
        setStatus("finished");
        S.ended = true;
        showEnd(msg.metrics);
        break;
      case "error":
        W.note(`服务端异常：${msg.message || "未知错误"}`);
        setStatus("error");
        break;
      default:
        break;
    }
  }

  // ---------------------------------------------------------------- overlay

  function hideOverlay() {
    el.overlay.classList.add("hidden");
  }

  function showOverlay(title, desc, actionText) {
    el.ovTitle.textContent = title;
    el.ovDesc.textContent = desc;
    el.ovCount.classList.add("hidden");
    el.ovAction.disabled = false;
    el.ovAction.textContent = actionText;
    el.overlay.classList.remove("hidden");
  }

  // 重连失败：不再无限静默重试，明确告诉受试者发生了什么
  function showLost() {
    const demo = S.session && S.session.demo;
    clearTimeout(retryTimer);
    el.status.textContent = "连接已断开";
    showOverlay(
      "连接已断开",
      demo
        ? "演示会话已结束或服务已重启。点下方按钮重新开始 3 分钟演示。"
        : "与本轮服务的连接已中断，本轮数据可能不完整。请举手示意主试，不要自行刷新。",
      demo ? "重新演示" : "刷新页面"
    );
    el.ovAction.onclick = async () => {
      if (!demo) {
        location.reload();
        return;
      }
      try {
        const res = await W.api("/api/demo", { method: "POST" });
        const q = new URLSearchParams({ session: res.session_id, token: res.token, demo: "1" });
        location.href = `/trading?${q.toString()}`;
      } catch (err) {
        el.ovDesc.textContent = err.message || "创建演示失败，请检查服务是否在运行。";
      }
    };
  }

  function showEnd() {
    const acc = S.account;
    const summary = acc
      ? `最终可用现金 ${W.money(acc.cash)}，持仓 ${W.num(acc.position, 2)} 股，已实现盈亏 ${W.money(acc.realized_pnl)}。`
      : "";
    const demo = S.session && S.session.demo;
    showOverlay(
      "本轮完成",
      `感谢参与。${summary}请保持坐姿，举手示意主试。`,
      demo ? "重新演示" : "返回首页"
    );
    el.ovAction.onclick = async () => {
      if (demo) {
        try {
          const res = await W.api("/api/demo", { method: "POST" });
          const q = new URLSearchParams({ session: res.session_id, token: res.token, demo: "1" });
          location.href = `/trading?${q.toString()}`;
        } catch (err) {
          W.note(err.message);
        }
      } else {
        location.href = "/";
      }
    };
  }

  async function begin() {
    el.ovAction.disabled = true;
    el.ovDesc.textContent = "倒计时结束后自动开始，请保持坐姿，不要切换窗口。";
    el.ovCount.classList.remove("hidden");
    for (let n = 3; n > 0; n -= 1) {
      el.ovCount.textContent = String(n);
      await sleep(1000);
    }
    el.ovCount.textContent = "开始";
    await sleep(350);
    try {
      await W.api(
        `/api/sessions/${sessionId}/start?token=${encodeURIComponent(token)}`,
        { method: "POST" }
      );
      hideOverlay();
      setStatus("running");
      el.stateText.textContent = "本轮进行中，请保持坐姿";
    } catch (err) {
      el.ovCount.classList.add("hidden");
      el.ovDesc.textContent = err.message || "启动失败，请通知主试。";
      el.ovAction.disabled = false;
      el.ovAction.textContent = "重试";
      el.ovAction.onclick = begin;
    }
  }

  el.ovAction.onclick = begin;

  // ---------------------------------------------------------------- trade

  async function trade(direction) {
    if (S.status !== "running" || S.busy) return;
    S.busy = true;
    syncButtons();
    el.msg.textContent = `${direction}委托提交中…`;
    try {
      const res = await W.api(
        `/api/sessions/${sessionId}/trades?token=${encodeURIComponent(token)}`,
        {
          method: "POST",
          body: JSON.stringify({ direction, ratio: S.ratio }),
        }
      );
      S.trades.push({ tick: res.tick, price: res.price, direction });
      applyAccount(res.account);
      drawChart();
      el.msg.textContent =
        `${direction} ${res.decision.position_ratio} 仓 · 成交价 ${W.num(res.price, 2)} · ` +
        `${W.clock(secOf(res.tick))}`;
    } catch (err) {
      el.msg.textContent = err.message || "下单失败";
      W.note(err.message || "下单失败");
    } finally {
      S.busy = false;
      syncButtons();
    }
  }

  el.seg.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-ratio]");
    if (!btn) return;
    [...el.seg.querySelectorAll("button")].forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    S.ratio = Number(btn.dataset.ratio);
  });

  el.buy.addEventListener("click", () => trade("买入"));
  el.sell.addEventListener("click", () => trade("卖出"));

  // ---------------------------------------------------------------- websocket

  function connect() {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/subject/${encodeURIComponent(sessionId)}` +
      `?token=${encodeURIComponent(token)}`;
    ws = new WebSocket(url);
    ws.onopen = () => {
      retryCount = 0;
      el.status.textContent = S.status === "running" ? "进行中" : "已连接";
    };
    ws.onmessage = (ev) => {
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleMessage(msg);
    };
    ws.onclose = () => {
      if (S.ended) return;
      retryCount += 1;
      if (retryCount > 8) {
        showLost();
        return;
      }
      el.status.textContent = "连接中断，重连中…";
      clearTimeout(retryTimer);
      retryTimer = setTimeout(connect, 1500);
    };
  }

  // ---------------------------------------------------------------- boot

  function boot() {
    if (!sessionId || !token) {
      showOverlay("无法进入", "缺少会话参数，请从入场页或首页重新进入。", "返回首页");
      el.ovAction.onclick = () => { location.href = "/"; };
      return;
    }
    initChart();
    connect();
  }

  boot();
})();
