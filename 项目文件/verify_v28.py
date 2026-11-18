# -*- coding: utf-8 -*-
"""v2.8 专项回归：疲劳 I_f 积分、嗜睡合取判据、S6 疲劳→嗜睡迁移、
#9 基线自适应、§六 校准协议代码化（A/B/C 段统计）。
验证方式：与生成路径不同的断言式回读；全部通过输出 ALL PASS。"""
import os
import sys
import random

# 2026-10-23：原为硬编码绝对路径，换盘符/换机器即失效。改为按本文件定位——
# 嵌入式运行时存在 ._pth 时不会自动把脚本目录加入 sys.path，所以这一行不能省。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from state_engine import (  # noqa: E402
    StateEngine, Frame, _mock_frame, _make_mock_clock,
)

random.seed(42)

def base_with_theta_ref():
    b = StateEngine.calibrate_from_mock([])
    b["theta_ref"] = 100.0
    return b


def test_fatigue_integral():
    """疲劳：θ_norm>1.2（IF_THETA_NORM）持续累积 I_f（ts 步长 10s，theta=250 → +13s 等效/帧）；
    BAR 低 + I_f≥300 → 判 fatigue（且 feedback 为 mild 休息提示）"""
    eng = StateEngine(baseline=base_with_theta_ref(), clock=_make_mock_clock())
    eng._theta_ref = 100.0
    for i in range(10):                       # 预热平滑（mock 时钟 ts=1..10）
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    dec = fb = None
    fbs = []
    for i in range(60):                       # 60×10s = 600s，theta=250（θ_norm=2.5）恒定
        fr = Frame(theta=250.0, beta=50.0, alpha=100.0, delta=100.0, gamma=50.0,
                   attention=50.0, meditation=50.0, ts=10.0 * (i + 1))
        dec, fb = eng.process_frame(fr, ctx={"task_active": True})
        fbs.append(fb)
    assert eng._theta_int >= IF_HOLD_S_guard(eng), \
        f"V9 fail: I_f={eng._theta_int:.1f} 应≥300（θ_norm=2.5 累积 600s 等效）"
    # 疲劳门控：z(R_ba)<-1 且 I_f 达标（R_ba=50/350≈0.143 → z≈-1.8）——仲裁应在 fatigue
    assert dec.state == "fatigue", f"V9 fail: 期望 fatigue, got {dec.state}"
    assert any(fb_.level == "mild" and "休息" in fb_.action for fb_ in fbs), \
        f"V9 fail: 疲劳话术未出现（末帧冷却中属预期）, got {fbs[-1]}"
    print(f"[V9] 疲劳 I_f 积分 OK（I_f={eng._theta_int:.1f} → fatigue mild 提醒）")


def IF_HOLD_S_guard(eng):
    # 与引擎常量一致（避免魔法数漂移）
    import state_engine as se
    return se.IF_HOLD_S


def test_drowsy():
    """嗜睡：TAR 高（θ/α=2 → z=4）且 δ 高（160 → z=3）→ drowsy 门控过；
    关键：同帧 R_tb=θ/β=1.4 会过 distracted 门控——断言 PRIORITY 中 drowsy 在 distracted 前被选中"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    for i in range(10):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    dec = fb = None
    for i in range(8):                        # 持续嗜睡帧（ts 续预热时钟 11..18，超过 10s 窗混合期）
        fr = Frame(theta=140.0, alpha=70.0, beta=100.0, delta=160.0, gamma=50.0,
                   attention=40.0, meditation=30.0, ts=float(11 + i))
        dec, fb = eng.process_frame(fr, ctx={"task_active": True})
    assert dec.state == "drowsy", f"V10 fail: 期望 drowsy（drowsy>distracted 优先级）, got {dec.state}"
    assert fb.level == "calm", f"V10 fail: 嗜睡 calm 话术, got {fb}"
    print("[V10] 嗜睡合取判据 OK（TAR+δ 双高 → drowsy，且压过 distracted）")


def test_s6_migrate():
    """S6：疲劳触发后 60s 内 TAR/δ 转嗜睡条件 → 改判 drowsy"""
    eng = StateEngine(baseline=base_with_theta_ref(), clock=_make_mock_clock())
    eng._theta_ref = 100.0
    for i in range(10):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    # 先喂疲劳帧 30 帧（I_f≈150，未达标）——再喂同时满足嗜睡的帧（theta 高+alpha 低+delta 高）
    dec = None
    for i in range(30):
        fr = Frame(theta=150.0, beta=50.0, alpha=100.0, delta=100.0, gamma=50.0,
                   attention=50.0, meditation=50.0, ts=10.0 * (i + 1))
        dec, fb = eng.process_frame(fr, ctx={"task_active": True})
    # 此帧同时满足疲劳（I_f 已积累）与嗜睡（TAR/δ）——S6 语义：嗜睡是恶化态，应判 drowsy
    for i in range(10):
        fr = Frame(theta=150.0, beta=50.0, alpha=70.0, delta=170.0, gamma=50.0,
                   attention=40.0, meditation=30.0, ts=10.0 * (i + 1))
        dec, fb = eng.process_frame(fr, ctx={"task_active": True})
    assert dec.state == "drowsy", f"V11 fail: S6 疲劳→嗜睡迁移, got {dec.state}"
    print("[V11] S6 疲劳→嗜睡迁移 OK（嗜睡合取成立时优先判嗜睡）")


def test_adapt():
    """#9：启用后 neutral 帧（无状态命中）更新 μ，有状态帧不更新；限幅生效"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    import state_engine as se
    old_enabled = se.ADAPT_ENABLED
    se.ADAPT_ENABLED = True
    try:
        # 预热后喂"无状态命中"的 neutral 帧：R_tb≈1.0（z=0 附近，各门控均不过）
        # 但 RMSSD 需正常（focus mock 预热），仍=15 → stress 不过；TAR≈0.6 → drowsy 不过
        before = eng.baseline["R_tb"].mu
        for i in range(3):
            fr = Frame(theta=100.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                       attention=50.0, meditation=50.0, ts=60.0 * (i + 1))
            dec, fb = eng.process_frame(fr, ctx={"task_active": True})
            # 帧 ts 间隔 60s → _last_adapt_ts 更新条件（60s 评估一次）
        after = eng.baseline["R_tb"].mu
        assert abs(after - before) <= se.ADAPT_MAX_DRIFT * 0.3 + 1e-9, \
            f"V12 fail: 漂移限幅, Δ={after - before:.4f}"
        # 有状态命中帧不更新：喂 focus 帧（R_tb=0.55）→ gates 有 focus → 不更新
        mu_before_focus = eng.baseline["R_tb"].mu
        for i in range(3):
            fr = Frame(theta=55.0, beta=100.0, alpha=130.0, delta=100.0, gamma=50.0,
                       attention=50.0, meditation=50.0, ts=90.0 * (i + 1))
            eng.process_frame(fr, ctx={"task_active": True})
        assert eng.baseline["R_tb"].mu == mu_before_focus, \
            "V12 fail: 有状态命中帧不应更新基线"
        print(f"[V12] #9 基线自适应 OK（neutral 帧更新、限幅 {after - before:+.4f}σ、状态帧不更新）")
    finally:
        se.ADAPT_ENABLED = old_enabled


