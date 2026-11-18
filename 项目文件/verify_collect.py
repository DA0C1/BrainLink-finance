# -*- coding: utf-8 -*-
"""verify_collect.py — collect_real.py 采集驱动的「不丢数据」验证

为什么要有这个文件
------------------
2026-11-09 真机首采（`real_sub01_20261109_211433.jsonl`）暴露了一个**静默失效**：

    marker  t =   8417 … 401156 ms      （D2 三段校准，共 6 分 30 秒）
    全部 eeg t = 401157 … 639285 ms      （seq 6–246 连续，1 Hz 正常）

即校准 6.5 分钟**一帧都没落盘**。根因不是硬件：旧版 `run_live` 先开串口，
再进入 `guided_session`，而后者全程 `input()` 阻塞，**这期间没有任何代码读串口**。
Windows 蓝牙 SPP 缓冲很小，满了就丢；等分段结束进 `while True: ser.read()` 才开始收。

修法是把 `ser.read()` 放进独立线程。但"改了"不等于"修好了"——本文件用**假串口 +
假输入**在无硬件条件下证明这条性质，并且证明判据会变红。

    python verify_collect.py

通过项：
  P1 输入阻塞期间仍在采（核心反证）：假 input 每次阻塞 0.3s，D2 三段每段都必须 > 0 帧
     —— 旧版（串口读与 input 同线程）这里必然是 0，所以 P1 能抓住该缺陷
  P2 首帧门禁·零帧时中止：假串口永不产出 → run_live 返回 2，且不产生任何 eeg
  P3 首帧门禁·用户强行继续：答 y → 继续跑，段末必须打印「本段零帧」告警
  P4 落盘字段正确：读到 ≠ 解析对；断言 dump 里的 attention/meditation/theta 与造帧一致
  P5 回归：--sim / --sim-sdk 工作流仍通（改动未伤合成自检路径）
  P6 链路失联：采集中途拔串口 → 立即中止（返回码 3）并明说，不静默录空段
  P7 卡顿恢复：短暂丢包后恢复 → 不误判作废，五段照常采完
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time

import collect_real
from tgam_parser import build_frame

SEG_SIGNAL, SEG_ATT, SEG_MED, SEG_BANDS = 0x02, 0x04, 0x05, 0x83


def _payload(signal: int, theta: int = 20000, alpha: int = 40000, beta: int = 30000,
             attention: int = 65, meditation: int = 55) -> bytes:
    bands = [100, theta, alpha // 2, alpha // 2, int(beta * 0.6), int(beta * 0.4), 20, 10]
    p = bytes([SEG_SIGNAL, signal, SEG_BANDS, 24])
    for b in bands:
        p += b.to_bytes(3, "big")
    return p + bytes([SEG_ATT, attention, SEG_MED, meditation])


class FakeSerial:
    """只在被 read() 时才产出数据 —— 正是真串口的缓冲语义：不读就丢。

    旧版把 read 放在 input 之后，等价于"采集期间从不 read"，因此本类在旧版下
    一帧都产不出来（这正是 P1 的判别力来源）。
    """

    def __init__(self, frame: bytes = b"", interval: float = 0.05,
                 fail_after: int | None = None,
                 stall_at: int | None = None, stall_len: int = 0):
        self.frame = frame
        self.interval = interval
        self.served = 0
        self.fail_after = fail_after      # 读若干次后抛异常，模拟串口被拔
        self.stall_at = stall_at          # 第 N 次读之后开始卡顿（返回空）
        self.stall_len = stall_len        # 卡顿持续几次读
        self.closed = False

    def read(self, size: int = 4096) -> bytes:
        time.sleep(self.interval)          # 模拟串口节拍
        if self.fail_after is not None and self.served >= self.fail_after:
            raise OSError("模拟串口被拔出")

        if self.stall_at is not None and self.stall_at <= self.served < self.stall_at + self.stall_len:
            self.served += 1
            return b""                    # 卡顿：返回空，模拟蓝牙短暂丢包
        if not self.frame:
            return b""                     # 空链路（P2/P3 用）
        self.served += 1
        return self.frame

    def close(self) -> None:
        self.closed = True


def _run_case(frame: bytes, ask_sleep: float, ask_answer: str,
              first_frame_timeout: float, preset: str = "d2",
              post_seconds: float = 0.3, fail_after: int | None = None,
              stall_at: int | None = None, stall_len: int = 0):
    """跑一次 run_live，返回 (rc, dump 行列表, stdout 文本)。"""
    tmp = tempfile.mkdtemp(prefix="vc_")
    path = os.path.join(tmp, f"t_{int(time.time() * 1000)}.jsonl")
    fake = FakeSerial(frame, fail_after=fail_after,
                       stall_at=stall_at, stall_len=stall_len)
    old_open, old_ask = collect_real._open_serial, collect_real._ask
    collect_real._open_serial = lambda port, baud: fake
    collect_real._ask = lambda prompt="": (time.sleep(ask_sleep), ask_answer)[1]
    dump = collect_real.RawDump(path)
    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            rc = collect_real.run_live("COM_FAKE", 115200, dump, preset, 0,
                                       first_frame_timeout=first_frame_timeout,
                                       post_seconds=post_seconds)
    finally:
        collect_real._open_serial, collect_real._ask = old_open, old_ask
        dump.close()
    rows = []
    with open(path, encoding="utf-8") as fh:
        rows = [json.loads(x) for x in fh if x.strip()]
    shutil.rmtree(tmp, ignore_errors=True)     # 自检不留垃圾（每次跑都会建一个临时目录）
    return rc, rows, buf.getvalue()


def main() -> int:
    fails: list[str] = []

    def check(name: str, cond: bool, extra: str = "") -> None:
        print(("  [PASS] " if cond else "  [FAIL] ") + name + (f"\n         {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    frame = build_frame(_payload(0, attention=65, meditation=55))

    # ---------------- P1 核心反证
    print("P1 输入阻塞期间仍在采（核心反证）")
    rc, rows, out = _run_case(frame, ask_sleep=0.3, ask_answer="",
                             first_frame_timeout=3.0)
    eeg = [r for r in rows if r.get("type") == "eeg"]
    segs = {}
    for line in out.splitlines():
        if "本段收到" in line:
            # 形如 "  ✓ 「D2_A」本段收到 6 帧 EEG"
            label = line.split("「")[1].split("」")[0]
            segs[label] = int(line.split("收到")[1].split("帧")[0].strip())
    check("P1a D2 三段全部收到帧（旧版此处必为 0）",
          len(segs) == 3 and all(v > 0 for v in segs.values()),
          f"各段帧数={segs}")
    check("P1b 落盘帧数与段内合计一致",
          len(eeg) >= sum(segs.values()) > 0,
          f"落盘={len(eeg)} 段内合计={sum(segs.values())}")
    check("P1c 阻塞累计 1.8s 却拿到远多于 1 帧（证明读在并行线程）",
          len(eeg) >= 6, f"落盘={len(eeg)} 帧")

    # ---------------- P2 首帧门禁·中止
    print("P2 首帧门禁：零帧时应中止")
    rc2, rows2, out2 = _run_case(b"", ask_sleep=0.05, ask_answer="n",
                                 first_frame_timeout=0.4)
    check("P2a 返回码 = 2（中止）", rc2 == 2, f"rc={rc2}")
    check("P2b 未产生任何 eeg 行", not [r for r in rows2 if r.get("type") == "eeg"],
          f"行数={len(rows2)}")
    check("P2c 提示了三条现场检查项",
          "传出" in out2 and "App" in out2 and "休眠" in out2)

    # ---------------- P3 首帧门禁·强行继续
    print("P3 首帧门禁：用户强行继续 → 段末必须告警零帧")
    rc3, rows3, out3 = _run_case(b"", ask_sleep=0.02, ask_answer="y",
                                 first_frame_timeout=0.3)
    check("P3a 继续执行（rc=0）", rc3 == 0, f"rc={rc3}")
    check("P3b 打印「本段零帧」告警", "本段零帧" in out3,
          f"出现次数={out3.count('本段零帧')}")

    # ---------------- P4 落盘字段
    print("P4 落盘字段正确（读到 ≠ 解析对）")
    ok = bool(eeg) and eeg[0].get("attention") == 65.0 and eeg[0].get("meditation") == 55.0
    check("P4a attention/meditation 与造帧一致", ok,
          f"首帧 attention={eeg[0].get('attention') if eeg else None} "
          f"meditation={eeg[0].get('meditation') if eeg else None}")
    check("P4b theta 与造帧一致", bool(eeg) and int(eeg[0].get("theta", -1)) == 20000,
          f"theta={eeg[0].get('theta') if eeg else None}")
    check("P4c 频段按 low/high 拆分落盘",
          bool(eeg) and "lowAlpha" in eeg[0] and "highAlpha" in eeg[0])

    # ---------------- P5 合成自检未回归
    print("P5 合成自检路径未受影响")
    tmp = tempfile.mkdtemp(prefix="vc_sim_")
    d = collect_real.RawDump(os.path.join(tmp, "sim.jsonl"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        collect_real.run_sim(d, with_sdk=False)
    d.close()
    check("P5a --sim（纯 tgam）只出 eeg，且如实无 extend/gyro/rr",
          d.counts["eeg"] > 0 and d.counts["extend"] == 0
          and d.counts["gyro"] == 0 and d.counts["rr"] == 0,
          f"counts={d.counts}")

    d2 = collect_real.RawDump(os.path.join(tmp, "sim_sdk.jsonl"))
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        collect_real.run_sim(d2, with_sdk=True)
    d2.close()
    check("P5b --sim-sdk 四类回调齐全（分析器全分支可测）",
          all(d2.counts[k] > 0 for k in ("eeg", "extend", "gyro", "rr")),
          f"counts={d2.counts}")
    shutil.rmtree(tmp, ignore_errors=True)     # 同上：不留临时目录

    # ---------------- P6 链路失联：串口中途拔掉，必须中止而不是静默录空段
    print("P6 链路失联：采集中途拔串口")
    rc6, rows6, out6 = _run_case(frame, ask_sleep=0.25, ask_answer="",
                                 first_frame_timeout=3.0, preset="d2v4",
                                 post_seconds=0.2, fail_after=25)
    segs6 = [r["label"] for r in rows6
             if r.get("type") == "marker" and not r["label"].endswith("_END")]
    check("P6a 采集中止返回码 3", rc6 == 3, f"rc={rc6}")
    check("P6b 明确告知链路已断", "链路已断" in out6)
    check("P6c 提前中止，未把五段采完", len(segs6) < 5, f"已开始段={segs6}")

    # ---------------- P7 短暂卡顿后恢复：不应中止
    print("P7 卡顿后恢复：不应中止")
    old_down = collect_real.LINK_DOWN_S
    collect_real.LINK_DOWN_S = 0.20          # 亚秒级触发；卡顿 0.45s 后再恢复 → 段内完成置位+清除
    try:
        rc7, rows7, out7 = _run_case(frame, ask_sleep=0.35, ask_answer="",
                                     first_frame_timeout=3.0, preset="d2v4",
                                     post_seconds=0.2, stall_at=18, stall_len=9)
    finally:
        collect_real.LINK_DOWN_S = old_down
    segs7 = [r["label"] for r in rows7
             if r.get("type") == "marker" and not r["label"].endswith("_END")]
    check("P7a 恢复后正常结束（rc=0，未被误判作废）", rc7 == 0, f"rc={rc7}")
    check("P7b 打印过恢复提示", "已恢复" in out7)
    check("P7c 五段全部采完", len(segs7) == 5, f"已采段={segs7}")
    print()
    if fails:
        print(f"FAIL — {len(fails)} 项未通过：")
        for f in fails:
            print("  ·", f)
        return 1
    print("全部通过（7 组 / 19 项断言）。采集驱动「不丢数据 + 断线不静默」的性质已被证明。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
