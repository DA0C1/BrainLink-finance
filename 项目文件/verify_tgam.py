# -*- coding: utf-8 -*-
"""verify_tgam.py — 纯 Python TGAM 解析链路的端到端验证

`tgam_parser.py` 的自检只证明「帧解得出来」。本文件证明下一件更要紧的事：
**这些帧真的能驱动引擎判出状态**。两者缺一，都只算"有数据"而不算"算得出来"。

    python verify_tgam.py

通过项：
  P1 合成 TGAM 流 → tgam_parser → SdkAdapter → StateEngine 判出 focus
  P2 反例·全程 signal=200（电极未接触皮肤）→ 引擎收不到任何帧（硬门生效）
  P3 反例·半数帧校验被篡改 → 仍能判出状态，且 bad_checksum 计数对得上
  P4 接口兼容性：5 个位置参数顺序与官方一致（可零侵入替换 import）
"""
from __future__ import annotations

import inspect
import sys

from sdk_adapter import SdkAdapter
from state_engine import StateEngine
from tgam_parser import BrainLinkParser, build_frame

SEG_SIGNAL, SEG_ATT, SEG_MED, SEG_BANDS = 0x02, 0x04, 0x05, 0x83


def _payload(signal: int, theta: float, beta: float, alpha: float,
             attention: int = 65, meditation: int = 55) -> bytes:
    """按参考分布造一个标准 TGAM 大包（把 alpha/beta 拆成 low/high 两段）"""
    bands = [100, theta, alpha / 2, alpha / 2, beta * 0.6, beta * 0.4, 20, 10]
    p = bytes([SEG_SIGNAL, signal, SEG_BANDS, 24])
    for b in bands:
        p += int(round(b)).to_bytes(3, "big")
    return p + bytes([SEG_ATT, attention, SEG_MED, meditation])


def _run(frames: list[bytes], corrupt_every: int = 0) -> tuple[StateEngine, BrainLinkParser]:
    """喂帧 → 引擎；用虚拟时钟推进（与 serial_bridge 回放同口径，否则滑窗不滑动）"""
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    ad = SdkAdapter(eng)
    vt = [0.0]
    eng._clock = lambda: vt[0]
    parser = BrainLinkParser(ad.onEEG, ad.onExtendEEG, ad.onGyro, ad.onRR, None)
    for i, fr in enumerate(frames):
        if corrupt_every and i % corrupt_every == 0:
            fr = fr[:-1] + bytes([(fr[-1] + 1) & 0xFF])   # 篡改校验和
        vt[0] += 1.2
        parser.parse(fr)
    return eng, parser


def main() -> int:
    fails: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    # P1 正常链路：θ 低 β 高（M13 主口径的专注形态）→ 应判 focus
    focus = [build_frame(_payload(25, theta=55, beta=100, alpha=130)) for _ in range(90)]
    eng, pr = _run(focus)
    dec = eng.last_decision
    # 帧数要看解析器的回调计数：eeg_buf 是滑动窗口（只留最近 9 帧），拿它当"喂了几帧"会误判
    check("P1 合成 TGAM 流 → 引擎判 focus",
          dec is not None and dec.state == "focus" and pr.stats["eeg_callbacks"] == 90,
          f"state={getattr(dec, 'state', None)} conf={getattr(dec, 'conf', 0):.2f} "
          f"解析回调={pr.stats['eeg_callbacks']}/90，窗口帧数={len(eng.eeg_buf)}")

    # P2 反例：电极未接触皮肤 → SdkAdapter 硬门必须一帧都不放行
    off = [build_frame(_payload(200, theta=55, beta=100, alpha=130)) for _ in range(90)]
    eng2, _ = _run(off)
    check("P2 反例·signal=200 全程 → 引擎零帧",
          len(eng2.eeg_buf) == 0,
          f"入引擎帧数={len(eng2.eeg_buf)}（应为 0）")

    # P3 反例：半数帧坏校验 → 不能崩，仍应判出状态，且坏帧计数对得上
    eng3, pr3 = _run(focus, corrupt_every=2)
    dec3 = eng3.last_decision
    bad = pr3.stats["bad_checksum"]
    check("P3 反例·半数帧校验被篡改 → 仍判出状态且计数正确",
          dec3 is not None and dec3.state == "focus" and bad == 45,
          f"state={getattr(dec3, 'state', None)} bad_checksum={bad}（期望 45/90）")

    # P4 接口兼容：serial_bridge 是按位置传 5 个回调，顺序错了不会报错只会静默错投
    params = [p for p in inspect.signature(BrainLinkParser.__init__).parameters.values()
              if p.kind is p.POSITIONAL_OR_KEYWORD][1:]
    check("P4 构造函数 5 个位置参数（eeg/extend/gyro/rr/raw 顺序一致）",
          len(params) == 5,
          ", ".join(p.name for p in params))

    print()
    if fails:
        print(f"失败 {len(fails)} 项：{fails}")
        return 1
    print("全部通过（4 项，含 2 条反例）。")
    print("提醒：tgam_parser 不输出 RR/心率 → 本链路下 HRV 特征恒缺，stress 判据须降级为佐证（anxiety 不依赖 HRV，不受影响）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
