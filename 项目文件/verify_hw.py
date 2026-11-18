# -*- coding: utf-8 -*-
"""verify_hw.py — T4 真机五项验证测试模板（V3 前置，V1/V2/V4，V5 可选）

对应《阶段二规划》§六 5.1 验证顺序与 5.3 通过标准 + 参数决策映射表：

    V3 signal 标定 → V1 RR 质量 → V2 δ 可测性 → V4 说话肌电 → V5 咖啡因（可选）

每项都是"函数 + 断言 + 结论建议"，不是一张给人看的检查表——真机现场最怕
"看了一遍觉得还行"。结论直接映射到参数决策（5.3 表），失败时给出可执行的下一步。

用法
----
    # 无真机自检：合成数据跑通五项（验证测试逻辑本身）
    python verify_hw.py --demo

    # 真机数据（serial_bridge.py --record 产出的 CSV）
    python verify_hw.py --v3 rec.csv --v1 rec.csv --v2-closed closed.csv --v2-open open.csv \
                        --v4-silent silent.csv --v4-speak speak.csv
    python verify_hw.py --all rec.csv          # 单文件跑 V3/V1（V2/V4 需成对数据）

    # 咖啡因（可选，需伦理同意）：摄入前后各一段
    python verify_hw.py --v5-before before.csv --v5-after after.csv

设计说明
--------
* 阈值即 5.3 表的"经验初值"，代码里集中定义，真机实测后改这里一处即可；
* 全部只读 CSV，不改引擎、不写项目数据；
* 退出码 0=全过，1=有 FAIL（便于 CI/脚本串联）。
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
from typing import Any

# ---- 5.3 通过标准（经验初值；真机实测后在此处改） -------------------------------
# ⚠ signal 方向（2026-10-23 修正，阻塞级）：BrainLink 透传的是 TGAM poorSignalLevel，
#   它是「噪声值」——0 = 信号最好，200 = 电极未接触皮肤（脱戴），**数值越大越差**。
#   依据：NeuroSky《MindSet Communications Protocol》§POOR_SIGNAL Quality 原文
#   "The higher the number, the more noise is detected. A value of 200 has a special
#    meaning, specifically that the ThinkGear contacts are not touching the user's skin"；
#   ThinkGear Socket Protocol 亦注明 "0 indicating a good signal and 200 indicating an
#   off-head state"。
#   修正前本文件写作 SIGNAL_GOOD = 200 且判 `s >= 200` 为「良好」，方向整体反了：
#   真机上会把好帧全丢、只放行「脱戴」帧；而离线合成数据原先用 signal=230（已超 0–200 上限，
#   本就不合法）恰好让这条错误判据永远通过 —— 典型的"测试自证"。
SIGNAL_OFFHEAD = 200       # 硬上限：>=200 即电极未接触皮肤，该帧不可用
SIGNAL_GOOD_MAX = 100      # 「良好」上沿初值（越小越好；V3 实测后按分位数收紧）
V3_GOOD_RATIO_MIN = 0.80   # V3：佩戴良好时稳定档位占比 >80%
V1_RR_LOSS_MAX = 0.10      # V1：RR 丢失率 <10%
V1_RR_BAD_MAX = 0.05       # V1：超生理范围（<300ms / >2000ms）占比上限
V2_SEPARATION_MIN = 0.20   # V2：闭眼 vs 睁眼 δ 中位数相对差异 ≥20%
V4_ARTIFACT_MIN = 0.90     # V4：说话时伪迹触发率 ≥90%
# V5 咖啡因（可选、描述性）：**不做显著性检验**（样本量不足，硬判会得到假结论），
# 只判断"有没有明显响应"。原 V5_ALPHA=0.05 是死常量（从不做检验，无消费方）——
# 换成下面两个真正参与判定的阈值，避免"文档写着 0.05、代码根本没算 p 值"的误读。
V5_BETA_RISE_MIN = 0.10    # β 相对基线上升 >10% 才算"有响应"
V5_HR_RISE_MIN = 3.0       # HR 上升 >3 bpm（低于此属正常波动）

RR_MIN, RR_MAX = 300.0, 2000.0


# --------------------------------------------------------------------------- 读 CSV
def load_rows(path: str) -> list[dict[str, Any]]:
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        raise SystemExit(f"CSV 为空：{path}")
    return rows


def num(row: dict, *names: str) -> float | None:
    for n in names:
        v = row.get(n)
        if v is None or v == "":
            continue
        try:
            f = float(v)
        except ValueError:
            continue
        if math.isfinite(f):
            return f
    return None


def med(vals: list[float]) -> float | None:
    vals = [v for v in vals if v is not None and math.isfinite(v)]
    return statistics.median(vals) if vals else None


def _pct(sorted_vals: list[float], q: float) -> float:
    """已排序列表的分位数（线性插值；空列表返回 nan，调用方已先行非空判断）"""
    if not sorted_vals:
        return float("nan")
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    pos = q * (len(sorted_vals) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_vals) - 1)
    frac = pos - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


# --------------------------------------------------------------------------- V3
def v3_signal(rows: list[dict]) -> tuple[bool, str, str]:
    """V3：signal 档位标定。口径——poorSignalLevel，0 最好、200 脱戴，越小越好。"""
    sig = [num(r, "signal") for r in rows]
    sig = [s for s in sig if s is not None]
    if not sig:
        return False, "无 signal 列", "录制器未写 signal：检查 sdk_adapter 的 signal 字段是否透传"
    mx = max(sig)
    if mx > SIGNAL_OFFHEAD:
        # 超出协议范围：说明这一列根本不是 poorSignalLevel（例如被换算成"质量分"或写错列）
        return (False,
                f"signal 最大值 {mx:.0f} 超出协议上限 {SIGNAL_OFFHEAD}（legal 0–200）",
                "超出 0–200 说明字段口径不对：核对 sdk_adapter.onEEG 是否原样透传 data.signal，"
                "以及录制器 CSV_FIELDS 的 signal 列有没有被别的量覆盖")
    worn = sum(1 for s in sig if s < SIGNAL_OFFHEAD) / len(sig)   # 已佩戴（硬门槛，与阈值无关）
    good = sum(1 for s in sig if s < SIGNAL_GOOD_MAX) / len(sig)  # 良好档（供标定参考）
    srt = sorted(sig)
    p50, p90 = _pct(srt, 0.50), _pct(srt, 0.90)
    ok = worn >= V3_GOOD_RATIO_MIN
    detail = (f"已佩戴(signal<{SIGNAL_OFFHEAD}) {worn:.0%}；良好(<{SIGNAL_GOOD_MAX}) {good:.0%}；"
              f"P50={p50:.0f} P90={p90:.0f}（越小越好，样本 {len(sig)}）")
    advice = (f"可作为 `signal_max` 初值：取良好档上沿 P90={p90:.0f}"
              "（取值越小门控越严；0 表示不过滤）") if ok else \
        (f"已佩戴占比 <{V3_GOOD_RATIO_MIN:.0%} → 先查佩戴（额头触片/耳夹/头发汗液遮挡）；"
         "佩戴稳定后仍偏高，才收紧 signal_max 丢帧，并在答辩标注「高档位数据丢弃策略」")
    return ok, detail, advice


# --------------------------------------------------------------------------- V1
def _rmssd_from_rows(rows: list[dict]) -> tuple[float, str]:
    """按录制 CSV 重建 RR 缓冲并算 RMSSD，返回 (值, 口径说明)。

    V1 的结论写的是"RMSSD→stress 判据可用"，那就必须**真的算一次**——
    原实现只查 rr_ms 的丢失率/范围，而 RMSSD 恒 nan 时它照样 PASS
    （onRR 每批只 1 个元素 → _rmssd 跨批全 continue → nan，是已修过的真实缺陷）。
    """
    if se is None:
        return float("nan"), "state_engine 不可用"
    has_batch = any(num(r, "rr1") is not None for r in rows)
    buf: list[tuple] = []
    if has_batch:
        for i, r in enumerate(rows):
            ts = num(r, "ts") or float(i)
            for key in ("rr1", "rr2", "rr3"):
                v = num(r, key)
                if v is not None and RR_MIN <= v <= RR_MAX:
                    buf.append((v, i, ts))          # 同一行的三个间期 = 同批
        return se.StateEngine._rmssd(buf), "同批三值"
    # 旧 CSV（无 rr1–rr3）：只能单值路径 batch=None（允许跨元素差分，降级口径）
    for i, r in enumerate(rows):
        v = num(r, "rr_ms", "rr")
        if v is not None and RR_MIN <= v <= RR_MAX:
            buf.append((v, None, num(r, "ts") or float(i)))
    return se.StateEngine._rmssd(buf), "单值路径（无批次信息）"


def v1_rr(rows: list[dict]) -> tuple[bool, str, str]:
    rr = [num(r, "rr_ms", "rr") for r in rows]
    present = [v for v in rr if v is not None]
    if not present:
        return False, "无 RR 数据", "心率未接通：软头环须先组装 PPG 再开机；硬头环无心率（预期降级）"
    loss = 1 - len(present) / len(rr)
    bad = sum(1 for v in present if v < RR_MIN or v > RR_MAX) / len(present)
    rmssd, mode = _rmssd_from_rows(rows)
    rmssd_ok = math.isfinite(rmssd) and rmssd > 0
    ok = loss < V1_RR_LOSS_MAX and bad < V1_RR_BAD_MAX and rmssd_ok
    detail = (f"丢失率 {loss:.1%}（<{V1_RR_LOSS_MAX:.0%}）、超生理范围 {bad:.1%}（<{V1_RR_BAD_MAX:.0%}），"
              f"有效 {len(present)}/{len(rr)}；RMSSD="
              + ("nan（算不出来）" if not math.isfinite(rmssd) else f"{rmssd:.1f}ms（{mode}）"))
    if ok:
        advice = "RR 质量合格且 RMSSD 实算可达 → stress 判据可用"
    elif not rmssd_ok:
        advice = ("RR 有数据但 RMSSD 算不出来 → 检查录制是否保留了**同一次 onRR 的 3 个间期**"
                  "（rr1/rr2/rr3 三列）；若每批只剩 1 个，引擎按批差分全断，RMSSD 恒 nan，"
                  "stress/anxiety 的关键特征静默失效")
    else:
        advice = ("RR 质量差 → 放宽 Z_STRESS（-1.5 → -1.2）或加长 HRV 窗；极差则 stress/focus 门控降级为佐证，"
                  "答辩标注「HRV 判据降级」")
    return ok, detail, advice


# --------------------------------------------------------------------------- V2
def v2_delta(rows_closed: list[dict], rows_open: list[dict]) -> tuple[bool, str, str]:
    dc = med([num(r, "delta") for r in rows_closed])
    do = med([num(r, "delta") for r in rows_open])
    if dc is None or do is None:
        return False, "缺 delta 列", "确认 SDK 回调含 delta（TGAM 原始频段），录制器未丢列"
    rel = abs(dc - do) / max(abs(do), 1e-9)
    ok = rel >= V2_SEPARATION_MIN
    detail = f"闭眼 δ 中位数 {dc:.1f} / 睁眼 {do:.1f}，相对差异 {rel:.0%}（阈值 {V2_SEPARATION_MIN:.0%}）"
    advice = "δ 可分 → drowsy 双判据（TAR+δ）保留" if ok else \
        ("δ 不可测 → drowsy 改单判据 TAR>+1.5（去掉 δ 合取），并在文档标注降级原因")
    return ok, detail, advice


# --------------------------------------------------------------------------- V4
# 直接驱动引擎（state_engine），不在测试里另写一套判据——
# 历史上"测试自己实现一遍"导致测试全绿而引擎行为不同（v2.9 的 JS/Py 分叉就是教训）。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import state_engine as se
except Exception:                                    # 缺文件/语法错时给出可执行提示
    se = None

_BANDS = ("delta", "theta", "alpha", "beta", "gamma")


def _to_frames(rows: list[dict]) -> list:
    """CSV 行 → 引擎 Frame（缺频段的行跳过，不猜值）。"""
    out = []
    for r in rows:
        vals = {k: num(r, k) for k in _BANDS}
        if any(v is None for v in vals.values()):
            continue
        out.append(se.Frame(**vals))
    return out


def _artifact_flags(frames: list, base: dict, gate: bool) -> list[bool]:
    """按引擎真实代码路径逐帧判伪迹（含平滑推进，与 process_frame 的更新顺序一致）。

    gate=True/False 通过临时切换模块常量实现——测的就是"开关拨上去到底管不管用"。
    """
    old = se.ARTIFACT_EMG_GATE
    se.ARTIFACT_EMG_GATE = gate
    try:
        eng = se.StateEngine(baseline=base)
        flags = []
        for fr in frames:
            hit = eng._artifact(fr)
            flags.append(hit)
            if hit:                                  # 伪迹帧不更新平滑（引擎一致）
                continue
            for key in _BANDS:
                eng._ewma(key, getattr(fr, key))
            eng._ewma("R_tb", eng._safe_ratio(fr.theta, fr.beta))
    finally:
        se.ARTIFACT_EMG_GATE = old
    return flags


def v4_emg(rows_silent: list[dict], rows_speak: list[dict]) -> tuple[bool, str, str]:
    if se is None:
        return False, "无法导入 state_engine", "确认 verify_hw.py 与 state_engine.py 同目录后重跑"
    silent, speak = _to_frames(rows_silent), _to_frames(rows_speak)
    if len(silent) < 20 or len(speak) < 10:
        return False, f"样本不足（静默 {len(silent)} 帧 / 朗读 {len(speak)} 帧）", \
            "静默段至少 20 帧、朗读段至少 10 帧：延长录制（1.2–1.5s/帧，20 帧≈30s）"
    # 静默段拆两半：前半建基线、后半测误报率（同一段既建基线又验误报是自证，必假过）
    cut = int(len(silent) * 0.6)
    base = se.StateEngine.calibrate_from_frames([("B", f) for f in silent[:cut]])
    hold = silent[cut:]
    f_off = _artifact_flags(speak, base, False)
    f_on = _artifact_flags(speak, base, True)
    f_fp = _artifact_flags(hold, base, True)
    r_off = sum(f_off) / max(1, len(f_off))
    r_on = sum(f_on) / max(1, len(f_on))
    r_fp = sum(f_fp) / max(1, len(f_fp))
    ok8 = r_off >= V4_ARTIFACT_MIN and r_off > 0.0
    ok_gate = r_on >= V4_ARTIFACT_MIN and r_fp <= 0.10
    ok = ok8 or ok_gate
    b0 = med([num(r, "beta") for r in rows_silent])
    b1 = med([num(r, "beta") for r in rows_speak])
    ratio = f"，β 中位数 {b0:.0f}→{b1:.0f}（{b1 / b0:.1f}×）" if b0 and b1 else ""
    detail = (f"引擎 8× 门拦截朗读帧 {r_off:.0%}；开 β+γ 门后 {r_on:.0%}"
              f"（静默段误报 {r_fp:.0%}，要求 ≤10%；阈值 ≥{V4_ARTIFACT_MIN:.0%}{ratio}）")
    if ok8:
        advice = "8× 门已能拦住说话肌电 → 保持 ARTIFACT_EMG_GATE=False，主任务无需额外门控"
    elif ok_gate:
        advice = ("8× 门对持续肌电饱和（只有首帧爆发超 8×），需启用 β+γ 专用门："
                  "把 state_engine.py 与任务一 js/engine.js 的 ARTIFACT_EMG_GATE 同时改 True"
                  "（两处必须一起改，verify_parity.py 会卡）")
    else:
        advice = ("两条门都不够。先看伪迹落在哪个频段（analyze_v4.py 会打印）："
                  "若 δ 独涨而 β/γ 平 → 是机械位移类低频伪迹，β+γ 门本来就无效，"
                  "不要动 ARTIFACT_RATIO（降回 6× 会毁掉 A 段定标），改为协议要求任务期间不发声；"
                  "若 β/γ 也不涨且各频段都没差异 → 受试者可能没真说话，重录说话段；"
                  "真·接触不良才查硬件")
    return ok, detail, advice


# --------------------------------------------------------------------------- V5
def v5_caffeine(rows_before: list[dict], rows_after: list[dict]) -> tuple[bool | None, str, str]:
    def stat(rows, key):
        return med([num(r, key) for r in rows])

    b_be, a_be = stat(rows_before, "beta"), stat(rows_after, "beta")
    b_hr, a_hr = stat(rows_before, "hr"), stat(rows_after, "hr")
    if None in (b_be, a_be, b_hr, a_hr):
        return None, "缺 beta/hr 列", "V5 为可选验证：缺列时跳过，不影响主实验"
    d_be = (a_be - b_be) / max(abs(b_be), 1e-9)
    d_hr = a_hr - b_hr
    flagged = d_be > V5_BETA_RISE_MIN and d_hr > V5_HR_RISE_MIN
    detail = f"β 变化 {d_be:+.0%}、HR 变化 {d_hr:+.1f} bpm（描述性，不做显著性判定）"
    advice = ("摄入后 β/HR 同升 → 建议在应用层提示「摄入咖啡时段判据降权」" if flagged
              else "未见明显响应 → 兴奋态判据维持现状，答辩标注「个体差异大、未独立验证」")
    return None, detail, advice   # V5 不作为通过/失败门禁


# --------------------------------------------------------------------------- demo
def _synth(kind: str, n: int = 120) -> list[dict]:
    import random
    rnd = random.Random(7)
    rows = []
    for i in range(n):
        if kind == "good":
            base = dict(theta=55, alpha=130, beta=100, delta=100, gamma=50,
                        attention=65, meditation=55, hr=75, rr_ms=820, signal=30)
        elif kind == "closed":
            base = dict(theta=100, alpha=170, beta=70, delta=150, gamma=45,
                        attention=55, meditation=72, hr=70, rr_ms=950, signal=30)
        elif kind == "open":
            base = dict(theta=55, alpha=95, beta=130, delta=100, gamma=50,
                        attention=65, meditation=55, hr=75, rr_ms=820, signal=30)
        elif kind == "speak":     # 说话肌电：β/γ 持续大幅同升（θ 基本不动），带帧间抖动
            base = dict(theta=60, alpha=120, beta=520 + 400 * rnd.random(),
                        delta=100, gamma=260 + 200 * rnd.random(),
                        attention=55, meditation=50, hr=80, rr_ms=800, signal=55)
        elif kind == "tense":     # 反例：β/γ 与 θ 一起升（下颌紧咬/整体唤醒），不应判肌电
            base = dict(theta=110 + 30 * rnd.random(), alpha=120,
                        beta=520 + 400 * rnd.random(), delta=100,
                        gamma=260 + 200 * rnd.random(),
                        attention=55, meditation=40, hr=88, rr_ms=760, signal=55)
        elif kind == "caffeine":
            base = dict(theta=58, alpha=120, beta=130, delta=100, gamma=55,
                        attention=62, meditation=52, hr=84, rr_ms=790, signal=30)
        elif kind == "offsig":
            # 反例：全程 signal=200（电极未接触皮肤）。旧口径 `s>=200 视为良好` 会把它判成
            # 100% 良好 —— 这条反例就是专门用来钉死方向性的。
            base = dict(theta=55, alpha=130, beta=100, delta=100, gamma=50,
                        attention=65, meditation=55, hr=75, rr_ms=820, signal=200)
        else:
            base = dict(theta=55, alpha=130, beta=100, delta=100, gamma=50,
                        attention=65, meditation=55, hr=75, rr_ms=820, signal=30)
        row = {k: (v * (1 + rnd.uniform(-0.03, 0.03)) if isinstance(v, (int, float)) and k not in ("signal",) else v)
               for k, v in base.items()}
        if kind == "rrloss" and i % 5 == 0:
            row["rr_ms"] = ""
        if kind == "rrsingle":
            # 反例：三列都在，但每批只落盘 1 个间期（模拟"onRR 只留最后一个"的旧缺陷）。
            # 此时丢失率 0%、范围 0%，弱断言全过，只有 RMSSD 可达性判据能抓到。
            row["rr1"], row["rr2"], row["rr3"] = row["rr_ms"], "", ""
        row["ts"] = i * 1.2
        rows.append({k: str(v) for k, v in row.items()})
    return rows


def run_demo() -> int:
    print("=== verify_hw 自检（合成数据；只验证测试逻辑，不代表真机结果）===")
    results = []
    results.append(("V3 signal 标定", *v3_signal(_synth("good"))))
    results.append(("V1 RR 质量", *v1_rr(_synth("good"))))
    results.append(("V2 δ 可测性", *v2_delta(_synth("closed"), _synth("open"))))
    results.append(("V4 说话肌电", *v4_emg(_synth("good"), _synth("speak"))))
    results.append(("V5 咖啡因(可选)", *v5_caffeine(_synth("good"), _synth("caffeine"))))
    # 反例：验证失败路径确实会 FAIL（否则测试等于没写）
    results.append(("反例·V3 signal 全 200（电极未接触皮肤）", *v3_signal(_synth("offsig"))))
    results.append(("反例·V1 丢 20% RR", *v1_rr(_synth("rrloss"))))
    results.append(("反例·V1 每批只存 1 个间期（RMSSD 应失效）", *v1_rr(_synth("rrsingle"))))
    results.append(("反例·V4 β/γ 与 θ 同升", *v4_emg(_synth("good"), _synth("tense"))))
    return _report(results, expect_fail=(5, 6, 7, 8))


# --------------------------------------------------------------------------- report
def _report(results: list[tuple], expect_fail: tuple[int, ...] | None = None) -> int:
    print()
    bad = 0
    for i, (name, ok, detail, advice) in enumerate(results):
        tag = "N/A " if ok is None else ("PASS" if ok else "FAIL")
        print(f"[{tag}] {name}")
        print(f"       指标：{detail}")
        print(f"       决策：{advice}")
        if ok is False and (expect_fail is None or i not in expect_fail):
            bad += 1
    print()
    if expect_fail:
        missed = [i for i in expect_fail if results[i][1] is not False]
        if missed:
            print(f"自检失败：反例 {missed} 未被判 FAIL —— 断言可能失效，请检查测试逻辑")
            return 1
        print(f"自检：{len(expect_fail)} 条反例均按预期 FAIL —— 断言有效")
        main = [r for i, r in enumerate(results) if i not in expect_fail]
        mb = sum(1 for r in main if r[1] is False)
        print(f"正例：{len(main) - mb}/{len(main)} 项通过")
        return 0 if mb == 0 else 1
    print(f"结论：{len(results) - bad}/{len(results)} 项通过"
          + ("（V5 为描述性，不计入通过率）" if any(r[1] is None for r in results) else ""))
    return 0 if bad == 0 else 1


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T4 真机五项验证（V3 前置）")
    ap.add_argument("--demo", action="store_true", help="合成数据自检（无需真机）")
    ap.add_argument("--v3", metavar="CSV", help="signal 档位标定")
    ap.add_argument("--v1", metavar="CSV", help="RR 间期质量")
    ap.add_argument("--v2-closed", dest="v2_closed", metavar="CSV", help="V2 闭眼段")
    ap.add_argument("--v2-open", dest="v2_open", metavar="CSV", help="V2 睁眼段")
    ap.add_argument("--v4-silent", dest="v4_silent", metavar="CSV", help="V4 静默段")
    ap.add_argument("--v4-speak", dest="v4_speak", metavar="CSV", help="V4 朗读段")
    ap.add_argument("--v5-before", dest="v5_before", metavar="CSV", help="V5 摄入前（可选）")
    ap.add_argument("--v5-after", dest="v5_after", metavar="CSV", help="V5 摄入后（可选）")
    ap.add_argument("--all", metavar="CSV", help="单文件跑 V3+V1（V2/V4 需成对数据）")
    args = ap.parse_args(argv)

    if args.demo or not any([args.v3, args.v1, args.v2_closed, args.v4_silent,
                             args.v5_before, args.all]):
        return run_demo()

    results: list[tuple] = []
    if args.all:
        rows = load_rows(args.all)
        results.append(("V3 signal 标定", *v3_signal(rows)))
        results.append(("V1 RR 质量", *v1_rr(rows)))
    if args.v3:
        results.append(("V3 signal 标定", *v3_signal(load_rows(args.v3))))
    if args.v1:
        results.append(("V1 RR 质量", *v1_rr(load_rows(args.v1))))
    if args.v2_closed and args.v2_open:
        results.append(("V2 δ 可测性", *v2_delta(load_rows(args.v2_closed), load_rows(args.v2_open))))
    if args.v4_silent and args.v4_speak:
        results.append(("V4 说话肌电", *v4_emg(load_rows(args.v4_silent), load_rows(args.v4_speak))))
    if args.v5_before and args.v5_after:
        results.append(("V5 咖啡因(可选)", *v5_caffeine(load_rows(args.v5_before), load_rows(args.v5_after))))
    return _report(results)


if __name__ == "__main__":
    sys.exit(main())
