# -*- coding: utf-8 -*-
"""state_engine.py — 状态判别引擎 v3.4.1（算法层重设计 + 复查修正）

对应《状态判别建模设计_优化版_组长E_20261015.md》（下称"设计稿"）：
全特征 z-score 个体偏离 + 门控 + 优先级仲裁 + 反馈 + 理智度记录；
8 状态 = 专注/分心/放松/压力（MVP）+ 焦虑/心流/疲劳/嗜睡（增强版）。

─────────────── 分层（单向依赖，便于单测与复核） ───────────────
Frame ──► ArtifactDetector ──► 特征窗 ──► _extract ──► z-score ──► 门控余量
                                                                     │
                        RmssdWindow ─┘（HRV 缓冲，运行/校准共用）      ▼
                                                          证据归一(EVID) ──► 仲裁 ──► 时间确认 ──► 反馈
主要类：
  ArtifactDetector  伪迹/接触判定**唯一实现**（引擎与校准共用，杜绝"同一规则两处维护"）
  RmssdWindow       RR 缓冲 + RMSSD（运行时与校准共用，P3-3 口径统一的落地）
  ScoreMap          评分表（dict 子类，附带 margins/evidence，不改 _scores 返回元数）
  StateEngine       编排：平滑 → 特征 → z → 余量 → 证据 → 仲裁 → 确认 → 反馈

─────────────── v3.4 相对 v3.3 的算法改动（每条都有量化审计支撑） ───────────────
1. 门控余量 + 证据归一（新算法）
   旧：conf = 0.6+0.35·(best−second)/(|best|+|second|) —— 跨状态**比不同量纲的分数**
       （z 组合量级 1–3；官方指数余量量级 10–20）。实测：RMSSD z=−2 的强应激
       （score 2.0）与 att/med=30 的焦虑（score 20）并存 → conf 被压到 0.60（下限），
       强应激被标成"弱判别，仅记录"，干预永不触发。
   新：每状态先算**门控余量 m（σ 当量，可跨状态比较）**，再归一成证据 e=clamp(m/EVID_REF,0,1)；
       证据比 → 同一场景 conf 0.60→0.75（越过 WEAK_CONF，恢复干预）。
   新（v3.4.1 补）：conf = 0.6+0.35·**sep**·**strength**。
       只做证据比时有个更老的洞：单候选（无对手）sep 恒 1 → 超阈 0.001σ 与 5σ 都给 0.95，
       WEAK_CONF 的"弱判别仅记录"永不生效（旧式同样有此洞，只因旧式还比量纲而未被发现）。
       strength=clamp(m_best/EVID_FULL) 把"本身够不够硬"补上：0.001σ→0.601、0.25σ→0.775、
       ≥0.5σ→0.95（verify_engine E7 端到端锁死）。EVID_REF=1σ 管排他性、EVID_FULL=0.5σ 管强度，
       两者不可合并（合并必然一边错）。
       仲裁仍**只按 PRIORITY**（临床语义：生理应激优先干预），见 verify_v30 K 用例锁死。
2. 疲劳积分改用平滑 θ（EWMA α=0.2）
   旧：I_f 积分瞬时逐帧 θ，经 max(0,θ_norm−1.2) **整流**后，噪声产生正偏置
       （Jensen：E[max(0,X)]≈σ/√(2π)>0）。实测 θ 均值恰在阈值、σ=12（10% 噪声）时，
       1800s 无中生有累积 I_f=179s（0.0995/s）→ 长时盯盘会假阳性疲劳。
   新：积分对象改为 EWMA 平滑 θ，σ 降至 1/3（√(α/(2−α))=0.333）→ 偏置约降 3 倍。
3. δ 特征统一到 10s 窗均值
   旧：z(δ) 用**瞬时帧** δ，其余特征全用窗均值——单帧 δ 起伏（+40%）即令 z(δ) 跳 2.0σ，
       嗜睡门控随单帧抖动翻转。
   新：Feats 增加 delta/theta/alpha 窗均值，z(δ) 与其它特征同一时间尺度（+40% 单帧 → z 0.2）。
4. 接触质量门进引擎（Frame.signal）
   旧：引擎不看 signal，脱戴帧（protocol：200=电极未接触皮肤）照样进特征窗——实测 12 帧
       signal=200 全被吸收（eeg_buf=11）。串口路径靠适配器单独挡，回放/校准路径无保护。
   新：硬门 signal ≥ SIGNAL_BAD(200) 判无效帧，统一走 ArtifactDetector；细粒度阈值仍由
       `--signal-max`（V3 标定）在适配器侧处理，职责不重叠。
5. 校准层三处口径修正
   a) δ 基线改取 **B 段（任务参照）**，A 段仅留 delta_rest 参考。旧实现取 A 段（闭眼），
      δ 基线 μ=149.7，任务中 δ=118 的 z=−3.42 → 嗜睡佐证恒不成立（真机校准后嗜睡静默失效，
      与 v2.9.2 修的 θ_ref 取 A 段属**同一失效模式**）。
   b) RMSSD 校准改走 RmssdWindow（与运行同一函数、同一批次语义）→ 消除 P3-3 的
      "校准帧级差分 / 运行批内差分"双口径。
   c) alpha 基线先算再算 A_rel（显式两段），不再依赖特征名列表的迭代顺序（隐性依赖）。
6. RMSSD 加异位搏动滤波（相邻间期突变 >20% 的对剔除）+ 差分对下限
   旧：只按样本数 <8 返回 nan，且不滤波 → 一次掉搏/异位（800→1420ms）把 RMSSD 抬高一个量级
       （校准基线随之虚高 → z 偏低 → 压力少报）。20% 规则见 Task Force 1996 / Malik 异位滤波惯例。
       差分对下限改为"有效差分对 ≥ RMSSD_MIN_PAIRS"，与"窗口未满"的真实语义一致。
7. 时间确认按**时长加权**（原按帧数）
   帧率抖动（真机 1.2–1.5s/帧，掉帧时更慢）时，帧数占比与时间占比不再等价；
   改为 Σdt(命中)/Σdt(窗口)，确认时长恒为 DEBOUNCE_S·HIT_RATIO，与帧率解耦。
8. push_rr 缺省时间戳不再调用 _clock()
9. （v3.4.1）焦虑 z 分支的余量符号
   旧：门控是 za < ANX_Z（**低于**阈值），余量却用 gt（v−th）→ 恒为负 → 证据 0 → conf 掉到 0.6，
   该提醒的焦虑被判"弱判别"静默。只影响 conf、不影响状态判定，所以全套历史测试都不红，
   唯一抓住它的是 verify_v26 V5。改 lt("att_z", ANX_Z)。
10. （v3.4.1）校准报告不再重复回放；ok 判据补接触率/差分对
    旧：calibration_report 另起一个 ArtifactDetector 统计同一批帧（与 calib_meta 可能不一致），
    且 ok 只判"段长/伪迹率"，而设计稿 §6.1 已写接触>10%、差分对<6 也判失败。
11. （v3.4.1）差分脚本 att/med 插值
    _diff_py/_diff_js 里非 flow 段 k=0 → att 恒 52、med 恒 48，anxiety 段（28/26）实际喂的是
    52/48 → gates 全空判 neutral，而闸门 2 一直"逐行一致"（一致的是"两边都没判出来"）。
    修正后 8 段全部命中目标状态；闸门 2b 逐段核对，这类"假覆盖"再也藏不住。
   旧：默认调 clock，mock 时钟下一次 onRR 三值即推进 3s（时间被凭空拉长）；改为复用最近帧 ts。
9. 伪迹参照系改为稳健中位数 + 门限留余量（8× → 6×）
   旧：判据比对 EWMA 平滑值，且 abs(v/ref−1)>8 实为"9 倍以上"。两处叠加导致**眨眼类突变恒漏检**：
   项目伪迹模型里眨眼 α 抬升 8.2 倍（110→900），与 8× 门限同量级 → 参照的随机起伏决定判不判得出来
   （20 个噪声种子实测检出率 **0–12/12** 抖动），且首个漏检的眨眼会把平滑参照抬走、连累后续判定。
   现：参照取最近 31 个**有效帧**的中位数（伪迹帧不记账 → 不会被抬走；若记账，污染 >50% 时中位数
   会翻到伪迹电平，实测 16 帧后整批放行），门限回到"倍数"字面语义并双向判，8×→6× 留 1.36 倍余量。
   实测：眨眼段 0/12 → **12/12**（σ 586→5）；突变肌电（β 20×）40/40（旧实现亦 40/40，此项作回归守卫）；
   **已知局限**：渐变起音的肌电（每帧 +7.8%，40 帧升 20×）两版都检不出（0/40）——比值判据分不清
   "缓慢涨到 20 倍"与"真实状态迁移"，这条仍须靠 β+γ 专用门（§6.3，默认关，V4 判是否启用）。

运行演示（mock 数据验证管道）：
  python state_engine.py
各层自检：
  python verify_engine.py
"""
from __future__ import annotations

import math
import random
import time
from collections import deque
from dataclasses import dataclass, field

# ---------------------------------------------------------------- 常量（初值，待真机校准）
EWMA_ALPHA = 0.2          # EEG 快特征平滑（SDK 指南 4.2：0.1–0.3）
ARTIFACT_RATIO = 15.0     # 伪迹门限：单频段相对参照的**倍数**（v≥15×ref 或 v≤ref/15 即判伪迹）
                          # v3.6 真机定标（原值 6.0）：2026-11-09 四份真人 dump 实测，6× 严重过严——
                          # A 段（闭眼静息）clean 帧只剩 1/2/9/22 帧（共 32-48 帧），三段伪迹率
                          # 33.7%→80.5%，四份全部 ok=false。根因是真机 eegPower 是 1 秒瞬时 FFT，
                          # α 在闭眼时呈"纺锤"（成串出现/消失），单频段天然在 0.1×–6× 间摆，
                          # 这不是伪迹。定标依据（三份数据，三段合计伪迹率）：
                          #     6× → 47.5% / 48.7% / 80.5%   A 段 clean 1 / 2 / 9
                          #    15× → 20.1% / 10.1% / 11.8%   A 段 clean 18 / 32 / 43
                          #    20× → 12.9% /  5.8% /  9.1%   A 段 clean 21 / 32 / 47（收益递减）
                          # 取 15×：三份全部 <30%（ok 可过），且 A 段 clean 已饱和（15→20 只多 0-4 帧）。
                          # 真肌电仍由 emg_artifact（β+γ 同升）独立拦截，与本门限无关。
                          # v3.4 由 8× 收紧到 6×，理由是"零余量门限"：项目自带伪迹模型里眨眼
                          # α 抬升约 8.2 倍（110→900），与 8× 门限同量级 → 参照的随机起伏决定
                          # 判不判得出来。20 个噪声种子实测检出率 0–12/12（同一段数据时有时无）；
                          # 6× 下 12/12 稳定检出，且真实状态迁移（闭眼 α 抬升）实测 ≤3–4 倍仍在门下。
