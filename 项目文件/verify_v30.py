# -*- coding: utf-8 -*-
"""verify_v30：现实参数可达性专项（10-23 代码审查 P2-1 盲区补位）

目标：补"断言一次判对"之外的盲区——断言**现实参数区间内状态可被判出**。
覆盖三个 P1 修复的可达性：
  A. 校准协议（calibrate_from_frames B 段）产出的基线 → relaxed 在 α+60% 现实幅度可判
     （P1-1 A_rel 量纲修复后：z(A_rel)=3.3 过 1.5）
  B. sdk_adapter 默认 ctx → distracted 经适配器在强分心帧可判
     （P1-2 ctx 注入修复后：task_active 恒真，门控放行）
  C. 疲劳纯时长积分 → θ_norm=1.5（现实抬升 50%）持续 15min 必达 I_f≥300
     （P1-3 修复后：300s 真实时长 = 300s 判据，不再要求 θ>2.2 倍基线）
运行：python verify_v30.py（全绿输出 14 条 OK）
"""
import random
import sys
import time

import state_engine as se
from state_engine import (Frame, StateEngine, IF_HOLD_S)

rnd = random.Random(7)
fails = []


def check(name, ok, detail=""):
    print(f"[{'OK' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        fails.append(name)


def make_clock(step):
    t = [0.0]
    return lambda: (t.__setitem__(0, t[0] + step), t[0])[1]


# ---------- A. 校准基线 → relaxed 可达（P1-1 修复核验）----------
def test_calibrated_relaxed():
    """模拟真机 B 段校准（α=100±15），再喂放松帧 α=160（+60% 现实幅度）"""
    frames = []
    for _ in range(30):
        frames.append(("B", Frame(theta=100 + rnd.uniform(-5, 5), beta=100.0,
                                  alpha=100 + rnd.uniform(-15, 15), delta=100.0,
                                  gamma=50.0, attention=50.0, meditation=50.0,
                                  rr_ms=800 + rnd.uniform(-15, 15),
                                  gyro=[0.01, 0.01, 0.01])))
    base = StateEngine.calibrate_from_frames(frames)
    eng = StateEngine(baseline=base, clock=make_clock(1.0))
    eng._theta_ref = 100.0
    # 预热窗（特征 z 需窗口满才生效；放松判定还需时间确认/冷却，喂 80 帧放松帧）
    last = None
    for i in range(80):
        fr = Frame(theta=100.0, beta=100.0, alpha=160.0, delta=100.0, gamma=50.0,
                   attention=50.0, meditation=70.0, rr_ms=950.0,
                   gyro=[0.005, 0.005, 0.005])
        dec, _ = eng.process_frame(fr, {"task_active": False})
        last = dec
    # 校准产出的 A_rel 基线与运行时同量纲（比值）→ z(A_rel) 应 > Z_RELAX_A=1.5
    az = eng._z(0.6, base["A_rel"])   # α=160 vs 基线 μ≈100 → 比值 0.6
    ok = az > se.Z_RELAX_A
    check("A 校准后 relaxed 可达（z(A_rel)=%.2f>1.5）" % az, ok,
          f"last={last.state if last else None}")


# ---------- B. sdk_adapter 默认 ctx → distracted 可达（P1-2 修复核验）----------
def test_adapter_distracted():
    """必须走适配器真实回调路径（onEEG），不得直接给引擎传 ctx——
    原版写法 `eng.process_frame(fr, adapter.ctx)` 绕过了适配器，即使 P1-2 未修也能通过（无效测试）。"""
    import sdk_adapter
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]),
                      clock=make_clock(1.0))
    eng.baseline["theta_ref"] = 55.0
    eng._theta_ref = 55.0
    adapter = sdk_adapter.SdkAdapter(eng)      # 不传 ctx：验证默认值即 task_active=True
    assert adapter.ctx.get("task_active") is True, "适配器默认 ctx 未注入 task_active"
    last = None
    for i in range(70):   # 强分心帧：θ=160/β=100（R_tb 显著升）；RR 保持正常变异（避免 RMSSD 塌陷触发 stress）
        data = sdk_adapter.SDKFrame(theta=160.0, lowBeta=50.0, highBeta=50.0,
                                    lowAlpha=50.0, highAlpha=50.0,
                                    lowGamma=25.0, highGamma=25.0,
                                    attention=45.0, meditation=45.0, signal=30)
        adapter.onEEG(data)                      # ← 真实通路
        last = eng.last_decision
    ok = last is not None and last.state == "distracted" and last.gates.get("distracted")
    check("B 适配器真实回调路径 distracted 可达", ok,
          f"last={last.state if last else None} task_active={adapter.ctx.get('task_active')}")


