# -*- coding: utf-8 -*-
"""全局配置与实验口径（所有自定义数值均标注“经验初值”）。

纪律：
  - 时间基准统一 time.time()（epoch 秒，毫秒 3 位），date 由 time.strftime 派生；
  - 随机源统一 np.random.Generator(np.random.PCG64(seed))，禁止 random 模块；
  - 事件类型只使用中文枚举值。
"""

from __future__ import annotations

import os
import time
from enum import Enum
from pathlib import Path


# ---------------------------------------------------------------------------
# 路径
# ---------------------------------------------------------------------------

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DIR = PROJECT_ROOT / "frontend"
DATA_DIR = Path(os.environ.get("WATCHLAB_DATA_DIR", PROJECT_ROOT / "data"))


# ---------------------------------------------------------------------------
# 事件类型 / 状态口径
# ---------------------------------------------------------------------------

class EventType(str, Enum):
    CHASE_INDUCE = "追涨诱导"
    BREAKING_NEWS = "突发消息"
    VOL_SHOCK = "波动冲击"


DECISION_ATTRIBUTION_FALLBACK = "自发决策"

IS_IMPULSIVE_STATES = frozenset({"stress", "anxiety", "fatigue", "drowsy", "distracted"})  # 五类与 output_demo.py L166 / engine.js 口径一致

STATE_LABELS_CN = {
    "stress": "压力", "anxiety": "焦虑", "fatigue": "疲劳", "drowsy": "嗜睡",
    "distracted": "分心", "flow": "心流", "focus": "专注", "relaxed": "放松",
    "neutral": "数据不足",
}

TASK_CTX = {"task_active": True}

SIGNAL_QUALITY_PLACEHOLDER = ""            # 无真机：占位空值，待真机联调
SIGNAL_QUALITY_RATING = "待真机联调"        # 会话元数据占位，不臆造参数


# ---------------------------------------------------------------------------
# 实验轮次 / 顺序
# ---------------------------------------------------------------------------

ROUND_FEEDBACK = "有反馈"
ROUND_NO_FEEDBACK = "无反馈"
ROUNDS = (ROUND_FEEDBACK, ROUND_NO_FEEDBACK)

MODE_EXPERIMENT = "experiment"   # 正式实验：2 秒 / tick，受试者可见
MODE_TEST = "test"               # 研究者自检：更快、留日志、受试者端不得使用
MODE_DEMO = "demo"               # 对外演示：快速、自动成交、不落实验日志

# v2.9.2 会话运行时回收（数据已落盘，运行时可丢；防多被试多天连续跑批内存线性增长）
SESSION_KEEP_FINISHED_S = 1800   # 已结束会话保留 30min：够研究者回看/导出，之后释放
SESSION_KEEP_IDLE_S = 7200       # 创建后从未启动的僵尸会话保留 2h

ORDER_NO_FIRST = "先无反馈"
ORDER_FEEDBACK_FIRST = "先有反馈"
ORDERS = (ORDER_NO_FIRST, ORDER_FEEDBACK_FIRST)


def round_index(order: str, round_label: str) -> int:
    """返回该轮在受试者实验顺序中的序号（1/2），不向受试者暴露条件名。"""
    if order == ORDER_NO_FIRST:
        return 1 if round_label == ROUND_NO_FEEDBACK else 2
    return 1 if round_label == ROUND_FEEDBACK else 2


# ---------------------------------------------------------------------------
# 交易参数（经验初值）
# ---------------------------------------------------------------------------

INITIAL_CASH = 100_000.0
RATIO_CHOICES = (("1/4", 0.25), ("1/2", 0.50), ("3/4", 0.75), ("全仓", 1.00))
_MIN_CASH = 1e-6
_MIN_POS = 1e-9

TICK_MS_EXPERIMENT = 2000          # 经验初值：实验模式 2 秒 / tick（给受试者反应时间）
TICK_MS_DEMO = 2000                # 经验初值：演示沿用正式节奏（2 秒 / tick），3 分钟走完
DURATION_EXPERIMENT = 600          # 经验初值：单轮 10 分钟（秒）
DURATION_DEMO = 180                # 经验初值：演示 3 分钟（秒）
DEMO_SEED = 20261023               # 经验初值：演示固定种子
DEMO_TRADES_S = {20: ("买入", 0.50), 60: ("卖出", 0.50),
                 120: ("买入", 0.50), 150: ("卖出", 0.50)}  # 经验初值：按秒


# ---------------------------------------------------------------------------
# 决策 / 事件日志字段（全局统一口径）
# ---------------------------------------------------------------------------

DECISION_FIELDS = [
    "decision_id", "subject_id", "round", "order", "date", "seed",
    "t", "tick", "direction", "position_ratio", "qty", "price",
    "cash_after", "pos_after", "floating_pnl", "realized_pnl",
    "trigger_event", "engine_state", "engine_conf", "is_impulsive",
    "signal_quality",
]

