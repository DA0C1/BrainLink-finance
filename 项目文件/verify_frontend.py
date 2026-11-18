# -*- coding: utf-8 -*-
"""前后端同步闸门（v3.8 新增）。

校验三处"同一条数据"的定义是否一致：
  1. live_bridge.FRAME_KEYS        —— 服务端 SSE 实际下发的字段
  2. engine.js  class Frame        —— 前端引擎认识的字段
  3. app.js    nextFrame() 的映射  —— 界面把服务端帧搬进 Engine 时真正传了哪些

历史教训：`signal`（接触质量）三处里前两处都有，唯独第 3 处没传，
于是 engine.js 里 v3.4 加的接触门在**真机实时链路上从未生效**——
Python 侧有 SdkAdapter 硬门兜底，JS 侧没有第二道，脱戴帧照样进特征窗。
这种"字段存在但没接线"的缺陷，任何单侧测试都发现不了。

纯静态解析，不依赖运行环境；有偏差即返回非 0。
用法：python verify_frontend.py
"""
from __future__ import annotations

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
JS_DIR = os.path.join(HERE, "..", "组员文件", "任务一", "js")

# 服务端字段名 → Engine 字段名（协议名与引擎名不同的）
ALIAS = {"hr": "heart"}

# 服务端额外下发、Engine 不需要的
IGNORE = {
    "seq": "帧序号，仅用于断点续推",
    "source": "数据源标识（mock/serial/replay）",
    "ts": "设备时间戳；实时链路刻意不传，引擎回退 wall-clock",
}

# Engine 侧非服务端来源的字段（决策结果 / 校准统计，由引擎自己填）
DERIVED = {
    "rr_batch": "一次 onRR 的 3 个间期，走 rr_batch 通道",
    "mu": "校准基线统计量", "sigma": "校准基线统计量", "sigma_min": "校准基线统计量",
    "log_flag": "对数域标记", "state": "决策结果", "conf": "决策结果",
    "scores": "决策结果", "gates": "决策结果", "margins": "决策结果",
    "evidence": "决策结果", "reason": "决策结果", "level": "决策结果",
    "action": "决策结果", "cooldown_left": "决策结果", "smooth": "平滑缓存",
    "baseline": "基线引用", "ts": "时间戳（见 IGNORE）",
}


def read(path: str) -> str:
    return io.open(path, encoding="utf-8", newline="").read().replace("\r\n", "\n")


def _brace_block(s: str, start: int, open_ch: str = "{", close_ch: str = "}") -> str:
    """从 start 处的开括号取到配对的闭括号（跳过字符串与注释）。"""
    depth = 0
    i = start
    in_str = None
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'`":
            in_str = c
        elif s.startswith("//", i):
            j = s.find("\n", i)
            i = len(s) if j < 0 else j
            continue
        elif s.startswith("/*", i):
            j = s.find("*/", i)
            i = len(s) if j < 0 else j + 2
            continue
        elif c == open_ch:
            depth += 1
        elif c == close_ch:
            depth -= 1
            if depth == 0:
                return s[start + 1:i]
        i += 1
    raise ValueError("括号不配对")



def _strip_comments(body: str) -> str:
    """去掉行注释（字符串内的 // 不算）。注释里带冒号会让字段解析整段丢失。"""
    out, i, in_str = [], 0, None
    while i < len(body):
        c = body[i]
        if in_str:
            out.append(c)
            if c == "\\":
                out.append(body[i + 1] if i + 1 < len(body) else "")
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'`":
            in_str = c
            out.append(c)
        elif body.startswith("//", i):
            j = body.find("\n", i)
            i = len(body) if j < 0 else j
            out.append("\n")
            continue
        else:
            out.append(c)
        i += 1
    return "".join(out)


def _top_level_split(body: str) -> list[str]:
    """按顶层逗号切分（忽略括号/字符串内的逗号）。"""
    parts, buf, depth, in_str = [], [], 0, None
    i = 0
    while i < len(body):
        c = body[i]
        if in_str:
            buf.append(c)
            if c == "\\":
                buf.append(body[i + 1] if i + 1 < len(body) else "")
                i += 2
                continue
            if c == in_str:
                in_str = None
        elif c in "\"'`":
            in_str = c
            buf.append(c)
        elif c in "([{":
            depth += 1
            buf.append(c)
        elif c in ")]}":
            depth -= 1
            buf.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(c)
        i += 1
    if "".join(buf).strip():
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def server_keys() -> list[str]:
    s = read(os.path.join(HERE, "live_bridge.py"))
    m = re.search(r"FRAME_KEYS\s*=\s*\(([^)]*)\)", s, re.S)
    if not m:
        raise SystemExit("找不到 FRAME_KEYS")
    return re.findall(r'"([a-zA-Z_]+)"', m.group(1))


