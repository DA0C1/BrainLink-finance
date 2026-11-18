# -*- coding: utf-8 -*-
"""verify_parity：双引擎口径闸门（v2.9.1 新增）

背景：本项目 Python（state_engine.py）与 JS（组员文件/任务一/js/engine.js）双引擎并行，
历史上两次真实事故都源于"改了一边没改另一边"：
  · 10-21 IF_THETA_NORM 1.0→1.2 修复，漏了任务1/2/3 分发副本；
  · 10-23 疲劳积分 v2.9 改纯时长，JS 仍用旧公式 → 同一输入 I_f 偏差 11 倍，
    而当时的 _diff 脚本只比"末态 + conf"，conf 被 min(I_f/300,1) 饱和，差异完全不可见。

本脚本提供两道闸门：
  闸门 1（常量表）：Python 模块级常量 vs JS `K` 对象逐项比对（数值容差 1e-9）。
  闸门 2（轨迹）：跑 _diff_py.py / _diff_js.js，逐行比对 状态|conf|I_f
                  ——I_f 入列是关键，端点相同但轨迹漂移也能抓出。

用法：
  python verify_parity.py                # 全跑，全绿 exit 0
  python verify_parity.py --node <path>  # 指定 node（默认自动探测）
"""
import json
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
JS_ENGINE = os.path.join(ROOT, "组员文件", "任务一", "js", "engine.js")

# Python 常量名 == JS K 键名（本项目两侧同名，语义一一对应）
PARAM_KEYS = [
    "EWMA_ALPHA", "ARTIFACT_RATIO", "ARTIFACT_RATIO_RTB", "RR_MIN_MS", "RR_MAX_MS",
    # v3.4：接触门 / 异位滤波 / 差分对下限 / 证据归一参考（两侧同名同值）
    "SIGNAL_BAD", "RR_DIFF_MAX_RATIO", "RMSSD_MIN_PAIRS",
    "ARTIFACT_EMG_GATE", "ARTIFACT_EMG_RATIO", "ARTIFACT_EMG_THETA_TOL",   # v3.1 肌电专用门
    # v3.4：伪迹参照系（稳健中位数窗口 / 样本下限 / 连续全拦逃生门）
    "ARTIFACT_REF_WINDOW", "ARTIFACT_REF_MIN", "ARTIFACT_REF_RESET",
    "EVID_REF", "EVID_FULL",
    "Z_CONC", "Z_RMSSD_NO_STRESS", "Z_DIST", "Z_RELAX_A", "Z_STRESS", "Z_STILL_CAP",
    "WEAK_CONF", "WEAK_ESCALATE_FRAMES", "WEAK_ESCALATE_CD",
    "ANX_ATT_MAX", "ANX_MED_MAX", "ANX_Z",
    "FLOW_ATT_MIN", "FLOW_MED_MIN", "FLOW_HOLD_FRAMES", "FLOW_Z",
    "SYNC_WIN", "SYNC_MIN", "MIN_SYNC_FRAMES",
    "IF_THETA_NORM", "IF_HOLD_S", "IF_REF_EXCESS", "IF_RATE_MAX", "IF_RECOVER_HALF_S",
    "IF_MAX_DT_S",
    "SLEEP_MIGRATE_S",
    "Z_FATIGUE_BAR", "Z_DROWSY_TAR", "Z_DROWSY_DELTA",
    "ADAPT_ALPHA", "ADAPT_MAX_DRIFT", "ADAPT_EVAL_S",
    "WINDOW_EEG_S", "WINDOW_HRV_S", "HIT_RATIO", "DEBOUNCE_S",
    "CONF_MIN", "CONF_MAX", "COOL_DOWN", "PRIORITY",
]

fails = []


def check(name, ok, detail=""):
    print(f"[{'OK' if ok else 'FAIL'}] {name} {detail}")
    if not ok:
        fails.append(name)


