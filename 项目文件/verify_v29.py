# -*- coding: utf-8 -*-
"""v2.8.2 独立核验：参数对照 + 数据处理手算 + 判断链路手算。
与 verify_v26/v28 不同：期望值全部手工推导（不依赖引擎内部函数），
从 state_engine 读取实际实现值对比——"与生成路径不同的回读验证"。"""
import math
import os
import random
import sys

# 2026-10-23：原为硬编码绝对路径，换盘符/换机器即失效。改为按本文件定位。
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import state_engine as se
from state_engine import (  # noqa: E402

    StateEngine, Frame, BaselineStats, _mock_frame, _make_mock_clock,
)

# v3.7.1：固定随机种子 —— 本文件用 _mock_frame（内部 random.random 抖动）却未播种，
# 导致 C5a 等边界断言偶发翻转（实测同一份代码两次跑一红一绿）。
random.seed(29)

PASS = 0


def check(name, cond, detail=""):
    global PASS
    assert cond, f"[{name}] FAIL {detail}"
    PASS += 1
    print(f"[{name}] OK {detail}")


# ============ A. 参数对照（文档 §四 声称值 vs 代码实际值）============
def test_params():
    exp = {
        "EWMA_ALPHA": 0.2, "ARTIFACT_RATIO": 15.0, "ARTIFACT_RATIO_RTB": 15.0,
        "Z_CONC": -1.0, "Z_DIST": 1.0, "Z_RELAX_A": 1.5, "Z_STRESS": -1.5,
        "Z_RMSSD_NO_STRESS": -1.0, "Z_STILL_CAP": 1.0,
        "ANX_ATT_MAX": 40.0, "ANX_MED_MAX": 40.0, "ANX_Z": -1.5,
        "FLOW_ATT_MIN": 60.0, "FLOW_MED_MIN": 60.0, "FLOW_HOLD_FRAMES": 5,
        "FLOW_Z": 1.5, "SYNC_WIN": 10, "SYNC_MIN": 0.6, "MIN_SYNC_FRAMES": 5,
        "IF_THETA_NORM": 1.2, "IF_HOLD_S": 300,
        # v2.9.1 疲劳积分口径（幅度加权 + 可恢复；旧 FATIGUE_HALF_LIFE_S 已废弃）
        "IF_REF_EXCESS": 0.3, "IF_RATE_MAX": 2.0, "IF_RECOVER_HALF_S": 600,
        "SLEEP_MIGRATE_S": 60,
        "Z_FATIGUE_BAR": -1.0, "Z_DROWSY_TAR": 1.5, "Z_DROWSY_DELTA": 1.0,
        "ADAPT_ENABLED": False, "ADAPT_ALPHA": 0.001, "ADAPT_MAX_DRIFT": 0.5,
        "ADAPT_EVAL_S": 60,
        "WINDOW_EEG_S": 10, "WINDOW_HRV_S": 60,
        "HIT_RATIO": 0.6, "DEBOUNCE_S": 5.0,
        "CONF_MIN": 0.6, "CONF_MAX": 0.95,
    }
    bad = []
    for k, v in exp.items():
        if abs(getattr(se, k) - v) > 1e-9:
            bad.append(f"{k}={getattr(se, k)}(期望{v})")
    check("A1 常量对照", not bad, "；".join(bad) or f"{len(exp)} 项全部一致")
    # 冷却表与优先级
    cd = se.COOL_DOWN
    check("A2 冷却表", cd == {"stress": 60, "anxiety": 60, "distracted": 60,
                              "fatigue": 60, "drowsy": 60,
                              "focus": 0, "flow": 0, "relaxed": 0, "neutral": 0},
          str(cd))
    check("A3 优先级", se.PRIORITY == ["stress", "anxiety", "drowsy", "fatigue",
                                        "distracted", "flow", "focus", "relaxed"],
          str(se.PRIORITY))
    # 权重矩阵
    W = StateEngine._default_weights()
    check("A4 权重矩阵",
          W["focus"] == {"R_tb": -1.0, "R_ba": 0.5}
          and W["distracted"] == {"R_tb": 1.0, "A_rel": 0.3, "still": 0.5}
          and W["relaxed"] == {"A_rel": 1.0, "RMSSD_60": 0.7, "still": -0.5}
          and W["stress"] == {"RMSSD_60": -1.0, "HR": 0.7}
          and W["anxiety"] == {"att": 1.0, "med": 1.0}
          and W["flow"] == {"att": 1.0, "med": 1.0, "sync": 2.0}
          and W["fatigue"] == {"R_ba": -1.0, "I_f": 1.0}
          and W["drowsy"] == {"TAR": 1.0, "delta": 0.5},
          str(W))