ARTIFACT_RATIO_RTB = 15.0 # v2.8：R_tb 比值门限独立放宽——状态切换 R_tb 可达 5-10 倍，
                          # 原 8× 把真实疲劳/分心切换当伪迹丢弃（I_f 永不积分）；单频段 8× 已覆盖硬件伪迹
                          # （v3.4：单频段已收紧到 6×，倍率关系不变——R_tb 仍独立放宽 2.5 倍）
# v3.4：伪迹判据的参照系改为**稳健中位数**，门限回归"倍数"字面语义。E13 校准用例实测（30 帧里
# 12 帧眨眼 α=900，占 40%）：旧实现伪迹率报 **0%**、σ 被抬到 587（真实 σ≈5），根源是两处叠加：
#   a) 参照系用 EWMA → 眨眼先把参照抬上去（900 连撞几帧后 smooth≈370），之后"900 vs 370"仅 2.4×，
#      **持续伪迹自我掩护**；越是高发的伪迹越检不出来，与"伪迹率>30% 判校准失败"的规矩直接冲突；
#   b) 判据 abs(v/ref−1) > 8 实际是"9 倍及以上"，而真机眨眼 α 抬升实测约 8.2 倍（110→900）→ 恰好漏掉。
# 现口径：ref = 最近 ARTIFACT_REF_WINDOW 个**有效帧**（被判伪迹的帧不记账）的中位数。
#   为什么伪迹帧不记账（这是与"只记全部帧"的关键取舍，实测两次都踩过）：
#     · 全记 → 伪迹窗口内污染 >50% 时中位数**翻到伪迹电平**（20× 肌电持续 16 帧后锚点≈2000，
#       之后同一批 20× 帧反而"不超阈"全部放行）——即"持续伪迹自我掩护"换个形式又回来了；
#     · 只记有效帧 → 伪迹期间锚点冻结在最后一段干净电平，持续肌电一帧不漏（实测 40/40）。
#   代价：若"伪迹"其实是新的工作电平（增益切换/重戴），锚点永远不会跟过去 → 判据锁死。
#   故补 ARTIFACT_REF_RESET 逃生门：连续这么多帧全判伪迹，说明判据前提（电平连续）不成立，
#   以当前帧重建参照并计数，而不是静默锁死。
# 判据取双侧（过高=眨眼/碰电极；过低=贴片干涸/脱落导致幅值塌陷），与比值量的乘性本质一致。
ARTIFACT_REF_WINDOW = 31  # 参照窗口帧数（真机 ~1–1.5s/帧 → 半分钟量级，覆盖一次呼吸/眨眼周期）
ARTIFACT_REF_MIN = 5      # 参照样本下限（5 帧后中位数才有意义；此前回退到 smooth 种子）
ARTIFACT_REF_RESET = 60   # 连续判伪迹帧数上限（≈1 分钟）：超过即重建参照系（逃生门）
# v3.1：β+γ 肌电专用门（默认关闭，真机 V4 通过后按《规划》5.3 打开）
# 背景：8× 门是"突变检测"，对**持续**肌电（说话/咬牙）会饱和——首帧爆发触发一次，
#       随后平滑基线被抬上去，后续帧不再超过 8×，伪迹率上不去 → 朗读段 EEG 被当有效数据。
# 判据：β、γ 同时高于 B 段参照的 N 倍，且 θ 仍在参照附近（θ 大动属真实状态迁移，不该拦）。
# 取 4× 而非 2×：真实肌电幅值远超 EEG（10–100×），2× 会与"认知负荷升 β"混淆。
ARTIFACT_EMG_GATE = False       # 总开关；verify_hw.py V4 判"需启用"时改 True（两引擎同步）
ARTIFACT_EMG_RATIO = 4.0        # β/γ 相对 B 段中位数的倍数门限
ARTIFACT_EMG_THETA_TOL = 0.35   # θ 允许偏离参照的比例（±35% 内视为"θ 没动"）
# v3.4：接触质量硬门。TGAM poorSignalLevel 0=最好、200=电极未接触皮肤（NeuroSky 协议）。
# 只挡协议明确的"无效"档；细粒度阈值由 --signal-max（verify_hw V3 用 P90 标定）在适配器侧定，
# 两层不重叠：引擎保底（回放/校准/第三方调用也受保护），适配器做现场调优。
SIGNAL_BAD = 200
RR_MIN_MS, RR_MAX_MS = 300, 2000     # RR 生理范围（R7）
# v3.4：异位搏动/掉搏滤波——相邻间期突变超过前一心搏 20% 的对不参与 RMSSD。
# 依据：HRV 标准处理流程要求剔除异位搏动与漏搏（Task Force 1996 相邻间期差值筛选惯例；
# 训练监控实践同取 ±20%）。不滤波时一次 800→1420ms 的掉搏可把 RMSSD 抬高一个量级。
RR_DIFF_MAX_RATIO = 0.20
RMSSD_MIN_PAIRS = 6       # RMSSD 有效差分对下限（"窗口未满"的真实语义；原按样本数判）
                          # 取 6 而非 8：真机每批 3 值只产生 2 对，历史口径"≥8 个 RR 值"
                          # 恰好等价于 6 对；改成按对数判是为了让语义可读，而不是收紧可用性。
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
# ---- 增强版第二阶段初值（v2.3 S3/S6）----
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
# v3.4：θ_norm 的分子改用**平滑 θ**（EWMA，见 ArtifactDetector.smooth）——瞬时值经整流后
#       有正偏置（实测 0.0995/s，1800s 凭空 +179s），平滑后 σ 降为 1/3，偏置约降 3 倍。
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
WINDOW_GYRO_S = 30        # 体动窗（秒）
HIT_RATIO = 0.6           # 慢变状态时间确认：窗内命中**时长**占比 ≥ 0.6（R5 4/5 思想）
DEBOUNCE_S = 5.0          # 防抖窗口（秒）
CONF_MIN, CONF_MAX = 0.6, 0.95
# v3.4 证据归一参考：门控余量 m 达到 EVID_REF 个 σ 即视为"满证据"（e=1）。
# 取 1.0σ 的理由：门控阈值本身取在 1–1.5σ（Z_CONC/Z_STRESS），再往外 1σ 已是该分布的
# 明显尾部；更大的参考值会让 e 长期贴 0，置信度失去分辨力。经验初值，真机可调。
EVID_REF = 1.0
# v3.4.1 绝对强度参考：门控余量达到 EVID_FULL 个 σ 才视为"证据充分"（strength=1）。
# 为什么必须与 EVID_REF 分开：EVID_REF 只服务**证据比**（排他性，需要区分度，取 1σ）；
# 而"这个结论本身有多硬"要另一个标尺。若两者合一，会同时犯两种错：
#   ① 用 EVID_REF=1σ 当强度 → 超阈 0.5σ 只给 0.5 强度，把"z=−2 vs 阈 −1.5"这种
#      正常强判定压成弱判别（m 是**超额**，不是绝对偏离，天然偏小）；
#   ② 完全不管强度（v3.4 原实现）→ 超阈 0.001σ 与超阈 5σ 都给 CONF_MAX，单状态时
#      置信度恒 0.95，WEAK_CONF 的"弱判别仅记录"永不触发（实测 conf 0.950/0.950）。
# 取 0.5σ：门控阈值已在 1–1.5σ，再超 0.5σ 即"明显越过判据"，与 8 状态的余量分布同量级。
EVID_FULL = 0.5
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
BANDS = ("delta", "theta", "alpha", "beta", "gamma")

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
    rr_ms: float | None = None        # 心率间期（ms）；单值路径（模拟器 / 旧 CSV）
    rr_batch: tuple | None = None     # v3.4：一次 onRR 的三个连续间期（同批语义，优先于 rr_ms）
    heart: float | None = None        # 心率直读 bpm（SDK 扩展回调，真机 HR 特征；None=走 RR 换算）
    gyro: tuple = (0.0, 0.0, 0.0)     # 体动（v2.2 still 特征）
    signal: int | None = None         # v3.4 接触质量 poorSignalLevel（0 最好 / 200 未接触；None=未知）
    ts: float | None = None           # v2.7 时间戳（秒；None/<=0 时引擎用内部时钟补——mock 帧可不传）

    def __post_init__(self):
        """gyro 归一化：外部传 None / 短元组 / 字符串时不许把引擎打崩。

        此前 `Frame(gyro=None)` 会在 `_extract` 的 `g[0]` 上抛 TypeError——而"本机没有
        陀螺仪"最自然的表达就是 None。缺失与全 0 目前仍按同一口径处理（见数据链路审查
        P3-2：缺通道被当成"完全静止"，但改语义会动到已验收分数，暂只做类型兜底）。
        """
        g = self.gyro
        if g is None:
            self.gyro = (0.0, 0.0, 0.0)
            return
        try:
            vals = [float(x) for x in list(g)[:3]]
        except (TypeError, ValueError):
            self.gyro = (0.0, 0.0, 0.0)
            return
        while len(vals) < 3:
            vals.append(0.0)
        self.gyro = tuple(vals)

@dataclass
class BaselineStats:
    mu: float = 0.0
    sigma: float = 1.0
    sigma_min: float = 0.1
    log_flag: bool = False            # RMSSD 建议 ln 化（R7）；置 True 时 μ/σ 必须在 log 空间

@dataclass
class Feats:
    """特征窗口输出（全部为窗均值口径 —— v3.4：δ/θ/α 补入，消除"z(δ) 用瞬时值"的时间尺度错配）"""
    R_tb: float = 0.0
    R_ba: float = 0.0
    TAR: float = 0.0          # v2.8 θ/α（嗜睡 TAR 判据；映射表 M5）
    A_rel: float = 0.0
    HR: float = 0.0
    RMSSD_60: float = 0.0
    dRMSSD: float = 0.0
    dHR: float = 0.0
    still: float = 0.0
    delta: float = float("nan")       # v3.4：δ 窗均值（嗜睡佐证；原用瞬时帧值）
    theta: float = float("nan")       # v3.4：θ 窗均值（参考/诊断）
    alpha: float = float("nan")       # v3.4：α 窗均值（参考/诊断；A_rel 已含）

