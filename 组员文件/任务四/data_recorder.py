# -*- coding: utf-8 -*-
"""data_recorder.py — 真机数据录制器：录制到 CSV + 回放喂回引擎

功能：
  1. 录制：包装 SdkAdapter 的回调，每次 EEG/RR/gyro 回调都写入 CSV，
     包含时间戳 + EEG 频段 + attention/meditation + heart + RR + gyro + signal
  2. 回放：读 CSV 文件，按时间间隔逐帧喂回 SdkAdapter，用于离线调参和回归验证

CSV 列：
  timestamp, signal, attention, meditation, delta, theta, alpha, beta, gamma,
  heart, rr1, rr2, rr3, gyro_x, gyro_y, gyro_z

用法：
  # 录制（配合 serial_bridge 使用）
  python data_recorder.py record --output my_session.csv

  # 回放（喂回引擎，离线分析）
  python data_recorder.py playback --input my_session.csv
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import time
from dataclasses import dataclass, field

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_CORE = _os.path.abspath(_os.path.join(_HERE, "..", "..", "项目文件"))
if _os.path.isdir(_CORE) and _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)   # 复用项目权威引擎；本目录不复制引擎实现，避免口径漂移

from state_engine import StateEngine
from sdk_adapter import SdkAdapter, SDKFrame


# ---------------------------------------------------------------- CSV 列定义
CSV_COLUMNS = [
    "timestamp", "signal", "attention", "meditation",
    "delta", "theta", "alpha", "beta", "gamma",
    "heart", "rr1", "rr2", "rr3",
    "gyro_x", "gyro_y", "gyro_z",
]


# ---------------------------------------------------------------- 录制器
class DataRecorder:
    """数据录制器：包装 SdkAdapter，同时把回调数据写入 CSV

    用法：
      recorder = DataRecorder("session.csv")
      adapter = recorder.make_adapter(engine)
      # 然后 adapter 就可以像普通 SdkAdapter 一样用，数据自动存盘
    """

    def __init__(self, filepath: str, flush_every: int = 10):
        self.filepath = filepath
        self.flush_every = flush_every      # 每 N 行 flush 一次（防断电丢数据）
        self._file = None
        self._writer = None
        self._row_count = 0
        self._last_gyro = (0.0, 0.0, 0.0)
        self._last_heart = None
        self._rr_pending = None            # 攒 RR 三值，和下一帧 EEG 一起写

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *args):
        self.close()

    def open(self):
        self._file = open(self.filepath, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._file, fieldnames=CSV_COLUMNS)
        self._writer.writeheader()
        self._row_count = 0
        print(f"[recorder] 开始录制 → {self.filepath}")

    def close(self):
        if self._file:
            self._file.flush()
            self._file.close()
            print(f"[recorder] 录制结束，共 {self._row_count} 行 → {self.filepath}")
            self._file = None

    def make_adapter(self, engine: StateEngine, signal_min: int = 0) -> SdkAdapter:
        """创建一个包装了录制功能的 SdkAdapter"""
        adapter = SdkAdapter(engine, signal_min=signal_min)
        # 包一层 onEEG，在调原方法的同时写 CSV
        orig_on_eeg = adapter.onEEG
        orig_on_extend = adapter.onExtendEEG
        orig_on_gyro = adapter.onGyro
        orig_on_rr = adapter.onRR

        def wrapped_onEEG(data):
            # 先调原方法（进引擎）
            orig_on_eeg(data)
            # 再写一行 CSV
            row = {
                "timestamp": time.time(),
                "signal": getattr(data, "signal", ""),
                "attention": getattr(data, "attention", ""),
                "meditation": getattr(data, "meditation", ""),
                "delta": getattr(data, "delta", ""),
                "theta": getattr(data, "theta", ""),
                "alpha": getattr(data, "lowAlpha", 0) + getattr(data, "highAlpha", 0),
                "beta": getattr(data, "lowBeta", 0) + getattr(data, "highBeta", 0),
                "gamma": getattr(data, "lowGamma", 0) + getattr(data, "highGamma", 0),
                "heart": self._last_heart if self._last_heart else "",
                "rr1": self._rr_pending[0] if self._rr_pending else "",
                "rr2": self._rr_pending[1] if self._rr_pending else "",
                "rr3": self._rr_pending[2] if self._rr_pending else "",
                "gyro_x": self._last_gyro[0],
                "gyro_y": self._last_gyro[1],
                "gyro_z": self._last_gyro[2],
            }
            self._writer.writerow(row)
            self._row_count += 1
            self._rr_pending = None
            if self._row_count % self.flush_every == 0:
                self._file.flush()

        def wrapped_onExtend(data):
            orig_on_extend(data)
            self._last_heart = getattr(data, "heart", None)

        def wrapped_onGyro(x, y, z):
            orig_on_gyro(x, y, z)
            self._last_gyro = (float(x), float(y), float(z))

        def wrapped_onRR(rr1, rr2, rr3):
            orig_on_rr(rr1, rr2, rr3)
            # RR 攒着，和下一帧 EEG 一起写（因为 RR 频率和 EEG 不同步）
            self._rr_pending = (rr1, rr2, rr3)

        adapter.onEEG = wrapped_onEEG
        adapter.onExtendEEG = wrapped_onExtend
        adapter.onGyro = wrapped_onGyro
        adapter.onRR = wrapped_onRR
        return adapter


# ---------------------------------------------------------------- 回放器
class DataPlayer:
    """数据回放器：读 CSV，逐帧喂回引擎

    用法：
      player = DataPlayer("session.csv")
      player.play(engine, speed=1.0)
    """

    def __init__(self, filepath: str):
        self.filepath = filepath
        self.rows: list[dict] = []
        self._load()

    def _load(self):
        if not os.path.exists(self.filepath):
            raise FileNotFoundError(f"回放文件不存在: {self.filepath}")
        with open(self.filepath, "r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                self.rows.append(row)
        print(f"[player] 已加载 {len(self.rows)} 行回放数据 ← {self.filepath}")

    def play(self, engine: StateEngine, speed: float = 1.0,
             print_interval: int = 100):
        """逐行回放数据喂回引擎

        Args:
            engine: 状态引擎实例
            speed: 回放速度倍数（1.0=原速，2.0=两倍速）
            print_interval: 每 N 行打印一次状态
        """
        if not self.rows:
            print("[player] 无数据可回放")
            return

        adapter = SdkAdapter(engine)
        t0 = time.time()
        ts0 = float(self.rows[0]["timestamp"])

        print(f"[player] 开始回放（速度 {speed}x）...")
        for i, row in enumerate(self.rows):
            # 按时间戳间隔控制回放节奏
            ts = float(row["timestamp"])
            target_elapsed = (ts - ts0) / speed
            real_elapsed = time.time() - t0
            sleep_s = target_elapsed - real_elapsed
            if sleep_s > 0:
                time.sleep(sleep_s)

            # 构造 SDKFrame 喂回 adapter
            data = SDKFrame(
                attention=float(row.get("attention") or 50.0),
                meditation=float(row.get("meditation") or 50.0),
                signal=int(float(row.get("signal") or 200)),
                delta=float(row.get("delta") or 0),
                theta=float(row.get("theta") or 0),
                lowAlpha=float(row.get("alpha") or 0) / 2,
                highAlpha=float(row.get("alpha") or 0) / 2,
                lowBeta=float(row.get("beta") or 0) / 2,
                highBeta=float(row.get("beta") or 0) / 2,
                lowGamma=float(row.get("gamma") or 0) / 2,
                highGamma=float(row.get("gamma") or 0) / 2,
            )

            # heart
            heart = row.get("heart")
            if heart and heart != "":
                adapter.onExtendEEG(_Ext(float(heart)))

            # gyro
            adapter.onGyro(
                int(float(row.get("gyro_x") or 0)),
                int(float(row.get("gyro_y") or 0)),
                int(float(row.get("gyro_z") or 0)),
            )

            # RR
            rr1, rr2, rr3 = row.get("rr1"), row.get("rr2"), row.get("rr3")
            if rr1 and rr2 and rr3 and rr1 != "":
                adapter.onRR(int(float(rr1)), int(float(rr2)), int(float(rr3)))

            # EEG 帧
            adapter.onEEG(data)

            if (i + 1) % print_interval == 0:
                dec = engine.last_decision
                if dec:
                    print(f"[player] 行 {i+1}/{len(self.rows)} "
                          f"state={dec.state} conf={dec.conf:.2f}")

        print(f"[player] 回放完成。最终状态: {engine.last_decision.state}")


@dataclass
class _Ext:
    """仿 BrainLinkExtendData"""
    heart: float = 75.0


# ---------------------------------------------------------------- 自测
def _self_test():
    """录制→回放闭环自测：mock 数据录一段，再回放喂回引擎"""
    import tempfile
    from serial_bridge import MockParser

    print("=== data_recorder 自测：录制→回放闭环 ===")

    # 1. 录制阶段：mock 数据跑 10s，存 CSV
    tmpdir = tempfile.gettempdir()
    csv_path = os.path.join(tmpdir, "test_recording.csv")

    engine1 = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    with DataRecorder(csv_path) as recorder:
        adapter = recorder.make_adapter(engine1)
        mock = MockParser(adapter, fps=10.0)
        mock.start()
        time.sleep(10)
        mock.stop()

    print(f"  录制完成，引擎状态: {engine1.last_decision.state}")

    # 2. 回放阶段：读 CSV，喂进一个新引擎，对比状态分布
    engine2 = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    player = DataPlayer(csv_path)
    player.play(engine2, speed=50.0)   # 50 倍速快速回放

    # 3. 验证：两个引擎的最终状态应该一致（同一份数据）
    s1 = engine1.last_decision.state
    s2 = engine2.last_decision.state
    print(f"\n  录制端最终状态: {s1}")
    print(f"  回放端最终状态: {s2}")
    assert s1 == s2, f"回放状态不一致: {s1} vs {s2}"
    print("  [PASS] 录制→回放闭环验证通过")


# ---------------------------------------------------------------- main
def main():
    parser = argparse.ArgumentParser(description="BrainLink 数据录制器/回放器")
    sub = parser.add_subparsers(dest="mode")

    # 录制命令
    rec = sub.add_parser("record", help="录制数据到 CSV")
    rec.add_argument("--output", "-o", required=True, help="输出 CSV 路径")
    rec.add_argument("--duration", "-d", type=float, default=0,
                     help="录制时长（秒，0=一直录）")
    rec.add_argument("--mock", action="store_true", help="用 mock 数据录制（无真机自测）")

    # 回放命令
    play = sub.add_parser("playback", help="从 CSV 回放数据")
    play.add_argument("--input", "-i", required=True, help="输入 CSV 路径")
    play.add_argument("--speed", "-s", type=float, default=1.0, help="回放速度倍数")

    args = parser.parse_args()

    if args.mode == "record":
        engine = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
        with DataRecorder(args.output) as recorder:
            adapter = recorder.make_adapter(engine)
            if args.mock:
                from serial_bridge import MockParser
                mock = MockParser(adapter, fps=10.0)
                mock.start()
                print(f"  mock 录制中，{args.duration}s 后停止...")
                try:
                    time.sleep(args.duration)
                except KeyboardInterrupt:
                    pass
                mock.stop()
            else:
                print("  真机录制模式（请配合 serial_bridge 使用）")
                print("  按 Ctrl+C 停止录制")
                try:
                    while True:
                        time.sleep(1)
                except KeyboardInterrupt:
                    pass

    elif args.mode == "playback":
        engine = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
        player = DataPlayer(args.input)
        player.play(engine, speed=args.speed)

    elif args.mode is None:
        # 无参数跑自测
        _self_test()


if __name__ == "__main__":
    main()
