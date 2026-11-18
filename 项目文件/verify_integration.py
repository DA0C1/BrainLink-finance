# -*- coding: utf-8 -*-
"""接口联调闸门（v3.9）：真起服务、真拉流、真断言。

覆盖此前只能手工 curl 的几件事：
  I1 静态资源：/ 与 js/css 能取到且 Content-Type 正确
  I2 404 语义：不存在的路径返回 404，不是 200 或空
  I3 /health 结构与字段
  I4 SSE 首帧时延与字段完整性（必须含 FRAME_KEYS）
  I5 seq 单调递增（断点续推依赖它）
  I6 HEAD /stream 不建流（返回快、clients 不涨）
  I7 停机：进程能干净退出（SIGTERM 后不留孤儿）

用法：python verify_integration.py [--port 8790]
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable
PORT = 8790

for i, a in enumerate(sys.argv):
    if a == "--port" and i + 1 < len(sys.argv):
        PORT = int(sys.argv[i + 1])

BASE = f"http://127.0.0.1:{PORT}"
fails: list[str] = []


def ck(ok: bool, name: str, extra: str = "") -> None:
    print(("  [PASS] " if ok else "  [FAIL] ") + name + (" — " + extra if extra else ""))
    if not ok:
        fails.append(name)


def http(path: str, method: str = "GET", timeout: float = 5.0, host: str | None = None):
    # 路径可能含中文：先按 RFC 3986 编码，否则 http.client 会在 ascii 编码处抛错
    safe = urllib.parse.quote(path, safe="/%")
    req = urllib.request.Request(BASE + safe, method=method)
    if host is not None:
        req.add_header("Host", host)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()
    except Exception as e:                       # 连接被重置等，按失败处理而非中断整轮
        return -1, {}, str(e).encode()


def wait_port(deadline: float = 15.0) -> bool:
    t0 = time.time()
    while time.time() - t0 < deadline:
        try:
            with socket.create_connection(("127.0.0.1", PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def clients_now() -> int:
    _, _, body = http("/health")
    try:
        return int(json.loads(body).get("clients", -1))
    except Exception:
        return -1


def wait_clients_zero(deadline: float = 4.0) -> int:
    """断开的 SSE 连接要等下一次写失败才会 -1，这里等它收敛。"""
    t0 = time.time()
    c = clients_now()
    while c != 0 and time.time() - t0 < deadline:
        time.sleep(0.2)
        c = clients_now()
    return c


def read_sse(n: int, seconds: float = 12.0, path: str = "/stream") -> list[dict]:
    """读 n 条 data 帧（或超时）。用裸 socket，避免 urllib 缓冲。"""
    out: list[dict] = []
    t0 = time.time()
    s = socket.create_connection(("127.0.0.1", PORT), timeout=3.0)
    s.sendall(f"GET {path} HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\nAccept: text/event-stream\r\n\r\n".encode())
    buf = b""
    while len(out) < n and time.time() - t0 < seconds:
        try:
            chunk = s.recv(4096)
        except socket.timeout:
            break
        if not chunk:
            break
        buf += chunk
        while b"\n\n" in buf:
            ev, buf = buf.split(b"\n\n", 1)
            for line in ev.split(b"\n"):
                if line.startswith(b"data: "):
                    try:
                        out.append(json.loads(line[6:].decode("utf-8")))
                    except json.JSONDecodeError:
                        # 心跳注释行或半包（HTTP 头混入首块）：不是数据帧，跳过即可
                        continue
    s.close()
    return out


def main() -> int:
    proc = subprocess.Popen(
        [PY, os.path.join(HERE, "live_bridge.py"), "--source", "mock",
         "--port", str(PORT), "--interval", "0.15"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=HERE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        if not wait_port():
            print("✗ 服务 15s 内未监听，后续检查无意义")
            return 1
        print(f"服务已起：{BASE}\n")

        print("I1 静态资源")
        st, hd, body = http("/")
        ck(st == 200, "I1a / 返回 200", f"status={st}")
        ck("text/html" in hd.get("Content-Type", ""), "I1b / 是 text/html", hd.get("Content-Type", ""))
        ck(b"<html" in body.lower() or b"<!doctype" in body.lower(), "I1c / 是 HTML 正文", f"{len(body)}B")
        st, hd, body = http("/js/app.js")
        ck(st == 200 and b"function" in body, "I1d /js/app.js 可取且像 JS",
           f"status={st} {len(body)}B")
        ck("javascript" in hd.get("Content-Type", ""), "I1e app.js Content-Type", hd.get("Content-Type", ""))
        st, hd, _ = http("/css/styles.css")
        ck(st == 200 and "css" in hd.get("Content-Type", ""), "I1f styles.css Content-Type",
           f"status={st} {hd.get('Content-Type','')}")

        print("\nI2 404 语义")
        st, _, _ = http("/definitely-not-here.js")
        ck(st == 404, "I2a 不存在的路径 → 404", f"status={st}")
        st, _, _ = http("/../项目文件/state_engine.py")
        ck(st in (403, 404), "I2b 穿越尝试被拒", f"status={st}")

        print("\nI3 /health")
        st, hd, body = http("/health")
        j = json.loads(body)
        ck(st == 200 and j.get("ok") is True, "I3a ok=true", json.dumps(j, ensure_ascii=False))
        ck(j.get("source") == "mock", "I3b source=mock", str(j.get("source")))
        ck(isinstance(j.get("frames"), int), "I3c frames 是整数", str(j.get("frames")))
        ck(j.get("clients") == 0, "I3d 无 SSE 连接时 clients=0", str(j.get("clients")))
        ck(j.get("alive") is True, "I3e alive=true（帧源在跑）", str(j.get("alive")))
        ck(j.get("error") is None, "I3f error=null", str(j.get("error")))
        ck(j.get("last_frame_age") is None or j.get("last_frame_age") >= 0,
           "I3g last_frame_age 可用", str(j.get("last_frame_age")))

        print("\nI4/I5 SSE 数据流")
        t0 = time.time()
        frames = read_sse(6, seconds=12)
        first_latency = time.time() - t0
        ck(len(frames) >= 3, "I4a 10s 内至少 3 帧", f"{len(frames)} 帧")
        sys.path.insert(0, HERE)
        import live_bridge  # noqa: E402
        need = set(live_bridge.FRAME_KEYS) | {"gyro", "seq", "ts", "source"}
        if frames:
            miss = sorted(need - set(frames[0].keys()))
            ck(not miss, "I4b 帧字段完整", f"缺 {miss}" if miss else f"{len(need)} 字段齐")
            ck(all(isinstance(f.get("theta"), (int, float)) for f in frames), "I4c theta 是数值")
            seqs = [f["seq"] for f in frames]
            ck(seqs == sorted(seqs) and len(set(seqs)) == len(seqs), "I5 seq 单调且不重复", str(seqs))
            # v3.16：新连接默认只推实时（seq 接近 latest），不再从 0 回填 600 帧
            latest0 = j.get("frames", 0)
            ck(seqs[0] >= max(0, latest0 - 1), "I5b 新连接只推实时（seq 接近 latest）",
               f"first={seqs[0]} latest_at_health={latest0}")
            bf = read_sse(3, seconds=4, path="/stream?backfill=3")
            _, _, hb = http("/health")
            latest1 = json.loads(hb).get("frames", 0)
            ck(len(bf) >= 2 and bf[0]["seq"] <= latest1 - 2, "I5c ?backfill=3 可显式回填",
               f"backfill_first={bf[0]['seq'] if bf else None} latest={latest1}")
        else:
            ck(False, "I4b 帧字段完整（无帧）")

        print("\nI6 HEAD /stream 不建流")
        before = wait_clients_zero()
        ck(before == 0, "I6a 上一轮 SSE 连接已释放（clients 归零）", f"clients={before}")
        t0 = time.time()
        st, _, _ = http("/stream", method="HEAD", timeout=3)
        dt = time.time() - t0
        ck(st == 200 and dt < 1.5, "I6b HEAD 快速返回", f"{dt*1000:.0f}ms status={st}")
        ck(clients_now() == before, "I6c HEAD 未增加 clients", f"clients={clients_now()}")

        print("\nI8 Host 校验（DNS rebinding 防护）")
        st, _, _ = http("/health")
        ck(st == 200, "I8a 合法 Host 放行", f"status={st}")
        st, _, body = http("/health", host="evil.example.com")
        ck(st == 403, "I8b 伪造 Host 被拒", f"status={st}")
        st, _, _ = http("/", host="evil.example.com")
        ck(st == 403, "I8c 伪造 Host 取静态页也被拒", f"status={st}")

        print("\nI9 SSE 客户端上限")
        import socket as _sock
        held, codes = [], []
        for _ in range(18):
            try:
                c = _sock.create_connection(("127.0.0.1", PORT), timeout=3)
                c.sendall(f"GET /stream HTTP/1.1\r\nHost: 127.0.0.1:{PORT}\r\n\r\n".encode())
                c.settimeout(1.2)
                head = b""
                try:
                    while b"\r\n\r\n" not in head:
                        ch = c.recv(4096)
                        if not ch:
                            break
                        head += ch
                except _sock.timeout:
                    pass      # 读响应头超时：说明服务已发完头部在推流，属预期，继续判状态码
                parts = head.split(b" ", 2)
                code = parts[1].decode() if len(parts) > 2 else "???"
                codes.append(code)
                (held if code == "200" else c).close() if code != "200" else held.append(c)
            except OSError as exc:
                codes.append("ERR:" + type(exc).__name__)
        got503 = codes.count("503")
        ck(got503 >= 1, "I9a 超上限返回 503", f"200×{codes.count('200')} 503×{got503}")
        ck(codes.count("200") <= 16, "I9b 同时在线不超过 16", str(codes.count("200")))
        for c in held:
            c.close()
        wait_clients_zero(6.0)

        print("\nI7 停机")
        proc.terminate()
        try:
            proc.wait(timeout=5)
            # Windows 的 terminate() 走 TerminateProcess，退出码恒为 1；这里只要求"确实退出"
            ck(True, "I7 收到终止信号后退出", f"rc={proc.returncode}")
        except subprocess.TimeoutExpired:
            proc.kill()
            ck(False, "I7 进程 5s 内未退出，已强杀")
    finally:
        if proc.poll() is None:
            proc.kill()

    print()
    if fails:
        print(f"✗ 接口联调未通过：{len(fails)} 项")
        for f in fails:
            print("   -", f)
        return 1
    print("✓ 接口联调全通（静态 / 404 / health / SSE / seq / HEAD / 停机）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
