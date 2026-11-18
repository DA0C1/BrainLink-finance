# -*- coding: utf-8 -*-
"""verify_v26.py — v2.6 回归验证：覆盖主演示未覆盖的 5 个分支
（弱判别升级 / 同步率锁存复位 / RMSSD 分组差分 / neutral 兜底 / 焦虑 z 判据）

运行：python verify_v26.py
全部断言通过输出 "ALL PASS"；任一失败退出码非 0。
"""
from __future__ import annotations

import math
import random
import sys

sys.path.insert(0, __file__.rsplit("\\", 1)[0])
from state_engine import (  # noqa: E402
    StateEngine, Frame, Feats, BaselineStats,
    WEAK_ESCALATE_FRAMES, FLOW_HOLD_FRAMES,
    _mock_frame, _make_mock_clock,
)


def _mk_clock():
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    return clock


_FRAME_N = [0]   # 确定性 RR 抖动计数器（恒定 RR → RMSSD=0 → 假 stress，机制正确但测试要避开）


def _frame(**kw) -> Frame:
    base = dict(theta=100.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                rr_ms=830.0 + 12.0 * ((_FRAME_N[0] * 7) % 5 - 2),  # 抖动 ∈ {-24,-12,0,12,24}ms
                gyro=(0.01, 0.01, 0.01), attention=50.0, meditation=50.0)
    _FRAME_N[0] += 1
    base.update(kw)
    return Frame(**base)


def test_neutral():
    """V1 窗口未满（1 帧）→ neutral，而非 relaxed（v2.6 修复：无判别不伪装放松）"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_mk_clock())
    dec, fb = eng.process_frame(_frame(), ctx={"task_active": True})
    assert dec.state == "neutral", f"V1 fail: {dec.state}"
    assert "数据不足" in fb.action, f"V1 fail: {fb.action}"
    print("[V1] neutral 兜底 OK（窗口未满 1 帧 -> neutral, 不伪装 relaxed）")


def test_weak_escalate():
    """V2 弱判别持续 >=60 帧触发 mild 升级（v2.6；直接测 feedback_policy 分支，
    含首次升级无需等 300s 的 bug 修复；neutral 不走升级由 V1 文案断言覆盖）"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_mk_clock())
    escalated = None
    for i in range(WEAK_ESCALATE_FRAMES + 5):
        fb = eng.feedback_policy("stress", conf=0.65)   # 弱判别 stress（conf<0.7）
        if fb.level == "mild" and "趋近" in fb.action:
            escalated = i
            break
    assert escalated is not None and escalated < WEAK_ESCALATE_FRAMES + 2, \
        f"V2 fail: 未在 60 帧内升级 (i={escalated})"
    # 冷却验证：升级后 60 帧内再次连续弱判别不立即升级（冷却 300s 未到）
    for i in range(WEAK_ESCALATE_FRAMES):
        fb = eng.feedback_policy("stress", conf=0.65)
        assert not (fb.level == "mild" and "趋近" in fb.action), "V2 fail: 冷却期内不应再升级"
    print(f"[V2] 弱判别升级 OK（第 {escalated + 1} 帧触发 mild 升级；冷却期内不重复升级）")


