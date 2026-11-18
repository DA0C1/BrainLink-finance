# -*- coding: utf-8 -*-
"""BrainLink 脑波监测模拟程序（无真机时使用）

本程序在没有 BrainLink 头环真机时，模拟其串口上报的全部数据通道，
字段定义与 BrainLinkParser 回调（onEEG/onEEGExtend/onGyro/onRR/onRaw）一一对应。

============================ 输出方式 ============================
- 控制台与日志文件均逐行输出，格式相同，每行带毫秒级时间戳:
      [HH:MM:SS.mmm] [类型] 数据内容
- 日志文件: logs/brainwave_YYYYMMDD_HHMMSS.log
      文件头: "# BrainLink 数据记录 - <开始时间>" 与 "# 格式: [类型] 数据内容"
      文件尾: "# 统计: EEG=.. Extend=.. Gyro=.. RR=.. Raw=.."
- Ctrl+C 退出时，控制台同样打印上述五项计数统计。
- 数据范围按真机日志 brainwave_20260617_152610.log 的实际观测值设定。

======================== 各部分数据含义与作用 ======================

[EEG] 脑电核心指标（始终启用，约每 1.3 秒一帧）
    attention   注意力指数 0-100，设备厂商算法综合评分。
                数值越高表示当前精神越集中（适合专注度/分心监测）。
    meditation  冥想度（放松度）0-100。数值越高表示越平静放松，
                常用于冥想训练、压力/焦虑评估。
    delta       δ 波功率（约 0.5-4 Hz）。深睡眠、无意识状态占主导；
                清醒时异常偏高常提示困倦或脑电受运动伪迹干扰。
    theta       θ 波功率（约 4-8 Hz）。困倦、浅睡、冥想、记忆加工时活跃。
    alpha       α 波功率（约 8-13 Hz）= lowAlpha + highAlpha。
                闭眼放松、清醒静息时明显，睁眼/专注时被抑制。
    beta        β 波功率（约 13-30 Hz）= lowBeta + highBeta。
                清醒、主动思考、逻辑推理、注意力集中时增强。
    gamma       γ 波功率（约 30-100 Hz）= lowGamma + highGamma。
                与高级认知、跨脑区信息整合、顿悟等相关。
    说明: 后五项是各频段"功率值"（无符号整数，数值越大该频段活动越强），
          真机日志中多数时间为几千~十几万，受眨眼/肌肉干扰时会偶发
          几十万~数百万的突发值，本模拟按此分布生成。

[EXT] 扩展设备/生理数据（--extend 启用，每帧 1 条；对应 BrainLinkExtendData）
    ap          佩戴/电极接触状态（ap=attachment point），反映设备是否
                佩戴良好、电极与皮肤接触是否正常。
    battery     设备电量百分比 1-100。
    version     设备固件版本号。
    gnaw        咬合（磨牙）检测值，用于识别咬牙/咀嚼这类肌肉干扰。
    temperature 体表/额头温度，单位 ℃。
    heart       心率，单位次/分（bpm），由额部光电/电极信号估算。
    注: 打印格式与真机程序 brain_test.py 一致，只输出 battery/heart/version。

[GYRO] 三轴陀螺仪数据（--gyro 启用，每帧 GYRO_PER_FRAME 条）
    x / y / z   注意: 不是绝对坐标，也不是"转了多少度"，而是头部绕三个
                相互垂直坐标轴的【角速度】(angular velocity)——转动的快慢，
                回调给的是传感器整数原始量化值（通常单位 °/秒 dps，需乘芯片
                量程系数换算，BrainLink 未公布该系数，需实测标定）。
                数值大小 = 转得快不快: 静止时三轴都接近 0（真机有零漂，
                不会严格为 0），猛转头时瞬间变大。
                正负号 = 转动方向（右手定则: 向左/向右转符号相反）。
    三轴与头部动作（以设备佩戴坐标系为准，具体轴向以厂商定义为准）:
        x 轴 -> 抬头/低头（点头）   姿态学名称: 俯仰 pitch
        y 轴 -> 左右转头（摇头）   姿态学名称: 偏航 yaw
        z 轴 -> 耳朵贴肩式侧倾     姿态学名称: 滚转 roll
    数据举例（本模拟可能输出的行）:
        [GYRO] x= -23 y= -53 z= -22   基本静止: 三轴都在 0 附近小幅抖动，
                                       模拟静坐时的轻微晃动/传感器零漂。
        [GYRO] x= 900 y=  40 z=  30   正在快速点头/低头: x 轴角速度很大，
                                       y、z 接近 0，头部基本只在做俯仰运动。
        [GYRO] x=  20 y=-1200 z= 60   正在快速向一侧转头: y 轴角速度很大，
                                       负号代表转向；转头停止后 y 会迅速回落到 0 附近。
        [GYRO] x=  10 y=  30 z= 700   正在歪头（侧倾）: z 轴角速度明显增大。
    重要区分:
      1) 角速度≠角度: 想知道"头转到了多少度"需对时间积分 ∫ωdt，
         但纯陀螺仪积分会累积漂移(zero drift)，几分钟后就不准。
      2) 陀螺仪≠加速度计: 加速度计静止时也测得到重力(约1g静态分量)，
         陀螺仪静止时输出≈0；想要稳定的绝对姿态，需陀螺仪+加速度计
         (+磁力计)融合(互补/卡尔曼滤波)，单凭这三个整数只能感知
         "动没动、转多快"，不存在绝对方向。
    作用: 运动伪迹处理——三轴数值明显变大说明用户在转头/晃动，此时 EEG
          会被肌肉和电极接触噪声污染，可将该时段数据标记或剔除；
          也可辅助判断低头(困倦)或用户是否离开。

[RR] 心跳 R-R 间期（--rr 启用，每帧 1 组 rr1/rr2/rr3）
    rr1/rr2/rr3 最近连续三次心跳中相邻 R 波的时间间隔，单位毫秒。
                心率 ≈ 60000 / RR（如 830ms ≈ 72 次/分）。
                作用: ①换算实时心率；②分析心率变异性 HRV——放松时
                间隔随呼吸轻微起伏，紧张疲劳时趋于平直。

[raw] 原始脑电波形采样（始终在后台计数，不打印）
    设备以约 512Hz 上报原始波形（数值为有符号的微小电压量化值），
    每帧约产生 505-520 个点。真机程序也只计数不打印，仅用于核对丢包。

============================ 命令行参数 ===========================
  --extend  启用扩展数据 [EXT]（电量/心率/固件版本）
  --gyro    启用陀螺仪   [GYRO]
  --rr      启用心跳RR   [RR]
  EEG 通道始终启用；raw 始终计数。

示例:
  python brain_sim.py                            # 仅 EEG
  python brain_sim.py --extend --gyro --rr       # 全部通道
  python brain_sim.py --extend                   # 任意组合
"""
import argparse
import os
import random
import time
from datetime import datetime
from types import SimpleNamespace

