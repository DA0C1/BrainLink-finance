# -*- coding: utf-8 -*-
"""verify_data.py — 数据链路审查的自动化回归（v3.15）

覆盖的不是"判据对不对"，而是**每个输入字段被怎么对待**：
  D1 真机 dump 字段完整性 / 零值分布
  D2 eSense=0 不得被当成"极度焦虑"
  D3 gyro=None 不得打崩引擎
  D4 频段全 0 不得产出高置信状态
  D5 校准 σ 与运行时特征的估计量差异（只报告，不判失败——它是设计决策）
  D6 接触帧（signal=200）必须下发到界面，不能丢在服务端
  D7 乱序 CSV 回放必须先按 ts 排序

用法：
    python verify_data.py
    python verify_data.py --dump data/real_sub01_20261110_211436.jsonl
"""
from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import statistics as st
import sys
import tempfile
import threading
import time
import types

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import state_engine as se          # noqa: E402

PASS, FAIL, WARN = "PASS", "FAIL", "WARN"
results: list[tuple[str, str, str]] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    results.append((PASS if cond else FAIL, name, detail))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f" — {detail}" if detail else ""))


def note(name: str, detail: str) -> None:
    results.append((WARN, name, detail))
    print(f"  [WARN] {name} — {detail}")


def make_engine():
    return se.StateEngine(baseline=dict(se.StateEngine.calibrate_from_mock([])))


def feed(eng, n=20, **kw):
    dec = None
    for i in range(n):
        d = dict(attention=60, meditation=60, theta=100, alpha=100, beta=100,
                 delta=100, gamma=50, gyro=(0.01, 0.01, 0.01), ts=float(i))
        d.update(kw)
        dec, _ = eng.process_frame(se.Frame(**d), {"task_active": True})
    return dec


# --------------------------------------------------------------------- D1
def d1_dump(path: str | None) -> None:
    print("\nD1 真机 dump 字段完整性")
    paths = [path] if path else sorted(glob.glob(os.path.join(HERE, "data", "real_*.jsonl")))
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        print("  [SKIP] data/ 下没有 real_*.jsonl")
        return
    keys = ("signal", "attention", "meditation", "delta", "theta",
            "lowAlpha", "highAlpha", "lowBeta", "highBeta", "lowGamma", "highGamma")
    for p in paths:
        rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
        eeg = [r for r in rows if r.get("type") == "eeg"]
        if not eeg:
            continue
        missing = sum(1 for r in eeg if any(k not in r for k in keys))
        neg = sum(1 for r in eeg if any((r.get(k) or 0) < 0 for k in keys[3:]))
        zeros = sum(1 for r in eeg if any((r.get(k) or 0) == 0 for k in keys[1:]))
        check(f"{os.path.basename(p)} 字段齐全", missing == 0, f"{len(eeg)} 帧，缺字段 {missing}")
        check(f"{os.path.basename(p)} 频段非负", neg == 0, f"负值帧 {neg}")
        check(f"{os.path.basename(p)} 频段/指数无零值", zeros == 0,
              f"零值帧 {zeros}/{len(eeg)}（0 在频段/指数上应被伪迹门或 eSense 有效性拦截）")


# --------------------------------------------------------------------- D2
def d2_esense_zero() -> None:
    print("\nD2 eSense=0 不得判焦虑")
    for tag, kw in (("att=med=0 / signal=0", dict(attention=0, meditation=0)),
                    ("att=med=0 / signal=120", dict(attention=0, meditation=0, signal=120)),
                    ("med=0 / att=35", dict(attention=35, meditation=0))):
        dec = feed(make_engine(), **kw)
        check(tag, dec.state != "anxiety", f"判定 {dec.state} conf {dec.conf:.2f}")
    dec = feed(make_engine(), attention=30, meditation=25)
    check("真·双低仍判焦虑（防矫枉过正）", dec.state == "anxiety", f"{dec.state} conf {dec.conf:.2f}")


# --------------------------------------------------------------------- D3/D4
def d3_d4_bad_frames() -> None:
    print("\nD3/D4 异常帧不得打崩或产出高置信")
    try:
        feed(make_engine(), gyro=None)
        check("gyro=None 不抛异常", True)
    except Exception as exc:                       # noqa: BLE001
        check("gyro=None 不抛异常", False, f"{type(exc).__name__}: {exc}")
    dec = feed(make_engine(), theta=0, alpha=0, beta=0, delta=0, gamma=0)
    check("频段全 0 → neutral", dec.state == "neutral", f"{dec.state} conf {dec.conf:.2f}")
    dec = feed(make_engine(), theta=100, beta=0)
    check("β=0（比值无穷）→ 非高置信", dec.state == "neutral" and dec.conf <= se.CONF_MIN + 1e-6,
          f"{dec.state} conf {dec.conf:.2f}")


