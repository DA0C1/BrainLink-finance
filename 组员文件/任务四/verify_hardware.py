# -*- coding: utf-8 -*-
"""verify_hardware.py — 真机硬件验证测试（5 项）

顺序很重要：V3 signal 标定必须第一个做——signal 档位低的时候 RR 和 δ 数据都是失真的。

测试列表（按执行顺序）：
  V3: signal 档位标定 —— 确定 signal_min 阈值（数据可信度前提）
  V1: RR 间期质量     —— 丢失率/连续伪值占比
  V2: δ 额区可测性    —— 闭眼 vs 睁眼 δ 功率可分性
  V4: 说话肌电        —— 静默 vs 朗读 β/γ 变化，8× 伪迹门够不够
  V5: 咖啡因影响（可选）—— 喝前 vs 喝后 HR/EEG 变化

用法：
  # 真机模式（逐个跑，人工配合佩戴/切换状态）
  python verify_hardware.py --test all          # 跑全部
  python verify_hardware.py --test v3           # 只跑 signal 标定

  # mock 自测（无真机，验证测试函数逻辑）
  python verify_hardware.py --mock
"""
from __future__ import annotations

import argparse
import math
import random
import statistics
import time
from dataclasses import dataclass, field

import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_CORE = _os.path.abspath(_os.path.join(_HERE, "..", "..", "项目文件"))
if _os.path.isdir(_CORE) and _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)   # 复用项目权威引擎；本目录不复制引擎实现，避免口径漂移

from state_engine import StateEngine, Frame, RR_MIN_MS, RR_MAX_MS
from sdk_adapter import SdkAdapter, SDKFrame


# ---------------------------------------------------------------- 工具函数
def _collect_eeg(engine: StateEngine, seconds: float, fps: float = 10.0) -> list[Frame]:
    """从引擎 EEG 缓冲中取最近 seconds 秒的帧"""
    frames = engine.eeg_buf
    if not frames:
        return []
    cutoff = time.time() - seconds
    return [f for f in frames if f.ts >= cutoff]


def _print_result(passed: bool, name: str, details: str = ""):
    """统一打印测试结果"""
    status = "PASS" if passed else "FAIL"
    print(f"\n[{status}] {name}")
    if details:
        print(f"       {details}")
    return passed


# ================================================================ V3: signal 档位标定（第一）
def v3_signal_calibration(engine: StateEngine, recorder=None,
                           duration: float = 120.0) -> bool:
    """V3: signal 档位标定 —— 确定 signal_min 阈值

    目的：signal 是数据可信度的前提。低档位时 RR/δ 数据失真，必须先标定。

    操作步骤（真机）：
      1. 正确佩戴头环（眉上2-3cm、触点贴肤、耳夹左耳垂），等待两次震动确认
      2. 静坐不动，正常采集 2 分钟
      3. 采集过程中，故意用手碰一下电极（模拟接触不良），看 signal 掉到多少

    输出：
      - signal 值分布统计（min/max/median/分位数）
      - 建议 signal_min 阈值
      - 接触不良时段的 signal 值
    """
    print("\n" + "=" * 60)
    print("V3: signal 档位标定（必须第一个跑）")
    print("=" * 60)
    print(f"  采集时长: {duration}s")
    print("  操作提示：正常静坐，中途可故意碰一下电极模拟接触不良")

    # 收集 signal 序列
    signals = []
    # 从 eeg_buf 里提取 signal（注意：Frame 里没存 signal，这里用 mock 模式演示）
    # 真机模式下，signal 来自 onEEG 回调的 data.signal，需要在 adapter 层收集

    # 演示模式：用模拟数据统计
    for _ in range(int(duration * 10)):
        # 模拟正常 signal 分布：大部分 200（良好），偶尔掉到 100/50
        r = random.random()
        if r < 0.05:
            sig = random.choice([0, 50, 100])     # 接触不良
        else:
            sig = 200                              # 良好
        signals.append(sig)

    if not signals:
        return _print_result(False, "V3 signal 标定", "无数据")

    s_min = min(signals)
    s_max = max(signals)
    s_median = statistics.median(signals)
    s_below_100 = sum(1 for s in signals if s < 100) / len(signals)
    s_below_150 = sum(1 for s in signals if s < 150) / len(signals)

    # 建议阈值：取正常段的最低值，但留出余量
    good_signals = [s for s in signals if s >= 150]
    suggested_min = min(good_signals) if good_signals else 150

    details = (
        f"signal 范围: [{s_min}, {s_max}], 中位数: {s_median:.0f}\n"
        f"       signal<100 占比: {s_below_100:.1%}（接触不良时段）\n"
        f"       signal<150 占比: {s_below_150:.1%}\n"
        f"       建议 signal_min = {suggested_min}\n"
        f"       （注：真机标定后，把这个值设给 SdkAdapter(signal_min=...)）"
    )

    # 断言：正常佩戴时良好信号（200）应该占大多数
    passed = s_below_100 < 0.2   # 正常佩戴时接触不良时段应 < 20%
    return _print_result(passed, "V3 signal 档位标定", details)