def find_node(explicit=None):
    for cand in (explicit, os.environ.get("NODE"), shutil.which("node")):
        if cand and os.path.exists(cand):
            return cand
    # 兜底：常见安装位置（换机器时 PATH 里可能没有 node）
    for cand in (r"C:\Program Files\nodejs\node.exe",
                 r"C:\Program Files (x86)\nodejs\node.exe"):
        if os.path.exists(cand):
            return cand
    return None


def js_dump_params(node):
    """在 node 中加载 engine.js，导出 K 对象 JSON"""
    code = (
        "const fs=require('fs'),vm=require('vm');"
        "global.window=global;"
        "vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));"
        "process.stdout.write(JSON.stringify(global.BrainState.K));"
    )
    r = subprocess.run([node, "-e", code, JS_ENGINE],
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode != 0:
        raise RuntimeError(f"node 加载 engine.js 失败：{r.stderr[:400]}")
    return json.loads(r.stdout)


def num_eq(a, b):
    if isinstance(a, bool) or isinstance(b, bool):
        return a == b
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return abs(float(a) - float(b)) <= 1e-9
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(num_eq(a[k], b[k]) for k in a)
    if isinstance(a, (list, tuple)) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(num_eq(x, y) for x, y in zip(a, b))
    return a == b


def gate_params(node):
    import state_engine as se
    js = js_dump_params(node)
    bad, missing_js, missing_py = [], [], []
    for k in PARAM_KEYS:
        if not hasattr(se, k):
            missing_py.append(k)
            continue
        if k not in js:
            missing_js.append(k)
            continue
        if not num_eq(getattr(se, k), js[k]):
            bad.append(f"{k}: py={getattr(se, k)!r} js={js[k]!r}")
    check(f"闸门1 常量表一致（{len(PARAM_KEYS) - len(bad) - len(missing_js) - len(missing_py)}"
          f"/{len(PARAM_KEYS)} 项）", not bad and not missing_js and not missing_py,
          ("；".join(bad + [f"JS缺{k}" for k in missing_js] + [f"Py缺{k}" for k in missing_py]))[:400])


def run(cmd):
    r = subprocess.run(cmd, cwd=HERE, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise RuntimeError(f"{cmd[0]} 退出码 {r.returncode}: {(r.stderr or r.stdout)[:400]}")
    return r.stdout.replace("\r\n", "\n").replace("\r", "\n").strip().split("\n")


def gate_trajectory(node):
    py = run([sys.executable, os.path.join(HERE, "_diff_py.py")])
    js = run([node, os.path.join(HERE, "_diff_js.js")])
    same = py == js
    detail = ""
    if not same:
        diffs = [f"L{i+1} py={a!r} js={b!r}"
                 for i, (a, b) in enumerate(zip(py, js)) if a != b]
        if len(py) != len(js):
            diffs.append(f"行数 py={len(py)} js={len(js)}")
        detail = "；".join(diffs[:6])
    check(f"闸门2 双引擎轨迹逐行一致（{len(py)} 段 × 状态/conf/I_f/余量）", same, detail)

    # 闸门 2b：**每段都要命中自己的目标状态**（v3.4.1 新增）。
    # 为什么必须有：v3.4.1 之前这里只比 py==js。_diff_* 有个 att/med 插值 bug，
    # anxiety 段实际喂的是 att=52/med=48（既不 <40 也不 z<−1.5）→ 全程 gates 为空判 neutral，
    # 两侧都是 neutral → "逐行一致"永远为真。**一致的是"两边都没判出来"**，覆盖是假的。
    # 断言"段名 == 状态"才能证明这条轨迹真的走了目标判据分支。
    rows = [ln.split("|") for ln in py if ln.strip()]
    miss = [f"{r[0]}→{r[1]}" for r in rows if len(r) >= 2 and r[0] != r[1]]
    check(f"闸门2b 8 段各自命中目标状态（{len(rows)} 段逐段核对，防「两侧都没判出来」）",
          not miss and len(rows) == 8, "；".join(miss) if miss else "")
    return py


def gate_baseline(node):
    """闸门 3：同一组三段校准帧 → 两侧 calibrate_from_frames 产出的基线必须**逐值**一致。

    为什么单独设这道门：θ_ref 的参照系（A 段 vs B 段）在两侧曾经分叉过，而
    常量表和轨迹闸门都抓不到——常量相同、轨迹相同，但基线不同，后果是"校准后疲劳永不触发"。
    基线是校准协议与引擎之间的**契约**，必须显式比对。

    v3.4 扩容：原来只比 5 个标量（θ_ref/β_ref/γ_ref 及其来源），实测两侧在
    **稳健 σ 口径**上不同（JS 用上中位数 + max(med−q1,q3−med)，Python 用偶样本均值 +
    (q3−q1)/2），非对称分布下基线会漂，而旧闸门看不见。现补上：
      · 各特征的 μ/σ（α、δ、A_rel、R_tb、RMSSD_60）
      · 伪迹剔除后的实际伪迹率与 RMSSD 差分对数（calib_meta）
    用例里故意放 3 帧眨眼（α=900），让"伪迹剔除"这段也进比对，而不是只比干净段。

    v3.4.x 再扩容：补上 att/med/still 的 μ/σ（**此前是盲区**）。真实轨迹 parity 曾抓到
    双引擎在 att.sigma（Python=3 vs JS=15）、med.mu（55 vs 60）、still.mu（0 vs 0.03）
    三向不一致 → 焦虑/体动门控在真机上分叉（py/js 状态一致仅 80/160），而本门只比
    θ_ref 系 + α/δ/A_rel/R_tb/RMSSD_60，全绿抓不到。根因是 JS calibrateFromFrames 用
    `fr[name]` 取 att/med（Frame 只暴露 attention/meditation），B 段覆盖静默 no-op。
    修引擎后此处把 att/med/still 纳入逐值比对，今后任一侧回归都会让本门变红。
    """
    scalars = ["theta_ref", "theta_rest", "beta_ref", "gamma_ref", "theta_ref_src", "delta_src"]
    stats = ["alpha", "delta", "A_rel", "R_tb", "RMSSD_60", "att", "med", "still"]
    py_code = (
        "import json, state_engine as se\n"
        "from state_engine import Frame\n"
        "def mk(tag, n, **kw):\n"
        "    return [(tag, Frame(**kw)) for _ in range(n)]\n"
        "fr = (mk('A', 40, theta=130.0, alpha=120.0, beta=70.0, delta=120.0, gamma=45.0,\n"
        "         attention=55.0, meditation=70.0, rr_ms=950.0)\n"
        "      + mk('B', 37, theta=100.0, alpha=100.0, beta=100.0, delta=100.0, gamma=50.0,\n"
        "           attention=60.0, meditation=55.0, rr_ms=820.0)\n"
        "      + mk('B', 3, theta=100.0, alpha=900.0, beta=100.0, delta=100.0, gamma=50.0,\n"
        "           attention=60.0, meditation=55.0, rr_ms=820.0)\n"
        "      + mk('C', 30, theta=95.0, alpha=160.0, beta=70.0, delta=100.0, gamma=45.0,\n"
        "           attention=50.0, meditation=72.0, rr_ms=950.0))\n"
        "for i, it in enumerate(fr):\n"
        "    if it[0] == 'B': it[1].rr_batch = [800.0 + (i % 3) * 6, 812.0, 804.0]\n"
        "b = se.StateEngine.calibrate_from_frames(fr)\n"
        f"out = {{k: b.get(k) for k in {scalars!r}}}\n"
        f"for k in {stats!r}:\n"
        "    st = b.get(k)\n"
        "    out[k + '.mu'] = None if st is None else st.mu\n"
        "    out[k + '.sigma'] = None if st is None else st.sigma\n"
        "m = b.get('calib_meta', {})\n"
        "out['artifact_ratio'] = m.get('artifact_ratio')\n"
        "out['rmssd_pairs'] = m.get('rmssd_pairs')\n"
        "print(json.dumps(out))\n"
    )
    js_code = (
        "const fs=require('fs'),vm=require('vm');"
        "global.window=global;"
        "vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));"
        "const {StateEngine,Frame}=global.BrainState;"
        "const mk=(tag,n,o)=>Array.from({length:n},()=>({tag,frame:new Frame(o)}));"
        "let fr=[...mk('A',40,{theta:130,alpha:120,beta:70,delta:120,gamma:45,attention:55,meditation:70,rr_ms:950}),"
        "...mk('B',37,{theta:100,alpha:100,beta:100,delta:100,gamma:50,attention:60,meditation:55,rr_ms:820}),"
        "...mk('B',3,{theta:100,alpha:900,beta:100,delta:100,gamma:50,attention:60,meditation:55,rr_ms:820}),"
        "...mk('C',30,{theta:95,alpha:160,beta:70,delta:100,gamma:45,attention:50,meditation:72,rr_ms:950})];"
        "fr.forEach((it,i)=>{ if(it.tag==='B') it.frame.rr_batch=[800+(i%3)*6,812,804]; });"
        "const b=StateEngine.calibrateFromFrames(fr);"
        f"const out=Object.fromEntries({scalars!r}.map(k=>[k,b[k]]));"
        f"for(const k of {stats!r}){{ const st=b[k]; out[k+'.mu']=st?st.mu:null; out[k+'.sigma']=st?st.sigma:null; }}"
        "const m=b.calib_meta||{};"
        "out.artifact_ratio=m.artifact_ratio; out.rmssd_pairs=m.rmssd_pairs;"
        "process.stdout.write(JSON.stringify(out));"
    )
    rp = subprocess.run([sys.executable, "-c", py_code], cwd=HERE, capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    rj = subprocess.run([node, "-e", js_code, JS_ENGINE], capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if rp.returncode != 0 or rj.returncode != 0:
        check("闸门3 校准基线一致", False, (rp.stderr or rj.stderr)[:300])
        return
    py = json.loads(rp.stdout.strip().splitlines()[-1])
    js = json.loads(rj.stdout.strip().splitlines()[-1])
    bad = [f"{k}: py={py.get(k)!r} js={js.get(k)!r}" for k in py if not num_eq(py.get(k), js.get(k))]
    check("闸门3 校准基线逐值一致（%d 项：θ_ref 来源 / %d 特征 μ·σ / 伪迹率 / 差分对）"
          % (len(py), len(stats)),
          not bad, "；".join(bad)[:500])
    if not bad:
        print(f"      α μ/σ={py['alpha.mu']:.3f}/{py['alpha.sigma']:.3f}"
              f"（已剔除 3 帧眨眼）；伪迹率={py['artifact_ratio']:.3f}；"
              f"RMSSD={py['RMSSD_60.mu']:.4f}" if py.get("alpha.mu") is not None else "")


def gate_artifact(node):
    """闸门 4：伪迹参照系行为一致（v3.4 新增）。

    为什么必须有这一道：verify_engine 只看得见 Python 的 ArtifactDetector；JS 侧那份是
    独立实现，改门限/改记账规则时极易单边漂移。而"伪迹判据漂移"在轨迹闸门里是隐形的——
    只有某帧恰好被判伪迹时轨迹才分叉，用例没覆盖到就一路绿灯（历史上就是这么漏的）。
    这里直接给两边喂同一段"持续肌电"，比对拦截帧数与逃生门触发次数。
    """
    scenario = ("60 帧干净（β=100）→ N 帧 β=2000 的持续肌电；记录拦截数与参照系重建次数")
    py_code = (
        "import json\n"
        "from state_engine import Frame, ArtifactDetector\n"
        "def run(nb):\n"
        "    d = ArtifactDetector()\n"
        "    for i in range(60):\n"
        "        d.push(Frame(theta=100.0, beta=100.0, alpha=110.0, gamma=50.0, delta=100.0, ts=float(i)))\n"
        "    hits = 0\n"
        "    for i in range(nb):\n"
        "        hits += d.push(Frame(theta=100.0, beta=2000.0, alpha=110.0, gamma=1000.0,\n"
        "                             delta=100.0, ts=float(60 + i))) is not None\n"
        "    return hits, d.reseeds, round(d.smooth['beta'], 6)\n"
        "h40, r40, s40 = run(40)\n"
        "h70, r70, _ = run(70)\n"
        "print(json.dumps({'hits40': h40, 'reseeds40': r40, 'smooth40': s40,\n"
        "                  'hits70': h70, 'reseeds70': r70}))\n"
    )
    js_code = (
        "const fs=require('fs'),vm=require('vm');"
        "global.window=global;"
        "vm.runInThisContext(fs.readFileSync(process.argv[1],'utf8'));"
        "const {Frame,ArtifactDetector}=global.BrainState;"
        "function run(nb){"
        "  const d=new ArtifactDetector();"
        "  for(let i=0;i<60;i++) d.push(new Frame({theta:100,beta:100,alpha:110,gamma:50,delta:100,ts:i}));"
        "  let hits=0;"
        "  for(let i=0;i<nb;i++) hits += (d.push(new Frame({theta:100,beta:2000,alpha:110,gamma:1000,delta:100,ts:60+i}))!==null);"
        "  return [hits,d.reseeds,Math.round(d.smooth.beta*1e6)/1e6];"
        "}"
        "const a=run(40), b=run(70);"
        "process.stdout.write(JSON.stringify({hits40:a[0],reseeds40:a[1],smooth40:a[2],hits70:b[0],reseeds70:b[1]}));"
    )
    rp = subprocess.run([sys.executable, "-c", py_code], cwd=HERE, capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    rj = subprocess.run([node, "-e", js_code, JS_ENGINE], capture_output=True,
                        text=True, encoding="utf-8", errors="replace")
    if rp.returncode != 0 or rj.returncode != 0:
        check("闸门4 伪迹门行为一致", False, (rp.stderr or rj.stderr)[:300])
        return
    py = json.loads(rp.stdout.strip().splitlines()[-1])
    js = json.loads(rj.stdout.strip().splitlines()[-1])
    bad = [f"{k}: py={py.get(k)!r} js={js.get(k)!r}" for k in py if not num_eq(py.get(k), js.get(k))]
    # 兼行为断言：持续肌电必须全拦、连续超限必须重建（数值相同但都错也要能 FAIL）
    expect = py.get("hits40") == 40 and py.get("reseeds40") == 0 and py.get("hits70") == 60 \
        and py.get("reseeds70") == 1
    check(f"闸门4 伪迹门行为一致（{scenario}）", not bad and expect,
          ("；".join(bad) if bad else
           f"40 帧拦截 {py.get('hits40')}/40、70 帧拦截 {py.get('hits70')}/70 后重建 "
           f"{py.get('reseeds70')} 次、平滑 β 未被拖走={py.get('smooth40') == 100}")[:400])


def main():
    node = find_node(sys.argv[sys.argv.index("--node") + 1] if "--node" in sys.argv else None)
    if not node:
        print("FAIL 未找到 node（用 --node 指定）")
        sys.exit(2)
    import state_engine  # noqa: F401  确保模块可导入
    gate_params(node)
    gate_baseline(node)
    gate_artifact(node)
    rows = gate_trajectory(node)
    print("\n轨迹（Python 侧视角，JS 侧逐行相同）：")
    for r in rows:
        print("  " + r)
    print(f"\nverify_parity 结果: {'全部通过' if not fails else '失败: ' + ','.join(fails)}")
    sys.exit(1 if fails else 0)


if __name__ == "__main__":
    main()
