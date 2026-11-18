/* 研究者控制台：建会话、看实时状态与指标、导出数据。 */
(function () {
  const W = window.WatchLab;
  const TOKEN_KEY = "watchlab.research.token";

  const el = {
    token: document.getElementById("token"),
    tokenState: document.getElementById("token-state"),
    btnToken: document.getElementById("btn-token"),
    subject: document.getElementById("f-subject"),
    seed: document.getElementById("f-seed"),
    round: document.getElementById("f-round"),
    order: document.getElementById("f-order"),
    duration: document.getElementById("f-duration"),
    tick: document.getElementById("f-tick"),
    engine: document.getElementById("f-engine"),
    btnCreate: document.getElementById("btn-create"),
    btnPair: document.getElementById("btn-pair"),
    createMsg: document.getElementById("create-msg"),
    rows: document.getElementById("session-rows"),
    btnRefresh: document.getElementById("btn-refresh"),
    dTitle: document.getElementById("d-title"),
    dStatus: document.getElementById("d-status"),
    dStart: document.getElementById("d-start"),
    dExport: document.getElementById("d-export"),
    dMeta: document.getElementById("d-meta"),
    mDecisions: document.getElementById("m-decisions"),
    mCalmness: document.getElementById("m-calmness"),
    mFocus: document.getElementById("m-focus"),
    mInterventions: document.getElementById("m-interventions"),
    mResponse: document.getElementById("m-response"),
    dDecisions: document.getElementById("d-decisions"),
    dEvents: document.getElementById("d-events"),
  };

  let token = localStorage.getItem(TOKEN_KEY) || "";
  let sessions = [];
  let selectedId = null;
  let D = null;
  let ws = null;
  let chart = null;
  let ticksSinceTable = 0;
  let wsRetry = null;

  const yes = (v) => v === true || v === 1 || v === "1" || v === "true" || v === "True";
  const pct = (v) => (v == null ? "--" : `${(Number(v) * 100).toFixed(1)}%`);
  const r4 = (v) => Math.round(Number(v) * 10000) / 10000;

  const MODE_LABEL = { experiment: "正式", test: "测试", demo: "演示" };
  const STATUS_LABEL = {
    created: "待开始", running: "进行中", finished: "已结束",
    interrupted: "已中断", error: "异常",
  };
  const STATE_LABEL = {
    stress: "压力", anxiety: "焦虑", fatigue: "疲劳", drowsy: "嗜睡",
    distracted: "分心", flow: "心流", focus: "专注", relaxed: "放松",
    neutral: "平静",
  };
  const stateCn = (s) => STATE_LABEL[s] || s || "";
  const LEVEL_LABEL = { mild: "轻提醒", calm: "安抚提醒", strong: "强提醒" };

  async function api(url, opts = {}) {
    return W.api(url, {
      ...opts,
      headers: { "X-Research-Token": token, ...(opts.headers || {}) },
    });
  }

  // ---------------------------------------------------------------- list

  async function loadSessions() {
    if (!token) return;
    try {
      sessions = await api("/api/research/sessions");
      el.tokenState.textContent = "已连接";
      el.tokenState.className = "tag finished";
      renderRows();
    } catch (err) {
      el.tokenState.textContent = "连接失败";
      el.tokenState.className = "tag error";
      el.rows.innerHTML =
        `<tr><td colspan="8" class="muted">${W.escape(err.message || "加载失败")}</td></tr>`;
      if (err.status === 401) {
        el.rows.innerHTML =
          '<tr><td colspan="8" class="muted">口令无效，请检查 WATCHLAB_RESEARCH_TOKEN。</td></tr>';
      }
    }
  }

  function renderRows() {
    if (!sessions.length) {
      el.rows.innerHTML =
        '<tr><td colspan="8" class="muted">还没有会话。填写受试者编号后创建。</td></tr>';
      return;
    }
    el.rows.innerHTML = sessions.map((s) => {
      const sel = s.id === selectedId ? ' class="sel"' : "";
      const status = STATUS_LABEL[s.status] || s.status;
      const tagCls = s.status === "running" ? "running"
        : s.status === "finished" ? "finished"
        : s.status === "error" ? "error" : "";
      const startBtn = s.status === "created"
        ? `<button class="btn btn-dark btn-sm" data-act="start" data-id="${s.id}">开始</button>`
        : "";
      return `<tr data-id="${s.id}"${sel}>
        <td class="mono"><strong>${W.escape(s.code)}</strong></td>
        <td>${W.escape(s.subject_id)}</td>
        <td>${W.escape(s.round)}</td>
        <td class="muted">${W.escape(s.order || "")}</td>
        <td class="mono">${s.seed}</td>
        <td>${MODE_LABEL[s.mode] || s.mode || "--"}</td>
        <td><span class="tag ${tagCls}">${status}</span></td>
        <td>
          <button class="btn btn-ghost btn-sm" data-act="view" data-id="${s.id}">查看</button>
          ${startBtn}
        </td>
      </tr>`;
    }).join("");
  }

  el.rows.addEventListener("click", (ev) => {
    const btn = ev.target.closest("button[data-act]");
    if (!btn) {
      const tr = ev.target.closest("tr[data-id]");
      if (tr) selectSession(tr.dataset.id);
      return;
    }
    if (btn.dataset.act === "view") selectSession(btn.dataset.id);
    if (btn.dataset.act === "start") startSession(btn.dataset.id);
  });

  // ---------------------------------------------------------------- detail

  function normaliseDetail(raw) {
    return {
      session: raw.session || {},
      prices: (raw.prices || [])
        .map((p) => ({ tick: Number(p.tick), price: Number(p.price) }))
        .sort((a, b) => a.tick - b.tick),
      states: (raw.states || []).map((s) => ({
        tick: Number(s.tick), t: Number(s.t),
        state: s.state, conf: Number(s.conf),
        is_impulsive: yes(s.is_impulsive),
      })),
      events: (raw.events || []).map((e) => ({
        tick: Number(e.tick), event_type: e.event_type,
        popup_text: e.popup_text || e.text || "",
        t_popup: Number(e.t_popup || e.t || 0),
      })),
      decisions: (raw.decisions || []).map((d) => ({
        decision_id: d.decision_id,
        tick: Number(d.tick), direction: d.direction,
        position_ratio: d.position_ratio,
        qty: Number(d.qty || 0), price: Number(d.price),
        state: d.state || d.engine_state || "",
        conf: Number(d.conf != null ? d.conf : d.engine_conf),
        is_impulsive: yes(d.is_impulsive),
        trigger_event: d.trigger_event || "",
      })),
      interventions: (raw.interventions || []).map((i) => ({
        tick: Number(i.tick), t: Number(i.t), state: i.state,
        level: i.level, action: i.action, channel: i.channel,
      })),
      metrics: raw.metrics || null,
    };
  }

  function computeMetrics() {
    if (!D) return null;
    if (D.metrics) return D.metrics;
    const total = D.decisions.length;
    const impulsive = D.decisions.filter((d) => d.is_impulsive).length;
    const calmness = total ? r4(1 - impulsive / total) : null;
    const tickMs = Number(D.session.tick_ms) || 2000;
    const valid = D.states.filter((s) => s.tick * tickMs / 1000 >= 10 && s.state !== "neutral");
    const focus = valid.filter((s) => s.state === "focus" || s.state === "flow").length;
    const focusRatio = valid.length ? r4(focus / valid.length) : null;
    const responded = D.interventions.filter((it) => {
      let segStart = null;
      for (const s of D.states) {
        const inOk = s.t > it.t && s.t <= it.t + 60 && !s.is_impulsive && s.state !== "neutral";
        if (inOk) { if (segStart === null) segStart = s.t; else if (s.t - segStart >= 5) return true; }
        else segStart = null;
      }
      return false;
    }).length;
    return {
      decisions: total, impulsive_decisions: impulsive,
      calmness, focus_ratio: focusRatio,
      interventions: D.interventions.length,
      intervention_response_rate:
        D.interventions.length ? r4(responded / D.interventions.length) : null,
    };
  }

  function renderMetrics() {
    const m = computeMetrics();
    if (!m) {
      ["mDecisions", "mCalmness", "mFocus", "mInterventions", "mResponse"]
        .forEach((k) => { el[k].textContent = "--"; });
      return;
    }
    el.mDecisions.textContent = `${m.decisions}`;
    el.mCalmness.textContent = m.calmness == null ? "--" : pct(m.calmness);
    el.mFocus.textContent = m.focus_ratio == null ? "--" : pct(m.focus_ratio);
    el.mInterventions.textContent = `${m.interventions}`;
    el.mResponse.textContent = m.intervention_response_rate == null
      ? "--" : pct(m.intervention_response_rate);
  }

  function renderMeta() {
    if (!D) return;
    const s = D.session;
    const subjectUrl = s.code ? `${location.origin}/s/${s.code}` : "";
    el.dTitle.textContent = `受试者 ${s.subject_id || "—"} · ${s.round || "—"}`;
    const status = STATUS_LABEL[s.status] || s.status || "—";
    el.dStatus.textContent = status;
    el.dStatus.className = "tag " +
      (s.status === "running" ? "running" : s.status === "finished" ? "finished"
        : s.status === "error" ? "error" : "");
    el.dStart.disabled = s.status !== "created";
    el.dExport.disabled = !s.code || s.status === "created" || Boolean(s.demo);
    el.dMeta.innerHTML =
      `种子 <b>${s.seed}</b>（复现用） · 时长 ${s.duration}s · 每 ${Number(s.tick_ms) / 1000}s 一个点 · ` +
      `引擎 ${W.escape(s.engine_mode || "mock")} · 模式 ${MODE_LABEL[s.mode] || s.mode || "--"} · ` +
      `会话码 <b>${W.escape(s.code || "")}</b> · ` +
      (subjectUrl ? `<a href="${subjectUrl}" target="_blank" style="color:var(--purple-600)">受试者链接</a>` : "");
  }

  function drawChart() {
    if (!chart) return;
    if (!D || !D.prices.length) {
      chart.clear();
      return;
    }
    const x = D.prices.map((p) => p.tick);
    const y = D.prices.map((p) => p.price);
    const spanSec = (Number(D.session.tick_ms) || 2000) / 1000;
    const events = D.events.map((e) => ({ xAxis: e.tick, name: e.event_type }));
    const trades = D.decisions.filter((d) => d.price > 0).map((d) => ({
      coord: [d.tick, d.price],
      value: d.direction === "买入" ? "B" : "S",
      itemStyle: { color: d.is_impulsive ? "#7a4fd0" : (d.direction === "买入" ? "#f04444" : "#00a99b") },
      label: { color: "#fff", fontSize: 10, fontWeight: "bold" },
    }));

    chart.setOption({
      animation: false,
      grid: { left: 58, right: 26, top: 24, bottom: 34 },
      tooltip: {
        trigger: "axis",
        formatter: (params) => {
          const p = params && params[0];
          return p
            ? `${W.clock(Number(p.axisValue) * spanSec)}<br/>价格 ${Number(p.data).toFixed(2)}`
            : "";
        },
      },
      xAxis: {
        type: "category", data: x, boundaryGap: false,
        axisLine: { lineStyle: { color: "#e6e1ef" } }, axisTick: { show: false },
        axisLabel: {
          color: "#6f6883",
          formatter: (v) => W.clock(Number(v) * spanSec),
          hideOverlap: true,
        },
      },
      yAxis: {
        type: "value", scale: true,
        axisLabel: { color: "#6f6883", formatter: (v) => Number(v).toFixed(2) },
        splitLine: { lineStyle: { color: "#f2eff7" } },
      },
      series: [{
        type: "line", data: y, showSymbol: false,
        lineStyle: { color: "#4b2e83", width: 2 }, itemStyle: { color: "#4b2e83" },
        areaStyle: {
          color: new echarts.graphic.LinearGradient(0, 0, 0, 1, [
            { offset: 0, color: "rgba(75,46,131,.22)" },
            { offset: 1, color: "rgba(75,46,131,0)" },
          ]),
        },
        markLine: events.length ? {
          symbol: "none", silent: true,
          lineStyle: { type: "dashed", color: "#d68910", width: 1 },
          label: { formatter: (p) => p.name, color: "#7a5b00", fontSize: 10, position: "insideEndTop" },
          data: events,
        } : undefined,
        markPoint: trades.length ? { symbolSize: 34, data: trades } : undefined,
      }],
    }, true);
  }

  function renderDecisions() {
    if (!D || !D.decisions.length) {
      el.dDecisions.innerHTML = '<tr><td colspan="8" class="muted">暂无决策</td></tr>';
      return;
    }
    const tm = Number(D.session.tick_ms) || 2000;
    el.dDecisions.innerHTML = D.decisions.slice(-40).reverse().map((d) => `
      <tr>
        <td class="mono">${W.clock(d.tick * tm / 1000)}</td>
        <td>${W.escape(d.direction)}</td>
        <td class="mono">${W.escape(String(d.position_ratio || ""))}</td>
        <td class="mono">${W.num(d.price, 2)}</td>
        <td>${W.escape(stateCn(d.state))}</td>
        <td class="mono">${Number.isFinite(d.conf) ? d.conf.toFixed(2) : "--"}</td>
        <td>${d.is_impulsive ? '<span class="tag error">冲动</span>' : "—"}</td>
        <td class="muted">${W.escape(d.trigger_event || "")}</td>
      </tr>`).join("");
  }

  function renderEvents() {
    if (!D) return;
    const tm = Number(D.session.tick_ms) || 2000;
    const rows = [];
    D.events.forEach((e) => rows.push({
      tick: e.tick, kind: e.event_type,
      text: e.popup_text, cls: "tag",
    }));
    D.interventions.forEach((i) => rows.push({
      tick: i.tick, kind: "状态提醒",
      text: `${stateCn(i.state)} · ${LEVEL_LABEL[i.level] || i.level} · ${i.action}`,
      cls: "tag feedback",
    }));
    rows.sort((a, b) => b.tick - a.tick);
    if (!rows.length) {
      el.dEvents.innerHTML = '<tr><td colspan="3" class="muted">暂无事件</td></tr>';
      return;
    }
    el.dEvents.innerHTML = rows.slice(0, 40).map((r) => `
      <tr>
        <td class="mono">${W.clock(r.tick * tm / 1000)}</td>
        <td><span class="${r.cls}">${W.escape(r.kind)}</span></td>
        <td>${W.escape(r.text)}</td>
      </tr>`).join("");
  }

  function renderAll() {
    renderMeta();
    renderMetrics();
    drawChart();
    renderDecisions();
    renderEvents();
  }

  // ---------------------------------------------------------------- select / live

  function closeWs() {
    clearTimeout(wsRetry);
    if (ws) {
      const old = ws;
      ws = null;
      old.onclose = null;
      try { old.close(); } catch (e) { /* ignore */ }
    }
  }

  async function selectSession(id) {
    selectedId = id;
    D = null;
    closeWs();
    renderRows();
    try {
      const raw = await api(`/api/research/sessions/${id}`);
      if (selectedId !== id) return;
      D = normaliseDetail(raw);
      renderAll();
      if (D.session.status === "running" || D.session.status === "created") {
        connectWs(id);
      }
    } catch (err) {
      W.note(err.message || "加载会话失败");
    }
  }

  function connectWs(id) {
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const url = `${proto}://${location.host}/ws/research/${encodeURIComponent(id)}` +
      `?token=${encodeURIComponent(token)}`;
    ws = new WebSocket(url);
    ws.onmessage = (ev) => {
      if (selectedId !== id) return;
      let msg = null;
      try { msg = JSON.parse(ev.data); } catch (e) { return; }
      handleLive(msg);
    };
    ws.onclose = () => {
      if (selectedId !== id || !D) return;
      if (D.session.status === "running") {
        wsRetry = setTimeout(() => connectWs(id), 2000);
      }
    };
  }

  function handleLive(msg) {
    if (!D) return;
    switch (msg.type) {
      case "snapshot":
        D = normaliseDetail(msg);
        ticksSinceTable = 0;
        renderAll();
        break;
      case "tick": {
        const tick = Number(msg.tick);
        if (!D.prices.length || tick > D.prices[D.prices.length - 1].tick) {
          D.prices.push({ tick, price: Number(msg.price) });
        }
        D.states.push({
          tick, t: Number(msg.t), state: msg.state,
          conf: Number(msg.conf), is_impulsive: yes(msg.is_impulsive),
        });
        D.metrics = null;
        renderMetrics();
        drawChart();
        ticksSinceTable += 1;
        if (ticksSinceTable >= 5) {
          ticksSinceTable = 0;
          renderDecisions();
          renderEvents();
          renderMeta();
        }
        break;
      }
      case "event":
        D.events.push({
          tick: Number(msg.tick), event_type: msg.event_type || "事件",
          popup_text: msg.text || msg.popup_text || "",
          t_popup: Number(msg.t_popup || 0),
        });
        renderEvents();
        drawChart();
        break;
      case "trade":
        D.decisions.push({
          decision_id: D.decisions.length + 1,
          tick: Number(msg.tick), direction: msg.direction,
          position_ratio: msg.decision && msg.decision.position_ratio,
          qty: null, price: Number(msg.price),
          state: msg.state, conf: Number(msg.conf),
          is_impulsive: yes(msg.is_impulsive),
          trigger_event: msg.decision && msg.decision.trigger_event,
        });
        D.metrics = null;
        renderMetrics();
        renderDecisions();
        drawChart();
        break;
      case "intervention":
        D.interventions.push({
          tick: Number(msg.tick), t: Number(msg.t),
          state: msg.state, level: msg.level, action: msg.action,
          channel: msg.channel,
        });
        D.metrics = null;
        renderMetrics();
        renderEvents();
        break;
      case "end":
        D.session.status = "finished";
        if (msg.metrics) D.metrics = msg.metrics;
        renderAll();
        loadSessions();
        break;
      case "error":
        W.note(`服务端异常：${msg.message || ""}`);
        break;
      default:
        break;
    }
  }

  el.dStart.addEventListener("click", () => {
    if (selectedId) startSession(selectedId);
  });

  async function startSession(id) {
    try {
      await api(`/api/research/sessions/${id}/start`, { method: "POST" });
      W.note("会话已开始");
      if (selectedId !== id) selectSession(id);
      else if (D) { D.session.status = "running"; renderMeta(); }
      loadSessions();
    } catch (err) {
      W.note(err.message || "启动失败");
    }
  }

  el.dExport.addEventListener("click", () => {
    if (!selectedId || !token) return;
    location.href =
      `/api/research/sessions/${encodeURIComponent(selectedId)}/export` +
      `?token=${encodeURIComponent(token)}`;
  });

  // ---------------------------------------------------------------- create

  function formPayload(round, seed) {
    const engineMode = el.engine.value;
    return {
      subject_id: el.subject.value.trim() || "未命名",
      round,
      order: el.order.value,
      seed: seed != null ? seed : (el.seed.value ? Number(el.seed.value) : null),
      duration: Number(el.duration.value) || 600,
      tick_ms: Number(el.tick.value) || 2000,
      engine_mode: engineMode,
      engine_path: null,
    };
  }

  function showCreated(list) {
    el.createMsg.innerHTML = list.map((r) =>
      `已创建 <b>${W.escape(r.round || "")}</b> 会话码 <b>${W.escape(r.code)}</b>` +
      `，种子 ${r.seed} · <a href="/s/${r.code}" target="_blank" style="color:var(--purple-600)">打开入场页</a>`
    ).join("<br>");
  }

  el.btnCreate.addEventListener("click", async () => {
    el.btnCreate.disabled = true;
    el.btnPair.disabled = true;
    try {
      const payload = formPayload(el.round.value, null);
      const res = await api("/api/research/sessions", {
        method: "POST", body: JSON.stringify(payload),
      });
      showCreated([{ round: payload.round, code: res.code, seed: res.seed }]);
      el.createMsg.innerHTML += `<br><span class="muted">模式：${MODE_LABEL[res.mode] || res.mode}</span>`;
      await loadSessions();
      selectSession(res.session_id);
    } catch (err) {
      el.createMsg.textContent = err.message || "创建失败";
    } finally {
      el.btnCreate.disabled = false;
      el.btnPair.disabled = false;
    }
  });

  el.btnPair.addEventListener("click", async () => {
    el.btnCreate.disabled = true;
    el.btnPair.disabled = true;
    try {
      const firstRound = el.order.value === "先无反馈" ? "无反馈" : "有反馈";
      const secondRound = firstRound === "无反馈" ? "有反馈" : "无反馈";
      const p1 = formPayload(firstRound, null);
      const r1 = await api("/api/research/sessions", {
        method: "POST", body: JSON.stringify(p1),
      });
      const p2 = formPayload(secondRound, r1.seed);
      const r2 = await api("/api/research/sessions", {
        method: "POST", body: JSON.stringify(p2),
      });
      showCreated([
        { round: firstRound, code: r1.code, seed: r1.seed },
        { round: secondRound, code: r2.code, seed: r2.seed },
      ]);
      el.createMsg.innerHTML += `<br><span class="muted">两轮同种子 ${r1.seed}，顺序 ${el.order.value}。</span>`;
      await loadSessions();
      selectSession(r1.session_id);
    } catch (err) {
      el.createMsg.textContent = err.message || "创建失败";
    } finally {
      el.btnCreate.disabled = false;
      el.btnPair.disabled = false;
    }
  });

  // ---------------------------------------------------------------- token

  function saveToken() {
    token = el.token.value.trim();
    if (!token) {
      el.tokenState.textContent = "口令为空";
      el.tokenState.className = "tag error";
      return;
    }
    localStorage.setItem(TOKEN_KEY, token);
    el.tokenState.textContent = "连接中…";
    el.tokenState.className = "tag running";
    loadSessions();
  }

  el.btnToken.addEventListener("click", saveToken);
  el.token.addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") saveToken();
  });
  el.btnRefresh.addEventListener("click", loadSessions);

  // ---------------------------------------------------------------- boot

  function boot() {
    if (window.echarts) {
      chart = echarts.init(document.getElementById("d-chart"));
      window.addEventListener("resize", () => chart && chart.resize());
    }
    el.token.value = token || "research-demo-token";
    if (token) loadSessions();
  }

  boot();
})();
