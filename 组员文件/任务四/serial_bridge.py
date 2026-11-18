# -*- coding: utf-8 -*-
"""serial_bridge.py — 串口桥：BrainLink 串口 → BrainLinkParser → SdkAdapter → StateEngine

功能：
  1. 真机模式：打开串口（蓝牙虚拟 COM），逐字节读取，喂给官方 BrainLinkParser.parse()，
     parser 自动分发 5 个回调，SdkAdapter 把回调转成引擎 Frame，process_frame 进引擎。
  2. mock 模式：无真机时跑通全链路，用于开发自测和回归验证。

串口参数（硬件侧依据：补充说明 §二）：
  - 波特率 115200
  - TGAM 芯片，UART RS232
  - 蓝牙配对后会出两个虚拟 COM 口，选「输出」那个

用法：
  # 真机（Windows）
  python serial_bridge.py --port COM3 --baud 115200

  # macOS / Linux
  python serial_bridge.py --port /dev/cu.BrainLink_Pro --baud 115200

  # mock 自测（无真机，跑通全链路）
  python serial_bridge.py --mock
"""
from __future__ import annotations

import argparse
import math
import random
import signal as sig
import sys
import time
from dataclasses import dataclass

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_CORE = _os.path.abspath(_os.path.join(_HERE, "..", "..", "项目文件"))
if _os.path.isdir(_CORE) and _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)   # 复用项目权威引擎；本目录不复制引擎实现，避免口径漂移

from state_engine import Frame, StateEngine, RR_MIN_MS, RR_MAX_MS
from sdk_adapter import SdkAdapter, SDKFrame


# ---------------------------------------------------------------- mock 模拟器
class MockParser:
    """无真机时的 mock parser：定时产生模拟 EEG/RR/gyro 回调，走和真机一样的回调路径。

    用途：开发期自测串口桥→adapter→引擎全链路，不依赖硬件。
    数据特征参考 brain_sim_tb.py / state_engine._mock_frame 的口径。
    """

    def __init__(self, adapter: SdkAdapter, fps: float = 10.0):
        self.adapter = adapter
        self.interval = 1.0 / fps
        self._running = False
        self._rr_counter = 0

    def start(self):
        """后台线程模式启动 mock 数据流"""
        import threading
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def _loop(self):
        rnd = random.random
        while self._running:
            # 模拟一段专注稳态数据（θ 低、β 正常 + 心率平稳）
            theta = 55.0 * (0.9 + 0.2 * rnd())
            beta = 100.0 * (0.9 + 0.2 * rnd())
            alpha = 130.0 * (0.9 + 0.2 * rnd())
            delta = 100.0 * (0.9 + 0.2 * rnd())

            # EEG 回调
            data = SDKFrame(
                attention=65 + 5 * (rnd() - 0.5),
                meditation=55 + 5 * (rnd() - 0.5),
                signal=200,  # 良好信号
                delta=delta,
                theta=theta,
                lowAlpha=alpha * 0.5,
                highAlpha=alpha * 0.5,
                lowBeta=beta * 0.6,
                highBeta=beta * 0.4,
                lowGamma=15.0,
                highGamma=10.0,
            )
            self.adapter.onEEG(data)

            # 扩展回调（heart）—— 每 3 帧一次
            if self._rr_counter % 3 == 0:
                heart = 72 + 2 * (rnd() - 0.5)
                self.adapter.onExtendEEG(_MockExtend(heart))

            # gyro —— 体动很小
            self.adapter.onGyro(
                int(2 * (rnd() - 0.5)),
                int(2 * (rnd() - 0.5)),
                int(2 * (rnd() - 0.5)),
            )

            # RR 回调 —— 每 ~1s 一次（一次 3 个间期）
            # 抖动幅度 ±30ms，对应 RMSSD ≈ 20-30ms（正常静息范围）
            if self._rr_counter % 10 == 0:
                base_rr = 800 + 20 * (rnd() - 0.5)
                self.adapter.onRR(
                    int(base_rr + 30 * (rnd() - 0.5)),
                    int(base_rr + 30 * (rnd() - 0.5)),
                    int(base_rr + 30 * (rnd() - 0.5)),
                )

            self._rr_counter += 1
            time.sleep(self.interval)


@dataclass
class _MockExtend:
    """仿 BrainLinkExtendData"""
    heart: float = 75.0