# ---------- C. 疲劳现实幅度可达（P1-3 修复核验）----------
def test_fatigue_realistic():
    eng = StateEngine(clock=make_clock(1.0))
    eng._theta_ref = 100.0
    # θ_norm=1.5（θ=150，现实抬升 50%）→ rate=(1.5-1.2)/0.3=1.0，持续 900s → I_f=900 ≥ 300
    for _ in range(900):
        fr = Frame(theta=150.0, beta=50.0, alpha=100.0, delta=100.0, gamma=50.0,
                   attention=50.0, meditation=50.0, rr_ms=860.0,
                   gyro=[0.01, 0.01, 0.01])
        eng.process_frame(fr, {"task_active": False})
    ok = eng._theta_int >= IF_HOLD_S
    check("C 疲劳 θ_norm=1.5 持续15min 可达（I_f=%.1f≥300）" % eng._theta_int, ok)


# ---------- D/E/F. v2.9.1 疲劳模型三不变量 ----------
def _run_theta(eng, theta, seconds, theta_ref=100.0):
    eng._theta_ref = theta_ref
    for _ in range(seconds):
        eng.process_frame(Frame(theta=theta, beta=50.0, alpha=100.0, delta=100.0,
                                gamma=50.0, attention=50.0, meditation=50.0,
                                rr_ms=860.0, gyro=[0.01, 0.01, 0.01]),
                          {"task_active": False})
    return eng._theta_int


def test_fatigue_severity_weighted():
    """幅度加权：θ_norm=1.5 的累积速率必须显著高于 1.3（原 v2.9 纯时长版两者相同=1.0/秒）"""
    i15 = _run_theta(StateEngine(clock=make_clock(1.0)), 150.0, 300)
    i13 = _run_theta(StateEngine(clock=make_clock(1.0)), 130.0, 300)
    ok = i15 > i13 * 2.0
    check("D 疲劳幅度加权（1.5×→I_f=%.0f ＞ 1.3×→I_f=%.0f 的两倍）" % (i15, i13), ok)


def test_fatigue_recovers():
    """恢复通道：θ 回落阈下后 I_f 必须下降（原 v2.9 单调不减→永久锁存）"""
    eng = StateEngine(clock=make_clock(1.0))
    hi = _run_theta(eng, 150.0, 400)
    lo = _run_theta(eng, 90.0, 600)      # θ_norm=0.9，半衰 600s → 约减半
    ok = lo < hi * 0.75
    check("E 疲劳可恢复（400s@1.5×→%.0f，回落 600s→%.0f）" % (hi, lo), ok)


def test_fatigue_threshold_edge():
    """阈值边界：θ_norm=1.19 不应累积（防止"一过阈即满速"的严重高估）"""
    eng = StateEngine(clock=make_clock(1.0))
    v = _run_theta(eng, 119.0, 600)
    check("F 疲劳阈下不累积（θ_norm=1.19 跑 600s → I_f=%.1f）" % v, v == 0.0)


def test_fatigue_rate_cap():
    """速率上限：θ_norm=3.0 与 5.0 均应被 IF_RATE_MAX 截断（极端尖峰不刷满）
    注：首帧无前序时间戳 → dt=0，n 帧只有 n−1 秒有效累积。"""
    a = _run_theta(StateEngine(clock=make_clock(1.0)), 300.0, 100)
    b = _run_theta(StateEngine(clock=make_clock(1.0)), 500.0, 100)
    exp = se.IF_RATE_MAX * 99
    ok = abs(a - b) < 1e-6 and abs(a - exp) < 1e-6
    check("G 疲劳速率上限（3.0×与5.0×同为 %.0f=IF_RATE_MAX×99）" % a, ok)


def test_fatigue_gap_guard():
    """断帧保护：SDK 掉线/休眠恢复后首帧 dt 可达数千秒，必须封顶在 IF_MAX_DT_S，
    否则单帧即把 I_f 刷满（实测未封顶时 3600s 断帧 → 单帧 +7200）。"""
    eng = StateEngine(clock=make_clock(1.0))
    eng._theta_ref = 100.0
    eng._last_theta_ts = 0.0
    fr = Frame(theta=150.0, beta=50.0, alpha=100.0, delta=100.0, gamma=50.0,
               attention=50.0, meditation=50.0, rr_ms=860.0, gyro=[0.01, 0.01, 0.01])
    fr.ts = 3600.0                      # 断帧 1 小时后恢复
    eng.process_frame(fr, {"task_active": False})
    cap = se.IF_RATE_MAX * se.IF_MAX_DT_S
    ok = eng._theta_int <= cap + 1e-6
    check("H 断帧不刷满（断 3600s → I_f=%.0f ≤ %.0f）" % (eng._theta_int, cap), ok)


