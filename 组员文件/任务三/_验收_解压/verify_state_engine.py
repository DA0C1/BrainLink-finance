# 任务3 真实引擎模式实测：engine_mode=state_engine + 引擎路径
import json, time, httpx, os

BASE = "http://127.0.0.1:8010"
TOKEN = "research-demo-token"
# 2026-10-23：原为硬编码绝对路径，改为按本文件相对定位（_验收_解压 → 项目文件）
ENGINE = os.path.abspath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "项目文件", "state_engine.py"))
H = {"X-Research-Token": TOKEN}

# 建会话（真实引擎 + 短测：30s / 500ms）
r = httpx.post(f"{BASE}/api/research/sessions", headers=H, json={
    "subject_id": "组长验收E", "round": "有反馈", "order": "先无反馈",
    "duration": 60, "tick_ms": 500,
    "engine_mode": "state_engine", "engine_path": ENGINE})
r.raise_for_status()
sid = r.json()["session_id"]
code = r.json()["code"]
print("会话:", sid, "mode:", r.json()["mode"])

# 启动
s = httpx.post(f"{BASE}/api/research/sessions/{sid}/start", headers=H)
print("启动:", s.json()["status"])

# 受试者接入（拿 subject token）
join = httpx.post(f"{BASE}/api/sessions/join", json={"code": code}).json()
stok = join["token"]
print("受试者已接入，token 掩码:", stok[:6] + "...")

# 运行中触发 2 笔交易（验证决策时刻引擎状态快照）
time.sleep(6)
for dirn, ratio in (("买入", 0.25), ("卖出", 0.25)):
    try:
        t = httpx.post(f"{BASE}/api/sessions/{sid}/trades", params={"token": stok},
                       json={"direction": dirn, "ratio": ratio})
        print("交易:", dirn, t.status_code, t.json().get("ok", t.text[:80]))
    except Exception as e:
        print("交易异常:", e)
    time.sleep(2)

# 轮询至 finished
for _ in range(60):
    time.sleep(1)
    d = httpx.get(f"{BASE}/api/research/sessions/{sid}", headers=H).json()
    if d["session"]["status"] == "finished":
        break
st = d["session"]["status"]
print("状态:", st)

decs = d["decisions"] or []
print("决策数:", len(decs))
if decs:
    sample = decs[0]
    keys = ("t", "direction", "position_ratio", "engine_state", "engine_conf", "is_impulsive", "signal_quality")
    print("首笔:", {k: sample.get(k) for k in keys})
    states = [x.get("engine_state") for x in decs]
    confs = [x.get("engine_conf") for x in decs]
    print("状态分布:", {s: states.count(s) for s in set(states)})
    print("conf 范围: %.2f ~ %.2f" % (min(confs), max(confs)))
    print("非空 conf 占比: %.0f%%" % (100 * sum(1 for c in confs if c is not None) / len(confs)))
intv = d["interventions"] or []
print("干预记录数:", len(intv))
if intv:
    print("干预样例:", {k: intv[0].get(k) for k in ("t", "state", "level", "action", "channel")})
