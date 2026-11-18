# -*- coding: utf-8 -*-
"""live_bridge.py — 真机 / 回放 → 浏览器界面 的数据桥（v2.9.2，组长侧）

解决的问题
----------
任务1 的验收标准是"mock 数据驱动…**真机换数据源即用**"，但交付的界面是纯浏览器页面：
没有串口能力、也不该有。此前 JS 引擎既没有 calibrateFromFrames 移植、UI 也没有校准入口，
真机数据根本进不了界面 —— 阶段 B 无法开始。本文件补上这一环：

    串口 / 录制 CSV / mock  ──►  live_bridge.py  ──SSE──►  浏览器界面（js/bridge.js）
                                    │
                                    └── 帧格式与 state_engine.Frame 同构（离线分析见 analyze_real.py）

为什么用 SSE 而不是 WebSocket
----------------------------
只需要 server→browser 单向推送；SSE 是浏览器原生 EventSource，服务端用标准库即可实现，
**不需要给项目加任何第三方依赖**（真机现场装包往往是最大的坑）。控制指令仍由界面本地处理。

用法
----
    # 1) 合成数据（无真机时验证链路 / 演示）
    python live_bridge.py --source mock

    # 2) 回放录制好的 CSV（阶段 B 离线调参闭环）
    python live_bridge.py --source replay --csv data/session_01.csv --speed 2.0

    # 3) 真机串口（需 pip install pyserial BrainLinkParser）
    #    注意：--port 是 **HTTP 端口**（默认 8770），串口名走 --serial-port。
    #    旧示例写成 `--port COM5`，照抄会 argparse 报 invalid int value。
    python live_bridge.py --source serial --serial-port COM5 --baud 115200 --signal-max 60

启动后浏览器打开 http://127.0.0.1:8770/ ，在界面顶部选「真机/回放」，再打开「校准」面板即可。

CSV 列（缺列按默认值处理，多余列忽略）
------------------------------------
    ts,theta,alpha,beta,delta,gamma,attention,meditation,rr_ms,hr,gyro_x,gyro_y,gyro_z
兼容适配层/录制器的导出（low/high 分离时会在启动时自动合并）。
"""
from __future__ import annotations

import argparse
import csv
import hmac
import json
import math
import os
import signal
import sys
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_UI = os.path.join(HERE, "..", "组员文件", "任务一")

from frameio import row_to_frame as _row_to_frame_impl

# v3.3：白名单补 hr / signal——原实现只放行 8 项，`on_ext()` 拿到的 hr 与 `on_rr()` 攒的
# rr_batch 全被 _clean() 过滤在服务端，**从没到过浏览器**。表现为面板 HRV 口径与权威链不一致。
# v3.10：Host 白名单（防 DNS rebinding）。默认只认回环名；
# 用 --host 0.0.0.0 暴露到局域网时，用 --allow-host 追加机器名/IP。
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}

# v3.10：SSE 客户端上限。每连接占一个线程且长期存活，
# 无上限时（尤其配合 rebinding）可以把面板拖到无法响应。
MAX_SSE_CLIENTS = 16
BACKFILL_MAX = 600          # 新连接显式 ?backfill=N 的上限（与 Broker keep 同量级）

FRAME_KEYS = ("theta", "alpha", "beta", "delta", "gamma",
              "attention", "meditation", "rr_ms", "hr", "signal")


# --------------------------------------------------------------------------- 帧源
class Broker:
    """帧广播：源线程写入，每个 SSE 客户端各自维护读游标（不共享队列，避免抢帧）。"""

    def __init__(self, keep: int = 600):
        self._buf: deque[dict] = deque(maxlen=keep)
        self._cond = threading.Condition()
        self._seq = 0
        self.source = ""
        self.started = time.time()
        self.clients = 0
        self.dropped = 0
        # v3.16：帧源健康状态。源线程可能在启动/运行中死掉（COM 选错、CSV 被删、
        # 解析器异常），此前 HTTP 仍返回 ok:true、界面仍显示"已连接"——这是最贵的
        # 静默失效。alive/error 由 _run_source 维护，/health 与前端消费。
        self.alive = True
        self.error: str | None = None
        self.last_frame_ts = 0.0

    def publish(self, frame: dict) -> None:
        with self._cond:
            self._seq += 1
            frame = dict(frame)
            frame["seq"] = self._seq
            # v3.16：ts=0 是合法相对时间起点（回放首帧），只有 None/非法才补墙钟。
            try:
                frame["ts"] = float(frame.get("ts"))
            except (TypeError, ValueError):
                frame["ts"] = time.time()
            self._buf.append(frame)
            self.last_frame_ts = time.time()
            self._cond.notify_all()

    def read_from(self, seq: int, timeout: float = 1.0) -> list[dict]:
        """取 seq 之后的新帧；无新帧则最多等 timeout 秒（用于 SSE 心跳）。"""
        deadline = time.time() + timeout
        with self._cond:
            while not self._buf or self._buf[-1]["seq"] <= seq:
                left = deadline - time.time()
                if left <= 0:
                    return []
                self._cond.wait(left)
            return [f for f in self._buf if f["seq"] > seq]

    @property
    def latest_seq(self) -> int:
        with self._cond:
            return self._seq


