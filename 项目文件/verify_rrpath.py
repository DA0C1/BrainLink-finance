"""verify_rrpath.py — RR 三值链路端到端断言（live_bridge → JSON → Frame → rrBuf → RMSSD）

为什么需要它
    2026-10-23 的完整检查报告指出 P1-1：`live_bridge.FRAME_KEYS` 白名单漏掉 `rr_batch`/`hr`，
    一次 `onRR` 的 3 个间期到浏览器只剩最后 1 个，且批次号恒 `null` → 引擎只能跨帧差分。
    实测 RMSSD 由 24.14ms 虚高到 38.38ms（+59%）→ `z(RMSSD)` 偏高 → **面板上的 stress
    比 serial_bridge 上更难判出**，两侧判定对不上。

    既有 `verify_parity` 抓不到：它的帧序列只带单值 `rr_ms`、不带批次，天然覆盖不到真机路径。
    本脚本补的就是这个缺口——**只断言"数值等于多少"是不够的，必须断言"链路有没有把批次送到"**。

三条断言（缺一不可）
    A 通路：N 帧 × 3 值全部到达 rrBuf，批次号非空且批次数 == N
    B 口径：JS 与 Python 的 `_rmssd` 对同输入结果一致（容差 1e-6）
    C 分辨力：故意退回修复前的单值路径，RMSSD 必须与正确值显著不同
              —— 若 C 也"通过"，说明本脚本根本抓不到这个 bug（弱断言）

用法：
    ../runtime/python/python.exe verify_rrpath.py
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from state_engine import StateEngine          # noqa: E402
import live_bridge                             # noqa: E402  （只 import，不会起服务）

N_BATCH = 30          # 批次数（每批 3 个间期 → 90 个 RR）
TOL = 1e-6            # 双引擎口径比对容差
MIN_GAP = 1.0         # 反例必须与正确值差多少才算"抓到了"（ms）

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, ok, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))


def find_node() -> str | None:
    for cand in (os.environ.get("NODE"), shutil.which("node"),
                 r"C:\Program Files\nodejs\node.exe"):
        if cand and os.path.exists(cand):
            return cand
    return None


def build_frames(with_batch: bool) -> list[dict]:
    """构造与真机同构的帧：一次 onRR 带 3 个连续间期，批间基线缓变（模拟呼吸/体位漂移）。

    关键：**批内**差分小（相邻心跳连续），**跨批**跳变大。这样"跨帧差分"一定会算出更大的值，
    反例才有分辨力。
    """
    frames = []
    for j in range(N_BATCH):
        base = 800.0 + 2.0 * j                       # 批间基线缓变
        batch = (base, base + 8.0, base - 6.0)       # 批内：+8 / -14 的连续心跳
        raw = {
            "theta": 55.0, "alpha": 130.0, "beta": 100.0, "delta": 100.0, "gamma": 50.0,
            "attention": 65.0, "meditation": 55.0,
            "rr_ms": batch[-1],                      # 旧 UI 兼容字段：本批最后一个
            "rr_batch": list(batch) if with_batch else None,
            "hr": 75.0, "signal": 30, "gyro": [0.01, 0.01, 0.01],
            "ts": 1.0 + 1.2 * j,
            "source": "verify_rrpath",
        }
        frames.append(live_bridge._clean(raw))       # 走真实清洗函数，验证白名单不丢字段
    return frames


def py_rmssd_from_frames(frames: list[dict]) -> float:
    """按 Python 引擎的缓冲语义还原 rr_buf：(rr, batch) —— 只取同批内差分。"""
    buf = []
    for i, f in enumerate(frames):
        rb = f.get("rr_batch")
        if rb:
            for rr in rb:
                buf.append((float(rr), i))
        elif f.get("rr_ms") is not None:
            buf.append((float(f["rr_ms"]), None))    # 降级：批次 None（跨元素差分）
    return StateEngine._rmssd(buf)


def run_js(node: str, frames: list[dict]) -> dict:
    script = os.path.join(HERE, "_rrpath_js.js")
    proc = subprocess.run([node, script], input=json.dumps(frames).encode("utf-8"),
                          capture_output=True, timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"node 失败：{proc.stderr.decode('utf-8', 'replace')[:400]}")
    return json.loads(proc.stdout.decode("utf-8"))


def main() -> int:
    node = find_node()
    if not node:
        print("未找到 node：无法双引擎比对（装 node 或设 NODE 环境变量）")
        return 2
    print(f"node = {node}\n")

    # ---------------------------------------------------------------- A 通路
    frames = build_frames(with_batch=True)
    lost = [k for k in ("rr_batch", "hr", "signal") if frames[0].get(k) is None]
    check("A0 _clean 未丢字段（rr_batch / hr / signal）", not lost,
          f"丢失: {lost}" if lost else "三项均透传")

    js = run_js(node, frames)
    expect_rr = N_BATCH * 3
    check("A1 3 个间期全部到达 rrBuf", js["rrBufLen"] == expect_rr,
          f"rrBufLen={js['rrBufLen']}，期望 {expect_rr}")
    check("A2 批次号非空（未退化为跨帧差分）", js["nullBatch"] == 0,
          f"nullBatch={js['nullBatch']}")
    check("A3 批次数与帧数一致", js["batchCount"] == N_BATCH,
          f"batchCount={js['batchCount']}，期望 {N_BATCH}")
    check("A4 HR 已送达（heart 字段有值）", js["hr"] is not None and js["hr"] > 0,
          f"HR={js['hr']}")

    # ---------------------------------------------------------------- B 口径
    py_val = py_rmssd_from_frames(frames)
    js_val = js["rmssd"]
    same = (js_val is not None and math.isfinite(py_val)
            and abs(js_val - py_val) <= TOL)
    check("B 双引擎同输入 RMSSD 一致", same,
          f"JS={js_val:.4f}ms  Python={py_val:.4f}ms  差={abs((js_val or 0) - py_val):.2e}")

    # ---------------------------------------------------------------- C 分辨力
    frames_bad = build_frames(with_batch=False)      # 退回修复前：每帧只剩单值
    js_bad = run_js(node, frames_bad)
    py_bad = py_rmssd_from_frames(frames_bad)
    gap_js = abs((js_bad["rmssd"] or 0) - (js_val or 0))
    check("C1 反例：单值跨帧的 RMSSD 与正确值显著不同", gap_js >= MIN_GAP,
          f"错误={js_bad['rmssd']:.2f}ms  正确={js_val:.2f}ms  差={gap_js:.2f}ms"
          f"（阈值 {MIN_GAP}）")
    check("C2 反例两侧同时偏移（说明是口径问题而非单侧实现差异）",
          abs(py_bad - js_bad["rmssd"]) <= TOL,
          f"JS={js_bad['rmssd']:.4f}  Python={py_bad:.4f}")

    # ---------------------------------------------------------------- 汇总
    passed = sum(1 for _, ok, _ in results if ok)
    print(f"\n结论：{passed}/{len(results)} 项通过")
    if passed != len(results):
        print("  排查方向：live_bridge.FRAME_KEYS 是否含 rr_batch/hr → _clean 是否透传 →"
              " app.js nextFrame 是否传 rr_batch → engine.js _pushWindow 是否按批次入缓冲")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
