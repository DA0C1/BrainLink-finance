# -*- coding: utf-8 -*-
"""collect_real.py — 真机采集驱动（引导式，一次采集存全量原始 dump）

设计目标（阶段 B · 只有一台真机，且官方 BrainLinkParser.pyd 已确认不可装）：
- **采集一次、离线分析多次**：把 4 类 SDK/TGAM 回调全部原样落盘（JSONL），事后判定
  RR/心率到底有没有推、各 D 段判得对不对，都不必再占用真机。
- 引导式分段：内置 D2 校准（A/B/C）与自由分段，每段用 Enter 起停，落 marker 行，
  使「一次多相会话」自描述，离线可切分。
- 双解析器兼容：优先官方 BrainLinkParser（能拿 RR/心率/陀螺仪）；否则回退 tgam_parser
  （仅 TGAM 标准帧，RR/心率/陀螺仪 按协议边界不触发——这是已知代价，落盘会如实记录「未收到」）。

落盘格式：data/real_<subject>_<ts>.jsonl，每行一种类型：
  {"t":<mono_ms>,"wall":<iso>,"seq":<int>,"type":"eeg",
   "signal":..,"attention":..,"meditation":..,"delta":..,"theta":..,
   "lowAlpha":..,"highAlpha":..,"lowBeta":..,"highBeta":..,"lowGamma":..,"highGamma":..}
  {"t":..,"type":"extend","heart":..,"ap":..,"battery":..,"temperature":..}   # 官方 SDK 才有
  {"t":..,"type":"gyro","x":..,"y":..,"z":..}                                  # 官方 SDK 才有
  {"t":..,"type":"rr","rr":[r1,r2,r3]}                                        # 官方 SDK 才有
  {"t":..,"seq":..,"type":"marker","label":"D2_A","note":"闭眼静坐"}

用法：
  python collect_real.py --sim                 # 无硬件自检（纯 tgam 合成帧，验证整条工作流）
  python collect_real.py --sim-sdk             # 模拟官方 SDK（带 RR/心率/陀螺仪）验证分析器全分支
  python collect_real.py --port COM3 --baud 115200 --subject sub01
  python collect_real.py --port COM3 --baud 115200 --subject sub01 --preset d2   # 引导 D2 校准
  python collect_real.py --port COM3 --baud 115200 --subject sub01 --free        # 自由分段
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from types import SimpleNamespace

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")
sys.path.insert(0, HERE)

from sdk_adapter import SdkAdapter  # noqa: E402


# ----------------------------------------------------------------- 落盘
class RawDump:
    """把每类回调原样写成 JSONL，附带单调时间戳与序号，离线可重建会话。"""

    def __init__(self, path: str):
        self.path = path
        self._fh = open(path, "w", encoding="utf-8")
        self._t0 = time.monotonic()
        self._seq = 0
        self.counts = {"eeg": 0, "extend": 0, "gyro": 0, "rr": 0, "marker": 0}
        # 最近一帧 EEG 的快照：供「首帧门禁」打印 signal、供段末自证判据使用
        self.last_eeg: dict | None = None

    def _row(self, obj: dict) -> None:
        obj = dict(obj)
        obj["t"] = round((time.monotonic() - self._t0) * 1000)
        obj["wall"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        obj["seq"] = self._seq
        self._seq += 1
        self._fh.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self._fh.flush()

    def eeg(self, d) -> None:
        self.counts["eeg"] += 1
        row = {
            "type": "eeg",
            "signal": int(getattr(d, "signal", 0) or 0),
            "attention": float(getattr(d, "attention", 0.0)),
            "meditation": float(getattr(d, "meditation", 0.0)),
            "delta": float(getattr(d, "delta", 0.0)),
            "theta": float(getattr(d, "theta", 0.0)),
            "lowAlpha": float(getattr(d, "lowAlpha", 0.0)),
            "highAlpha": float(getattr(d, "highAlpha", 0.0)),
            "lowBeta": float(getattr(d, "lowBeta", 0.0)),
            "highBeta": float(getattr(d, "highBeta", 0.0)),
            "lowGamma": float(getattr(d, "lowGamma", 0.0)),
            "highGamma": float(getattr(d, "highGamma", 0.0)),
        }
        self.last_eeg = row
        self._row(row)

    def extend(self, d) -> None:
        self.counts["extend"] += 1
        self._row({
            "type": "extend",
            "heart": getattr(d, "heart", None),
            "ap": getattr(d, "ap", None),
            "battery": getattr(d, "battery", None),
            "temperature": getattr(d, "temperature", None),
        })

    def gyro(self, x, y, z) -> None:
        self.counts["gyro"] += 1
        self._row({"type": "gyro", "x": float(x), "y": float(y), "z": float(z)})

    def rr(self, r1, r2, r3) -> None:
        self.counts["rr"] += 1
        self._row({"type": "rr", "rr": [r1, r2, r3]})

    def marker(self, label: str, note: str = "") -> None:
        self.counts["marker"] += 1
        self._row({"type": "marker", "label": label, "note": note})

    def close(self) -> None:
        try:
            self._fh.close()
        except OSError:
            # 文件句柄已关闭或盘满等：采集已落盘，关闭失败不影响已写数据
            pass


# ----------------------------------------------------------------- 解析器装配
def build_parser(on_eeg, on_ext, on_gyro, on_rr):
    """返回 (parser, src)。优先官方 pyd，回退纯 Python tgam。"""
    try:
        from BrainLinkParser import BrainLinkParser      # type: ignore
        src = "官方 BrainLinkParser.pyd（含 RR / gyro / 心率扩展）"
    except ImportError:
        from tgam_parser import BrainLinkParser
        src = "纯 Python tgam_parser（仅 EEG 主干；不输出 RR/心率/gyro → HRV 判据须降级）"
    parser = BrainLinkParser(on_eeg, on_ext, on_gyro, on_rr, None)
    return parser, src


# ----------------------------------------------------------------- 可注入点（便于自检）
# 把「打开串口」与「等用户回车」抽成模块级函数，测试时可替换，
# 从而在无硬件、无人值守的条件下复现"input 阻塞期间是否仍在采数据"。
def _ask(prompt: str = "") -> str:
    return input(prompt)


def _open_serial(port: str, baud: int):
    import serial
    return serial.Serial(port, baud, timeout=1.0)


# ----------------------------------------------------------------- 引导分段
# 串口静默阈值（秒）。提成常量是为了可测：verify_collect 的 P7 会临时改小，
# 在亚秒级内模拟「卡顿→恢复」，否则真等 30 秒没法测。
LINK_WARN_S = 15.0     # 超过这么久没字节 → 告警一次（可能选错口/休眠）
LINK_DOWN_S = 30.0     # 超过这么久 → 判失联，主线程段末中止采集
D2_PRESET = [
    # 时长与《真机校准实操清单》/app.js::calibTargets 对齐：A 30s / B 120s / C 120s。
    # 旧提示把 A 也写成 120s（"或按现场节拍"），2026-11-09 首采照它做了 122.7s —— 
    # 多采不算错，但与协议不符会让"三段各多久"这件事两处不一致。
    ("D2_A", "校准 A 段：闭眼静息 30s（δ 基线；本段不用于 theta_ref）"),
    ("D2_B", "校准 B 段：睁眼静坐（看十字、不做任务）120s → 全部 z 基线 + theta_ref"),
    ("D2_C", "校准 C 段：**闭眼**放松深呼吸 120s（必须闭眼——α 靠闭眼升高，睁眼放松 α 升不上去）"),
]

# v3.6：一次采完「校准三态 + V4 肌电判定」的合并预设。
# 起因：2026-11-09 四份 dump 只做了 D2 三段，V4（说话肌电）永远缺数据，
# 而要补 V4 就必须再占用一次真机。既然只有一次补测机会，就把两个目的并进同一次会话。
# 判据来源：verify_hw.v4_emg —— 静默段至少 20 帧（前 60% 建基线、后 40% 测误报），
# 说话段至少 10 帧。按 1 Hz 计，40s 静默给足余量。
D2V4_PRESET = D2_PRESET + [
    ("V4_SILENT", "V4 静默基线 40s：不说话、不吞咽、不动（前 24s 建基线，后 16s 测误报）"),
    ("V4_TALK", "V4 说话 40s：正常音量朗读或聊天（制造面部肌电，不必大喊）"),
]


def guided_session(dump: RawDump, preset: str, sim: bool, link_check=None) -> bool:
    """按预设/自由模式逐段采集，每段用 Enter 起停并落 marker。"""
    # link_check：可选回调，返回 None=链路正常，返回字符串=失联原因。每段结束调用一次；
    # 串口中途被拔时 read 线程会退出，不检查的话主线程仍会在 input() 上等回车，一路采出空段。
    # 返回 False 表示采集中止。

    if preset == "d2":
        phases = D2_PRESET
    elif preset == "d2v4":
        phases = D2V4_PRESET
    else:
        phases = []
        print("\n自由分段：每段输入标签后回车开始，再次回车结束；空标签结束会话。")
        while True:
            label = _ask("  本段标签（空=结束）: ").strip()
            if not label:
                break
            note = _ask("  本段说明（可空）: ").strip()
            phases.append((label, note))

    for label, note in phases:
        if sim:
            print(f"\n[sim] ▶ {label} — {note}")
        else:
            print(f"\n▶ 开始「{label}」— {note}")
            _ask("  准备好后回车开始本段（Ctrl+C 可随时中止整个会话）: ")
        before = dump.counts["eeg"]
        dump.marker(label, note)
        if not sim:
            _ask("  本段结束回车停止: ")
        else:
            time.sleep(0.05)   # sim 不需要用户等待
        dump.marker(label + "_END", note)
        # v3.6 段自证：本段到底收到多少帧。旧版没有任何反馈，2026-11-09 真机首采
        # 三段校准全空（marker 0–401s / eeg 401–639s）却是回家跑分析才发现。
        got = dump.counts["eeg"] - before
        if got <= 0:
            print(f"  ✗ 【本段零帧】「{label}」没有收到任何 EEG —— 这一段作废！")
            print("    立即检查：头环是否戴好/亮灯、COM 是否「传出」口、是否被手机 App 占用、是否休眠。")
        else:
            print(f"  ✓ 「{label}」本段收到 {got} 帧 EEG")
        # v3.7：链路失联早退 —— 先让用户看到本段收了几帧，再告诉他后面不用采了。
        if link_check:
            why = link_check()
            if why:
                print(f"\n  ✗ 【链路已断】{why}")
                print("    → 本次采集作废，后续段不再进行。")
                print("    → 请检查头环/串口连接（或换 COM 口）后重新采集。")
                return False
    return True


# ----------------------------------------------------------------- 在线采集
def run_live(port: str, baud: int, dump: RawDump,
             preset: str, frames_limit: int,
             first_frame_timeout: float = 20.0,
             post_seconds: float | None = None,
             stop_after_preset: bool = False) -> int:
    """真机在线：串口 → 解析器 → 适配器（入引擎）+ 原始落盘。

    **读串口必须在独立线程**（v3.6 修复）：旧版把 `ser.read()` 放在 `guided_session`
    之后，而 guided_session 全程用 `input()` 阻塞，导致引导分段期间**没有任何代码读串口**。
    Windows 蓝牙 SPP 的驱动缓冲很小，满了就丢 —— 校准 3 段 6.5 分钟的数据会全部消失，
    等分段结束才开始收。2026-11-09 真机首采实证：
        marker   t = 8417 … 401156 ms
        全部 eeg t = 401157 … 639285 ms（seq 6–246 连续无缺号）
    即"校准期间零帧、结束后突然正常 1 Hz"。这不是硬件问题，是采集脚本的单线程阻塞。
    """
    parser, src = build_parser(
        on_eeg=lambda d: (dump.eeg(d), None),
        on_ext=lambda d: dump.extend(d),
        on_gyro=lambda x, y, z: dump.gyro(x, y, z),
        on_rr=lambda r1, r2, r3: dump.rr(r1, r2, r3),
    )
    # 注意：适配器在引导分段里也会用，这里仅做「原始落盘」；引擎实时判别由单独链路负责，
    # 本驱动聚焦「全量原始 dump」，避免与 serial_bridge 的在线判别重复实现。
    print(f"解析器：{src}")
    print(f"打开串口 {port} @ {baud} …")
    ser = _open_serial(port, baud)

    stop = threading.Event()
    state = {"last_rx": time.monotonic(), "last_warn": 0.0, "down": None}

    def _reader() -> None:
        """后台持续读串口；input() 阻塞与否都不影响落盘。"""
        while not stop.is_set():
            try:
                chunk = ser.read(4096)
            except Exception as exc:                       # 串口被拔/驱动异常
                state["down"] = f"串口读异常（{type(exc).__name__}）：{exc}"
                if not stop.is_set():
                    print(f"\n  ⚠ 串口读异常：{exc}")
                return
            now = time.monotonic()
            if chunk:
                state["last_rx"] = now
                # v3.7.1：零字节型失联可恢复 —— 蓝牙 SPP 偶发卡顿 30-60s 后自行恢复是常态，
                # 一旦置位就锁死会把整场采集误判作废。异常型（读线程已退出）不在此列。
                if state["down"] and state["down"].startswith("串口"):
                    state["down"] = None
                    print("\n  ✓ 串口数据已恢复，继续采集")
                parser.parse(bytearray(chunk))
            elif now - state["last_rx"] > LINK_WARN_S and now - state["last_warn"] > LINK_WARN_S:
                state["last_warn"] = now
                print("\n  ⚠ 串口 15s 零字节（常见：选错 COM 口 / 设备未推送 / 头环休眠）")
            # v3.7：30s 无字节判失联。真机 1Hz，30s 空窗不可能是正常状态；
            # 只告警不置位的话，主线程会继续等回车，一路采出空段。
            if not chunk and not state["down"] and now - state["last_rx"] > LINK_DOWN_S:
                state["down"] = f"串口 {now - state['last_rx']:.0f}s 无任何数据"
                print(f"\n  ⚠ 串口已 {now - state['last_rx']:.0f}s 无数据，判为链路失联")

    th = threading.Thread(target=_reader, name="serial-reader", daemon=True)
    th.start()
    print("后台读线程已启动（校准期间持续落盘，不再丢数据）")

    # 首帧门禁：没收到数据就不要开始校准。旧版无此检查，可能对着空链路录 6 分钟。
    print(f"等待首帧（最多 {first_frame_timeout:g} 秒）…")
    t0 = time.monotonic()
    while dump.counts["eeg"] == 0 and time.monotonic() - t0 < first_frame_timeout:
        time.sleep(0.1)
    if dump.counts["eeg"] == 0:
        print("  ✗ 超时没有任何 EEG 帧 —— 现在开始校准会全部作废！")
        print("    逐项检查：① COM 是否为蓝牙「传出」口（COM1 永远不对）")
        print("              ② 头环是否被手机 App 占用（先断开 App）")
        print("              ③ 头环是否休眠 / 电极是否贴好（signal=200 表示未接触）")
        ans = _ask("  仍要继续采吗？(y/N) ").strip().lower()
        if ans != "y":
            stop.set()
            th.join(timeout=2.0)
            ser.close()
            print("  已中止，未产生有效数据。")
            return 2
    else:
        last = dump.last_eeg or {}
        print(f"  ✓ 首帧已到：signal={last.get('signal')} "
              f"attention={last.get('attention')} （链路正常）")

    try:
        if not guided_session(dump, preset, sim=False,
                              link_check=lambda: state.get("down")):
            print("\n  采集中止：链路已断，已落盘的数据不完整，不要用于分析。")
            return 3
        if stop_after_preset:
            print("\n预设分段已采完，按 --stop-after-preset 直接收尾。")
            return 0
        print("\n分段采集完成，继续采集（Ctrl+C 结束）…")
        # post_seconds=None → 一直采到 Ctrl+C（现场默认）；给数值则到时自动停（自检用）
        deadline = (time.monotonic() + post_seconds) if post_seconds is not None else None
        while True:
            if frames_limit and dump.counts["eeg"] >= frames_limit:
                break
            if deadline is not None and time.monotonic() >= deadline:
                break
            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\n  用户中止（Ctrl+C），正在收尾…")
    finally:
        stop.set()
        th.join(timeout=2.0)
        try:
            ser.close()
        except Exception as exc:                            # 关串口失败不该掩盖已落盘数据
            print(f"  （串口关闭时提示：{exc}）")
    return 0


# ----------------------------------------------------------------- 合成自检
def run_sim(dump: RawDump, with_sdk: bool, preset: str = "d2") -> int:
    """无硬件自检：合成 TGAM/SDK 帧，验证整条采集→落盘→分析工作流。

    preset="d2v4" 时连 V4 静默/说话段一起合成——这样组员在不接硬件时就能
    把补测那天的五段流程完整预演一遍（含每段帧数自证），而不是到现场才第一次跑。
    V4_TALK 刻意让 β≈7×、γ≈6×（θ 不动）→ 构成真肌电特征，供 analyze_v4 验证判据。
    """
    # 相位画像：每相给一组「合理」的 TGAM 数值区间
    profiles = {
        "D2_A": dict(att=55, med=70, theta=130, alpha=120, beta=70, delta=120, gamma=45, sig=8),
        "D2_B": dict(att=60, med=55, theta=100, alpha=100, beta=100, delta=100, gamma=50, sig=10),
        "D2_C": dict(att=50, med=72, theta=95, alpha=160, beta=70, delta=100, gamma=45, sig=6),
        "V4_SILENT": dict(att=55, med=60, theta=100, alpha=100, beta=100, delta=100, gamma=50, sig=8),
        # 说话段：β/γ 同升、θ 不动 —— 肌电的判据特征（只升 β 不构成肌电）
        "V4_TALK": dict(att=60, med=55, theta=100, alpha=100, beta=700, delta=100, gamma=300, sig=10),
        "TASK_focus": dict(att=88, med=42, theta=55, alpha=70, beta=150, delta=70, gamma=30, sig=8),
        "TASK_distract": dict(att=42, med=50, theta=125, alpha=135, beta=88, delta=115, gamma=45, sig=12),
    }
    try:
        from tgam_parser import BrainLinkData
    except ImportError:
        BrainLinkData = None

    def synth(p, n):
        for _ in range(n):
            if BrainLinkData is not None:
                d = BrainLinkData(
                    signal=p["sig"], attention=p["att"], meditation=p["med"],
                    bands=[p["delta"], p["theta"], p["alpha"] // 2, p["alpha"] // 2,
                           p["beta"] // 2, p["beta"] // 2, p["gamma"] // 2, p["gamma"] // 2],
                )
            else:
                d = SimpleNamespace(
                    signal=p["sig"], attention=p["att"], meditation=p["med"],
                    delta=p["delta"], theta=p["theta"], lowAlpha=p["alpha"] // 2,
                    highAlpha=p["alpha"] // 2, lowBeta=p["beta"] // 2, highBeta=p["beta"] // 2,
                    lowGamma=p["gamma"] // 2, highGamma=p["gamma"] // 2,
                )
            dump.eeg(d)
            if with_sdk:
                dump.extend(SimpleNamespace(heart=72.0, ap=0, battery=90, temperature=36.5))
                dump.gyro(0, 0, 0)
                dump.rr(820, 810, 830)
            time.sleep(0.001)

    base_phases = D2V4_PRESET if preset == "d2v4" else D2_PRESET
    print(f"[sim] 纯 Python 路线={'tgam' if not with_sdk else '官方SDK(模拟)'}；"
          f"合成 {len(base_phases)} 段（{'含 V4 静默/说话' if preset == 'd2v4' else 'D2 三段'}）+ 两个任务段")
    for label, note in base_phases + [("TASK_focus", "任务：专注看盘"),
                                      ("TASK_distract", "任务：分心刷手机")]:
        print(f"[sim] ▶ {label} — {note}")
        before = dump.counts["eeg"]
        dump.marker(label, note)
        synth(profiles[label], 80)
        dump.marker(label + "_END", note)
        got = dump.counts["eeg"] - before
        print(f"  ✓ 「{label}」本段收到 {got} 帧 EEG")
    return 0


# ----------------------------------------------------------------- 收尾
def summarize(dump: RawDump, path: str) -> None:
    c = dump.counts
    print("\n===== 采集小结 =====")
    print(f"dump 文件 : {path}")
    print(f"帧统计   : eeg={c['eeg']}  extend={c['extend']}  gyro={c['gyro']}  "
          f"rr={c['rr']}  marker={c['marker']}")
    if c["rr"] == 0 and c["extend"] == 0:
        print("⚠ RR/心率：本会话未收到任何 extend/rr 回调 → 符合纯 Python tgam 路线预期"
              "（HRV 判据在校验/分析时须降级处理）。")
        print("  若你期望拿到 RR/心率，需确认设备经官方 SDK 推送（当前环境官方 pyd 不可装）。")
    else:
        print("✓ 收到 RR/心率/陀螺仪回调 → 官方 SDK 路径生效，HRV 判据可用。")
    print("下一步：python analyze_real.py " + path)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="真机采集驱动（引导式，全量原始 dump）")
    ap.add_argument("--port", default="COM3", help="串口（蓝牙 SPP 传出口，非 COM1）")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--subject", default="sub01", help="受试者编号，用于文件名")
    ap.add_argument("--preset", choices=["d2", "d2v4", "free"], default="d2",
                    help="引导分段预设：d2=校准 A/B/C；d2v4=A/B/C + V4 静默/说话（一次采完）；"
                         "free=自由命名分段")
    ap.add_argument("--sim", action="store_true", help="无硬件自检（纯 tgam 合成帧）")
    ap.add_argument("--sim-sdk", action="store_true",
                    help="模拟官方 SDK（带 RR/心率/陀螺仪）验证分析器全分支")
    ap.add_argument("--frames", type=int, default=0, help="在线模式帧上限（0=不限）")
    ap.add_argument("--stop-after-preset", dest="stop_after_preset", action="store_true",
                    help="引导分段（D2/自由）采完立即收尾，不进入持续采集"
                         "——现场用启动器采集时省掉「还要按 Ctrl+C」这一步")
    args = ap.parse_args(argv)

    os.makedirs(DATA_DIR, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    path = os.path.join(DATA_DIR, f"real_{args.subject}_{stamp}.jsonl")

    if args.sim or args.sim_sdk:
        dump = RawDump(path)
        run_sim(dump, with_sdk=args.sim_sdk, preset=args.preset)
        dump.close()
        summarize(dump, path)
        return 0

    # 在线模式需要 pyserial；缺依赖时显式报错而非静默
    try:
        import serial  # noqa: F401
    except ImportError:
        print("✗ 缺少 pyserial（系统 Python 装：pip install pyserial；便携运行时已内置）。")
        return 2

    dump = RawDump(path)
    try:
        rc = run_live(args.port, args.baud, dump, args.preset, args.frames,
                      stop_after_preset=args.stop_after_preset)
    finally:
        dump.close()
    summarize(dump, path)
    return rc


if __name__ == "__main__":
    sys.exit(main())
