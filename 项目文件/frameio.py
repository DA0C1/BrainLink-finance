# -*- coding: utf-8 -*-
"""frameio.py — CSV 行 → 帧字典的**唯一**转换实现（v3.16）

为什么单独抽出来
----------------
同一份 CSV 有两条回放路径：
  · `serial_bridge.run_replay`  → Python 引擎（离线调参 / 验收）
  · `live_bridge._row_to_frame` → 浏览器引擎（面板回放）

两边各自手写了一份"低/高α合并、rr1–3 批次、缺列默认值"。它们已经漂移过：
`signal` 缺列时一边给 0（当作完美接触）、一边给 None（当作未知）；`rr_ms` 一边取批首、
一边取批尾；`gyro` 一边不调用、一边恒写 0。修接触帧/RR 时只改一边，另一条路径就静默不一致。

本模块只做"行 → 规范字典"，不依赖引擎；两条回放路径都必须走它。
字段默认值的选择原则：**缺值就是缺值**（None），不拿 0 冒充"最好/最安静"。
"""
from __future__ import annotations

import json
import math
from typing import Any

FRAME_FIELDS = ("theta", "alpha", "beta", "delta", "gamma",
                "attention", "meditation", "rr_ms", "rr_batch",
                "hr", "signal", "gyro", "ts", "source")


def _num(row: dict, *names: str, default: Any = None) -> Any:
    """按列名顺序取第一个可解析的有限数；空串/None/非法值跳过。"""
    for name in names:
        if name not in row:
            continue
        raw = row[name]
        if raw is None or raw == "":
            continue
        try:
            v = float(raw)
        except (TypeError, ValueError):
            continue
        if math.isfinite(v):
            return v
    return default


def _band(row: dict, key: str, low: str, high: str) -> float:
    """合并后的频段值；缺列按 0（与旧实现一致，缺失帧由伪迹门拦截）。"""
    v = _num(row, key)
    if v is not None:
        return v
    lo, hi = _num(row, low), _num(row, high)
    if lo is None and hi is None:
        return 0.0
    return (lo or 0.0) + (hi or 0.0)


def row_to_frame(row: dict, source: str = "replay") -> dict:
    """CSV 行 → 规范帧字典（与 live_bridge 下发的 JSON 同构）。

    只负责字段级归一化，不做范围校验（RR 生理范围由 RmssdWindow 过滤，
    接触门由引擎的 ArtifactDetector 判）。这样两条路径的口径完全一致。
    """
    alpha = _band(row, "alpha", "lowAlpha", "highAlpha")
    beta = _band(row, "beta", "lowBeta", "highBeta")
    gamma = _band(row, "gamma", "lowGamma", "highGamma")

    # RR：优先 rr1–rr3 整批（同一次 onRR 的三个连续间期）；其次 rr_batch JSON；最后单值。
    rr_batch = None
    if row.get("rr1") not in (None, ""):
        vals = []
        for c in ("rr1", "rr2", "rr3"):
            v = _num(row, c)
            if v is not None:
                vals.append(v)
        rr_batch = vals or None
    elif row.get("rr_batch") not in (None, ""):
        try:
            raw = json.loads(row["rr_batch"])
            if isinstance(raw, (list, tuple)):
                rr_batch = [float(x) for x in raw[:3] if x not in (None, "")]
        except (TypeError, ValueError, json.JSONDecodeError):
            rr_batch = None

    rr_ms = _num(row, "rr_ms", "rr")
    if rr_batch:
        rr_ms = rr_batch[-1]              # 兼容字段：批内最后一个（与旧 _row_to_frame 一致）

    return {
        "theta": _num(row, "theta", default=0.0),
        "alpha": alpha,
        "beta": beta,
        "delta": _num(row, "delta", default=0.0),
        "gamma": gamma,
        "attention": _num(row, "attention", "att", default=50.0),
        "meditation": _num(row, "meditation", "med", default=50.0),
        "rr_ms": rr_ms,
        "rr_batch": rr_batch,
        "hr": _num(row, "hr", "heart"),
        "signal": _num(row, "signal", "poorSignalLevel"),
        "gyro": [_num(row, "gyro_x", default=0.0),
                 _num(row, "gyro_y", default=0.0),
                 _num(row, "gyro_z", default=0.0)],
        "ts": _num(row, "ts", "timestamp", "time"),
        "source": source,
    }
