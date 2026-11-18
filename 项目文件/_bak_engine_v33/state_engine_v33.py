# -*- coding: utf-8 -*-
"""state_engine.py — 状态判别引擎 MVP（v2.2 规格实现）

对应《状态判别建模设计_优化版_组长E_20261015.md》：
- MVP 范围：专注/分心/放松/压力 4 状态 + 门控 + 线性加权评分 + 仲裁 + 反馈 + 理智度记录
- 恐慌/过度兴奋/嗜睡/疲劳 为增强版第二阶段（本文件留接口，不实现）

口径：
- 全特征 z-score 个体偏离（相对基线 μ/σ），不判绝对状态
- 专注主口径 = R_tb(θ/β) 下降（M13 决议），HR 作专注/兴奋区分预留
- 压力 = RMSSD_60 下降 + 体动平静门控（R5 运动回归思想）
- 置信度 = 线性映射 + clamp [0.6, 0.95]（v2.2 双绝对值分母）
- 基线：校准协议 μ/σ + σ_min 下限保护；θ_ref 冻结（疲劳积分参考系）
- 嗜睡（增强版）：TAR 判"θ 主导"（水平判据，R8）+ δ 判"真嗜睡"（变化方向，M5/Zhang 2018）
  合取口径（v2.3 S5）——答辩口径：TAR 判是否 θ 主导、δ 判是否真嗜睡，合取更稳

运行演示（mock 数据验证管道）：
  python state_engine.py
"""
from __future__ import annotations

import math
import random
import time
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 常量（初值，待真机校准）
EWMA_ALPHA = 0.2          # EEG 快特征平滑（SDK 指南 4.2：0.1–0.3）
ARTIFACT_RATIO = 8.0      # 伪迹门限：单频段对平滑值偏差倍数（模拟实测）
ARTIFACT_RATIO_RTB = 15.0 # v2.8：R_tb 比值门限独立放宽——状态切换 R_tb 可达 5-10 倍，
                          # 原 8× 把真实疲劳/分心切换当伪迹丢弃（I_f 永不积分）；单频段 8× 已覆盖硬件伪迹
# v3.1：β+γ 肌电专用门（默认关闭，真机 V4 通过后按《规划》5.3 打开）
# 背景：8× 门是"突变检测"，对**持续**肌电（说话/咬牙）会饱和——首帧爆发触发一次，
#       随后平滑基线被抬上去，后续帧不再超过 8×，伪迹率上不去 → 朗读段 EEG 被当有效数据。
# 判据：β、γ 同时高于 B 段参照的 N 倍，且 θ 仍在参照附近（θ 大动属真实状态迁移，不该拦）。
# 取 4× 而非 2×：真实肌电幅值远超 EEG（10–100×），2× 会与"认知负荷升 β"混淆。
ARTIFACT_EMG_GATE = False       # 总开关；verify_hw.py V4 判"需启用"时改 True（两引擎同步）
ARTIFACT_EMG_RATIO = 4.0        # β/γ 相对 B 段中位数的倍数门限
ARTIFACT_EMG_THETA_TOL = 0.35   # θ 允许偏离参照的比例（±35% 内视为"θ 没动"）
RR_MIN_MS, RR_MAX_MS = 300, 2000     # RR 生理范围（R7）
Z_CONC = -1.0             # 专注：z(R_tb) < -1.0
Z_RMSSD_NO_STRESS = -1.0  # 专注排除法（v2.3 S1）：无应激佐证 z(RMSSD_60) > -1.0
                          # （v2.7.1 下调：原 -0.5 过紧——专注时 HRV 略低于基线均值是常态，
                          # 实测 focus mock RMSSD z=-0.53 即误排除 focus → neutral；
                          # -1.0 与 Z_CONC 对称，stress 判据仍 Z_STRESS=-1.5 文献值不受影响）
                          # 旧 v2.2 Z_HR_CAP(+1.5) 已废弃——认知努力也升心率，与弃用 β 上限同构
Z_DIST = +1.0             # 分心：z(R_tb) > +1.0
Z_RELAX_A = +1.5          # v2.7 放松：z(A_rel) > +1.5（原复用 Z_DIST=1.0——focus(α+30%)
                          # 时 A_rel z 恰在 1.0 边界抖动，半数帧 relaxed 门控过 → 时间确认
                          # 窗口占比叠加、focus 漂移 relaxed；1σ 不算状态，+1.5σ 才构成
                          # "明显放松"，与 Z_STRESS=-1.5 对称）
Z_STRESS = -1.5           # 压力：z(RMSSD_60) < -1.5（v2.6 对齐 R6/Stephenson 2021 文献值 −1.5σ；
                          # 旧初值 −1.0 更敏感但无文献直接支撑，口径审计修正）
Z_STILL_CAP = +1.0        # 体动平静门控：专注（v2.3 S1）与压力（v2.2 R5 思想）共用
WEAK_CONF = 0.7           # 弱判别阈值（v2.3 S4）：conf∈[0.6,0.7) 仅记录不提醒
WEAK_ESCALATE_FRAMES = 60 # v2.6：弱判别持续 60 帧（≈80s）升级一次低强度提醒（防长期弱判别零干预）
WEAK_ESCALATE_CD = 300    # 弱判别升级提醒冷却（s）
# ---- v2.4：官方指数状态（指南 2.1/2.2 判据；attention/meditation 为设备 0-100 常模指数）----
ANX_ATT_MAX = 40.0   # 焦虑：专注度 <40（较低值区；指南 2.1 明示"焦虑时二者同时低值"）
ANX_MED_MAX = 40.0   # 焦虑：放松度 <40
ANX_Z = -1.5         # 焦虑 z 判据（v2.5 优化）：绝对常模 OR 相对个体基线显著下滑（双判据）
FLOW_ATT_MIN = 60.0  # 心流：专注度 ≥60（高值区起点；指南 2.2 "稳定于高值区间"）
FLOW_MED_MIN = 60.0  # 心流：放松度 ≥60
FLOW_HOLD_FRAMES = 5 # 心流"稳定高值"持续帧数（≈7s；指南 2.2 稳定于高值区间，防单帧抖动假阳性）
FLOW_Z = 1.5          # v2.6 心流 z 判据：绝对双高 OR z(att)>+1.5 且 z(med)>+1.5（与焦虑对称；
                      # 个体指数基线低的人绝对 60 永远达不到，需相对判据兜底）
SYNC_WIN = 10        # 派生同步率窗口（帧；指南 2.2 同步率=双指数趋势一致性，非官方独立字段）
SYNC_MIN = 0.6       # 同向帧占比下限（≥0.6 视为协同趋势；R5 4/5 思想同源）
MIN_SYNC_FRAMES = 5  # 同步率有效差分帧数下限（防 1 帧同向即达标 1.0 的假阳性）
# ---- 增强版第二阶段初值（v2.3 S3/S6，MVP 不实现，仅留接口）----
IF_THETA_NORM = 1.2       # I_f 触发：θ_norm>1.2（θ_ref=校准冻结基线，防滚动基线吃掉）
IF_HOLD_S = 300           # 疲劳判据：I_f≥300 等效秒（"θ 超阈 0.3 倍持续 300s"为 1 个单位）
IF_REF_EXCESS = 0.3       # v2.9.1 幅度归一：θ_norm 高出阈 0.3 倍 → 1.0 等效秒/秒（线性加权）
IF_RATE_MAX = 2.0         # v2.9.1 单帧累积速率上限（防极端 θ 尖峰瞬间刷满）
IF_RECOVER_HALF_S = 600   # v2.9.1 恢复半衰期：θ 回落至阈下时 I_f 每 600s 减半（可恢复，不复位）
IF_MAX_DT_S = 60          # v2.9.1 单帧积分时长上限：SDK 掉线/休眠恢复后首帧 dt 可能达数百秒，
                          # 不封顶会"一帧刷满疲劳"（实测断帧 3600s → 单帧 +7200）。60s 覆盖最慢帧率