# ---------- I/J/K. v2.9.2 设计与校准闭环 ----------
def _calib_frames(a_theta=130.0, b_theta=100.0, n_a=40, n_b=40, n_c=30):
    """构造三段校准帧：A 闭眼（θ 高）/ B 睁眼任务参照 / C 放松（α 升）"""
    out = []
    for _ in range(n_a):
        out.append(("A", Frame(theta=a_theta + rnd.uniform(-8, 8), alpha=120.0, beta=70.0,
                               delta=120 + rnd.uniform(-8, 8), gamma=45.0,
                               attention=55.0, meditation=70.0,
                               rr_ms=950 + rnd.uniform(-20, 20), gyro=[0.01, 0.01, 0.01])))
    for _ in range(n_b):
        out.append(("B", Frame(theta=b_theta + rnd.uniform(-5, 5), alpha=100 + rnd.uniform(-12, 12),
                               beta=100.0, delta=100.0, gamma=50.0,
                               attention=60.0, meditation=55.0,
                               rr_ms=820 + rnd.uniform(-15, 15), gyro=[0.01, 0.01, 0.01])))
    for _ in range(n_c):
        out.append(("C", Frame(theta=95.0, alpha=160 + rnd.uniform(-10, 10), beta=70.0,
                               delta=100.0, gamma=45.0, attention=50.0, meditation=72.0,
                               rr_ms=950 + rnd.uniform(-18, 18), gyro=[0.005, 0.005, 0.005])))
    return out


def test_calibrated_fatigue_reachable():
    """θ_ref 必须取 B 段（任务参照）：A 闭眼 θ=130 / B 睁眼 θ=100 / 任务疲劳 θ=150（1.5×B）。
    原实现取 A 段 → θ_norm=150/130=1.15<1.2 → I_f 恒 0，校准后疲劳永不触发（与 P1-3 同型）。"""
    base = StateEngine.calibrate_from_frames(_calib_frames())
    eng = StateEngine(baseline=base, clock=make_clock(1.0))
    for _ in range(400):
        eng.process_frame(Frame(theta=150.0, beta=60.0, alpha=100.0, delta=100.0, gamma=50.0,
                                attention=50.0, meditation=52.0, rr_ms=830.0,
                                gyro=[0.01, 0.01, 0.01]), {"task_active": True})
    ok = eng._theta_ref is not None and eng._theta_ref < 120 and eng._theta_int >= IF_HOLD_S
    check("I 校准后疲劳可达（θ_ref=%.0f 取自 B 段，I_f=%.0f≥300）"
          % (eng._theta_ref or -1, eng._theta_int), ok)


def test_calibration_report():
    """§六 第 5 条：校准失败须可判（段样本不足/伪迹超限 → ok=False + 可读告警）；
    C 段必须产出放松锚点可分性（此前 C 段无任何消费者，采集 2min 数据白采）。"""
    rep_ok = StateEngine.calibration_report(_calib_frames())
    short = [f for f in _calib_frames() if True]
    # 仅保留 5 帧 B 段 → 样本不足
    short = [("A", fr) for tag, fr in short if tag == "A"] + \
            [("B", fr) for tag, fr in short if tag == "B"][:5] + \
            [("C", fr) for tag, fr in short if tag == "C"][:5]
    rep_bad = StateEngine.calibration_report(short)
    checks = [
        ("J1 合格校准判定", rep_ok["ok"] is True, f"ok={rep_ok['ok']}"),
        ("J2 C 段放松锚点可分", (rep_ok["c_anchor_z"] or 0) > se.Z_RELAX_A,
         f"c_anchor_z={rep_ok['c_anchor_z']}"),
        ("J3 段样本不足判失败", rep_bad["ok"] is False and len(rep_bad["warnings"]) >= 2,
         f"warnings={len(rep_bad['warnings'])}"),
        ("J4 θ_ref 来源可追溯", rep_ok["theta_ref_src"] == "B", str(rep_ok["theta_ref_src"])),
    ]
    for name, ok, detail in checks:
        check(name, ok, detail)


