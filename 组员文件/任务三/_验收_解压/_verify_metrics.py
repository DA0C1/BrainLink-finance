# 离线验证 metrics 新口径（专注占比扣 neutral + 响应率 ≥5s 防游戏化）
import os
import sys

# 2026-10-23：原为硬编码绝对路径（换盘符/换机器即失效），改为按本文件相对定位
_PLAT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "盯盘实验平台_成员D_20261023")
sys.path.insert(0, _PLAT)
from backend.session import SessionRuntime
from backend.storage import SessionStore

# 用 demo 会话构造 runtime（不落盘），直接注入 state_rows/interventions 后调 metrics()
rt = SessionRuntime.__new__(SessionRuntime)
rt.tick_ms = 2000
rt.decisions = []
rt.state_rows = []
rt.interventions = []

def set_case(states, its):
    rt.state_rows = [{"t": t, "tick": i, "state": s,
                      "is_impulsive": s in ("stress","anxiety","drowsy","fatigue","distracted")}
                     for i, (t, s) in enumerate(states)]
    rt.interventions = [{"t": t} for t in its]

# 场景A：干预 t=10 后窗口内 focus 连续 6s（t=12..22）→ 应计 1 响应
set_case([(i, "focus") for i in range(0, 40)], [10.0])
m = rt.metrics()
print("A 连续6s改善:", m["intervention_response_rate"], "专注占比:", m["focus_ratio"])

# 场景B：干预 t=10 后只有 2s 非冲动（t=12,14 两帧 focus 各2s）→ 不应计响应（旧逻辑误计）
set_case([(i, "neutral") for i in range(0, 12)] + [(12.0, "focus"), (14.0, "neutral"),
         (16.0, "focus"), (18.0, "neutral")] + [(i, "neutral") for i in range(20, 80)], [10.0])
m = rt.metrics()
print("B 断续2s:", m["intervention_response_rate"])

# 场景C：干预 t=10 后窗口内 neutral 连续 10s（neutral 非冲动但不算改善）→ 不应计响应
set_case([(i, "neutral") for i in range(0, 60)], [10.0])
m = rt.metrics()
print("C neutral连续10s:", m["intervention_response_rate"])

# 场景D：干预 t=10 后 distracted 2s 后 focus 持续 6s → 应计 1（排除 impulsive 帧后连续段 6s）
set_case([(i, "neutral") for i in range(0, 12)] + [(12.0, "distracted"), (14.0, "focus"),
         (16.0, "focus"), (18.0, "focus"), (20.0, "focus"), (22.0, "focus"),
         (24.0, "focus")] + [(i, "neutral") for i in range(26, 80)], [10.0])
m = rt.metrics()
print("D distracted后focus6s:", m["intervention_response_rate"])

# 场景E：专注占比口径——warmup 后 40 帧：focus 10 + flow 5 + neutral 15 + stress 10 → 分母扣 neutral = 25 帧，分子=15
set_case([(i, "neutral") for i in range(0, 5)] + [(5+i, "focus") for i in range(10)] +
         [(15+i, "flow") for i in range(5)] + [(20+i, "neutral") for i in range(15)] +
         [(35+i, "stress") for i in range(10)], [])
m = rt.metrics()
print("E 专注占比(应0.6000):", m["focus_ratio"])