class ScoreMap(dict):
    """状态评分表。dict 子类额外挂 margins（门控余量，σ 单位）与 evidence（归一证据 0–1）。

    用子类承载而非改 `_scores` 返回元数（(scores, gates) 已被多处按 2 元解包），
    同时让 `_arbitrate(scores, gates)` 能拿到同量纲证据——外部若直接传普通 dict，
    仲裁自动退回旧"分数差比"兼容路径（仅单测/UI 重算会这样用）。
    """
    __slots__ = ("margins", "evidence")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.margins = {}
        self.evidence = {}

@dataclass
class Decision:
    state: str = "relaxed"
    conf: float = 0.0
    scores: dict = field(default_factory=dict)
    gates: dict = field(default_factory=dict)
    # v3.4：证据/余量随决策一并返回——conf 表示"排他性"（与谁竞争），margins 表示"强度"。
    # 两者分开后，界面上"强应激但因焦虑并存而 conf 中等"能被如实呈现，不再被误标为弱判别。
    margins: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    reason: str = ""                  # ""=正常；"contact"/"artifact"=该帧未参与判别（诊断用）

@dataclass
class Feedback:
    level: str = "silent"             # silent / mild / calm / strong
    action: str = ""
    cooldown_left: float = 0.0

# ---------------------------------------------------------------- 预处理组件

class ArtifactDetector:
    """伪迹 + 接触质量判定（**唯一实现**）。

    为什么单独成类：v3.3 之前引擎的 `_artifact` 与校准的 `_artifact_ratio_static` 是同一套
    规则的两份手写实现（平滑推进顺序、R_tb 检查、肌电门三处都要人工对齐）。本项目已四次
    栽在"同一事实两处维护"（RMSSD 恒 nan / signal=230 / signal 帧内 30 而 CSV 写 200 /
    相位表 6 vs 8）。这里把它收敛成一份，校准直接复用同一个实例。
    """

    def __init__(self, baseline: dict | None = None, alpha: float = EWMA_ALPHA):
        self.smooth: dict[str, float] = {}
        self.baseline = baseline or {}
        self.alpha = alpha
        self.frames = 0
        self.artifact_hits = 0
        self.contact_hits = 0
        self._rejects = 0            # 连续判伪迹帧数（逃生门用）
        self.reseeds = 0             # 参照系重建次数（诊断：>0 说明中途电平变了）
        # 伪迹判据的**稳健参照系**（5 频段 + R_tb），与 smooth 分属两件事：
        #   smooth = 信号通路的平滑值（I_f 积分对象，跟随状态变化，允许被抬走）；
        #   _refs  = 判据的锚（中位数，抗污染，只用于"这一帧是不是突变"）。
        # 合成一个必然二选一错：既让积分看到真实趋势，又让判据不被伪迹带走，只能分开。
        self._refs: dict[str, deque] = {k: deque(maxlen=ARTIFACT_REF_WINDOW)
                                        for k in (*BANDS, "R_tb")}

    # -- 平滑（信号通路；伪迹帧不推进）------------------------
    def ewma(self, key, x):
        if key not in self.smooth:
            self.smooth[key] = x
        else:
            self.smooth[key] = self.alpha * x + (1 - self.alpha) * self.smooth[key]
        return self.smooth[key]

    def update(self, frame) -> None:
        """有效帧推进平滑（顺序与引擎/校准一致：5 频段 → R_tb 比值）"""
        for key in BANDS:
            v = getattr(frame, key, None)
            if v is None or not math.isfinite(v):
                continue
            self.ewma(key, v)
        tb = StateEngine._safe_ratio(frame.theta, frame.beta)
        if math.isfinite(tb):
            self.ewma("R_tb", tb)

    # -- 参照系（判据锚；只记有效帧，理由见 ARTIFACT_REF_WINDOW 注释）---------
    def ref(self, key) -> float | None:
        """参照值：窗口内中位数；样本不足 ARTIFACT_REF_MIN → 回退到 smooth（外部注入的种子）。

        为什么保留回退：历史调用面（verify_v29 B2 等）用 `eng.smooth = {"theta": 100}` 直接
        给判据播种，若无回退，这类调用会"永远检不出伪迹"。回退只在前 5 帧生效——一旦窗口攒够
        样本就切到中位数，不会被 EWMA 的漂移带回去。
        """
        d = self._refs.get(key)
        if d and len(d) >= ARTIFACT_REF_MIN:
            return StateEngine._median(list(d))
        s = self.smooth.get(key)
        return s if s is not None and math.isfinite(s) and s > 0 else None

    def observe(self, frame) -> None:
        """把一个**有效帧**的原始值记入参照窗口（伪迹帧不记账，理由见 ARTIFACT_REF_WINDOW）。

        位置在判定之后（先判后记）：本帧不得参与自己的判据。
        接触无效帧从不记账（signal≥SIGNAL_BAD 时频段值无意义，记进去只会把锚拖向垃圾）。
        """
        for key in BANDS:
            v = getattr(frame, key, None)
            if v is None:
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                continue
            if math.isfinite(v):
                self._refs[key].append(v)
        tb = StateEngine._safe_ratio(frame.theta, frame.beta)
        if math.isfinite(tb):
            self._refs["R_tb"].append(float(tb))

    def reseed(self, frame) -> None:
        """参照系重建（逃生门）：连续 ARTIFACT_REF_RESET 帧全判伪迹时调用。

        此时更可能是"电平换了"（重戴/增益切换/长时间贴片漂移）而不是"连续一分钟都是伪迹"。
        把 smooth 与参照窗口一起对齐到当前电平——只重建 refs 不够：ref() 在样本不足时会回退到
        smooth，若 smooth 还是旧电平，重建后第一帧又会被判伪迹，形成死循环。
        """
        for key in (*BANDS, "R_tb"):
            self._refs[key].clear()
        self.smooth = {}
        self._rejects = 0
        self.reseeds += 1
        self.update(frame)
        self.observe(frame)

    # -- 判定 ---------------------------------------------------------
    def check(self, frame) -> str | None:
        """返回 None（有效）/ "contact"（电极未接触）/ "artifact"（幅值突变或肌电）"""
        self.frames += 1
        sig = getattr(frame, "signal", None)
        if sig is not None:
            try:
                sig = float(sig)
            except (TypeError, ValueError):
                sig = None
            if sig is not None and math.isfinite(sig) and sig >= SIGNAL_BAD:
                self.contact_hits += 1
                return "contact"
        if self.emg_artifact(frame) or self.ratio_artifact(frame):
            self.artifact_hits += 1
            self._rejects += 1
            if self._rejects >= ARTIFACT_REF_RESET:
                self.reseed(frame)
            return "artifact"
        self._rejects = 0
        self.observe(frame)                      # 先判后记：本帧不参与自己的参照
        return None

    def push(self, frame) -> str | None:
        """check + 条件 update（校准回放用；与 process_frame 的更新顺序一致）"""
        reason = self.check(frame)
        if reason is None:
            self.update(frame)
        return reason

    def emg_artifact(self, frame) -> bool:
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

    def ratio_artifact(self, frame) -> bool:
        """单频段偏离参照 ARTIFACT_RATIO(=6) 倍（双向）判伪迹；R_tb 门限独立放宽 15×（v2.8）。

        R_tb 放宽的理由：状态切换（疲劳/分心）R_tb 可达 5-10 倍，6× 会把真实状态切换当伪迹
        丢弃（实测疲劳帧 R_tb=5 vs 参照 0.55 → 9 倍 → 早退导致 I_f 永不积分）。单频段的 6×
        已覆盖硬件伪迹，比值检查只防积分污染，故独立放宽。
        """
        for key in BANDS:
            ref = self.ref(key)
            v = getattr(frame, key, None)
            if ref is None or ref <= 0 or v is None or not math.isfinite(v):
                continue
            r = v / ref
            if r > ARTIFACT_RATIO or r * ARTIFACT_RATIO < 1.0:
                return True
        ref = self.ref("R_tb")
        if ref and ref > 0:
            tb = StateEngine._safe_ratio(frame.theta, frame.beta)
            if math.isfinite(tb) and (tb > ARTIFACT_RATIO_RTB * ref
                                      or tb * ARTIFACT_RATIO_RTB < ref):
                return True
        return False

    # -- 统计（校准报告用）-------------------------------------------
    @property
    def artifact_ratio(self) -> float:
        """伪迹帧占比（不含接触无效帧——两者分开报，处置动作不同）"""
        return self.artifact_hits / self.frames if self.frames else 1.0

    @property
    def contact_ratio(self) -> float:
        return self.contact_hits / self.frames if self.frames else 0.0

class RmssdWindow:
    """RR 缓冲 + RMSSD（运行时与校准**共用**，P3-3 口径统一的落地）。

    元素 = (rr_ms, batch, ts)。batch 相同才能差分：真机 onRR 一次三值是连续心跳，
    跨回调的相邻值可能隔着丢帧/重叠，不能直接相减。校准若另写"帧→帧差分"就是另一套口径
    （同一段 RR 数据能差出 40%+ 的差分对数，且掉搏时量级都不同）。
    """

    def __init__(self, window_s: float = WINDOW_HRV_S):
        self.buf: list[tuple[float, object, float]] = []
        self.window_s = window_s

    def push(self, rr_ms: float, batch=None, ts: float = 0.0) -> bool:
        """入窗并滑动截断；返回是否接受（生理范围外丢弃，R7）"""
        try:
            rr = float(rr_ms)
        except (TypeError, ValueError):
            return False
        if not math.isfinite(rr) or not (RR_MIN_MS <= rr <= RR_MAX_MS):
            return False
        self.buf.append((rr, batch, ts))
        while self.buf and ts - self.buf[0][2] > self.window_s:
            self.buf.pop(0)
        return True

    def trim(self, ts: float) -> None:
        while self.buf and ts - self.buf[0][2] > self.window_s:
            self.buf.pop(0)

    def value(self) -> float:
        return StateEngine._rmssd(self.buf)

    def pairs(self) -> int:
        """有效差分对数（已剔除跨批与异位对）；< RMSSD_MIN_PAIRS 即"窗口未满"。"""
        return len(_rmssd_pairs(self.buf)[0])

    def mean_rr(self) -> float:
        return sum(x[0] for x in self.buf) / len(self.buf) if self.buf else 0.0