def _clean(frame: dict) -> dict:
    out = {}
    for k in FRAME_KEYS:
        v = frame.get(k)
        if v is None:
            out[k] = None
        else:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                out[k] = None
                continue
            out[k] = fv if math.isfinite(fv) else None
    gyro = frame.get("gyro") or [0.0, 0.0, 0.0]
    try:
        out["gyro"] = [float(x) for x in list(gyro)[:3]]
    except (TypeError, ValueError):
        out["gyro"] = [0.0, 0.0, 0.0]
    # v3.3：rr_batch 是"一次 onRR 的 3 个连续间期"，不能走上面的 float() 通道（会炸），
    # 单独按列表透传。丢掉它 → 浏览器每帧只剩 rr_ms 单值 → 引擎只能跨帧差分，
    # 实测 RMSSD 由 24.14ms 虚高到 38.38ms（+59%），z(RMSSD) 偏高 → 面板 stress 比真机更难判出。
    rb = frame.get("rr_batch")
    if isinstance(rb, (list, tuple)) and rb:
        vals = []
        for x in rb[:3]:
            try:
                fx = float(x)
            except (TypeError, ValueError):
                continue
            if math.isfinite(fx):
                vals.append(fx)
        out["rr_batch"] = vals or None
    else:
        out["rr_batch"] = None
    if frame.get("ts") is not None:
        out["ts"] = frame["ts"]
    if frame.get("seq") is not None:
        out["seq"] = frame["seq"]          # 诊断用（界面可看推送序号，断流一眼可见）
    out["source"] = frame.get("source", "")
    return out


class _QuietServer(ThreadingHTTPServer):
    """关闭浏览器/切页会主动断开 SSE 长连接。socketserver 默认把这种正常断连当异常，
    打印整段 Traceback（`ConnectionAbortedError: [WinError 10053]`），现场看像服务崩了——
    实际一切正常。这里只对"客户端断开"静默，真异常仍照常打印，不掩盖问题。"""

    _CLIENT_GONE = (ConnectionAbortedError, ConnectionResetError,
                    BrokenPipeError, ConnectionError)

    def handle_error(self, request, client_address) -> None:
        if isinstance(sys.exc_info()[1], self._CLIENT_GONE):
            return
        super().handle_error(request, client_address)


def _run_source(target, broker: Broker) -> None:
    """帧源线程的统一外壳：任何退出/异常都要能被 /health 看见。

    背景：`threading` 会**静默吞掉**线程里的 SystemExit。串口名写错时
    `source_serial` 抛 SystemExit，HTTP 服务继续跑、`/health` 仍 ok:true、界面显示
    "已连接"——现场看到的是"面板正常但永远没数据"，这是本项目最贵的失败模式。
    """
    try:
        target()
    except BaseException as exc:                  # 含 SystemExit / KeyboardInterrupt
        broker.error = f"{type(exc).__name__}: {exc}"
        print(f"[bridge] [致命] 帧源已停止：{broker.error}", file=sys.stderr, flush=True)
    finally:
        broker.alive = False


