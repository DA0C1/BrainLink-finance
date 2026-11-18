# 双引擎差分·Python 侧：确定性帧序列（无噪声），与 _diff_js.js 逐位一致
# v2.9.1 加强：①输出增加 I_f（原只比末态+conf，掩盖了双引擎疲劳积分 11 倍偏差）；
#            ②flow 段 att/med 线性爬升（原常值帧无趋势→同步率判据不成立，flow 实为伪覆盖）。
import state_engine as se

P = {
    'focus':      [55, 130, 100, 100, 50, 65, 55, 800, 820],
    'distracted': [160, 100, 100, 100, 50, 45, 45, 770, 790],
    'relaxed':    [100, 160, 100, 100, 50, 50, 70, 950, 990],
    'stress':     [90, 110, 100, 100, 50, 40, 30, 680, 686],
    'anxiety':    [130, 100, 100, 100, 50, 28, 26, 710, 730],
    'flow':       [70, 140, 100, 100, 50, 88, 84, 910, 930],
    'fatigue':    [220, 130, 45, 100, 50, 50, 55, 860, 880],
    'drowsy':     [180, 60, 90, 180, 50, 45, 45, 970, 990],
}
PLAN = [
    ('focus', 65, 1.0, True), ('distracted', 65, 1.0, True), ('relaxed', 65, 1.0, False),
    ('stress', 90, 1.0, False), ('anxiety', 65, 1.0, False), ('flow', 65, 1.0, False),
    ('fatigue', 100, 3.0, True), ('drowsy', 40, 1.0, True),
]
eng = se.StateEngine(baseline=se.StateEngine.calibrate_from_mock([]))  # 默认基线（含 theta_ref=55）
ts = 1.0
out = []
for ph, n, dt, task in PLAN:
    th, al, be, dl, ga, att, med, rr0, rr1 = P[ph]
    last_state = None
    last_conf = None
    for i in range(n):
        # v3.4.1 修：原来非 flow 段写 k=0.0 → att_i 恒 52.0、med_i 恒 48.0，
        # **每段用的都不是自己 P 表里的 att/med**。代价是 anxiety 段（att=28/med=26）
        # 实际喂的是 52/48：绝对常模不成立（52≥40）、个体 z=−0.53>−1.5 也不成立 →
        # 该段全程 gates 为空判 neutral，而这被当成"8 段覆核通过"。
        # 只有 flow 段需要线性爬升（同步率判据依赖趋势），其余段取段值。
        k = (i / max(n - 1, 1)) if ph == 'flow' else 1.0
        att_i = 52.0 + (att - 52.0) * k
        med_i = 48.0 + (med - 48.0) * k
        f = se.Frame(theta=th, alpha=al, beta=be, delta=dl, gamma=ga,
                     attention=att_i, meditation=med_i, rr_ms=(rr0 if i % 2 == 0 else rr1),
                     gyro=[0.01, 0.01, 0.01])
        f.ts = ts; ts += dt
        dec, fb = eng.process_frame(f, {'task_active': task})
        last_state = dec.state; last_conf = dec.conf
    # v3.4.1：再加 best 状态的**门控余量**。conf 是 sep×strength 的乘积，两侧若在
    # margins 上分叉，conf 有时会因四舍五入巧合相同；margins 是算法层最直接的观测量。
    m_best = (dec.margins or {}).get(last_state, 0.0)
    out.append(f'{ph}|{last_state}|{last_conf:.4f}|{eng._theta_int:.2f}|{m_best:.4f}')
print('\n'.join(out))
