/* 受试者入场页：校验会话码 → 展示公开元数据 → 知情同意 → 进入交易页 */
(function () {
  const W = window.WatchLab;
  const code = decodeURIComponent(location.pathname.split("/").filter(Boolean).pop() || "");

  const el = {
    subject: document.getElementById("m-subject"),
    round: document.getElementById("m-round"),
    duration: document.getElementById("m-duration"),
    device: document.getElementById("m-device"),
    consent: document.getElementById("consent"),
    enter: document.getElementById("btn-enter"),
    msg: document.getElementById("entry-msg"),
  };

  let meta = null;

  function setMsg(text) {
    el.msg.textContent = text;
  }

  function fill(m) {
    el.subject.textContent = m.subject_id || "—";
    el.round.textContent = `第 ${m.round_index} 轮`;
    el.duration.textContent = `${Math.round(Number(m.duration || 0) / 60)} 分钟`;
    el.device.textContent = "模拟数据源";
    el.duration.title = "单轮时长由研究者设定";
  }

  async function load() {
    try {
      meta = await W.api(`/api/sessions/by-code/${encodeURIComponent(code)}`);
      fill(meta);
      if (meta.status === "finished") {
        setMsg("该会话已结束，请联系主试。");
        el.enter.disabled = true;
        el.consent.disabled = true;
        return;
      }
      setMsg("");
    } catch (err) {
      setMsg(err.message || "会话码无效");
      el.enter.disabled = true;
      el.consent.disabled = true;
      ["m-subject", "m-round", "m-duration", "m-device"].forEach((id) => {
        document.getElementById(id).textContent = "—";
      });
    }
  }

  el.consent.addEventListener("change", () => {
    el.enter.disabled = !el.consent.checked || !meta;
  });

  el.enter.addEventListener("click", async () => {
    el.enter.disabled = true;
    setMsg("正在载入会话…");
    try {
      const res = await W.api("/api/sessions/join", {
        method: "POST",
        body: JSON.stringify({ code }),
      });
      const q = new URLSearchParams({
        session: res.session_id,
        token: res.token,
      });
      location.href = `/trading?${q.toString()}`;
    } catch (err) {
      setMsg(err.message || "进入失败，请通知主试");
      el.enter.disabled = false;
    }
  });

  load();
})();
