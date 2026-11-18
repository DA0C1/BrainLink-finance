# -*- coding: utf-8 -*-
"""make_demo_csv.py — 生成**不含受试者信息**的演示回放 CSV（v3.16.2）

为什么需要它
------------
便携包按隐私规则**不携带** `项目文件/data/` 下的真机数据（.jsonl / .csv 一律排除）。
但「启动-实时面板.cmd」的菜单第 3 项是「回放已有 CSV」，包里没有 CSV 就会空转；
真机演示失败时也少了一条退路。

折中做法：用 mockdata 生成一份 8 相位、520 帧的合成 CSV，**它不含任何真人数据**，
可以安全地进包，用于：
  · 验证「CSV → 引擎 → 面板」这条回放链路；
  · 现场真机故障时的兜底（效果与 mock 一致，但走的是完整录制回放路径）。

它**不能**用来证明判别准确率 —— 与 mock 同源，判据结论与 mock 完全相同。

用法：
    python 项目文件/make_demo_csv.py                 # 写 项目文件/data/demo_mock_8phase.csv
    python 项目文件/make_demo_csv.py -o /tmp/x.csv --repeat 2
"""
from __future__ import annotations

import argparse
import csv
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import mockdata as md                                    # noqa: E402
from serial_bridge import CSV_FIELDS, _row_from          # noqa: E402
from state_engine import StateEngine                     # noqa: E402

FRAMES_PER_PHASE = 65          # 与前端自动轮播一致（65 帧 × 1.2s ≈ 78s/相位）
STEP_SEC = 1.2


def build(repeat: int = 1) -> tuple[list[dict], dict]:
    """返回 (CSV 行列表, 统计)。同一随机种子保证可复现。"""
    random.seed(7)
    eng = StateEngine(baseline=dict(StateEngine.calibrate_from_mock([])),
                      clock=md._make_mock_clock())
    rows: list[dict] = []
    tally: dict[str, int] = {}
    t = 0.0
    for _ in range(repeat):
        for phase in md.MOCK_PHASES:
            task_active = phase in ("focus", "distracted")
            for i in range(1, FRAMES_PER_PHASE + 1):
                fr = md._mock_frame(phase, t=(i / FRAMES_PER_PHASE) if phase == "flow" else None)
                fr.ts = t
                dec, _fb = eng.process_frame(fr, {"task_active": task_active})
                row = _row_from(fr, dec.state, dec.conf, None, 0)
                row["ts"] = round(t, 3)
                rows.append(row)
                tally[dec.state] = tally.get(dec.state, 0) + 1
                t = round(t + STEP_SEC, 3)
    return rows, tally


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成合成演示回放 CSV（无受试者数据）")
    ap.add_argument("-o", "--out", default=os.path.join(HERE, "data", "demo_mock_8phase.csv"))
    ap.add_argument("--repeat", type=int, default=1, help="把 8 相位重复几轮（默认 1）")
    args = ap.parse_args(argv)

    os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
    rows, tally = build(max(1, args.repeat))
    with open(args.out, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        w.writeheader()
        w.writerows(rows)

    total = len(rows)
    print(f"已生成 {args.out}")
    print(f"行数 {total}（{total * STEP_SEC / 60:.1f} 分钟，{STEP_SEC}s/帧，{len(md.MOCK_PHASES)} 相位 × {FRAMES_PER_PHASE} 帧 × {args.repeat} 轮）")
    print("判定分布：" + "、".join(f"{k} {v}" for k, v in
                                sorted(tally.items(), key=lambda kv: -kv[1])))
    print("\n回放：")
    print(f'  python 项目文件/live_bridge.py --source replay --csv "{args.out}" --loop')
    print("  （或双击 启动-实时面板.cmd 选 3，粘贴上面这个路径）")
    print("\n注意：本文件由 mock 合成，不含任何受试者数据，也不能用于证明判别准确率。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
