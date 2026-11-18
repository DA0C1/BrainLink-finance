# -*- coding: utf-8 -*-
"""analyze_real.py — 真机 dump 离线分析（采集一次、分析多次）

消费 collect_real.py 产出的 JSONL：
1. 可用性报告：8 频段/信号/RR/心率/陀螺仪 到底收到了哪些
   （实证回答「设备推没推 RR」——阶段 B 清单 §四 的开放问题）
2. 校准：从 D2 A/B/C 段 calibrate_from_frames → 基线 + calibration_report（ok/警告/θ_ref 来源）
3. 回放：全量帧按真实时间线经 SdkAdapter→StateEngine 回放（与在线同代码路径），
   逐段统计 状态分布 / 平均 conf / 伪迹率 / 接触率 / RMSSD（无 RR 则 NaN）
4. 双引擎 parity（--parity）：真实轨迹同时喂 py/js 引擎，比对状态序列是否一致

用法：
  python analyze_real.py data/real_sub01_*.jsonl
  python analyze_real.py data/real_sub01_*.jsonl --parity
  python analyze_real.py data/real_sub01_*.jsonl --signal-max 42
"""
from __future__ import annotations

import argparse
import collections
import json
import os
import subprocess
import sys
import tempfile
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import state_engine as se
from sdk_adapter import SdkAdapter
import verify_parity as vp


# ----------------------------------------------------------------- 载入与切分
def load(path: str) -> list[dict]:
    """读 JSONL，返回事件列表（按写入顺序即时间序）。"""
    events: list[dict] = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                events.append(json.loads(line))
    events.sort(key=lambda e: e.get("t", 0))
    return events


def build_segments(events: list[dict]) -> dict:
    """由 marker 行切分；返回 {label: {'eeg':[ev...], 'open':bool}}。"""
    segs: "dict[str, dict]" = {}
    open_label: str | None = None
    for ev in events:
        if ev.get("type") != "marker":
            if open_label is not None:
                segs[open_label].setdefault("ev", []).append(ev)
            continue
        label = ev["label"]
        if label.endswith("_END"):
            base = label[:-4]
            if base in segs:
                segs[base]["open"] = False
            open_label = None
        else:
            if open_label is not None and segs[open_label].get("open"):
                segs[open_label]["open"] = False
            segs[label] = {"ev": [], "open": True}
            open_label = label
    return segs


# ----------------------------------------------------------------- 帧构造
def frame_kwargs(ev: dict) -> dict:
    """把 eeg 事件合成「合并后」的帧口径字典（alpha=low+high 等）。"""
    return {
        "theta": ev["theta"], "delta": ev["delta"],
        "alpha": ev["lowAlpha"] + ev["highAlpha"],
        "beta": ev["lowBeta"] + ev["highBeta"],
        "gamma": ev["lowGamma"] + ev["highGamma"],
        "attention": ev["attention"], "meditation": ev["meditation"],
        "signal": ev["signal"],
    }


def py_frame(kw: dict, ts: float | None = None):
    """构造 Python Frame（校准/回放用）。

    ts 必须传**秒**（引擎窗口常量 WINDOW_EEG_S=10 的单位就是秒）。
    旧版不传 ts → `process_frame` 回退 `self._clock()`（wall-clock），
    连续帧的 dt≈0 → 疲劳积分 I_f 的衰减/累积每次运行都不同
    → 同一份 dump 连跑三次得到 drowsy 117/121/120 三个答案（实测）。
    """
    return se.Frame(theta=kw["theta"], delta=kw["delta"], alpha=kw["alpha"],
                    beta=kw["beta"], gamma=kw["gamma"],
                    attention=kw["attention"], meditation=kw["meditation"],
                    rr_ms=None, ts=ts)


# ----------------------------------------------------------------- 可用性与校准
def availability(events: list[dict]) -> dict:
    c = collections.Counter(e["type"] for e in events if e["type"] != "marker")
    return dict(c)