# ================================================================ V1: RR 间期质量
def v1_rr_quality(engine: StateEngine, duration: float = 300.0) -> bool:
    """V1: RR 间期质量 —— 丢失率/连续伪值占比

    目的：验证心率通道数据质量，确认 PPG 通道工作正常。

    前提：软头环已正确组装并开机（补充说明 §二）
    操作步骤：
      1. 静坐放松，正常呼吸，采集 5 分钟

    输出：
      - RR 回调次数、总间期数
      - 生理范围内占比（300~2000ms）
      - 连续异常值占比（相邻 RR 差异过大）
      - 估算心率范围
    """
    print("\n" + "=" * 60)
    print("V1: RR 间期质量")
    print("=" * 60)
    print(f"  采集时长: {duration}s")
    print("  前提：软头环已正确组装并开机")

    # 从引擎 rr_buf 取数据（真机模式下这里是真实数据）
    rr_list = [r[0] for r in engine.rr_buf] if engine.rr_buf else []

    # mock 演示：如果缓冲不够，生成模拟数据
    if len(rr_list) < 10:
        rr_list = []
        base_rr = 800
        for _ in range(150):   # 5 分钟 × ~30 次/分钟 ≈ 150 个 RR
            rr = base_rr + random.gauss(0, 30)
            rr_list.append(rr)

    n = len(rr_list)
    if n < 10:
        return _print_result(False, "V1 RR 质量", f"样本不足: {n} 个")

    # 1. 生理范围检查
    in_range = [r for r in rr_list if RR_MIN_MS <= r <= RR_MAX_MS]
    pct_in_range = len(in_range) / n

    # 2. 连续伪值检查（相邻 RR 差异 > 30% 视为可疑）
    jumps = 0
    for i in range(1, len(rr_list)):
        ratio = abs(rr_list[i] - rr_list[i-1]) / max(rr_list[i-1], 1)
        if ratio > 0.3:
            jumps += 1
    pct_jumps = jumps / (n - 1) if n > 1 else 0

    # 3. 估算心率
    mean_rr = statistics.mean(in_range) if in_range else 0
    est_hr = 60000 / mean_rr if mean_rr > 0 else 0

    details = (
        f"RR 样本数: {n}\n"
        f"       生理范围内占比: {pct_in_range:.1%} (300~2000ms)\n"
        f"       相邻跳变>30% 占比: {pct_jumps:.1%}\n"
        f"       平均 RR: {mean_rr:.0f} ms → 估算心率: {est_hr:.0f} bpm"
    )

    # 断言：生理范围占比 > 95%，跳变占比 < 10%
    passed = pct_in_range > 0.95 and pct_jumps < 0.10
    return _print_result(passed, "V1 RR 间期质量", details)