def test_sync_latch_reset():
    """V3 同步率锁存：flow 稳定期保持；双高跌破后锁存复位、flow 掉出"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_mk_clock())
    # 预热 focus 填窗
    for _ in range(65):
        eng.process_frame(_frame(theta=55.0, attention=65, meditation=55), ctx={"task_active": True})
    # flow：协同上升 15 帧（触发 sync_ok -> latched）后稳定高值 30 帧（无趋势，锁存保 flow）
    for i in range(15):
        eng.process_frame(_frame(theta=70.0, attention=72.0 + i, meditation=68.0 + i),
                          ctx={"task_active": False})
    assert eng.sync_latched, "V3 fail: 协同上升后 sync 未锁存"
    for _ in range(30):   # 稳定期：att/med 恒定 86/82（差分归零），锁存应保 flow
        dec, _ = eng.process_frame(_frame(theta=70.0, attention=86.0, meditation=82.0),
                                   ctx={"task_active": False})
        assert dec.state == "flow", f"V3 fail: 稳定期掉出 flow -> {dec.state}"
    # 双高跌破：att/med <60 持续 -> flow_hold 归 0 -> 锁存复位
    for i in range(10):
        dec, _ = eng.process_frame(_frame(theta=70.0, attention=50.0, meditation=48.0),
                                   ctx={"task_active": False})
        if i >= FLOW_HOLD_FRAMES:
            assert not eng.sync_latched, "V3 fail: 双高跌破后锁存未复位"
    print("[V3] 同步率锁存 OK（稳定期保 flow；双高跌破后锁存复位、flow 掉出）")


def test_rmssd_batch():
    """V4 RMSSD 分组差分：同批内差分、跨批断开（v2.6 修复跨回调污染）"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    # 同批 3 间期：810/830/850（差分 20/20）
    eng.push_rr(810.0, batch=1); eng.push_rr(830.0, batch=1); eng.push_rr(850.0, batch=1)
    # 第二批（跨批边界 850->810 不应差分）
    eng.push_rr(810.0, batch=2); eng.push_rr(800.0, batch=2); eng.push_rr(790.0, batch=2)
    eng.push_rr(810.0, batch=3); eng.push_rr(800.0, batch=3); eng.push_rr(790.0, batch=3)
    r = eng._rmssd(eng.rr_buf)
    # 有效差分：同批内 2+2+2=6 对（20,20 / -10,-10 / -10,-10）
    expect = math.sqrt((400 + 400 + 100 + 100 + 100 + 100) / 6.0)
    assert abs(r - expect) < 1e-9, f"V4 fail: RMSSD={r} 期望 {expect}"
    print(f"[V4] RMSSD 分组差分 OK（跨批 850->810 未差分，RMSSD={r:.2f} ms 与同批内计算一致）")


def test_anxiety_z():
    """V5 焦虑 z 判据：绝对不低（>=40）但相对个体基线显著下滑（z<-1.5）→ 触发焦虑"""
    base = StateEngine.calibrate_from_mock([])
    base["att"] = BaselineStats(mu=80.0, sigma=10.0, sigma_min=3.0)  # 该个体指数常年在 80
    base["med"] = BaselineStats(mu=80.0, sigma=10.0, sigma_min=3.0)
    eng = StateEngine(baseline=base, clock=_mk_clock())
    for _ in range(65):
        eng.process_frame(_frame(theta=100.0, attention=78.0, meditation=76.0), ctx={"task_active": True})
    # 绝对路径不触发（62>=40），z=(62-80)/10=-1.8 < -1.5 触发 z 路径
    dec, fb = eng.process_frame(_frame(theta=130.0, attention=62.0, meditation=62.0),
                                ctx={"task_active": False})
    assert dec.state == "anxiety", f"V5 fail: {dec.state}（应走 z 判据触发焦虑）"
    assert fb.level == "calm", f"V5 fail: fb={fb.level}"
    print("[V5] 焦虑 z 判据 OK（绝对 62 不触发，z=-1.8 触发焦虑 + calm 干预）")


