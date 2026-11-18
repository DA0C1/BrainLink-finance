# -*- coding: utf-8 -*-
"""verify_tgam_fuzz.py — TGAM 解析器随机字节鲁棒性（v3.16）

真机串口会给出任意噪声/半包/假同步头。解析器允许"解不出来"，但不允许：
  · 抛异常终止采集；
  · 内部缓冲无界增长（内存泄漏）；
  · 把随机字节当成无限多帧（CPU 打满）。
用法：python verify_tgam_fuzz.py
"""
from __future__ import annotations

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from tgam_parser import BrainLinkParser, MAX_PLEN, build_frame  # noqa: E402

random.seed(20261111)
fails = []


def check(name, ok, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
    if not ok:
        fails.append(name)


def main() -> int:
    print("=== TGAM 解析器 fuzz（随机字节 / 半包 / 假同步头）===")
    p = BrainLinkParser()
    max_buf = 0
    try:
        for _ in range(3000):
            n = random.randint(0, 600)
            p.parse(os.urandom(n))
            max_buf = max(max_buf, len(p._buf))
        check("3000 段随机字节不抛异常", True)
        check("内部缓冲有界（≤ MAX_PLEN+4）", max_buf <= MAX_PLEN + 4, f"max={max_buf}")
    except Exception as exc:                       # noqa: BLE001
        check("3000 段随机字节不抛异常", False, f"{type(exc).__name__}: {exc}")

    # 半包 + 假头：先喂半帧，再喂足够多的假 AA，最后补完整帧，必须恢复
    p2 = BrainLinkParser(eeg_callback=lambda d: None)
    payload = bytes([0x02, 25, 0x04, 60, 0x05, 55,
                     0x83, 24] + [0, 0, 100] * 8)
    frame = build_frame(payload)
    p2.parse(frame[:10])
    p2.parse(b"\xAA\xAA\x02\xFF" * 20)
    fired = p2.parse(frame)                 # 噪声后重新收到一整帧，必须能恢复
    check("半包/假头后可恢复解析", p2.stats["frames"] >= 1 and fired >= 1,
          f"frames={p2.stats['frames']} callbacks={fired}")

    print(f"\n结果：{'全部通过' if not fails else '失败 ' + '、'.join(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