def run_calibration(segs: dict):
    """从 D2_A/B/C 段构造校准帧；返回 (base, report, calib_tuples)。"""
    tag_map = {"D2_A": "A", "D2_B": "B", "D2_C": "C"}
    tuples: list = []
    for label, tag in tag_map.items():
        seg = segs.get(label)
        if not seg:
            print(f"  ⚠ 未找到校准段 {label}（本 dump 无 D2 预设标记）")
            continue
        rr_events = [ev["rr"] for ev in seg.get("ev", []) if ev["type"] == "rr"]
        for i, ev in enumerate(seg.get("ev", [])):
            if ev["type"] != "eeg":
                continue
            if ev["signal"] >= se.SIGNAL_BAD:
                continue
            fr = py_frame(frame_kwargs(ev), ts=ev["t"] / 1000.0)
            if rr_events:
                # 把同段 RR 三值按序关联到校准帧（与运行时 rr_batch 同语义）
                triple = rr_events[i % len(rr_events)]
                fr.rr_batch = tuple(float(x) for x in triple)
            tuples.append((tag, fr))
    if not tuples:
        print("  · 无校准帧 → 用 calibrate_from_mock([]) 默认基线（仅演示，非正式结论）")
        base = se.StateEngine.calibrate_from_mock([])
        return base, None, tuples
    base = se.StateEngine.calibrate_from_frames(tuples)
    report = se.StateEngine.calibration_report(tuples, base)
    return base, report, tuples


def print_calibration(report: dict | None) -> None:
    # 注：原第二参数 rr_available 在 v3.6 拆出 hrv_ok 后已无消费方，删掉（死参数）。
    if report is None:
        return
    print("\n===== 校准报告（D2 A/B/C）=====")
    print(f"  段样本量      : {report['segments']}（clean {report['segments_clean']}）")
    # v3.6：ok 已收窄为「EEG 基线可用」；HRV 能力单独由 hrv_ok 表达。
    # 旧的 sole_rr 猜测逻辑废弃——它要求"所有告警都与 RMSSD 相关"，一旦同时出现
    # C 段锚点告警就退化成"不通过 → 重戴/重采"，而这两件事重戴都解决不了。
    ok_txt = "✓ EEG 基线可用（7 态）" if report["ok"] else "✗ 不通过 → 重戴/重采"
    if report["ok"] and not report.get("hrv_ok", True):
        ok_txt += "；HRV 不可用（无 RR → stress 不可判，预期降级）"
    print(f"  ok            : {ok_txt}")
    print(f"  θ_ref 来源    : {report['theta_ref_src']}  θ_ref={report['theta_ref']}")
    print(f"  伪迹帧占比    : {report['artifact_ratio']:.1%}"
          f"（阈值 {se.CALIB_ARTIFACT_MAX:.0%} → "
          f"{'合格' if report['artifact_ratio'] <= se.CALIB_ARTIFACT_MAX else '超限'}）")
    print(f"  接触无效占比  : {report['contact_ratio']:.1%}（阈值 10% → "
          f"{'合格' if report['contact_ratio'] <= 0.10 else '超限，查电极贴合'}）")
    pairs = report.get("rmssd_pairs")
    if pairs is not None:
        txt = f"{pairs}（<{se.RMSSD_MIN_PAIRS} → HRV 判据不可用）" if pairs < se.RMSSD_MIN_PAIRS \
            else f"{pairs}（可用）"
        print(f"  RMSSD 差分对  : {txt}")
    cz = report.get("c_anchor_z")
    if cz is not None:
        # v3.6：原来无条件拼「（<1.5 放松态不可判）」，z=10 也照说不误——文案说谎。
        verdict = "放松态可判" if cz >= se.Z_RELAX_A else "放松态不可判"
        print(f"  C 段放松锚 z  : {cz:.2f}（门控 {se.Z_RELAX_A} → {verdict}）")
    for w in report["warnings"]:
        print(f"  ⚠ {w}")


