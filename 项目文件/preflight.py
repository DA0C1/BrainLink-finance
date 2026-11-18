# -*- coding: utf-8 -*-
"""preflight.py — 真机首采预检（接设备后、正式录制前跑一遍）

为什么单独要这个脚本：真机首采的失败几乎都发生在"开始录制之后才发现不对"，
而现场最高频的三个坑**都能在录制前用 30 秒空跑拦掉**：

  ① 口选错（BrainLink 配对生成两个虚拟 COM，必须选「传出/输出」，
            选"传入"或主板自带 COM1 的表现是：能打开、零字节、零报错）
  ② 头环被手机 App 占用（串口直接打不开 → Access denied）
  ③ 头环休眠/没推数据（能打开、零字节）

它们和今天已修的三个坑是**同一类失效模式**：工具无法区分「连上了」与「收到数据了」
（前三次：RMSSD 恒 nan 仍 PASS、signal=230 超范围仍 PASS、帧内 signal=30 但 CSV 写 200）。
所以本脚本的最小承诺是：**只要它不是 PASS，就明确告诉你属于上面哪一类、下一步做什么。**

用法：
  python preflight.py                      列出全部 COM 口并逐个空跑探测（默认 30s/口）
  python preflight.py --port COM5          只探指定口
  python preflight.py --seconds 10         缩短空跑（赶时间用，≥10s 才可靠）
  python preflight.py --mock               无硬件自检（验证本脚本自身的判据，含 3 条反例）

判据（信号语义：signal = TGAM poorSignalLevel，**0 最好、200 电极未接触皮肤**）：
  FAIL 打不开        → 坑②（被占用）或口号不存在
  FAIL 零字节        → 坑①（口选错）/ 坑③（休眠）
  FAIL 有字节零有效帧 → 波特率或协议不匹配（不是 TGAM 流）
  WARN 接触无效率高  → 能解帧但没戴好（signal ≥ SIGNAL_BAD 占比高）
  PASS 有帧且接触良好 → 可以开始正式采集
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
import os

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import state_engine as se

SEG_SIGNAL, SEG_ATT, SEG_MED, SEG_BANDS = 0x02, 0x04, 0x05, 0x83


# ----------------------------------------------------------------- 解析器
def pick_parser():
    """与 serial_bridge.run_serial 完全同口径：先官方、失败则纯 Python 兜底。"""
    try:
        from BrainLinkParser import BrainLinkParser as P      # type: ignore
        return P, "官方 BrainLinkParser.pyd（含 RR / gyro / 心率扩展）"
    except ImportError:
        from tgam_parser import BrainLinkParser as P
        return P, ("纯 Python tgam_parser（仅 EEG 主干；不输出 RR/心率/gyro → "
                   "stress 一态不可判、HRV 判据降级为佐证）")


# ----------------------------------------------------------------- 合成流（--mock 自检用）
def synth_frame(signal: int = 25, att: int = 65, med: int = 55,
                theta: int = 60, alpha: int = 100, beta: int = 120) -> bytes:
    """造一帧标准 TGAM 大包（口径与 verify_tgam._payload 一致）。"""
    from tgam_parser import build_frame
    bands = [100, theta, alpha // 2, alpha // 2, int(beta * 0.6), int(beta * 0.4), 20, 10]
    p = bytes([SEG_SIGNAL, signal, SEG_BANDS, 24])
    for b in bands:
        p += int(round(b)).to_bytes(3, "big")
    return build_frame(p + bytes([SEG_ATT, att, SEG_MED, med]))


def synth_stream(n: int, **kw) -> bytes:
    return b"".join(synth_frame(**kw) for _ in range(n))


# ----------------------------------------------------------------- 统计
class Stat:
    """一次探测的原始观测（不做判断，判断交给 judge）。"""

    def __init__(self):
        self.opened = False
        self.open_err = ""
        self.bytes = 0
        self.frames = 0                 # 解析器成功回调 onEEG 的次数
        self.bad_checksum = 0
        self.signals: list[int] = []
        self.atts: list[float] = []
        self.meds: list[float] = []
        self.rr_calls = 0

    @property
    def contact_bad_ratio(self) -> float:
        """signal ≥ SIGNAL_BAD 的占比（电极未接触/信号不可用的帧比例）"""
        if not self.signals:
            return 1.0
        return sum(1 for s in self.signals if s >= se.SIGNAL_BAD) / len(self.signals)


def feed(data: bytes, parser_cls, st: Stat) -> None:
    """把字节流喂解析器，填 Stat。回调只做计数/采样，不接引擎（预检不判状态）。"""

    def on_eeg(d):
        st.frames += 1
        st.signals.append(int(getattr(d, "signal", 0) or 0))
        st.atts.append(float(getattr(d, "attention", 0) or 0))
        st.meds.append(float(getattr(d, "meditation", 0) or 0))

    def on_rr(*_a):
        st.rr_calls += 1

    parser = parser_cls(on_eeg, None, None, on_rr, None)
    st.bytes = len(data)
    parser.parse(data)
    st.bad_checksum = getattr(parser, "stats", {}).get("bad_checksum", 0)


def judge(st: Stat, seconds: float = 30.0) -> tuple[str, list[str]]:
    """返回 (PASS/WARN/FAIL, 结论列表)。每条结论都指向一个具体动作。

    seconds 必须传实际空跑时长——结论里会复述这个数字，写死会造成
    "说了 30 秒其实只跑了 5 秒"这种**文案说谎**（本项目踩过：--seconds 实为帧数上限）。
    """
    msgs: list[str] = []
    if not st.opened:
        return "FAIL", [
            f"串口打不开：{st.open_err}",
            "→ 坑②「被 App 占用 / 口号不存在」：",
            "   a) 手机端 BrainLink App 若正连着头环，先断开（串口被独占是最常见原因）",
            "   b) 设备管理器 →「端口(COM 和 LPT)」确认真实 COM 号，别用 COM1（主板自带）",
        ]
    if st.bytes == 0:
        return "FAIL", [
            f"串口打开了，但 {seconds:.0f} 秒内**零字节**。",
            "→ 坑①「口选错」或坑③「头环休眠」：",
            "   a) BrainLink 配对会生成**两个**虚拟 COM 口 → 换另一个试（必须选「传出/输出」）",
            "   b) 头环是否开机/未休眠（重新戴上一次或重启头环，看指示灯）",
            "   c) 换个 USB 蓝牙适配器或重配对",
        ]
    if st.frames == 0:
        return "FAIL", [
            f"收到 {st.bytes} 字节，但解出 **0 个有效 EEG 帧**（坏校验 {st.bad_checksum}）。",
            "→ 这不是上面三个坑，而是协议/波特率不匹配：",
            "   a) 确认波特率（TGAM 通常 57600 或 115200，本脚本默认 115200，用 --baud 改）",
            "   b) 若坏校验数接近总字节/帧长 → 线路干扰或波特率错",
        ]
    # 有帧：看戴没戴好
    cbr = st.contact_bad_ratio
    med_sig = statistics.median(st.signals)
    lvl = "PASS"
    msgs.append(f"解出 {st.frames} 帧；signal 中位数={med_sig:.0f}（0 最好 / 200 未接触）；"
                f"接触无效帧占比={cbr:.0%}")
    msgs.append(f"attention {min(st.atts):.0f}–{max(st.atts):.0f}、"
                f"meditation {min(st.meds):.0f}–{max(st.meds):.0f}（正常应在 0–100）")
    if cbr > 0.10:
        lvl = "WARN"
        msgs.append("→ 接触无效帧 >10%：电极没贴好/头发遮挡，调整佩戴后重跑"
                    "（判据与 calibration_report 的接触率一致）")
    if st.rr_calls:
        msgs.append(f"收到 RR 回调 {st.rr_calls} 次 → HRV 判据可用"
                    "（说明生效的是官方 pyd，纯 Python 路线此处应为 0）")
    else:
        msgs.append("RR 回调 0 次 → 符合纯 Python tgam_parser 预期（stress 一态不可判，"
                    "其余照常）")
    if lvl == "PASS":
        msgs.append("→ 可以开始正式采集")
    return lvl, msgs


# ----------------------------------------------------------------- 真机探测
def probe_port(port: str, baud: int, seconds: float, parser_cls) -> Stat:
    st = Stat()
    try:
        import serial                                    # type: ignore
    except ImportError:
        st.open_err = "缺少 pyserial（便携包已内置，确认用的是包内运行时）"
        return st
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as exc:                             # 坑②：被占用/不存在
        st.open_err = str(exc)
        return st
    st.opened = True
    chunks: list[bytes] = []
    deadline = time.time() + seconds
    try:
        while time.time() < deadline:
            raw = ser.read(256)
            if raw:
                chunks.append(raw)
    finally:
        try:
            ser.close()
        except Exception:
            # 关闭失败不影响结论：判定只依赖已读到的字节与解出的帧；
            # 且此时进程即将退出，句柄会被回收。刻意不打印，避免淹没真正的失败原因。
            pass
    feed(b"".join(chunks), parser_cls, st)
    return st


def list_ports() -> list:
    try:
        from serial.tools import list_ports as lp          # type: ignore
        return list(lp.comports())
    except Exception:
        return []


# ----------------------------------------------------------------- 自检（含反例）
def selftest() -> int:
    """验证本脚本自身的判据：正例要 PASS，反例要 FAIL/WARN（否则脚本是摆设）。"""
    cls, src = pick_parser()
    fails: list[str] = []

    def check(name, cond, extra=""):
        print(f"[{'PASS' if cond else 'FAIL'}] {name}" + (f" — {extra}" if extra else ""))
        if not cond:
            fails.append(name)

    print("解析器来源：" + src)
    print("\n—— 反例（判据必须变红，否则本脚本等于没检查）——")

    # 反例1：零字节（坑①/③）
    s = Stat(); s.opened = True; s.bytes = 0
    lvl, _ = judge(s)
    check("R1 打开但零字节 → FAIL", lvl == "FAIL")

    # 反例2：有字节但零有效帧（乱码）
    s = Stat(); feed(b"\x01\x02\x03" * 200, cls, s); s.opened = True
    lvl, _ = judge(s)
    check("R2 有字节但解不出帧 → FAIL", lvl == "FAIL",
          f"bytes={s.bytes} frames={s.frames}")

    # 反例3：能解帧但全程未接触（signal=200）
    s = Stat(); feed(synth_stream(20, signal=200), cls, s); s.opened = True
    lvl, _ = judge(s)
    check("R3 全程 signal=200（未佩戴）→ 非 PASS", lvl in ("WARN", "FAIL"),
          f"接触无效率={s.contact_bad_ratio:.0%}")

    # 正例：正常佩戴
    print("\n—— 正例 ——")
    s = Stat(); feed(synth_stream(20, signal=25), cls, s); s.opened = True
    lvl, msgs = judge(s)
    for m in msgs:
        print("      " + m)
    check("P1 正常流（signal=25）→ PASS", lvl == "PASS")

    print("\n" + ("自检通过：判据有效（3 反例均变红、正例 PASS）" if not fails
                  else f"自检失败：{fails}"))
    return 0 if not fails else 1


# ----------------------------------------------------------------- 主流程
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="真机首采预检（录制前拦掉选错口/被占用/休眠）")
    ap.add_argument("--port", help="指定串口；不给则列出全部 COM 并逐个探测")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--seconds", type=float, default=30.0,
                    help="每口空跑秒数（默认 30；<10 不足以区分休眠与慢启动）")
    ap.add_argument("--mock", action="store_true", help="无硬件自检（验证判据本身）")
    args = ap.parse_args(argv)

    if args.mock:
        return selftest()

    cls, src = pick_parser()
    print("=" * 62)
    print("真机首采预检")
    print("=" * 62)
    print(f"解析器来源：{src}")
    print(f"空跑时长：{args.seconds:.0f}s/口   波特率：{args.baud}")

    ports = [args.port] if args.port else [p.device for p in list_ports()]
    if not ports:
        print("\n未发现任何 COM 口。→ 先配对蓝牙，再在设备管理器确认「传出」口。")
        return 3

    if not args.port:
        print("\n可用 COM 口：")
        for p in list_ports():
            print(f"  {p.device:<8} {p.description or ''}")

    worst = 0
    results = []
    for port in ports:
        print(f"\n----- 探测 {port} -----")
        st = probe_port(port, args.baud, args.seconds, cls)
        lvl, msgs = judge(st, args.seconds)
        for m in msgs:
            print(f"  {m}")
        print(f"  结论：{lvl}")
        results.append((port, lvl))
        worst = max(worst, {"PASS": 0, "WARN": 1, "FAIL": 2}[lvl])

    print("\n" + "=" * 62)
    for port, lvl in results:
        print(f"  {port:<8} {lvl}")
    ok = [p for p, l in results if l == "PASS"]
    if ok:
        print(f"\n可用口：{', '.join(ok)} → 下一步：")
        print(f"  python collect_real.py --port {ok[0]} --subject sub01 --preset d2")
    else:
        print("\n没有可用口。按上面的「→」提示逐条排查后再跑一次。")
    return 0 if worst == 0 else (1 if worst == 1 else 2)


if __name__ == "__main__":
    sys.exit(main())