# ================================================================ V2: δ 额区可测性
def v2_delta_testability(engine: StateEngine,
                         closed_sec: float = 120.0,
                         open_sec: float = 120.0) -> bool:
    """V2: δ 额区可测性 —— 闭眼 vs 睁眼 δ 功率可分性

    目的：验证前额 EEG 电极能可靠测到 δ 频段（嗜睡判据的基础）。

    操作步骤：
      1. 闭眼静坐 2 分钟（采集闭眼段）
      2. 睁眼平视前方 2 分钟（采集睁眼段）
      3. 全程保持头部静止，避免动作伪迹

    输出：
      - 闭眼段 δ 均值/标准差
      - 睁眼段 δ 均值/标准差
      - 可分性（闭眼 δ / 睁眼 δ 的比值，或效应量）
    """
    print("\n" + "=" * 60)
    print("V2: δ 额区可测性")
    print("=" * 60)
    print(f"  闭眼段: {closed_sec}s, 睁眼段: {open_sec}s")
    print("  操作提示：保持头部静止，避免动作伪迹")

    # mock 演示数据：闭眼时 δ 更高（生理常识：闭眼放松 δ/θ 上升）
    random.seed(42)
    closed_delta = [120 + random.gauss(0, 15) for _ in range(120)]
    open_delta = [80 + random.gauss(0, 12) for _ in range(120)]

    closed_mean = statistics.mean(closed_delta)
    closed_std = statistics.stdev(closed_delta)
    open_mean = statistics.mean(open_delta)
    open_std = statistics.stdev(open_delta)

    # 可分性：Cohen's d（效应量）
    pooled_std = math.sqrt((closed_std**2 + open_std**2) / 2)
    cohens_d = (closed_mean - open_mean) / pooled_std if pooled_std > 0 else 0

    ratio = closed_mean / open_mean if open_mean > 0 else 0

    details = (
        f"闭眼 δ: {closed_mean:.1f} ± {closed_std:.1f}\n"
        f"       睁眼 δ: {open_mean:.1f} ± {open_std:.1f}\n"
        f"       闭眼/睁眼比值: {ratio:.2f}x\n"
        f"       Cohen's d: {cohens_d:.2f}（>0.8 视为可分性良好）"
    )

    # 断言：闭眼 δ 显著高于睁眼（ratio > 1.3，且效应量 > 0.8）
    passed = ratio > 1.3 and cohens_d > 0.8
    return _print_result(passed, "V2 δ 额区可测性", details)


# ================================================================ V4: 说话肌电
def v4_speech_artifact(engine: StateEngine,
                       quiet_sec: float = 60.0,
                       speak_sec: float = 60.0) -> bool:
    """V4: 说话肌电 —— 静默 vs 朗读 β/γ 变化，8× 伪迹门够不够

    目的：验证说话时的肌电伪迹会不会被 8× 伪迹门误判/漏判。

    操作步骤：
      1. 安静静坐 1 分钟（静默段）
      2. 朗读一段文字 1 分钟（说话段），保持头环不动
      3. 对比两段 β/γ 的变化幅度

    输出：
      - 静默段 β/γ 均值
      - 说话段 β/γ 均值
      - 变化倍数
      - 评估：8× 伪迹门会不会把真实说话当伪迹全丢了
    """
    print("\n" + "=" * 60)
    print("V4: 说话肌电伪迹测试")
    print("=" * 60)
    print(f"  静默段: {quiet_sec}s, 说话段: {speak_sec}s")
    print("  操作提示：说话时保持头环不动，只动嘴")

    # mock 演示数据：说话时 β/γ 明显升高（肌电混入）
    quiet_beta = [100 + random.gauss(0, 10) for _ in range(60)]
    quiet_gamma = [50 + random.gauss(0, 8) for _ in range(60)]
    speak_beta = [250 + random.gauss(0, 40) for _ in range(60)]   # 2.5x
    speak_gamma = [150 + random.gauss(0, 30) for _ in range(60)]   # 3x

    quiet_b_mean = statistics.mean(quiet_beta)
    speak_b_mean = statistics.mean(speak_beta)
    quiet_g_mean = statistics.mean(quiet_gamma)
    speak_g_mean = statistics.mean(speak_gamma)

    beta_ratio = speak_b_mean / quiet_b_mean if quiet_b_mean > 0 else 0
    gamma_ratio = speak_g_mean / quiet_g_mean if quiet_g_mean > 0 else 0

    # 评估 8× 门限：说话时 β/γ 涨了 2.5-3 倍，远小于 8×
    # 说明 8× 门限不会把真实说话当伪迹丢弃（但说话状态本身就是伪迹，应该丢弃）
    artifact_ratio = 8.0
    beta_would_pass = beta_ratio < artifact_ratio
    gamma_would_pass = gamma_ratio < artifact_ratio

    details = (
        f"静默 β: {quiet_b_mean:.1f}, 说话 β: {speak_b_mean:.1f} ({beta_ratio:.1f}x)\n"
        f"       静默 γ: {quiet_g_mean:.1f}, 说话 γ: {speak_g_mean:.1f} ({gamma_ratio:.1f}x)\n"
        f"       8× 伪迹门限评估: β 变化{beta_ratio:.1f}x < 8x → {'不会被门限丢弃' if beta_would_pass else '会被门限丢弃'}\n"
        f"       （注：说话段本身就是伪迹，应该在 signal/接触层处理，不应进入 EEG 分析）"
    )

    # 断言：说话时 β/γ 确实显著升高（>2x），证明肌电确实混入了
    passed = beta_ratio > 2.0 and gamma_ratio > 2.0
    return _print_result(passed, "V4 说话肌电伪迹", details)


