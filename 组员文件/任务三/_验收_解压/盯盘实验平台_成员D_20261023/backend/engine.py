# -*- coding: utf-8 -*-
"""EEG 状态引擎适配：默认 MockEngine，可切换真实 state_engine。

接线口径（任务二 §3.4）：
  engine.process_frame(frame, ctx={"task_active": True}) -> (Decision, Feedback)
  engine.last_decision -> (state, conf)
  engine.rationality_log(is_impulsive, decision)
不修改 state_engine.py 内部代码。
"""

from __future__ import annotations

import glob
import importlib.util
import os
import sys
from dataclasses import dataclass

import numpy as np

from . import config as C
from .simulation import PriceEngine


@dataclass
class EngineFrame:
    tick: int
    ts: float
    attention: float = 50.0
    meditation: float = 50.0
    delta: float = 100.0
    theta: float = 100.0
    alpha: float = 130.0
    beta: float = 100.0
    gamma: float = 50.0
    rr_ms: float | None = None
    heart: float | None = None
    gyro: tuple[float, float, float] = (0.0, 0.0, 0.0)


@dataclass
class EngineDecision:
    state: str
    conf: float


@dataclass
class EngineFeedback:
    level: str = "silent"          # silent / mild / calm / strong
    action: str = ""
    cooldown_left: float = 0.0


_FALLBACK_DECISION = EngineDecision(state="neutral", conf=0.0)


_MOCK_STATE_PROFILE: dict[str, tuple[float, float, float, float, float, float, float, float]] = {
    "focus":      (0.55, 1.30, 1.00, 65.0, 55.0, 72.0, 30.0, 0.02),
    "flow":       (0.70, 1.40, 1.00, 72.0, 68.0, 66.0, 42.0, 0.02),
    "relaxed":    (1.00, 1.60, 1.00, 50.0, 70.0, 62.0, 50.0, 0.02),
    "distracted": (1.60, 1.00, 1.00, 45.0, 45.0, 78.0, 30.0, 0.60),
    "stress":     (0.90, 1.10, 1.00, 40.0, 30.0, 88.0, 10.0, 0.05),
    "anxiety":    (1.30, 1.00, 1.05, 28.0, 26.0, 84.0, 30.0, 0.10),
    "fatigue":    (1.35, 1.00, 1.10, 48.0, 45.0, 76.0, 22.0, 0.05),
    "drowsy":     (1.50, 0.85, 1.40, 38.0, 50.0, 68.0, 25.0, 0.03),
    "neutral":    (1.00, 1.10, 1.00, 55.0, 55.0, 72.0, 25.0, 0.03),
}