# --------------------------------------------------------------------- D5
def d5_estimator_gap(path: str | None) -> None:
    print("\nD5 校准 σ（逐帧） vs 运行时（10s 窗）的估计量差异")
    paths = [path] if path else sorted(glob.glob(os.path.join(HERE, "data", "real_*.jsonl")))
    paths = [p for p in paths if p and os.path.exists(p)]
    if not paths:
        print("  [SKIP] 无 dump")
        return
    rows = [json.loads(l) for l in open(paths[-1], encoding="utf-8") if l.strip()]
    cur, seg = None, {}
    for r in rows:
        if r.get("type") == "marker":
            lb = r["label"]
            cur = None if lb.endswith("_END") else lb
            continue
        if r.get("type") == "eeg" and cur:
            seg.setdefault(cur, []).append(r)
    rs = seg.get("D2_B") or next((v for v in seg.values() if len(v) >= 30), [])
    if len(rs) < 30:
        print("  [SKIP] 没有足够长的段")
        return

    def per_frame(rs):
        out = {"R_tb": [], "TAR": [], "R_ba": [], "alpha": [], "delta": []}
        for r in rs:
            th, al = r["theta"], r["lowAlpha"] + r["highAlpha"]
            be = r["lowBeta"] + r["highBeta"]
            out["R_tb"].append(th / be if be else float("nan"))
            out["TAR"].append(th / al if al else float("nan"))
            out["R_ba"].append(be / (th + al) if (th + al) else float("nan"))
            out["alpha"].append(al); out["delta"].append(r["delta"])
        return out

    def win(rs, w=9):
        out = {"R_tb": [], "TAR": [], "R_ba": [], "alpha": [], "delta": []}
        for i in range(len(rs)):
            sub = rs[max(0, i - w + 1):i + 1]
            th = sum(x["theta"] for x in sub) / len(sub)
            al = sum(x["lowAlpha"] + x["highAlpha"] for x in sub) / len(sub)
            be = sum(x["lowBeta"] + x["highBeta"] for x in sub) / len(sub)
            dl = sum(x["delta"] for x in sub) / len(sub)
            out["R_tb"].append(th / be if be else float("nan"))
            out["TAR"].append(th / al if al else float("nan"))
            out["R_ba"].append(be / (th + al) if (th + al) else float("nan"))
            out["alpha"].append(al); out["delta"].append(dl)
        return out

    a, b = per_frame(rs), win(rs)
    ratios = []
    for k in a:
        sa = st.pstdev([v for v in a[k] if v == v])
        sb = st.pstdev([v for v in b[k] if v == v])
        if sb > 0:
            ratios.append(sa / sb)
            print(f"       {k:6s} 逐帧σ={sa:10.3f}  10s窗σ={sb:10.3f}  比值={sa / sb:.2f}×")
    note("σ 估计量不一致", f"逐帧/窗口 比值中位 {st.median(ratios):.2f}× —— "
                          f"运行时 |z| 被压缩同等倍数；改法见《数据链路审查》P1-1")


# --------------------------------------------------------------------- D6
def d6_contact_forward() -> None:
    print("\nD6 接触帧必须下发（不能丢在服务端）")
    fake_serial = types.ModuleType("serial")

    class FakeSerial:
        def __init__(self, *a, **k): pass
        def reset_input_buffer(self): pass
        def read(self, n):
            time.sleep(0.02)
            return b""
        def close(self): pass

    fake_serial.Serial = FakeSerial
    old_serial = sys.modules.get("serial")
    sys.modules["serial"] = fake_serial

    fake_parser = types.ModuleType("BrainLinkParser")

    def mk(sig):
        return types.SimpleNamespace(
            signal=sig, theta=100.0, lowAlpha=50.0, highAlpha=50.0,
            lowBeta=50.0, highBeta=50.0, lowGamma=25.0, highGamma=25.0,
            delta=100.0, attention=60.0, meditation=60.0)

    class FakeParser:
        def __init__(self, on_eeg, on_ext, on_gyro, on_rr, on_raw):
            on_eeg(mk(200))
            on_eeg(mk(0))
        def parse(self, raw): pass

    fake_parser.BrainLinkParser = FakeParser
    old_parser = sys.modules.get("BrainLinkParser")
    sys.modules["BrainLinkParser"] = fake_parser
    try:
        import live_bridge as lb
        broker = lb.Broker()
        threading.Thread(target=lb.source_serial,
                         args=(broker, "COM_TEST", 115200, 0), daemon=True).start()
        time.sleep(0.35)
        frames = list(broker._buf)
        contact = [f for f in frames if f.get("source") == "serial:contact"]
        check("接触帧已下发", bool(contact),
              f"共 {len(frames)} 帧，contact {len(contact)} 帧")
        check("接触帧 signal=200", bool(contact) and contact[0].get("signal") == 200,
              str(contact[0].get("signal") if contact else None))
    except Exception as exc:                       # noqa: BLE001
        check("接触帧已下发", False, f"{type(exc).__name__}: {exc}")
    finally:
        if old_serial is not None:
            sys.modules["serial"] = old_serial
        else:
            sys.modules.pop("serial", None)
        if old_parser is not None:
            sys.modules["BrainLinkParser"] = old_parser
        else:
            sys.modules.pop("BrainLinkParser", None)