# v2.9 曾改为"纯时长"积分（θ 一过阈即 1:1 累加、无衰减），v2.9.1 修正其两处过度：
#   (a) 丢失幅度权重——θ_norm=1.21 与 3.0 累积速率相同，轻微波动即被当重度疲劳；
#   (b) 删除恢复通道——I_f 单调不减，一次长时高 θ 之后永久锁存（实测张角 1200s 仍 399）。
# 现口径：I_f += min((θ_norm−1.2)/0.3, 2.0)·dt；θ_norm≤1.2 时 I_f 半衰 600s。
# 现实可达性：θ_norm=1.5 → 300s 达阈；1.3 → 900s；1.21 → 9000s（缓慢，符合轻度抬升）。
# S6 疲劳→嗜睡迁移：设计稿原定"疲劳触发后 60s 内转嗜睡条件则改判"，实现上由 PRIORITY
# （drowsy 排在 fatigue 之前）等效达成——嗜睡门控成立即胜出，不依赖时间窗。
# 本常量保留为接口预留，当前无消费方（v2.9.1 注明，避免"文档承诺 60s 窗口、代码无此机制"的误读）。
SLEEP_MIGRATE_S = 60
# ---- v2.8：疲劳/嗜睡判据（映射表 M5/M8 落地；经验初值，真机校准）----
Z_FATIGUE_BAR = -1.0      # 疲劳：BAR（β/(α+θ)）相对基线 -1σ（警觉降低，β 活动下降）
Z_DROWSY_TAR = +1.5       # 嗜睡：TAR（θ/α）相对基线 +1.5σ（θ 主导；S5 口径：TAR 判"θ 主导"）
Z_DROWSY_DELTA = +1.0     # 嗜睡：δ 相对基线 +1σ（"真嗜睡"佐证；S5 合取口径——TAR 与 δ 须同时满足）
# ---- v2.8：#9 个体基线自适应（漂移补偿；默认关闭，真机联调后按 §六 启用；经验初值）----
ADAPT_ENABLED = False     # 默认关闭：自适应有污染风险（neutral 帧可能含漏判状态），须人工评估启用
ADAPT_ALPHA = 0.001       # EWMA 时间常数 ~1000s（慢漂移补偿；快于 1000s 的变化属状态非漂移）
ADAPT_MAX_DRIFT = 0.5     # 单次更新限幅 ±0.5σ（防 outlier 帧污染基线）
ADAPT_EVAL_S = 60         # 每 60s 评估一次更新
# ---- v2.9.2：校准质量门槛（设计文档 §六 第 5 条"校准失败提示重戴，不静默进入判别"）----
CALIB_ARTIFACT_MAX = 0.30  # 校准段伪迹帧占比上限（>30% 判失败，§六 标准）
WINDOW_EEG_S = 10         # EEG 特征窗（秒）
WINDOW_HRV_S = 60         # HRV 窗（秒）
HIT_RATIO = 0.6           # 慢变状态时间确认：窗内命中率 ≥ 0.6（R5 4/5 思想）
DEBOUNCE_S = 5.0          # 防抖最短持续
CONF_MIN, CONF_MAX = 0.6, 0.95
COOL_DOWN = {             # 反馈冷却（R5 silent 期；焦虑同压力类需冷却，心流为鼓励态不冷却）
    "stress": 60, "anxiety": 60,
    "distracted": 60,                     # v2.8.1：30→60s——频繁分心者 30s 一提示过于打扰
                                          # （mild 级但高频轰炸损害体验；与 fatigue/drowsy 对齐）
    "fatigue": 60, "drowsy": 60,          # v2.8：长时盯盘高危状态同冷却（重复提醒 1 分钟一次）
    "focus": 0, "flow": 0, "relaxed": 0, "neutral": 0,
}
# 优先级仲裁（v2.4 六状态：生理应激优先干预→认知扰动→任务分心→心流→专注→放松；
# 心流=专注+放松协同的最佳态（指南 2.2），是 focus 的语义超集，优先级高于 focus——
# 双状态同时命中时报 flow 信息更精确；反馈仅鼓励不打断）
# v2.8：疲劳/嗜睡插入 flow 之前——长时盯盘高危状态优先干预（S6：疲劳 60s 内转嗜睡改判嗜睡）。
# 嗜睡/疲劳 > 分心：意识水平下降/注意耗竭对金融决策危害高于瞬时注意游离；
# 且嗜睡帧 TAR 高必然伴随 R_tb 偏高（θ 主导），若 drowsy 排在 distracted 后会全程被抢占）
PRIORITY = ["stress", "anxiety", "drowsy", "fatigue", "distracted", "flow", "focus", "relaxed"]

# ---------------------------------------------------------------- 数据结构

@dataclass
class Frame:
    """单帧输入（BrainLink 字段，与官方 SDK / brain_sim 对齐）"""
    attention: float = 50.0
    meditation: float = 50.0
    delta: float = 100.0
    theta: float = 100.0
    alpha: float = 100.0
    beta: float = 100.0
    gamma: float = 50.0
    rr_ms: float | None = None        # 心率间期（ms）
    heart: float | None = None        # 心率直读 bpm（SDK 扩展回调，真机 HR 特征；None=走 RR 换算）
    gyro: tuple = (0.0, 0.0, 0.0)     # 体动（v2.2 still 特征）
    ts: float | None = None           # v2.7 时间戳（秒；None/<=0 时引擎用内部时钟补——mock 帧可不传）

@dataclass
class BaselineStats:
    mu: float = 0.0
    sigma: float = 1.0
    sigma_min: float = 0.1
    log_flag: bool = False            # RMSSD 建议 ln 化（R7）

@dataclass
class Feats:
    """特征窗口输出"""
    R_tb: float = 0.0
    R_ba: float = 0.0
    TAR: float = 0.0          # v2.8 θ/α（嗜睡 TAR 判据；映射表 M5）
    A_rel: float = 0.0
    HR: float = 0.0
    RMSSD_60: float = 0.0
    dRMSSD: float = 0.0
    dHR: float = 0.0
    still: float = 0.0

@dataclass
class Decision:
    state: str = "relaxed"
    conf: float = 0.0
    scores: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)

@dataclass
class Feedback:
    level: str = "silent"             # silent / mild / calm / strong
    action: str = ""
    cooldown_left: float = 0.0

# ---------------------------------------------------------------- 状态引擎