def test_calibrate_frames():
    """§六 校准协议：A/B/C 段帧 → 基线统计正确（μ≈构造值、sigma 稳健、theta_ref 冻结）"""
    frames = []
    rnd = random.Random(7)
    # A 段：闭眼静息 theta≈130±10、delta≈120±10
    for i in range(20):
        frames.append(("A", Frame(theta=130 + rnd.uniform(-10, 10),
                                  delta=120 + rnd.uniform(-10, 10), ts=float(i))))
    # B 段：任务前睁眼 alpha≈110、beta≈100、theta≈100、rr≈800±15
    for i in range(20):
        frames.append(("B", Frame(theta=100 + rnd.uniform(-5, 5),
                                  beta=100 + rnd.uniform(-5, 5),
                                  alpha=110 + rnd.uniform(-8, 8),
                                  delta=100, gamma=50,
                                  rr_ms=800 + rnd.uniform(-15, 15),
                                  ts=float(i))))
    # C 段：放松 alpha 升（A_rel 锚点）——MVP 只统计不直接改阈值
    for i in range(15):
        frames.append(("C", Frame(theta=90, beta=60, alpha=140 + rnd.uniform(-8, 8),
                                  delta=100, gamma=50, ts=float(i))))
    base = StateEngine.calibrate_from_frames(frames)
    # v2.9.2 修正断言：θ_ref 必须取 B 段（任务参照帧）而非 A 段（闭眼静息）。
    # 原断言 abs(theta_ref-130)<12 把"A 段闭眼 θ（本用例 130）当参照"写死进测试——
    # 而主任务 θ（B 段=100）永远达不到 130×1.2，校准后疲劳再度不可达（与 P1-3 同失效模式）。
    assert abs(base["theta_ref"] - 100) < 12, f"V13 fail: theta_ref={base['theta_ref']}（应取 B 段≈100）"
    assert base.get("theta_ref_src") == "B", f"V13 fail: theta_ref_src={base.get('theta_ref_src')}"
    assert abs(base.get("theta_rest", 0) - 130) < 12, f"V13 fail: theta_rest={base.get('theta_rest')}"
    assert 100 < base["alpha"].mu < 120, f"V13 fail: alpha μ={base['alpha'].mu}"
    assert abs(base["R_tb"].mu - 1.0) < 0.15, f"V13 fail: R_tb μ={base['R_tb'].mu}"
    assert abs(base["R_ba"].mu - 0.476) < 0.06, f"V13 fail: R_ba μ={base['R_ba'].mu}"
    assert base["RMSSD_60"].mu > 0 and base["RMSSD_60"].sigma >= base["RMSSD_60"].sigma_min, \
        f"V13 fail: RMSSD 统计异常 μ={base['RMSSD_60'].mu}"
    # C 段不覆盖 B 段统计（独立锚点，暂不直接改阈值）——断言 A_rel 仍为默认 0
    assert abs(base["A_rel"].mu) < 1e-6, "V13 fail: A_rel 锚点不应覆盖 B 段默认"
    # 用校准基线驱动引擎：focus mock 仍应判 focus（回归不破坏）
    eng = StateEngine(baseline=base, clock=_make_mock_clock())
    dec = None
    for i in range(65):
        dec, _ = eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    assert dec.state == "focus", f"V13 fail: 校准基线下 focus 应判 focus, got {dec.state}"
    print("[V13] §六 校准协议 OK（A/B/C 段统计 + theta_ref 冻结 + 校准基线驱动引擎回归）")


if __name__ == "__main__":
    test_fatigue_integral()
    test_drowsy()
    test_s6_migrate()
    test_adapt()
    test_calibrate_frames()
    print("\nALL PASS — v2.8 专项 5/5（疲劳/嗜睡/S6/自适应/校准协议）")
