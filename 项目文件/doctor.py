# -*- coding: utf-8 -*-
"""doctor.py — 环境诊断：换电脑 / 换目录 / 出问题时的第一站

## 为什么需要它
项目要做到「整个文件夹拷走就能跑」，那第一件事是能自己回答三个问题：
  1. 我该用哪个 Python？（便携运行时 / 系统 Python 混用是最常见的坑）
  2. 依赖齐了吗？
  3. 硬件通道通不通？
本脚本只做**只读检查**，不改任何文件、不连任何设备、不需要真机。

## 用法
    runtime\\python\\python.exe 项目文件\\doctor.py      # 便携方式（推荐）
    python 项目文件\\doctor.py                           # 系统 Python 方式
    ... --json                                           # 机器可读输出

退出码：0 = 全部通过；1 = 有阻塞项（标 [FAIL]）
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)                     # 项目根（脑机接口项目）
RUNTIME = os.path.join(ROOT, "runtime", "python")

OK, WARN, FAIL = "OK", "WARN", "FAIL"
_STATUS_ORDER = {OK: 0, WARN: 1, FAIL: 2}


class Report:
    def __init__(self) -> None:
        self.items: list[tuple[str, str, str, str]] = []   # (级别, 项, 实测, 建议)

    def add(self, level: str, name: str, detail: str, advice: str = "") -> None:
        self.items.append((level, name, detail, advice))

    @property
    def worst(self) -> str:
        worst = OK
        for lv, *_ in self.items:
            if _STATUS_ORDER[lv] > _STATUS_ORDER[worst]:
                worst = lv
        return worst

    def dump_text(self) -> None:
        icon = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
        width = max((len(n) for _, n, _, _ in self.items), default=10)
        print("=" * 74)
        print(" 脑机接口项目 · 环境诊断")
        print("=" * 74)
        for lv, name, detail, advice in self.items:
            print(f"{icon[lv]} {name.ljust(width)}  {detail}")
            if advice and lv != OK:
                print(f"{'':7s}{' ' * width}  → {advice}")
        print("-" * 74)
        bad = [n for lv, n, _, _ in self.items if lv == FAIL]
        warn = [n for lv, n, _, _ in self.items if lv == WARN]
        if bad:
            print(f"结论：有 {len(bad)} 项阻塞 —— {', '.join(bad)}")
            print("      按上面 → 后的建议处理后重跑本诊断。")
        elif warn:
            print(f"结论：可运行，但有 {len(warn)} 项需注意 —— {', '.join(warn)}")
        else:
            print("结论：全部通过，可以接硬件（下一步见 阶段B真机联调执行清单 D1）。")
        print("=" * 74)

    def dump_json(self) -> None:
        print(json.dumps(
            {"worst": self.worst,
             "items": [{"level": lv, "item": n, "detail": d, "advice": a}
                       for lv, n, d, a in self.items]},
            ensure_ascii=False, indent=2))


def check_python(r: Report) -> None:
    v = sys.version_info
    if v >= (3, 9):
        r.add(OK, "Python 版本", f"{v.major}.{v.minor}.{v.micro}")
    else:
        r.add(FAIL, "Python 版本", f"{v.major}.{v.minor}.{v.micro}（需 ≥3.9）",
              "用项目自带的 runtime\\python\\python.exe 运行")

    exe = os.path.abspath(sys.executable)
    in_runtime = os.path.normcase(exe).startswith(os.path.normcase(os.path.abspath(RUNTIME)))
    if in_runtime:
        r.add(OK, "解释器来源", "项目内便携运行时（可整包拷走）")
    else:
        has_runtime = os.path.isfile(os.path.join(RUNTIME, "python.exe"))
        r.add(WARN if has_runtime else OK, "解释器来源",
              f"系统 Python：{exe}",
              "项目已自带便携运行时，改用 runtime\\python\\python.exe 可避免依赖本机环境")

    # 嵌入式运行时靠 ._pth 找项目目录；缺了会出现 import state_engine 失败
    if in_runtime:
        if HERE in sys.path:
            r.add(OK, "模块搜索路径", "含 项目文件 目录")
        else:
            r.add(FAIL, "模块搜索路径", "缺 项目文件 目录",
                  "检查 runtime\\python\\python313._pth 是否含 '../../项目文件'")


def check_deps(r: Report) -> None:
    try:
        import serial
        r.add(OK, "pyserial", serial.__version__)
    except ImportError:
        r.add(FAIL, "pyserial", "未安装",
              "便携运行时已内置；若用系统 Python：py -m pip install --isolated "
              "--index-url https://pypi.org/simple pyserial")


def check_parser(r: Report) -> None:
    try:
        from BrainLinkParser import BrainLinkParser      # noqa: F401
        r.add(OK, "解析器", "官方 BrainLinkParser（可拿到 RR/心率）")
        return
    except ImportError:
        # 官方 .pyd 是 Python 3.11 的闭源二进制，在 3.13 上必然 ImportError——
        # 这不是故障，是预期的降级路径，接着试纯 Python 实现即可。
        pass
    except OSError as exc:
        # .pyd 存在但加载失败（缺 VC 运行库 / 位数不匹配）：与"没装"是两件事，
        # 值得单独说，否则现场会以为官方解析器根本不存在。
        r.add(WARN, "官方解析器", f"存在但加载失败：{exc}",
              "通常是缺 VC++ 运行库或 32/64 位不匹配；纯 Python 兜底仍可用（无 RR/心率）")
    try:
        from tgam_parser import BrainLinkParser          # noqa: F401
        r.add(WARN, "解析器", "纯 Python tgam_parser（无 RR/心率，stress 一态不可判）",
              "属已知代价，无需处理；答辩时按「无心率硬件通道」说明")
    except Exception as exc:                             # noqa: BLE001
        r.add(FAIL, "解析器", f"两者都不可用：{exc}",
              "确认 项目文件\\tgam_parser.py 存在且未损坏")


def check_runtime_pkgs(r: Report) -> None:
    """便携运行时只应含 pyserial。v3.16 曾发现 numpy/fastapi 等 68 MB 无关包混入。"""
    sp = os.path.join(RUNTIME, "Lib", "site-packages")
    if not os.path.isdir(sp):
        return
    allowed = {"serial", "pyserial-3.5.dist-info", "__pycache__"}
    extra = sorted(n for n in os.listdir(sp) if n not in allowed)
    if extra:
        r.add(WARN, "运行时依赖", f"site-packages 含 {len(extra)} 个额外包：{', '.join(extra[:4])}…",
              "项目只用 pyserial；无关包会让包体膨胀（v3.16 已归档 68 MB）。"
              "如非项目需要，移到 _archive/runtime_unused_site_packages")
    else:
        r.add(OK, "运行时依赖", "仅 pyserial（干净）")


def check_files(r: Report) -> None:
    need = ["state_engine.py", "sdk_adapter.py", "serial_bridge.py",
            "tgam_parser.py", "verify_hw.py"]
    missing = [f for f in need if not os.path.isfile(os.path.join(HERE, f))]
    if missing:
        r.add(FAIL, "核心文件", f"缺 {len(missing)} 个：{', '.join(missing)}",
              "疑似拷贝不完整，重新复制整个项目文件夹")
    else:
        r.add(OK, "核心文件", f"{len(need)}/{len(need)} 齐全")

    ui = os.path.join(ROOT, "组员文件", "任务一", "index.html")
    if os.path.isfile(ui):
        r.add(OK, "界面前端", "组员文件\\任务一\\index.html")
    else:
        r.add(WARN, "界面前端", "未找到 index.html",
              "实时面板会 404；补齐 组员文件\\任务一 目录")

    data_dir = os.path.join(HERE, "data")
    try:
        os.makedirs(data_dir, exist_ok=True)
        probe = os.path.join(data_dir, ".write_test")
        with open(probe, "w", encoding="utf-8") as fh:
            fh.write("1")
        os.remove(probe)
        r.add(OK, "数据目录可写", os.path.relpath(data_dir, ROOT))
    except Exception as exc:                             # noqa: BLE001
        r.add(FAIL, "数据目录可写", str(exc), "录制会失败；换到有写权限的目录")


def check_ports(r: Report) -> None:
    try:
        from serial.tools import list_ports
        ports = list(list_ports.comports())
    except Exception as exc:                             # noqa: BLE001
        r.add(FAIL, "串口枚举", str(exc), "pyserial 异常，重装")
        return
    names = [p.device for p in ports]
    if not names:
        r.add(WARN, "串口", "未检测到任何串口（未接硬件属正常）",
              "配对头环后重跑本诊断；注意选「传出」口，忽略主板 COM1")
        return
    detail = "、".join(f"{p.device}({(p.description or '')[:18]})" for p in ports)
    real = [p for p in ports if p.device.upper() != "COM1"]
    if real:
        r.add(OK, "串口", f"{len(ports)} 个：{detail}")
    else:
        r.add(WARN, "串口", f"只有 {detail}",
              "COM1 是主板自带口，永远不要选；配对头环后会新增两个口")


def check_engine(r: Report) -> None:
    """引擎冒烟：能 import、能标定、喂 60 帧能出一条决策。只读，不写盘、不碰串口。"""
    try:
        from state_engine import StateEngine
        from sdk_adapter import SDKFrame, SdkAdapter
    except Exception as exc:                             # noqa: BLE001
        r.add(FAIL, "引擎冒烟", f"import 失败：{exc}", "见上方「模块搜索路径」项")
        return
    try:
        eng = StateEngine(baseline=StateEngine.calibrate_from_mock([]))
        ad = SdkAdapter(eng)
        # 真机 1.2s/帧，脚本循环是毫秒级——不注入虚拟时钟，滑动窗永不滑，判不出状态
        vt = [0.0]
        eng._clock = lambda: (vt.__setitem__(0, vt[0] + 1.2), vt[0])[1]
        for _ in range(60):
            ad.onEEG(SDKFrame(theta=55, lowAlpha=65, highAlpha=65,
                              lowBeta=50, highBeta=50,
                              lowGamma=25.0, highGamma=25.0,
                              attention=65, meditation=55, signal=30))
        dec = eng.last_decision
        if dec is None:
            r.add(WARN, "引擎冒烟", "60 帧后仍未产生决策", "检查 state_engine 的窗口/门控参数")
        elif dec.state == "neutral":
            r.add(WARN, "引擎冒烟", f"判出 neutral conf={dec.conf:.2f}", "合成数据未达判据，真机再验")
        else:
            r.add(OK, "引擎冒烟", f"判出 {dec.state} conf={dec.conf:.2f}")
    except Exception as exc:                             # noqa: BLE001
        r.add(FAIL, "引擎冒烟", f"{type(exc).__name__}: {exc}", "引擎或适配器代码异常")


def main(argv: list[str]) -> int:
    r = Report()
    check_python(r)
    check_deps(r)
    check_runtime_pkgs(r)
    check_parser(r)
    check_files(r)
    check_ports(r)
    check_engine(r)

    if "--json" in argv:
        r.dump_json()
    else:
        r.dump_text()

    if r.worst == FAIL:
        print("\n下一步：按 [FAIL] 项的建议处理，然后重跑本诊断。")
        return 1
    if r.worst == WARN:
        print("\n下一步：无阻塞。可跑 `runtime\\python\\python.exe 项目文件\\serial_bridge.py --mock` 复核链路。")
        return 0
    print("\n下一步：`启动-数据采集.cmd`（或 serial_bridge.py --port COMx）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
