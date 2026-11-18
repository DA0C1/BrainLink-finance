# -*- coding: utf-8 -*-
"""_audit_engine.py — 引擎算法审计（改造前/后对照，临时脚本，不进交付包）

用法：
  python _audit_engine.py                      # 审当前 state_engine.py
  python _audit_engine.py _bak_engine_v33/state_engine_v33.py   # 审改造前快照

只做测量、不做断言——断言在 verify_engine.py。
输出 8 组指标，对应 8 个待证或待修的算法问题。
"""
import importlib.util
import math
import random
import sys
import os

HERE = os.path.dirname(os.path.abspath(__file__))
path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(HERE, "state_engine.py")
if not os.path.isabs(path):
    path = os.path.join(HERE, path)
spec = importlib.util.spec_from_file_location("se_audit", path)
se = importlib.util.module_from_spec(spec)
sys.modules["se_audit"] = se
spec.loader.exec_module(se)

print(f"审计对象：{os.path.relpath(path, HERE)}")
base = se.StateEngine.calibrate_from_mock([])


def line(tag, txt):
    print(f"  [{tag}] {txt}")


# ---------------------------------------------------------------- A1 疲劳积分整流偏置
def a1_if_bias():
    """θ 均值恰好压在触发阈上（θ_norm=1.2）时，噪声会不会把 I_f 顶上去？
    理论：rate=max(0,(θ_norm-1.2)/0.3)，若 θ 有噪声 σ，E[rate]≈σ_norm/(0.3·√(2π))>0（Jensen）。"""
    print("\nA1 疲劳积分整流偏置（θ 均值=阈上 0，噪声 σ=12，1800s）")
    for tag, noise in (("无噪声 σ=0", 0.0), ("含噪声 σ=12", 12.0)):
        rnd = random.Random(11)
        eng = se.StateEngine(baseline=dict(base), clock=lambda: 0.0)
        eng._theta_ref = 100.0
        ts = 0.0
        for i in range(1800):
            th = 120.0 + (rnd.gauss(0, noise) if noise else 0.0)
            fr = se.Frame(theta=th, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                          attention=50.0, meditation=50.0, ts=ts)
            eng.process_frame(fr, {"task_active": True})
            ts += 1.0
        line(tag, f"I_f={eng._theta_int:8.1f}s  等效累积速率={eng._theta_int/1800:+.4f}/s  "
                   f"疲劳门控={'达标(假阳性)' if eng._theta_int >= se.IF_HOLD_S else '未达标'}")


# ---------------------------------------------------------------- A2 δ 基线取段
def a2_delta_seg():
    """校准把 δ 基线建在 A 段（闭眼，δ 系统性偏高），运行时却是睁眼任务 → z(δ) 恒负 → 嗜睡静默失效。"""
    print("\nA2 嗜睡 δ 佐证基线：A 段(闭眼) vs B 段(任务参照)")
    rnd = random.Random(5)
    frames = []
    for _ in range(30):   # A 段闭眼：δ 高
        frames.append(("A", se.Frame(theta=130 + rnd.uniform(-6, 6), alpha=120,
                                     beta=80, delta=150 + rnd.uniform(-8, 8))))
    for _ in range(30):   # B 段睁眼任务：δ 低
        frames.append(("B", se.Frame(theta=100 + rnd.uniform(-6, 6), alpha=100,
                                     beta=100, delta=100 + rnd.uniform(-8, 8))))
    b = se.StateEngine.calibrate_from_frames(frames)
    mu = b["delta"].mu
    sig = max(b["delta"].sigma, b["delta"].sigma_min)
    for label, d in (("任务中轻度嗜睡 δ=118", 118.0), ("明显嗜睡 δ=130", 130.0)):
        z = (d - mu) / sig
        line(label, f"δ 基线 μ={mu:.1f} σ={sig:.1f} → z(δ)={z:+.2f}（嗜睡门控需 >+1.0）"
                    f" → {'通过' if z > 1.0 else '不通过（嗜睡不可判）'}")


