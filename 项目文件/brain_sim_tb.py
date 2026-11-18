# -*- coding: utf-8 -*-
"""brain_sim 增强版：追加 θ/β 比值实时输出（M13 决议案A 主口径）

用途：在无真机阶段验证《脑波-心率与状态映射关系表》专注度主口径可落地。
口径：专注度↑ ↔ θ/β 下降（M13 决议：θ/β 比值主口径，α↓/β↑ 仅交叉佐证）。

工程处理（依据《BrainLink脑机接口设备数据解析与应用开发指南》4.2 节与映射表）：
1. EWMA 平滑（alpha=0.2）：原始脑波存在实时波动，直接应用会导致控制不稳定；
2. 伪迹打标：单帧比值相对平滑值偏差 >8 倍视为眨眼/肌电/体动伪迹（真机日志
   中偶发几十万~数百万突发值），该帧不更新平滑值、输出打标，供后续剔除。
   （对应映射表 δ/θ 行"重点清洗+打标不硬判"口径）

实现方式：import 原 brain_sim 模块并覆写 onEEG，追加 [TB] 输出；
不修改原 brain_sim.py（课程原文件保持不动）。

运行：
  python brain_sim_tb.py                            # 仅 EEG + θ/β
  python brain_sim_tb.py --extend --gyro --rr       # 全部通道 + θ/β
"""
import brain_sim as bs

EWMA_ALPHA = 0.2        # 指数加权移动平均系数（指南 4.2 推荐范围 0.1–0.3）
ARTIFACT_RATIO = 8.0    # 单帧比值相对平滑值的偏差倍数门限，超过视为伪迹
_tb_ewma = None         # θ/β 平滑值（跨帧保持）


def _theta_beta(data):
    """同帧 theta/beta 功率比值（beta<=0 时为无穷大占位）"""
    beta = data['beta']
    if beta <= 0:
        return float('inf')
    return data['theta'] / beta


def onEEG_with_tb(data):
    """原 onEEG 输出 + 追加 θ/β 比值行（含 EWMA 平滑与伪迹打标）"""
    global _tb_ewma
    bs.counters['eeg'] += 1
    msg = (f"[EEG] attention={data['attention']:3d} meditation={data['meditation']:3d} "
           f"delta={data['delta']:5d} theta={data['theta']:5d} "
           f"alpha={data['alpha']:5d} "
           f"beta={data['beta']:5d} "
           f"gamma={data['gamma']:5d}")
    bs.emit(msg)

    tb = _theta_beta(data)
    if _tb_ewma is None:
        _tb_ewma = tb
        tag = "基线"
    elif _tb_ewma == 0 or (tb / _tb_ewma) > ARTIFACT_RATIO or (_tb_ewma / tb) > ARTIFACT_RATIO:
        tag = "伪迹-不更新"   # 突发污染帧：打标不硬判，平滑值保持
    else:
        _tb_ewma = EWMA_ALPHA * tb + (1 - EWMA_ALPHA) * _tb_ewma
        tag = "更新"
    bs.emit(f"[TB] theta/beta_raw={tb:9.3f}  smoothed={_tb_ewma:9.3f}  ({tag})  (案A：θ/β↓=专注度↑)")


# 覆写模块全局回调，main() 内对 onEEG 的引用即指向增强版
bs.onEEG = onEEG_with_tb

if __name__ == '__main__':
    bs.main()