# 全局变量（与 brain_test.py 保持一致）
counters = {'eeg': 0, 'extend': 0, 'gyro': 0, 'rr': 0, 'raw': 0}
log_file = None

# 每帧（约 1.3 秒）陀螺仪输出条数；真机约 50Hz（约 68 条/帧），
# 默认调小以免刷屏，可按需修改
GYRO_PER_FRAME = 8

# 上一帧数值，用于随机游走，让各指标变化更平滑自然
_last = {
    'attention': 50,
    'meditation': 50,
    'heart': 72,
    'rr': 830,
    'gyro_x': 0,
    'gyro_y': 0,
    'gyro_z': 0,
    'battery': 95,
}


def init_log():
    """初始化日志文件"""
    global log_file
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f'brainwave_{timestamp}.log')
    log_file = open(log_path, 'w', encoding='utf-8')
    log_file.write(f"# BrainLink 数据记录 - {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    log_file.write(f"# 格式: [类型] 数据内容\n\n")
    log_file.flush()
    return log_path


def close_log():
    """关闭日志文件"""
    global log_file
    if log_file:
        log_file.write(f"\n# 统计: EEG={counters['eeg']} Extend={counters['extend']} "
                       f"Gyro={counters['gyro']} RR={counters['rr']} Raw={counters['raw']}\n")
        log_file.close()
        log_file = None


def emit(message):
    """统一输出：控制台带毫秒时间戳打印，同时原样写入日志文件（同一时间戳）"""
    global log_file
    timestamp = datetime.now().strftime('%H:%M:%S.%f')[:-3]
    line = f"[{timestamp}] {message}"
    print(line)
    if log_file:
        log_file.write(line + "\n")
        log_file.flush()


def _walk(key, step=22):
    """0-100 区间内随机游走，返回新值"""
    v = _last[key] + random.randint(-step, step)
    v = max(0, min(100, v))
    _last[key] = v
    return v


def _band(normal_lo, normal_hi, burst_hi, burst_prob=0.15):
    """频段功率：多数时候为常规小值，偶发大幅值突发（与真机日志分布一致）"""
    if random.random() < burst_prob:
        return random.randint(normal_hi, burst_hi)
    return random.randint(normal_lo, normal_hi)


def gen_eeg():
    """生成一帧模拟脑波数据

    范围依据真机日志统计:
      attention/meditation : 0-100
      delta   : 常时 1k-12万，突发至约 245 万
      theta   : 常时 2k-12万，突发至约 210 万
      alpha   : 常时 2k-10万，突发至约 82 万
      beta    : 常时 1.5k-9万，突发至约 110 万
      gamma   : 常时 1.5k-7万，突发至约 38 万
    """
    return {
        'attention': _walk('attention'),
        'meditation': _walk('meditation'),
        'delta': _band(1000, 120000, 2500000, 0.18),
        'theta': _band(2000, 120000, 2100000, 0.15),
        'alpha': _band(2000, 100000, 820000, 0.15),
        'beta': _band(1500, 90000, 1100000, 0.10),
        'gamma': _band(1500, 70000, 380000, 0.10),
    }


def onEEG(data):
    """输出一帧 EEG（格式与 brain_test.py 完全一致）"""
    counters['eeg'] += 1
    msg = (f"[EEG] attention={data['attention']:3d} meditation={data['meditation']:3d} "
           f"delta={data['delta']:5d} theta={data['theta']:5d} "
           f"alpha={data['alpha']:5d} "
           f"beta={data['beta']:5d} "
           f"gamma={data['gamma']:5d}")
    emit(msg)


def gen_extend():
    """生成一帧扩展数据，返回与 BrainLinkExtendData 同构的对象

    字段(README): ap / battery / version / gnaw / temperature / heart
    brain_test.py 只打印 battery / heart / version
    """
    # 电量缓慢下降，偶尔回充一点，限制 1-100
    battery = max(1, min(100, _last['battery'] - random.choice([0, 0, 0, 0, 1])))
    _last['battery'] = battery
    # 心率 60-95 bpm 随机游走
    heart = max(60, min(95, _last['heart'] + random.randint(-2, 2)))
    _last['heart'] = heart
    return SimpleNamespace(
        ap=random.randint(0, 100),       # 佩戴接触状态
        battery=battery,                 # 电量 %
        version=1.4,                     # 固件版本
        gnaw=random.randint(0, 3),       # 咬合检测
        temperature=round(random.uniform(36.0, 37.0), 1),  # 体温 ℃
        heart=heart,                     # 心率 bpm
    )


def gen_gyro():
    """生成一个陀螺仪采样 (x, y, z)，在 -2000~2000 内小幅游走

    x/y/z 是绕三轴的角速度（非绝对坐标、非角度）:
    x≈抬头/低头(pitch)，y≈左右转头(yaw)，z≈歪头侧倾(roll)。
    静止时三轴在 0 附近抖动（如 x=-23,y=-53,z=-22）；
    某一轴绝对值骤增表示对应方向快速转动（如 y=-1200 表示快速转头）。
    详见文件头 [GYRO] 章节。
    """
    xyz = []
    for axis in ('gyro_x', 'gyro_y', 'gyro_z'):
        v = _last[axis] + random.randint(-150, 150)
        v = max(-2000, min(2000, v))
        _last[axis] = v
        xyz.append(v)
    return tuple(xyz)


def gen_rr():
    """生成一组心跳 RR 间期 (rr1, rr2, rr3)，单位 ms，正常范围约 600-1000"""
    base = max(600, min(1000, _last['rr'] + random.randint(-20, 20)))
    _last['rr'] = base
    return tuple(max(550, min(1050, base + random.randint(-25, 25))) for _ in range(3))


def onEEGExtend(data):
    """输出一帧扩展数据（格式与 brain_test.py 完全一致）"""
    counters['extend'] += 1
    msg = f"[EXT] battery={data.battery}% heart={data.heart} version={data.version}"
    emit(msg)


def onGyro(x, y, z):
    """输出一个陀螺仪采样（格式与 brain_test.py 完全一致）"""
    counters['gyro'] += 1
    msg = f"[GYRO] x={x:4d} y={y:4d} z={z:4d}"
    emit(msg)


def onRR(rr1, rr2, rr3):
    """输出一组心跳 RR 数据（格式与 brain_test.py 完全一致）"""
    counters['rr'] += 1
    msg = f"[RR] {rr1} {rr2} {rr3}"
    emit(msg)


def onRaw(raw):
    """原始波数据（真机只计数不输出）"""
    counters['raw'] += 1


def parse_args():
    parser = argparse.ArgumentParser(description="BrainLink 脑波监测模拟程序（无真机）")
    parser.add_argument('--extend', action='store_true', help='启用扩展数据 [EXT]（电量/心率/版本）')
    parser.add_argument('--gyro', action='store_true', help='启用陀螺仪数据 [GYRO]')
    parser.add_argument('--rr', action='store_true', help='启用心跳 RR 数据 [RR]')
    return parser.parse_args()


def main():
    args = parse_args()

    print("=== BrainLink Parser Test (无真机模拟) ===")
    print("Initializing parser...")
    print("Parser created successfully")

    channels = ['EEG']
    if args.extend:
        channels.append('EXT')
    if args.gyro:
        channels.append(f'GYRO({GYRO_PER_FRAME}/帧)')
    if args.rr:
        channels.append('RR')
    print(f"已启用通道: {', '.join(channels)}（raw 始终计数）")

    log_path = init_log()
    print(f"日志文件: {log_path}")
    print("\n✓ 模拟设备连接成功！正在读取数据... (按 Ctrl+C 停止)\n")

    try:
        while True:
            # 一帧 EEG：真机约每 1.37 秒一帧，期间约产生 512 个 raw 采样
            onEEG(gen_eeg())
            if args.extend:
                onEEGExtend(gen_extend())
            if args.gyro:
                for _ in range(GYRO_PER_FRAME):
                    onGyro(*gen_gyro())
            if args.rr:
                onRR(*gen_rr())
            for _ in range(random.randint(505, 520)):
                onRaw(random.randint(-30000, 30000))

            # 偶发长间隔，模拟真机受干扰/丢包时的空档
            if random.random() < 0.05:
                time.sleep(random.uniform(2.0, 6.0))
            else:
                time.sleep(random.uniform(1.25, 1.45))

    except KeyboardInterrupt:
        print("\n\n程序已退出")
        print(f"\n数据统计: EEG={counters['eeg']} Extend={counters['extend']} "
              f"Gyro={counters['gyro']} RR={counters['rr']} Raw={counters['raw']}")
        close_log()
        print(f"日志已保存: {log_path}")


if __name__ == "__main__":
    main()
