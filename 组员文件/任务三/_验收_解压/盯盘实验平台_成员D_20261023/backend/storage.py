# -*- coding: utf-8 -*-
"""会话元数据、逐秒日志（CSV + SQLite）与导出。

每个实验会话一个目录：data/sessions/<session_id>/
  session.json, prices.csv, decisions.csv, events.csv, states.csv, interventions.csv
SQLite 仅作研究端查询索引；演示会话不落实验日志。
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import sqlite3
import threading
import time
import zipfile
from pathlib import Path
from typing import Any

log = logging.getLogger("watchlab.storage")

from . import config as C


class CsvWriter:
    def __init__(self, path: Path, fields: list[str]) -> None:
        self.path = path
        self._fh = open(path, "w", newline="", encoding="utf-8-sig")
        self._w = csv.writer(self._fh)
        self._w.writerow(fields)
        self._fh.flush()

    def row(self, values: list[Any]) -> None:
        self._w.writerow(values)
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


class SessionStore:
    def __init__(self, data_dir: Path = C.DATA_DIR) -> None:
        self.data_dir = Path(data_dir)
        self.sessions_dir = self.data_dir / "sessions"
        self.sessions_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.data_dir / "sessions.db"
        self._lock = threading.Lock()
        self._db = sqlite3.connect(self.db_path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._init_schema()
        self._csv: dict[str, dict[str, CsvWriter]] = {}

    def _init_schema(self) -> None:
        with self._lock:
            self._db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions(
              id TEXT PRIMARY KEY, code TEXT UNIQUE, subject_id TEXT, round TEXT,
              ord TEXT, date TEXT, seed INTEGER, duration INTEGER, tick_ms INTEGER,
              engine_mode TEXT, signal_quality_rating TEXT, demo INTEGER, mode TEXT,
              status TEXT, created_at REAL, started_at REAL, ended_at REAL,
              subject_token TEXT, metrics_json TEXT, meta_json TEXT
            );
            CREATE TABLE IF NOT EXISTS decisions(
              session_id TEXT, decision_id INTEGER, subject_id TEXT, round TEXT,
              ord TEXT, date TEXT, seed INTEGER, t REAL, tick INTEGER,
              direction TEXT, position_ratio TEXT, qty TEXT, price TEXT,
              cash_after TEXT, pos_after TEXT, floating_pnl TEXT,
              realized_pnl TEXT, trigger_event TEXT, engine_state TEXT,
              engine_conf TEXT, is_impulsive TEXT, signal_quality TEXT
            );
            CREATE TABLE IF NOT EXISTS events(
              session_id TEXT, event_id INTEGER, subject_id TEXT, round TEXT,
              t_popup REAL, tick INTEGER, event_type TEXT, popup_text TEXT, t_close REAL
            );
            CREATE TABLE IF NOT EXISTS states(
              session_id TEXT, t REAL, tick INTEGER, state TEXT, conf TEXT,
              is_impulsive TEXT, signal_quality TEXT
            );
            CREATE TABLE IF NOT EXISTS prices(
              session_id TEXT, t REAL, tick INTEGER, price TEXT, segment TEXT
            );
            CREATE TABLE IF NOT EXISTS interventions(
              session_id TEXT, intervention_id INTEGER, subject_id TEXT, round TEXT,
              t REAL, tick INTEGER, state TEXT, level TEXT, action TEXT, channel TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_decisions_session ON decisions(session_id);
            CREATE INDEX IF NOT EXISTS idx_events_session ON events(session_id);
            CREATE INDEX IF NOT EXISTS idx_states_session ON states(session_id);
            CREATE INDEX IF NOT EXISTS idx_interventions_session ON interventions(session_id);
            """)
            try:
                self._db.execute("ALTER TABLE sessions ADD COLUMN mode TEXT")
            except sqlite3.OperationalError:
                pass
            self._db.commit()

    # ---------------- session ----------------

    def create_session(self, meta: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                """INSERT INTO sessions(id, code, subject_id, round, ord, date, seed,
                   duration, tick_ms, engine_mode, signal_quality_rating, demo, mode,
                   status, created_at, subject_token, meta_json)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (meta["id"], meta["code"], meta["subject_id"], meta["round"],
                 meta["order"], meta["date"], meta["seed"], meta["duration"],
                 meta["tick_ms"], meta["engine_mode"], meta["signal_quality_rating"],
                 1 if meta.get("demo") else 0, meta["mode"],
                 meta.get("status", "created"),
                 time.time(), meta["subject_token"], json.dumps(meta, ensure_ascii=False)),
            )
            self._db.commit()
        if not meta.get("demo"):
            d = self.sessions_dir / meta["id"]
            d.mkdir(parents=True, exist_ok=True)
            self._csv[meta["id"]] = {
                "prices": CsvWriter(d / "prices.csv", C.PRICE_FIELDS),
                "decisions": CsvWriter(d / "decisions.csv", C.DECISION_FIELDS),
                "events": CsvWriter(d / "events.csv", C.EVENT_FIELDS),
                "states": CsvWriter(d / "states.csv", C.STATE_FIELDS),
                "interventions": CsvWriter(d / "interventions.csv", C.INTERVENTION_FIELDS),
            }

    def update_status(self, session_id: str, status: str,
                      started_at: float | None = None) -> None:
        with self._lock:
            if started_at is not None:
                self._db.execute("UPDATE sessions SET status=?, started_at=? WHERE id=?",
                                 (status, started_at, session_id))
            else:
                self._db.execute("UPDATE sessions SET status=? WHERE id=?", (status, session_id))
            self._db.commit()

    def finalize(self, session_id: str, metrics: dict[str, Any]) -> None:
        with self._lock:
            self._db.execute(
                "UPDATE sessions SET status='finished', ended_at=?, metrics_json=? WHERE id=?",
                (time.time(), json.dumps(metrics, ensure_ascii=False), session_id),
            )
            self._db.commit()
        logs = self._csv.get(session_id, {})
        for w in logs.values():
            w.close()
        meta = self.get_session(session_id)
        if meta and not meta.get("demo"):
            path = self.sessions_dir / session_id / "session.json"
            payload = dict(meta)
            payload["metrics"] = metrics
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                            encoding="utf-8")

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE id=?",
                                   (session_id,)).fetchone()
        return dict(row) if row else None

    def get_by_code(self, code: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM sessions WHERE code=?",
                                   (code,)).fetchone()
        return dict(row) if row else None

    def list_sessions(self, subject_id: str | None = None) -> list[dict[str, Any]]:
        sql = "SELECT * FROM sessions"
        args: tuple = ()
        if subject_id:
            sql += " WHERE subject_id=?"
            args = (subject_id,)
        sql += " ORDER BY created_at DESC"
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def mark_stale_running(self) -> None:
        """服务重启后，内存中的运行会话已丢失，标记为 interrupted 以免误判。"""
        with self._lock:
            self._db.execute("UPDATE sessions SET status='interrupted' WHERE status='running'")
            self._db.commit()

    # ---------------- rows ----------------

    def log_price(self, session_id: str, values: list[Any]) -> None:
        logs = self._csv.get(session_id)
        if not logs:
            log.warning("log_price: 会话 %s 未注册，日志被丢弃", session_id)  # 10-23 代改（P2-5）
            return
        if logs:
            logs["prices"].row(values)
            with self._lock:
                self._db.execute("INSERT INTO prices VALUES(?,?,?,?,?)",
                                 (session_id, *values))
                self._db.commit()

    def log_state(self, session_id: str, values: list[Any]) -> None:
        logs = self._csv.get(session_id)
        if not logs:
            log.warning("log_state: 会话 %s 未注册，日志被丢弃", session_id)  # 10-23 代改（P2-5）
            return
        if logs:
            logs["states"].row(values)
            with self._lock:
                self._db.execute(
                    "INSERT INTO states VALUES(?,?,?,?,?,?,?)",
                    (session_id, *values))
                self._db.commit()

    def log_event(self, session_id: str, values: list[Any]) -> None:
        logs = self._csv.get(session_id)
        if not logs:
            log.warning("log_event: 会话 %s 未注册，日志被丢弃", session_id)  # 10-23 代改（P2-5）
            return
        if logs:
            logs["events"].row(values)
            with self._lock:
                self._db.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?)",
                                 (session_id, *values))
                self._db.commit()

    def log_decision(self, session_id: str, values: list[Any]) -> None:
        logs = self._csv.get(session_id)
        if not logs:
            log.warning("log_decision: 会话 %s 未注册，日志被丢弃", session_id)  # 10-23 代改（P2-5）
            return
        if logs:
            logs["decisions"].row(values)
            with self._lock:
                self._db.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (session_id, *values))
                self._db.commit()

    def log_intervention(self, session_id: str, values: list[Any]) -> None:
        logs = self._csv.get(session_id)
        if not logs:
            log.warning("log_intervention: 会话 %s 未注册，日志被丢弃", session_id)  # 10-23 代改（P2-5）
            return
        if logs:
            logs["interventions"].row(values)
            with self._lock:
                self._db.execute(
                    "INSERT INTO interventions VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (session_id, *values))
                self._db.commit()

    def rows(self, table: str, session_id: str) -> list[dict[str, Any]]:
        with self._lock:
            cur = self._db.execute(f"SELECT * FROM {table} WHERE session_id=? ORDER BY rowid",
                                   (session_id,))
        return [dict(r) for r in cur.fetchall()]

    # ---------------- export ----------------

    def export_zip(self, session_id: str) -> bytes:
        # v2.9.1 纵深防御：API 层已做白名单（main.export_session），此处再校验一次——
        # session_id 直接参与路径拼接（sessions_dir / session_id），任何新调用方漏校验
        # 都可能逃出数据目录（`../../`）。会话 id 恒为 uuid4().hex[:12]。
        if not re.fullmatch(r"[0-9a-f]{12}", session_id):
            raise ValueError(f"非法会话标识: {session_id!r}")
        logs = self._csv.get(session_id, {})
        for w in logs.values():
            w._fh.flush()
        d = self.sessions_dir / session_id
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for path in sorted(d.glob("*")):
                if path.is_file():
                    z.write(path, arcname=path.name)
        return buf.getvalue()

    def close(self) -> None:
        for logs in self._csv.values():
            for w in logs.values():
                w.close()
        self._db.close()