# ----------------------------------------------------------------- 回放
def replay(events: list[dict], base: dict, signal_max: int) -> dict:
    """全量帧按时间线经 SdkAdapter→StateEngine 回放；返回每段决策序列。"""
    engine = se.StateEngine(baseline=base)
    adapter = SdkAdapter(engine, signal_max=signal_max, ctx={"task_active": True})
    open_label: str | None = None
    seg_states: "dict[str, list]" = {}
    for ev in events:
        if ev.get("type") == "marker":
            label = ev["label"]
            if label.endswith("_END"):
                open_label = None
            else:
                open_label = label
            continue
        if ev["type"] == "eeg":
            obj = SimpleNamespace(
                signal=ev["signal"], attention=ev["attention"], meditation=ev["meditation"],
                delta=ev["delta"], theta=ev["theta"],
                lowAlpha=ev["lowAlpha"], highAlpha=ev["highAlpha"],
                lowBeta=ev["lowBeta"], highBeta=ev["highBeta"],
                lowGamma=ev["lowGamma"], highGamma=ev["highGamma"],
                ts=ev["t"] / 1000.0,      # 毫秒 → 秒（引擎窗口单位）；见 py_frame 注释
            )
            adapter.onEEG(obj)
            lab = open_label or "unseg"
            seg_states.setdefault(lab, []).append(
                (engine.last_decision.state, engine.last_decision.conf,
                 engine.last_decision.reason))
        elif ev["type"] == "extend":
            adapter.onExtendEEG(SimpleNamespace(heart=ev.get("heart")))
        elif ev["type"] == "rr":
            adapter.onRR(*ev["rr"])
    feats = engine._extract()
    return {"seg_states": seg_states, "rmssd": getattr(feats, "RMSSD_60", float("nan")),
            "hr": getattr(feats, "HR", float("nan"))}


def print_replay(result: dict) -> None:
    print("\n===== 回放逐段统计 =====")
    for lab, states in result["seg_states"].items():
        if lab.endswith("_END"):
            continue
        dist = collections.Counter(s for s, _, _ in states)
        confs = [c for _, c, _ in states if c is not None]
        rej = collections.Counter(r for _, _, r in states if r)
        mean_conf = sum(confs) / len(confs) if confs else 0.0
        total = len(states)
        art = rej.get("artifact", 0)
        con = rej.get("contact", 0)
        top = dist.most_common(3)
        print(f"  [{lab}] 帧={total}  主导态={top[0][0] if top else '?'}({top[0][1] if top else 0})"
              f"  均conf={mean_conf:.2f}")
        print(f"        状态分布: " + ", ".join(f"{k}×{v}" for k, v in top))
        if art or con:
            print(f"        伪迹/接触未判帧: {art}/{con}（占总 {art/total:.0%}/{con/total:.0%}）")
    print(f"\n  全会话 RMSSD_60 = {result['rmssd']:.2f} ms"
          f"   HR = {result['hr']:.1f} bpm"
          if (result['rmssd'] == result['rmssd'] and result['hr'] == result['hr'])
          else "  RMSSD/HR = NaN（无 RR → HRV 判据不可用，符合纯 Python 路线）")


# ----------------------------------------------------------------- 双引擎 parity（真实轨迹）
# 设计：完全对齐 verify_parity 的 _diff_py.py / _diff_js.js 已验证范式——
#   · 两侧都用「合并后」的 Frame（alpha=low+high 合成单值），不依赖 SdkAdapter；
#   · 两侧都用真实校准基线（calibrate_from_frames / calibrateFromFrames），无校准则 defaultBaseline；
#   · 两侧都直接 process_frame(frame,{task_active:true})，逐帧比对 state；
#   · 两侧都带真实 signal（poorSignalLevel），统一走引擎内置 SIGNAL_BAD 接触门。
# 这样比对的是「两套核心决策逻辑」是否在同一真实轨迹上一致，而非适配器差异。
# 实现：写成临时 .js 文件（而非 node -e），payload 走临时 .json 文件，
#       既绕开 -e 的单行限制/截断，也绕开 Windows 命令行长度上限（真机 dump 帧多）。
JS_HARNESS = """const fs=require('fs'),vm=require('vm');
global.window=global;
vm.runInThisContext(fs.readFileSync(process.argv[2],'utf8'));
const {StateEngine,Frame}=global.BrainState;
const [calibJson,taskJson]=JSON.parse(fs.readFileSync(process.argv[3],'utf8'));
const base=(calibJson&&calibJson.length)
  ? StateEngine.calibrateFromFrames(calibJson.map(([t,o])=>({tag:t,frame:new Frame(o)})))
  : StateEngine.defaultBaseline();
const eng=new StateEngine({baseline:base});
const states=[];
for(const o of taskJson){
  const f=new Frame({theta:o.theta,delta:o.delta,alpha:o.alpha,beta:o.beta,gamma:o.gamma,
    attention:o.attention,meditation:o.meditation,signal:o.signal,rr_ms:o.rr_ms,ts:o.ts,
    gyro:(o.gyro||[0.01,0.01,0.01])});
  const out=eng.processFrame(f,{task_active:true});
  const dec=(out&&out.decision)?out.decision:null;
  states.push(dec?dec.state:null); }
process.stdout.write(JSON.stringify(states));
"""


