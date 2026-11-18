# -*- coding: utf-8 -*-
"""export_csv.py — 把 collect_real 的 jsonl dump 转成 serial_bridge 能回放的 CSV

为什么需要它
------------
D4「录制回放调参」要的是 `serial_bridge.py --record` 产的 CSV，而补测采的是
`collect_real.py` 的 jsonl。两者字段同源（都是 TGAM 那 8 个频段 + 指数），
所以不必为 D4 再占一次真机 —— 转格式即可。

    python export_csv.py data/real_sub01_xxx.jsonl                 # 全段导出
    python export_csv.py data/real_sub01_xxx.jsonl --seg D2_B      # 只导某段
    python export_csv.py data/real_sub01_xxx.jsonl -o out.csv

列顺序与 `serial_bridge.CSV_FIELDS` **完全一致**（直接从该模块 import，不手抄），
避免"两处维护列名"漂移。本 dump 无 RR/心率/陀螺仪 → 对应列留空，
`run_replay` 对空值走 `_fnum` → None → 单值路径，不会造假批次。
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from serial_bridge import CSV_FIELDS      # noqa: E402  —— 列定义的唯一来源


def load(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    rows.sort(key=lambda e: e.get("t", 0))
    return rows


def pick(rows: list[dict], seg: str | None) -> list[dict]:
    """只取 eeg 行；给了 seg 就只取该 marker 段内的帧。"""
    if not seg:
        return [r for r in rows if r.get("type") == "eeg"]
    out, cur = [], None
    for r in rows:
        if r.get("type") == "marker":
            lb = r["label"]
            cur = None if lb.endswith("_END") else lb
            continue
        if r.get("type") == "eeg" and cur == seg:
            out.append(r)
    return out


def to_row(e: dict) -> dict:
    """jsonl eeg 行 → CSV 行（8 频段合并 low/high）。"""
    return {
        "ts": round(e["t"] / 1000.0, 3),
        "theta": e["theta"],
        "alpha": e["lowAlpha"] + e["highAlpha"],
        "beta": e["lowBeta"] + e["highBeta"],
        "delta": e["delta"],
        "gamma": e["lowGamma"] + e["highGamma"],
        "attention": e["attention"],
        "meditation": e["meditation"],
        "hr": "",            # 本 dump 无 PPG
        "rr_ms": "",         # 本 dump 无 RR
        "signal": e["signal"],
        "gyro_x": 0.0, "gyro_y": 0.0, "gyro_z": 0.0,   # 无陀螺仪 → 恒 0（与 SdkAdapter 初值一致）
        "state": "", "conf": "",                        # 回放时由引擎重算，留空
        "rr1": "", "rr2": "", "rr3": "",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="jsonl dump → serial_bridge 回放 CSV")
    ap.add_argument("dump")
    ap.add_argument("-o", "--out", help="输出 CSV（默认同目录同名 .csv）")
    ap.add_argument("--seg", help="只导出某个 marker 段（如 D2_B）；默认全部 eeg")
    a = ap.parse_args(argv)

    rows = pick(load(a.dump), a.seg)
    if not rows:
        print(f"✗ 没有可导出的 eeg 行（seg={a.seg}）", file=sys.stderr)
        return 2
    out = a.out or (os.path.splitext(a.dump)[0] + (f"_{a.seg}" if a.seg else "") + ".csv")
    with open(out, "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        for e in rows:
            w.writerow(to_row(e))
    print(f"导出 {len(rows)} 行 → {out}")
    print(f"列数 {len(CSV_FIELDS)}（与 serial_bridge.CSV_FIELDS 同源）")
    print(f"回放：python serial_bridge.py --replay \"{out}\"")
    return 0


if __name__ == "__main__":
    sys.exit(main())
