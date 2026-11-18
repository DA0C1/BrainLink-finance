# -*- coding: utf-8 -*-
"""sdk_adapter.py — BrainLink SDK 回调 → state_engine.Frame 适配层（真机接入桥）

官方 SDK（Macrotellect/BrainLinkParser-Python，2026-10-16 官方 README 核实）回调形态：
- eeg_callback(BrainLinkData)：signal, attention, meditation, delta, theta,
  lowAlpha, highAlpha, lowBeta, highBeta, lowGamma, highGamma（分离字段）
- eeg_extend_callback(BrainLinkExtendData)：ap, battery, version, gnaw, temperature, heart
- gyro_callback(x, y, z)：整数
- rr_callback(rr1, rr2, rr3)：整数 RR 间期（一次 3 个）

适配要点（对应《状态覆盖 × SDK 兼容性检查》缺口，报告 2026-10-16）：
1. 🔴 字段分离：low/high 合并为 alpha/beta/gamma（Frame 口径），delta/theta/attention/meditation 透传
2. 🔴 RR 三值：经 engine.push_rr() 逐个灌入 HRV 缓冲（HRV 窗按 RR 间期序列，与 EEG 帧率解耦）
3. ⚠️ heart/RR 分工：heart(bpm) 直读作 HR 特征，RR 只供 RMSSD（state_engine._extract 已支持）
4. ✅ 归一化口径：SDK 数值为归一化能量强度，比值特征有效（现有 z-score 体系兼容，无需改）

用法（真机，需安装 BrainLinkParser）：
  from BrainLinkParser import BrainLinkParser
  adapter = SdkAdapter(engine)
  parser = BrainLinkParser(adapter.onEEG, adapter.onExtendEEG, adapter.onGyro, adapter.onRR, None)
  串口消息 -> parser.parse(msg)   # 回调自动进引擎

用法（无 SDK 时离线验证适配层与字段合并）：
  python sdk_adapter.py
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass

from state_engine import Frame, StateEngine, RR_MIN_MS, RR_MAX_MS


@dataclass
class SDKFrame:
    """仿 BrainLinkData（SDK 回调对象的最小结构，无 SDK 离线验证用）"""
    attention: float = 50.0
    meditation: float = 50.0
    signal: int = 0              # poorSignalLevel：0=最好，200=电极未接触皮肤（越小越好）
    delta: float = 0.0
    theta: float = 0.0
    lowAlpha: float = 0.0
    highAlpha: float = 0.0
    lowBeta: float = 0.0
    highBeta: float = 0.0
    lowGamma: float = 0.0
    highGamma: float = 0.0


class SdkAdapter:
    """SDK 回调 → state_engine 适配器"""

    def __init__(self, engine: StateEngine, signal_max: int = 0, ctx: dict | None = None):
        self.engine = engine
        # 信号门控上限（0=不额外过滤）：TGAM poorSignalLevel **越小越好**，200=电极未接触皮肤。
        # 2026-10-23 修正：原参数为 signal_min 且判 `signal < min 丢弃`，方向与协议相反——
        # 真机上等于「只保留脱戴帧、丢掉所有好帧」。依据见 verify_hw.py 顶部注释。
        self.signal_max = signal_max
        # v2.9 修复（10-23 代码审查 P1-2）：原 process_frame(fr) 不传 ctx → task_active 恒 False，
        # 分心门控（state_engine.py L381 硬性要求任务态）永不过 → 真机路径 distracted 永不可判（8 态缺 1）。
        # 真机=盯盘实验场景默认任务态开启；非任务态场景由调用方显式覆盖。
        self.ctx = {"task_active": True} if ctx is None else ctx
        self._heart: float | None = None  # 最近一次扩展回调的 heart(bpm)
        self._last_gyro = (0.0, 0.0, 0.0)
        self._rr_batch = 0               # v2.6 RR 回调批次号（同批 3 间期连续，组内差分）

    # -- 回调入口 -------------------------------------------------------
    def onEEG(self, data) -> None:
        """EEG 回调：合并 low/high 频段 → Frame → 引擎"""
        sig = int(getattr(data, "signal", 0) or 0)
        if sig >= 200:
            return                       # 电极未接触皮肤：该帧无生理意义，直接丢（硬门，不受参数影响）
        if self.signal_max and sig > self.signal_max:
            return                       # 噪声过强（V3 标定 signal_max），丢弃该帧
        fr = Frame(
            attention=float(getattr(data, "attention", 50.0)),
            meditation=float(getattr(data, "meditation", 50.0)),
            delta=float(getattr(data, "delta", 0.0)),
            theta=float(getattr(data, "theta", 0.0)),
            alpha=float(getattr(data, "lowAlpha", 0.0) + getattr(data, "highAlpha", 0.0)),
            beta=float(getattr(data, "lowBeta", 0.0) + getattr(data, "highBeta", 0.0)),
            gamma=float(getattr(data, "lowGamma", 0.0) + getattr(data, "highGamma", 0.0)),
            rr_ms=None,                   # RR 走 onRR 灌缓冲，不随帧
            gyro=self._last_gyro,
            # v3.6：离线回放必须携带真实时间戳。真机链路里 data 没有 ts 属性 → None
            # → 引擎回退 wall-clock（帧到达即真实时刻，语义正确，行为完全不变）；
            # 但离线回放会把几分钟的帧瞬间灌入，wall-clock 的 dt≈0 → 疲劳积分等
            # 累积判据失真（同一 dump 连跑三次得到不同答案）。
            ts=getattr(data, "ts", None),
        )
        if self._heart is not None:
            fr.heart = self._heart        # heart(bpm) → HR 特征（缺口 3 分工）
        self.engine.process_frame(fr, self.ctx)   # v2.9：传 ctx（task_active）——P1-2 修复

    def onExtendEEG(self, data) -> None:
        """扩展回调：heart(bpm) 直读，作 HR 特征（BrainLinkExtendData.heart）"""
        self._heart = float(getattr(data, "heart", float("nan")))
        if not math.isfinite(self._heart):
            self._heart = None

    def onGyro(self, x, y, z) -> None:
        self._last_gyro = (float(x), float(y), float(z))

    def onRR(self, rr1, rr2, rr3) -> None:
        """RR 回调：一次 3 个连续心跳间期，同批灌入 HRV 缓冲（缺口 2 不丢信息）。
        v2.6：批次号传入——RMSSD 只对同批内间期差分（跨回调间期不连续，混差分污染 RMSSD）"""
        self._rr_batch += 1
        for rr in (rr1, rr2, rr3):
            if rr and RR_MIN_MS <= rr <= RR_MAX_MS:
                self.engine.push_rr(float(rr), batch=self._rr_batch)


# ---------------------------------------------------------------- 离线验证
class _Ext:
    """仿 BrainLinkExtendData"""
    def __init__(self, heart):
        self.heart = heart


def simulate():
    random.seed(11)
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    ad = SdkAdapter(eng)
    print("=== sdk_adapter.py 离线验证（仿 SDK 回调，验证 字段合并 + heart/RR 分工）===")

    # 1 字段合并单测：lowAlpha/highAlpha 分离 -> Frame.alpha 合并
    d = SDKFrame(theta=95.0, lowAlpha=30.0, highAlpha=25.0,
                 lowBeta=45.0, highBeta=45.0, lowGamma=15.0, highGamma=15.0)
    ad.onExtendEEG(_Ext(heart=75.0))     # heart 先到（扩展回调时序独立于 EEG）
    ad.onRR(830, 810, 790)               # RR 三值先入缓冲
    ad.onEEG(d)
    fr = eng.eeg_buf[-1]
    assert fr.alpha == 55.0, f"alpha 合并失败: {fr.alpha}"
    assert fr.beta == 90.0 and fr.gamma == 30.0, "beta/gamma 合并失败"
    assert fr.heart == 75.0, "heart 未透传"
    print(f"[1] 字段合并 OK: lowAlpha30+highAlpha25 -> alpha={fr.alpha} "
          f"(beta={fr.beta}, gamma={fr.gamma})")
    print(f"[1] heart 透传 OK: heart={fr.heart} bpm")

    # 2 RR 三值入缓冲：一次回调灌 3 个间期
    assert len(eng.rr_buf) == 3, f"RR 缓冲应为 3: {len(eng.rr_buf)}"
    print(f"[2] RR 三值入缓冲 OK: rr_buf={eng.rr_buf}")

    # 3 HR 分工：heart 直读优先，RR 只供 RMSSD（RMSSD 需 >=8 间期，补齐到 12 个）
    for _ in range(3):
        ad.onRR(820, 810, 830)
    feats = eng._extract()
    assert abs(feats.HR - 75.0) < 1e-6, f"HR 应直读 heart=75: {feats.HR}"
    assert feats.RMSSD_60 > 0, "RMSSD 应由 RR 间期算得"
    print(f"[3] HR 分工 OK: HR={feats.HR:.1f} bpm (heart 直读) / "
          f"RMSSD_60={feats.RMSSD_60:.2f} ms (RR 计算)")

    # 4 端到端：65 帧专注稳态（θ 高、β 低 + 静体动）应判 focus
    eng2 = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
    ad2 = SdkAdapter(eng2)
    for i in range(65):
        th = 100.0 * (0.55 + 0.1 * (random.random() - 0.5))    # 专注：θ/β 下降（M13 主口径）
        be = 100.0 * (1.0 + 0.1 * (random.random() - 0.5))
        al = 100.0 * (1.3 + 0.1 * (random.random() - 0.5))
        ad2.onExtendEEG(_Ext(heart=72 + random.random() * 2))
        ad2.onRR(820 + random.randint(-20, 20), 810 + random.randint(-20, 20),
                 830 + random.randint(-20, 20))
        ad2.onEEG(SDKFrame(theta=th, lowAlpha=al * 0.5, highAlpha=al * 0.5,
                           lowBeta=be * 0.6, highBeta=be * 0.4,
                           lowGamma=20, highGamma=10))
    dec, fb = eng2.last_decision, eng2.last_feedback
    print(f"[4] 端到端 65 帧专注稳态 -> state={dec.state} conf={dec.conf:.2f} "
          f"fb=[{fb.level}] (期望 focus)")
    assert dec.state == "focus", f"端到端判别失败: {dec.state}"
    print("\n全部验证通过。真机接入时：SdkAdapter(engine) + 官方回调 -> parser.parse()")


if __name__ == "__main__":
    simulate()