# ---------------------------------------------------------------- A3 RMSSD 口径
def a3_rmssd_path():
    """校准产出的 RMSSD 基线，是否等于运行时对**同一段数据**算出的值？

    两种录制形态分别比（同类比同类，否则是拿苹果比橘子）：
      ① 整批（真机新录制 rr1–rr3）：帧带 rr_batch，运行走 push_rr(batch)
      ② 单值（旧 CSV 降级）：帧只有 rr_ms，运行走 push_rr(None)
    数据里插一次掉搏（1420ms），用来同时检验异位滤波。
    """
    print("\nA3 RMSSD 口径：校准基线与运行时实算（同一段数据，逐形态对比）")
    rnd = random.Random(3)
    batches = []
    for i in range(20):
        seed = 800 + 6 * math.sin(i / 3.0)
        batch = [seed + rnd.uniform(-12, 12) for _ in range(3)]
        if i == 10:
            batch = [seed, 1420.0, seed + 4]            # 掉搏
        batches.append([round(v, 1) for v in batch])

    def runtime_rmssd(by_batch: bool) -> float:
        eng = se.StateEngine(baseline=dict(base), clock=lambda: 0.0)
        for i, b in enumerate(batches):
            for v in b:
                eng.push_rr(v, batch=(i if by_batch else None), ts=0.0)
        return se.StateEngine._rmssd(eng.rr_buf)

    def calib_rmssd(by_batch: bool):
        frames = []
        if by_batch:
            # 整批录制：一行 = 一次 onRR 的三个间期（CSV 的 rr1/rr2/rr3 三列）
            for i, b in enumerate(batches):
                fr = se.Frame(theta=100.0, beta=100.0, alpha=100.0, delta=100.0,
                              gamma=50.0, ts=float(i))
                fr.rr_batch = tuple(b)                  # 旧版无该字段 → 忽略（正是要测的差异）
                frames.append(("B", fr))
        else:
            # 单值录制：一行 = 一个间期（旧 CSV 只有 rr_ms 列）
            for k, v in enumerate(v for b in batches for v in b):
                frames.append(("B", se.Frame(theta=100.0, beta=100.0, alpha=100.0,
                                             delta=100.0, gamma=50.0, rr_ms=v, ts=float(k))))
        b_ = se.StateEngine.calibrate_from_frames(frames)
        meta = b_.get("calib_meta") or {}
        return b_["RMSSD_60"].mu, meta.get("rmssd_pairs")

    for tag, by_batch in (("①整批 rr1–rr3（真机形态）", True), ("②单值 rr_ms（旧 CSV 降级）", False)):
        rt = runtime_rmssd(by_batch)
        cal, pairs = calib_rmssd(by_batch)
        if math.isfinite(rt) and math.isfinite(cal) and rt:
            line(tag, f"运行 {rt:6.2f}ms / 校准 {cal:6.2f}ms → 偏差 {(cal/rt-1)*100:+6.1f}%"
                      + (f"（校准差分对 {pairs}）" if pairs is not None else ""))
        else:
            line(tag, f"运行 {rt} / 校准 {cal}（有一侧不可算）")


# ---------------------------------------------------------------- A4 跨量纲置信度
def a4_conf_units():
    """强应激（RMSSD z=−2，门控余量 0.5σ）与焦虑（att/med=30，余量 0.67σ）同时命中：
    两个状态**证据相当**，理应给出中等置信度；但旧公式比的是"分数"，
    焦虑的量纲大 10 倍（20.0 vs 2.0），把分母吃掉 → 强应激被判成弱判别。"""
    print("\nA4 跨状态置信度：同一场景，两种口径")
    eng = se.StateEngine(baseline=dict(base))
    neg = float("-inf")
    gates = {x: False for x in se.PRIORITY}
    gates.update({"stress": True, "anxiety": True})
    scores = {x: neg for x in se.PRIORITY}
    scores.update({"stress": 2.0, "anxiety": 20.0})   # stress: RMSSD z=−2；anxiety: att/med 双低 20 余量
    st, conf = eng._arbitrate(scores, gates)          # 普通 dict → 走"分数差比"兼容路径
    line("分数差比", f"state={st} conf={conf:.3f} → "
                    f"{'弱判别（仅记录，不干预）' if conf < se.WEAK_CONF else '正常提醒'}")
    if hasattr(se, "ScoreMap"):
        sc = se.ScoreMap(scores)
        sc.margins = {"stress": 0.5, "anxiety": 0.67}
        sc.evidence = {"stress": 0.5, "anxiety": 0.67}
        st2, conf2 = eng._arbitrate(sc, gates)
        line("证据比", f"state={st2} conf={conf2:.3f} → "
                      f"{'弱判别（仅记录，不干预）' if conf2 < se.WEAK_CONF else '正常提醒'}")
    else:
        line("证据比", "本版本无证据表（旧引擎）")


