# -*- coding: utf-8 -*-
"""verify_all.py — 验证脚本总入口（v3.16）

之前 13 个 verify_*.py 各自为战，回归靠人记；这个入口按"快/全"两组跑，统一退出码。

    python verify_all.py            # 快检：lint + 引擎 + 数据 + 对拍 + 契约 + 校准（<15s）
    python verify_all.py --full     # 加接口联调 / 采集自检 / 解析器 / 历史用例
    python verify_all.py --only verify_engine verify_data
    python verify_all.py --list

UI e2e / 响应式需要浏览器与本地服务，不放进这里：
    node tools/ui_e2e.mjs http://127.0.0.1:8791/
    node tools/ui_responsive.mjs
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
FAST = ["_lint.py", "verify_engine.py", "verify_data.py", "verify_parity.py",
        "verify_frontend.py", "verify_v30.py"]
FULL = FAST + ["verify_integration.py", "verify_collect.py", "verify_tgam.py",
               "verify_tgam_fuzz.py", "verify_rrpath.py", "verify_v26.py",
               "verify_v28.py", "verify_v29.py"]
# verify_hw.py 需要真机串口，不自动跑；单独执行。


def run_one(name: str, timeout: int) -> tuple[bool, float, str]:
    path = HERE / name
    if not path.exists():
        return False, 0.0, "文件不存在"
    t0 = time.time()
    try:
        r = subprocess.run([sys.executable, str(path)], cwd=str(HERE),
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
        out = (r.stdout or "") + (r.stderr or "")
        return r.returncode == 0, time.time() - t0, out
    except subprocess.TimeoutExpired:
        return False, time.time() - t0, f"超时（>{timeout}s）"


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="验证脚本总入口")
    ap.add_argument("--full", action="store_true", help="包含接口联调与历史用例（较慢）")
    ap.add_argument("--only", nargs="+", default=None, help="只跑指定脚本")
    ap.add_argument("--list", action="store_true", help="列出分组")
    ap.add_argument("--timeout", type=int, default=180, help="单个脚本超时秒数")
    ap.add_argument("--js", action="store_true", help="附加 node:test 前端纯函数单测")
    args = ap.parse_args(argv)

    if args.list:
        print("快检：", " ".join(FAST))
        print("全量：", " ".join(FULL))
        return 0

    names = args.only or (FULL if args.full else FAST)
    print(f"=== verify_all（{'全量' if args.full else '快检'}，{len(names)} 个）===")
    fails = []
    for name in names:
        ok, sec, out = run_one(name, args.timeout)
        print(f"[{'PASS' if ok else 'FAIL'}] {name:24s} {sec:5.1f}s")
        if not ok:
            fails.append(name)
            tail = "\n".join(out.strip().splitlines()[-12:])
            print("  " + tail.replace("\n", "\n  "))

    if args.js:
        node = shutil.which("node")
        if not node:
            print("[FAIL] node:test（未找到 node）")
            fails.append("node:test")
        else:
            r = subprocess.run([node, "--test", str(HERE / "tools" / "engine.test.cjs")],
                               capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=args.timeout)
            ok = r.returncode == 0
            print(f"[{'PASS' if ok else 'FAIL'}] node:test tools/")
            if not ok:
                fails.append("node:test")
                print("\n".join(((r.stdout or '') + (r.stderr or '')).splitlines()[-12:]))
    print(f"\n结果：{len(names) - len(fails)}/{len(names)} 通过")
    if fails:
        print("失败：" + "、".join(fails))
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