def test_aux_missing_release():
    """V6 辅助特征缺失放行（v2.6.2）：still/RMSSD 缺失（nan）时
    分心/放松主判据不被污染——score 不再因 nan 项整体变 -inf"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    # 分心：R_tb 强高（z=+2.0）+ still 缺失 → 应判 distracted（不因 nan 漏报）
    fA = Feats(R_tb=1.6, R_ba=0.5, A_rel=0.1, HR=78, RMSSD_60=6.0, dRMSSD=0, dHR=0, still=float("nan"))
    scores, gates = eng._scores(fA, {"task_active": True}, Frame(theta=160, beta=100, alpha=100,
                                                                 attention=50, meditation=50))
    assert gates["distracted"] and scores["distracted"] != float("-inf"), \
        f"V6 fail: 分心 still 缺失漏报 (gate={gates['distracted']}, score={scores['distracted']})"
    # 放松：A_rel 高（z=+1.5）+ RMSSD 缺失（HRV 窗未满）→ 应判 relaxed（不因 nan 漏报）
    fB = Feats(R_tb=1.0, R_ba=0.5, A_rel=0.5, HR=62, RMSSD_60=float("nan"), dRMSSD=0, dHR=0, still=0.1)
    scores, gates = eng._scores(fB, {"task_active": False}, Frame(theta=100, beta=100, alpha=150,
                                                                  attention=50, meditation=50))
    assert gates["relaxed"] and scores["relaxed"] != float("-inf"), \
        f"V6 fail: 放松 RMSSD 缺失漏报 (gate={gates['relaxed']}, score={scores['relaxed']})"
    print("[V6] 辅助特征缺失放行 OK（分心/放松主判据不受 still/RMSSD nan 污染）")


def test_time_confirm():
    """V7 时间确认（v2.7：#8 落地）：单帧抖动不翻转状态；持续状态窗口占比达标后确认"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    for _ in range(65):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    # 冷启动末帧 A_rel 边界抖动可能单帧 relaxed 未确认 → 补 2 帧 focus 稳定（时间确认正确行为）
    for _ in range(2):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    assert eng.last_decision.state == "focus", f"V7 fail: 预热稳定后应 focus, got {eng.last_decision.state}"
    # 单帧 distracted 抖动 → 时间确认拦截（占比 <0.6 → neutral）
    dec, _ = eng.process_frame(_mock_frame("distracted"), ctx={"task_active": True})
    assert dec.state != "distracted", f"V7 fail: 单帧抖动不应翻转, got {dec.state}"
    # 持续 distracted 帧 → 超过 10s 窗混合期（ts 滑动下 eeg_buf 需 ~11 帧才全为新状态，
    # 中间帧 R_tb 落在 focus/distracted 门控空档判 neutral 属设计特性）→ 必确认
    dec = None
    for _ in range(15):
        dec, _ = eng.process_frame(_mock_frame("distracted"), ctx={"task_active": True})
    assert dec.state == "distracted", f"V7 fail: 持续分心(15帧>混合期)应确认, got {dec.state}"
    print("[V7] 时间确认 OK（单帧抖动不翻转；持续状态超过混合期后窗口占比达标确认）")


def test_relaxed_still_weight():
    """V8 relaxed 评分补 still 权重项（v2.7.1）：体动活跃（still z 高）时放松置信度应扣减——
    权重表（§四）明示 +0.5·z(−still) 但评分公式漏掉（与 v2.6.2 distracted A_rel 同类）"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    fr = Frame(theta=100, beta=100, alpha=190, attention=50, meditation=50)
    # A_rel 强高（z≈3.0，>Z_RELAX_A=1.5 门控过）+ RMSSD 正常 + 体动高 vs 体动低
    fA = Feats(R_tb=1.0, R_ba=0.5, A_rel=0.9, HR=62, RMSSD_60=20.0, dRMSSD=0, dHR=0, still=1.5)
    fB = Feats(R_tb=1.0, R_ba=0.5, A_rel=0.9, HR=62, RMSSD_60=20.0, dRMSSD=0, dHR=0, still=0.0)
    sA, gA = eng._scores(fA, {}, fr)
    sB, gB = eng._scores(fB, {}, fr)
    assert gA["relaxed"] and gB["relaxed"], "V8 fail: relaxed 门控应过"
    assert math.isfinite(sA["relaxed"]) and math.isfinite(sB["relaxed"]), "V8 fail: score 应有限"
    assert sA["relaxed"] < sB["relaxed"], \
        f"V8 fail: 体动高应扣减放松置信度 (still高={sA['relaxed']:.3f} vs still低={sB['relaxed']:.3f})"
    print(f"[V8] relaxed still 权重项 OK（体动高 score={sA['relaxed']:.3f} < 体动低 {sB['relaxed']:.3f}）")


if __name__ == "__main__":
    random.seed(42)   # 回归确定性：_mock_frame 内部用全局 random，不固定种子则预热序列随机漂移
    test_neutral()
    test_weak_escalate()
    test_sync_latch_reset()
    test_rmssd_batch()
    test_anxiety_z()
    test_aux_missing_release()
    test_time_confirm()
    test_relaxed_still_weight()
    print("\nALL PASS — v2.6 回归验证 8/8 通过（含 v2.7 时间确认 + v2.7.1 权重补项）")
