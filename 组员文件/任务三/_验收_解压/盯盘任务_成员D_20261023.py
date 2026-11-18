# -*- coding: utf-8 -*-
"""BCI 模拟盯盘实验平台 · 启动器（任务三交付入口）

用法：
    python 盯盘任务_成员D_20261023.py              # 默认 127.0.0.1:8000，自动打开浏览器
    python 盯盘任务_成员D_20261023.py --port 8080  # 指定端口
    python 盯盘任务_成员D_20261023.py --no-browser # 不自动打开浏览器

研究者口令：启动前设置环境变量 WATCHLAB_RESEARCH_TOKEN，未设置时为 research-demo-token。
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP_DIR = ROOT / "盯盘实验平台_成员D_20261023"

if not (APP_DIR / "backend" / "main.py").is_file():
    raise SystemExit(f"找不到平台目录：{APP_DIR}")

sys.path.insert(0, str(APP_DIR))
os.chdir(APP_DIR)


def main() -> None:
    parser = argparse.ArgumentParser(description="BCI 模拟盯盘实验平台")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--no-browser", action="store_true")
    args = parser.parse_args()

    import uvicorn

    url = f"http://{args.host}:{args.port}/"
    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    print(f"\nWatchLab 已启动：{url}")
    print(f"研究者口令：{os.environ.get('WATCHLAB_RESEARCH_TOKEN', 'research-demo-token')}\n")
    uvicorn.run("backend.main:app", host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