class StateEngine:
    """MVP 状态判别引擎：平滑 → 特征 → z-score → 评分 → 仲裁 → 反馈"""

    def __init__(self, baseline: dict | None = None, weights: dict | None = None,
                 clock: callable | None = None):
        self.smooth = {}                    # EEG 频段/比值平滑值
        self.rr_buf = []                    # RR 环形缓冲（60s，元素 (rr_ms, batch, ts)，v2.7 按 ts 滑动）
        self.eeg_buf = []                   # EEG 特征窗缓冲（10s，v2.7 按 ts 滑动）
        self.gyro_buf = []
        self._gyro_ts = []                  # v2.7 gyro 缓冲时间戳（30s 滑动窗）
        self.baseline = baseline or {}
        # v2.3 补实：校准基线关键特征必填——A_rel 依赖 alpha，缺省会静默降级为 0 掩盖 relaxed 失效
        # v2.8.2 逻辑修正：delta 加入必填——drowsy（嗜睡）门控依赖 z(δ) 佐证，缺省时
        # z.get("delta",0)=0 恒不过 → 嗜睡静默永不触发（比报错更难排查）
        if self.baseline:
            missing = {"R_tb", "R_ba", "alpha", "A_rel", "HR", "RMSSD_60", "still", "delta"} - set(self.baseline)
            if missing:
                raise ValueError(f"基线缺失关键特征: {missing}（校准协议必填，见 §六；"
                                 f"含 delta——嗜睡 δ 佐证判据依赖，不能省）")
        self.W = weights or self._default_weights()
        self._clock = clock or time.time    # 可注入虚拟时钟（mock 演示冷却用，v2.3）
        self.last_decision: Decision | None = None
        self.last_feedback = Feedback()
        self._hit_buf = []                  # 时间确认命中缓冲
        self._last_action_time = {}         # 冷却计时
        self.att_hist, self.med_hist = [], []   # v2.4 派生同步率窗口（原始指数帧）
        self.sync_ratio = 0.0               # 最新同步率（0-1，窗口内同向帧占比）
        self.sync_ok = False                # v2.5 同步率达标（窗口满+有效差分帧足+占比达标）
        self.sync_latched = False           # v2.6 同步率锁存：达标后保持，直到双高跌破才复位
        self.flow_hold = 0                  # v2.5 心流"稳定高值"连续帧计数（指南 2.2）
        self.weak_count = 0                 # v2.6 弱判别连续帧计数（升级提醒用）
        self._last_weak_escalate = float("-inf")  # v2.6 弱判别升级时间戳（-inf=首次可直接升级，
                                                  # 修 bug：初始 0.0 导致需运行 300s 后首次升级才可能触发）
        self._heart_ewma = None                   # v2.6.2 heart(bpm) 轻平滑（扩展回调瞬时值抖动，z 判据易噪）
        self._rr_batch = 0                  # v2.6 RR 回调批次号（RMSSD 组内差分）
        # v2.8 疲劳/嗜睡状态机
        self._theta_ref = None              # θ 校准冻结基线（校准协议 B 段定，防滚动基线吃掉 I_f）
        if isinstance(self.baseline.get("theta_ref"), (int, float)):
            self._theta_ref = float(self.baseline["theta_ref"])   # v2.8 校准协议 A 段冻结值
        self._theta_int = 0.0               # I_f 疲劳积分（等效秒；θ_norm>1 累积、半衰期衰减）
        self._last_theta_ts = None          # 上一帧时间戳（I_f 积分 dt 用；伪迹帧也推进但不积分）
        # 注：S6"疲劳→嗜睡迁移"由 PRIORITY 实现（drowsy 优先级高于 fatigue——
        # 疲劳触发后若 TAR/δ 合取成立，仲裁直接选 drowsy，无需显式状态机计时）
        # v2.8 #9 基线自适应
        self._last_adapt_ts = float("-inf") # 上次自适应评估时间戳
        self.log = {"decisions": [], "interventions": [], "impulsive": 0, "total": 0}

    # -- 预处理 ---------------------------------------------------------
    def _ewma(self, key, x):
        if key not in self.smooth:
            self.smooth[key] = x
        else:
            self.smooth[key] = EWMA_ALPHA * x + (1 - EWMA_ALPHA) * self.smooth[key]
        return self.smooth[key]

    def _artifact(self, frame) -> bool:
        """伪迹：单频段对平滑值偏差 >8× 判伪迹（v2.1：接触不良/眨眼幅值突变）；
        R_tb 比值门限独立放宽 15×（v2.8 修正）——状态切换（疲劳/分心）R_tb 可达 5-10 倍，
        原 8× 门限把真实状态切换当伪迹丢弃（实测疲劳帧 R_tb=5 vs 平滑 0.55 → 8.1 倍 →
        早退导致 I_f 永不积分；单频段 8× 已覆盖硬件伪迹，比值检查只防积分污染）

        v3.1：可选 β+γ 肌电门（ARTIFACT_EMG_GATE，默认关）。8× 是突变检测，持续肌电会饱和
        ——首帧爆发触发后平滑基线被抬上去，后续帧不再超 8×，朗读/说话段被当有效 EEG。
        """
        if self._emg_artifact(frame):
            return True
        for key in ("delta", "theta", "alpha", "beta", "gamma"):
            if key in self.smooth and self.smooth[key] > 0:
                if abs(frame.__dict__[key] / self.smooth[key] - 1.0) > ARTIFACT_RATIO:
                    return True
        if self.smooth.get("R_tb", 0) > 0:
            tb = self._safe_ratio(frame.theta, frame.beta)
            if abs(tb / self.smooth["R_tb"] - 1.0) > ARTIFACT_RATIO_RTB:
                return True
        return False

    def _emg_artifact(self, frame) -> bool:
        """β+γ 同升且 θ 不动 → 肌电伪迹（对照 B 段参照，非平滑值——平滑值会被肌电抬走，
        用它当基线就永远检不出来，这正是本门要解决的问题）。"""
        if not ARTIFACT_EMG_GATE:
            return False
        br = self.baseline.get("beta_ref")
        gr = self.baseline.get("gamma_ref")
        tr = self.baseline.get("theta_ref")
        if not br or not gr or not tr:
            return False
        return (frame.beta > ARTIFACT_EMG_RATIO * br
                and frame.gamma > ARTIFACT_EMG_RATIO * gr
                and abs(frame.theta - tr) < ARTIFACT_EMG_THETA_TOL * tr)

    @staticmethod
    def _safe_ratio(a, b):
        return float("inf") if b <= 0 else a / b

    @staticmethod
    def _artifact_ratio_static(frames: list, refs: dict | None = None) -> float:
        """校准段伪迹率（与 _artifact 同判据，按序回放维护平滑值）。

        用于 calibration_report：伪迹率 > CALIB_ARTIFACT_MAX 判校准失败（§六 第 5 条）。
        不复用实例方法是因为校准发生在引擎创建之前，且不应对 calibrate 输入产生状态副作用。
        refs：启用 β+γ 肌电门时需传入 beta_ref/gamma_ref/theta_ref（缺省则只跑 8×/15× 门）。
        """
        smooth: dict[str, float] = {}
        art = 0
        n = 0
        g_br = (refs or {}).get("beta_ref")
        g_gr = (refs or {}).get("gamma_ref")
        g_tr = (refs or {}).get("theta_ref")
        for _, fr in frames:
            n += 1
            hit = False
            # β+γ 肌电门（v3.1，仅当引擎侧启用且参照齐全时生效）
            if ARTIFACT_EMG_GATE and g_br and g_gr and g_tr:
                if (getattr(fr, "beta", 0) > ARTIFACT_EMG_RATIO * g_br
                        and getattr(fr, "gamma", 0) > ARTIFACT_EMG_RATIO * g_gr
                        and abs(getattr(fr, "theta", 0) - g_tr) < ARTIFACT_EMG_THETA_TOL * g_tr):
                    hit = True
            for key in ("delta", "theta", "alpha", "beta", "gamma"):
                prev = smooth.get(key, 0.0)
                v = getattr(fr, key, None)
                if v is None or not math.isfinite(v) or prev <= 0:
                    continue
                if abs(v / prev - 1.0) > ARTIFACT_RATIO:
                    hit = True
                    break
            if not hit:
                prev_rtb = smooth.get("R_tb", 0.0)
                tb = StateEngine._safe_ratio(fr.theta, fr.beta)
                if prev_rtb > 0 and math.isfinite(tb) and abs(tb / prev_rtb - 1.0) > ARTIFACT_RATIO_RTB:
                    hit = True
            if hit:
                art += 1
            # 伪迹帧不更新平滑（与引擎一致）
            if hit:
                continue
            for key in ("delta", "theta", "alpha", "beta", "gamma"):
                v = getattr(fr, key, 0.0)
                if not math.isfinite(v):
                    continue
                prev = smooth.get(key)
                smooth[key] = v if prev is None else EWMA_ALPHA * v + (1 - EWMA_ALPHA) * prev
            tb = StateEngine._safe_ratio(fr.theta, fr.beta)
            if math.isfinite(tb):
                prev = smooth.get("R_tb")
                smooth["R_tb"] = tb if prev is None else EWMA_ALPHA * tb + (1 - EWMA_ALPHA) * prev
        return art / n if n else 1.0

    def _update_sync(self, frame) -> bool:
        """v2.4/2.5 心流派生同步率（指南 2.2 同步率=attention/meditation 趋势一致性）：
        窗口内双指数相邻帧差分同号帧占比。官方 README 无独立同步率字段，
        此为本实现派生口径——答辩需注明"派生指标，非官方字段"。
        返回是否达标：窗口未满（<SYNC_WIN）或有效差分帧不足（<MIN_SYNC_FRAMES）→ False
        （心流需足够趋势证据，防单帧同向即 1.0 的假阳性）。"""
        self.att_hist.append(frame.attention)
        self.med_hist.append(frame.meditation)
        if len(self.att_hist) > SYNC_WIN:
            self.att_hist.pop(0)
            self.med_hist.pop(0)
        same = total = 0
        for i in range(1, len(self.att_hist)):
            da = self.att_hist[i] - self.att_hist[i - 1]
            dm = self.med_hist[i] - self.med_hist[i - 1]
            if da != 0 and dm != 0:
                total += 1
                if (da > 0) == (dm > 0):
                    same += 1
        self.sync_ratio = same / total if total > 0 else 0.0
        return len(self.att_hist) >= SYNC_WIN and total >= MIN_SYNC_FRAMES \
            and self.sync_ratio >= SYNC_MIN

    def _index_high(self, frame) -> bool:
        """v2.6 心流"高值"双判据：绝对常模（≥60，指南 2.2 高值区）OR 相对个体基线显著高
        （z>+1.5）——与焦虑 z 判据对称：个体指数基线低的人绝对 60 永远达不到，
        绝对阈值会漏掉其"相对自身的最佳状态"（个体校准意义，指南 4.1）"""
        if frame.attention >= FLOW_ATT_MIN and frame.meditation >= FLOW_MED_MIN:
            return True
        if "att" in self.baseline and "med" in self.baseline:
            za = self._z(frame.attention, self.baseline["att"])
            zm = self._z(frame.meditation, self.baseline["med"])
            if za > FLOW_Z and zm > FLOW_Z:
                return True
        return False

    # -- 特征 -----------------------------------------------------------
    @staticmethod
    def _rmssd(rr_list):
        """v2.6：RR 缓冲元素为 (rr_ms, batch[, ts])。RMSSD 只在同 batch 内做相邻差分——
        真机 onRR 一次回调的 3 个间期是连续心跳，跨回调（不同 batch）的间期不连续，
        差分会污染 RMSSD；batch=None（模拟 Frame.rr_ms 单值路径）保留跨元素差分（演示用）。
        组内差分不足 8 对返回 nan（窗口未满语义）。"""
        if len(rr_list) < 8:
            return float("nan")            # v2.6.1 修正：窗口未满返回 nan（原 0.0 被当有效值，
                                           # z=(0-μ)/σ 极低 → 1 帧即 stress 假阳性）
        diffs = []
        for i in range(len(rr_list) - 1):
            r0, r1 = rr_list[i], rr_list[i + 1]
            if r0[1] is not None and r1[1] is not None and r0[1] != r1[1]:
                continue                     # 跨回调批次：断开差分
            diffs.append(r1[0] - r0[0])
        if not diffs:
            return float("nan")
        return math.sqrt(sum(d * d for d in diffs) / len(diffs))

    def _push_window(self, frame, ts: float):
        """维护 10s EEG / 60s RR / 30s GYRO 缓冲（v2.7 改为按时间戳滑动截断——
        原按元素计数截断：模拟器每帧 1 间期近似 60s，但真机 onRR 一次 3 间期
        60 元素只有 ~24–27s（#7 缺口），RMSSD 窗偏短压力判别偏敏感）"""
        self.eeg_buf.append(frame)
        while self.eeg_buf and ts - (self.eeg_buf[0].ts or ts) > WINDOW_EEG_S:
            self.eeg_buf.pop(0)
        if frame.rr_ms is not None:
            self.rr_buf.append((frame.rr_ms, None, ts))   # 模拟单值路径：batch=None 跨元素差分
            while self.rr_buf and ts - self.rr_buf[0][2] > WINDOW_HRV_S:
                self.rr_buf.pop(0)
        self.gyro_buf.append(frame.gyro)
        self._gyro_ts.append(ts)
        while self.gyro_buf and ts - self._gyro_ts[0] > 30:
            self.gyro_buf.pop(0)
            self._gyro_ts.pop(0)

    def push_rr(self, rr_ms: float, batch: int | None = None, ts: float | None = None):
        """真机路径：RR 回调（onRR 一次 3 间期）逐个灌入 HRV 缓冲，与 EEG 帧率解耦。
        batch=同一 onRR 回调的批次号（3 个连续间期同批）——RMSSD 组内差分依据
        （v2.6 修正：跨回调间期不连续，混差分会污染 RMSSD）。模拟器路径走 Frame.rr_ms。
        v2.7：ts 时间戳滑动窗（ts=None 用内部时钟）；缓冲元素 (rr, batch, ts)"""
        if rr_ms and RR_MIN_MS <= rr_ms <= RR_MAX_MS:
            ts = ts if ts is not None else self._clock()
            self.rr_buf.append((rr_ms, batch, ts))
            while self.rr_buf and ts - self.rr_buf[0][2] > WINDOW_HRV_S:
                self.rr_buf.pop(0)

    def _extract(self) -> Feats:
        f = Feats()
        n = len(self.eeg_buf)
        if n == 0:
            return f
        th = sum(x.theta for x in self.eeg_buf) / n
        al = sum(x.alpha for x in self.eeg_buf) / n
        be = sum(x.beta for x in self.eeg_buf) / n
        f.R_tb = self._safe_ratio(th, be)
        f.R_ba = self._safe_ratio(be, th + al)
        f.TAR = self._safe_ratio(th, al)     # v2.8 嗜睡 TAR（θ/α；Muse 公式族，R8 口径）
        # A_rel：相对任务前睁眼基线（v2.2：禁闭眼基线作分母）
        base_alpha = self.baseline.get("alpha", BaselineStats()).mu
        f.A_rel = (al - base_alpha) / base_alpha if base_alpha else 0.0
        # HR 分工（SDK 兼容缺口 3）：heart(bpm) 直读优先作 HR 特征，RR 只供 RMSSD
        # v2.6.2：heart 轻 EWMA 平滑——扩展回调瞬时值抖动大，直接进 z-score 易误判 HR 显著偏离
        last_heart = self.eeg_buf[-1].heart if self.eeg_buf and self.eeg_buf[-1].heart is not None else None
        if last_heart is not None and math.isfinite(last_heart):
            if self._heart_ewma is None:
                self._heart_ewma = last_heart
            else:
                self._heart_ewma = EWMA_ALPHA * last_heart + (1 - EWMA_ALPHA) * self._heart_ewma
            f.HR = self._heart_ewma
        elif self.rr_buf:
            mean_rr = sum(x[0] for x in self.rr_buf) / len(self.rr_buf)
            f.HR = 60000.0 / mean_rr if mean_rr else 0.0
        else:
            f.HR = float("nan")                  # 窗口未满：非有限值，门控跳过（防假饱和）
        if self.rr_buf:
            f.RMSSD_60 = self._rmssd(self.rr_buf)
        else:
            f.RMSSD_60 = float("nan")            # 窗口未满：非有限值，门控跳过（防假饱和）
        if self.gyro_buf:
            f.still = sum(abs(g[0]) + abs(g[1]) + abs(g[2]) for g in self.gyro_buf) / len(self.gyro_buf)
        else:
            f.still = float("nan")
        return f

    # -- z-score --------------------------------------------------------
    @staticmethod
    def _z(x, stat: BaselineStats):
        v = math.log(x) if stat.log_flag and x > 0 else x
        sigma = max(stat.sigma, stat.sigma_min)     # σ_min 下限保护（v2.1）
        return (v - stat.mu) / sigma if sigma else 0.0

    # -- 评分 + 门控（v2.3：专注排除法；分心 task_active；压力 still 平静门控）-------------
    def _scores(self, feats: Feats, ctx: dict | None = None, frame: Frame | None = None) -> (dict, dict):
        W, z = self.W, {}
        for name in ("R_tb", "R_ba", "TAR", "A_rel", "HR", "RMSSD_60", "still"):
            stat = self.baseline.get(name)
            if stat:
                v = getattr(feats, name)
                z[name] = self._z(v, stat) if math.isfinite(v) else float("nan")
        # v2.8：δ 原始频段 z（嗜睡"真嗜睡"佐证；frame=None 时跳过——_scores 无官方帧调用）
        if frame is not None:
            stat = self.baseline.get("delta")
            if stat:
                z["delta"] = self._z(frame.delta, stat) if math.isfinite(frame.delta) else float("nan")
        # v2.5 焦虑 z 判据：官方指数也做个体 z（个体差异大，绝对常模 40 会漏报"相对自身显著下滑"）
        za = zm = float("nan")
        if frame is not None and "att" in self.baseline and "med" in self.baseline:
            za = self._z(frame.attention, self.baseline["att"])
            zm = self._z(frame.meditation, self.baseline["med"])
        # v2.5 心流"稳定高值"：双高持续帧计数（在 process_frame 更新），门控只看计数与同步率
        gates = {
            # 专注排除法（v2.3 S1）：v2.2 的 z(HR)<+1.5 与弃用 β 上限同构——认知努力也升
            # 心率，高强度任务仍会误伤专注。改为"排除应激"：HRV 未显著降（无应激佐证）
            # + 体动平静 → 判专注；兴奋/压力由各自门控 + 仲裁优先级兜底。
            # 窗口未满（nan）时不设限，防误杀专注
            "focus":      z.get("R_tb", 0) < Z_CONC and (
                not math.isfinite(z.get("RMSSD_60", 0)) or z.get("RMSSD_60", 0) > Z_RMSSD_NO_STRESS) and (
                not math.isfinite(z.get("still", 0)) or z.get("still", 0) < Z_STILL_CAP),
            # v2.3 补实：分心必须任务态（ctx.task_active）——非任务态 R_tb 高（闭眼/发呆 θ 升）
            # 是真实场景，不限制会误报分心；ctx 由演示流程/上层注入（task_active=True）
            "distracted": z.get("R_tb", 0) > Z_DIST and bool((ctx or {}).get("task_active")),
            "relaxed":    z.get("A_rel", 0) > Z_RELAX_A,
            # v2.6.2 修复：still 无 nan 保护——gyro 缓冲 30 帧填满前 still=nan，原式
            # nan<+1.0 恒 False → 启动前 30–45s 压力静默失效；真机 gyro 缺失时压力永久失效。
            # 与专注门控一致：体动数据缺失时不作"平静"约束（RMSSD 主判据仍生效）
            "stress":     z.get("RMSSD_60", 0) < Z_STRESS and (
                not math.isfinite(z.get("still", 0)) or z.get("still", 0) < Z_STILL_CAP),
            # v2.4/2.5 官方指数状态（指南 2.1/2.2；与功率比 z 特征正交）：
            # 焦虑=绝对常模双低（厂商标准） OR 相对个体基线显著下滑（z<-1.5，防常模漏报）
            # 心流=双高稳定（≥FLOW_HOLD_FRAMES 帧，指南"稳定于高值区间"）+ 派生同步率达标
            #   + z(R_tb)<Z_DIST 功率比交叉（官方指数与 θ/β 可能冲突——指南 2.1 明示专注度与
            #   β 非简单正相关；功率比显示明显分心时不判 flow，冲突由优先级兜底）
            "anxiety":    (frame is not None and frame.attention < ANX_ATT_MAX
                           and frame.meditation < ANX_MED_MAX) or (
                math.isfinite(za) and math.isfinite(zm) and za < ANX_Z and zm < ANX_Z),
            "flow":       frame is not None and self._index_high(frame)
                          and self.flow_hold >= FLOW_HOLD_FRAMES
                          and self.sync_latched
                          and (not math.isfinite(z.get("R_tb", 0)) or z.get("R_tb", 0) < Z_DIST),
            # v2.8 疲劳/嗜睡（映射表 M5/M8 落地；单通道额区判据，真机校准）：
            # 疲劳=BAR(β/(α+θ))显著降（警觉降低）且 I_f 疲劳积分超阈值（θ 主导持续累积）
            #   并排除应激（RMSSD 未显著降——疲劳≠压力，S3 口径）
            # 嗜睡=TAR(θ/α)显著高（θ 主导）且 δ 显著高（真嗜睡佐证；S5 合取口径）
            "fatigue":    z.get("R_ba", 0) < Z_FATIGUE_BAR and self._theta_int >= IF_HOLD_S
                          and (not math.isfinite(z.get("RMSSD_60", 0))
                               or z.get("RMSSD_60", 0) > Z_STRESS),
            "drowsy":     z.get("TAR", 0) > Z_DROWSY_TAR and z.get("delta", 0) > Z_DROWSY_DELTA,
        }
        scores = {
            "focus":      W["focus"]["R_tb"] * z.get("R_tb", 0) + W["focus"]["R_ba"] * z.get("R_ba", 0),
            # v2.6.2 修复：评分漏 A_rel 权重项——v2.3 权重表已补实（R_tb↑ + A_rel↑ + 体动↑，
            # 文档 §判别层 L130 明示 +0.3·z(A_rel)），评分公式未同步，真机睁眼任务 α 上升时
            # 分心置信度被系统性低估
            "distracted": W["distracted"]["R_tb"] * z.get("R_tb", 0)
                          + W["distracted"]["A_rel"] * (z.get("A_rel", 0)
                                                        if math.isfinite(z.get("A_rel", 0)) else 0)
                          + W["distracted"]["still"] * (z.get("still", 0)
                                                        if math.isfinite(z.get("still", 0)) else 0),
            # v2.6.2 修复：辅助特征（A_rel/still/RMSSD_60）缺失（nan）时该项置 0 而非污染
            # 整个 score——原实现 nan 项使 score=nan → 门控过也判不出（分心/放松漏报），
            # 与佐证缺失放行原则统一（主判据 R_tb/A_rel 仍生效）
            # v2.7.1 修复：补 relaxed still 权重项——权重表（§四）明示 +0.5·z(−still)，
            # 评分公式漏掉（与 v2.6.2 外部修的 distracted A_rel 项同类）；体动活跃时
            # 放松置信度应扣减（放松=α 高且体动平静，文档口径）
            "relaxed":    W["relaxed"]["A_rel"] * z.get("A_rel", 0)
                          + W["relaxed"]["RMSSD_60"] * (z.get("RMSSD_60", 0)
                                                        if math.isfinite(z.get("RMSSD_60", 0)) else 0)
                          + W["relaxed"]["still"] * (z.get("still", 0)
                                                     if math.isfinite(z.get("still", 0)) else 0),
            "stress":     W["stress"]["RMSSD_60"] * z.get("RMSSD_60", 0) + W["stress"]["HR"] * z.get("HR", 0),
            # v2.4：焦虑=离低值区边界的距离（越低越确定）；心流=双高余量+同步率余量
            # v2.6.1 修复：z 路径下 (ANX_ATT_MAX-att) 变负 → 双绝对值分母 conf 恒 0.6 →
            # 弱判别永不干预。改为 max(绝对余量, z 余量)——取更确定的路径（两条都 >0）
            # v2.6.2 健壮性：frame=None（_scores 无官方指数帧调用）时焦虑/心流评分置 -inf，
            # 避免 AttributeError（门控已前置 None 检查，评分同样需防御）
            "anxiety":    max(
                W["anxiety"]["att"] * (ANX_ATT_MAX - frame.attention)
                + W["anxiety"]["med"] * (ANX_MED_MAX - frame.meditation)
                if frame is not None else float("-inf"),
                W["anxiety"]["att"] * (-ANX_Z - za) + W["anxiety"]["med"] * (-ANX_Z - zm)
                if math.isfinite(za) and math.isfinite(zm) else float("-inf")),
            "flow":       (W["flow"]["att"] * (frame.attention - FLOW_ATT_MIN)
                          + W["flow"]["med"] * (frame.meditation - FLOW_MED_MIN)
                          + W["flow"]["sync"] * (self.sync_ratio - SYNC_MIN))
                          if frame is not None else float("-inf"),
            # v2.8 疲劳/嗜睡评分（权重初值见 _default_weights；drowsy 高于 fatigue——
            # S6：疲劳持续 60s 内若转嗜睡条件则改判嗜睡，嗜睡是恶化态）
            "fatigue":    W["fatigue"]["R_ba"] * z.get("R_ba", 0)
                          + W["fatigue"]["I_f"] * min(self._theta_int / IF_HOLD_S, 1.0),
            "drowsy":     W["drowsy"]["TAR"] * z.get("TAR", 0)
                          + W["drowsy"]["delta"] * (z.get("delta", 0)
                                                    if math.isfinite(z.get("delta", 0)) else 0),
        }
        for s in scores:
            # 门控不过 或 关键特征无效（nan）→ 不参与竞争
            if not gates[s] or not math.isfinite(scores[s]):
                scores[s] = float("-inf")
        return scores, gates

    # -- 仲裁 -----------------------------------------------------------
    def _arbitrate(self, scores, gates) -> (str, float):
        # 未过门控按 0 计（v2.2：防 -inf 假饱和）
        finite = {s: v for s, v in scores.items() if v != float("-inf")}
        if not finite:
            # v2.6 修正：无状态命中返回 neutral（数据不足/窗口未满/无判别）而非 relaxed——
            # 原实现把"无判别"伪装成"放松"，污染理智度统计（calmness 用决策日志）与干预语义
            return "neutral", CONF_MIN
        # v2.3 补实：同窗多状态命中按 PRIORITY（压力>分心>专注>放松）——
        # 纯分数仲裁会让分数略低的高优先级状态被掩盖（如压力被分心盖过）；
        # 语义：门控=资格、优先级=谁赢、分数仅用于置信度。
        #
        # v2.9.2 复核结论（曾尝试改为"能提醒者优先"，已回退）：该改法不成立——各状态评分
        # 量纲不同（stress/relaxed/focus 为 z 线性组合，量级 ~1–3；anxiety 为官方指数余量，
        # 量级 ~10–20；flow 为余量+同步率），跨状态比 conf 等于比不同单位，实测会把
        # "RMSSD z=−2 的强应激" 判给 "att/med=30 的焦虑"，破坏"生理应激优先干预"的临床语义。
        # 因此维持"仅按 PRIORITY"的仲裁：这是**刻意取舍**，不是遗漏。
        # 代价（须在答辩说明）：边缘化的高优先级状态会压掉低优先级的强判别（两个都不报，
        # 或仅走弱判别累积升级）。若日后要做强度仲裁，须先把 8 态评分归一到同一量纲
        # （如统一用"门控阈值外的 σ 余量"），属真机拟合阶段的工作。
        best = next((s for s in PRIORITY if s in finite), max(finite, key=finite.get))
        # v2.8 资格型状态特判：fatigue/drowsy 是"门控达标即成立"的长时累积/合取强条件状态
        # （I_f 积分、TAR+δ 合取），conf 反映自身门控余量而非跨状态分差——否则与同源
        # distracted（R_tb 高、z 无界高分）并存时被系统性压低到弱判别（I_f 达标是强证据，
        # 却被标"弱判别仅记录"）；wsum=该状态权重绝对值之和（满分=1.0 余量）
        if best in ("fatigue", "drowsy"):
            wsum = sum(abs(v) for v in self.W[best].values())
            if wsum > 0:
                conf = CONF_MIN + 0.35 * (finite[best] / wsum)
                return best, max(CONF_MIN, min(CONF_MAX, conf))
        # 时间确认已由 process_frame._confirm 实装（v2.7：#8 落地，命中率+HIT_RATIO/防抖）
        second = sorted((v for s, v in finite.items() if s != best), reverse=True)
        second_v = second[0] if second else 0.0
        # v2.2 置信度：双绝对值分母 + clamp
        conf = CONF_MIN + 0.35 * (finite[best] - second_v) / (abs(finite[best]) + abs(second_v) + 1e-9)
        return best, max(CONF_MIN, min(CONF_MAX, conf))

    # -- 反馈 -----------------------------------------------------------
    def feedback_policy(self, state: str, conf: float = CONF_MAX, now: float | None = None) -> Feedback:
        # v2.7：now 复用 process_frame 的时间戳（mock 时钟"每次调用+1s"语义下，
        # 若冷却另调一次 clock → 每帧推进 2s → 时间确认窗口缩半，状态被偶发抖动拉垮）
        # v2.3（S4）：conf∈[0.6,0.7) 弱判别——仅记录状态、不触发提醒
        if conf < WEAK_CONF:
            if state == "neutral":
                # v2.6.1：neutral 不是判别结果（窗口未满/无判别），走"等待填充"文案，
                # 不参与弱判别升级——升级只针对真实判别状态（防"数据不足"被误当长期弱判别）
                return Feedback(level="silent", action="数据不足/无判别，等待窗口填充")
            # v2.6 弱判别累积：持续 ≥WEAK_ESCALATE_FRAMES 帧升级一次低强度提醒——
            # 原实现长期弱判别（如 20 分钟 conf 0.6 的 stress）永远零干预，用户收不到任何提示
            self.weak_count += 1
            if self.weak_count >= WEAK_ESCALATE_FRAMES:
                now = now if now is not None else self._clock()   # v2.7：复用帧时间戳（同主路径）
                if (now - self._last_weak_escalate) >= WEAK_ESCALATE_CD:
                    self._last_weak_escalate = now
                    self.weak_count = 0
                    return Feedback(level="mild",
                                    action=f"状态趋近 {state}（置信度低，持续观察中），建议短暂关注")
                self.weak_count = 0   # 冷却期内重置，冷却后再累积（防频繁轰炸）
            return Feedback(level="silent", action="弱判别，仅记录状态")
        self.weak_count = 0
        now = now if now is not None else self._clock()   # 可注入虚拟时钟（v2.3：真实 time.time 在毫秒级 mock 下冷却永不解除）
        last = self._last_action_time.get(state, 0)
        cd = COOL_DOWN.get(state, 0)
        # v2.6.1：last==0（从未触发）放行——原 (now-0)<cd 在虚拟时钟被 neutral 短路不推进时
        # 首次干预就被"冷却中"拦截（now≈1 < 60）；真实场景 last 为真实时间戳不受影响
        if cd and last and (now - last) < cd:
            return Feedback(level="silent", action="冷却中", cooldown_left=cd - (now - last))
        self._last_action_time[state] = now
        table = {
            "focus":      Feedback("silent", "维持当前节奏"),
            "flow":       Feedback("silent", "心流状态，保持当前节奏"),
            "relaxed":    Feedback("silent", "无干预"),
            "neutral":    Feedback("silent", "数据不足/无判别，等待窗口填充"),
            "distracted": Feedback("mild",  "回到任务，注意关键信息"),
            "anxiety":    Feedback("calm",  "情绪扰动高：暂缓交易决策，先做 3 次深呼吸（Laborde 2022）"),
            "stress":     Feedback("calm",  "3 次深呼吸（Laborde 2022）"),
            # v2.8：疲劳=长时间 θ 主导累积（映射表 M8）；建议短暂休息（含眼部放松——
            # 盯盘疲劳首现于眼动/注意维持，非仅 EEG 信号，话术不夸大 EEG 证据）
            "fatigue":    Feedback("mild",  "疲劳累积：建议短暂休息（含眼部放松）后继续决策"),
            "drowsy":     Feedback("calm",  "嗜睡迹象：建议起身活动或暂停交易决策（清醒后再回）"),
        }
        return table.get(state, Feedback("silent", ""))

    # -- 理智度 ---------------------------------------------------------
    def rationality_log(self, is_impulsive: bool, decision: Decision):
        self.log["total"] += 1
        if is_impulsive:
            self.log["impulsive"] += 1
        self.log["decisions"].append(decision.state)

    @property
    def calmness(self):
        return 1.0 - (self.log["impulsive"] / self.log["total"]) if self.log["total"] else 1.0

    def _confirm(self, state: str, ts: float) -> str:
        """v2.7 时间确认（#8 缺口落地）：候选状态在近 DEBOUNCE_S 窗口内命中率 ≥HIT_RATIO
        才确认输出——单帧抖动不再瞬时翻转状态（原"单次即判"，偶发伪帧/瞬时特征波动
        会触发错误反馈，金融决策场景代价高）。窗口未满（<2 帧，启动期）直接确认；
        未达占比输出 neutral（数据不足/未稳定）。neutral 本身不确认（无判别）"""
        if state == "neutral":
            return state
        self._hit_buf.append((state, ts))
        while self._hit_buf and ts - self._hit_buf[0][1] > DEBOUNCE_S:
            self._hit_buf.pop(0)
        if len(self._hit_buf) < 2:
            return state
        hits = sum(1 for s, _ in self._hit_buf if s == state)
        return state if hits / len(self._hit_buf) >= HIT_RATIO else "neutral"

    # -- 主入口 ---------------------------------------------------------
    def process_frame(self, frame: Frame, ctx: dict | None = None) -> (Decision, Feedback):
        # v2.7 时间戳：帧带 ts 用之（真机），否则内部时钟补（mock 每帧 +1s）；
        # 回写 frame.ts——缓冲内帧必须携带真实时间戳，窗口滑动截断依赖它
        # （原不回写：frame.ts=None → 窗口判断 ts-(None or ts)=0 永不截断 → 缓冲无限累积，
        # 特征恒为预热均值 → 状态切换后门控永不触发）
        ts = frame.ts if frame.ts is not None and frame.ts > 0 else self._clock()
        if frame.ts is None or frame.ts <= 0:
            frame.ts = ts
        # 1 平滑（伪迹帧不更新平滑，v2.1）
        if self._artifact(frame):
            # v2.8.2 逻辑修正：伪迹帧推进疲劳积分时间戳但不积分——
            # 否则恢复后第一帧 dt 把伪迹期间（可能数十秒）的 θ 主导时间一次性计入 I_f（跳增）
            if self._theta_ref:
                self._last_theta_ts = ts
            # v2.6.2 语义对齐：无判别返回 neutral 而非 relaxed（首帧即伪迹时，
            # "数据不足/无判别"如实呈现，不伪装成放松；已有判别时保持上帧状态）
            return self.last_decision or Decision("neutral", CONF_MIN), self.last_feedback
        for key in ("delta", "theta", "alpha", "beta", "gamma"):
            self._ewma(key, frame.__dict__[key])
        self._push_window(frame, ts)
        # 2 特征 → 3 z-score → 4 评分 → 5 仲裁（ctx: task_active 等任务态注入，v2.3）
        feats = self._extract()
        self._ewma("R_tb", feats.R_tb)   # R_tb 也进平滑（伪迹检测用）
        # v2.9（10-23 代码审查 P1-3）：疲劳积分改"纯时长"语义——原公式
        #   I_f += (θ_norm−IF_THETA_NORM)·dt 再乘 exp(−dt/τ) 衰减，
        #   稳态上限 I_f∞=(θ_norm−1.2)×300，要 ≥IF_HOLD_S(300) 须 θ_norm≥2.2（θ 超基线 2.2 倍），
        #   而现实疲劳 θ 抬升仅 1.2~1.8 倍 → I_f 稳态 30~180s，疲劳永不触发（测试用 θ_norm=2.5/4.0 掩盖）。
        # v2.9.1 定为"幅度加权 + 可恢复"（兼顾可达性与物理合理性）：
        #   rate = (θ_norm−1.2)/IF_REF_EXCESS 截断到 [0, IF_RATE_MAX]，I_f += rate·dt（等效秒）；
        #   θ 回落至阈下时 I_f 按半衰期 IF_RECOVER_HALF_S 衰减——疲劳是"暴露累积+恢复"，
        #   不能单调锁存（v2.9 纯时长版实测 θ 回落 1200s 后 I_f 仍 399，永久锁存会误报后续所有疲劳）。
        #   单位统一：I_f=300 等价"θ 超阈 0.3 倍持续 300s"，与 IF_HOLD_S 语义一致。
        if self._theta_ref and self._theta_ref > 0:
            theta_norm = frame.theta / self._theta_ref
            dt = ts - (self._last_theta_ts if self._last_theta_ts is not None else ts)
            if dt > IF_MAX_DT_S:                  # 断帧保护（v2.9.1）：见常量注释
                dt = IF_MAX_DT_S
            if dt > 0:
                rate = (theta_norm - IF_THETA_NORM) / IF_REF_EXCESS
                if rate > 0:
                    self._theta_int += min(rate, IF_RATE_MAX) * dt
                elif self._theta_int > 0:
                    self._theta_int *= math.exp(-math.log(2.0) * dt / IF_RECOVER_HALF_S)
                    if self._theta_int < 1e-6:
                        self._theta_int = 0.0      # 数值归零（防长尾浮点残留）
            self._last_theta_ts = ts
        # v2.8：#9 个体基线自适应评估（默认关闭；启用时仅"无任何状态命中"帧更新，防污染）
        self.sync_ok = self._update_sync(frame)   # v2.4/2.5：派生同步率先更新（心流门控依赖）
        # v2.5/2.6 心流"稳定高值"计数：高值（绝对或 z）连续帧 +1，中断归 0
        self.flow_hold = (self.flow_hold + 1) if self._index_high(frame) else 0
        # v2.6 同步率锁存：达标后保持，直到双高跌破才复位——修复"心流稳定期掉出"漏洞：
        # 指南 2.2"稳定于高值区间"正是无趋势期，但同步率按趋势定义，稳定时窗口内差分
        # 归零 → sync_ok 掉 False → flow 被误杀（演示 t=65 掉回 focus 即此漏洞）
        if self.sync_ok:
            self.sync_latched = True
        elif self.flow_hold == 0:
            self.sync_latched = False
        scores, gates = self._scores(feats, ctx, frame)
        self._maybe_adapt(feats, gates, ts)   # v2.8 #9：gates 全 False（无状态命中）才更新基线
        state, conf = self._arbitrate(scores, gates)
        state = self._confirm(state, ts)   # v2.7 时间确认：未达窗口占比 → neutral
        if state == "neutral":
            conf = CONF_MIN                # 未确认状态按最低置信呈现
        dec = Decision(state=state, conf=conf, scores=scores, gates=gates)
        self.last_decision = dec
        fb = self.feedback_policy(state, conf, now=ts)   # v2.7：冷却复用帧时间戳
        self.last_feedback = fb
        return dec, fb

    # -- 权重/基线 ------------------------------------------------------
    @staticmethod
    def _default_weights() -> dict:
        # v2.3 §四 初值权重矩阵（方向=文献，量级=初值，真机 Logistic 拟合路径）。
        # 完整 8 状态矩阵（增强版第二阶段启用）：
        #   专注{R_tb:-, R_ba:+0.5}   分心{R_tb:+, A_rel:+0.3, still:+0.5}
        #   放松{A_rel:+, RMSSD_60:+0.7, still:-0.5}   压力{RMSSD_60:-, HR:+0.7, still:门控}
        #   恐慌{RMSSD_30速率, HR速率}   过度兴奋{HR:+}
        #   嗜睡{TAR:+, BAR:-, δ:+}   疲劳{BAR:门控, I_f:积分}
        return {
            "focus":      {"R_tb": -1.0, "R_ba": +0.5},                  # R_tb↓ + R_ba↑（Pope 指数）
            "distracted": {"R_tb": +1.0, "A_rel": +0.3, "still": +0.5},  # v2.3 表：R_tb↑ + A_rel↑ + 体动↑
            "relaxed":    {"A_rel": +1.0, "RMSSD_60": +0.7, "still": -0.5},  # v2.3 表：体动降佐证放松
            "stress":     {"RMSSD_60": -1.0, "HR": +0.7},                # still 走门控不作权重
            # v2.4：官方指数状态——焦虑=双低距离（绝对判据，方向=距边界越远越确定）
            "anxiety":    {"att": +1.0, "med": +1.0},
            # v2.4：心流=双高余量 + 同步率余量（同步率权重高于指数余量：趋势一致性是心流关键）
            "flow":       {"att": +1.0, "med": +1.0, "sync": +2.0},
            # v2.8：疲劳=BAR 下降 + I_f 积分占比（评分随积分饱和封顶，防无限增长刷分）
            "fatigue":    {"R_ba": -1.0, "I_f": +1.0},
            # v2.8：嗜睡=TAR 上升 + δ 佐证（权重低于 TAR——δ 是佐证不是主判据）
            "drowsy":     {"TAR": +1.0, "delta": +0.5},
        }

    @classmethod
    def calibrate_from_mock(cls, frames: list[Frame]) -> dict:
        """占位：MVP mock 返回默认基线（真机版按 §六 协议：A 闭眼 / B 专注 / C 放松 分段估算 μ/σ）"""
        return {
            "R_tb": BaselineStats(mu=1.0, sigma=0.3, sigma_min=0.1),
            "R_ba": BaselineStats(mu=0.5, sigma=0.2, sigma_min=0.05),
            "TAR": BaselineStats(mu=0.8, sigma=0.3, sigma_min=0.1),   # v2.8 嗜睡基线（θ/α 常模初值）
            "alpha": BaselineStats(mu=100.0, sigma=20.0, sigma_min=5.0),  # A_rel 基准（任务前睁眼 α 基线）
            "A_rel": BaselineStats(mu=0.0, sigma=0.3, sigma_min=0.06),
            "HR": BaselineStats(mu=75.0, sigma=6.0, sigma_min=1.0),
            # 注：MVP mock 用原始空间 RMSSD（mu=15ms）；真机协议按 R7 建议 ln 化，基线须给 ln 空间统计量
            "RMSSD_60": BaselineStats(mu=15.0, sigma=5.0, sigma_min=1.0, log_flag=False),
            "still": BaselineStats(mu=0.5, sigma=0.2, sigma_min=0.05),
            "delta": BaselineStats(mu=100.0, sigma=20.0, sigma_min=5.0),  # v2.8 嗜睡 δ 佐证基线
            # v2.5 焦虑 z 判据基线：官方指数个体 μ/σ（常模初值 mu=60；真机校准 A/B/C 段更新，
            # 未校准前绝对常模路径仍可用——att/med 不加入必填校验，缺省走绝对判据）
            "att": BaselineStats(mu=60.0, sigma=15.0, sigma_min=3.0),
            "med": BaselineStats(mu=60.0, sigma=15.0, sigma_min=3.0),
            # v2.8.5b：补 theta_ref（对齐 JS defaultBaseline=55；mock focus θ≈55，A 段 θ 中位数）。
            # 此前缺失 → _theta_ref=None → I_f 恒不积分 → 默认基线疲劳状态死锁
            # （output_demo/verify 显式注入掩盖）；真机由 calibrate_from_frames A 段覆盖
            "theta_ref": 55.0,
        }

    # -- v2.8 #9：个体基线自适应 ---------------------------------------
    def _maybe_adapt(self, feats: Feats, gates: dict, ts: float) -> None:
        """个体基线漂移补偿（默认 ADAPT_ENABLED=False，真机联调后启用）。

        设计要点（可辩解性优先）：
        - 仅在"无任何状态命中"的帧（gates 全 False → 该帧未被任何状态门控选中）更新 μ，
          降低把真实状态误当漂移的污染风险（neutral 帧仍可能含漏判状态，故更新极慢）；
        - EWMA α=ADAPT_ALPHA（时间常数 ~1000s）：快于 1000s 的变化属状态而非基线漂移；
        - 单次限幅 ±ADAPT_MAX_DRIFT σ：outlier 帧不得一次性拉动基线；
        - σ 不更新（漂移主要体现为均值，方差变化留给真机校准重估）。
        注意：这是经验初值方案（非文献），答辩须注明"漂移补偿未独立验证"。
        """
        if not ADAPT_ENABLED or not gates:
            return
        if ts - self._last_adapt_ts < ADAPT_EVAL_S:
            return
        if any(gates.values()):          # 有状态命中 → 该帧不是漂移样本
            return
        self._last_adapt_ts = ts
        for name, stat in self.baseline.items():
            if not isinstance(stat, BaselineStats):
                continue                  # theta_ref 等非统计量不参与自适应
            if stat.sigma <= 0 or name in ("att", "med"):
                continue                  # 官方指数基线不动（绝对常模路径保持）；σ<=0 无意义
            v = getattr(feats, name, None)
            if v is None or not math.isfinite(v):
                continue
            new_mu = stat.mu + ADAPT_ALPHA * (v - stat.mu)
            new_mu = max(stat.mu - ADAPT_MAX_DRIFT * stat.sigma,
                         min(stat.mu + ADAPT_MAX_DRIFT * stat.sigma, new_mu))
            stat.mu = new_mu

    # -- v2.8 §六 校准协议代码化 ----------------------------------------
    @staticmethod
    def calibrate_from_frames(frames: list[tuple[str, Frame]]) -> dict:
        """真机校准协议（§六 A/B/C 段）代码化：输入 (段标识, Frame) 列表，输出完整基线。

        段约定（与设计文档 §六 对齐）：
          "A" 闭眼静息（2min）：δ/θ 基线（嗜睡 δ 佐证、疲劳 θ_ref）
          "B" 任务前睁眼（30s）：R_tb/R_ba/TAR/A_rel(α)/HR/RMSSD/still 基线（z-score 参照）
          "C" 放松（2min）：A_rel/RMSSD 放松锚点（真机阈值校准预留；MVP 不直接改阈值）
        统计：μ/σ 用稳健估计（中位数 + MAD×1.4826，抗单次伪迹/眨眼尖峰），
        σ 下限取对应默认基线的 sigma_min（防单段样本过齐致 σ→0）。
        返回 dict 与 calibrate_from_mock 同构，可直接喂 StateEngine(baseline=...)。
        """
        from collections import defaultdict
        seg: dict[str, list] = defaultdict(list)
        for tag, fr in frames:
            seg[tag].append(fr)
        base = StateEngine.calibrate_from_mock([])   # 以默认基线的 sigma_min/结构为模板
        names = {
            "B": ["R_tb", "R_ba", "TAR", "alpha", "A_rel", "HR", "RMSSD_60", "still"],
            "A": ["delta", "theta"],
        }
        for tag, fnames in names.items():
            fs = seg.get(tag)
            if not fs or len(fs) < 10:
                continue                            # 段样本不足不覆盖对应基线（保留默认）
            for n in fnames:
                stat = base.get(n)
                if not stat or n not in base:
                    continue
                vals = []
                for fr in fs:
                    if n == "R_tb":
                        v = StateEngine._safe_ratio(fr.theta, fr.beta)
                    elif n == "R_ba":
                        v = StateEngine._safe_ratio(fr.beta, fr.theta + fr.alpha)
                    elif n == "TAR":
                        v = StateEngine._safe_ratio(fr.theta, fr.alpha)
                    elif n == "A_rel":
                        # v2.9 修复（10-23 代码审查 P1-1）：原除以 base["alpha"].sigma → 产出 z 量纲，
                        # 与运行时口径（L320 除以 μ 的比值量纲）错配——真机校准后 z(A_rel)≈比值
                        # 永远 < Z_RELAX_A=1.5 → relaxed 永久失效（分心评分 A_rel 项同步被废）。
                        # 改除以 μ：校准基线与运行时同为比值量纲，z(A_rel) 语义闭合。
                        v = (fr.alpha - base["alpha"].mu) / base["alpha"].mu
                    elif n == "RMSSD_60":
                        continue                    # RMSSD 需批内差分，校准段另行计算（见下）
                    else:
                        v = fr.__dict__.get(n)
                    if v is not None and math.isfinite(v):
                        vals.append(v)
                if not vals:
                    continue
                vals.sort()
                med = vals[len(vals) // 2]
                mad = med - vals[len(vals) // 4] if len(vals) > 3 else 0.0
                mad = max(mad, vals[len(vals) * 3 // 4] - med)
                sigma = max(mad * 1.4826, stat.sigma_min)
                stat.mu = med
                stat.sigma = sigma
        # B 段 RMSSD：同帧 rr 无法差分的用相邻帧差分（真机 RR 回调 3 值/次，帧内已有 batch 分组——
        # 校准段按帧内 rr 序列差分，样本不足时相邻帧首尾差分）
        bs = seg.get("B", [])
        diffs = []
        for i in range(len(bs)):
            f = bs[i]
            if f.rr_ms is not None and i > 0 and bs[i - 1].rr_ms is not None:
                d = abs(f.rr_ms - bs[i - 1].rr_ms)
                if RR_MIN_MS <= f.rr_ms <= RR_MAX_MS and RR_MIN_MS <= bs[i - 1].rr_ms <= RR_MAX_MS:
                    diffs.append(d)
        if len(diffs) >= 8:
            diffs.sort()
            med = diffs[len(diffs) // 2]
            mad = max(med - diffs[len(diffs) // 4], diffs[len(diffs) * 3 // 4] - med)
            rm = base["RMSSD_60"]
            rm.mu = math.sqrt(sum(d * d for d in diffs) / len(diffs))
            rm.sigma = max(mad * 1.4826, rm.sigma_min)
        # θ_ref（疲劳 I_f 冻结基线）——v2.9.2 修正：必须取 B 段（任务参照帧），不能取 A 段
        #   原因：A 段是闭眼静息，θ 系统性高于睁眼任务（测试用例自身即 A=130 / B=100）。
        #   原实现取 A 段 → 主任务 θ 只有 A 段的 0.5~0.8 倍，θ_norm 恒 <1，I_f 永不积分，
        #   真机校准后疲劳再度不可达（与 P1-3 同一失效模式，只是换校准协议重新引入）；
        #   且 R_tb/R_ba/TAR/A_rel/HR/RMSSD/still 全部以 B 段为参照，唯独 θ_ref 用 A 段，
        #   量纲参照系不一致。改为 B 段中位数，A 段 θ 仅存 theta_rest 作静息参考（不参与判据）。
        def _median(xs):
            xs = sorted(xs)
            return xs[len(xs) // 2] if xs else None

        b_ths = [fr.theta for fr in seg.get("B", []) if math.isfinite(fr.theta)]
        a_ths = [fr.theta for fr in seg.get("A", []) if math.isfinite(fr.theta)]
        if len(b_ths) >= 10:
            base["theta_ref"] = _median(b_ths)          # 首选：B 段任务参照
            base["theta_ref_src"] = "B"
        elif len(a_ths) >= 10:
            base["theta_ref"] = _median(a_ths)          # 回退：A 段（仅在 B 缺失时，附告警）
            base["theta_ref_src"] = "A-fallback"
        else:
            base["theta_ref_src"] = "default"
        if a_ths:
            base["theta_rest"] = _median(a_ths)         # 闭眼静息 θ（仅供参考/答辩，不参与判据）
        # v3.1 β/γ 参照（肌电专用门用；与 theta_ref 同段，保证参照系一致）
        for band in ("beta", "gamma"):
            b_vals = [getattr(fr, band) for fr in seg.get("B", [])
                      if math.isfinite(getattr(fr, band, float("nan")))]
            a_vals = [getattr(fr, band) for fr in seg.get("A", [])
                      if math.isfinite(getattr(fr, band, float("nan")))]
            v = _median(b_vals) if len(b_vals) >= 10 else (_median(a_vals) if len(a_vals) >= 10 else None)
            if v:
                base[f"{band}_ref"] = v
        return base

    # -- v2.9.2 校准质量自检（§六 第 5 步：校准失败须提示重戴，不得静默进入判别）--------
    @staticmethod
    def calibration_report(frames: list[tuple[str, "Frame"]],
                          base: dict | None = None) -> dict:
        """校准质量报告：段样本量 / θ_ref 来源 / C 段放松锚点可分性 / 伪迹率。

        原实现缺陷：三段中 C 段（放松锚点）无任何消费者，且段样本不足时静默保留默认基线，
        违反设计文档 §六 第 5 条"校准失败提示重戴，不静默进入判别"。此报告补上可见性：
        - artifact_ratio：伪迹帧占比（>30% 判失败，§六 标准）
        - c_anchor_z：C 段 A_rel 相对 B 段基线的 z（放松态是否可判；< Z_RELAX_A 说明场景无效）
        - warnings：可直接展示给操作者的提示文本
        """
        base = base or StateEngine.calibrate_from_frames(frames)
        seg: dict[str, list] = {"A": [], "B": [], "C": []}
        for tag, fr in frames:
            if tag in seg:
                seg[tag].append(fr)
        counts = {k: len(v) for k, v in seg.items()}

        # 伪迹率：与引擎 _artifact 同一判据（8×/15× 突增、β+γ 肌电门），按序回放维护平滑值
        artifact_ratio = StateEngine._artifact_ratio_static(frames, base)

        # C 段放松锚点：以 B 段 alpha 基线折算 A_rel，再看 z 是否足以越过放松门控
        c_z = None
        alpha_stat = base.get("alpha")
        a_rel_stat = base.get("A_rel")
        if alpha_stat and a_rel_stat and alpha_stat.mu > 0 and counts["C"] >= 10:
            vals = [(fr.alpha - alpha_stat.mu) / alpha_stat.mu for fr in seg["C"]
                    if math.isfinite(fr.alpha)]
            if vals:
                vals.sort()
                c_med = vals[len(vals) // 2]
                sigma = max(a_rel_stat.sigma, a_rel_stat.sigma_min)
                c_z = (c_med - a_rel_stat.mu) / sigma

        warnings: list[str] = []
        if counts["B"] < 10:
            warnings.append(f"B 段样本不足（{counts['B']} 帧 <10），R_tb/R_ba/TAR/θ_ref 沿用默认值，"
                            f"校准无效，请重做 B 段（睁眼、静坐、不看屏幕强光）")
        if counts["A"] < 10:
            warnings.append(f"A 段样本不足（{counts['A']} 帧 <10），δ 基线沿用默认值，嗜睡判据不可信")
        if counts["C"] < 10:
            warnings.append(f"C 段（放松锚点）样本不足（{counts['C']} 帧 <10），无法校验放松态可判性")
        if artifact_ratio > CALIB_ARTIFACT_MAX:
            warnings.append(f"伪迹帧占比 {artifact_ratio:.0%} > {CALIB_ARTIFACT_MAX:.0%}，"
                            f"信号质量不合格，请检查佩戴（§六 第 5 条）")
        if c_z is not None and c_z < Z_RELAX_A:
            warnings.append(f"C 段放松锚点 z={c_z:.2f} < 门控 {Z_RELAX_A}，"
                            f"该受试者放松态在当前 α 基线下不可判（建议提高 C 段放松引导强度或复核佩戴）")
        ok = (counts["B"] >= 10 and counts["A"] >= 10 and counts["C"] >= 10
              and artifact_ratio <= CALIB_ARTIFACT_MAX)
        return {"ok": ok, "segments": counts, "artifact_ratio": artifact_ratio,
                "theta_ref": base.get("theta_ref"), "theta_ref_src": base.get("theta_ref_src"),
                "theta_rest": base.get("theta_rest"),
                "beta_ref": base.get("beta_ref"), "gamma_ref": base.get("gamma_ref"),
                "emg_gate": bool(ARTIFACT_EMG_GATE),
                "c_anchor_z": c_z, "warnings": warnings}


# ---------------------------------------------------------------- mock 演示
# v2.8.6：相位表提为模块级单一事实源。
# 起因：live_bridge.source_mock 自己另写了一份 8 态列表（含 fatigue/drowsy），
# 而 _mock_frame 的 base 只有 6 态 → 轮到第 7 个相位时 base[phase] 抛 KeyError，
# **帧源线程直接死掉**，但 HTTP 服务仍返回 200 —— 表现为"面板还在、数据不再刷新"的静默失败。
# 两个列表各自维护必然漂移，故导出 MOCK_PHASES 供 live_bridge 直接引用。
# 元组含义：(θ系数, α系数, 心率, RR 抖动ms)；β/δ 默认 1.0 / 100，少数相位用下表覆盖
_MOCK_PHASE_SPEC: dict[str, tuple[float, float, int, int]] = {
    "focus":      (0.55, 1.3, 75, 30),
    "distracted": (1.60, 1.0, 78, 30),
    "relaxed":    (1.00, 1.6, 62, 50),
    "stress":     (0.90, 1.1, 88, 10),
    "anxiety":    (1.30, 1.0, 84, 30),   # θ/β 偏高（非专注）+ 心率偏高 + 双指数低；
                                         # jitter=30（HRV 正常）区分于 stress（jitter=10，HRV 骤降）
    "flow":       (0.70, 1.4, 66, 42),   # 接近专注 + 放松（心流=专注+放松协同，指南 2.2）
    # v2.8.6 补全 8 态：这两项原缺失，是上面的崩溃根因
    "fatigue":    (1.50, 1.10, 68, 32),  # θ↑ 使 θ_norm>1 → I_f 可持续累积；
                                         # β 由下表降到 0.30 → R_ba≈0.27，满足 z(R_ba)<-1.0
    "drowsy":     (1.90, 1.10, 60, 40),  # TAR=θ/α≈1.7 满足 z(TAR)>+1.5；
                                         # δ 由下表抬到 220 满足 z(δ)>+1.0（合取口径 S5）
}
_MOCK_ATT_MED: dict[str, tuple[int, int]] = {
    "focus": (65, 55), "distracted": (45, 45), "relaxed": (50, 70),
    "stress": (40, 30), "anxiety": (28, 26), "flow": (72, 68),
    "fatigue": (38, 52),   # 警觉度下降但未入睡
    "drowsy": (25, 40),    # 接近睡眠（attention 常模下限）
}
_MOCK_BETA: dict[str, float] = {"fatigue": 0.30, "drowsy": 0.35}   # 缺省 1.0
_MOCK_DELTA: dict[str, float] = {"drowsy": 220.0}                  # 缺省 100

MOCK_PHASES: tuple[str, ...] = tuple(_MOCK_PHASE_SPEC)   # 唯一相位清单，外部请引用这个


def _mock_frame(phase: str, t: float | None = None) -> Frame:
    """按阶段生成脑波/心率帧（仅验证管道与格式，不验证判别准确性）
    rr_jitter: 相邻 RR 抖动幅度（ms），越大 HRV 越高——relaxed 大（RMSSD↑）、stress 小（RMSSD↓）
    attention/meditation 按阶段常模（指南 2.1/2.2 区间）：anxiety 双低值、flow 双高值

    t：相位内进度 0→1，**仅 flow 使用**。心流判据含"派生同步率"（双指数趋势一致性），
    静态值在窗口内差分为 0 → sync_ok=False → flow 被误杀（详见 `_index_high` 注释与指南 2.2）。
    故 flow 需注入单调同向上升。t=None 表示不注入趋势（历史调用的默认行为，保持不变）。"""
    rnd = random.random
    if phase not in _MOCK_PHASE_SPEC:
        raise ValueError(f"未知 mock 相位 {phase!r}；可用：{', '.join(MOCK_PHASES)}")
    tb, al, hr, jitter = _MOCK_PHASE_SPEC[phase]
    rr = 60000.0 / hr + jitter * (rnd() - 0.5)
    be = _MOCK_BETA.get(phase, 1.0)
    dl = _MOCK_DELTA.get(phase, 100.0)
    att, med = _MOCK_ATT_MED[phase]
    if phase == "flow" and t is not None:
        # 与 output_demo 原内联公式等价（72+0.25i 截顶 88 / 68+0.25i 截顶 84，i 走满 65 帧即 t=1）
        tt = max(0.0, min(1.0, t))
        att = min(88.0, 72.0 + 16.0 * tt)
        med = min(84.0, 68.0 + 16.0 * tt)
    return Frame(
        theta=100 * (tb + 0.2 * (rnd() - 0.5)),
        beta=100 * (be + 0.2 * (rnd() - 0.5)),
        alpha=100 * (al + 0.2 * (rnd() - 0.5)),
        delta=dl, gamma=50,
        rr_ms=max(RR_MIN_MS, min(RR_MAX_MS, rr)),
        gyro=(0.02 * rnd(), 0.02 * rnd(), 0.02 * rnd()),
        attention=att, meditation=med,
    )


def _make_mock_clock():
    """虚拟时钟：每帧推进 1s（v2.3——真实 time.time 在毫秒级 mock 下冷却永不解除，
    distracted/stress 反馈永远显示"冷却中"，演示价值受损）"""
    t = [0.0]

    def clock():
        t[0] += 1.0
        return t[0]

    return clock


def main():
    random.seed(7)
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    print("=== state_engine.py MVP 管道演示（mock 数据，验证管道与格式）===")
    print("窗口 60s 设计特性：阶段切换存在窗内混合 transient；演示用 65s/阶段稳态验证\n")
    # 预热：填充 60s HRV 窗（focus，任务态）
    for _ in range(65):
        eng.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    print("预热 focus 65s 完成（60s 窗已填充）\n")
    # v2.4 六状态演示：focus/distracted/relaxed/stress/anxiety/flow（各 65s 稳态）
    # flow 阶段注入递增 attention/meditation（单调同向 → 派生同步率达标；指南 2.2 上升趋势）
    # task_active 仅任务阶段注入（focus/distracted）；其余为非任务态
    for ph in ("focus", "distracted", "relaxed", "stress", "anxiety", "flow"):
        task = ph in ("focus", "distracted")
        for i in range(1, 66):
            # flow 需双指数协同上升才满足派生同步率；趋势注入公式已收敛进 _mock_frame(t=...)
            fr = _mock_frame(ph, t=(i / 65.0) if ph == "flow" else None)
            eng.process_frame(fr, ctx={"task_active": task})
            if i % 20 == 0 or i == 65:
                dec, fb = eng.last_decision, eng.last_feedback
                print(f"phase={ph:10s} t={i:3d}s -> state={dec.state:10s} conf={dec.conf:.2f} "
                      f"fb=[{fb.level}: {fb.action}]")
    eng.rationality_log(True, eng.last_decision)   # 示例：1 次冲动决策
    eng.rationality_log(False, eng.last_decision)
    print(f"\n理智度示例: calmness={eng.calmness:.2f}")


if __name__ == "__main__":
    main()