# ============ B. 数据处理手算核验 ============
def test_data():
    eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    # B1 EWMA：喂 theta=100 → smooth=100；再喂 110 → 0.2*110+0.8*100=102
    fr1 = Frame(theta=100.0, alpha=100.0, beta=100.0)
    fr2 = Frame(theta=110.0, alpha=100.0, beta=100.0)
    eng._ewma("theta", fr1.theta)
    check("B1a EWMA 首值", eng.smooth["theta"] == 100.0)
    eng._ewma("theta", fr2.theta)
    check("B1b EWMA 递推", abs(eng.smooth["theta"] - 102.0) < 1e-9,
          f"smooth={eng.smooth['theta']}(期望102)")
    # B2 伪迹：参照 theta=100，边界必须精确卡在 ARTIFACT_RATIO 上
    # v3.6 门限 6×→15×（真机定标，见 state_engine.ARTIFACT_RATIO 注释）：
    #   高值侧 r>15 判 → 2000(20×) 触发、1000(10×) 不触发
    #   低值侧 r*15<1 即 r<0.0667 判 → 5(0.05×) 触发、10(0.10×) 不触发
    eng_b = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    eng_b.smooth = {"theta": 100.0}
    check("B2a 伪迹触发（高值侧 20×）", eng_b._artifact(Frame(theta=2000.0)) is True)
    check("B2b 伪迹不触发（高值侧 10×）", eng_b._artifact(Frame(theta=1000.0)) is False)
    check("B2c 伪迹触发（低值侧 0.05×，<1/15）", eng_b._artifact(Frame(theta=5.0)) is True)
    check("B2d 伪迹不触发（低值侧 0.10×，>1/15）", eng_b._artifact(Frame(theta=10.0)) is False)
    # B3 特征手算：eeg_buf 两帧 theta=[100,100] beta=[100,100] alpha=[100,100]
    #   R_tb=100/100=1.0；R_ba=100/200=0.5；TAR=100/100=1.0；A_rel=(100-100)/100=0
    eng.eeg_buf = [Frame(theta=100.0, beta=100.0, alpha=100.0),
                   Frame(theta=100.0, beta=100.0, alpha=100.0)]
    f = eng._extract()
    check("B3a R_tb", abs(f.R_tb - 1.0) < 1e-9, f"R_tb={f.R_tb}")
    check("B3b R_ba", abs(f.R_ba - 0.5) < 1e-9, f"R_ba={f.R_ba}")
    check("B3c TAR", abs(f.TAR - 1.0) < 1e-9, f"TAR={f.TAR}")
    check("B3d A_rel", abs(f.A_rel - 0.0) < 1e-9, f"A_rel={f.A_rel}")
    # B4 RMSSD 手算：同批 rr=[800,820,790,810,800,820,790,810,800]
    #   diffs=[20,-30,20,-10,20,-30,20,-10] → mean(d²)=(400+900+400+100)*2/8=450 → sqrt≈21.21
    rrs = [(800.0, 1, 0), (820.0, 1, 0), (790.0, 1, 0), (810.0, 1, 0),
           (800.0, 1, 0), (820.0, 1, 0), (790.0, 1, 0), (810.0, 1, 0), (800.0, 1, 0)]
    rm = StateEngine._rmssd(rrs)
    check("B4 RMSSD 同批", abs(rm - math.sqrt(450.0)) < 1e-6, f"RMSSD={rm:.2f}(期望{math.sqrt(450):.2f})")
    # B5 RMSSD 跨批断开：8 元素，batch=1 六连 + batch=2 两连——跨批 (810,1)→(790,2) 断开
    #   同批差分 [20,-30,20,-10,20,20]（6 个）→ mean(d²)=2600/6 → sqrt≈20.82
    rm2 = StateEngine._rmssd([(800.0, 1, 0), (820.0, 1, 0), (790.0, 1, 0), (810.0, 1, 0),
                              (800.0, 1, 0), (820.0, 1, 0), (790.0, 2, 0), (810.0, 2, 0)])
    check("B5 RMSSD 跨批断开", abs(rm2 - math.sqrt(2600.0 / 6)) < 1e-6,
          f"RMSSD={rm2:.2f}(期望{math.sqrt(2600/6):.2f})")
    # B6 z 手算：R_tb=0.55 vs μ=1.0 σ=0.3 → (0.55-1)/0.3=-1.5
    z = eng._z(0.55, BaselineStats(mu=1.0, sigma=0.3))
    check("B6 z-score", abs(z - (-1.5)) < 1e-9, f"z={z}(期望-1.5)")
    # B7 缓冲滑动：_push_window ts 推进 → 10s 窗（保留跨度 ≤10s 的帧）
    #   12 帧每秒一帧：ts=12 时 12-1=11>10 弹 ts=1 → 剩 11 帧（ts 2..12，跨度恰 10s）
    eng2 = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    for i in range(12):
        eng2._push_window(Frame(theta=100.0, beta=100.0, alpha=100.0, ts=float(i + 1)), float(i + 1))
    check("B7 EEG 窗滑动", len(eng2.eeg_buf) == 11 and eng2.eeg_buf[0].ts == 2.0,
          f"len={len(eng2.eeg_buf)} 首帧ts={eng2.eeg_buf[0].ts}(期望2, 11帧)")
    # B8 still 手算：gyro [(0.5,0,0),(0.3,0,0)] → (0.5+0.3)/2=0.4
    eng3 = StateEngine(baseline=StateEngine.calibrate_from_mock([]), clock=_make_mock_clock())
    eng3.eeg_buf = [Frame(theta=100.0, beta=100.0, alpha=100.0)] * 2   # 填 EEG 窗（_extract 依赖）
    eng3.gyro_buf = [(0.5, 0.0, 0.0), (0.3, 0.0, 0.0)]
    check("B8 still", abs(eng3._extract().still - 0.4) < 1e-9)


