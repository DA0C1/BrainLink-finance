/* 公共工具：无框架、无构建步骤。 */
(function () {
  const W = window.WatchLab = window.WatchLab || {};

  W.qs = (name) => new URLSearchParams(location.search).get(name);

  W.api = async function (url, options = {}) {
    const res = await fetch(url, {
      ...options,
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    });
    let data = null;
    try { data = await res.json(); } catch (e) { /* 空响应 */ }
    if (!res.ok) {
      const detail = (data && (data.detail || data.message)) || `请求失败（${res.status}）`;
      const err = new Error(detail);
      err.status = res.status;
      throw err;
    }
    return data;
  };

  W.money = (v) => {
    const n = Number(v || 0);
    return n.toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  };

  W.num = (v, d = 2) => {
    const n = Number(v || 0);
    return n.toLocaleString("zh-CN", { minimumFractionDigits: d, maximumFractionDigits: d });
  };

  W.clock = (seconds) => {
    const s = Math.max(0, Math.floor(Number(seconds || 0)));
    return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
  };

  W.hhmmss = (epoch) => {
    if (!epoch) return "--:--:--";
    const d = new Date(Number(epoch) * 1000);
    return d.toLocaleTimeString("zh-CN", { hour12: false });
  };

  W.escape = (s) => String(s == null ? "" : s)
    .replaceAll("&", "&amp;").replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;").replaceAll('"', "&quot;");

  W.note = function (text, ms = 3200) {
    const el = document.createElement("div");
    el.className = "toast-note";
    el.textContent = text;
    document.body.appendChild(el);
    setTimeout(() => el.remove(), ms);
  };

  W.speak = function (text) {
    if (!("speechSynthesis" in window)) return;
    try {
      window.speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance(text);
      u.lang = "zh-CN";
      u.rate = 1.0;
      window.speechSynthesis.speak(u);
    } catch (e) { /* 浏览器不支持语音时静默降级 */ }
  };
})();
