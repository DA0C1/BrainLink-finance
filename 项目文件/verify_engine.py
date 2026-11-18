# -*- coding: utf-8 -*-
"""verify_engine.py — state_engine v3.4 算法层自检（16 项，含 5 条反例）

与既有 verify_* 的分工：
  verify_v26/28/29/30  历史行为的回归（接口、阈值、优先级语义"不许变"）
  verify_parity        双引擎口径闸门（常量表 + 轨迹逐行一致）
  **verify_engine**    v3.4 新算法自身的正确性 + **能抓住旧实现缺陷的反例**

每条都用"同一份数据两条路径"或"正例/反例"成对断言——单侧断言只能证明"能跑"，
证明不了"改对了"（本项目历史上踩过三次：断言了有数据没断言算得出来）。

用法：
  python verify_engine.py          # 全跑，失败 exit 1
  python verify_engine.py -v       # 打印中间量
"""
from __future__ import annotations

import math
import random
import sys

import state_engine as se
from state_engine import (ArtifactDetector, BaselineStats, Decision, Frame,
                          ScoreMap, StateEngine)

fails: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    print(f"[{'OK  ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)
    return ok


def base() -> dict:
    return StateEngine.calibrate_from_mock([])



def feed(eng: StateEngine, n: int, ts0: float = 0.0, dt: float = 1.0,
         task: bool = True, **kw) -> Decision:
    """按固定节拍灌 n 帧；kw 覆盖 Frame 字段。返回最后一帧决策。"""
    dec = Decision("neutral", se.CONF_MIN)
    for i in range(n):
        fr = Frame(ts=ts0 + i * dt, **kw)
        dec, _ = eng.process_frame(fr, {"task_active": task})
    return dec


# ---------------------------------------------------------------- E1 接触质量门
def e1_contact_gate():
    """signal=200（protocol：电极未接触皮肤）的帧不得进入特征窗。
    反例：同一批帧 signal=30（良好）必须能被正常吸收——证明不是"什么都拦"。"""
    kw = dict(theta=55.0, beta=100.0, alpha=130.0, delta=100.0, gamma=50.0,
              attention=65.0, meditation=55.0)
    bad = StateEngine(baseline=base())
    dec = feed(bad, 12, signal=200, **kw)
    good = StateEngine(baseline=base())
    feed(good, 12, signal=30, **kw)
    ok = (len(bad.eeg_buf) == 0 and bad.diag["contact"] == 12
          and dec.reason == "contact" and len(good.eeg_buf) > 0)
    check("E1 接触质量门（signal≥200 不入窗 / signal=30 正常）", ok,
          f"坏帧入窗 {len(bad.eeg_buf)} 帧、reason={dec.reason!r}；好帧入窗 {len(good.eeg_buf)} 帧")


# ---------------------------------------------------------------- E2 伪迹规则单一实现
def e2_single_artifact_rule():
    """引擎 `_artifact` 与 ArtifactDetector.push 必须逐帧同判（v3.4 前是两份手写实现）。"""
    rnd = random.Random(9)
    eng = StateEngine(baseline=base())
    det = ArtifactDetector(base())
    flags_eng, flags_det = [], []
    for i in range(60):
        th = 100.0 + rnd.uniform(-8, 8)
        if i == 30:
            th = 2000.0                      # 单帧突变 20× ref → 伪迹（v3.6 门限 15×，9× 已不触发）
        fr = Frame(theta=th, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                   attention=60.0, meditation=55.0, ts=float(i))
        flags_eng.append(eng._artifact(fr))
        if not flags_eng[-1]:                # 伪迹帧不推进平滑（与 process_frame 一致）
            for k in se.BANDS:
                eng._ewma(k, getattr(fr, k))
            eng._ewma("R_tb", eng._safe_ratio(fr.theta, fr.beta))
        flags_det.append(det.push(fr) is not None)
    ratio_static = StateEngine._artifact_ratio_static([("B", fr) for fr in
                                                       [Frame(theta=100.0, beta=100.0)] * 5])
    ok = flags_eng == flags_det and sum(flags_eng) == 1 and ratio_static == 0.0
    check("E2 伪迹规则单一实现（引擎/校准逐帧一致）", ok,
          f"引擎拦截 {sum(flags_eng)} 帧、检测器拦截 {sum(flags_det)} 帧、逐帧一致={flags_eng == flags_det}")


# ---------------------------------------------------------------- E3 δ 时间尺度
def e3_delta_window():
    """δ 的 z 用 10s 窗均值：单帧 δ 冒 +40% 不得让 z(δ) 跳到 2σ。
    反例：同样 +40%，若用瞬时值则 z=+2.00（旧行为）。"""
    eng = StateEngine(baseline=base())
    feed(eng, 10, delta=100.0, theta=100.0, alpha=100.0, beta=100.0, gamma=50.0)
    eng.process_frame(Frame(delta=140.0, theta=100.0, alpha=100.0, beta=100.0,
                            gamma=50.0, ts=11.0), {"task_active": True})
    f = eng._extract()
    st = base()["delta"]
    z_win = (f.delta - st.mu) / max(st.sigma, st.sigma_min)
    z_inst = (140.0 - st.mu) / max(st.sigma, st.sigma_min)
    ok = math.isfinite(f.delta) and z_win < 0.5 < 1.0 < z_inst
    check("E3 δ 走窗均值（单帧尖峰不翻转嗜睡门控）", ok,
          f"δ̄={f.delta:.1f} → z={z_win:+.2f}；瞬时值 z={z_inst:+.2f}（旧行为）")


# ---------------------------------------------------------------- E4 疲劳积分整流偏置
def e4_if_rectifier_bias():
    """θ 均值恰压阈值时，噪声不得把 I_f 顶上去（Jensen 偏置）。
    正例：无噪声 → I_f 必须严格 0；反例：瞬时值方案实测 0.0995/s。"""
    def run(noise: float) -> float:
        rnd = random.Random(11)
        eng = StateEngine(baseline=base(), clock=lambda: 0.0)
        eng._theta_ref = 100.0
        for i in range(1800):
            th = 120.0 + (rnd.gauss(0, noise) if noise else 0.0)
            eng.process_frame(Frame(theta=th, beta=100.0, alpha=100.0, delta=100.0,
                                    gamma=50.0, attention=50.0, meditation=50.0,
                                    ts=float(i)), {"task_active": True})
        return eng._theta_int

    i0, i1 = run(0.0), run(12.0)
    rate = i1 / 1800
    ok = i0 == 0.0 and rate < 0.05 and i1 < se.IF_HOLD_S
    check("E4 疲劳积分整流偏置受控（平滑 θ）", ok,
          f"无噪声 I_f={i0:.1f}；σ=12 时 I_f={i1:.1f}（{rate:+.4f}/s，旧实现 0.0995/s）")


# ---------------------------------------------------------------- E5 I_f 断帧与恢复
def e5_if_guard_recover():
    """① 断帧 3600s 只按 IF_MAX_DT_S 计（防"一帧刷满"）；② θ 回落后 I_f 半衰可恢复。"""
    eng = StateEngine(baseline=base(), clock=lambda: 0.0)
    eng._theta_ref = 100.0
    # ① 断帧保护：θ=250（norm=2.5）从 t=0 直接跳到 t=3600
    eng.process_frame(Frame(theta=250.0, ts=0.0), {"task_active": True})
    eng.process_frame(Frame(theta=250.0, ts=3600.0), {"task_active": True})
    jump = eng._theta_int
    ok1 = jump <= se.IF_RATE_MAX * se.IF_MAX_DT_S + 1e-9
    # ② 半衰：先让平滑 θ 收敛到阈下（EWMA 有滞后，直接喂会让前几帧仍在累积），
    #    再把 I_f 置回已知值，喂 600s 观察是否减半（dt 上限 60s/帧）
    for i in range(20):
        eng.process_frame(Frame(theta=60.0, ts=3600.0 + i + 1), {"task_active": True})
    eng._theta_int = 120.0
    eng._last_theta_ts = 3620.0
    before = eng._theta_int
    for i in range(10):
        eng.process_frame(Frame(theta=60.0, ts=3620.0 + 60.0 * (i + 1)), {"task_active": True})
    after = eng._theta_int
    half = before * 0.5
    ok2 = abs(after - half) < 0.02 * max(before, 1e-9)
    check("E5 I_f 断帧保护 + 600s 半衰恢复", ok1 and ok2,
          f"断帧 3600s 仅累积 {jump:.0f}s（上限 {se.IF_RATE_MAX * se.IF_MAX_DT_S:.0f}）；"
          f"回落 600s 后 {before:.1f}→{after:.1f}（半衰期望 {half:.1f}）")


# ---------------------------------------------------------------- E6 证据同量纲
def e6_evidence_units():
    """两个状态证据相同、但原始分数差 100 倍 → 置信度必须相同。
    反例：旧"分数差比"路径在同一组分数上给出不同结论（0.60 vs 0.95）。"""
    eng = StateEngine(baseline=base())
    gates = {s: False for s in se.PRIORITY}
    gates.update({"stress": True, "anxiety": True})

    def conf_of(s_stress: float, s_anx: float, e_stress: float, e_anx: float) -> tuple[str, float]:
        sc = ScoreMap({s: float("-inf") for s in se.PRIORITY})
        sc.update({"stress": s_stress, "anxiety": s_anx})
        sc.evidence = {"stress": e_stress, "anxiety": e_anx}
        return eng._arbitrate(sc, gates)

    st1, c1 = conf_of(2.0, 200.0, 0.5, 0.5)      # 量纲差 100 倍、证据相同
    st2, c2 = conf_of(2.0, 2.0, 0.5, 0.5)
    st3, c3 = conf_of(2.0, 200.0, 0.9, 0.1)      # 证据一边倒
    # 旧路径（普通 dict，无证据表）作为反例：**同一结构、仅把量纲反转**——
    # 焦虑分数 200 时 conf 贴底，应激分数 200 时 conf 贴顶。这就是"比不同单位"的直接后果。
    plain = {s: float("-inf") for s in se.PRIORITY}
    plain.update({"stress": 2.0, "anxiety": 200.0})
    _, c_old_low = eng._arbitrate(plain, gates)
    plain2 = {s: float("-inf") for s in se.PRIORITY}
    plain2.update({"stress": 200.0, "anxiety": 2.0})
    _, c_old_high = eng._arbitrate(plain2, gates)
    ok = (abs(c1 - c2) < 1e-12 and st1 == st2 == "stress" and c3 > c1
          and abs(c_old_high - c_old_low) > 0.3)
    check("E6 证据同量纲（分数差 100 倍不影响 conf）", ok,
          f"证据 0.5/0.5 → conf {c1:.3f}（分数 2 vs 200）== {c2:.3f}（分数 2 vs 2）；"
          f"证据 0.9/0.1 → {c3:.3f}；旧路径同结构反转量纲 {c_old_low:.3f} vs {c_old_high:.3f}")


# ---------------------------------------------------------------- E7 置信度边界
def e7_conf_bounds():
    """置信度是两个维度的乘积：**排他性** sep × **绝对强度** strength。

    这里锁死 v3.4 漏掉 strength 时的两个极端（都会让 WEAK_CONF 失效）：
      ① 单候选时 sep 恒 1 → 超阈 0.001σ 也拿 CONF_MAX（实测 0.950）；
      ② 有对手但双方证据都极弱时，比的是"谁略强"，同样给出 0.79 这类"中等偏高"。
    v3.4.1 加 strength = clamp(m_best/EVID_FULL) 后：
      单候选·余量 0.001σ → 贴近 CONF_MIN（弱判别，仅记录）；
      单候选·余量 ≥EVID_FULL → CONF_MAX（够强且独占）；
      双候选按证据比打折。结果恒在 [CONF_MIN, CONF_MAX]。
    """
    eng = StateEngine(baseline=base())
    neg = float("-inf")

    def one(m_best):
        sc = ScoreMap({s: neg for s in se.PRIORITY})
        sc["stress"] = 2.0
        sc.margins = {"stress": m_best}
        sc.evidence = {"stress": min(1.0, m_best / se.EVID_REF)}
        return eng._arbitrate(sc, {})[1]

    c_tiny, c_full, c_mid = one(0.001), one(se.EVID_FULL), one(se.EVID_FULL / 2)

    two = ScoreMap({s: neg for s in se.PRIORITY})
    two.update({"stress": 2.0, "anxiety": 5.0})
    two.margins = {"stress": se.EVID_FULL, "anxiety": se.EVID_FULL}
    two.evidence = {"stress": 0.6, "anxiety": 0.3}
    c_two = eng._arbitrate(two, {})[1]
    expect_two = se.CONF_MIN + (se.CONF_MAX - se.CONF_MIN) * (0.6 / 0.9) * 1.0

    in_range = all(se.CONF_MIN <= c <= se.CONF_MAX for c in (c_tiny, c_mid, c_full, c_two))
    ok = (c_tiny < se.WEAK_CONF and c_full >= se.CONF_MAX - 1e-9
          and c_tiny < c_mid < c_full             # 单调：强度越高 conf 越高
          and abs(c_two - expect_two) < 1e-9 and in_range)

    # 端到端：真实帧驱动，只让 relaxed 过门控，用 α 扫出"刚过线 → 典型命中"的阶梯。
    # 这一层是必须的——单元层手工塞 margins 能过，不代表主链路（_extract → _margins）算对了。
    def e2e(alpha):
        eng = StateEngine(baseline=base())
        dec = fb = None
        for i in range(70):
            fr = Frame(theta=100, beta=100, alpha=float(alpha), delta=100, gamma=50,
                       attention=50, meditation=50, ts=i * 1.3 + 1)
            dec, fb = eng.process_frame(fr, {"task_active": False})
        return dec, fb

    d_weak, f_weak = e2e(146)      # A_rel 余量 ≈0.03σ（刚过 1.5σ 判据）
    d_full, f_full = e2e(200)      # ≈1.8σ（典型命中）
    e2e_ok = (d_weak.state == "relaxed" and d_weak.conf < se.WEAK_CONF
              and d_full.state == "relaxed" and d_full.conf >= se.CONF_MAX - 1e-9)
    ok = ok and e2e_ok
    check("E7 置信度 = 排他性 × 绝对强度（弱证据不给满置信度）", ok,
          f"单候选 余量 0.001σ→{c_tiny:.3f}（旧实现 0.950）、0.25σ→{c_mid:.3f}、"
          f"0.5σ→{c_full:.3f}；双候选→{c_two:.3f}（期望 {expect_two:.3f}）；"
          f"端到端 α=146 余量 0.03σ→{d_weak.conf:.3f}（弱判别）、α=200 →{d_full.conf:.3f}")


# ---------------------------------------------------------------- E8 时间确认按时长
def e8_confirm_by_time():
    """"60% 命中"应按**时间**占比，而不是帧数占比。
    正例（1s/帧）与正例（0.1s 快帧突发）确认耗时必须**同为** DEBOUNCE_S×HIT_RATIO；
    反例：按帧数算，快帧突发下 9 帧（=0.9s）就已达 60%，会提前 2s 翻转状态。
    注：直接测确认层（_confirm），不走完整管道——特征窗有 10s 滞后，会把这一层的差异掩盖掉。"""
    def scenario(dt_new: float):
        eng = StateEngine(baseline=base(), clock=lambda: 0.0)
        t = 0.0
        for _ in range(5):                       # 先 5 帧旧状态（1s/帧）
            t += 1.0
            eng._confirm("focus", t)
        switch, k = t, 0
        while t < switch + 10.0:
            t += dt_new
            k += 1
            if eng._confirm("distracted", t) == "distracted":
                return t - switch, k
        return float("inf"), k

    el_slow, k_slow = scenario(1.0)
    el_fast, k_fast = scenario(0.1)
    want = se.DEBOUNCE_S * se.HIT_RATIO
    ok = (abs(el_slow - want) <= 0.6 and abs(el_fast - want) <= 0.6 and k_fast > 20)
    check("E8 时间确认按**时长**加权（快帧突发不提前确认）", ok,
          f"1s/帧 → {el_slow:.1f}s（{k_slow} 帧）；0.1s/帧 → {el_fast:.1f}s（{k_fast} 帧）；"
          f"期望同为 {want:.1f}s。按帧数则 0.9s/9 帧就已达标")


# ---------------------------------------------------------------- E9 校准 δ 取 B 段
def e9_calib_delta_from_b():
    """δ 基线必须取 B 段（任务参照）。A 段（闭眼）δ 系统性偏高，作判据参照会让 z(δ) 恒负。
    反例：旧实现取 A 段 → z(δ)=−3.42，嗜睡永久不可判。"""
    rnd = random.Random(5)
    frames = []
    for _ in range(30):
        frames.append(("A", Frame(theta=130 + rnd.uniform(-6, 6), alpha=120, beta=80,
                                  delta=150 + rnd.uniform(-8, 8), ts=0.0)))
    for _ in range(30):
        frames.append(("B", Frame(theta=100 + rnd.uniform(-6, 6), alpha=100, beta=100,
                                  delta=100 + rnd.uniform(-8, 8), ts=0.0)))
    b = StateEngine.calibrate_from_frames(frames)
    st = b["delta"]
    z_task = (118.0 - st.mu) / max(st.sigma, st.sigma_min)
    z_old = (118.0 - 149.7) / 9.3          # 旧实现（A 段 μ/σ）实测值
    ok = (abs(st.mu - 100) < 8 and abs(b.get("delta_rest", 0) - 150) < 10
          and b.get("delta_src") == "B" and z_task > se.Z_DROWSY_DELTA)
    check("E9 校准 δ 基线取 B 段（嗜睡佐证可判）", ok,
          f"δ_ref={st.mu:.1f}（A 段参考 delta_rest={b.get('delta_rest'):.1f}）；"
          f"任务中 δ=118 → z={z_task:+.2f}（旧实现 {z_old:+.2f}）")


# ---------------------------------------------------------------- E10 校准/运行同口径
def e10_calib_rmssd_same_path():
    """同一段整批 RR：校准产出的基线必须**逐位等于**运行时实算（P3-3 闭合）。
    反例：旧实现只看 rr_ms，整批录制下 diffs 为空 → 静默保留默认 15.0ms（偏差 −89%）。"""
    rnd = random.Random(3)
    batches = []
    for i in range(20):
        seed = 800 + 6 * math.sin(i / 3.0)
        bs = [seed + rnd.uniform(-12, 12) for _ in range(3)]
        if i == 10:
            bs = [seed, 1420.0, seed + 4]           # 掉搏（异位滤波应剔除）
        batches.append([round(v, 1) for v in bs])

    eng = StateEngine(baseline=base(), clock=lambda: 0.0)
    for i, bs in enumerate(batches):
        for v in bs:
            eng.push_rr(v, batch=i, ts=0.0)
    rt = StateEngine._rmssd(eng.rr_buf)

    frames = []
    for i, bs in enumerate(batches):
        fr = Frame(theta=100.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0, ts=float(i))
        fr.rr_batch = tuple(bs)
        frames.append(("B", fr))
    b = StateEngine.calibrate_from_frames(frames)
    cal = b["RMSSD_60"].mu
    pairs = (b.get("calib_meta") or {}).get("rmssd_pairs")
    ok = math.isfinite(rt) and abs(cal - rt) < 1e-9 and rt < 30   # 掉搏被滤掉，不应是 100+
    check("E10 校准与运行时 RMSSD 同口径（逐位相等）", ok,
          f"运行 {rt:.4f}ms == 校准 {cal:.4f}ms（差分对 {pairs}）；旧实现此处为默认 15.0000ms")


# ---------------------------------------------------------------- E11 校准 A_rel 显式依赖
def e11_calib_a_rel():
    """A_rel 必须以 **B 段 α 中位数**为分母（显式两段，不靠特征名顺序）。
    断言 A_rel.mu≡0（数学恒等）且 C 段放松锚点 z 显著为正。"""
    rnd = random.Random(7)
    frames = []
    for _ in range(20):
        frames.append(("B", Frame(theta=100, beta=100, alpha=110 + rnd.uniform(-8, 8),
                                  delta=100, ts=0.0)))
    for _ in range(20):
        frames.append(("C", Frame(theta=90, beta=60, alpha=145 + rnd.uniform(-8, 8),
                                  delta=100, ts=0.0)))
    for _ in range(20):
        frames.append(("A", Frame(theta=130, beta=80, alpha=120, delta=150, ts=0.0)))
    b = StateEngine.calibrate_from_frames(frames)
    rep = StateEngine.calibration_report(frames, b)
    z_c = rep["c_anchor_z"]
    ok = abs(b["A_rel"].mu) < 1e-9 and abs(b["alpha"].mu - 110) < 6 \
        and z_c is not None and z_c > se.Z_RELAX_A
    check("E11 校准 A_rel 显式依赖 B 段 α（放松锚点可分）", ok,
          f"alpha μ={b['alpha'].mu:.1f}、A_rel μ={b['A_rel'].mu:+.1e}、C 段 z={z_c:+.2f}")


# ---------------------------------------------------------------- E12 log 空间
def e12_log_space():
    """log_flag 基线的 μ/σ 必须在 log 空间估（否则 _z 取对数后与线性 μ 相减是量纲错配）。"""
    stat = BaselineStats(sigma=1.0, sigma_min=0.01, log_flag=True)
    vals = [10.0, 20.0, 40.0, 80.0, 160.0]        # 奇数个 → 样本中位数唯一=40
    mu, sig = StateEngine._robust_stats(vals, stat)
    z_med = (math.log(40.0) - mu) / sig
    z_2x = (math.log(80.0) - mu) / sig
    # 乘性翻倍 → z 平移 ln2/σ（log 空间的可检验性质，线性空间不成立）
    ok = abs(mu - math.log(40.0)) < 1e-9 and abs((z_2x - z_med) - math.log(2.0) / sig) < 1e-9
    lin = BaselineStats(sigma=1.0, sigma_min=0.01, log_flag=False)
    mu_lin, _ = StateEngine._robust_stats(vals, lin)
    check("E12 log_flag 校准在 log 空间（乘性缩放 → z 平移可预测）", ok,
          f"μ_log={mu:.4f}=ln40；翻倍后 Δz={z_2x - z_med:.4f}=ln2/σ；"
          f"线性空间 μ={mu_lin:.1f}（旧实现只会这个）")


# ---------------------------------------------------------------- E13 校准剔除伪迹帧
def e13_calib_artifact_filter():
    """校准统计前须剔除伪迹帧。用高频眨眼段（30 帧里 12 帧 α=20×ref，占 40%）构造：
    中位数/IQR 对**单点**尖峰本就稳健（这一点不夸大），但污染率超过 25% 时四分位点会落到
    伪迹堆里 → σ 被抬一个量级（586）→ z(A_rel) 被压扁 → 放松判据失效。剔除后 σ 回到 5，
    同时校准报告须**报出**"伪迹率超限"这条具体告警（而不是笼统的 ok=False——A/C 段缺失也会
    让 ok=False，那样的断言抓不住伪迹率本身）。

    反例（旧实现的真实行为，20 个噪声种子实测）：伪迹率 0%、σ=586，且检出率随参照噪声在
    0–12/12 之间抖动——参照系被眨眼抬走 + 门限与物理幅度同量级（8× vs 8.2 倍）双重失守。"""
    rnd = random.Random(13)
    frames = [("B", Frame(theta=100, beta=100, alpha=110 + rnd.uniform(-6, 6),
                          delta=100, ts=float(i))) for i in range(30)]
    for i in (3, 5, 9, 11, 14, 16, 19, 21, 23, 25, 27, 29):
        frames[i] = ("B", Frame(theta=100, beta=100, alpha=2000.0, delta=100, ts=float(i)))
    b = StateEngine.calibrate_from_frames(frames)
    rep = StateEngine.calibration_report(frames, b)
    sig_clean = b["alpha"].sigma
    vals = sorted(fr.alpha for _, fr in frames)
    sig_raw = (vals[(3 * len(vals)) // 4] - vals[len(vals) // 4]) / 2 * 1.4826
    kept = b["calib_meta"]["segments"].get("B", 0)
    warned = [w for w in rep["warnings"] if "伪迹帧占比" in w]
    ok = (sig_clean < 20 and sig_raw > 100 and abs(b["alpha"].mu - 110) < 5
          and kept == 18                                   # 12 帧伪迹无一进入统计
          and b["calib_meta"]["artifact_ratio"] > se.CALIB_ARTIFACT_MAX
          and rep["ok"] is False and bool(warned))
    check("E13 校准剔除伪迹帧 + 伪迹率超限判校准失败", ok,
          f"剔除后 σ={sig_clean:.1f}、μ={b['alpha'].mu:.1f}（干净帧 {kept}/30）；"
          f"不剔除 σ≈{sig_raw:.0f}；报告 ok={rep['ok']}、伪迹率 {rep['artifact_ratio']:.0%}、"
          f"告警={'/'.join(warned) or '无'}")


# ---------------------------------------------------------------- E14 调用面兼容
def e14_api_compat():
    """历史调用面不许断：_scores 仍返回 2 元、_arbitrate 2 参可用、push_rr 缺省不推进时钟、
    eng.rr_buf 可直接替换（verify_v29 就这么干）。"""
    calls = [0]

    def clock():
        calls[0] += 1
        return float(calls[0])

    eng = StateEngine(baseline=base(), clock=clock)
    fr = Frame(theta=55.0, beta=100.0, alpha=130.0, delta=100.0, gamma=50.0,
               attention=65.0, meditation=55.0)
    for i in range(12):
        eng._push_window(fr, float(i))
    feats = eng._extract()
    scores, gates = eng._scores(feats, {"task_active": True}, fr)      # 2 元解包
    before = calls[0]
    eng.push_rr(800.0, batch=1)                                        # 缺省 ts 不应调 clock
    untouched = calls[0] == before
    eng.rr_buf = [(800.0, None, 0.0), (820.0, None, 0.0)] * 6          # 直接替换仍有意义
    rmssd = eng._extract().RMSSD_60
    st, conf = eng._arbitrate(scores, gates)                           # 2 参调用
    ok = (isinstance(scores, ScoreMap) and untouched and math.isfinite(rmssd)
          and abs(rmssd - 20.0) < 1e-9 and st in se.PRIORITY + ["neutral"])
    check("E14 调用面兼容（_scores 2 元 / _arbitrate 2 参 / push_rr 不动时钟 / rr_buf 可替换）", ok,
          f"RMSSD={rmssd:.2f}（替换后应为 20）、clock 调用次数未变={untouched}、state={st}")


# ---------------------------------------------------------------- E15 异位搏动滤波
def e15_ectopic_filter():
    """一次掉搏（800→1420ms）不得进入 RMSSD。
    反例：关掉滤波（RR_DIFF_MAX_RATIO=∞）同一段数据 RMSSD 抬一个量级。"""
    buf = []
    for i in range(20):
        for j in range(3):
            buf.append((800.0 + 10 * ((i + j) % 3), i, 0.0))
    clean = StateEngine._rmssd(buf)
    buf_bad = list(buf)
    buf_bad[30] = (1420.0, 10, 0.0)                    # 掉搏
    filtered = StateEngine._rmssd(buf_bad)
    old = se.RR_DIFF_MAX_RATIO
    try:
        se.RR_DIFF_MAX_RATIO = float("inf")            # 反例：关闭滤波
        unfiltered = StateEngine._rmssd(buf_bad)
    finally:
        se.RR_DIFF_MAX_RATIO = old
    ok = abs(filtered / clean - 1) < 0.03 and unfiltered > 5 * filtered
    check("E15 异位搏动滤波（掉搏不进 RMSSD）", ok,
          f"干净 {clean:.2f}ms / 含掉搏(滤波后) {filtered:.2f}ms / 关闭滤波 {unfiltered:.2f}ms")


def e16_sustained_artifact():
    """突变型持续伪迹（β 20× 基线、连续 40 帧）：必须一帧不漏，且参照/平滑不得被伪迹带走。

    这一项的真实作用分两层，别夸大：
      · **不是**在修旧实现的缺陷——旧实现（EWMA 参照 + 伪迹帧不推进平滑）对"突变"同样 40/40；
        旧实现的病在"门限与眨眼幅度同量级"，由 E13 覆盖。
      · **是**一条回归守卫：v3.4 设计参照系时试过"把伪迹帧也记进参照窗口"，实测 16 帧后中位数
        翻到伪迹电平、同一批 20× 帧全部放行（16/40），"持续伪迹自我掩护"换个形式又回来了。
        这里锁死"只由有效帧记账"这条规则。
    另锁逃生门：连续拦到 ARTIFACT_REF_RESET 帧仍不放行 → 说明"电平换了"而不是"连续一分钟都是
    伪迹"（重戴/增益切换），此时重建参照，否则判据永久锁死。
    已知局限（不在此断言范围内）：渐变起音的肌电（每帧 +7.8%，40 帧升到 20×）两版都检不出（0/40），
    须靠 β+γ 专用门（ARTIFACT_EMG_GATE，§6.3）。"""
    det = ArtifactDetector()
    for i in range(60):
        det.push(Frame(theta=100, beta=100, alpha=110, gamma=50, delta=100, ts=float(i)))
    hits = sum(det.push(Frame(theta=100, beta=2000, alpha=110, gamma=1000,
                              delta=100, ts=float(60 + i))) is not None for i in range(40))
    smooth_kept = abs(det.smooth["beta"] - 100.0) < 1e-9

    det2 = ArtifactDetector()                      # 逃生门：70 帧同电平（>60）→ 中途重建
    for i in range(60):
        det2.push(Frame(theta=100, beta=100, alpha=110, gamma=50, delta=100, ts=float(i)))
    hits2 = sum(det2.push(Frame(theta=100, beta=2000, alpha=110, gamma=1000,
                                delta=100, ts=float(60 + i))) is not None for i in range(70))
    ok = hits == 40 and smooth_kept and hits2 == 60 and det2.reseeds == 1
    check("E16 突变持续伪迹全拦（只由有效帧记账）+ 连续全拦走逃生门", ok,
          f"40 帧拦截 {hits}/40、平滑 β 未被拖走={smooth_kept}（若把伪迹记进参照→16/40 并拖到 1991）；"
          f"70 帧同电平拦截 {hits2}/70 后重建 {det2.reseeds} 次（期望 60/70、1 次）")


def e17_ts_zero_window():
    """v3.16：ts=0 是合法时间戳，不得被当成缺值回退墙钟；窗口必须按 ts 裁剪。
    旧实现首帧 0 被换成 time.time()（1.7e9），后续帧永远"在过去"→ 100 帧后缓冲仍是 100，
    10s 窗退化为全程累计（回放结论静默失真）。"""
    eng = StateEngine(baseline=base())
    kw = dict(theta=100.0, alpha=100.0, beta=100.0, delta=100.0, gamma=50.0,
              attention=60.0, meditation=60.0, gyro=(0.01, 0.01, 0.01))
    for i in range(5):
        eng.process_frame(Frame(ts=float(i), **kw), {"task_active": True})
    first_ok = eng.eeg_buf[0].ts == 0.0
    for i in range(5, 12):
        eng.process_frame(Frame(ts=float(i), **kw), {"task_active": True})
    # 1s/帧、窗口 10s（含端点）→ 稳定后 td=1..11 共 11 帧，ts=0 已出窗
    ok = first_ok and len(eng.eeg_buf) == 11 and eng.eeg_buf[0].ts == 1.0
    check("E17 首帧 ts=0 合法且 10s 窗按 ts 裁剪", ok,
          f"首帧保留={first_ok}、12 帧后缓冲={len(eng.eeg_buf)}（期望 11）、窗首 ts={eng.eeg_buf[0].ts}")


def main():
    for fn in (e1_contact_gate, e2_single_artifact_rule, e3_delta_window,
               e4_if_rectifier_bias, e5_if_guard_recover, e6_evidence_units,
               e7_conf_bounds, e8_confirm_by_time, e9_calib_delta_from_b,
               e10_calib_rmssd_same_path, e11_calib_a_rel, e12_log_space,
               e13_calib_artifact_filter, e14_api_compat, e15_ectopic_filter,
               e16_sustained_artifact, e17_ts_zero_window):
        fn()
    n = 17
    if fails:
        print(f"\n失败 {len(fails)}/{n}：{'、'.join(fails)}")
        return 1
    print(f"\n全部通过（{n} 项，含 5 条反例对照）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