# ============ C. 判断链路手算 ============
def test_judge():
    base = StateEngine.calibrate_from_mock([])
    # C1 专注门控：R_tb=0.55(z=-1.5<-1) RMSSD=20(z=1>-1) still=nan(放行) → focus 过
    eng = StateEngine(baseline=base, clock=_make_mock_clock())
    fr = Frame(theta=55.0, beta=100.0, alpha=130.0, delta=100.0, gamma=50.0,
               attention=50.0, meditation=50.0)
    for i in range(12):                       # 填窗
        eng._push_window(fr, float(i + 1))
    eng.rr_buf = [(800.0, None, 0), (820.0, None, 0)] * 6   # 交替 → |diff|恒 20 → RMSSD=20 (z=1)
    feats = eng._extract()
    scores, gates = eng._scores(feats, {"task_active": True}, fr)
    check("C1 focus 门控", gates["focus"] is True,
          f"R_tb={feats.R_tb:.2f} z={eng._z(feats.R_tb, base['R_tb']):.2f} "
          f"RMSSD={feats.RMSSD_60:.1f} z={eng._z(feats.RMSSD_60, base['RMSSD_60']):.2f}")
    check("C1b 非专注门控不过", gates["distracted"] is False and gates["relaxed"] is False
          and gates["stress"] is False)
    # C2 压力门控：RMSSD=5（交替 500/505 → |diff|恒 5 → RMSSD=5 → z=(5-15)/5=-2<-1.5）still 低 → stress 过
    fr2 = Frame(theta=100.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                attention=50.0, meditation=50.0)
    eng2 = StateEngine(baseline=base, clock=_make_mock_clock())
    for i in range(12):
        eng2._push_window(fr2, float(i + 1))
    eng2.rr_buf = [(500.0, None, 0), (505.0, None, 0)] * 6
    eng2.gyro_buf = [(0.02, 0.0, 0.0)] * 3
    f2 = eng2._extract()
    s2, g2 = eng2._scores(f2, {"task_active": True}, fr2)
    check("C2 stress 门控", g2["stress"] is True, f"RMSSD={f2.RMSSD_60} z={eng2._z(f2.RMSSD_60, base['RMSSD_60']):.2f}")
    # C3 仲裁优先级：焦虑+压力同时命中 → stress（PRIORITY 首位）
    fr3 = Frame(theta=100.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                attention=30.0, meditation=30.0)   # att/med 双低 → anxiety 过
    eng3 = StateEngine(baseline=base, clock=_make_mock_clock())
    for i in range(12):
        eng3._push_window(fr3, float(i + 1))
    eng3.rr_buf = [(500.0, None, 0), (505.0, None, 0)] * 6   # RMSSD=5 z=-2 → stress 过
    eng3.gyro_buf = [(0.02, 0.0, 0.0)] * 3
    f3 = eng3._extract()
    s3, g3 = eng3._scores(f3, {"task_active": True}, fr3)
    check("C3a 双状态命中", g3["anxiety"] and g3["stress"], f"gates={g3}")
    best, conf = eng3._arbitrate(s3, g3)
    check("C3b 仲裁 stress 优先", best == "stress", f"best={best}")
    # C4 置信度范围：conf ∈ [0.6, 0.95]
    check("C4 conf 范围", 0.6 <= conf <= 0.95, f"conf={conf}")
    # C5 时间确认：单帧抖动不翻转；5s 窗命中达标确认
    eng5 = StateEngine(baseline=base, clock=_make_mock_clock())
    for i in range(12):
        eng5.process_frame(_mock_frame("focus"), ctx={"task_active": True})
    before = eng5.last_decision.state
    fr_d = Frame(theta=160.0, beta=100.0, alpha=100.0, delta=100.0, gamma=50.0,
                 attention=50.0, meditation=50.0, ts=20.0)   # 单帧分心
    dec, _ = eng5.process_frame(fr_d, ctx={"task_active": True})
    check("C5a 单帧抖动不翻转", dec.state == "neutral", f"got {dec.state}")
    # C6 疲劳/嗜睡门控手算（直接查门控函数，不走长时间积分）
    eng6 = StateEngine(baseline=base, clock=_make_mock_clock())
    eng6._theta_int = 300.0                       # 手动置积分达标（I_f 逻辑已由 V9 实测）
    fr6 = Frame(theta=150.0, beta=50.0, alpha=100.0, delta=100.0, gamma=50.0,
                attention=50.0, meditation=50.0)  # R_ba=50/250=0.2 → z=-1.5<-1
    for i in range(12):
        eng6._push_window(fr6, float(i + 1))
    eng6.rr_buf = [(800.0, None, 0), (820.0, None, 0)] * 6   # RMSSD=20 z=1 → 非应激（疲劳不被排除）
    f6 = eng6._extract()
    s6, g6 = eng6._scores(f6, {"task_active": True}, fr6)
    check("C6a fatigue 门控", g6["fatigue"] is True, f"R_ba={f6.R_ba:.2f} z={eng6._z(f6.R_ba, base['R_ba']):.2f}")
    fr7 = Frame(theta=150.0, beta=100.0, alpha=60.0, delta=160.0, gamma=50.0,
                attention=40.0, meditation=30.0)  # TAR=150/60=2.5 z=5.7>1.5；δ z=3>1
    eng7 = StateEngine(baseline=base, clock=_make_mock_clock())
    for i in range(12):
        eng7._push_window(fr7, float(i + 1))
    f7 = eng7._extract()
    s7, g7 = eng7._scores(f7, {"task_active": True}, fr7)
    check("C6b drowsy 门控", g7["drowsy"] is True,
          f"TAR={f7.TAR:.2f} z={eng7._z(f7.TAR, base['TAR']):.2f} δz={eng7._z(160, base['delta']):.2f}")


if __name__ == "__main__":
    test_params()
    test_data()
    test_judge()
    print(f"\nALL PASS — 独立核验 {PASS} 项（参数对照/数据手算/判断手算）")