class SyntheticStatePlanner:
    """确定性状态剧本（无真机占位；仅演示/自检，不代表真实判别）。"""

    def __init__(self, seed: int, n_ticks: int,
                 events: list[tuple[int, C.EventType, str]],
                 tick_ms: int = 1000) -> None:
        rng = np.random.Generator(np.random.PCG64(seed))
        segs = PriceEngine._build_segments(n_ticks)
        to_ticks = lambda seconds: max(1, int(round(seconds * 1000 / tick_ms)))
        self._warmup = to_ticks(C.MOCK_WARMUP_S)
        self._block = to_ticks(C.MOCK_TICK_BLOCK_S)
        self._fatigue = min(n_ticks, to_ticks(C.MOCK_FATIGUE_ONSET_S))
        self._drowsy = min(n_ticks, to_ticks(C.MOCK_DROWSY_ONSET_S))
        event_state = to_ticks(C.MOCK_EVENT_STATE_S)
        states = [self._base_state(tick, str(segs[tick])) for tick in range(n_ticks)]
        for tick, etype, _ in events:
            if etype is C.EventType.BREAKING_NEWS:
                lo, hi, state = tick, min(n_ticks, tick + event_state), "anxiety"
            elif etype is C.EventType.VOL_SHOCK:
                lo, hi, state = tick, min(n_ticks, tick + event_state), "distracted"
            else:
                lo = max(0, tick - to_ticks(10))
                hi, state = min(n_ticks, tick + event_state), "anxiety"
            for k in range(lo, hi):
                if k >= self._warmup:
                    states[k] = state
        confs = [round(0.60 if s == "neutral" else 0.62 + 0.30 * float(rng.random()), 2)
                 for s in states]
        self.states = states
        self.confs = confs

    def _base_state(self, tick: int, segment: str) -> str:
        if tick < self._warmup:
            return "neutral"
        block = (tick // self._block) % 3
        if segment == C.SEGMENT_FLAT:
            return ("focus", "relaxed", "focus")[block]
        if segment == C.SEGMENT_RALLY:
            return ("focus", "flow", "flow")[block]
        if tick < self._fatigue:
            return ("distracted", "focus", "fatigue")[block]
        if tick < self._drowsy:
            return ("fatigue", "distracted", "fatigue")[block]
        return ("drowsy", "fatigue", "drowsy")[block]


class SyntheticFrameSource:
    """确定性合成帧源；真机接入时替换为设备 SDK 回调帧。"""

    def __init__(self, planner: SyntheticStatePlanner, seed: int) -> None:
        self._planner = planner
        rng = np.random.Generator(np.random.PCG64(seed + 20261023))
        n = len(planner.states)
        self._noise = rng.standard_normal((n, 7))
        self._jitter = rng.random(n) - 0.5

    def frame_at(self, tick: int, ts: float) -> EngineFrame:
        state = self._planner.states[int(tick)]
        tb, a_rel, d_rel, att, med, hr, rr_jitter, gyro = _MOCK_STATE_PROFILE[state]
        z = self._noise[int(tick)]
        rr = 60000.0 / hr + rr_jitter * float(self._jitter[int(tick)])
        g = gyro * (0.5 + abs(float(z[4])))
        return EngineFrame(
            tick=int(tick), ts=float(ts),
            attention=float(np.clip(att + 3.0 * z[4], 0.0, 100.0)),
            meditation=float(np.clip(med + 3.0 * z[5], 0.0, 100.0)),
            delta=100.0 * (d_rel + 0.10 * z[3]),
            theta=100.0 * (tb + 0.10 * z[0]),
            alpha=100.0 * (a_rel + 0.10 * z[2]),
            beta=100.0 * (1.0 + 0.10 * z[1]),
            gamma=50.0,
            rr_ms=max(300.0, min(2000.0, rr)),
            heart=float(hr + 2.0 * z[6]),
            gyro=(g, g * 0.8, g * 0.6),
        )


class MockEngine:
    """内置模拟引擎：与 StateEngine 同形接口子集，无 EEG 依赖可独立运行。"""

    def __init__(self, planner: SyntheticStatePlanner) -> None:
        self._planner = planner
        self.last_decision: EngineDecision | None = None
        self.last_feedback = EngineFeedback()
        self._last_feedback_t: dict[str, float] = {}
        self.log = {"decisions": [], "impulsive": 0, "total": 0}

    def process_frame(self, frame: EngineFrame,
                      ctx: dict | None = None) -> tuple[EngineDecision, EngineFeedback]:
        if not (ctx or {}).get("task_active"):
            raise ValueError("任务态未注入：ctx.task_active 必须为 True（任务二 §3.4）")
        tick = int(frame.tick)
        dec = EngineDecision(state=self._planner.states[tick],
                             conf=self._planner.confs[tick])
        fb = self._feedback(float(frame.ts), dec.state)
        self.last_decision, self.last_feedback = dec, fb
        return dec, fb

    def rationality_log(self, is_impulsive: bool, decision) -> None:
        self.log["total"] += 1
        if is_impulsive:
            self.log["impulsive"] += 1
        self.log["decisions"].append(decision.state)

    @property
    def calmness(self) -> float:
        total = self.log["total"]
        return 1.0 - self.log["impulsive"] / total if total else 1.0

    def _feedback(self, ts: float, state: str) -> EngineFeedback:
        table = {
            "distracted": ("mild", "回到任务，注意关键信息"),
            "anxiety": ("calm", "情绪扰动高：暂缓交易决策，先做 3 次深呼吸"),
            "stress": ("calm", "3 次深呼吸"),
            "fatigue": ("mild", "疲劳累积：建议短暂休息（含眼部放松）后继续决策"),
            "drowsy": ("calm", "嗜睡迹象：建议起身活动或暂停交易决策（清醒后再回）"),
        }
        level, action = table.get(state, ("silent", ""))
        if level == "silent":
            return EngineFeedback()
        last = self._last_feedback_t.get(state)
        if last is not None and (ts - last) < C.FEEDBACK_COOLDOWN_S:
            return EngineFeedback(level="silent", action="冷却中",
                                  cooldown_left=C.FEEDBACK_COOLDOWN_S - (ts - last))
        self._last_feedback_t[state] = ts
        return EngineFeedback(level=level, action=action)


class StateEngineAdapter:
    """真实 StateEngine 适配器：只做帧转换与接口转发。"""

    def __init__(self, module) -> None:
        self._Frame = module.Frame
        self._engine = module.StateEngine(
            baseline=module.StateEngine.calibrate_from_mock([]))
        self.last_decision = None
        self.last_feedback = None

    def process_frame(self, frame: EngineFrame, ctx: dict | None = None):
        fr = self._Frame(
            attention=frame.attention, meditation=frame.meditation,
            delta=frame.delta, theta=frame.theta, alpha=frame.alpha,
            beta=frame.beta, gamma=frame.gamma,
            rr_ms=frame.rr_ms, heart=frame.heart, gyro=frame.gyro, ts=frame.ts,
        )
        dec, fb = self._engine.process_frame(fr, ctx=ctx)
        self.last_decision, self.last_feedback = dec, fb
        return dec, fb

    def rationality_log(self, is_impulsive: bool, decision) -> None:
        self._engine.rationality_log(is_impulsive, decision)

    @property
    def calmness(self) -> float:
        return self._engine.calmness


def load_state_engine(engine_path: str):
    path = engine_path
    if os.path.isdir(path):
        candidates = [os.path.join(path, "state_engine.py")]
        candidates += sorted(glob.glob(os.path.join(path, "*state_engine.py")))
        found = next((c for c in candidates if os.path.isfile(c)), None)
        if found is None:
            raise FileNotFoundError(f"目录下找不到 state_engine.py：{path}")
        path = found
    if not os.path.isfile(path):
        raise FileNotFoundError(f"找不到 state_engine.py：{path}")
    spec = importlib.util.spec_from_file_location("state_engine_external", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["state_engine_external"] = module
    spec.loader.exec_module(module)
    return module


def build_engine(seed: int, n_ticks: int,
                 events: list[tuple[int, C.EventType, str]],
                 engine_mode: str, engine_path: str | None,
                 tick_ms: int = 1000):
    planner = SyntheticStatePlanner(seed, n_ticks, events, tick_ms)
    frames = SyntheticFrameSource(planner, seed)
    if engine_mode == "state_engine":
        if not engine_path:
            raise ValueError("engine_mode=state_engine 需要 engine_path")
        engine = StateEngineAdapter(load_state_engine(engine_path))
    else:
        engine = MockEngine(planner)
    return engine, planner, frames