# ---------------------------------------------------------------- A5 δ 瞬时 vs 窗均值
def a5_delta_window():
    """δ 的 z 用瞬时帧值，其余特征全用 10s 窗均值——单帧 δ 起伏即可让嗜睡门控翻转。"""
    print("\nA5 δ 特征时间尺度：瞬时 vs 10s 窗均值")
    eng = se.StateEngine(baseline=dict(base))
    ts = 0.0
    for _ in range(10):
        eng._push_window(se.Frame(delta=100.0), ts)
        ts += 1.0
    spike = se.Frame(delta=140.0)
    eng._push_window(spike, ts)
    f = eng._extract()
    win = getattr(f, "delta", None)
    z_inst = (140.0 - base["delta"].mu) / max(base["delta"].sigma, base["delta"].sigma_min)
    line("瞬时", f"z(δ)={z_inst:+.2f}")
    if win is None:
        line("窗均值", "当前引擎 Feats 无 delta 字段（无法用窗均值）→ 只能用瞬时值")
    else:
        z_win = (win - base["delta"].mu) / max(base["delta"].sigma, base["delta"].sigma_min)
        line("窗均值", f"δ̄={win:.1f} z(δ)={z_win:+.2f}  抖动抑制 {(1-abs(z_win)/max(abs(z_inst),1e-9))*100:.0f}%")


# ---------------------------------------------------------------- A6 接触质量门
def a6_contact():
    """signal=200（电极未接触皮肤）的帧是否会被当成有效 EEG 喂进判别。"""
    print("\nA6 接触质量门（signal=200 = 电极未接触皮肤）")
    eng = se.StateEngine(baseline=dict(base))
    ok = hasattr(se.Frame(), "signal")
    for i in range(12):
        fr = se.Frame(theta=55.0, beta=100.0, alpha=130.0, delta=100.0, gamma=50.0,
                      attention=65.0, meditation=55.0, ts=float(i + 1))
        if ok:
            fr.signal = 200
        eng.process_frame(fr, {"task_active": True})
    line("帧字段", f"Frame.signal 存在={ok}")
    line("结果", f"eeg_buf={len(eng.eeg_buf)} 帧（>0 即：脱戴帧已进入特征窗）"
                f" state={eng.last_decision.state if eng.last_decision else None}")


# ---------------------------------------------------------------- A7 伪迹参考系
def a7_artifact_ref():
    """三种高发伪迹形态下的拦截率。**分形态看**，避免"改好了"说得过头：
      ① 突变肌电（β 一步到 20×）：两版都能拦——旧实现伪迹帧不推进平滑，这一点本来是对的；
      ② 眨眼类幅值突变（α 8.2×，30 帧里 12 帧）：旧实现 0/12（门限 8× 与物理幅度同量级），新实现全拦；
      ③ 渐变起音肌电（每帧 +7.8%，40 帧升到 20×）：两版都 0/40——已知局限，须靠 β+γ 专用门（§6.3）。
    """
    print("\nA7 伪迹拦截率：三种形态（同一引擎入口，判伪迹→有效帧推进平滑）")

    def feed(eng, frames):
        hit = 0
        for fr in frames:
            if eng._artifact(fr):
                hit += 1
                continue
            for k in ("delta", "theta", "alpha", "beta", "gamma"):
                eng._ewma(k, getattr(fr, k))
            eng._ewma("R_tb", fr.theta / fr.beta)
        return hit

    def new_eng():
        return se.StateEngine(baseline=dict(base))

    # ① 突变肌电
    eng = new_eng()
    feed(eng, [se.Frame(theta=100, beta=100, alpha=110, gamma=50, delta=100) for _ in range(60)])
    h = feed(eng, [se.Frame(theta=100, beta=2000, alpha=110, gamma=1000, delta=100) for _ in range(40)])
    line("① 突变肌电", f"拦截 {h}/40　平滑 β={eng.smooth.get('beta', float('nan')):.0f}")

    # ② 眨眼类幅值突变（α 8.2× 基线，占 40%）
    rnd = random.Random(13)
    eng = new_eng()
    fr = [se.Frame(theta=100, beta=100, alpha=110 + rnd.uniform(-6, 6), gamma=50, delta=100)
          for _ in range(30)]
    for i in (3, 5, 9, 11, 14, 16, 19, 21, 23, 25, 27, 29):
        fr[i] = se.Frame(theta=100, beta=100, alpha=900.0, gamma=50, delta=100)
    line("② 眨眼 8.2×", f"拦截 {feed(eng, fr)}/12")

    # ③ 渐变起音（朗读/咬牙的典型起音形态）
    eng = new_eng()
    feed(eng, [se.Frame(theta=100, beta=100, alpha=110, gamma=50, delta=100) for _ in range(60)])
    ramp = [se.Frame(theta=100, beta=100 * 20 ** (i / 39), alpha=110,
                     gamma=50 * 20 ** (i / 39), delta=100) for i in range(40)]
    h = feed(eng, ramp)
    line("③ 渐变起音", f"拦截 {h}/40　平滑 β={eng.smooth.get('beta', float('nan')):.0f}"
                       f"（两版都拦不住：比值判据分不清'缓涨 20 倍'与真实状态迁移）")