def parity_on_real(segs: dict, signal_max: int, calib_tuples: list,
                   events: list | None = None) -> None:
    """把真实轨迹同时喂 py/js 引擎，比对状态序列（双引擎一致性 on real data）。

    calib_tuples：来自 run_calibration 的真实校准帧（(tag, Frame) 列表），
    两侧都用它构造同一基线；为空则用默认基线（仅演示，非正式结论）。

    events：原始事件流（已按 t 排序）。**必须传**——否则落在 marker 段之外的帧
    （unseg）会被漏掉。2026-11-09 真机首采正是这种情况：241 帧全在 D2_C_END 之后，
    旧版按 segs 遍历 → task_frames 为空 → parity 直接跳过，唯一可用的真实数据白白浪费。
    """
    node = vp.find_node(None)
    if not node:
        print("\n[parity] 未找到 node，跳过双引擎比对（引擎口径闸门仍见 verify_parity.py）。")
        return
    calib_labels = {"D2_A", "D2_B", "D2_C"}
    # 校准段内的 eeg 只用于建基线，不参与任务轨迹
    calib_ids = {id(ev) for lb in calib_labels if lb in segs
                 for ev in segs[lb].get("ev", [])}
    # 任务帧：按时间顺序取所有非校准段的 eeg（含 unseg），合成合并口径 + 真实 signal
    task_frames: list[dict] = []
    if events is not None:
        ordered = [e for e in events
                   if e.get("type") == "eeg" and id(e) not in calib_ids]
    else:                                  # 兼容旧调用：只遍历已知段
        ordered = [e for lb, seg in segs.items()
                   if not lb.endswith("_END") and lb not in calib_labels
                   for e in seg.get("ev", []) if e.get("type") == "eeg"]
    for ev in ordered:
        kw = frame_kwargs(ev)
        task_frames.append({
            "theta": kw["theta"], "delta": kw["delta"], "alpha": kw["alpha"],
            "beta": kw["beta"], "gamma": kw["gamma"],
            "attention": kw["attention"], "meditation": kw["meditation"],
            "signal": ev["signal"], "rr_ms": None,
            # dump 的 t 是毫秒，引擎窗口是秒 → 必须 /1000。
            # 旧版直接传毫秒：`ts - buf[0].ts > 10` 恒真 → 10s 窗退化为 1 帧，
            # 两侧同样退化所以 parity 看不出来（对称的错），但判别力被悄悄削弱。
            "ts": ev["t"] / 1000.0,
            "gyro": [0.01, 0.01, 0.01],
        })
    if not task_frames:
        print("\n[parity] 无任务段（只有校准帧），跳过。")
        return
    # py 侧：真实校准基线 + 逐帧 process_frame（与 _diff_py.py 同源驱动）
    base = (se.StateEngine.calibrate_from_frames(calib_tuples)
            if calib_tuples else se.StateEngine.calibrate_from_mock([]))
    py_eng = se.StateEngine(baseline=base)
    py_states = []
    for o in task_frames:
        f = se.Frame(theta=o["theta"], delta=o["delta"], alpha=o["alpha"], beta=o["beta"],
                     gamma=o["gamma"], attention=o["attention"], meditation=o["meditation"],
                     signal=o["signal"], rr_ms=None, ts=o["ts"], gyro=[0.01, 0.01, 0.01])
        dec, _ = py_eng.process_frame(f, {"task_active": True})
        py_states.append(dec.state)
    # js 侧：序列化校准帧（与 Python 同源）+ 任务帧，走引擎
    calib_json = []
    for tag, fr in calib_tuples:
        o = {"theta": fr.theta, "delta": fr.delta, "alpha": fr.alpha, "beta": fr.beta,
             "gamma": fr.gamma, "attention": fr.attention, "meditation": fr.meditation,
             "signal": getattr(fr, "signal", None), "rr_ms": fr.rr_ms,
             # v3.16：校准帧必须带真实 ts —— 两边都改用 10s 窗估基线后，没有 ts
             # 会让 JS 把整段当成一个窗口（窗口不前进），与 Python 分叉。
             "ts": fr.ts,
             # 与 Python 侧 py_frame 同源：用真实帧的 gyro（无则为 0），不注入假值，
             # 否则 JS 校准基线 still 会凭空 0.03 而 Python 为 0，制造不该有的分歧。
             "gyro": (list(getattr(fr, "gyro", None) or [0.0, 0.0, 0.0]))}
        rb = getattr(fr, "rr_batch", None)
        if rb:
            o["rr_batch"] = list(rb)
        calib_json.append([tag, o])
    payload = json.dumps([calib_json, task_frames])
    harness_path = payload_path = None
    try:
        fd, harness_path = tempfile.mkstemp(suffix=".js", prefix="parity_")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(JS_HARNESS)
        fd2, payload_path = tempfile.mkstemp(suffix=".json", prefix="parity_")
        with os.fdopen(fd2, "w", encoding="utf-8") as fh:
            fh.write(payload)
        r = subprocess.run([node, harness_path, vp.JS_ENGINE, payload_path],
                           capture_output=True, text=True, encoding="utf-8")
    finally:
        for p in (harness_path, payload_path):
            if p and os.path.exists(p):
                os.remove(p)
    if r.returncode != 0:
        print(f"\n[parity] JS 引擎执行失败：{r.stderr[:400]}")
        return
    try:
        js_states = json.loads(r.stdout.strip())
    except json.JSONDecodeError:
        print(f"\n[parity] JS 输出非 JSON：OUT={r.stdout[:200]} ERR={r.stderr[:200]}")
        return
    n = min(len(py_states), len(js_states))
    mism = [i for i in range(n) if py_states[i] != js_states[i]]
    print(f"\n===== 双引擎 parity（真实轨迹，{n} 帧 / 基线来源="
          f"{'D2 A/B/C' if calib_tuples else '默认'}）=====")
    print(f"  py/js 状态一致: {n - len(mism)}/{n}"
          f"（{'✓ 全一致' if not mism else '✗ 有 ' + str(len(mism)) + ' 处分歧'}）")
    for i in mism[:8]:
        print(f"    帧{i}: py={py_states[i]} js={js_states[i]}")


