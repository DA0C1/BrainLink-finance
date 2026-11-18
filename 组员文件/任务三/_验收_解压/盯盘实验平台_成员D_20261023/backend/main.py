# -*- coding: utf-8 -*-
"""FastAPI 入口：受试者端、研究者端、WebSocket 推送与数据导出。

启动：python -m uvicorn backend.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import json
import hmac
import logging
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import (Depends, FastAPI, Header, HTTPException, Query, Request,
                    WebSocket, WebSocketDisconnect)
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import config as C
from .session import SessionManager
from .storage import SessionStore


@asynccontextmanager
async def lifespan(app: FastAPI):
    # v2.9.1：正式实验前必须注入研究者口令（环境变量 WATCHLAB_RESEARCH_TOKEN）。
    # 未注入时沿用配置默认值（本地演示可用），但显式告警，避免"公开默认口令上线而无感知"。
    if not os.environ.get("WATCHLAB_RESEARCH_TOKEN"):
        logging.getLogger("watchlab").warning(
            "未设置 WATCHLAB_RESEARCH_TOKEN，正在使用仓库内置默认研究者口令——"
            "仅限本地演示；正式实验请先注入环境变量。")
    store = SessionStore()
    store.mark_stale_running()
    app.state.manager = SessionManager(store)
    yield
    await app.state.manager.close()


app = FastAPI(title="BCI 模拟盯盘实验平台", version="2.0", lifespan=lifespan)
app.mount("/assets", StaticFiles(directory=str(C.FRONTEND_DIR / "assets"),
                                 check_dir=False), name="assets")
app.mount("/vendor", StaticFiles(directory=str(C.FRONTEND_DIR / "vendor"),
                                 check_dir=False), name="vendor")


def manager(request: Request) -> SessionManager:
    return request.app.state.manager


def _token_eq(given: str | None, expected: str | None) -> bool:
    """恒定时间口令比较（v2.9.1）：
    原实现 `given != expected` 为短路比较，理论上可被逐字节计时侧信道探测；
    研究平台虽在局域网运行，但口令即"受试者/研究者"唯一凭证，统一改为 hmac.compare_digest。
    注意：compare_digest 对 str 要求两侧均为纯 ASCII，故先编码为 bytes。"""
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode("utf-8"), expected.encode("utf-8"))


def research_auth(x_research_token: str | None = Header(default=None),
                  token: str | None = Query(default=None)) -> None:
    if not _token_eq(x_research_token or token, C.DEFAULT_RESEARCH_TOKEN):
        raise HTTPException(status_code=401, detail="研究者口令无效")


def _subject(rt, token: str | None) -> None:
    if not _token_eq(token, rt.meta["subject_token"]):
        raise HTTPException(status_code=401, detail="受试者会话口令无效")


def _public_meta(rt) -> dict[str, Any]:
    return {
        "code": rt.meta["code"],
        "subject_id": rt.meta["subject_id"],
        "round_index": C.round_index(rt.meta["order"], rt.meta["round"]),
        "duration": rt.meta["duration"],
        "status": rt.status,
        "mode": rt.meta["mode"],
        "demo": rt.demo,
    }


# ---------------------------------------------------------------------------
# 页面
# ---------------------------------------------------------------------------

def _page(name: str) -> FileResponse:
    return FileResponse(C.FRONTEND_DIR / name)


@app.get("/")
def index() -> FileResponse:
    return _page("index.html")


@app.get("/s/{code}")
def entry(code: str) -> FileResponse:
    return _page("entry.html")


@app.get("/trading")
def trading() -> FileResponse:
    return _page("trading.html")


@app.get("/research")
def research() -> FileResponse:
    return _page("researcher.html")


@app.get("/favicon.ico")
def favicon() -> Response:
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# 受试者 API
# ---------------------------------------------------------------------------

class JoinReq(BaseModel):
    code: str


@app.get("/api/sessions/by-code/{code}")
def public_meta(code: str, request: Request) -> dict[str, Any]:
    rt = manager(request).get_by_code(code)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在或服务已重启")
    return _public_meta(rt)


@app.post("/api/sessions/join")
def join(req: JoinReq, request: Request) -> dict[str, Any]:
    rt = manager(request).get_by_code(req.code)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在或服务已重启")
    if rt.status == "finished":
        raise HTTPException(status_code=409, detail="该会话已结束")
    return {"session_id": rt.id, "token": rt.meta["subject_token"],
            "meta": _public_meta(rt)}


@app.get("/api/sessions/{session_id}/snapshot")
def snapshot(session_id: str, token: str | None = Query(default=None),
             request: Request = None) -> dict[str, Any]:
    rt = manager(request).get(session_id)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在")
    _subject(rt, token)
    return rt.subject_snapshot()


@app.post("/api/sessions/{session_id}/start")
async def start_subject(session_id: str, token: str | None = Query(default=None),
                        request: Request = None) -> dict[str, Any]:
    rt = manager(request).get(session_id)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在")
    _subject(rt, token)
    if rt.status == "finished":
        raise HTTPException(status_code=409, detail="该会话已结束")
    if rt.status == "created":
        await rt.start("subject")
    return {"ok": True, "status": rt.status, "start_epoch": rt.start_epoch}


class TradeReq(BaseModel):
    direction: str
    ratio: float


@app.post("/api/sessions/{session_id}/trades")
async def trade(session_id: str, req: TradeReq,
                token: str | None = Query(default=None),
                request: Request = None) -> dict[str, Any]:
    rt = manager(request).get(session_id)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在")
    _subject(rt, token)
    try:
        return rt.execute_trade(req.direction, req.ratio)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/demo")
def create_demo(request: Request) -> dict[str, Any]:
    rt = manager(request).create(subject_id="演示访客",
                                 round_label=C.ROUND_FEEDBACK,
                                 order=C.ORDER_FEEDBACK_FIRST,
                                 demo=True)
    return {"session_id": rt.id, "token": rt.meta["subject_token"],
            "code": rt.meta["code"]}


# ---------------------------------------------------------------------------
# 研究者 API
# ---------------------------------------------------------------------------

class CreateSessionReq(BaseModel):
    subject_id: str = Field(min_length=1, max_length=40)
    round: str
    order: str
    seed: int | None = None
    duration: int = C.DURATION_EXPERIMENT
    tick_ms: int = C.TICK_MS_EXPERIMENT
    engine_mode: str = "mock"
    engine_path: str | None = None


@app.post("/api/research/sessions", dependencies=[Depends(research_auth)])
def create_session(req: CreateSessionReq, request: Request) -> dict[str, Any]:
    if req.engine_mode not in ("mock", "state_engine"):
        raise HTTPException(status_code=400, detail="engine_mode 必须是 mock/state_engine")
    if not (10 <= req.duration <= 1800):
        raise HTTPException(status_code=400, detail="duration 需在 10~1800 秒")
    if not (10 <= req.tick_ms <= 2000):
        raise HTTPException(status_code=400, detail="tick_ms 需在 10~2000 毫秒")
    engine_path = req.engine_path
    if req.engine_mode == "state_engine" and not engine_path:
        # 2026-10-22 组长代改：删除原"自动查找"死代码——
        # (C.PROJECT_ROOT.parents[1] / "任务4_真机工具链" / "任务4_真机工具链")
        # 双重嵌套且层级偏移，candidate 恒不存在，实测必然 400。
        # 真实引擎一律要求显式传入 engine_path（与验收时显式路径用法一致）。
        raise HTTPException(status_code=400, detail="真实引擎模式必须显式提供 engine_path")
    try:
        rt = manager(request).create(
            subject_id=req.subject_id, round_label=req.round, order=req.order,
            duration=req.duration, tick_ms=req.tick_ms, seed=req.seed,
            engine_mode=req.engine_mode, engine_path=engine_path)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"session_id": rt.id, "code": rt.meta["code"], "seed": rt.meta["seed"],
            "mode": rt.meta["mode"], "subject_url": f"/s/{rt.meta['code']}"}


@app.get("/api/research/sessions", dependencies=[Depends(research_auth)])
def list_sessions(request: Request, subject_id: str | None = None) -> list[dict[str, Any]]:
    rows = manager(request).store.list_sessions(subject_id)
    out = []
    for r in rows:
        item = {k: r[k] for k in
                ("id", "code", "subject_id", "round", "ord", "date", "seed",
                 "duration", "tick_ms", "engine_mode", "mode", "status",
                 "created_at", "started_at", "ended_at", "demo")}
        item["order"] = item.pop("ord")
        item["metrics"] = json.loads(r["metrics_json"]) if r.get("metrics_json") else None
        item["subject_url"] = f"/s/{r['code']}"
        out.append(item)
    return out


def _detail_from_store(store: SessionStore, session_id: str) -> dict[str, Any] | None:
    row = store.get_session(session_id)
    if not row:
        return None
    session = {k: row[k] for k in
               ("id", "code", "subject_id", "round", "ord", "date", "seed",
                "duration", "tick_ms", "engine_mode", "mode", "status")}
    session["order"] = session.pop("ord")
    metrics = json.loads(row["metrics_json"]) if row.get("metrics_json") else None
    return {
        "type": "session",
        "session": session,
        "prices": store.rows("prices", session_id),
        "states": store.rows("states", session_id),
        "events": store.rows("events", session_id),
        "decisions": store.rows("decisions", session_id),
        "interventions": store.rows("interventions", session_id),
        "metrics": metrics,
    }


@app.get("/api/research/sessions/{session_id}", dependencies=[Depends(research_auth)])
def session_detail(session_id: str, request: Request) -> dict[str, Any]:
    rt = manager(request).get(session_id)
    if rt:
        return rt.researcher_snapshot()
    detail = _detail_from_store(manager(request).store, session_id)
    if not detail:
        raise HTTPException(status_code=404, detail="会话不存在")
    return detail


@app.post("/api/research/sessions/{session_id}/start",
          dependencies=[Depends(research_auth)])
async def start_research(session_id: str, request: Request) -> dict[str, Any]:
    rt = manager(request).get(session_id)
    if not rt:
        raise HTTPException(status_code=404, detail="会话不存在")
    if rt.status == "finished":
        raise HTTPException(status_code=409, detail="该会话已结束")
    if rt.status == "created":
        await rt.start("researcher")
    return {"ok": True, "status": rt.status}


@app.get("/api/research/sessions/{session_id}/export",
         dependencies=[Depends(research_auth)])
def export_session(session_id: str, request: Request) -> Response:
    store = manager(request).store
    # 2026-10-23 组长代改（P2-3）：session_id 白名单校验，阻断路径穿越——
    # 原 `store.sessions_dir / session_id` 直接拼接 URL 参数，`../../x` 可逃出数据目录打包任意目录
    if not re.fullmatch(r"[0-9a-f]{12}", session_id):
        raise HTTPException(status_code=400, detail="非法会话标识")
    d = store.sessions_dir / session_id
    if not d.is_dir():
        raise HTTPException(status_code=404, detail="该会话没有可导出的实验日志")
    data = store.export_zip(session_id)
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition":
                             f'attachment; filename="watchlab_{session_id}.zip"'})


@app.get("/api/health")
def health(request: Request) -> dict[str, Any]:
    mgr = manager(request)
    # data_dir 保留：verify_e2e.py 依赖它定位 decisions.csv 做 21 列校验（验收契约）。
    # 属受控局域网工具，可接受；如需收紧，应在 e2e 改为从 /api/research/* 取路径。
    return {"ok": True, "sessions": len(mgr.runtimes), "data_dir": str(mgr.store.data_dir)}


# ---------------------------------------------------------------------------
# WebSocket
# ---------------------------------------------------------------------------

@app.websocket("/ws/subject/{session_id}")
async def ws_subject(websocket: WebSocket, session_id: str,
                     token: str | None = Query(default=None)) -> None:
    await websocket.accept()
    rt = websocket.app.state.manager.get(session_id)
    if not rt or not _token_eq(token, rt.meta["subject_token"]):
        await websocket.close(code=4401)
        return
    rt.subscribe(websocket, "subject")
    try:
        await websocket.send_json(rt.subject_snapshot())
        if rt.status == "finished":
            await websocket.send_json({"type": "end", "status": "finished"})
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        rt.unsubscribe(websocket, "subject")


@app.websocket("/ws/research/{session_id}")
async def ws_research(websocket: WebSocket, session_id: str,
                      token: str | None = Query(default=None)) -> None:
    await websocket.accept()
    if not _token_eq(token, C.DEFAULT_RESEARCH_TOKEN):
        await websocket.close(code=4401)
        return
    rt = websocket.app.state.manager.get(session_id)
    if not rt:
        await websocket.close(code=4404)
        return
    rt.subscribe(websocket, "researcher")
    try:
        await websocket.send_json(rt.researcher_snapshot())
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    except Exception:
        pass
    finally:
        rt.unsubscribe(websocket, "researcher")
