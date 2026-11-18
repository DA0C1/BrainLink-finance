# -*- coding: utf-8 -*-
"""理智度三指标复算与对账（任务二 §1.1 / §1.2 / §1.3）

为什么需要这个脚本
------------------
任务二验收项 2.1 要求"3 指标各有公式且可由引擎 log 字段直接计算"。此前
只有公式推导，从没有人**拿真实落库数据算过一次**——`metrics()` 跑得出来，
但那是任务三自己的一套实现，等于自证。本脚本按方案文档原文**独立重算**，
再与平台 `session.json` 的 `metrics` 逐项对账：

  - 算得出且与平台一致  → 方案可复算成立，两套实现互证
  - 算得出但与平台不一致 → 有一方的口径实现偏了，必须查
  - 算不出             → 落库字段不足以支撑方案，属接口脱节

用法
----
    python rationality_report.py <session_dir>          # 单个会话
    python rationality_report.py --all <sessions_root>  # 批量（含组均值）
    python rationality_report.py <session_dir> --json   # 机器可读

数据来源（均为任务三平台落库文件，编码 utf-8-sig）
--------------------------------------------------
    decisions.csv      is_impulsive   → §1.1 冲动率
    states.csv         t / state      → §1.2 专注占比、§1.3 响应判定
    interventions.csv  t / state      → §1.3 干预响应率

注意：平台 CSV 带 BOM（为了 Excel 直接双击不乱码），读取必须用 utf-8-sig，
否则第一个列名会变成 '\\ufefft'。用 pandas 复算的人最容易在这里翻车。
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import sys
from pathlib import Path

# ---------------------------------------------------------------- 方案常量
# 全部来自任务二文档，不从平台 config 导入——导入就变成"用被测方的参数测被测方"
ATTR_WINDOW_S = 60.0      # §1.3 干预后观察窗口
MIN_HOLD_S = 5.0          # §1.3 有效响应须持续 ≥5s（防游戏化：瞬时抖动不计）
FOCUS_STATES = frozenset({"focus", "flow"})            # §1.2 计入专注态
IMPULSIVE_STATES = frozenset(                          # §1.1 五类非理智态
    {"stress", "anxiety", "distracted", "fatigue", "drowsy"}
)
NEUTRAL = "neutral"

# 仅用于"复刻平台口径"以解释差异，不作为判定依据
PLATFORM_WARMUP_S = 10.0
PLATFORM_TICK_S = 2.0
PLATFORM_COOL_DOWN_S = 60.0   # 引擎 COOL_DOWN，用于 §1.3 冷却期去重


# ---------------------------------------------------------------- 读取
def _read_csv(path: Path) -> list[dict]:
    """读平台 CSV。utf-8-sig 吃掉 BOM；缺文件返回空表而不是抛异常。"""
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as fh:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k}
                for row in csv.DictReader(fh)]


def _f(v, default=None):
    try:
        x = float(v)
        return x if x == x and abs(x) != float("inf") else default
    except (TypeError, ValueError):
        return default


def _b(v) -> bool:
    """CSV 里的布尔：平台写 '1'/'0'，也容忍 True/true。"""
    return str(v).strip().lower() in ("1", "true", "yes")


# ---------------------------------------------------------------- §1.2
def _frame_durations(rows: list[dict]) -> list[float]:
    """每帧代表的时长 = 下一帧 ts − 当前帧 ts；末帧取中位间隔。

    文档 §1.2 明确"一律按时间戳差计算"，不能用帧数乘固定间隔：
    真机 1.2–1.37s/帧 不恒定，按帧数算会有系统性偏差。
    间隔超过中位数 3 倍视为会话中断，不把中断时长计入任何状态。
    """
    ts = [_f(r.get("t")) for r in rows]
    ts = [x for x in ts if x is not None]
    if len(ts) < 2:
        return [PLATFORM_TICK_S] * len(rows)
    gaps = [b - a for a, b in zip(ts, ts[1:]) if b > a]
    med = statistics.median(gaps) if gaps else PLATFORM_TICK_S
    cap = max(med * 3.0, med)
    out = []
    for i, r in enumerate(rows):
        t = _f(r.get("t"))
        nt = _f(rows[i + 1].get("t")) if i + 1 < len(rows) else None
        if t is None:
            out.append(0.0)
        elif nt is None or nt <= t:
            out.append(0.0 if nt is not None else med)
        else:
            out.append(min(nt - t, cap))
    return out


def focus_ratio_doc(states: list[dict]) -> tuple[float | None, dict]:
    """§1.2 专注态占比：Σdur(focus/flow) ÷（总时长 − neutral 时长）。"""
    if not states:
        return None, {"valid_s": 0.0, "focus_s": 0.0}
    durs = _frame_durations(states)
    focus_s = 0.0
    neutral_s = 0.0
    total_s = 0.0
    for r, d in zip(states, durs):
        st = r.get("state", "")
        total_s += d
        if st == NEUTRAL:
            neutral_s += d
        elif st in FOCUS_STATES:
            focus_s += d
    valid = total_s - neutral_s
    if valid <= 0:
        return None, {"valid_s": 0.0, "focus_s": focus_s}
    return focus_s / valid, {"valid_s": valid, "focus_s": focus_s, "total_s": total_s}


def focus_ratio_platform(states: list[dict]) -> float | None:
    """复刻平台 metrics()：帧数比，且剔除预热期。用于解释差异来源。"""
    valid = [r for r in states
             if (_f(r.get("tick"), 0.0) * PLATFORM_TICK_S) >= PLATFORM_WARMUP_S
             and r.get("state") != NEUTRAL]
    if not valid:
        return None
    return sum(1 for r in valid if r.get("state") in FOCUS_STATES) / len(valid)


# ---------------------------------------------------------------- §1.3
def response_rate_doc(states: list[dict], interventions: list[dict]) -> tuple[float | None, list]:
    """§1.3 干预响应率。

    有效响应 = 干预后 60s 内出现「非冲动且非 neutral」的连续段，且该段
    持续 ≥5s（文档原文的防游戏化条款）。

    冷却期去重：同一目标状态在 COOL_DOWN 窗口内的重复干预只计第一次。
    平台侧不显式实现这条，因为它依赖引擎 feedback_policy 自身不重复触发——
    这里仍然实现，用于验证"引擎确实没重复触发"这个前提成立。
    """
    if not interventions:
        return None, []
    durs = _frame_durations(states)
    detail = []
    last_hit: dict[str, float] = {}
    counted = 0
    for iv in sorted(interventions, key=lambda r: _f(r.get("t"), 0.0)):
        t0 = _f(iv.get("t"))
        st = iv.get("state", "")
        if t0 is None:
            continue
        # 冷却期去重
        prev = last_hit.get(st)
        if prev is not None and t0 - prev < PLATFORM_COOL_DOWN_S:
            detail.append({"t": t0, "state": st, "ok": None, "why": "cool-down 内重复干预，不计数"})
            continue
        hit = False
        run = 0.0
        for r, d in zip(states, durs):
            t = _f(r.get("t"))
            if t is None:
                continue
            in_win = t0 < t <= t0 + ATTR_WINDOW_S
            good = (not _b(r.get("is_impulsive"))) and r.get("state") != NEUTRAL
            if in_win and good:
                run += d
                if run >= MIN_HOLD_S:
                    hit = True
                    break
            else:
                run = 0.0
        if hit:
            counted += 1
            last_hit[st] = t0
        detail.append({"t": t0, "state": st, "ok": hit,
                       "why": "窗口内出现 ≥5s 平稳段" if hit else "60s 内无 ≥5s 平稳段"})
    return counted / len(interventions), detail


# ------------------------------------------------------- §2.4 自然回落率
STABLE_STATES = frozenset({"focus", "relaxed"})


def _runs(states: list[dict]) -> list[dict]:
    """把逐帧状态序列压成「同状态连续段」，每段带真实时长（ts 差分）。"""
    durs = _frame_durations(states)
    runs = []
    for r, d in zip(states, durs):
        st = r.get("state", "")
        t = _f(r.get("t"))
        if t is None:
            continue
        if runs and runs[-1]["state"] == st:
            runs[-1]["end"] = t
            runs[-1]["dur"] += d
        else:
            runs.append({"state": st, "start": t, "end": t, "dur": d})
    return runs


def natural_recovery_rate(states: list[dict]) -> tuple[float | None, list]:
    """§2.4 自然回落率（无反馈轮专用）。

    口径（文档原文）：无反馈轮中，负向状态在**无任何干预**下自发退出至平稳态
    （focus/relaxed）并持续 ≥5s 的比例；分母 = 负向状态总出现次数，观察窗口 60s。

    为什么要单独算：干预响应率只在有反馈轮有定义，没有对照就分不清"干预有效"
    和"状态本来就会自己回落"。这一段此前平台没有产出，属于 §2.4 第 3 条的空缺。

    实现要点：先按状态变化切段，再对每个负向段起点开 60s 窗口，找窗口内
    出现 ≥5s 的 focus/relaxed 连续段。与响应率用同一窗口、同一 ≥5s 阈值，
    两侧才可比。
    """
    runs = _runs(states)
    neg = [r for r in runs if r["state"] in IMPULSIVE_STATES]
    if not neg:
        return None, []
    hit = 0
    detail = []
    for r in neg:
        t0 = r["start"]
        ok = False
        for q in runs:
            if q["state"] in STABLE_STATES and q["start"] > t0 \
                    and q["start"] <= t0 + ATTR_WINDOW_S and q["dur"] >= MIN_HOLD_S:
                ok = True
                break
        if ok:
            hit += 1
        detail.append({"state": r["state"], "t": t0, "dur": r["dur"], "ok": ok})
    return hit / len(neg), detail


# ---------------------------------------------------------------- §1.1
def calmness_doc(decisions: list[dict]) -> tuple[float | None, float | None, int, int]:
    """§1.1 冷静度。

    返回 (文档口径, 平台口径, 总数, 冲动数)。
    两口径在 total=0 时不同：文档写"默认记 1.0"，平台给 None。
    文档那条在统计上会把"没下单"当成"最冷静"，拉高组均值——见 main() 的告警。
    """
    total = len(decisions)
    imp = sum(1 for d in decisions if _b(d.get("is_impulsive")))
    if total == 0:
        return 1.0, None, 0, 0
    return 1.0 - imp / total, 1.0 - imp / total, total, imp


# ---------------------------------------------------------------- 汇总
def analyze(session_dir: Path) -> dict:
    decisions = _read_csv(session_dir / "decisions.csv")
    states = _read_csv(session_dir / "states.csv")
    ivs = _read_csv(session_dir / "interventions.csv")

    calm_doc, calm_pf, total, imp = calmness_doc(decisions)
    focus_doc, focus_aux = focus_ratio_doc(states)
    focus_pf = focus_ratio_platform(states)
    resp_doc, resp_detail = response_rate_doc(states, ivs)
    recov, recov_detail = natural_recovery_rate(states)

    meta = {}
    sj = session_dir / "session.json"
    if sj.exists():
        try:
            meta = json.loads(sj.read_text(encoding="utf-8"))
        except Exception:
            meta = {}
    pm = meta.get("metrics") or {}

    checks = []

    def chk(name, mine, theirs, tol=1e-3):
        if mine is None and theirs is None:
            checks.append((name, "OK", "两侧均为空（数据不足）", True))
        elif mine is None or theirs is None:
            checks.append((name, "差异", f"复算={mine} / 平台={theirs}（一侧为空）", False))
        else:
            ok = abs(float(mine) - float(theirs)) <= tol
            checks.append((name, "OK" if ok else "差异",
                           f"复算={float(mine):.4f} / 平台={float(theirs):.4f}", ok))

    chk("calmness(§1.1)", calm_pf, pm.get("calmness"))
    chk("focus_ratio(§1.2)", focus_doc, pm.get("focus_ratio"), tol=0.02)
    chk("response_rate(§1.3)", resp_doc, pm.get("intervention_response_rate"))
    chk("interventions 计数", float(len(ivs)), _f(pm.get("interventions")))
    chk("decisions 计数", float(total), _f(pm.get("decisions")))

    return {
        "session": session_dir.name,
        "subject": meta.get("subject_id"),
        "round": meta.get("round"),
        "n": {"decisions": total, "states": len(states), "interventions": len(ivs)},
        "calmness": {"doc": calm_doc, "platform": calm_pf, "total": total, "impulsive": imp},
        "focus_ratio": {"doc": focus_doc, "platform": focus_pf, **focus_aux},
        "response_rate": {"doc": resp_doc, "detail": resp_detail},
        "recovery_rate": {"doc": recov, "detail": recov_detail},
        "feedback": meta.get("round"),
        "checks": checks,
        "notes": _notes(total, states, ivs, resp_detail),
    }


def _notes(total, states, ivs, resp_detail) -> list[str]:
    """把"为什么这个指标算不出来/不可信"讲清楚，而不是给个 null 了事。"""
    out = []
    if total == 0:
        out.append("本轮无交易决策 → calmness 文档口径记 1.0、平台口径为 None；"
                   "两者都不可用于组均值（见报告总告警）")
    if not states:
        out.append("states.csv 为空 → §1.2/§1.3 无法计算")
    if not ivs:
        out.append("本轮无干预 → 响应率无定义（无反馈轮本就应为 0 条提醒，属预期）")
    dup = [d for d in resp_detail if d["ok"] is None]
    if dup:
        out.append(f"{len(dup)} 次干预落在冷却期内（引擎应已拦截，出现即说明门控失效）")
    return out


def _fmt(v, pct=True):
    if v is None:
        return "  —  "
    return f"{v * 100:5.1f}%" if pct else f"{v:.4f}"


def _selftest() -> int:
    """合成数据自检：只验证本脚本的判据实现，不代表任何真实实验结果。

    每项都配一条反例——没有反例的断言等于没写（本项目历史上已踩过：
    断言写反了，测试全绿而行为错）。
    """
    def mk(spec, dt=2.0):
        """spec = [(state, n_frames), ...] → 逐帧状态行（ts 按 dt 递增）。"""
        rows, t = [], 1_000_000.0
        for st, n in spec:
            for _ in range(n):
                rows.append({"t": f"{t:.3f}", "tick": str(len(rows)),
                             "state": st, "is_impulsive": "1" if st in IMPULSIVE_STATES else "0"})
                t += dt
        return rows

    res = []

    def case(name, got, want):
        ok = abs(got - want) < 1e-6
        res.append((name, ok, f"得 {got if got is None else round(got, 4)} / 期望 {want}"))

    # J1 自然回落：负向 10 帧 → focus 5 帧（10s）→ 应 100%
    case("J1 负向→平稳 10s", natural_recovery_rate(
        mk([("distracted", 10), ("focus", 5)]))[0] or 0.0, 1.0)
    # J2 反例：平稳只持续 4s（窗口内 2 帧 × 2s）→ 不构成回落
    case("J2 反例·平稳仅 4s", natural_recovery_rate(
        mk([("distracted", 10), ("focus", 2)]))[0] or 0.0, 0.0)
    # J3 反例：一直负向 → 0
    case("J3 反例·持续负向", natural_recovery_rate(
        mk([("anxiety", 30)]))[0] or 0.0, 0.0)
    # J4 反例：唯一负向段持续 80s，平稳段落在 60s 观察窗之外 → 不算回落
    #   注意该用例只能有 1 个负向段：负向态接负向态时按"两段独立计数"（见 _runs 口径），
    #   混入第二个负向段会让它自己的窗口命中，反而算 0.5。
    case("J4 反例·窗口外回落", natural_recovery_rate(
        mk([("fatigue", 40), ("focus", 10)]))[0] or 0.0, 0.0)
    # J5 两个负向段命中 1 个 → 0.5
    case("J5 命中率 1/2", natural_recovery_rate(
        mk([("stress", 5), ("focus", 5), ("drowsy", 30)]))[0] or 0.0, 0.5)

    # K1 专注占比按 ts 差分：1.2s/帧（真机节奏），focus 10 帧 / 有效 20 帧 → 0.5
    case("K1 真机 1.2s/帧专注占比", focus_ratio_doc(
        mk([("focus", 10), ("distracted", 10)], dt=1.2))[0] or 0.0, 0.5)
    # K2 反例：neutral 必须从分母扣除（focus 10 + neutral 10 → 100%，不是 50%）
    case("K2 neutral 从分母扣除", focus_ratio_doc(
        mk([("focus", 10), ("neutral", 10)]))[0] or 0.0, 1.0)

    # I1 calmness：3 笔里 1 笔冲动 → 2/3
    dec = [{"is_impulsive": "0"}, {"is_impulsive": "1"}, {"is_impulsive": "0"}]
    case("I1 冷静度 2/3", calmness_doc(dec)[1] or 0.0, 2 / 3)
    # I2 无决策：文档口径 1.0、平台口径 None（这是两口径已知的唯一分歧）
    d0, p0, _, _ = calmness_doc([])
    res.append(("I2 无决策两口径分歧", d0 == 1.0 and p0 is None,
                f"文档={d0} / 平台={p0}（期望 1.0 / None）"))

    print("=== rationality_report 自检（合成数据，仅验证判据实现）===")
    bad = 0
    for name, ok, text in res:
        print(f"[{'PASS' if ok else 'FAIL'}] {name:<24} {text}")
        if not ok:
            bad += 1
    print()
    print(f"结果：{len(res) - bad}/{len(res)} 通过")
    return 0 if bad == 0 else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="理智度三指标复算与对账")
    ap.add_argument("path", nargs="?", help="会话目录，或配合 --all 指定 sessions 根目录")
    ap.add_argument("--all", action="store_true", help="批量处理根目录下所有会话")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    ap.add_argument("--selftest", action="store_true", help="用合成数据自检判据实现")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if not args.path:
        ap.error("需要 path（或使用 --selftest）")

    root = Path(args.path)
    dirs = sorted(d for d in root.iterdir()
                  if d.is_dir() and (d / "session.json").exists()) if args.all else [root]
    if not dirs or not dirs[0].exists():
        print(f"FAIL 路径不存在或无可分析会话：{root}", file=sys.stderr)
        return 2

    reports = [analyze(d) for d in dirs]
    if args.json:
        print(json.dumps(reports, ensure_ascii=False, indent=2))
        return 0

    print("=" * 78)
    print("理智度三指标复算（任务二 §1.1/§1.2/§1.3 独立实现）")
    print("=" * 78)
    for r in reports:
        print(f"\n■ 会话 {r['session']}  被试={r['subject']}  轮次={r['round']}  "
              f"决策{r['n']['decisions']} / 状态帧{r['n']['states']} / 干预{r['n']['interventions']}")
        c = r["calmness"]
        print(f"  §1.1 冷静度     复算 {_fmt(c['platform'])}   "
              f"(冲动 {c['impulsive']}/{c['total']})"
              + ("   [无决策：文档口径记 1.0，平台记空]" if c["total"] == 0 else ""))
        f = r["focus_ratio"]
        print(f"  §1.2 专注占比   复算 {_fmt(f['doc'])}   平台 {_fmt(f['platform'])}   "
              f"(有效 {f['valid_s']:.1f}s / 其中专注 {f['focus_s']:.1f}s)")
        v = r["response_rate"]
        print(f"  §1.3 响应率     复算 {_fmt(v['doc'])}")
        for d in v["detail"]:
            tag = "命中" if d["ok"] else ("跳过" if d["ok"] is None else "未命中")
            print(f"        · {d['state']:<10} {tag}  {d['why']}")
        g = r["recovery_rate"]
        print(f"  §2.4 自然回落率 {_fmt(g['doc'])}   "
              f"(负向段 {len(g['detail'])} 个"
              + ("，无干预轮对照口径" if r["feedback"] == "无反馈" else "，有反馈轮仅供参考") + ")")
        print("  对账：")
        for name, verdict, text, _ok in r["checks"]:
            print(f"    [{'✔' if verdict == 'OK' else '✘'}] {name:<18} {text}")
        if r["notes"]:
            print("  说明：")
            for n in r["notes"]:
                print(f"    - {n}")

    if args.all and len(reports) > 1:
        print("\n" + "=" * 78)
        print("组汇总（只统计有定义的轮次；无决策/无干预的轮次不进分母）")
        print("=" * 78)
        for key, label in (("calmness", "§1.1 冷静度"), ("focus_ratio", "§1.2 专注占比"),
                           ("response_rate", "§1.3 响应率")):
            if key == "calmness":
                vals = [r["calmness"]["platform"] for r in reports
                        if r["calmness"]["total"] > 0]
            else:
                vals = [r[key]["doc"] for r in reports]
                vals = [v for v in vals if v is not None]
            if not vals:
                print(f"  {label}: 无有效轮次")
                continue
            mean = sum(vals) / len(vals)
            sd = statistics.stdev(vals) if len(vals) > 1 else 0.0
            print(f"  {label}: n={len(vals)}  mean={mean * 100:.1f}%  sd={sd * 100:.1f}pp")

        # §2.4 第 3 条：剥离干预的真实效果——有反馈轮响应率 vs 无反馈轮自然回落率
        def _mean(vals):
            vals = [v for v in vals if v is not None]
            return (sum(vals) / len(vals)) if vals else None

        fb = _mean([r["response_rate"]["doc"] for r in reports
                    if r["feedback"] == "有反馈"])
        nf = _mean([r["recovery_rate"]["doc"] for r in reports
                    if r["feedback"] == "无反馈"])
        print("\n  干预净效应（§2.4 第 3 条）")
        print(f"    有反馈轮 响应率   {_fmt(fb)}")
        print(f"    无反馈轮 自然回落 {_fmt(nf)}")
        if fb is not None and nf is not None:
            print(f"    净效应 = {fb * 100:.1f} − {nf * 100:.1f} = {(fb - nf) * 100:+.1f}pp"
                  + ("  → 干预带来额外改善" if fb - nf > 0 else
                     "  → 未见额外改善，改善可能来自自然回落"))
        else:
            print("    缺一侧数据（需有反馈轮与无反馈轮各至少一轮）→ 暂不可算")

    bad = [c for r in reports for c in r["checks"] if not c[3]]
    print()
    if bad:
        print(f"结论：{len(bad)} 项对账存在差异 —— 复算与平台实现不一致，需查口径：")
        for name, _, text, _ in bad:
            print(f"  · {name}: {text}")
        return 1
    print("结论：三指标全部可复算，且与平台 metrics 逐项一致。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
