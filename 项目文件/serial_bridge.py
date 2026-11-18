# -*- coding: utf-8 -*-
"""serial_bridge.py — T4 真机联调工具链：串口桥 + 数据录制器 + 回放（组长侧参考实现）

任务4 验收项对应
----------------
4.1 串口 → BrainLinkParser → SdkAdapter 回调 → 引擎 全链路通，且有 mock 串口消息验证
4.2 真机数据存 CSV（EEG 频段/attention/meditation/HR/RR/gyro/signal/时间戳），**CSV 可回放进引擎**
4.5 代码放 项目文件\、命名合规、接入说明可读

三种运行方式
------------
    # ① 无真机自检：不碰串口，用 SDK 同构帧证明"回调→适配器→引擎"链路（验收 4.1 的 mock 部分）
    python serial_bridge.py --mock --frames 60

    # ② 真机采集 + 录制（需 pyserial + BrainLinkParser）
    python serial_bridge.py --port COM5 --baud 115200 --record data/subj01_A.csv --signal-max 60

    # ③ 录制回放（离线调参闭环：不反复佩戴，参数改动先在回放上验证）
    python serial_bridge.py --replay data/subj01_A.csv --baseline data/subj01_base.json

真机数据不进 git、不外发；CSV 只落本机 --record 指定路径（默认 data/）。
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import sys
import time

from sdk_adapter import SDKFrame, SdkAdapter
from state_engine import Frame, StateEngine
from frameio import row_to_frame

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(HERE, "data")

# 录制列（验收 4.2 要求逐项覆盖；顺序固定，便于 pandas/Excel 直接读）
CSV_FIELDS = ["ts", "theta", "alpha", "beta", "delta", "gamma",
              "attention", "meditation", "hr", "rr_ms", "signal",
              "gyro_x", "gyro_y", "gyro_z", "state", "conf",
              # v3.2：onRR 一次回调带 3 个连续间期。原实现只落盘最后 1 个（丢 2/3），
              # 且回放用 onRR(rr,0,0) 给每个 RR 单独一个批次号 → _rmssd 里全是跨批、
              # 全部 continue → RMSSD 恒 nan（stress/anxiety 的关键特征在回放路径上失效）。
              # 三列并排即隐式表达"同一批"，回放时一次喂回 onRR(r1,r2,r3) 恢复同批语义。
              "rr1", "rr2", "rr3"]


# --------------------------------------------------------------------------- 录制器
class Recorder:
    """逐行 flush 的 CSV 录制器。

    逐行 flush 是刻意的：真机采集中途断电/Ctrl+C 时，缓冲里未落盘的数据正好是最关键的
    最后几十秒。代价是磁盘 IO 略多，但 1.2s/帧 的写入频率完全无所谓。
    """

    def __init__(self, path: str):
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        self.fh = open(path, "w", encoding="utf-8-sig", newline="")
        self.writer = csv.DictWriter(self.fh, fieldnames=CSV_FIELDS)
        self.writer.writeheader()
        self.fh.flush()
        self.rows = 0
        self.path = path
        # v3.4.2：落盘失败必须留痕。原来 write 里的异常会冒泡到 parse() 的 except，
        # 现场只看到"解析失败（继续）"——真实原因是磁盘满/文件被 Excel 占用，
        # 结果是**整个会话一帧都没录上**却显示"录制成功"。
        self.error: str | None = None

    def write(self, row: dict) -> None:
        if self.error:                    # 已判定失败：不重试（避免每帧刷屏），但保持沉默记账
            return
        try:
            self.writer.writerow({k: row.get(k, "") for k in CSV_FIELDS})
            self.fh.flush()
        except (OSError, ValueError) as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            print(f"[bridge] [错误] CSV 写入失败，录制已中断（已成功写 {self.rows} 行）：{self.error}",
                  file=sys.stderr, flush=True)
            print("  常见原因：磁盘已满 / 目标 CSV 正被 Excel 打开 / 移动硬盘被拔出。\n"
                  "  采集会继续（判据照常输出），但这段数据未落盘——修好后重启录制。",
                  file=sys.stderr, flush=True)
            return
        self.rows += 1

    def close(self) -> None:
        if self.fh.closed:
            return
        try:
            self.fh.close()
        except OSError as exc:
            print(f"[bridge] [警告] CSV 关闭失败：{exc}（末尾若干行可能未落盘，请打开文件核对行数）",
                  file=sys.stderr, flush=True)


def make_recorder(path: str | None) -> "Recorder | None":
    """打开录制器；失败则**显式中止**（退出码 4），不静默降级。

    为什么单独抽出来：`--record` 指向一个目录、CSV 正被 Excel 打开、或目标盘无写权限时，
    原实现直接抛裸 traceback。而如果改成"悄悄不录"，用户会以为录上了——对一个
    "采集完要去答辩/写报告"的工具来说，这比报错更危险。
    """
    if not path:
        return None
    try:
        return Recorder(path)
    except OSError as exc:
        print(f"[bridge] [错误] 无法创建录制文件：{path}\n  {type(exc).__name__}: {exc}",
              file=sys.stderr)
        print("  排查：① 路径是不是一个已存在的**目录**（应写成 xxx.csv）\n"
              "        ② 同名 CSV 是否正被 Excel/WPS 打开（先关掉）\n"
              "        ③ 目标盘（U 盘/移动硬盘）是否已满或只读", file=sys.stderr)
        raise SystemExit(4) from exc


# --------------------------------------------------------------------------- 引擎装配
def build_engine(baseline_path: str | None) -> StateEngine:
    if baseline_path:
        with open(baseline_path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
        raw = raw.get("baseline", raw)
        base = StateEngine.calibrate_from_mock([])
        for k, v in raw.items():
            if k in base and hasattr(base[k], "mu") and isinstance(v, dict):
                base[k].mu = float(v.get("mu", base[k].mu))
                base[k].sigma = float(v.get("sigma", base[k].sigma))
                base[k].sigma_min = float(v.get("sigma_min", base[k].sigma_min))
            elif isinstance(v, (int, float)):
                base[k] = float(v)
        eng = StateEngine(baseline=base)
        print(f"[bridge] 已加载校准基线 {baseline_path}（theta_ref={base.get('theta_ref')}）")
        return eng
    return StateEngine(baseline=StateEngine.calibrate_from_mock([]))


def _fnum(v) -> float | None:
    """CSV 单元格 → float；空串/None/非法一律 None（不把缺值当 0 用）。"""
    if v is None or v == "":
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _row_from(frame: Frame, state: str, conf: float, hr: float | None, signal: int,
              rr_batch: tuple | None = None) -> dict:
    """一帧 → CSV 行。

    rr_batch：最近一次 onRR 回调的三个原始间期 (r1, r2, r3)，**同批语义必须整批落盘**。
    rr_ms 保留为兼容字段（旧 CSV / 单值模拟路径），新录制以 rr1–rr3 为准。
    """
    gyro = frame.gyro or (0.0, 0.0, 0.0)
    r1, r2, r3 = (None, None, None) if not rr_batch else rr_batch
    # 单值（模拟路径）没有批次信息：rr1 填该值、rr2/rr3 留空，回放按"批内单元素"处理即可
    if r1 is None and frame.rr_ms is not None:
        r1 = frame.rr_ms
    row = {
        "ts": round(frame.ts or time.time(), 3),
        "theta": round(frame.theta, 3), "alpha": round(frame.alpha, 3),
        "beta": round(frame.beta, 3), "delta": round(frame.delta, 3),
        "gamma": round(frame.gamma, 3),
        "attention": round(frame.attention, 1), "meditation": round(frame.meditation, 1),
        "hr": "" if hr is None else round(hr, 1),
        "rr_ms": "" if frame.rr_ms is None else round(frame.rr_ms, 1),
        "signal": signal,
        "gyro_x": round(float(gyro[0]), 4), "gyro_y": round(float(gyro[1]), 4),
        "gyro_z": round(float(gyro[2]), 4),
        "state": state, "conf": round(conf, 3),
    }
    for key, val in (("rr1", r1), ("rr2", r2), ("rr3", r3)):
        row[key] = "" if val in (None, 0) else round(float(val), 1)
    return row


# --------------------------------------------------------------------------- ① mock 自检
def run_mock(engine: StateEngine, frames_limit: int, record: str | None) -> int:
    """不碰串口，用 SDK 同构帧走真实回调路径（验收 4.1 mock 部分）。

    覆盖：字段合并（low/high → alpha/beta/gamma）、RR 三值分批、gyro 透传、
    适配器默认 ctx（task_active=True）——即 10-23 修的 P1-2 路径。
    断言（不是"看着像对"）：合并口径、ctx、引擎确实产出决策、至少判出 2 个非 neutral 状态。
    """
    # mock 的 signal 必须与帧里灌进适配器的值同源：原实现帧里 signal=30、写 CSV 时另写死 200，
    # 结果 mock 录出来的 CSV 全是"电极未接触皮肤"档，V3 恒 FAIL——用 mock 练手的人会误判成硬件坏了。
    MOCK_SIGNAL = 30
    adapter = SdkAdapter(engine)
    rec = make_recorder(record)
    # mock 需要虚拟时钟：真机 1.2s/帧，而脚本循环是毫秒级——不注入的话 v2.7 时间戳滑动窗
    # （10s EEG / 60s HRV）永远不滑，新旧相位混在中位数里，状态判不出来（曾误判为引擎缺陷）
    vt = [0.0]
    orig_clock = engine._clock
    engine._clock = lambda: (vt.__setitem__(0, vt[0] + 1.2), vt[0])[1]
    merged_ok = {"v": False}
    phases = [("focus", 55, 130, 100, 65, 55),
              ("distracted", 160, 100, 100, 45, 45),
              ("stress", 90, 110, 100, 40, 30),
              ("relaxed", 100, 160, 100, 50, 70)]
    seen: list[str] = []
    frames_seen: list[Frame] = []
    i = 0
    stop = False
    for phase, th, al, be, att, med in phases:
        for k in range(40):
            if frames_limit and i >= frames_limit:
                stop = True
                break
            data = SDKFrame(
                theta=th, lowAlpha=al / 2, highAlpha=al / 2,
                lowBeta=be / 2, highBeta=be / 2,
                lowGamma=25.0, highGamma=25.0,
                attention=att, meditation=med, signal=MOCK_SIGNAL,
                delta=100.0,
            )
            adapter.onEEG(data)
            if i == 0 and engine.eeg_buf:
                merged_ok["v"] = abs(engine.eeg_buf[-1].alpha - al) < 1e-6   # low+high 合并口径
            adapter.onGyro(0.01, 0.01, 0.01)
            # v3.2：一次 onRR 必须带 3 个连续间期（原写 onRR(x, 0, 0) → 每批仅 1 个元素，
            # _rmssd 里相邻对全部跨批直接 continue，diffs 为空 → RMSSD 恒 nan）。
            # v3.3：stress 段需单独给低 HRV——原四相位共用同一批常量差分（RMSSD 恒 11.4ms，
            # z=(11.4-15)/5=-0.72 够不到 Z_STRESS=-1.5），导致 mock 里 stress **从来没被判出过**，
            # 而断言只要求"≥2 个非 neutral"，照样绿 —— 典型的"绿了但没覆盖"。
            if phase == "stress":
                rr_batch = (800.0, 801.0, 799.0)      # 批内差分 +1/-2 → RMSSD≈1.6ms → z≈-2.7
            else:
                rr_batch = (800.0 + (k % 3) * 12, 808.0 + (k % 3) * 12, 794.0 + (k % 3) * 12)
            adapter.onRR(*rr_batch)
            dec = engine.last_decision
            seen.append(dec.state if dec else "neutral")
            if rec:
                rec.write(_row_from(Frame(theta=th, alpha=al, beta=be, delta=100.0,
                                          gamma=50.0, attention=att, meditation=med,
                                          rr_ms=rr_batch[0],
                                          gyro=(0.01, 0.01, 0.01), ts=vt[0]),
                                    dec.state if dec else "neutral",
                                    dec.conf if dec else 0.0, 75.0, MOCK_SIGNAL,
                                    rr_batch=rr_batch))
            i += 1
        if stop:
            break
    if rec:
        rec.close()
        print(f"[bridge] 录制 {rec.rows} 行 → {rec.path}")

    non_neutral = sorted(set(s for s in seen if s != "neutral"))
    last = engine.last_decision
    engine._clock = orig_clock            # 还原真实时钟（同一进程后续若继续跑不要被虚拟时钟污染）
    rmssd = StateEngine._rmssd(engine.rr_buf)
    # 反例守卫：若有人把 onRR 写回 (x, 0, 0)，每批只剩 1 个元素 → 相邻对全部跨批 →
    # diffs 为空 → RMSSD=nan。此时 stress/anxiety 的关键特征静默失效，而"RR 已入缓冲"
    # 这类弱断言照样为真。所以这里直接断言"算得出来"，不只断言"有数据"。
    checks = {
        "帧数一致": len(seen) == i and i > 0,
        "适配器默认 task_active": adapter.ctx.get("task_active") is True,
        "频段合并（alpha=low+high）": merged_ok["v"],
        "引擎产出决策": last is not None,
        # v3.3：原为"至少 2 个非 neutral"——四相位里 stress 其实一直判不出，断言照样绿。
        # 收紧为四相位全覆盖：任一相位判不出立刻 FAIL（"覆盖"比"数量"更能说明问题）。
        "四相位全部判出（focus/distracted/stress/relaxed）":
            set(non_neutral) >= {"focus", "distracted", "stress", "relaxed"},
        "RR 已入缓冲（同批三值）": len(engine.rr_buf) >= 8,
        "RMSSD 算得出来（同批差分有效）": math.isfinite(rmssd),
    }
    for k, v in checks.items():
        print(f"[bridge]   {'OK ' if v else 'FAIL'} {k}")
    print(f"[bridge] mock 链路完成：{i} 帧，非 neutral 状态 {non_neutral}，末态={seen[-1] if seen else '-'}")
    ok = all(checks.values())
    if not ok and frames_limit:
        # 帧数截断是"数据不够"，不是"代码坏了"——不提示的话 FAIL 容易被当成缺陷排查
        print(f"[bridge] 提示：当前 --frames {frames_limit} 只跑了 {i} 帧（1 帧≈1.2s）。"
              f"少于约 80 帧时状态样本不足，属预期；要完整自检请去掉 --frames 或设 ≥160")
    print("[bridge] 4.1 mock 链路 " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


# --------------------------------------------------------------------------- ② 真机
def run_serial(engine: StateEngine, port: str, baud: int, signal_max: int,
               record: str | None) -> int:
    try:
        import serial                                    # type: ignore
    except ImportError:
        print("缺少 pyserial：pip install pyserial（真机串口需要）", file=sys.stderr)
        return 2
    try:
        from BrainLinkParser import BrainLinkParser      # type: ignore
        parser_src = "官方 BrainLinkParser.pyd（含 RR / gyro / 心率扩展）"
    except ImportError:
        # 官方 .pyd 是 Python 3.11 的闭源二进制；本机 3.13 装不上时自动走纯 Python 兜底。
        # 代价明确：tgam_parser 只解 TGAM 标准帧 → RR/心率/gyro 不输出，HRV 判据须降级。
        from tgam_parser import BrainLinkParser
        parser_src = ("纯 Python tgam_parser（仅 EEG 主干；不输出 RR/心率/gyro → "
                      "stress/anxiety 的 HRV 判据降级为佐证）")
    print(f"[bridge] 解析器：{parser_src}", flush=True)

    adapter = SdkAdapter(engine, signal_max=signal_max)
    rec = make_recorder(record)
    latest_hr: list[float | None] = [None]
    latest_signal: list[int] = [0]
    last_frame: list[Frame | None] = [None]
    # v3.2：整批三值（原 list[float] 只留最后一个 → 一批 3 个间期丢 2 个，且丢批次信息）
    last_rr: list[tuple | None] = [None]

    def on_eeg(d) -> None:
        before = engine.last_decision
        adapter.onEEG(d)                                  # 适配器内部完成信号门控 + 合并 + 入引擎
        latest_signal[0] = int(getattr(d, "signal", 0) or 0)
        if engine.last_decision is before:                # 被信号门控丢弃
            return
        fr = Frame(attention=float(getattr(d, "attention", 50)),
                   meditation=float(getattr(d, "meditation", 50)),
                   delta=float(getattr(d, "delta", 0)),
                   theta=float(getattr(d, "theta", 0)),
                   alpha=float(getattr(d, "lowAlpha", 0) + getattr(d, "highAlpha", 0)),
                   beta=float(getattr(d, "lowBeta", 0) + getattr(d, "highBeta", 0)),
                   gamma=float(getattr(d, "lowGamma", 0) + getattr(d, "highGamma", 0)),
                   rr_ms=(last_rr[0][0] if last_rr[0] else None),
                   gyro=adapter._last_gyro, ts=time.time())
        last_frame[0] = fr
        if rec:
            dec = engine.last_decision
            rec.write(_row_from(fr, dec.state if dec else "neutral",
                                dec.conf if dec else 0.0, latest_hr[0], latest_signal[0],
                                rr_batch=last_rr[0]))

    def on_ext(d) -> None:
        adapter.onExtendEEG(d)
        h = getattr(d, "heart", None)
        if h:
            latest_hr[0] = float(h)

    def on_rr(r1, r2, r3) -> None:
        adapter.onRR(r1, r2, r3)                          # 同批三值进引擎（batch 由 adapter 递增）
        keep = tuple(float(r) if r and 300 <= float(r) <= 2000 else None
                     for r in (r1, r2, r3))
        if any(keep):
            last_rr[0] = keep                             # 整批保留，供落盘（v3.2）

    parser = BrainLinkParser(on_eeg, on_ext, adapter.onGyro, on_rr, None)
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as exc:                              # 现场最高频的失败点，别甩裸 traceback
        print(f"[bridge] 串口打开失败 {port}@{baud}：{exc}", file=sys.stderr)
        print("[bridge] 逐条排查：\n"
              "  ① 设备管理器 →「端口(COM 和 LPT)」确认实际 COM 号（不是 COM99 这类占位）\n"
              "  ② BrainLink 配对后会生成**两个**虚拟 COM 口，必须选「传出/输出」那个，\n"
              "     选错的表现就是能打开但永远 read 不到数据（官方 FAQ 明确说明）\n"
              "  ③ 头环已开机、蓝牙未被手机 App 占用（切 App 前先清后台）\n"
              "  ④ 波特率 115200（官方 sample 与说明书一致）\n"
              "  ⑤ Windows 蓝牙配对时选名字前带耳机图标的那个设备", file=sys.stderr)
        return 3
    print(f"[bridge] 串口 {port}@{baud} 已打开；Ctrl+C 结束（录制：{record or '关闭'}）")
    if hasattr(ser, "reset_input_buffer"):
        ser.reset_input_buffer()
    # 静默看门狗：串口"能打开"完全不等于"有数据"。实测打开主板自带 COM1 会打印
    # "已打开"然后 10s 零字节——现场最容易被误判成"正在采集"的失败模式，必须主动报。
    t_open = time.monotonic()
    t_last_rx = t_open          # 最后一次收到字节的时刻（采集中途断流检测用）
    nbytes = 0
    warned = False
    try:
        while True:
            try:
                raw = ser.read(64)
            except Exception as exc:                              # 串口被拔/被占用/蓝牙断开
                print(f"[bridge] 串口读失败（{type(exc).__name__}）：{exc}", file=sys.stderr)
                print("  采集结束。串口可能已被拔出、被其他程序占用，或蓝牙已断开。", file=sys.stderr)
                break
            if raw:
                nbytes += len(raw)
                t_last_rx = time.monotonic()
                if warned and nbytes <= len(raw):
                    print(f"[bridge] 已收到数据（累计 {nbytes} 字节），通道正常", flush=True)
                try:
                    parser.parse(raw)
                except Exception as exc:                  # 单条坏包不终止采集
                    print(f"[bridge] 解析失败（继续）：{exc}")
            elif not warned and nbytes == 0 and time.monotonic() - t_open > 15:
                warned = True
                print(f"[bridge] [警告] 已 15s 未收到任何字节（{port}）——串口开着，但设备没在发数据。",
                      file=sys.stderr, flush=True)
            # v3.7.1：采集中途断流 —— 原实现只在首次 15s 无字节时告警一次，
            # 采到一半设备掉线（蓝牙断/休眠）就再无提示，用户以为还在录。
            if not raw and nbytes > 0 and time.monotonic() - t_last_rx > 30:
                print(f"[bridge] [警告] 已 30s 无新数据（累计 {nbytes} 字节）——设备可能掉线/休眠，",
                      file=sys.stderr, flush=True)
                print("  录制未中断，恢复供数后自动继续；长时间不恢复请按 Ctrl+C 结束。",
                      file=sys.stderr, flush=True)
                t_last_rx = time.monotonic()      # 每 30s 提示一次，不刷屏
                print("  ① 最可能：选到了「传入」口或主板自带口（如 COM1）→ 换另一个 COM 口试\n"
                      "  ② 其次：头环被手机 App 占用 → 关 App / 清后台\n"
                      "  ③ 再则：头环已休眠 → 按电源键唤醒\n"
                      "  （采集未中断；一旦收到数据会自动停报）", file=sys.stderr, flush=True)
    except KeyboardInterrupt:
        print("\n[bridge] 已停止")
    finally:
        if rec:
            rec.close()
            if rec.error:
                print(f"[bridge] 录制 **未完整**：{rec.rows} 行已落盘，之后写入失败（{rec.error}）"
                      f" → {rec.path}", file=sys.stderr)
            else:
                print(f"[bridge] 录制 {rec.rows} 行 → {rec.path}")
        try:
            ser.close()
        except OSError as exc:
            # 串口关闭失败不致命，但必须留痕：Windows 上常见于句柄被其它进程占用，
            # 关不掉的串口会让「换口重连」失败，现场需要知道这一点。
            print(f"[bridge] [警告] 串口未正常关闭：{exc}（若重连失败请拔插数据线/重启蓝牙）",
                  file=sys.stderr, flush=True)
    return 0


# --------------------------------------------------------------------------- ③ 回放
def run_replay(engine: StateEngine, path: str, speed: float, realtime: bool) -> int:
    """CSV → SdkAdapter → 引擎（与在线采集同一条代码路径，参数可比）。"""
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        rows = list(csv.DictReader(fh))
    if not rows:
        print("CSV 为空", file=sys.stderr)
        return 2
    # v3.15：按 ts 稳定排序再回放。引擎的窗口滑动 / 时间确认 / I_f 积分都以 ts 单调
    # 为前提；手工编辑或合并过的 CSV 一旦乱序，`ts - buf[0].ts` 变负 → 窗口不裁剪、
    # 确认窗口权重算成负数，判别会静默偏掉。缺 ts 的行按 0 排在前面（稳定序）。
    rows.sort(key=lambda r: _fnum(r.get("ts")) or 0.0)
    adapter = SdkAdapter(engine)
    prev_ts = None
    timeline: list[str] = []
    # 回放用 CSV 时间戳驱动引擎时钟：加速回放（不 sleep）时若用墙钟，滑动窗永不滑，
    # 判别结果会与真实采集不一致——离线调参就失去意义。这里让"虚拟时间"严格等于 CSV ts。
    vt = [0.0]
    orig_clock = engine._clock
    engine._clock = lambda: vt[0]
    skipped = 0
    for row in rows:
        ts = _fnum(row.get("ts")) or 0.0
        if ts > 0:
            vt[0] = ts
        if realtime and prev_ts is not None and ts > prev_ts:
            time.sleep(min((ts - prev_ts) / max(speed, 1e-6), 5.0))
        prev_ts = ts or prev_ts
        try:
            # v3.16：统一走 frameio（与 live_bridge 回放同一套字段/默认值）。
            fr = row_to_frame(row)
            data = SDKFrame(
                theta=fr["theta"], delta=fr["delta"],
                lowAlpha=fr["alpha"] / 2, highAlpha=fr["alpha"] / 2,
                lowBeta=fr["beta"] / 2, highBeta=fr["beta"] / 2,
                lowGamma=fr["gamma"] / 2, highGamma=fr["gamma"] / 2,
                attention=fr["attention"], meditation=fr["meditation"],
                signal=int(fr["signal"]) if fr["signal"] is not None else 0,
            )
            adapter.onEEG(data)
            # RR 优先按"同批三值"喂回（同一次 onRR 的三个连续间期）；旧 CSV 只有单值
            # 时走 batch=None，允许跨元素差分（绝不能造"每批 1 个"的假批次）。
            if fr["rr_batch"]:
                rb = (list(fr["rr_batch"]) + [0, 0, 0])[:3]
                adapter.onRR(*rb)
            elif fr["rr_ms"] is not None:
                engine.push_rr(fr["rr_ms"], batch=None)
            gx, gy, gz = fr["gyro"]
            if gx or gy or gz:
                adapter.onGyro(gx, gy, gz)
        except (TypeError, ValueError, KeyError) as exc:      # 单行脏数据不中断整段回放
            skipped += 1
            if skipped <= 3:
                print(f"[bridge] 跳过无法回放的行（ts={ts}）：{type(exc).__name__}: {exc}",
                      file=sys.stderr)
            continue
        dec = engine.last_decision
        if dec and (not timeline or timeline[-1][0] != dec.state):
            timeline.append((dec.state, ts))
    has_batch = any(_fnum(r.get("rr1")) is not None for r in rows)
    # 时间线用**相对秒**（原实现直接打 CSV 的 epoch 秒 → "1700000000s"，现场没法读）
    t0 = _fnum(rows[0].get("ts")) or 0.0
    print(f"[bridge] 回放 {len(rows)} 帧（{'实时' if realtime else '加速'} 模式）")
    print("[bridge] 状态时间线：" + " → ".join(
        f"{s}@{max(t - t0, 0):.0f}s" for s, t in timeline[:30]))
    rmssd = StateEngine._rmssd(engine.rr_buf)
    print(f"[bridge] 末态={engine.last_decision.state} I_f={engine._theta_int:.1f}s "
          f"calmness={('—' if not math.isfinite(engine.calmness) else f'{engine.calmness:.2f}')} RR={len(engine.rr_buf)} "
          f"RMSSD={'nan' if not math.isfinite(rmssd) else f'{rmssd:.1f}ms'}")
    if skipped:
        print(f"[bridge] [警告] 共有 {skipped}/{len(rows)} 行无法回放（数值列含非法字符），"
              f"已跳过——结果只代表其余 {len(rows) - skipped} 行。", file=sys.stderr)
    if not has_batch:
        print("[bridge] 注意：该 CSV 无 rr1–rr3 列（v3.1 及以前的旧录制），RR 只能按单值"
              "路径回放——RMSSD 窗会比在线更早凑够差分对，属降级口径，重新录制可消除差异。")
    engine._clock = orig_clock
    return 0


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="T4 真机串口桥 / 录制器 / 回放")
    ap.add_argument("--mock", action="store_true", help="无真机自检（真实回调路径 + SDK 同构帧）")
    ap.add_argument("--frames", "--seconds", dest="frames", type=int, default=0,
                    help="mock 模式帧数上限（0=跑完四个相位共 160 帧）。"
                         "注意是帧数不是秒数：1 帧≈1.2s，--frames 10 只跑 12 秒的数据量")
    ap.add_argument("--port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200)
    ap.add_argument("--signal-max", dest="signal_max", type=int, default=0,
                    help="signal 门控上限（poorSignalLevel 越小越好；0=不过滤）")
    ap.add_argument("--record", nargs="?", const="auto", default=None,
                    help="录制 CSV 路径（不给值则自动 data/rec_<时间>.csv）")
    ap.add_argument("--replay", metavar="CSV", default=None)
    ap.add_argument("--speed", type=float, default=0.0, help="回放倍速（0=不等待，全速）")
    ap.add_argument("--realtime", action="store_true", help="回放按 CSV 时间戳节奏")
    ap.add_argument("--baseline", default=None, help="校准基线 JSON（真机必用）")
    args = ap.parse_args(argv)

    engine = build_engine(args.baseline)

    if args.replay:
        return run_replay(engine, args.replay, args.speed or 1.0, args.realtime)

    record = args.record
    if record == "auto" or (record and not record.strip()):
        os.makedirs(DATA_DIR, exist_ok=True)
        record = os.path.join(DATA_DIR, "rec_" + time.strftime("%Y%m%d_%H%M%S") + ".csv")

    if args.mock:
        return run_mock(engine, args.frames, record)
    return run_serial(engine, args.port, args.baud, args.signal_max, record)


if __name__ == "__main__":
    sys.exit(main())