def frame_fields() -> list[str]:
    """class Frame 构造函数体里 this.X = ... 的字段名。"""
    s = read(os.path.join(JS_DIR, "engine.js"))
    i = s.index("class Frame")
    b = s.index("{", i)
    body = _brace_block(s, b)
    return re.findall(r"this\.([a-zA-Z_]+)\s*=", body)


def live_mapping() -> dict[str, str]:
    s = read(os.path.join(JS_DIR, "app.js"))
    i = s.index("function nextFrame")
    j = s.index("new Frame(", i)
    b = s.index("{", j)
    body = _strip_comments(_brace_block(s, b))
    out = {}
    for part in _top_level_split(body):
        m = re.match(r"([a-zA-Z_]+)\s*:\s*([\s\S]+)$", part)
        if m:
            out[m.group(1)] = m.group(2).strip()
    return out


def main() -> int:
    fails: list[str] = []

    def ck(ok: bool, name: str, extra: str = "") -> None:
        print(("  [PASS] " if ok else "  [FAIL] ") + name + (" — " + extra if extra else ""))
        if not ok:
            fails.append(name)

    sk, ff, lm = server_keys(), frame_fields(), live_mapping()
    print("服务端下发 :", ", ".join(sk))
    print("Frame 字段 :", ", ".join(ff))
    print("实时映射   :", ", ".join(lm.keys()))
    print()

    print("F1 服务端每个字段：要么 Engine 认识（含别名），要么在忽略清单")
    for k in sk:
        eng = ALIAS.get(k, k)
        if eng in ff:
            ck(True, "F1 %-10s → Engine.%s" % (k, eng))
        elif k in IGNORE:
            print("  [SKIP] F1 %-10s 明确忽略：%s" % (k, IGNORE[k]))
        else:
            ck(False, "F1 %-10s 服务端下发但 Engine 无此字段" % k)

    print()
    print("F2 Engine 的每个字段：实时链路必须真的传（否则恒为默认值）")
    for k in ff:
        if k in lm:
            ck(True, "F2 %-12s 已传值" % k)
        elif k in DERIVED:
            print("  [SKIP] F2 %-12s 派生字段：%s" % (k, DERIVED[k]))
        else:
            ck(False, "F2 %-12s Engine 有但 nextFrame 未传" % k)

    print()
    print("F3 映射里不能出现 Engine 不认识的 key（typo 会静默失效）")
    for k in lm:
        ck(k in ff, "F3 %-12s 是 Engine 字段" % k)

    print()
    print("F4 app.js 引用的 DOM id 必须真实存在（$('x') 取不到就是 null，静默失效）")
    app = read(os.path.join(JS_DIR, "app.js"))
    html = read(os.path.join(HERE, "..", "组员文件", "任务一", "index.html"))
    used = set(re.findall(r"\$\('([A-Za-z0-9_]+)'\)", app))
    have = set(re.findall(r'id="([A-Za-z0-9_]+)"', html))
    missing = sorted(used - have)
    for m in missing:
        ck(False, "F4 #%s 在 app.js 里被引用但 HTML 没有" % m)
    if not missing:
        ck(True, "F4 %d 个 id 全部存在" % len(used))

    print()
    print("F5 回放帧 `frameio.row_to_frame` 必须覆盖服务端全部字段（少一个就会被 _clean 补 null）")
    lb = read(os.path.join(HERE, "frameio.py"))
    i = lb.index("def row_to_frame")
    j = lb.index("return {", i)
    body = _strip_comments(_brace_block(lb, lb.index("{", j)))
    body = re.sub(r"#[^\n]*", "", body)      # Python 的 # 注释（_strip_comments 只认 //）
    rk = set()
    for part in _top_level_split(body):
        mm = re.match(r'["\']([a-zA-Z_]+)["\']\s*:', part)
        if mm:
            rk.add(mm.group(1))
    miss = [k for k in sk if k not in rk and k not in ("ts", "source", "seq")]
    for m in miss:
        ck(False, "F5 回放帧缺少 %s（_clean 会补 null）" % m)
    if not miss:
        ck(True, "F5 回放帧覆盖 %d 个字段" % len(rk))

    print()
    if fails:
        print("✗ 同步闸门未通过：%d 项" % len(fails))
        for f in fails:
            print("   -", f)
        return 1
    print("✓ 前后端帧字段完全同步")
    return 0


if __name__ == "__main__":
    sys.exit(main())