def _rmssd_pairs(rr_list) -> tuple[list[float], int]:
    """同批内相邻差分（含异位搏动滤波），返回 (diffs, 被剔除的对数)。"""
    diffs: list[float] = []
    dropped = 0
    for i in range(len(rr_list) - 1):
        r0, r1 = rr_list[i], rr_list[i + 1]
        b0 = r0[1] if len(r0) > 1 else None
        b1 = r1[1] if len(r1) > 1 else None
        if b0 is not None and b1 is not None and b0 != b1:
            continue                      # 跨回调批次：断开差分
        d = r1[0] - r0[0]
        if r0[0] > 0 and abs(d) > RR_DIFF_MAX_RATIO * r0[0]:
            dropped += 1                  # 异位搏动 / 掉搏：不参与 RMSSD
            continue
        diffs.append(d)
    return diffs, dropped

# ---------------------------------------------------------------- 状态引擎

class StateEngine:
    """v3.4 引擎：ArtifactDetector → 特征窗 → z → 门控余量 → 证据 → 仲裁 → 确认 → 反馈"""

    def __init__(self, baseline: dict | None = None, weights: dict | None = None,
                 clock: callable | None = None):
        self.baseline = baseline or {}
        # v2.3 补实：校准基线关键特征必填——A_rel 依赖 alpha，缺省会静默降级为 0 掩盖 relaxed 失效
        # v2.8.2 逻辑修正：delta 加入必填——drowsy（嗜睡）门控依赖 z(δ) 佐证，缺省时
        # z.get("delta",0)=0 恒不过 → 嗜睡静默永不触发（比报错更难排查）
        if self.baseline:
            missing = {"R_tb", "R_ba", "alpha", "A_rel", "HR", "RMSSD_60", "still", "delta"} - set(self.baseline)
            if missing:
                raise ValueError(f"基线缺失关键特征: {missing}（校准协议必填，见 §六；"
                                 f"含 delta——嗜睡 δ 佐证判据依赖，不能省）")
        self._det = ArtifactDetector(self.baseline)   # 伪迹/接触判定单一实现
        self._rr = RmssdWindow()
        self._task_active = False                     # 最近一帧的 ctx.task_active（_margins 用）
        self.eeg_buf: list[Frame] = []                # EEG 特征窗（10s，按 ts 滑动）
        self.gyro_buf = []
        self._gyro_ts = []
        self.W = weights or self._default_weights()
        self._clock = clock or time.time    # 可注入虚拟时钟（mock 演示冷却用，v2.3）
        self.last_decision: Decision | None = None
        self.last_feedback = Feedback()
        self._hit_buf: list[tuple[str, float, float]] = []   # (state, ts, dt) 时间确认
        self._last_ts: float | None = None
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
        self._rr_batch_seq = 0              # v3.4 帧内 rr_batch 的批次号（逐帧自增）
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
        # v3.4 诊断计数（不复用于判据）
        self.diag = {"frames": 0, "contact": 0, "artifact": 0}

    # -- 兼容视图（旧代码直接读写 engine.smooth / engine.rr_buf）------------
    @property
    def smooth(self) -> dict:
        """平滑值表（伪迹判据的参照系）；与 ArtifactDetector 共用同一 dict。"""
        return self._det.smooth

    @smooth.setter
    def smooth(self, v):
        self._det.smooth = dict(v)

    @property
    def rr_buf(self) -> list:
        """RR 缓冲（元素 (rr_ms, batch, ts)）。整体赋值=替换窗口内容（verify 用例会这么用）。"""
        return self._rr.buf

    @rr_buf.setter
    def rr_buf(self, v):
        self._rr.buf = list(v)

    # -- 预处理（薄封装，保留历史调用面：verify_hw/verify_v30 直接调这两个）----
    def _ewma(self, key, x):
        return self._det.ewma(key, x)

    def _artifact(self, frame) -> bool:
        """兼容入口：True=该帧不参与判别（接触无效或伪迹）。需要区分原因时用 `_det.check()`。

        **有副作用**（勿当纯查询用）：内部走 `ArtifactDetector.check()`，会累加帧计数、
        推进参照系（有效帧 observe）、并在连续全拦时触发重建。也就是说它等价于
        `process_frame` 的预处理步骤，对同一帧调两次会把该帧记两遍。
        纯查询请直接用 `self._det.ratio_artifact(frame)` / `emg_artifact(frame)`（两者无副作用，
        但只覆盖幅值判据，不含接触门）。
        """
        return self._det.check(frame) is not None

    def _emg_artifact(self, frame) -> bool:
        return self._det.emg_artifact(frame)

    @staticmethod
    def _safe_ratio(a, b):
        return float("inf") if b <= 0 else a / b

    @staticmethod
    def _artifact_ratio_static(frames: list, refs: dict | None = None) -> float:
        """校准段伪迹率（兼容旧接口；内部改走 ArtifactDetector，与运行同规则）。"""
        det = ArtifactDetector(refs)
        for _, fr in frames:
            det.push(fr)
        return det.artifact_ratio

    def _update_sync(self, frame) -> bool:
        """v2.4/2.5 心流派生同步率（指南 2.2 同步率=attention/meditation 趋势一致性）：
        窗口内双指数相邻帧差分同号帧占比。官方 README 无独立同步率字段，
        此为本实现派生口径——答辩需注明"派生指标，非官方字段"。
        返回是否达标：窗口未满（<SYNC_WIN）或有效差分帧不足（<MIN_SYNC_FRAMES）→ False
        （心流需足够趋势证据，防单帧同向即 1.0 的假阳性）。"""
        # v3.15：eSense=0 表示"没有指数"，不是"指数极低"。把它写进趋势窗会让
        # 60→0 这种伪跃变被当成一次"同向下降"（双指数常同时归零），抬高同步率。
        if frame.attention <= 0 or frame.meditation <= 0:
            self.sync_ratio = 0.0
            return False
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
        """RMSSD（同批内相邻差分；v3.4 加异位搏动滤波与"差分对"下限）。

        元素 (rr_ms, batch[, ts])。batch=None（模拟单值路径）允许跨元素差分（降级口径）。
        返回 nan = 有效差分对 < RMSSD_MIN_PAIRS（"窗口未满"语义，不是 0——0 会被 z 当成
        "极低 HRV" → 单帧假阳性压力，这是 v2.6.1 修过的坑）。
        """
        diffs, _ = _rmssd_pairs(rr_list)
        if len(diffs) < RMSSD_MIN_PAIRS:
            return float("nan")
        return math.sqrt(sum(d * d for d in diffs) / len(diffs))

    def _push_window(self, frame, ts: float):
        """维护 10s EEG / 60s RR / 30s GYRO 缓冲（v2.7 按时间戳滑动截断——
        原按元素计数截断：模拟器每帧 1 间期近似 60s，但真机 onRR 一次 3 间期
        60 元素只有 ~24–27s（#7 缺口），RMSSD 窗偏短压力判别偏敏感）"""
        self.eeg_buf.append(frame)
        # v3.16：不能用 `or ts` 判断缺值——首帧 ts=0 是合法时间戳（回放/相对时间起点），
        # 旧写法会把 0 当缺值，退化成 ts-ts=0，窗口从此永不裁剪（实测 100 帧后仍有 100 帧）。
        while self.eeg_buf:
            first = self.eeg_buf[0].ts
            if ts - (first if first is not None else ts) <= WINDOW_EEG_S:
                break
            self.eeg_buf.pop(0)
        # v3.4：优先整批入窗（同批语义由批次号承载）；只有单值时才走 None 通道
        if frame.rr_batch:
            self._rr_batch_seq += 1
            for rr in frame.rr_batch:
                self._rr.push(rr, self._rr_batch_seq, ts)
        elif frame.rr_ms is not None:
            self._rr.push(frame.rr_ms, None, ts)
        self._rr.trim(ts)
        self.gyro_buf.append(frame.gyro)
        self._gyro_ts.append(ts)
        while self.gyro_buf and ts - self._gyro_ts[0] > WINDOW_GYRO_S:
            self.gyro_buf.pop(0)
            self._gyro_ts.pop(0)

    def push_rr(self, rr_ms: float, batch: int | None = None, ts: float | None = None):
        """真机路径：RR 回调（onRR 一次 3 间期）逐个灌入 HRV 缓冲，与 EEG 帧率解耦。
        batch=同一 onRR 回调的批次号（3 个连续间期同批）——RMSSD 组内差分依据
        （v2.6 修正：跨回调间期不连续，混差分会污染 RMSSD）。模拟器路径走 Frame.rr_ms。
        v3.4：ts 缺省不再调用 _clock()（原实现会让 mock 时钟被每个 RR 推进一次 →
        一次 onRR 三值白推 3s，时间轴被拉长、时间确认窗口失真），改用最近帧 ts。
        """
        t = ts if ts is not None else (self._last_ts if self._last_ts is not None else 0.0)
        self._rr.push(rr_ms, batch, t)

    def _extract(self) -> Feats:
        f = Feats()
        n = len(self.eeg_buf)
        if n == 0:
            return f
        th = sum(x.theta for x in self.eeg_buf) / n
        al = sum(x.alpha for x in self.eeg_buf) / n
        be = sum(x.beta for x in self.eeg_buf) / n
        dl = sum(x.delta for x in self.eeg_buf) / n
        f.theta, f.alpha, f.delta = th, al, dl      # v3.4：δ/θ/α 同窗均值（z 同一时间尺度）
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
            mean_rr = self._rr.mean_rr()
            f.HR = 60000.0 / mean_rr if mean_rr else 0.0
        else:
            f.HR = float("nan")                  # 窗口未满：非有限值，门控跳过（防假饱和）
        f.RMSSD_60 = self._rr.value() if self.rr_buf else float("nan")
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

    def _sigma_of(self, name: str, default: float = 15.0) -> float:
        stat = self.baseline.get(name)
        if not stat:
            return default
        return max(stat.sigma, stat.sigma_min) or default

    # -- v3.4 门控余量 + 证据归一 ---------------------------------------
    def _margins(self, z: dict, frame: Frame) -> dict:
        """每状态"距门控阈值还差多少"（m ≥ 0 即通过），统一到 **σ 当量**。

        为什么需要它：置信度若直接比各状态的 score，就是在比不同单位（stress 是 z 线性组合，
        量级 1–3；anxiety 是官方指数余量，量级 10–20）。改成余量后，8 个状态同一把尺子，
        才能回答"这个判定到底有多强 / 与对手差多少"。

        **量纲纪律（勿含糊）**：8 项里绝大多数是真 z 的 σ 单位（lt/gt 直接减阈值），
        但有两处是"约定标度换算"而非统计 σ，答辩须如实说明：
          · 焦虑/心流的绝对常模分支：(40−att)/σ_att —— 把"指数分差"除以该指数的个体 σ，
            得到的是"该指数偏离多少个个体标准差"，可当 σ 当量；
          · 疲劳的 I_f 项：(I_f/IF_HOLD_S − 1) —— 无量纲积分比值，按"超阈 1 倍 = 1σ 当量"
            约定标度。它是**人定的当量**，来源是 IF_HOLD_S 的等效秒定义，不是统计分布。
        """
        def lt(name, th):      # 门控要求 z < th
            v = z.get(name)
            return None if v is None or not math.isfinite(v) else th - v

        def gt(name, th):      # 门控要求 z > th
            v = z.get(name)
            return None if v is None or not math.isfinite(v) else v - th

        def mn(*vals):
            vs = [v for v in vals if v is not None]
            return min(vs) if vs else 0.0

        def mx(*vals):
            vs = [v for v in vals if v is not None]
            return max(vs) if vs else 0.0

        sa, sm = self._sigma_of("att"), self._sigma_of("med")
        # 焦虑/心流的"绝对常模"分支要折成 σ 单位才能与 z 分支比：指数 1σ ≈ σ_att 分
        anx_abs = mn((ANX_ATT_MAX - frame.attention) / sa, (ANX_MED_MAX - frame.meditation) / sm)
        # v3.4.1 修：焦虑 z 分支是 **z 低于阈值**（za < ANX_Z），余量必须用 lt 而非 gt。
        # 用 gt 会得到 ANX_Z−za 的相反数——z=−1.8（已满足 za<−1.5）算出 −0.3 → 余量负、
        # 证据 0、conf 掉到 CONF_MIN（verify_v26 V5 实测：该提醒的焦虑被判弱判别静默）。
        anx_z = mn(lt("att_z", ANX_Z), lt("med_z", ANX_Z))
        flow_abs = mn((frame.attention - FLOW_ATT_MIN) / sa, (frame.meditation - FLOW_MED_MIN) / sm)
        flow_z = mn(gt("att_z", FLOW_Z), gt("med_z", FLOW_Z))
        task = bool(self._task_active)
        m = {
            "focus": mn(lt("R_tb", Z_CONC),
                        gt("RMSSD_60", Z_RMSSD_NO_STRESS),
                        lt("still", Z_STILL_CAP)),
            "distracted": gt("R_tb", Z_DIST) if task else 0.0,
            "relaxed": mn(gt("A_rel", Z_RELAX_A)),
            "stress": mn(lt("RMSSD_60", Z_STRESS), lt("still", Z_STILL_CAP)),
            # 焦虑=双低；两条分支（绝对常模 / 个体 z）任一成立即可，故取 max
            "anxiety": mx(anx_abs, anx_z),
            # 心流=双高 + 稳定（flow_hold/sync 是二值前提，不参与余量；未达标时门控本来就 False）
            "flow": mn(mx(flow_abs, flow_z), lt("R_tb", Z_DIST)),
            # 疲劳=BAR 下降 且 I_f 累积 且 非应激；I_f 的余量用"相对阈值的倍数"折算
            "fatigue": mn(lt("R_ba", Z_FATIGUE_BAR),
                          (self._theta_int / IF_HOLD_S - 1.0) if IF_HOLD_S else 0.0,
                          gt("RMSSD_60", Z_STRESS)),
            "drowsy": mn(gt("TAR", Z_DROWSY_TAR), gt("delta", Z_DROWSY_DELTA)),
        }
        return m

    # -- 评分 + 门控（v2.3：专注排除法；分心 task_active；压力 still 平静门控）-------------
    def _scores(self, feats: Feats, ctx: dict | None = None, frame: Frame | None = None) -> (dict, dict):
        W, z = self.W, {}
        self._task_active = bool((ctx or {}).get("task_active"))
        for name in ("R_tb", "R_ba", "TAR", "A_rel", "HR", "RMSSD_60", "still", "delta"):
            stat = self.baseline.get(name)
            if not stat:
                continue
            v = getattr(feats, name, float("nan"))
            z[name] = self._z(v, stat) if (v is not None and math.isfinite(v)) else float("nan")
        # v2.5 焦虑/心流 z 判据：官方指数也做个体 z（个体差异大，绝对常模 40 会漏报"相对自身显著下滑"）
        za = zm = float("nan")
        if frame is not None and "att" in self.baseline and "med" in self.baseline:
            za = self._z(frame.attention, self.baseline["att"])
            zm = self._z(frame.meditation, self.baseline["med"])
            z["att_z"], z["med_z"] = za, zm
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
            # v3.15：eSense 必须先"有效"。TGAM 在信号差或尚未算出指数时会同时给
            # att=med=0——这不是"极度焦虑"，是"没有指数"。原实现会让它 0.95 置信度
            # 命中 anxiety（优先级仅次于 stress）：实测 20 帧全 0 → anxiety 分数 80。
            # 只在本帧两个指数都 >0 时才允许走焦虑判据（含 z 分支）。
            "anxiety":    (frame is not None and frame.attention > 0 and frame.meditation > 0
                           and ((frame.attention < ANX_ATT_MAX
                                 and frame.meditation < ANX_MED_MAX)
                                or (math.isfinite(za) and math.isfinite(zm)
                                    and za < ANX_Z and zm < ANX_Z))),
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
        scores = ScoreMap({
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
        })
        for s in list(scores):
            # 门控不过 或 关键特征无效（nan）→ 不参与竞争
            if not gates[s] or not math.isfinite(scores[s]):
                scores[s] = float("-inf")
        # v3.4：门控余量 → 证据（仅对已过门控的状态有意义；未过门控的余量为负，证据记 0）
        if frame is not None:
            m = self._margins(z, frame)
        else:                                       # 无官方帧（内部单测）：不产证据，仲裁走兼容路径
            m = {}
        scores.margins = m
        scores.evidence = {s: (0.0 if not math.isfinite(scores[s])
                               else max(0.0, min(1.0, m.get(s, 0.0) / EVID_REF)))
                           for s in scores}
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
        # 语义：门控=资格、优先级=谁赢、**证据=有多确定**。
        #
        # v3.4 置信度重设计（原式 (best−second)/(|best|+|second|) 已废弃）：
        #   旧式跨状态比的是"不同量纲的分数"——stress 为 z 线性组合（量级 1–3），
        #   anxiety 为官方指数余量（量级 10–20，att/med 各差 10 分即 20 分）。实测：
        #   RMSSD z=−2 的强应激(score 2.0) + att/med=30 的焦虑(score 20) 并存时，
        #   conf = 0.6+0.35·(2−20)/22 = 0.31 → 截到下限 0.60 → 被标"弱判别仅记录"，
        #   强应激反而拿不到干预。改法：用同量纲的**证据比** e_best/(e_best+e_second)。
        #   同一场景 conf 0.60 → 0.75（越过 WEAK_CONF，恢复提醒）。
        # 语义边界（答辩须说明）：conf 由**两个独立维度**相乘得到——
        #   排他性 sep = e_best/(e_best+e_second)：这个结论相对竞争对手有多独占；
        #   绝对强度 strength = clamp(m_best/EVID_FULL)：它本身离门控阈值有多远。
        #   v3.4 只做了 sep，导致单状态（无对手）时 sep 恒 1 → conf 恒 CONF_MAX，
        #   超阈 0.001σ 与 5σ 无法区分、WEAK_CONF 永不生效（v3.4.1 修）。
        #   两者相乘：**独占**且**够强**才给高置信度。
        # 仲裁仍**只按 PRIORITY**（不做"谁分高谁赢"）——见 verify_v30 K 用例锁死的语义。
        best = next((s for s in PRIORITY if s in finite), max(finite, key=finite.get))
        ev = getattr(scores, "evidence", None) or {}
        if ev:
            eb = max(0.0, ev.get(best, 0.0))
            e2 = max([max(0.0, ev.get(s, 0.0)) for s in finite if s != best], default=0.0)
            sep = eb / (eb + e2) if (eb + e2) > 0 else 0.0
            # 强度优先取真实余量（σ 单位）；外部只给 evidence 时退化为 e 本身
            m_best = (getattr(scores, "margins", None) or {}).get(best)
            if m_best is None or not math.isfinite(m_best):
                strength = min(1.0, eb)
            else:
                strength = max(0.0, min(1.0, m_best / EVID_FULL))
            conf = CONF_MIN + (CONF_MAX - CONF_MIN) * sep * strength
            return best, max(CONF_MIN, min(CONF_MAX, conf))
        # 兼容路径：调用方只给了普通 dict（无证据表）——单测/UI 重算会这样用，
        # 退回"分数差比"。**主链路不走这里**，勿据此判断引擎行为。
        second = sorted((v for s, v in finite.items() if s != best), reverse=True)
        second_v = second[0] if second else 0.0
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
        # v3.14：上限 500，与 engine.js 同步。该列表当前无消费方（冷静度用 total/impulsive），
        # 保留是为将来导出决策序列；不设上限会在长会话里无限增长。
        if len(self.log["decisions"]) > 500:
            del self.log["decisions"][:-500]

    @property
    def calmness(self):
        # v3.16：零决策时返回 nan（"没有样本"），不返回 1.0（"满分"）。
        # 界面早已按 log.total 判空显示"—"，但 Python 侧/导出/报告仍会拿到 1.00，
        # 与"尚无决策"自相矛盾。null 语义必须从引擎开始，而不是只靠 UI 遮。
        return (1.0 - (self.log["impulsive"] / self.log["total"])
                if self.log["total"] else float("nan"))

    def _confirm(self, state: str, ts: float) -> str:
        """v2.7 时间确认（#8 缺口落地）：候选状态在近 DEBOUNCE_S 窗口内命中率 ≥HIT_RATIO
        才确认输出——单帧抖动不再瞬时翻转状态（原"单次即判"，偶发伪帧/瞬时特征波动
        会触发错误反馈，金融决策场景代价高）。

        v3.4：命中率按**时长**而非帧数计。真机帧间隔 1.2–1.5s 且抖动/掉帧（SDK 回调不定时），
        帧数占比与时间占比不再等价：0.1s 快帧突发下 9 帧只占 0.9s，"60% 命中"的帧数版会
        在 0.9s 就翻转状态；时间版按真实时长计，确认点恒在 DEBOUNCE_S·HIT_RATIO 附近。

        实现要点：条目存 (state, ts, prev_ts)，查询时把区间 [prev_ts, ts] **裁剪到当前窗口**再累加。
        若直接累加建条目时的 dt，越窗的旧条目会带着过期权重长期占分母（实测会推迟确认）。
        """
        prev = self._last_ts
        self._last_ts = ts
        if state == "neutral":
            return state
        self._hit_buf.append((state, ts, prev if prev is not None else ts))
        lo = ts - DEBOUNCE_S
        while self._hit_buf and self._hit_buf[0][1] < lo:
            self._hit_buf.pop(0)
        if len(self._hit_buf) < 2:
            return state
        total = hits = 0.0
        for st, t1, t0 in self._hit_buf:
            w = min(t1, ts) - max(t0, lo)          # 该条目落在窗口内的时长
            if w <= 0:
                continue
            total += w
            if st == state:
                hits += w
        if total <= 0:
            return state
        return state if hits / total >= HIT_RATIO else "neutral"

    # -- 主入口 ---------------------------------------------------------
    def process_frame(self, frame: Frame, ctx: dict | None = None) -> (Decision, Feedback):
        # v2.7 时间戳：帧带 ts 用之（真机），否则内部时钟补（mock 每帧 +1s）；
        # 回写 frame.ts——缓冲内帧必须携带真实时间戳，窗口滑动截断依赖它
        # （原不回写：frame.ts=None → 窗口判断 ts-(None or ts)=0 永不截断 → 缓冲无限累积，
        # 特征恒为预热均值 → 状态切换后门控永不触发）
        # v3.16：ts=0 是合法时间戳（回放/相对时间起点），只有 None / 非有限 / 负数才回退时钟。
        # 旧写法 `frame.ts > 0` 会把首帧 0 换成墙钟（1.7e9），后续 1.2/2.4 永远"在过去"，
        # 窗口裁剪与时间确认全部失效——且不报错。
        try:
            ts = float(frame.ts) if frame.ts is not None else None
        except (TypeError, ValueError):
            ts = None
        if ts is None or not math.isfinite(ts) or ts < 0:
            ts = self._clock()
        frame.ts = ts
        self.diag["frames"] += 1
        self._task_active = bool((ctx or {}).get("task_active"))
        # 1 预处理：接触质量 + 伪迹（v3.4 合并为单一实现）。伪迹帧不更新平滑（v2.1）
        reason = self._det.check(frame)
        if reason:
            self.diag[reason] += 1
            # v2.8.2 逻辑修正：伪迹帧推进疲劳积分时间戳但不积分——
            # 否则恢复后第一帧 dt 把伪迹期间（可能数十秒）的 θ 主导时间一次性计入 I_f（跳增）
            if self._theta_ref:
                self._last_theta_ts = ts
            self._last_ts = ts
            # v2.6.2 语义对齐：无判别返回 neutral 而非 relaxed（首帧即伪迹时，
            # "数据不足/无判别"如实呈现，不伪装成放松；已有判别时保持上帧状态）
            prev = self.last_decision or Decision("neutral", CONF_MIN)
            return Decision(prev.state, prev.conf, prev.scores, prev.gates,
                            prev.margins, prev.evidence, reason=reason), self.last_feedback
        self._det.update(frame)
        self._push_window(frame, ts)
        # 2 特征 → 3 z-score → 4 余量/证据 → 5 评分 → 6 仲裁（ctx: task_active 等任务态注入，v2.3）
        feats = self._extract()
        # v2.9.1 疲劳积分："幅度加权 + 可恢复"（兼顾可达性与物理合理性）：
        #   rate = (θ_norm−1.2)/IF_REF_EXCESS 截断到 [0, IF_RATE_MAX]，I_f += rate·dt（等效秒）；
        #   θ 回落至阈下时 I_f 按半衰期 IF_RECOVER_HALF_S 衰减——疲劳是"暴露累积+恢复"，
        #   不能单调锁存（v2.9 纯时长版实测 θ 回落 1200s 后 I_f 仍 399，永久锁存会误报后续所有疲劳）。
        #   单位统一：I_f=300 等价"θ 超阈 0.3 倍持续 300s"，与 IF_HOLD_S 语义一致。
        # v3.4：分子改用平滑 θ（整流前先平滑，压掉噪声正偏置，见模块头 §2）。
        if self._theta_ref and self._theta_ref > 0:
            theta_s = self.smooth.get("theta", frame.theta)
            theta_norm = theta_s / self._theta_ref
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
        dec = Decision(state=state, conf=conf, scores=scores, gates=gates,
                       margins=scores.margins, evidence=scores.evidence)
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
            # v3.4：log_flag 现已被 calibrate_from_frames 尊重（置 True 时按 log 空间估 μ/σ）。
            # 默认仍 False——Z_STRESS=-1.5 是在线性空间标定的文献值，换空间须重标（见设计稿 §四）。
            "RMSSD_60": BaselineStats(mu=15.0, sigma=5.0, sigma_min=1.0, log_flag=False),
            "still": BaselineStats(mu=0.5, sigma=0.2, sigma_min=0.05),
            "delta": BaselineStats(mu=100.0, sigma=20.0, sigma_min=5.0),  # v2.8 嗜睡 δ 佐证基线
            # v2.5 焦虑 z 判据基线：官方指数个体 μ/σ（常模初值 mu=60；真机校准 B 段更新，
            # 未校准前绝对常模路径仍可用——att/med 不加入必填校验，缺省走绝对判据）
            "att": BaselineStats(mu=60.0, sigma=15.0, sigma_min=3.0),
            "med": BaselineStats(mu=60.0, sigma=15.0, sigma_min=3.0),
            # v2.8.5b：补 theta_ref（对齐 JS defaultBaseline=55；mock focus θ≈55，B 段 θ 中位数）。
            # 此前缺失 → _theta_ref=None → I_f 恒不积分 → 默认基线疲劳状态死锁
            # （output_demo/verify 显式注入掩盖）；真机由 calibrate_from_frames B 段覆盖
            "theta_ref": 55.0,
            # v3.6 说明：β/γ 参照**故意不在默认基线里给值**，与 engine.js 的
            # `beta_ref: null, gamma_ref: null` 保持同一语义——"未校准时肌电门自动失效"。
            # （真实校准由 calibrate_from_frames 从 B 段中位数填入，两侧一致。）
            # 历史缺陷：Python 侧连 calibrate_from_frames 都从未填过这两个字段，
            # 导致肌电门在真机上也是死代码；2026-11-10 已修（见 calibrate_from_frames 2b 段）。
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
        v3.4：log_flag 基线在 log 空间更新 μ（原实现把线性值塞进 log 空间 μ，量纲错配）。
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
            if stat.log_flag:
                if v <= 0:
                    continue
                v = math.log(v)
            new_mu = stat.mu + ADAPT_ALPHA * (v - stat.mu)
            new_mu = max(stat.mu - ADAPT_MAX_DRIFT * stat.sigma,
                         min(stat.mu + ADAPT_MAX_DRIFT * stat.sigma, new_mu))
            stat.mu = new_mu

    # -- v2.8 §六 校准协议代码化 ----------------------------------------
    @staticmethod
    def _median(xs):
        xs = sorted(x for x in xs if x is not None)
        if not xs:
            return None
        n = len(xs)
        return xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])

    @staticmethod
    def _robust_stats(vals: list[float], stat: BaselineStats) -> tuple[float, float]:
        """稳健 μ/σ：中位数 + 半 IQR×1.4826。

        口径说明（v3.4 正名）：原注释写"MAD×1.4826"，实际算的是
        max(med−q1, q3−med)×1.4826 = (IQR/2)×1.4826 = IQR/1.349（对称分布下），
        **数值上正是正态一致的 IQR 稳健 σ 估计**，只是名字写错。此处保留该估计并改名，
        避免答辩时被追问"你的 MAD 为什么不是中位绝对偏差"。
        log_flag=True 时在 log 空间估 μ/σ（与 _z 的 log 变换同空间）。
        """
        if not vals:
            return stat.mu, stat.sigma
        if stat.log_flag:
            vals = [math.log(v) for v in vals if v > 0]
            if not vals:
                return stat.mu, stat.sigma
        vals = sorted(vals)
        n = len(vals)
        med = vals[n // 2] if n % 2 else 0.5 * (vals[n // 2 - 1] + vals[n // 2])
        q1 = vals[n // 4]
        q3 = vals[(3 * n) // 4]
        sigma = max((q3 - q1) / 2.0 * 1.4826, stat.sigma_min)
        return med, sigma

    @staticmethod
    def _windowed_series(frames: list["Frame"], window_s: float, fn) -> list[float]:
        """按运行时同一**时长窗**生成特征序列（v3.16）。

        为什么必须这样做：运行时 `_extract` 用 10s 窗均值算 R_tb/R_ba/TAR/A_rel，而校准
        原实现用逐帧值估 μ/σ。同一段 D2_B 实测 σ 比值 2.3–3.4×——运行时 |z| 被压缩同样倍数，
        `Z_CONC=-1.0` 在逐帧空间是 16% 分位、窗口空间只剩 0.13%（D2_B focus 0/122）。
        基线与运行时必须是同一把尺子；这里只复刻"窗口内均值"这一步，不做别的平滑。
        """
        out: list[float] = []
        j = 0
        for i, fr in enumerate(frames):
            ts = fr.ts if fr.ts is not None and math.isfinite(fr.ts) else 0.0
            while j < i:
                t0 = frames[j].ts if frames[j].ts is not None and math.isfinite(frames[j].ts) else ts
                if ts - t0 <= window_s:
                    break
                j += 1
            sub = frames[j:i + 1]
            if not sub:
                continue
            out.append(fn(sub))
        return out

    @staticmethod
    def _window_means(frames: list["Frame"], window_s: float) -> list[dict]:
        """窗口内 θ/α/β/δ 均值序列（R_tb/R_ba/TAR/A_rel 的共同原料）。"""
        def mean_of(sub, attr):
            vals = [getattr(x, attr) for x in sub
                    if math.isfinite(getattr(x, attr, float("nan")))]
            return sum(vals) / len(vals) if vals else float("nan")

        return StateEngine._windowed_series(
            frames, window_s,
            lambda sub: {"theta": mean_of(sub, "theta"), "alpha": mean_of(sub, "alpha"),
                         "beta": mean_of(sub, "beta"), "delta": mean_of(sub, "delta")})

    @staticmethod
    def calibrate_from_frames(frames: list[tuple[str, Frame]]) -> dict:
        """真机校准协议（§六 A/B/C 段）代码化：输入 (段标识, Frame) 列表，输出完整基线。

        段约定（与设计文档 §六 对齐）：
          "A" 闭眼静息（2min）：θ/δ 静息参考（theta_rest / delta_rest，**仅参考不作判据参照**）
          "B" 任务前睁眼（30s）：R_tb/R_ba/TAR/A_rel(α)/HR/RMSSD/still/δ/att/med 基线（z 参照）
          "C" 放松（2min）：A_rel/RMSSD 放松锚点（真机阈值校准预留；MVP 不直接改阈值）
        统计：μ/σ 用稳健估计（中位数 + 半 IQR 折算，抗单次伪迹/眨眼尖峰），
        σ 下限取对应默认基线的 sigma_min（防单段样本过齐致 σ→0）。

        v3.4 三处口径修正（都有审计数字，见 _audit_engine.py / verify_engine.py）：
        1) **δ 基线改取 B 段**。旧实现只从 A 段取 δ（闭眼 δ 系统性偏高，实测 μ=149.7），
           而判别发生在睁眼任务（δ≈100–120）→ z(δ) 恒 −2 ~ −3.4 → 嗜睡合取永久不成立。
           这与 v2.9.2 修的"theta_ref 误取 A 段"是同一失效模式，只是换了个特征重新引入。
        2) **RMSSD 走 RmssdWindow**（与运行时同一函数、同一批次语义）。旧实现按"帧→帧"差分，
           与运行的"批内差分"是两套估计量（同一段 RR 数据差分对数可差 40%+，掉搏时量级都不同）。
           → 这就是遗留的 P3-3，此轮闭合。
        3) **alpha 基线先算、A_rel 再算**（显式两段）。旧实现靠特征名列表的迭代顺序隐式保证，
           一旦有人重排列顺序，A_rel 会静默用默认 alpha=100 折算 → 放松判据整体偏移。
        另：伪迹/接触无效帧在统计前剔除（与运行同一 ArtifactDetector），避免眨眼尖峰抬高 σ。

        返回 dict 与 calibrate_from_mock 同构，可直接喂 StateEngine(baseline=...)。
        """
        from collections import defaultdict
        seg: dict[str, list] = defaultdict(list)
        for tag, fr in frames:
            seg[tag].append(fr)
        base = StateEngine.calibrate_from_mock([])   # 以默认基线的 sigma_min/结构为模板

        # ---- 0) 伪迹/接触剔除（按段内序列回放，与 process_frame 同一判定顺序）----
        # 这里**故意**不传 baseline：此刻还没有 B 段参照，传了也只会拿到空 dict；
        # 代价是 β+γ 肌电门在此阶段必然关闭，只跑"接触门 + 幅值突变判据"。
        # 这是结构性的（参照本来就要先算出来才能用），不是遗漏——报告阶段
        # （calibration_report）拿到 base 后才会让肌电门生效，故两处伪迹率在
        # ARTIFACT_EMG_GATE=True 时可能有细微差异，属预期。
        det = ArtifactDetector()
        clean: dict[str, list] = {}
        for tag, fs in seg.items():
            keep = []
            for fr in fs:
                if det.push(fr) is None:
                    keep.append(fr)
            clean[tag] = keep
        rep_det = det          # 报告里复用同一实例的计数（不重复回放）

        # ---- 1) alpha 基线（A_rel 的分母来源，必须先算）----
        b_clean = clean.get("B", [])
        b_means = StateEngine._window_means(b_clean, WINDOW_EEG_S)
        b_alphas = [m["alpha"] for m in b_means if math.isfinite(m["alpha"])]
        if len(b_alphas) >= 10:
            base["alpha"].mu, base["alpha"].sigma = StateEngine._robust_stats(b_alphas, base["alpha"])
        alpha_mu = base["alpha"].mu

        # ---- 2) B 段：判据参照系（含 δ —— 必须是任务参照，不能用闭眼静息）----
        # v3.16：band 派生特征改用**与运行时相同的 10s 窗均值序列**估 μ/σ；
        # att/med 保持逐帧（运行时也是逐帧），still 用 30s 窗（运行时 WINDOW_GYRO_S），
        # HR 用 60s RR 均值（运行时同窗）。这样 z 的分子分母才是同一时间尺度。
        series: dict[str, list[float]] = {
            "R_tb": [StateEngine._safe_ratio(m["theta"], m["beta"]) for m in b_means],
            "R_ba": [StateEngine._safe_ratio(m["beta"], m["theta"] + m["alpha"]) for m in b_means],
            "TAR":  [StateEngine._safe_ratio(m["theta"], m["alpha"]) for m in b_means],
            "A_rel": [((m["alpha"] - alpha_mu) / alpha_mu) if alpha_mu else 0.0 for m in b_means],
            "alpha": b_alphas,
            "delta": [m["delta"] for m in b_means],
            "still": StateEngine._windowed_series(
                b_clean, WINDOW_GYRO_S,
                lambda sub: sum(abs(fr.gyro[0]) + abs(fr.gyro[1]) + abs(fr.gyro[2])
                                for fr in sub) / len(sub)),
            "att": [fr.attention for fr in b_clean],
            "med": [fr.meditation for fr in b_clean],
        }
        rr_pts = []
        for fr in b_clean:
            fts = fr.ts if fr.ts is not None else 0.0
            if fr.rr_batch:
                rr_pts.extend((float(r), fts) for r in fr.rr_batch)
            elif fr.rr_ms:
                rr_pts.append((float(fr.rr_ms), fts))
        hr_series = []
        for fr in b_clean:
            fts = fr.ts if fr.ts is not None else 0.0
            vals = [rr for rr, t in rr_pts if t <= fts + 1e-9 and fts - t <= WINDOW_HRV_S]
            hr_series.append(60000.0 / (sum(vals) / len(vals)) if vals else float("nan"))
        series["HR"] = hr_series

        for name, vals in series.items():
            stat = base.get(name)
            if stat is None:
                continue
            vals = [v for v in vals if v is not None and math.isfinite(v)]
            # v3.15：eSense=0 是"没有指数"，不能进 att/med 的 μ/σ——否则 B 段里只要夹
            # 几帧指数缺失，基线下移，之后正常帧的 z 被系统性抬高（焦虑误判的反方向）。
            if name in ("att", "med"):
                vals = [v for v in vals if v > 0]
            if not vals or (name != "still" and len(vals) < 10):
                continue
            stat.mu, stat.sigma = StateEngine._robust_stats(vals, stat)

        # ---- 2b) β/γ 参照（v3.6 修复：这两个字段**从未被写入过**）----
        # 影响面：`ArtifactDetector.emg_artifact()` 读 self.baseline["beta_ref"/"gamma_ref"]，
        # 读不到就 `return False` —— 于是 **ARTIFACT_EMG_GATE 是一段从未生效的死代码**，
        # 肌电门无论开关都拦不住任何帧。V4（说话肌电）因此永远判不出来：
        # 2026-11-10 用 20× 持续肌电实测，8×/15× 门与 EMG 门拦截率都是 0%
        # （比值门只能抓突变，持续肌电会被滑动参照系"适应"掉，这正是本门存在的理由）。
        # 参照取 B 段 clean 帧中位数（与 theta_ref 同段同口径）。
        if len(clean.get("B", [])) >= 10:
            b_betas = [fr.beta for fr in clean["B"] if math.isfinite(fr.beta)]
            b_gammas = [fr.gamma for fr in clean["B"] if math.isfinite(fr.gamma)]
            if len(b_betas) >= 10:
                base["beta_ref"] = StateEngine._median(b_betas)
            if len(b_gammas) >= 10:
                base["gamma_ref"] = StateEngine._median(b_gammas)

        # ---- 3) B 段 RMSSD：与运行时同一窗口/差分实现（含 rr_batch 批次语义）----
        rr_win = RmssdWindow(window_s=float("inf"))     # 校准段整体当一个窗口
        bi = 0
        for fr in clean.get("B", []):
            if fr.rr_batch:
                bi += 1
                for rr in fr.rr_batch:
                    rr_win.push(rr, bi, 0.0)            # 同一帧的批内三值同批
            elif fr.rr_ms is not None:
                rr_win.push(fr.rr_ms, None, 0.0)        # 单值路径：batch=None（与运行降级口径一致）
        rmssd = rr_win.value()
        rmssd_pairs = rr_win.pairs()   # 有效差分对数（已剔跨批/异位）
        if math.isfinite(rmssd):
            base["RMSSD_60"].mu = rmssd
            # σ：对"逐点 RMSSD"做稳健估计（滑窗序列），而非用差分对本身的离散度
            series = []
            sub = RmssdWindow(window_s=float("inf"))
            for elem in rr_win.buf:
                sub.push(elem[0], elem[1], 0.0)
                v = sub.value()
                if math.isfinite(v):
                    series.append(v)
            if len(series) >= 10:
                _, sig = StateEngine._robust_stats(series, base["RMSSD_60"])
                base["RMSSD_60"].sigma = sig

        # ---- 4) θ_ref（疲劳 I_f 冻结基线）必须取 B 段 ----
        # v2.9.2 修正：A 段是闭眼静息，θ 系统性高于睁眼任务（测试用例自身即 A=130 / B=100）。
        # 原实现取 A 段 → 主任务 θ 只有 A 段的 0.5~0.8 倍，θ_norm 恒 <1，I_f 永不积分。
        b_ths = [fr.theta for fr in clean.get("B", []) if math.isfinite(fr.theta)]
        a_ths = [fr.theta for fr in clean.get("A", []) if math.isfinite(fr.theta)]
        if len(b_ths) >= 10:
            base["theta_ref"] = StateEngine._median(b_ths)          # 首选：B 段任务参照
            base["theta_ref_src"] = "B"
        elif len(a_ths) >= 10:
            base["theta_ref"] = StateEngine._median(a_ths)          # 回退：A 段（仅在 B 缺失时，附告警）
            base["theta_ref_src"] = "A-fallback"
        else:
            base["theta_ref_src"] = "default"
        # A 段仅存静息参考（不参与判据）
        if a_ths:
            base["theta_rest"] = StateEngine._median(a_ths)
        a_dls = [fr.delta for fr in clean.get("A", []) if math.isfinite(fr.delta)]
        if a_dls:
            base["delta_rest"] = StateEngine._median(a_dls)
        base["delta_src"] = "B" if len([fr for fr in clean.get("B", [])]) >= 10 else "default"

        # ---- 5) β/γ 参照（肌电门用；与 θ_ref 同段，保证参照系一致）----
        for band in ("beta", "gamma"):
            b_vals = [getattr(fr, band) for fr in clean.get("B", [])
                      if math.isfinite(getattr(fr, band, float("nan")))]
            a_vals = [getattr(fr, band) for fr in clean.get("A", [])
                      if math.isfinite(getattr(fr, band, float("nan")))]
            v = StateEngine._median(b_vals) if len(b_vals) >= 10 else (
                StateEngine._median(a_vals) if len(a_vals) >= 10 else None)
            if v:
                base[f"{band}_ref"] = v

        # ---- 6) 校准质量元数据（报告用，不参与判别）----
        base["calib_meta"] = {
            "artifact_ratio": rep_det.artifact_ratio,
            "contact_ratio": rep_det.contact_ratio,
            "rmssd_pairs": rmssd_pairs,
            "segments": {k: len(v) for k, v in clean.items()},
            # v3.16：基线是用哪种估计量算的（window10=与运行时同款 10s 窗均值）。
            # 旧基线没有这个字段，读到时按 frame（逐帧）解释——便于追溯"为什么同一段数据
            # 换版本后 z 变大"。
            "estimator": "window10",
        }
        return base

    # -- v2.9.2 校准质量自检（§六 第 5 步：校准失败须提示重戴，不得静默进入判别）--------
    @staticmethod
    def calibration_report(frames: list[tuple[str, "Frame"]],
                          base: dict | None = None) -> dict:
        """校准质量报告：段样本量 / θ_ref 与 δ 来源 / C 段放松锚点可分性 / 伪迹率。

        原实现缺陷：三段中 C 段（放松锚点）无任何消费者，且段样本不足时静默保留默认基线，
        违反设计文档 §六 第 5 条"校准失败提示重戴，不静默进入判别"。此报告补上可见性：
        - artifact_ratio：伪迹帧占比（>30% 判失败，§六 标准）
        - contact_ratio：接触不合格帧占比（v3.4 新增，处置动作不同：查佩戴 vs 查动作）
        - c_anchor_z：C 段 A_rel 相对 B 段基线的 z（放松态是否可判；< Z_RELAX_A 说明场景无效）
        - rmssd_pairs：RMSSD 有效差分对（<RMSSD_MIN_PAIRS=6 说明 HRV 特征不可用）
        - warnings：可直接展示给操作者的提示文本
        """
        base = base or StateEngine.calibrate_from_frames(frames)
        seg: dict[str, list] = {"A": [], "B": [], "C": []}
        for tag, fr in frames:
            if tag in seg:
                seg[tag].append(fr)
        counts = {k: len(v) for k, v in seg.items()}
        meta = base.get("calib_meta", {})
        clean_counts = meta.get("segments", counts)

        # 伪迹/接触率：**优先复用** calibrate_from_frames 回放时记下的计数（v3.4.1）。
        # 旧实现此处再回放一遍，等于对同一批帧跑第二个 detector——两侧若配置不同
        # （一个带 base 进肌电门、一个不带），会给出两个互相矛盾的"伪迹率"。
        if "artifact_ratio" in meta:
            artifact_ratio = meta["artifact_ratio"]
            contact_ratio = meta.get("contact_ratio", 0.0)
        else:                                    # 外部传入旧基线（无 calib_meta）才回放
            det = ArtifactDetector(base)
            for _, fr in frames:
                det.push(fr)
            artifact_ratio, contact_ratio = det.artifact_ratio, det.contact_ratio

        # C 段放松锚点：以 B 段 alpha 基线折算 A_rel，再看 z 是否足以越过放松门控
        c_z = None
        alpha_stat = base.get("alpha")
        a_rel_stat = base.get("A_rel")
        if alpha_stat and a_rel_stat and alpha_stat.mu > 0 and counts["C"] >= 10:
            # v3.16：C 段锚点也用运行时同款 10s 窗均值（原实现取逐帧 α 的中位数，
            # 与 A_rel 基线的 σ 不是同一时间尺度，c_anchor_z 会被系统性放大）。
            c_frames = [fr for fr in seg["C"]
                        if math.isfinite(fr.alpha)
                        and (fr.signal is None or fr.signal < SIGNAL_BAD)]
            c_vals = StateEngine._windowed_series(
                c_frames, WINDOW_EEG_S,
                lambda sub: ((sum(fr.alpha for fr in sub) / len(sub)) - alpha_stat.mu)
                            / alpha_stat.mu)
            c_vals = [v for v in c_vals if math.isfinite(v)]
            if c_vals:
                c_med = StateEngine._median(c_vals)
                sigma = max(a_rel_stat.sigma, a_rel_stat.sigma_min)
                c_z = (c_med - a_rel_stat.mu) / sigma

        warnings: list[str] = []
        if counts["B"] < 10:
            warnings.append(f"B 段样本不足（{counts['B']} 帧 <10），R_tb/R_ba/TAR/θ_ref/δ 沿用默认值，"
                            f"校准无效，请重做 B 段（睁眼、静坐、不看屏幕强光）")
        if counts["A"] < 10:
            warnings.append(f"A 段样本不足（{counts['A']} 帧 <10），静息参考缺失（theta_rest 不可用）")
        if counts["C"] < 10:
            warnings.append(f"C 段（放松锚点）样本不足（{counts['C']} 帧 <10），无法校验放松态可判性")
        if artifact_ratio > CALIB_ARTIFACT_MAX:
            warnings.append(f"伪迹帧占比 {artifact_ratio:.0%} > {CALIB_ARTIFACT_MAX:.0%}，"
                            f"信号质量不合格，请检查佩戴（§六 第 5 条）")
        if contact_ratio > 0.10:
            warnings.append(f"接触无效帧占比 {contact_ratio:.0%}（signal≥{SIGNAL_BAD}）> 10%，"
                            f"电极贴合不良：先处理额贴/耳夹与头发遮挡，再重做校准")
        pairs = meta.get("rmssd_pairs")
        if pairs is not None and pairs < RMSSD_MIN_PAIRS:
            warnings.append(f"B 段 RMSSD 有效差分对仅 {pairs} < {RMSSD_MIN_PAIRS}，"
                            f"HRV 判据（stress/relaxed）不可用——检查心率是否接通、RR 批次是否保留")
        if c_z is not None and c_z < Z_RELAX_A:
            warnings.append(f"C 段放松锚点 z={c_z:.2f} < 门控 {Z_RELAX_A}，"
                            f"该受试者放松态在当前 α 基线下不可判（建议提高 C 段放松引导强度或复核佩戴）")
        # v3.4.1：ok 判据补上"判据不可用"的两条（设计文档 §6.1 早就这么写，代码此前只判前两条）。
        # 依据：接触不良 → 所有 EEG 判据失效；RMSSD 差分对不足 → stress/relaxed 失效。
        # c_anchor_z 不纳入 ok——它反映受试者放松能力/引导强度，个体差异大，
        # 只告警（阻断校准会让人永远"校准失败"却无法通过重做改善）。
        #
        # v3.6 修正（2026-11-09，四次真机实测后）：**RMSSD 差分对移出 ok，改为独立 hrv_ok**。
        # 原判据的隐含前提是"设备会推 RR，只是没接通"，但实测四次采集 extend/rr 事件恒为 0
        # （纯 Python 解析器只解 TGAM 标准包，RR 在私有扩展包里）→ pairs 恒 0 → ok 恒 False
        # → 报告永远输出"重戴/重采"，而重戴无解。D0 决议早已接受"8 态仅 stress 不可判"，
        # ok 的语义必须对齐成"EEG 基线能否用于 7 态判别"，HRV 能力用 hrv_ok 单独表达。
        ok = (counts["B"] >= 10 and counts["A"] >= 10 and counts["C"] >= 10
              and artifact_ratio <= CALIB_ARTIFACT_MAX
              and contact_ratio <= 0.10)
        # v3.14：fail-closed。原式 `pairs is None or pairs >= MIN` 在 pairs 缺失时判"HRV 可用"，
        # 而 pairs 缺失恰恰发生在"外部传入的旧基线不含 calib_meta"这类**无从判断**的场景
        # （见上方 meta = base.get("calib_meta", {})）。无从判断时应当报不可用，
        # 否则报告会宣称 HRV 能力存在，而 stress/relaxed 实际可能恒不可判。
        hrv_ok = (pairs is not None and pairs >= RMSSD_MIN_PAIRS)
        return {"ok": ok, "hrv_ok": hrv_ok,
                "segments": counts, "segments_clean": clean_counts,
                "artifact_ratio": artifact_ratio, "contact_ratio": contact_ratio,
                "theta_ref": base.get("theta_ref"), "theta_ref_src": base.get("theta_ref_src"),
                "theta_rest": base.get("theta_rest"),
                "delta_ref": (base.get("delta").mu if isinstance(base.get("delta"), BaselineStats) else None),
                "delta_src": base.get("delta_src"), "delta_rest": base.get("delta_rest"),
                "rmssd_pairs": pairs,
                "beta_ref": base.get("beta_ref"), "gamma_ref": base.get("gamma_ref"),
                "emg_gate": bool(ARTIFACT_EMG_GATE),
                "c_anchor_z": c_z, "warnings": warnings}


# v3.16：演示数据拆到 mockdata.py；这里 re-export 保持历史调用面
from mockdata import MOCK_PHASES, _mock_frame, _make_mock_clock, main  # noqa: E402,F401


if __name__ == "__main__":
    main()
