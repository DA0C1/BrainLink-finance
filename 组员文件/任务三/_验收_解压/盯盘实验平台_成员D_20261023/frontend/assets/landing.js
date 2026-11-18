(function () {
  const { api, qs } = window.WatchLab;

  const msg = document.getElementById("join-msg");
  const codeInput = document.getElementById("code");
  const joinBtn = document.getElementById("btn-join");
  const demoBtn = document.getElementById("btn-demo");

  if (codeInput) {
    codeInput.addEventListener("input", () => {
      codeInput.value = codeInput.value.toUpperCase().replace(/[^A-Z0-9]/g, "").slice(0, 6);
    });
    codeInput.addEventListener("keydown", (e) => {
      if (e.key === "Enter") join();
    });
  }

  async function join() {
    const code = (codeInput.value || "").trim().toUpperCase();
    if (code.length !== 6) {
      msg.textContent = "请输入 6 位会话码。";
      return;
    }
    joinBtn.disabled = true;
    msg.textContent = "正在校验会话码…";
    try {
      await api(`/api/sessions/by-code/${encodeURIComponent(code)}`);
      location.href = `/s/${encodeURIComponent(code)}`;
    } catch (err) {
      msg.textContent = err.message;
      joinBtn.disabled = false;
    }
  }

  joinBtn.addEventListener("click", join);

  demoBtn.addEventListener("click", async () => {
    demoBtn.disabled = true;
    demoBtn.textContent = "正在创建演示…";
    try {
      const r = await api("/api/demo", { method: "POST" });
      location.href = `/trading?session=${encodeURIComponent(r.session_id)}&token=${encodeURIComponent(r.token)}&demo=1`;
    } catch (err) {
      msg.textContent = err.message;
      demoBtn.disabled = false;
      demoBtn.textContent = "进入 3 分钟演示";
    }
  });
})();