# ---------------------------------------------------------------- A8 置信度对强度的钝化
def a8_conf_strength_sensitivity():
    """单候选（无竞争对手）时，置信度还分得清"刚过线"与"远超阈值"吗？

    这是 v3.4 复查时发现的问题：**两版都分不清**——
      旧 v3.3：conf = 0.6 + 0.35·(best−second)/(|best|+|second|)，second=0 → 恒 0.95；
      新 v3.4：只做证据比 sep = e_best/(e_best+e_second)，e_second=0 → sep 恒 1 → 恒 0.95。
    后果：WEAK_CONF=0.7 的"弱判别仅记录"在单状态场景永不生效（实测演示里一眼看不出，
    因为演示剧本恰好每个状态都有明显余量）。

    修法（v3.4.1）：conf 再乘 strength = clamp(m_best/EVID_FULL)。
    注意旧引擎**没有** margins 概念，只能用 score 当强度代理——对比时读作
    "旧：不同分数 → 同 conf"/"新：不同余量 → 不同 conf"。
    """
    print("\nA8 置信度强度敏感性（单候选，无竞争对手）")
    eng = se.StateEngine(baseline=dict(base))
    neg = float("-inf")

    def conf_new(m):
        sc = se.ScoreMap({x: neg for x in se.PRIORITY})
        sc["stress"] = 2.0
        sc.margins = {"stress": m}
        sc.evidence = {"stress": min(1.0, m / getattr(se, "EVID_REF", 1.0))}
        return eng._arbitrate(sc, {})[1]

    if hasattr(se, "ScoreMap") and "EVID_FULL" in dir(se):
        vals = [0.001, 0.05, 0.25, 0.5, 2.0]
        out = "  ".join(f"{m:g}σ→{conf_new(m):.3f}" for m in vals)
        line("新 v3.4.1", out)
        line("判读", f"0.001σ 与 2.0σ 的 conf 差 {conf_new(2.0) - conf_new(0.001):.3f}"
                    f"（差 >0.3 才算真的分辨得开；≈0 即钝化）")
    else:
        line("新 v3.4.1", "本版本无 strength 维度")

    # 旧版：用 score 当强度代理（0.1 与 50 相差 500 倍）
    old_scores = []
    for sc_v in (0.1, 50.0):
        plain = {x: neg for x in se.PRIORITY}
        plain["stress"] = sc_v
        old_scores.append(eng._arbitrate(plain, {})[1])   # 普通 dict → 走旧"分数差比"路径
    line("旧 v3.3 口径", f"score 0.1→{old_scores[0]:.3f}  score 50→{old_scores[1]:.3f}"
                        f"（差 {abs(old_scores[1]-old_scores[0]):.3f} → 完全钝化）")


for fn in (a1_if_bias, a2_delta_seg, a3_rmssd_path, a4_conf_units, a5_delta_window,
           a6_contact, a7_artifact_ref, a8_conf_strength_sensitivity):
    fn()
print("\n审计完成")
