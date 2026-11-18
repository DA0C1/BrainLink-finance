# -*- coding: utf-8 -*-
"""服务端行情与干扰事件生成（纯计算，无 UI 依赖）。

所有价格序列在本模块预生成，但只允许 SessionRuntime 逐 tick 对外推送；
任何 HTTP/WebSocket 接口都不得整段返回未来数据。
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from . import config as C


class PriceEngine:
    """固定段模板 + 种子噪声 + 事件脉冲；与 v1 价格引擎同口径。"""

    def __init__(self, seed: int, n_ticks: int,
                 events: list[tuple[int, C.EventType | str]] | None = None,
                 s0: float = C.S0, tick_ms: int = 1000) -> None:
        if n_ticks <= 0:
            raise ValueError("n_ticks 必须为正整数")
        self.seed = int(seed)
        self.n_ticks = int(n_ticks)
        self.s0 = float(s0)
        self.tick_ms = int(tick_ms)
        self.dt = self.tick_ms / 1000.0
        self.events: list[tuple[int, C.EventType]] = sorted(
            ((int(t), self._coerce_event(rest[0])) for t, *rest in (events or [])),
            key=lambda x: x[0],
        )
        for t, _ in self.events:
            if not (0 <= t < self.n_ticks):
                raise ValueError(f"事件 tick {t} 超出 [0, {self.n_ticks})")
        self._segments = self._build_segments(self.n_ticks)
        self._log_prices = self._generate(self.seed)
        self._prices = np.exp(self._log_prices)

    @staticmethod
    def _coerce_event(e: C.EventType | str) -> C.EventType:
        if isinstance(e, C.EventType):
            return e
        return C.EventType(str(e))

    @staticmethod
    def _build_segments(n_ticks: int) -> np.ndarray:
        """按固定比例把三段缩放到 n_ticks，保证不同节奏下行情结构一致。"""
        total = sum(d for _, d in C.SEGMENT_TEMPLATE)
        buf: list[str] = []
        prev = 0
        acc = 0
        for name, dur in C.SEGMENT_TEMPLATE:
            acc += dur
            end = int(round(n_ticks * acc / total))
            end = max(end, prev + 1)
            buf.extend([name] * (end - prev))
            prev = end
        return np.array(buf[:n_ticks], dtype="<U8")

    def _generate(self, seed: int) -> np.ndarray:
        rng = np.random.Generator(np.random.PCG64(seed))
        n = self.n_ticks
        x = np.empty(n, dtype=np.float64)
        x[0] = np.log(self.s0)
        z = rng.standard_normal(n)

        # 事件窗口按真实秒数换算成 tick，保证 1 秒 / 2 秒节奏下时间含义一致
        to_ticks = lambda seconds: max(1, int(round(seconds * 1000 / self.tick_ms)))
        chase_pre = to_ticks(C._CHASE_PRE_WINDOW)
        news_shock = to_ticks(C._NEWS_AFTERSHOCK)
        shock_window = to_ticks(C._SHOCK_WINDOW)
        dt = self.dt

        drift_add = np.zeros(n)
        sigma_mult = np.ones(n)
        gap_add = np.zeros(n)

        for tick, etype in self.events:
            if etype is C.EventType.CHASE_INDUCE:
                lo = max(0, tick - chase_pre)
                drift_add[lo:tick] += C._CHASE_DRIFT_BOOST * dt
            elif etype is C.EventType.BREAKING_NEWS:
                gap = rng.uniform(C._NEWS_GAP_RANGE[0], C._NEWS_GAP_RANGE[1])
                gap_add[tick] -= gap
                hi = min(n, tick + news_shock)
                drift_add[tick:hi] += C._NEWS_AFTERSHOCK_DRIFT * dt
            elif etype is C.EventType.VOL_SHOCK:
                hi = min(n, tick + shock_window)
                sigma_mult[tick:hi] *= C._SHOCK_SIGMA_MULT
                k = 0
                for t in range(tick, hi):
                    alt = C._SHOCK_ALT_DRIFT * dt
                    drift_add[t] += alt if (k % 2 == 0) else -alt
                    k += 1

        for t in range(1, n):
            seg = self._segments[t]
            if seg == C.SEGMENT_FLAT:
                mu = np.log(self.s0)
                drift = C._OU_KAPPA * dt * (mu - x[t - 1])
                sigma = C._OU_SIGMA
            elif seg == C.SEGMENT_RALLY:
                drift = C._RALLY_DRIFT * dt
                sigma = C._RALLY_SIGMA
            else:
                drift = C._DECLINE_DRIFT * dt
                sigma = C._DECLINE_SIGMA
                if rng.random() < C._DECLINE_GAP_PROB:
                    drift -= rng.uniform(C._DECLINE_GAP_SIZE[0], C._DECLINE_GAP_SIZE[1])
            drift = drift + drift_add[t]
            sigma = sigma * sigma_mult[t] * np.sqrt(dt)
            x[t] = x[t - 1] + drift + sigma * z[t] + gap_add[t]
        return x

    def price_at(self, tick: int) -> float:
        return float(self._prices[int(tick)])

    def segment_at(self, tick: int) -> str:
        return str(self._segments[int(tick)])

    @property
    def prices(self) -> np.ndarray:
        return self._prices


class EventScheduler:
    """会话开始前一次性确定全部事件（按秒调度）；只在触发 tick 才向客户端推送。"""

    def __init__(self, seed: int, duration_s: int, tick_ms: int, n_ticks: int) -> None:
        rng = np.random.Generator(np.random.PCG64(seed))
        self.events: list[tuple[int, C.EventType, str]] = self._schedule(
            rng, duration_s, tick_ms, n_ticks)

    @staticmethod
    def _schedule(rng: np.random.Generator, duration_s: int,
                  tick_ms: int, n_ticks: int) -> list[tuple[int, C.EventType, str]]:
        events: list[tuple[int, C.EventType, str]] = []
        last_sec: int | None = None
        types = tuple(C.EVENT_PROB_PER_MIN)
        segs = PriceEngine._build_segments(n_ticks)
        # 短会话时按比例收紧留白与最小间隔，保证测试模式也能触发事件
        lead = min(C.NO_EVENT_LEAD_S, max(1, duration_s // 10))
        tail = min(C.NO_EVENT_TAIL_S, max(1, duration_s // 12))
        gap = min(C.MIN_EVENT_GAP_S, max(1, duration_s // 4))
        for minute in range(0, duration_s, 60):
            span = min(60, duration_s - minute)
            for etype in types:
                if rng.random() >= C.EVENT_PROB_PER_MIN[etype]:
                    continue
                sec = minute + int(rng.integers(0, span))
                if sec < lead or sec > duration_s - 1 - tail:
                    continue
                tick = min(n_ticks - 1, int(sec * 1000 // tick_ms))
                if etype is C.EventType.CHASE_INDUCE and segs[tick] != C.SEGMENT_RALLY:
                    continue
                if last_sec is not None and sec - last_sec < gap:
                    continue
                text = C.EVENT_TEXT_VARIANTS[etype][int(rng.integers(0, 4))]
                events.append((tick, etype, text))
                last_sec = sec
        return events


@dataclass
class Account:
    cash: float = C.INITIAL_CASH
    position: float = 0.0
    avg_price: float = 0.0
    realized_pnl: float = 0.0

    def floating_pnl(self, price: float) -> float:
        if self.position <= C._MIN_POS:
            return 0.0
        return self.position * (price - self.avg_price)


def make_demo_events(duration_s: int, tick_ms: int,
                     n_ticks: int) -> list[tuple[int, C.EventType, str]]:
    """演示固定事件（按秒定义）：三类都出现，文案取该类第一条。"""
    out: list[tuple[int, C.EventType, str]] = []
    for sec, etype in C.DEMO_EVENTS_S:
        tick = int(sec * 1000 // tick_ms)
        if 0 <= tick < n_ticks:
            out.append((tick, etype, C.EVENT_TEXT_VARIANTS[etype][0]))
    return out