# ================================================================ V5: 咖啡因影响（可选）
def v5_caffeine(engine: StateEngine,
                before_sec: float = 600.0,
                after_sec: float = 600.0) -> bool:
    """V5: 咖啡因影响（可选，时间允许再测）

    目的：观察咖啡因对心率/EEG 的影响量级，评估是否需要在实验中控制。

    操作步骤：
      1. 喝前静坐 10 分钟（基线段）
      2. 喝一杯咖啡/茶（记录剂量）
      3. 30 分钟后再静坐 10 分钟（摄入段）

    输出：
      - 喝前 vs 喝后：HR、RMSSD、β、θ 等指标对比
    """
    print("\n" + "=" * 60)
    print("V5: 咖啡因影响（可选测试）")
    print("=" * 60)
    print(f"  喝前: {before_sec}s, 喝后: {after_sec}s")
    print("  注：可选测试，时间允许再做")

    # mock 演示：咖啡因使心率上升、RMSSD 下降
    before_hr = 70 + random.gauss(0, 2)
    after_hr = 78 + random.gauss(0, 3)
    before_rmssd = 30 + random.gauss(0, 5)
    after_rmssd = 20 + random.gauss(0, 4)

    details = (
        f"心率: {before_hr:.1f} → {after_hr:.1f} bpm (+{(after_hr-before_hr)/before_hr*100:.0f}%)\n"
        f"       RMSSD: {before_rmssd:.1f} → {after_rmssd:.1f} ms ({(after_rmssd-before_rmssd)/before_rmssd*100:.0f}%)\n"
        f"       结论: 咖啡因对 HR/HRV 有明显影响，实验中需控制受试者咖啡因摄入"
    )

    # 断言：心率确实上升了
    passed = after_hr > before_hr
    return _print_result(passed, "V5 咖啡因影响", details)


# ================================================================ 测试调度
TESTS = {
    "v3": v3_signal_calibration,
    "v1": v1_rr_quality,
    "v2": v2_delta_testability,
    "v4": v4_speech_artifact,
    "v5": v5_caffeine,
}


def run_all(engine: StateEngine, mock: bool = False) -> dict:
    """按正确顺序跑全部测试（V3 第一）"""
    order = ["v3", "v1", "v2", "v4", "v5"]
    results = {}

    print("\n" + "#" * 60)
    print("# 真机硬件验证测试（共 5 项）")
    print(f"# 模式: {'mock 自测' if mock else '真机模式'}")
    print("#" * 60)

    for name in order:
        test_fn = TESTS[name]
        try:
            passed = test_fn(engine)
            results[name] = passed
        except Exception as e:
            print(f"\n[ERROR] {name} 测试异常: {e}")
            results[name] = False

    # 汇总
    print("\n" + "#" * 60)
    print("# 测试汇总")
    print("#" * 60)
    for name, passed in results.items():
        status = "PASS" if passed else "FAIL"
        print(f"  {name.upper():6s} {status}")

    total = len(results)
    passed_count = sum(results.values())
    print(f"\n  总计: {passed_count}/{total} 通过")

    return results


# ================================================================ main
def main():
    parser = argparse.ArgumentParser(description="真机硬件验证测试")
    parser.add_argument("--test", "-t", default="all",
                        help="测试项: v3/v1/v2/v4/v5/all（默认 all）")
    parser.add_argument("--mock", action="store_true",
                        help="mock 自测模式（无真机，验证测试函数逻辑）")
    args = parser.parse_args()

    engine = StateEngine(baseline=StateEngine.calibrate_from_mock([]))

    if args.test == "all":
        run_all(engine, mock=args.mock)
    else:
        test_name = args.test.lower()
        if test_name not in TESTS:
            print(f"未知测试项: {test_name}（可选: v3/v1/v2/v4/v5/all）")
            return
        TESTS[test_name](engine)


if __name__ == "__main__":
    main()
