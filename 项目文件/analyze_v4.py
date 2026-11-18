# -*- coding: utf-8 -*-
"""analyze_v4.py — 从真机 dump 判定 V4（说话肌电门）

为什么单独一个脚本
------------------
D3 的 V4 需要「静默段 + 说话段」成对数据，判据在 `verify_hw.v4_emg` 里已经写好
（静默段前 60% 建基线、后 40% 测误报；说话段测拦截率；≥90% 才算拦得住）。
但 `verify_hw` 读的是 CSV，而真机 dump 是 JSONL —— 本脚本只做**格式适配**，
判据一律调用 `vh.v4_emg`，绝不另写一套（项目铁律：同一事实只维护一次）。

    python analyze_v4.py data/real_sub01_xxx.jsonl
    python analyze_v4.py --selftest          # 无数据自检：证明判据会变红

结论口径：
  8×门能拦住（现为 15× 门）→ 保持 ARTIFACT_EMG_GATE=False，主任务无需额外门控
  只有开 β+γ 门才拦得住 → 需把 state_engine.py 与 engine.js 的 ARTIFACT_EMG_GATE 同改 True
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import verify_hw as vh          # noqa: E402  —— 判据唯一来源
import state_engine as se       # noqa: E402

SILENT_LABEL = "V4_SILENT"
TALK_LABEL = "V4_TALK"


def load_segments(path: str) -> dict[str, list[dict]]:
    """按 marker 切段；返回 {label: [eeg 行]}。"""
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda e: e.get("t", 0))
    segs: dict[str, list[dict]] = {}
    cur = None
    for r in rows:
        if r.get("type") == "marker":
            lb = r["label"]
            if lb.endswith("_END"):
                cur = None
            else:
                cur = lb
                segs.setdefault(lb, [])
        elif r.get("type") == "eeg" and cur is not None:
            segs[cur].append(r)
    return segs


def to_v4_rows(evs: list[dict]) -> list[dict]:
    """jsonl 的 8 频段 → verify_hw 要的 5 频段（low/high 合并）。"""
    out = []
    for e in evs:
        if e.get("signal", 0) >= se.SIGNAL_BAD:
            continue                                  # 未接触帧不参与
        out.append({
            "delta": float(e["delta"]), "theta": float(e["theta"]),
            "alpha": float(e["lowAlpha"]) + float(e["highAlpha"]),
            "beta": float(e["lowBeta"]) + float(e["highBeta"]),
            "gamma": float(e["lowGamma"]) + float(e["highGamma"]),
        })
    return out


def band_medians(evs: list[dict]) -> dict[str, float]:
    """5 频段中位数（low/high 合并）。用来判断"说话伪迹落在哪个频段"。"""
    import statistics as st
    if not evs:
        return {}
    return {
        "delta": st.median([e["delta"] for e in evs]),
        "theta": st.median([e["theta"] for e in evs]),
        "alpha": st.median([e["lowAlpha"] + e["highAlpha"] for e in evs]),
        "beta": st.median([e["lowBeta"] + e["highBeta"] for e in evs]),
        "gamma": st.median([e["lowGamma"] + e["highGamma"] for e in evs]),
    }


def explain_by_band(silent_ev: list[dict], talk_ev: list[dict]) -> None:
    """v3.6：判据 FAIL 时给出**归因**，别让人误以为"查硬件"就完事。

    2026-11-10 补测实证（signal 全程 0、伪迹率 5.4%、接触 0%）：
        说话段 δ 9.42× / θ 2.67× / α 1.72× / β 1.20× / γ 2.01×
    → 说话产生的伪迹**落在 δ 低频**（下颌/头部运动导致的基线漂移），
      不是 β+γ 那种真正的面部肌电。所以 β+γ 专门前瞻性地判不出来，也不是硬件坏了。
    """
    s, t = band_medians(silent_ev), band_medians(talk_ev)
    if not s or not t:
        return
    print("\n各频段 静默→说话 倍数（判断伪迹落在哪个通道）：")
    ratios = {}
    for k in ("delta", "theta", "alpha", "beta", "gamma"):
        r = t[k] / s[k] if s[k] else 0.0
        ratios[k] = r
        mark = ""
        if r >= 4:
            mark = "  ← 显著升高"
        print(f"  {k:<6} {s[k]:>9.0f} → {t[k]:>9.0f}   {r:5.2f}×{mark}")

    # 归因分支
    if ratios["delta"] >= 3 and ratios["beta"] < 2 and ratios["gamma"] < 3:
        print("\n  ★ 归因：说话伪迹走 **δ 低频通道**（机械位移/基线漂移），"
              "不是 β+γ 肌电。")
        print("    → β+γ 专门前瞻性无效，**保持 ARTIFACT_EMG_GATE=False**；")
        print("    → 真正的风险是 δ 污染 drowsy/TAR 判据：15× 门拦不住 9.4× 的 δ 抬升，")
        print("      唯一可靠对策是**协议要求任务期间不发声**（不是调阈值）。")
    elif ratios["beta"] >= 4 and ratios["gamma"] >= 4:
        print("\n  ★ 归因：β+γ 同升 → 典型面部肌电，**应启用 ARTIFACT_EMG_GATE**"
              "（state_engine.py 与 engine.js 同时改 True）。")
    else:
        print("\n  ★ 归因：两段各频段差异均不显著 → 受试者可能没真说话（音量过小/时间过短），"
              "本段数据不足以判定 V4，建议按同样流程重录说话段。")


def run(path: str) -> int:
    segs = load_segments(path)
    silent_ev = segs.get(SILENT_LABEL) or []
    talk_ev = segs.get(TALK_LABEL) or []
    print(f"dump      : {os.path.basename(path)}")
    print(f"段        : {['%s(%d帧)' % (k, len(v)) for k, v in segs.items()]}")
    if not silent_ev or not talk_ev:
        print(f"\n✗ 缺段：需要 {SILENT_LABEL} 与 {TALK_LABEL}（用 collect_real.py --preset d2v4 采集）")
        print(f"  当前：静默 {len(silent_ev)} 帧、说话 {len(talk_ev)} 帧")
        return 2
    silent, talk = to_v4_rows(silent_ev), to_v4_rows(talk_ev)
    ok, detail, advice = vh.v4_emg(silent, talk)
    print(f"\n静默段 {len(silent)} 帧（前 {int(len(silent)*0.6)} 建基线、后 {len(silent)-int(len(silent)*0.6)} 测误报）"
          f" / 说话段 {len(talk)} 帧")
    print(f"V4 结论   : {'✓ PASS' if ok else '✗ FAIL'}（β+γ 专门口径）")
    print(f"  判据    : {detail}")
    print(f"  建议    : {advice}")
    explain_by_band(silent_ev, talk_ev)
    return 0 if ok else 1


def selftest() -> int:
    """用合成数据证明判据有判别力：强肌电必须被拦，弱变化必须不被拦。"""
    import random
    rnd = random.Random(11)

    def seg(beta, gamma=50.0, n=40):
        """合成一段：β/γ 可独立放大。**真肌电是 β+γ 同升**，只放大 β 不构成肌电特征。"""
        return [{"delta": 100.0, "theta": 100.0, "alpha": 100.0,
                 "beta": beta * (1 + 0.02 * (rnd.random() - .5)),
                 "gamma": gamma * (1 + 0.02 * (rnd.random() - .5))} for _ in range(n)]

    fails = []

    def ck(name, cond, extra=""):
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f" — {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("P1 真肌电（β 20× + γ 5× 同升）应被拦 —— 靠 β+γ 专用门")
    ok, detail, _ = vh.v4_emg(seg(100), seg(2000, 250))
    ck("P1 真肌电 PASS", ok, detail)

    print("P2 只有 β 升、γ 不动（5×/1×）——不是肌电，应拦不住（反例：防判据恒真）")
    ok2, detail2, _ = vh.v4_emg(seg(100), seg(500, 50))
    ck("P2 非肌电 FAIL", ok2 is False, detail2)

    print("P3 无变化（β/γ 同水平）也必须 FAIL")
    ok3, detail3, _ = vh.v4_emg(seg(100), seg(100, 50))
    ck("P3 无变化 FAIL", ok3 is False, detail3)

    print()
    if fails:
        print(f"FAIL — {len(fails)} 项未通过")
        return 1
    print("自检通过：判据在强肌电下变绿、弱变化与无肌电下变红（不是恒真断言）")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="V4 说话肌电门判定（从真机 dump）")
    ap.add_argument("dump", nargs="?", help="collect_real 产的 .jsonl")
    ap.add_argument("--selftest", action="store_true", help="无数据自检")
    a = ap.parse_args(argv)
    if a.selftest:
        return selftest()
    if not a.dump:
        ap.print_help()
        return 2
    return run(a.dump)


if __name__ == "__main__":
    sys.exit(main())