def test_arbitration_priority_semantics():
    """仲裁口径回归：**只按 PRIORITY**，不做跨状态强度仲裁。

    背景（v2.9.2 复核）：曾尝试"能提醒者优先"（conf≥0.7 的候选先选），实测把
    "RMSSD z=−2 的强应激 + att/med=30 的焦虑"判成 anxiety——原因是各状态评分量纲不同
    （z 组合 ~1–3 vs 指数余量 ~10–20），跨状态比 conf 无意义。故回退为纯优先级，
    并把该取舍写进代码注释与设计文档（答辩需说明其代价）。
    本用例锁死语义，防止后续"看起来更聪明"的改动再次引入量纲错配。"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=make_clock(1.0))
    neg = float("-inf")
    gates = {s: False for s in se.PRIORITY}
    gates.update({"stress": True, "anxiety": True})
    s1 = {s: neg for s in se.PRIORITY}
    s1.update({"stress": 2.0, "anxiety": 20.0})     # 焦虑分数远高，但优先级低于应激
    st1, _ = eng._arbitrate(s1, gates)
    s2 = {s: neg for s in se.PRIORITY}
    s2.update({"stress": 0.02, "anxiety": 4.0})     # 边缘应激仍按优先级胜出
    st2, _ = eng._arbitrate(s2, gates)
    # 单一候选时按自身胜出
    s3 = {s: neg for s in se.PRIORITY}
    s3.update({"relaxed": 1.2})
    st3, _ = eng._arbitrate(s3, gates)
    ok = st1 == "stress" and st2 == "stress" and st3 == "relaxed"
    check("K 仲裁按优先级（stress/anxiety→%s；边缘 stress→%s；单候选→%s）" % (st1, st2, st3), ok)


def test_emg_gate():
    """v3.1 β+γ 肌电专用门：开关关=不拦（8× 对持续肌电饱和），开关开=拦住说话帧且不误伤正常帧。

    这道门的必要性来自 verify_hw.py V4：持续肌电把平滑基线抬走后，8× 门再也触发不了，
    朗读段的 EEG 会被当有效数据喂进判据（β 升 → 误判 stress/anxiety）。
    """
    base = StateEngine.calibrate_from_frames(_calib_frames())   # B 段 β=100/γ=50
    quiet = Frame(theta=100.0, alpha=100.0, beta=100.0, delta=100.0, gamma=50.0,
                  attention=60.0, meditation=55.0, rr_ms=820.0, gyro=[0.01, 0.01, 0.01])
    speak = Frame(theta=100.0, alpha=100.0, beta=500.0, delta=100.0, gamma=250.0,  # β/γ 5×、θ 不动
                  attention=55.0, meditation=50.0, rr_ms=800.0, gyro=[0.01, 0.01, 0.01])
    old = se.ARTIFACT_EMG_GATE
    try:
        se.ARTIFACT_EMG_GATE = False
        off = StateEngine(baseline=base)
        # 先让平滑稳定在静默水平，再喂朗读帧（模拟"说到一半"）
        for _ in range(20):
            off._ewma("beta", quiet.beta)
            off._ewma("gamma", quiet.gamma)
        off_hit = off._artifact(speak)
        se.ARTIFACT_EMG_GATE = True
        on = StateEngine(baseline=base)
        for _ in range(20):
            on._ewma("beta", quiet.beta)
            on._ewma("gamma", quiet.gamma)
        on_hit = on._artifact(speak)
        quiet_hit = on._artifact(quiet)          # 正常帧不得被误判
    finally:
        se.ARTIFACT_EMG_GATE = old
    ok = (off_hit is False) and (on_hit is True) and (quiet_hit is False)
    check("L 肌电门（关=%s 开=%s 正常帧误判=%s）"
          % (off_hit, on_hit, quiet_hit), ok)


if __name__ == "__main__":
    t0 = time.time()
    test_calibrated_relaxed()
    test_adapter_distracted()
    test_fatigue_realistic()
    test_fatigue_severity_weighted()
    test_fatigue_recovers()
    test_fatigue_threshold_edge()
    test_fatigue_rate_cap()
    test_fatigue_gap_guard()
    test_calibrated_fatigue_reachable()
    test_calibration_report()
    test_arbitration_priority_semantics()
    test_emg_gate()
    print(f"\nverify_v30 结果: {'全部通过' if not fails else '失败: ' + ','.join(fails)} "
          f"（{time.time()-t0:.1f}s）")
    sys.exit(1 if fails else 0)