# ---------------------------------------------------------------- 串口读取
class SerialBridge:
    """真机串口桥：打开串口 → 循环读字节 → parser.parse → 回调进引擎

    依赖：pyserial（pip install pyserial）
    官方 parser：BrainLinkParser.pyd / .so（见仓库根目录，需 Python 3.11）
    """

    def __init__(self, port: str, baud: int = 115200,
                 engine: StateEngine | None = None,
                 signal_min: int = 0):
        self.port = port
        self.baud = baud
        self.engine = engine or StateEngine(
            baseline=StateEngine.calibrate_from_mock([])
        )
        self.adapter = SdkAdapter(self.engine, signal_min=signal_min)
        self._ser = None
        self._parser = None
        self._running = False

    def _import_parser(self):
        """延迟导入官方 BrainLinkParser（真机模式才需要）"""
        try:
            from BrainLinkParser import BrainLinkParser
        except ImportError as e:
            raise RuntimeError(
                f"无法导入 BrainLinkParser（{e}）。\n"
                f"请确认：1) Python 版本为 3.11（.pyd 绑定）；"
                f"2) BrainLinkParser.pyd 在脚本同目录或 PYTHONPATH 中"
            )
        self._parser = BrainLinkParser(
            self.adapter.onEEG,
            self.adapter.onExtendEEG,
            self.adapter.onGyro,
            self.adapter.onRR,
            None,  # raw_callback 不需要
        )

    def start(self):
        """打开串口并开始循环读取（阻塞）"""
        import serial  # pyserial

        self._import_parser()
        self._ser = serial.Serial(self.port, self.baud, timeout=1)
        self._running = True
        print(f"[serial_bridge] 已打开 {self.port} @ {self.baud}bps，开始读取...")
        print(f"[serial_bridge] 按 Ctrl+C 停止")

        try:
            while self._running:
                raw = self._ser.read(1)
                if raw:
                    self._parser.parse(raw)
        except KeyboardInterrupt:
            print("\n[serial_bridge] 收到中断，停止读取")
        finally:
            self.stop()

    def stop(self):
        self._running = False
        if self._ser and self._ser.is_open:
            self._ser.close()
            print(f"[serial_bridge] 串口 {self.port} 已关闭")


# ---------------------------------------------------------------- 状态打印
def _print_loop(engine: StateEngine, interval: float = 1.0):
    """后台打印引擎状态（每 interval 秒一次）"""
    while True:
        time.sleep(interval)
        dec = engine.last_decision
        fb = engine.last_feedback
        if dec:
            feats = engine._extract()
            print(f"[状态] state={dec.state:10s} conf={dec.conf:.2f} "
                  f"R_tb={feats.R_tb:.2f} HR={feats.HR:.0f} "
                  f"RMSSD={feats.RMSSD_60:.1f}ms "
                  f"| 反馈: [{fb.level}] {fb.action}")


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="BrainLink 串口桥")
    parser.add_argument("--port", type=str, default=None,
                        help="串口名（Windows: COM3, macOS: /dev/cu.BrainLink_Pro）")
    parser.add_argument("--baud", type=int, default=115200,
                        help="波特率（默认 115200）")
    parser.add_argument("--signal-min", type=int, default=0,
                        help="signal 质量下限（0=不过滤；真机标定后设置）")
    parser.add_argument("--mock", action="store_true",
                        help="mock 模式（无真机，模拟数据跑通全链路）")
    parser.add_argument("--duration", type=float, default=0,
                        help="运行时长（秒，0=一直跑）；mock 模式下用于自动测试")
    args = parser.parse_args()

    # 初始化引擎 + 适配器
    engine = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    adapter = SdkAdapter(engine, signal_min=args.signal_min)

    if args.mock:
        print("=== serial_bridge mock 模式（无真机自测）===")
        mock = MockParser(adapter, fps=10.0)
        mock.start()

        # 打印线程
        import threading
        printer = threading.Thread(
            target=_print_loop, args=(engine, 1.0), daemon=True
        )
        printer.start()

        # 跑指定时长或一直跑
        if args.duration > 0:
            print(f"[mock] 运行 {args.duration}s 后自动停止...")
            time.sleep(args.duration)
            mock.stop()
            # 打印最终统计
            dec = engine.last_decision
            print(f"\n[mock] 最终状态: {dec.state} (conf={dec.conf:.2f})")
            print(f"[mock] 理智度: calmness={engine.calmness:.2f}")
            print(f"[mock] EEG 缓冲帧数: {len(engine.eeg_buf)}")
            print(f"[mock] RR 缓冲个数: {len(engine.rr_buf)}")
            print("mock 自测完成。")
        else:
            print("按 Ctrl+C 停止...")
            try:
                while True:
                    time.sleep(1)
            except KeyboardInterrupt:
                mock.stop()
                print("\n已停止 mock。")
    else:
        if not args.port:
            print("错误：真机模式需要指定 --port（如 COM3），或加 --mock 用模拟数据")
            sys.exit(1)
        bridge = SerialBridge(
            port=args.port, baud=args.baud,
            engine=engine, signal_min=args.signal_min
        )
        bridge.start()


if __name__ == "__main__":
    main()