def source_mock(broker: Broker, interval: float) -> None:
    """合成数据：按 8 态轮转，仅用于验证链路（不验证判别准确性，与 output_demo 口径一致）"""
    # 相位清单必须与帧生成器同源：原实现此处另写一份 8 态列表，而 _mock_frame 只支持 6 态，
    # 转到 fatigue 即 KeyError 崩掉本线程；HTTP 仍 200 → 面板"还在但数据不动"，无任何报错。
    from state_engine import _mock_frame, MOCK_PHASES   # 单一事实源

    phases = list(MOCK_PHASES)
    i = 0
    while True:
        phase = phases[(i // 20) % len(phases)]
        try:
            # 每相位 20 帧，t 走 0→1；flow 靠这个趋势才能满足派生同步率（静态帧会被误杀成 focus）
            fr = _mock_frame(phase, t=((i % 20) / 20.0) if phase == "flow" else None)
        except Exception as exc:                          # 兜底：宁可报警也不能静默死线程
            broker.error = f"{type(exc).__name__}: {exc}"
            print(f"[bridge] [错误] mock 相位 {phase!r} 生成失败：{type(exc).__name__}: {exc}\n"
                  f"         帧源已停止，面板将不再刷新。"
                  f"请修 state_engine._MOCK_PHASE_SPEC。", flush=True)
            return
        broker.publish({
            "theta": fr.theta, "alpha": fr.alpha, "beta": fr.beta,
            "delta": fr.delta, "gamma": fr.gamma,
            "attention": fr.attention, "meditation": fr.meditation,
            "rr_ms": fr.rr_ms, "gyro": list(fr.gyro or (0, 0, 0)),
            # v3.9：补 signal —— _mock_frame 一直给 signal=30，但这里没取，
            # 于是 mock/live 演示路径的接触质量恒为 null（真机 serial 源有，合成源没有）。
            "hr": None, "signal": getattr(fr, "signal", None),
            "ts": time.time(), "source": f"mock:{phase}",
        })
        i += 1
        time.sleep(interval)


def source_replay(broker: Broker, path: str, speed: float, loop: bool) -> None:
    """CSV 回放：按 ts 差分控制节奏（speed=2 即两倍速）；无 ts 列则用 1s 间隔。"""
    while True:
        with open(path, "r", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.DictReader(fh))
        if not rows:
            raise SystemExit(f"CSV 为空: {path}")
        # v3.15：按 ts 排序（理由同 serial_bridge.run_replay：乱序会让窗口/确认/积分
        # 拿到负的时间差）。缺 ts 的行按 0 处理并保持相对顺序（sort 是稳定的）。
        def _ts_key(r):
            try:
                return float(r.get("ts") or 0.0)
            except (TypeError, ValueError):
                return 0.0
        rows.sort(key=_ts_key)
        prev_ts = None
        for row in rows:
            frame = _row_to_frame(row)
            ts = frame.get("ts")
            if ts is not None and prev_ts is not None:
                gap = max(0.0, (ts - prev_ts) / max(speed, 1e-6))
                time.sleep(min(gap, 10.0))       # 断帧不拖死回放
            else:
                time.sleep(max(0.02, 1.0 / max(speed, 1e-6)))
            prev_ts = ts if ts is not None else prev_ts
            broker.publish(frame)
        if not loop:
            broker.publish({"theta": None, "source": "replay:eof"})
            return


def _row_to_frame(row: dict) -> dict:
    """兼容入口：统一走 frameio.row_to_frame（v3.16）。

    旧实现自己手写了一份"低/高α合并 + rr1–3 批次 + 缺列默认值"，与
    serial_bridge.run_replay 各自维护，已经漂移过（signal 0 vs None、
    rr_ms 批首 vs 批尾）。现在两边都只调 frameio。
    """
    return _row_to_frame_impl(row)


def source_serial(broker: Broker, port: str, baud: int, signal_max: int) -> None:
    """真机串口：BrainLink 原始消息 → 官方 parser → 回调合并为帧。

    依赖：pyserial + BrainLinkParser（官方 SDK）。缺任一个给出可执行的安装提示，
    不静默失败——真机现场最怕"启动了但没数据"。
    """
    try:
        import serial                                    # type: ignore
    except ImportError:
        raise SystemExit("缺少 pyserial：请先 pip install pyserial（真机串口需要）")
    try:
        from BrainLinkParser import BrainLinkParser      # type: ignore
        parser_src = "官方 BrainLinkParser.pyd（含 RR / gyro / 心率扩展）"
    except ImportError:
        # 同 serial_bridge：3.13 下官方 .pyd 装不上时走纯 Python 兜底（无 RR/心率/gyro）
        from tgam_parser import BrainLinkParser
        parser_src = "纯 Python tgam_parser（仅 EEG；无 RR/心率 → HRV 判据降级）"
    print(f"[bridge] 解析器：{parser_src}", flush=True)

    latest: dict = {"theta": 0.0, "alpha": 0.0, "beta": 0.0, "delta": 0.0, "gamma": 0.0,
                    "attention": 50.0, "meditation": 50.0, "gyro": [0.0, 0.0, 0.0],
                    "rr_ms": None, "rr_batch": None, "hr": None, "signal": 0}
    lock = threading.Lock()

    def on_eeg(d) -> None:
        with lock:
            latest.update({
                "theta": float(getattr(d, "theta", 0.0)),
                "alpha": float(getattr(d, "lowAlpha", 0.0) + getattr(d, "highAlpha", 0.0)),
                "beta": float(getattr(d, "lowBeta", 0.0) + getattr(d, "highBeta", 0.0)),
                "gamma": float(getattr(d, "lowGamma", 0.0) + getattr(d, "highGamma", 0.0)),
                "delta": float(getattr(d, "delta", 0.0)),
                "attention": float(getattr(d, "attention", 50.0)),
                "meditation": float(getattr(d, "meditation", 50.0)),
                "signal": int(getattr(d, "signal", 0) or 0),
            })
            if latest["signal"] >= 200:
                # v3.15：以前这里直接 return，接触帧被丢在服务端 → 界面永远看不到
                # 「未接触」，只能等 6s 停更后误报「蓝牙卡顿/设备休眠」；校准报告的
                # 「接触无效占比」也因此恒为 0（统计的对象根本没送到）。
                # 现在原样下发，前端 contactBad() 会在入窗之前拦掉，不污染判别。
                contact = True
            else:
                contact = False
                if signal_max and latest["signal"] > signal_max:
                    return                                # 噪声过强（V3 标定 signal_max）
            frame = {k: latest[k] for k in ("theta", "alpha", "beta", "delta", "gamma",
                                            "attention", "meditation", "rr_ms", "rr_batch",
                                            "hr", "signal", "gyro")}   # v3.3：hr 原写了没取，白丢；signal 仅作门控未下发
            frame["ts"] = time.time()
            frame["source"] = "serial:contact" if contact else f"serial:signal={latest['signal']}"
        broker.publish(frame)

    def on_ext(d) -> None:
        with lock:
            heart = getattr(d, "heart", None)
            if heart:
                latest["hr"] = float(heart)

    def on_gyro(x, y, z) -> None:
        with lock:
            latest["gyro"] = [float(x or 0), float(y or 0), float(z or 0)]

    def on_rr(r1, r2, r3) -> None:
        with lock:
            # 整批保留：RMSSD 只在"同批内差分"，只留最后一个值会让 HRV 恒为 nan
            # （这是权威链 10-23 已修的同一个坑，实时面板这边同步口径）
            rr_ok = [float(r) for r in (r1, r2, r3) if r and 300 <= float(r) <= 2000]
            if rr_ok:
                latest["rr_ms"] = rr_ok[-1]              # 兼容旧 UI：瞬时值取本批最后一个
                latest["rr_batch"] = rr_ok               # 完整批次：算 RMSSD 必须用这个

    parser = BrainLinkParser(on_eeg, on_ext, on_gyro, on_rr, None)
    try:
        ser = serial.Serial(port, baud, timeout=1)
    except Exception as exc:                              # 现场最高频失败点，给可执行提示
        raise SystemExit(
            f"[bridge] 串口打开失败 {port}@{baud}：{exc}\n"
            "  ① 设备管理器 →「端口(COM 和 LPT)」确认实际 COM 号\n"
            "  ② BrainLink 配对后生成**两个** COM 口，选「传出/输出」那个（选错能打开但无数据）\n"
            "  ③ 头环已开机、蓝牙未被手机 App 占用　④ 波特率 115200")
    broker.source = f"serial:{port}"
    print(f"[bridge] 串口已打开 {port}@{baud}，等待数据…（Ctrl+C 退出）")
    if hasattr(ser, "reset_input_buffer"):
        ser.reset_input_buffer()
    # 静默看门狗：串口"能打开"≠"有数据"。实测打开主板自带 COM1 会打印已打开然后零字节，
    # 网页上表现为"一直没状态"，极易被误判为引擎坏了——在数据源头就把话说明白。
    t_open = time.monotonic()
    nbytes = 0
    warned = False
    while True:
        raw = ser.read(64)
        if raw:
            nbytes += len(raw)
            if warned and nbytes <= len(raw):
                print(f"[bridge] 已收到数据（累计 {nbytes} 字节），通道正常", flush=True)
            try:
                parser.parse(raw)
            except Exception as exc:                      # 单条坏包不该终止采集
                print(f"[bridge] 解析失败（继续）: {exc}")
        elif not warned and nbytes == 0 and time.monotonic() - t_open > 15:
            warned = True
            print(f"[bridge] [警告] 已 15s 未收到任何字节（{port}）——串口开着，但设备没在发数据。\n"
                  "  ① 最可能：选到了「传入」口或主板自带口（如 COM1）→ 换另一个 COM 口试\n"
                  "  ② 其次：头环被手机 App 占用 → 关 App / 清后台\n"
                  "  ③ 再则：头环已休眠 → 按电源键唤醒",
                  file=sys.stderr, flush=True)


# --------------------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    broker: Broker = None            # type: ignore[assignment]
    ui_dir: str = DEFAULT_UI
    quiet = True
    allowed_hosts: set = set(LOOPBACK_HOSTS)     # 由 main() 按 --host/--allow-host 覆盖
    token: str = ""                              # --token：保护 /stream 与 /health
    # 不暴露 Python 版本（现场排查用日志已经够了，响应头少给攻击者一点信息）
    server_version = "BrainState/3.16"
    sys_version = ""

    # ---- 安全门 ----
    def _token_ok(self) -> bool:
        """可选访问令牌。EventSource 不能带自定义头，因此同时接受 query 与 header。"""
        if not self.token:
            return True
        supplied = self.headers.get("X-Auth-Token", "")
        if not supplied:
            supplied = parse_qs(urlparse(self.path).query).get("token", [""])[0]
        return hmac.compare_digest(str(supplied), self.token)

    def _send_sec_headers(self, stream: bool = False) -> None:
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if not stream:
            self.send_header("X-Frame-Options", "DENY")

    @staticmethod
    def _host_name(host: str) -> str:
        """从 Host 头取主机名：`127.0.0.1:8770` → `127.0.0.1`；`[::1]:8770` → `[::1]`。"""
        host = (host or "").strip().lower()
        if host.startswith("["):
            return host.split("]")[0] + "]"
        return host.rsplit(":", 1)[0] if ":" in host else host

    def _host_ok(self) -> bool:
        """DNS rebinding 防护：只接受白名单 Host。

        HTTP/1.1 要求带 Host；缺失即视为异常客户端（也挡住裸 HTTP/1.0 探测）。
        """
        raw = self.headers.get("Host")
        if not raw:
            return False
        return self._host_name(raw) in self.allowed_hosts

    def _reject_host(self) -> None:
        self._text(403, "forbidden: host not allowed（DNS rebinding 防护；"
                        "如需局域网访问，用 --allow-host 追加主机名）")

    # ---- 路由 ----
    def do_GET(self) -> None:                       # noqa: N802
        if not self._host_ok():
            self._reject_host()
            return
        path = unquote(urlparse(self.path).path)
        if path in ("/stream", "/health") and not self._token_ok():
            self.close_connection = True
            self._text(401, "unauthorized：该桥启用了访问令牌（--token），"
                            "请在地址后加 ?token=… 或用 X-Auth-Token 头")
            return
        if path == "/stream":
            self._stream()
            return
        if path == "/health":
            age = (round(time.time() - self.broker.last_frame_ts, 1)
                   if self.broker.last_frame_ts else None)
            self._json({"ok": self.broker.error is None,
                        "alive": self.broker.alive,
                        "error": self.broker.error,
                        "last_frame_age": age,
                        "source": self.broker.source,
                        "frames": self.broker.latest_seq,
                        "clients": self.broker.clients,
                        "uptime": round(time.time() - self.broker.started, 1)})
            return
        self._static(path)

    def do_HEAD(self) -> None:                      # noqa: N802
        if not self._host_ok():
            self._reject_host()
            return
        # HEAD /stream 若走 do_GET 会进 _stream 的无限推送循环：这条连接线程被永久占住，
        # 且 broker.clients 被 +1 后永不 -1（探测/监控脚本每次 HEAD 都泄漏一个计数与一条线程）。
        # HEAD 只该返回头部，不建流、不计客户端。
        if unquote(urlparse(self.path).path) == "/stream":
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache, no-transform")
            self._send_sec_headers(stream=True)
            self.end_headers()
            return
        self.do_GET()

    # ---- SSE ----
    def _stream(self) -> None:
        # v3.10.1：上限必须在**发任何响应头之前**判。
        # 原实现放在 end_headers() 之后：响应行已经是 200，再 _text(503) 只会往同一个
        # 响应体里追加一段 JSON，浏览器看到的仍是"已连接但没数据"（实测 18 连接全 200）。
        if self.broker.clients >= MAX_SSE_CLIENTS:
            self.close_connection = True
            self._text(503, f"too many clients（上限 {MAX_SSE_CLIENTS}；"
                            f"关掉多余的面板标签页后重试）")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache, no-transform")
        self.send_header("Connection", "keep-alive")
        self.send_header("X-Accel-Buffering", "no")
        self._send_sec_headers(stream=True)
        self.end_headers()
        self.broker.clients += 1
        # v3.16：新连接默认**只推实时**。旧实现从 seq=0 开始，会把 Broker 里最多 600 帧
        # （约 10 分钟）一次性回填；浏览器队列上限 300，用户打开面板先看 30–60 秒旧数据。
        # 需要补历史时显式 ?backfill=N（上限 600）。
        try:
            backfill = int(parse_qs(urlparse(self.path).query).get("backfill", ["0"])[0])
        except (TypeError, ValueError):
            backfill = 0
        backfill = max(0, min(backfill, BACKFILL_MAX))
        latest = self.broker.latest_seq
        seq = max(0, latest - backfill) if backfill else latest
        try:
            self.wfile.write(b": connected\n\n")
            self.wfile.flush()
            idle = 0.0
            while True:
                frames = self.broker.read_from(seq, timeout=1.0)
                if not frames:
                    idle += 1.0
                    if idle >= 15.0:                 # 心跳注释行，防代理断流
                        self.wfile.write(b": ping\n\n")
                        self.wfile.flush()
                        idle = 0.0
                    continue
                idle = 0.0
                for f in frames:
                    seq = f["seq"]
                    payload = json.dumps(_clean(f), ensure_ascii=False)
                    self.wfile.write(("data: " + payload + "\n\n").encode("utf-8"))
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass                                    # 浏览器关闭页面
        finally:
            self.broker.clients -= 1

    # ---- 静态文件 ----
    def _static(self, path: str) -> None:
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        # v3.16：realpath 而非 abspath —— abspath 不解析符号链接，ui_dir 内的软链
        # 仍可指向目录外。两侧都 realpath 后再做前缀比较。
        full = os.path.realpath(os.path.join(self.ui_dir, rel))
        root = os.path.realpath(self.ui_dir)
        if not full.startswith(root + os.sep) and full != root:      # 防路径穿越
            self._text(403, "forbidden")
            return
        if not os.path.isfile(full):
            self._text(404, f"not found: {rel}")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
            ".png": "image/png",
            ".ico": "image/x-icon",
        }.get(os.path.splitext(full)[1].lower(), "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if ctype.startswith("text/html"):
            # 无内联脚本；CSS 有内联 style（tooltip 色块），故放开 style-src inline
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; img-src 'self' data:; "
                             "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                             "connect-src 'self'")
        self._send_sec_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj: dict) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_sec_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _text(self, code: int, msg: str) -> None:
        body = msg.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._send_sec_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt: str, *args) -> None:
        if not self.quiet:
            sys.stderr.write("[bridge] " + fmt % args + "\n")


