# -*- coding: utf-8 -*-
"""会话运行时：服务端权威时钟、逐 tick 推送、交易、反馈与指标。

安全边界：
  - 受试者快照/WS 只包含“已经发生”的价格与事件；
  - 无反馈轮受试者消息不含任何 state/conf；有反馈轮只通过状态提醒携带状态；
  - seed、事件总表、未来价格只存在于服务端与研究者端。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from typing import Any

import numpy as np

from . import config as C
from .engine import EngineDecision, build_engine
from .simulation import Account, EventScheduler, PriceEngine, make_demo_events
from .storage import SessionStore


def _new_token() -> str:
    entropy = int(np.random.SeedSequence().entropy)
    return hashlib.sha256(f"{entropy}-{time.time_ns()}".encode()).hexdigest()[:32]


def _new_code() -> str:
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    entropy = int(np.random.SeedSequence().entropy)
    rng = np.random.Generator(np.random.PCG64(entropy))
    return "".join(alphabet[int(rng.integers(0, len(alphabet)))] for _ in range(6))


def _new_seed() -> int:
    return int(int(np.random.SeedSequence().entropy) % (2 ** 31 - 1))


class SessionRuntime:
    def __init__(self, meta: dict[str, Any], store: SessionStore,
                 engine_path: str | None = None) -> None:
        self.meta = meta
        self.store = store
        self.id = meta["id"]
        self.demo = bool(meta.get("demo"))
        self.feedback_enabled = meta["round"] == C.ROUND_FEEDBACK
        self.tick_ms = int(meta["tick_ms"])
        self.n_ticks = max(1, int(round(meta["duration"] * 1000 / self.tick_ms)))

        if self.demo:
            events = make_demo_events(meta["duration"], self.tick_ms, self.n_ticks)
        else:
            events = EventScheduler(meta["seed"], meta["duration"],
                                    self.tick_ms, self.n_ticks).events
        self.events_schedule = events
        self.price_engine = PriceEngine(meta["seed"], self.n_ticks,
                                        [(t, e) for t, e, _ in events],
                                        tick_ms=self.tick_ms)
        self.engine, self.planner, self.frames = build_engine(
            meta["seed"], self.n_ticks, events,
            meta["engine_mode"], engine_path, self.tick_ms)

        # 演示自动成交：秒口径换算为 tick
        self._demo_trades = {
            int(sec * 1000 // self.tick_ms): v for sec, v in C.DEMO_TRADES_S.items()
        }

        self.account = Account()
        self.status = "created"
        self.current_tick = -1
        self.current_price: float | None = None
        self.start_epoch: float | None = None
        self.task: asyncio.Task | None = None
        self._stop = False
        # v2.9.2：会话生命周期时间戳（供 manager.sweep 回收内存；数据已落盘，运行时对象可丢）
        self.created_at = time.time()
        self.finished_at: float | None = None

        self.events_occurred: list[dict[str, Any]] = []
        self.decisions: list[dict[str, Any]] = []
        self.state_rows: list[dict[str, Any]] = []
        self.interventions: list[dict[str, Any]] = []
        self._decision_id = 0
        self._event_id = 0
        self._intervention_id = 0

        self._subs: dict[str, dict[Any, asyncio.Lock]] = {"subject": {}, "researcher": {}}

    # ---------------- lifecycle ----------------

    async def start(self, started_by: str = "subject") -> None:
        if self.status == "running":
            return
        if self.status != "created":
            raise ValueError(f"会话状态不可开始：{self.status}")
        self.status = "running"
        self.start_epoch = time.time()
        self.store.update_status(self.id, "running", self.start_epoch)
        self.task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            tick_s = self.tick_ms / 1000.0
            assert self.start_epoch is not None
            for tick in range(self.n_ticks):
                target = self.start_epoch + tick * tick_s
                delay = target - time.time()
                if delay > 0:
                    await asyncio.sleep(delay)
                if self._stop:
                    break
                await self._process_tick(tick)
            if not self._stop:
                await self._finish()
        except asyncio.CancelledError:
            self.status = "interrupted"
            raise
        except Exception as exc:  # 服务端异常：保留证据并通知两端
            self.status = "error"
            await self._push("subject", {"type": "error", "message": str(exc)})
            await self._push("researcher", {"type": "error", "message": str(exc)})
            raise

    async def _process_tick(self, tick: int) -> None:
        now = time.time()
        price = self.price_engine.price_at(tick)
        segment = self.price_engine.segment_at(tick)
        self.current_tick = tick
        self.current_price = price
        self.store.log_price(self.id, [round(now, 3), tick, f"{price:.6f}", segment])

        frame = self.frames.frame_at(tick, now)
        dec, fb = self.engine.process_frame(frame, ctx=C.TASK_CTX)
        state = dec.state
        conf = float(dec.conf)
        impulsive = state in C.IS_IMPULSIVE_STATES
        self.store.log_state(self.id, [round(now, 3), tick, state, f"{conf:.2f}",
                                       "1" if impulsive else "0", C.SIGNAL_QUALITY_PLACEHOLDER])
        row = {"t": round(now, 3), "tick": tick, "state": state, "conf": conf,
               "is_impulsive": impulsive}
        self.state_rows.append(row)

        base = self.price_engine.price_at(0)
        change_pct = (price / base - 1.0) * 100.0 if base else 0.0
        remaining_s = max(0, round(self.meta["duration"] - (tick + 1) * self.tick_ms / 1000.0, 1))
        payload = {
            "type": "tick", "t": round(now, 3), "tick": tick,
            "price": round(price, 4), "change_pct": round(change_pct, 2),
            "remaining": remaining_s,
            "status": self.status,
        }
        await self._push("subject", payload)
        await self._push("researcher", {**payload, "state": state, "conf": round(conf, 2),
                                        "is_impulsive": impulsive,
                                        "state_label": C.STATE_LABELS_CN.get(state, state)})

        for event_tick, etype, text in self.events_schedule:
            if event_tick == tick:
                t_popup = round(time.time(), 3)
                self._event_id += 1
                ev = {"event_id": self._event_id, "tick": tick, "event_type": etype.value,
                      "popup_text": text, "t_popup": t_popup,
                      "t_close": round(t_popup + C.POPUP_AUTO_CLOSE_S, 3)}
                self.events_occurred.append(ev)
                if not self.demo:
                    self.store.log_event(self.id, [
                        ev["event_id"], self.meta["subject_id"], self.meta["round"],
                        ev["t_popup"], ev["tick"], ev["event_type"],
                        ev["popup_text"], ev["t_close"]])
                event_msg = {"type": "event", "tick": tick, "event_type": etype.value,
                             "text": text, "t_popup": t_popup,
                             "auto_close_s": C.POPUP_AUTO_CLOSE_S}
                await self._push("subject", event_msg)
                await self._push("researcher", event_msg)

        if (self.feedback_enabled and fb is not None
                and getattr(fb, "level", "silent") != "silent"
                and getattr(fb, "action", "")):
            self._intervention_id += 1
            item = {
                "intervention_id": self._intervention_id,
                "t": round(time.time(), 3), "tick": tick, "state": state,
                "level": fb.level, "action": fb.action, "channel": "visual+voice",
            }
            self.interventions.append(item)
            if not self.demo:
                self.store.log_intervention(self.id, [
                    item["intervention_id"], self.meta["subject_id"], self.meta["round"],
                    item["t"], item["tick"], item["state"], item["level"],
                    item["action"], item["channel"]])
            await self._push("subject", {
                "type": "feedback", "state": state,
                "state_label": C.STATE_LABELS_CN.get(state, state),
                "level": fb.level, "action": fb.action, "t": item["t"],
            })
            # 研究者端同步收到一条干预记录，保证“系统提醒次数/提醒起效率”实时更新
            await self._push("researcher", {
                "type": "intervention", "tick": tick, "t": item["t"],
                "state": state, "level": fb.level, "action": fb.action,
                "channel": item["channel"],
            })

        if self.demo:
            trade = self._demo_trades.get(tick)
            if trade:
                direction, ratio = trade
                self.execute_trade(direction, ratio, demo=True)

    # ---------------- trading ----------------

    def execute_trade(self, direction: str, ratio: float, demo: bool = False) -> dict[str, Any]:
        if self.status != "running" or self.current_price is None:
            raise ValueError("会话尚未开始或已结束")
        if direction not in ("买入", "卖出"):
            raise ValueError("direction 必须是 买入/卖出")
        ratio = float(ratio)
        if not (0.0 < ratio <= 1.0):
            raise ValueError("仓位比例必须在 (0, 1]")

        price = float(self.current_price)
        tick = int(self.current_tick)
        qty = 0.0
        if direction == "买入":
            spend = self.account.cash * ratio
            if spend <= C._MIN_CASH:
                raise ValueError("可用现金不足")
            qty = spend / price
            new_pos = self.account.position + qty
            self.account.avg_price = (
                (self.account.position * self.account.avg_price + spend) / new_pos
                if new_pos > C._MIN_POS else 0.0)
            self.account.position = new_pos
            self.account.cash -= spend
            if self.account.cash < C._MIN_CASH:
                self.account.cash = 0.0
        else:
            if self.account.position <= C._MIN_POS:
                raise ValueError("当前没有可卖持仓")
            qty = self.account.position * ratio
            self.account.cash += qty * price
            self.account.realized_pnl += qty * (price - self.account.avg_price)
            self.account.position -= qty
            if self.account.position <= C._MIN_POS:
                self.account.position = 0.0
                self.account.avg_price = 0.0

        floating = self.account.floating_pnl(price)
        dec = getattr(self.engine, "last_decision", None) or EngineDecision("neutral", 0.0)
        state = dec.state
        conf = float(dec.conf)
        impulsive = state in C.IS_IMPULSIVE_STATES
        now = time.time()
        trigger = C.DECISION_ATTRIBUTION_FALLBACK
        for ev in reversed(self.events_occurred):
            delta = now - ev["t_popup"]
            if 0.0 <= delta <= C.ATTRIBUTION_WINDOW_S:
                trigger = ev["event_type"]
                break

        self._decision_id += 1
        rec = {
            "decision_id": self._decision_id,
            "t": round(now, 3), "tick": tick, "direction": direction,
            "position_ratio": f"{ratio:.2f}", "qty": qty, "price": price,
            "state": state, "conf": conf, "is_impulsive": impulsive,
            "trigger_event": trigger,
        }
        self.decisions.append(rec)

        if not demo:
            self.store.log_decision(self.id, [
                self._decision_id, self.meta["subject_id"], self.meta["round"],
                self.meta["order"], self.meta["date"], self.meta["seed"],
                f"{now:.3f}", tick, direction, f"{ratio:.2f}",
                f"{qty:.6f}", f"{price:.4f}", f"{self.account.cash:.2f}",
                f"{self.account.position:.6f}", f"{floating:.2f}",
                f"{self.account.realized_pnl:.2f}", trigger, state,
                f"{conf:.2f}", "1" if impulsive else "0",
                C.SIGNAL_QUALITY_PLACEHOLDER])
            try:
                self.engine.rationality_log(impulsive, dec)
            except Exception:
                pass

        result = {
            "ok": True, "tick": tick, "t": rec["t"], "direction": direction,
            "price": round(price, 4),
            "account": {
                "cash": round(self.account.cash, 2),
                "position": round(self.account.position, 4),
                "avg_price": round(self.account.avg_price, 4),
                "floating_pnl": round(floating, 2),
                "realized_pnl": round(self.account.realized_pnl, 2),
            },
            "decision": {"trigger_event": trigger, "position_ratio": f"{ratio:.2f}"},
        }
        asyncio.create_task(self._push("researcher", {**result, "type": "trade",
                                                     "state": state,
                                                     "conf": round(conf, 2),
                                                     "is_impulsive": impulsive}))
        asyncio.create_task(self._push("subject", {
            "type": "account", "account": result["account"],
            "trade": {"tick": tick, "price": round(price, 4),
                      "direction": direction}}))
        return result

    # ---------------- snapshots / metrics ----------------

    def subject_snapshot(self) -> dict[str, Any]:
        elapsed = self.current_tick
        prices = [{"tick": i, "price": round(self.price_engine.price_at(i), 4)}
                  for i in range(0, elapsed + 1)]
        return {
            "type": "snapshot",
            "session": {
                "id": self.id, "subject_id": self.meta["subject_id"],
                "round_index": C.round_index(self.meta["order"], self.meta["round"]),
                "duration": self.meta["duration"], "tick_ms": self.meta["tick_ms"],
                "demo": self.demo, "mode": self.meta["mode"],
                # v2.9.1：移除 n_ticks（前端未消费，且属容量配置；总长可由 duration/tick_ms 推得）。
                # mode 保留：前端 trading.js 用它区分 demo/test/experiment 横幅，属**展示态**，
                # 不是实验条件（条件字段是 round/order/seed，仍在禁发清单内）。
                # 2026-10-23 组长代改（P2-2）：移除 feedback_enabled——
                # 实验条件字段不得下发受试者端（DevTools 可见即破坏盲法）
                "status": self.status,
            },
            "tick": elapsed,
            "price": round(self.current_price, 4) if self.current_price is not None else None,
            "prices": prices,
            "events": [{k: v for k, v in ev.items() if k != "t_close"}
                       for ev in self.events_occurred],
            "account": {
                "cash": round(self.account.cash, 2),
                "position": round(self.account.position, 4),
                "avg_price": round(self.account.avg_price, 4),
                "floating_pnl": round(self.account.floating_pnl(self.current_price or 0.0), 2),
                "realized_pnl": round(self.account.realized_pnl, 2),
            },
        }

    def researcher_snapshot(self) -> dict[str, Any]:
        session = {k: self.meta[k] for k in
                   ("id", "code", "subject_id", "round", "order", "date", "seed",
                    "duration", "tick_ms", "engine_mode", "mode")}
        session["status"] = self.status
        return {
            "type": "snapshot",
            "session": session,
            "tick": self.current_tick,
            "price": round(self.current_price, 4) if self.current_price is not None else None,
            "prices": [{"tick": i, "price": round(self.price_engine.price_at(i), 4)}
                       for i in range(0, self.current_tick + 1)],
            "states": self.state_rows,
            "events": self.events_occurred,
            "decisions": self.decisions,
            "interventions": self.interventions,
            "metrics": self.metrics(),
            "account": {
                "cash": round(self.account.cash, 2),
                "position": round(self.account.position, 4),
                "avg_price": round(self.account.avg_price, 4),
                "floating_pnl": round(self.account.floating_pnl(self.current_price or 0.0), 2),
                "realized_pnl": round(self.account.realized_pnl, 2),
            },
        }

    def metrics(self) -> dict[str, Any]:
        total = len(self.decisions)
        impulsive = sum(1 for d in self.decisions if d["is_impulsive"])
        calmness = round(1.0 - impulsive / total, 4) if total else None

        # v2 口径对齐（任务二 §1.2/§1.3 组长定稿口径）：
        #   专注占比分母=有效盯盘帧（预热后且非 neutral，方案"扣除 neutral"）；
        #   响应=干预后 60s 窗口内出现连续 ≥5s 非冲动且非 neutral 段（防游戏化，瞬时抖动不计）
        valid = [s for s in self.state_rows
                 if s["tick"] * self.tick_ms / 1000.0 >= C.MOCK_WARMUP_S
                 and s["state"] != "neutral"]
        focus = sum(1 for s in valid if s["state"] in ("focus", "flow"))
        focus_ratio = round(focus / len(valid), 4) if valid else None

        responded = 0
        for item in self.interventions:
            hi = item["t"] + C.ATTRIBUTION_WINDOW_S
            seg_start = None
            for s in self.state_rows:      # 已按 tick 升序（时间戳升序）
                t = s["t"]
                in_ok = item["t"] < t <= hi and not s["is_impulsive"] and s["state"] != "neutral"
                if in_ok:
                    if seg_start is None:
                        seg_start = t
                    elif t - seg_start >= 5.0:
                        responded += 1
                        break
                else:
                    seg_start = None
        response_rate = round(responded / len(self.interventions), 4) \
            if self.interventions else None

        dist: dict[str, int] = {}
        for s in self.state_rows:
            dist[s["state"]] = dist.get(s["state"], 0) + 1

        return {
            "decisions": total, "impulsive_decisions": impulsive,
            "calmness": calmness, "focus_ratio": focus_ratio,
            "interventions": len(self.interventions),
            "intervention_response_rate": response_rate,
            "state_distribution": dist,
        }

    # ---------------- finish / push ----------------

    async def _finish(self) -> None:
        self.status = "finished"
        self.finished_at = time.time()
        metrics = self.metrics()
        if not self.demo:
            self.store.finalize(self.id, metrics)
        else:
            self.store.update_status(self.id, "finished")
        await self._push("subject", {"type": "end", "status": "finished"})
        await self._push("researcher", {"type": "end", "status": "finished",
                                        "metrics": metrics})
        self._subs = {"subject": {}, "researcher": {}}

    async def _push(self, role: str, payload: dict[str, Any]) -> None:
        for ws, lock in list(self._subs[role].items()):
            try:
                async with lock:
                    await ws.send_json(payload)
            except Exception:
                self._subs[role].pop(ws, None)

    def subscribe(self, ws, role: str) -> None:
        self._subs[role][ws] = asyncio.Lock()

    def unsubscribe(self, ws, role: str) -> None:
        self._subs[role].pop(ws, None)

    async def stop(self) -> None:
        self._stop = True
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except (asyncio.CancelledError, Exception):
                pass


class SessionManager:
    def __init__(self, store: SessionStore) -> None:
        self.store = store
        self.runtimes: dict[str, SessionRuntime] = {}

    def create(self, *, subject_id: str, round_label: str, order: str,
               duration: int = C.DURATION_EXPERIMENT,
               tick_ms: int = C.TICK_MS_EXPERIMENT,
               seed: int | None = None, engine_mode: str = "mock",
               engine_path: str | None = None, demo: bool = False) -> SessionRuntime:
        self.sweep()          # v2.9.2：建会话时顺手回收过期运行时（无需独立定时器）
        if round_label not in C.ROUNDS:
            raise ValueError("round 必须是 有反馈/无反馈")
        if order not in C.ORDERS:
            raise ValueError("order 必须是 先无反馈/先有反馈")
        if demo:
            duration, tick_ms, seed = C.DURATION_DEMO, C.TICK_MS_DEMO, C.DEMO_SEED
        mode = (C.MODE_DEMO if demo
                else (C.MODE_TEST if int(tick_ms) < C.TICK_MS_EXPERIMENT
                      else C.MODE_EXPERIMENT))
        sid = uuid.uuid4().hex[:12]
        meta = {
            "id": sid,
            "code": _new_code(),
            "subject_id": subject_id.strip() or "未命名",
            "round": round_label,
            "order": order,
            "date": C.date_str(),
            "seed": int(seed) if seed is not None else _new_seed(),
            "duration": int(duration),
            "tick_ms": int(tick_ms),
            "engine_mode": engine_mode,
            "signal_quality_rating": C.SIGNAL_QUALITY_RATING,
            "demo": bool(demo),
            "mode": mode,
            "status": "created",
            "subject_token": _new_token(),
        }
        runtime = SessionRuntime(meta, self.store, engine_path)
        self.store.create_session(meta)
        self.runtimes[sid] = runtime
        return runtime

    def get(self, session_id: str) -> SessionRuntime | None:
        return self.runtimes.get(session_id)

    def sweep(self, finished_ttl: float = C.SESSION_KEEP_FINISHED_S,
              idle_ttl: float = C.SESSION_KEEP_IDLE_S) -> int:
        """回收已结束后长期闲置的运行时对象（v2.9.2）。

        之前 runtimes 只增不减：每次新建会话都留一个 SessionRuntime（含引擎、价格引擎、
        决策/事件/状态列表与 CSV 句柄）。一次实验 2 轮、每轮 10min 时问题不大，
        但连续多被试多天跑批会线性涨内存，最终 OOM——实验平台最不能接受的就是中途崩。
        数据本身已落盘（decisions.csv / sqlite / 导出 zip），运行时对象销毁不影响归档；
        仍在进行或刚结束 TTL 内的会话保留，方便研究者立即回看/导出。
        """
        now = time.time()
        drop: list[str] = []
        for sid, rt in self.runtimes.items():
            if rt.status == "finished":
                ref = rt.finished_at or rt.created_at
                if now - ref > finished_ttl:
                    drop.append(sid)
            elif rt.status in ("created", "ready"):
                # 建了但从未开始（研究者误点/测试残留）：超时清理，避免僵尸会话
                if now - rt.created_at > idle_ttl:
                    drop.append(sid)
        for sid in drop:
            rt = self.runtimes.pop(sid, None)
            if rt and rt.task and not rt.task.done():
                rt.task.cancel()
        return len(drop)

    def get_by_code(self, code: str) -> SessionRuntime | None:
        for rt in self.runtimes.values():
            if rt.meta["code"].upper() == code.strip().upper():
                return rt
        return None

    async def start(self, session_id: str, by: str = "subject") -> None:
        rt = self.runtimes.get(session_id)
        if not rt:
            raise ValueError("会话不存在")
        await rt.start(by)

    async def close(self) -> None:
        for rt in list(self.runtimes.values()):
            await rt.stop()
        self.store.close()