EVENT_FIELDS = ["event_id", "subject_id", "round",
                "t_popup", "tick", "event_type", "popup_text", "t_close"]

PRICE_FIELDS = ["t", "tick", "price", "segment"]
STATE_FIELDS = ["t", "tick", "state", "conf", "is_impulsive", "signal_quality"]
INTERVENTION_FIELDS = ["intervention_id", "subject_id", "round",
                       "t", "tick", "state", "level", "action", "channel"]

ATTRIBUTION_WINDOW_S = 60.0     # 经验初值：决策归因窗口（待验证方案负责人确认）


# ---------------------------------------------------------------------------
# 行情剧本（与 v1 一致：段结构固定，种子只驱动噪声）
# ---------------------------------------------------------------------------

SEGMENT_FLAT = "平缓横盘"
SEGMENT_RALLY = "快速拉升"
SEGMENT_DECLINE = "震荡下跌"

# 段比例固定，按实际 tick 数等比缩放（10 分钟 / 2 秒一个点时为 100/100/100）
SEGMENT_TEMPLATE = ((SEGMENT_FLAT, 180), (SEGMENT_RALLY, 180), (SEGMENT_DECLINE, 240))

S0 = 100.0
_EPS = 1e-12

_OU_KAPPA = 0.10                # 经验初值
_OU_SIGMA = 0.0020              # 经验初值
_RALLY_DRIFT = 0.0008           # 经验初值
_RALLY_SIGMA = 0.0040           # 经验初值
_DECLINE_DRIFT = -0.0006        # 经验初值
_DECLINE_SIGMA = 0.0100         # 经验初值
_DECLINE_GAP_PROB = 0.02        # 经验初值
_DECLINE_GAP_SIZE = (0.01, 0.03)  # 经验初值

_CHASE_PRE_WINDOW = 30           # 经验初值
_CHASE_DRIFT_BOOST = 0.0012      # 经验初值
_NEWS_GAP_RANGE = (0.02, 0.05)   # 经验初值
_NEWS_AFTERSHOCK = 20            # 经验初值
_NEWS_AFTERSHOCK_DRIFT = -0.0008  # 经验初值
_SHOCK_WINDOW = 30               # 经验初值
_SHOCK_SIGMA_MULT = 5.0          # 经验初值
_SHOCK_ALT_DRIFT = 0.0010        # 经验初值


# ---------------------------------------------------------------------------
# 事件调度（经验初值）
# ---------------------------------------------------------------------------

EVENT_PROB_PER_MIN = {
    EventType.CHASE_INDUCE: 0.25,
    EventType.BREAKING_NEWS: 0.15,
    EventType.VOL_SHOCK: 0.20,
}
NO_EVENT_LEAD_S = 30
NO_EVENT_TAIL_S = 20
MIN_EVENT_GAP_S = 45
POPUP_AUTO_CLOSE_S = 8.0

EVENT_TEXT_VARIANTS = {
    EventType.CHASE_INDUCE: (
        "突破新高！主力资金大幅流入",
        "量能放大，机构抢筹中",
        "连续阳线，市场情绪高涨",
        "大单密集成交，跟风盘涌入",
    ),
    EventType.BREAKING_NEWS: (
        "突发：行业监管政策收紧",
        "快讯：上游原材料价格暴涨",
        "突发：龙头公司业绩暴雷",
        "快讯：海外市场深夜大跌",
    ),
    EventType.VOL_SHOCK: (
        "行情剧烈波动，请注意风险",
        "多空激战，振幅显著扩大",
        "波动率骤升，谨慎操作",
        "盘中异动，注意控制仓位",
    ),
}

DEMO_EVENTS_S = [(40, EventType.BREAKING_NEWS),
                 (100, EventType.CHASE_INDUCE),
                 (140, EventType.VOL_SHOCK)]  # 演示固定事件（秒），保证三类都出现


# ---------------------------------------------------------------------------
# MockEngine / 合成帧（经验初值；仅演示与自检，不代表真实判别准确率）
# ---------------------------------------------------------------------------

MOCK_WARMUP_S = 10
MOCK_TICK_BLOCK_S = 30
MOCK_FATIGUE_ONSET_S = 360
MOCK_DROWSY_ONSET_S = 520
MOCK_EVENT_STATE_S = 25
FEEDBACK_COOLDOWN_S = 60.0


# ---------------------------------------------------------------------------
# 服务 / 安全
# ---------------------------------------------------------------------------

DEFAULT_RESEARCH_TOKEN = os.environ.get("WATCHLAB_RESEARCH_TOKEN", "research-demo-token")
SUBJECT_TOKEN_BYTES = 16


def date_str(ts: float | None = None) -> str:
    """date 由 time.time() 派生（不使用 datetime 模块）。"""
    return time.strftime("%Y-%m-%d", time.localtime(time.time() if ts is None else ts))