# --------------------------------------------------------------------------- main
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="脑机接口 真机/回放 数据桥（SSE）")
    ap.add_argument("--source", choices=["mock", "replay", "serial"], default="mock")
    ap.add_argument("--host", default="127.0.0.1", help="默认仅本机可访问（实验数据不出机器）")
    ap.add_argument("--allow-host", action="append", default=[], metavar="NAME",
                    help="追加允许的 Host 主机名（局域网访问时用，可重复；默认只认 127.0.0.1/localhost）")
    ap.add_argument("--port", type=int, default=8770,
                    help="HTTP 端口（默认 8770）。串口名请用 --serial-port，两者名字容易混")
    ap.add_argument("--ui-dir", default=DEFAULT_UI, help="界面目录（默认 组员文件\\任务一）")
    ap.add_argument("--csv", help="replay 源：CSV 路径")
    ap.add_argument("--speed", type=float, default=1.0, help="replay 倍速")
    ap.add_argument("--loop", action="store_true", help="replay 循环播放")
    ap.add_argument("--interval", type=float, default=1.0, help="mock 源帧间隔（秒）")
    ap.add_argument("--serial-port", dest="serial_port", default="COM5")
    ap.add_argument("--baud", type=int, default=115200, help="BrainLink TGAM 默认 115200")
    ap.add_argument("--signal-max", dest="signal_max", type=int, default=0,
                    help="signal 门控上限（poorSignalLevel 越小越好；0=不过滤）")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--token", default="",
                    help="可选访问令牌：设置后 /stream 与 /health 需 ?token=… 或 X-Auth-Token")
    args = ap.parse_args(argv)

    if not os.path.isdir(args.ui_dir):
        print(f"[bridge] 警告：界面目录不存在 {args.ui_dir}（仍可提供 /stream）")
    broker = Broker()
    broker.source = args.source

    if args.source == "replay":
        if not args.csv or not os.path.isfile(args.csv):
            raise SystemExit("replay 需要 --csv <存在的文件>")
        broker.source = f"replay:{os.path.basename(args.csv)}"
        target = lambda: source_replay(broker, args.csv, args.speed, args.loop)
    elif args.source == "serial":
        # v3.16：启动前先确认串口能开。否则线程里抛 SystemExit 会被 threading 静默吞掉，
        # HTTP 照常起来、界面显示"已连接"但零帧——现场会误判为引擎/蓝牙问题。
        try:
            import serial                                # type: ignore
        except ImportError:
            print("缺少 pyserial：便携运行时已内置；系统 Python 请 pip install pyserial",
                  file=sys.stderr, flush=True)
            return 2
        try:
            probe = serial.Serial(args.serial_port, args.baud, timeout=1)
            probe.close()
        except Exception as exc:                          # noqa: BLE001
            print(f"[bridge] [错误] 串口打不开 {args.serial_port}@{args.baud}：{exc}\n"
                  "  ① 设备管理器确认实际 COM 号；BrainLink 会生成两个口，选「传出/输出」\n"
                  "  ② 关掉占用该口的手机 App / 串口助手\n"
                  "  ③ 头环已开机、蓝牙未被其他程序占用；波特率 115200",
                  file=sys.stderr, flush=True)
            return 2
        broker.source = f"serial:{args.serial_port}"
        target = lambda: source_serial(broker, args.serial_port, args.baud, args.signal_max)
    else:
        target = lambda: source_mock(broker, args.interval)

    threading.Thread(target=_run_source, args=(target, broker), name="frame-source", daemon=True).start()

    Handler.broker = broker
    Handler.token = args.token
    if args.token:
        print("[bridge] 已启用访问令牌：/stream 与 /health 需 ?token=…（静态页不校验）")

    Handler.ui_dir = args.ui_dir
    # v3.10：Host 白名单 = 回环名 ∪ 绑定地址 ∪ --allow-host。
    # 绑 0.0.0.0 时不自动放行任意 Host（那等于没防 rebinding），必须显式 --allow-host。
    allowed = set(LOOPBACK_HOSTS)
    if args.host not in ("0.0.0.0", "::", ""):
        allowed.add(args.host.lower())
    for h in args.allow_host:
        allowed.add(Handler._host_name(h))
    Handler.allowed_hosts = allowed
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"⚠ 已绑定 {args.host}：数据可被局域网访问。允许的 Host：{sorted(allowed)}")
    Handler.quiet = not args.verbose
    try:
        httpd = _QuietServer((args.host, args.port), Handler)
    except OSError as exc:
        print(f"[bridge] [错误] 无法监听 {args.host}:{args.port}：{exc}\n"
              f"  端口可能已被上一个实例占用：先关闭旧窗口，或换 --port {args.port + 1}。",
              file=sys.stderr, flush=True)
        return 2
    print(f"[bridge] 源={broker.source}  界面=http://{args.host}:{args.port}/  "
          f"流=http://{args.host}:{args.port}/stream")
    print("[bridge] 浏览器打开上述地址 → 顶部选「真机/回放」→「校准」面板可做三段校准")

    def _stop(*_):
        # serve_forever 是阻塞的；shutdown 必须从另一个线程调用（官方文档要求）
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _stop)
        except (ValueError, OSError):
            pass                                  # 非主线程/平台不支持时忽略
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        print("[bridge] 已停止")
    return 0


if __name__ == "__main__":
    sys.exit(main())
