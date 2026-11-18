# -*- coding: utf-8 -*-
"""平台端到端验收脚本（需先启动服务）。

用法：
    python -m uvicorn backend.main:app --port 8010
    python verify_e2e.py --base http://127.0.0.1:8010

依赖：httpx、websockets（uvicorn[standard] 已含 websockets）。
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from pathlib import Path

import httpx
import websockets

TOKEN = "research-demo-token"
SEED = 42
DURATION = 90      # 秒：验收用短会话（正式实验 600 秒）
TICK_MS = 200      # 毫秒：加速但不改变“按秒”的行情/事件语义
FORBIDDEN = {"seed", "round", "order", "state", "conf", "is_impulsive",
             "feedback_enabled", "n_ticks"}   # 10-23 组长代改：补条件/配置字段（P2-2 双保险）
# v2.9.1：黑名单之外补**白名单**——受试者快照 session 块只允许这些键，新增字段必须显式登记。
# 原因：黑名单只能挡"已想到"的字段，而 mode/n_ticks 的争议正是黑名单思路的副作用（n_ticks 既非
# 条件字段也无人消费，mode 却是前端横幅必需的展示态）。白名单让"新增即需复核"成为默认。
ALLOWED_SESSION_KEYS = {"id", "subject_id", "round_index", "duration",
                        "tick_ms", "demo", "mode", "status"}


def leaks(obj, bad, path="root", hits=None):
    hits = hits if hits is not None else []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in bad:
                hits.append(f"{path}.{k}")
            leaks(v, bad, f"{path}.{k}", hits)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            leaks(v, bad, f"{path}[{i}]", hits)
    return hits


async def run_round(client, base, ws_base, round_label, order):
    resp = await client.post(
        f"{base}/api/research/sessions",
        headers={"X-Research-Token": TOKEN},
        json={"subject_id": "VERIFY-01", "round": round_label, "order": order,
              "seed": SEED, "duration": DURATION, "tick_ms": TICK_MS,
              "engine_mode": "mock"},
    )
    resp.raise_for_status()
    created = resp.json()
    sid, code = created["session_id"], created["code"]

    public = (await client.get(f"{base}/api/sessions/by-code/{code}")).json()
    public_leak = leaks(public, {"seed", "round", "order"})

    joined = (await client.post(f"{base}/api/sessions/join",
                                json={"code": code})).json()
    token = joined["token"]

    messages = []
    reader_error = []
    ws_url = f"{ws_base}/ws/subject/{sid}?token={token}"
    done = asyncio.Event()

    async def reader():
        """持续收集消息；若连接被网络层关闭则自动重连（忽略重复快照）。"""
        seen_snapshot = False
        while not done.is_set():
            try:
                async with websockets.connect(ws_url, ping_interval=None) as ws:
                    async for raw in ws:
                        msg = json.loads(raw)
                        if msg.get("type") == "snapshot":
                            if seen_snapshot:
                                continue
                            seen_snapshot = True
                        messages.append(msg)
            except Exception as exc:
                if done.is_set():
                    break
                reader_error.append(repr(exc))
                await asyncio.sleep(1.0)

    task = asyncio.create_task(reader())
    await asyncio.sleep(0.5)
    await client.post(f"{base}/api/sessions/{sid}/start", params={"token": token})

    await asyncio.sleep(3.0)

    snap = (await client.get(f"{base}/api/sessions/{sid}/snapshot",
                             params={"token": token})).json()
    snap_leak = leaks(snap, FORBIDDEN)
    # 白名单校验：session 块出现未登记字段即视为潜在泄漏
    snap_leak += [f"session.{k}（未登记字段）"
                  for k in sorted(set((snap.get("session") or {})) - ALLOWED_SESSION_KEYS)]
    length_ok = len(snap["prices"]) == snap["tick"] + 1

    trade = await client.post(f"{base}/api/sessions/{sid}/trades",
                              params={"token": token},
                              json={"direction": "买入", "ratio": 0.5})
    trade.raise_for_status()

    await asyncio.sleep(DURATION + 4.0)
    done.set()
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    detail = (await client.get(f"{base}/api/research/sessions/{sid}",
                               headers={"X-Research-Token": TOKEN})).json()
    non_feedback_msgs = [m for m in messages if m.get("type") != "feedback"]
    ws_leak = leaks(non_feedback_msgs, {"seed", "state", "conf", "is_impulsive"})
    counts = {}
    for m in messages:
        counts[m.get("type")] = counts.get(m.get("type"), 0) + 1

    return {
        "round": round_label, "sid": sid, "code": code,
        "public_leak": public_leak, "snapshot_leak": snap_leak,
        "snapshot_len_ok": length_ok,
        "message_counts": counts, "ws_leak": ws_leak, "reader_error": reader_error,
        "metrics": detail.get("metrics") or {},
        "decisions": detail.get("decisions") or [],
        "events_count": len(detail.get("events") or []),
        "interventions_count": len(detail.get("interventions") or []),
        "prices": [(p["tick"], p["price"]) for p in detail.get("prices") or []],
    }


def check(label, cond):
    print(f"[{'PASS' if cond else 'FAIL'}] {label}")
    return cond


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8010")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    ws_base = "ws" + base[len("http"):]

    async with httpx.AsyncClient(timeout=20) as client:
        health = (await client.get(f"{base}/api/health")).json()
        data_dir = Path(health["data_dir"])
        no_fb = await run_round(client, base, ws_base, "无反馈", "先无反馈")
        fb = await run_round(client, base, ws_base, "有反馈", "先有反馈")

    print("消息统计：", no_fb["message_counts"], "|", fb["message_counts"])
    if no_fb["reader_error"] or fb["reader_error"]:
        print("WS 读取异常：", no_fb["reader_error"], fb["reader_error"])

    ok = True
    print("\n--- 无反馈轮 ---")
    ok &= check("受试者公开元数据不含 seed/round/order", not no_fb["public_leak"])
    ok &= check("受试者快照不含 seed/state/conf", not no_fb["snapshot_leak"])
    ok &= check("快照只含已发生价格", no_fb["snapshot_len_ok"])
    ok &= check("WS 消息不含状态字段", not no_fb["ws_leak"])
    ok &= check("无反馈轮 0 条状态提醒",
                no_fb["message_counts"].get("feedback", 0) == 0
                and no_fb["interventions_count"] == 0)
    ok &= check("无反馈轮出现三类干扰中的事件",
                no_fb["message_counts"].get("event", 0) > 0 or no_fb["events_count"] > 0)

    print("\n--- 有反馈轮 ---")
    ok &= check("有反馈轮出现状态提醒",
                fb["message_counts"].get("feedback", 0) > 0
                or fb["interventions_count"] > 0)
    ok &= check("受试者快照不含 seed/state/conf", not fb["snapshot_leak"])
    ok &= check("同种子价格逐点一致", no_fb["prices"] == fb["prices"])
    ok &= check("两轮指标结构一致",
                no_fb["metrics"].get("state_distribution") ==
                fb["metrics"].get("state_distribution"))

    print("\n--- 日志字段 ---")
    csv_path = data_dir / "sessions" / no_fb["sid"] / "decisions.csv"
    if csv_path.is_file():
        rows = list(csv.reader(csv_path.open(encoding="utf-8-sig")))
        header, first = rows[0], rows[1]
        ok &= check("decisions.csv 为 21 列", len(header) == 21)
        ok &= check("引擎状态字段已实填",
                    bool(first[17]) and bool(first[18]) and first[19] in ("0", "1"))
    else:
        ok &= check(f"找到 {csv_path}", False)

    print("\n结果：", "全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
