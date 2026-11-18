# -*- coding: utf-8 -*-
"""mockdata.py — 演示用合成数据（从 state_engine 拆出，v3.16）

分离理由：生产引擎不应背着"演示相位表/随机帧生成器"。live_bridge 的 mock 源、
output_demo 与 state_engine 的 __main__ 都从这里取，保持**唯一事实源**。

注意：本模块延迟导入 state_engine（函数内），避免 `python state_engine.py` 时
`__main__` 与 `state_engine` 两份模块互相导入、产生两套 Frame 类。
"""
from __future__ import annotations

import random


# v2.8.6：相位表提为模块级单一事实源。
# 起因：live_bridge.source_mock 自己另写了一份 8 态列表（含 fatigue/drowsy），
# 而 _mock_frame 的 base 只有 6 态 → 轮到第 7 个相位时 base[phase] 抛 KeyError，
# **帧源线程直接死掉**，但 HTTP 服务仍返回 200 —— 表现为"面板还在、数据不再刷新"的静默失败。
# 两个列表各自维护必然漂移，故导出 MOCK_PHASES 供 live_bridge 直接引用。
# 元组含义：(θ系数, α系数, 心率, RR 抖动ms)；β/δ 默认 1.0 / 100，少数相位用下表覆盖
_MOCK_PHASE_SPEC: dict[str, tuple[float, float, int, int]] = {
    "focus":      (0.55, 1.3, 75, 30),
    "distracted": (1.60, 1.0, 78, 30),
    "relaxed":    (1.00, 1.6, 62, 50),
    "stress":     (0.90, 1.1, 88, 10),
    "anxiety":    (1.30, 1.0, 84, 30),   # θ/β 偏高（非专注）+ 心率偏高 + 双指数低；
                                         # jitter=30（HRV 正常）区分于 stress（jitter=10，HRV 骤降）
    "flow":       (0.70, 1.4, 66, 42),   # 接近专注 + 放松（心流=专注+放松协同，指南 2.2）
    # v2.8.6 补全 8 态：这两项原缺失，是上面的崩溃根因
    "fatigue":    (1.50, 1.10, 68, 32),  # θ↑ 使 θ_norm>1 → I_f 可持续累积；
                                         # β 由下表降到 0.30 → R_ba≈0.27，满足 z(R_ba)<-1.0
    "drowsy":     (1.90, 1.10, 60, 40),  # TAR=θ/α≈1.7 满足 z(TAR)>+1.5；
                                         # δ 由下表抬到 220 满足 z(δ)>+1.0（合取口径 S5）
}
_MOCK_ATT_MED: dict[str, tuple[int, int]] = {
    "focus": (65, 55), "distracted": (45, 45), "relaxed": (50, 70),
    "stress": (40, 30), "anxiety": (28, 26), "flow": (72, 68),
    "fatigue": (38, 52),   # 警觉度下降但未入睡
    "drowsy": (25, 40),    # 接近睡眠（attention 常模下限）
}
_MOCK_BETA: dict[str, float] = {"fatigue": 0.30, "drowsy": 0.35}   # 缺省 1.0
_MOCK_DELTA: dict[str, float] = {"drowsy": 220.0}                  # 缺省 100

MOCK_PHASES: tuple[str, ...] = tuple(_MOCK_PHASE_SPEC)   # 唯一相位清单，外部请引用这个


def _mock_frame(phase: str, t: float | None = None) -> Frame:
    """按阶段生成脑波/心率帧（仅验证管道与格式，不验证判别准确性）
    rr_jitter: 相邻 RR 抖动幅度（ms），越大 HRV 越高——relaxed 大（RMSSD↑）、stress 小（RMSSD↓）
    attention/meditation 按阶段常模（指南 2.1/2.2 区间）：anxiety 双低值、flow 双高值

    t：相位内进度 0→1，**仅 flow 使用**。心流判据含"派生同步率"（双指数趋势一致性），
    静态值在窗口内差分为 0 → sync_ok=False → flow 被误杀（详见 `_index_high` 注释与指南 2.2）。
    故 flow 需注入单调同向上升。t=None 表示不注入趋势（历史调用的默认行为，保持不变）。

    v3.4：补 signal=30（良好接触档）——引擎新增接触门后，mock 帧必须显式给出"已佩戴"，
    否则演示会误走 "signal 未知" 分支（虽不拦截，但覆盖不到新逻辑）。
    """
    from state_engine import Frame, RR_MIN_MS, RR_MAX_MS
    rnd = random.random
    if phase not in _MOCK_PHASE_SPEC:
        raise ValueError(f"未知 mock 相位 {phase!r}；可用：{', '.join(MOCK_PHASES)}")
    tb, al, hr, jitter = _MOCK_PHASE_SPEC[phase]
    rr = 60000.0 / hr + jitter * (rnd() - 0.5)
    be = _MOCK_BETA.get(phase, 1.0)
    dl = _MOCK_DELTA.get(phase, 100.0)
    att, med = _MOCK_ATT_MED[phase]
    if phase == "flow" and t is not None:
        # 与 output_demo 原内联公式等价（72+0.25i 截顶 88 / 68+0.25i 截顶 84，i 走满 65 帧即 t=1）
        tt = max(0.0, min(1.0, t))
        att = min(88.0, 72.0 + 16.0 * tt)
        med = min(84.0, 68.0 + 16.0 * tt)
    return Frame(
        theta=100 * (tb + 0.2 * (rnd() - 0.5)),
        beta=100 * (be + 0.2 * (rnd() - 0.5)),
        alpha=100 * (al + 0.2 * (rnd() - 0.5)),
        delta=dl, gamma=50,
        rr_ms=max(RR_MIN_MS, min(RR_MAX_MS, rr)),
        gyro=(0.02 * rnd(), 0.02 * rnd(), 0.02 * rnd()),
        attention=att, meditation=med, signal=30,
    )


def _make_mock_clock():
    """虚拟时钟：每帧推进 1s（v2.3——真实 time.time 在毫秒级 mock 下冷却永不解除，
    distracted/stress 反馈永远显示"冷却中"，演示价值受损）"""
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    return clock


def main():
    # 延迟导入：见模块头
    from state_engine import StateEngine
    random.seed(7)
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    print("=== state_engine.py v3.16 管道演示（mock 数据，验证管道与格式）===")
    for _ in range(65):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    for ph in MOCK_PHASES:
        task = ph in ("focus", "distracted")
        for i in range(1, 66):
            fr = _mock_frame(ph, t=(i / 65.0) if ph == "flow" else None)
            eng.process_frame(fr, ctx={"task_active": task})
            if i % 20 == 0 or i == 65:
                dec, fb = eng.last_decision, eng.last_feedback
                mg = dec.margins.get(dec.state, float("nan"))
                print(f"phase={ph:10s} t={i:3d}s -> state={dec.state:10s} conf={dec.conf:.2f} "
                      f"margin={mg:+.2f}σ fb=[{fb.level}: {fb.action}]")
    eng.rationality_log(True, eng.last_decision)
    eng.rationality_log(False, eng.last_decision)
    cl = eng.calmness
    print("\n理智度示例: calmness=" + ("—（无决策样本）" if not __import__("math").isfinite(cl) else f"{cl:.2f}"))


if __name__ == "__main__":
    main()