# ----------------------------------------------------------------- 主流程
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真机 dump 离线分析")
    ap.add_argument("dump", help="collect_real.py 产出的 JSONL 文件")
    ap.add_argument("--signal-max", dest="signal_max", type=int, default=0,
                    help="回放时 signal 门控上限（应与采集时一致；默认 0=不过滤）")
    ap.add_argument("--parity", action="store_true", help="额外做双引擎真实轨迹比对")
    args = ap.parse_args(argv)

    if not os.path.exists(args.dump):
        print(f"✗ 文件不存在：{args.dump}")
        return 2
    events = load(args.dump)

    print("===== 可用性报告 =====")
    av = availability(events)
    print(f"  事件计数: {av}")
    rr = av.get("rr", 0)
    ext = av.get("extend", 0)
    gyro = av.get("gyro", 0)
    if rr == 0 and ext == 0 and gyro == 0:
        print("  → RR/心率/陀螺仪：**本 dump 均未收到**（符合纯 Python tgam 路线预期）。")
        print("    阶段 B §四『设备是否推 RR 未知』的实测答案：在当前环境下为『否』"
              "（HRV 判据须降级）。若需 RR，须换官方 SDK 且确认设备推送。")
    else:
        print(f"  → 收到扩展/陀螺仪/RR：extend={ext} gyro={gyro} rr={rr} → 官方 SDK 路径生效，HRV 可用。")

    segs = build_segments(events)
    print(f"\n  分段: {[k for k in segs if not k.endswith('_END')]}")

    base, report, tuples = run_calibration(segs)
    print_calibration(report)

    result = replay(events, base, args.signal_max)
    print_replay(result)

    if args.parity:
        parity_on_real(segs, args.signal_max, tuples, events)

    print("\n分析完成。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