# --------------------------------------------------------------------- D7
def d7_unsorted_replay() -> None:
    print("\nD7 乱序 CSV 回放需先排序")
    from serial_bridge import CSV_FIELDS, run_replay
    rows = []
    for ts in (3.0, 1.0, 2.0):                     # 故意乱序
        rows.append({"ts": ts, "theta": 100, "alpha": 100, "beta": 100, "delta": 100,
                     "gamma": 50, "attention": 60, "meditation": 60, "signal": 0,
                     "rr1": "", "rr2": "", "rr3": ""})
    fd, path = tempfile.mkstemp(suffix=".csv")
    try:
        with os.fdopen(fd, "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
            w.writeheader()
            for r in rows:
                w.writerow({k: r.get(k, "") for k in CSV_FIELDS})
        eng = make_engine()
        rc = run_replay(eng, path, 1.0, False)
        check("乱序回放不报错", rc == 0, f"rc={rc}")
        check("末帧时间戳为最大 ts", (eng._last_ts or 0) >= 2.9,
              f"_last_ts={eng._last_ts}")
    finally:
        os.remove(path)


def d8_source_watchdog() -> None:
    print("\nD8 帧源异常必须被 /health 看见")
    import live_bridge as lb

    broker = lb.Broker()

    def boom():
        raise SystemExit("COM_DOES_NOT_EXIST: cannot open port")

    lb._run_source(boom, broker)
    check("SystemExit 不再静默", broker.alive is False and "COM_DOES_NOT_EXIST" in (broker.error or ""),
          f"alive={broker.alive} error={broker.error!r}")

    b2 = lb.Broker()
    b2.publish({"theta": 100.0, "ts": 0.0})            # 回放首帧 ts=0 必须保留
    check("publish 保留 ts=0", b2._buf and b2._buf[-1]["ts"] == 0.0,
          f"ts={b2._buf[-1]['ts'] if b2._buf else None}")
    check("publish 更新 last_frame_ts", b2.last_frame_ts > 0, str(b2.last_frame_ts))


def d9_frameio_single_source() -> None:
    print("\nD9 CSV 帧转换只能有一份实现")
    import frameio
    import live_bridge as lb

    row = {"ts": "12.5", "theta": "100", "lowAlpha": "50", "highAlpha": "30",
           "lowBeta": "40", "highBeta": "20", "delta": "90", "gamma": "25",
           "attention": "60", "meditation": "55", "signal": "",
           "rr1": "800", "rr2": "", "rr3": "820", "gyro_x": "", "gyro_y": "0.1"}
    a = frameio.row_to_frame(row)
    b = lb._row_to_frame(row)
    check("两条回放路径产出逐字段相等", a == b,
          "差异=" + str({k: (a[k], b[k]) for k in a if a[k] != b[k]}))
    check("缺 signal 是 None（不是 0）", a["signal"] is None, str(a["signal"]))
    check("RR 批次保留缺位过滤", a["rr_batch"] == [800.0, 820.0], str(a["rr_batch"]))
    check("rr_ms 取批内最后（兼容字段）", a["rr_ms"] == 820.0, str(a["rr_ms"]))
    check("attention 缺列才是 50", a["attention"] == 60.0, str(a["attention"]))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="数据链路审查回归")
    ap.add_argument("--dump", default=None, help="指定真机 dump（默认扫 data/real_*.jsonl）")
    args = ap.parse_args(argv)
    print("=== 数据链路审查（v3.15）===")
    d1_dump(args.dump)
    d2_esense_zero()
    d3_d4_bad_frames()
    d5_estimator_gap(args.dump)
    d6_contact_forward()
    d7_unsorted_replay()
    d8_source_watchdog()
    d9_frameio_single_source()
    fails = [r for r in results if r[0] == FAIL]
    warns = [r for r in results if r[0] == WARN]
    print(f"\n结果：{len(results)} 项，FAIL {len(fails)}，WARN {len(warns)}")
    if fails:
        for _, n, d in fails:
            print(f"  ✗ {n} {d}")
        return 1
    print("全部通过（WARN 为设计层问题，见《数据链路审查_组长E_20261111.md》）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
