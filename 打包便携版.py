# -*- coding: utf-8 -*-
"""打包便携版.py — 把整个项目打成可直接拷走的 zip

## 打包什么
全部源码 + 内置运行时 runtime/python/，解压即用，目标机器无需装 Python。

## 排除什么
- 缓存与临时物：__pycache__、*.pyc、Thumbs.db、.DS_Store
- 版本控制/工具目录：.git、.workbuddy
- 历史备份快照：_backup_*
- 真机实验数据：项目文件/data/ 下的 *.csv 与 *.jsonl（含受试者信息，默认不外发；用 --with-data 可带上）
- 盯盘平台运行时数据：任务三 data/ 下的 sessions.db、sessions/、_archive_*
  （含测试会话与受试者标识；同一开关 --with-data 控制，data/README 说明文档始终保留）
- **真机说明书照片：参考资料/真机说明书/*.jpg（20 张、约 95 MB）默认不带**
  —— 它是说明书拍照原件，与"能不能跑起来"无关；JPEG 已是压缩格式，进 zip 也压不动，
  带上会让包从 ~81 MB 涨到 ~176 MB。现场需要时用 `--with-manual` 单独打一份，
  或直接从原盘拷这一个文件夹。

## 刻意**带上**的（别当垃圾清掉）
- `项目文件/_bak_engine_v33/` + `_audit_engine.py`：答辩里"旧→新"的数字由这俩产出，
  缺了就无法复现；`_diff_py.py` / `_diff_js.js` / `_rrpath_js.js` 同理，是 parity 的依赖。
- `runtime/python/Lib/site-packages/` 里的 numpy / fastapi / uvicorn / pydantic（约 66 MB）：
  核心引擎只用 pyserial + 标准库，这些是**任务三盯盘平台**的依赖，不能删。

## 用法
    runtime\\python\\python.exe 打包便携版.py                 # 输出到项目上一级目录
    ... --out D:\\备份\\脑机接口.zip                          # 指定输出路径
    ... --with-data                                          # 连录制数据一起打
    ... --with-manual                                        # 连说明书照片一起打（+95 MB）
    ... --dry-run                                            # 只统计不打包
"""
from __future__ import annotations

import argparse
import os
import sys
import time
import zipfile

ROOT = os.path.dirname(os.path.abspath(__file__))

SKIP_DIR_NAMES = {".git", ".workbuddy", "__pycache__", ".idea", ".vscode",
                  ".slidep",   # slidep 的构建缓存与命令日志，不进包
                  ".build",    # 汇报 PPT 的拼装产物（含 base64 图，可重建）
                  ".preview"}  # 汇报 PPT 的逐页渲染图（仅本地目检用）
# _archive_* = 已归档的原图/旧物（2026-11-11 起）。它们只用于本地留证，
# 进包会让便携包从 32 MB 涨到 120 MB，必须排除。
SKIP_DIR_PREFIX = ("_backup", "_archive", "_旧版")   # _旧版：被取代的历史 PPT
SKIP_FILE_NAMES = {"Thumbs.db", ".DS_Store", "desktop.ini"}
SKIP_SUFFIX = (".pyc", ".pyo")

# 任务三平台的运行时数据：含测试会话/受试者标识，默认不外发（与 项目文件/data/*.csv 同理）
WATCHLAB_DATA = "组员文件/任务三/_验收_解压/盯盘实验平台_成员D_20261023/data/"
WATCHLAB_DATA_KEEP = "README_数据目录说明.md"   # 说明文档保留，否则别人不知道 data 里该放什么

# 真机说明书拍照：2026-11-11 已重编码为 q85（94.6 MB → 22.9 MB），原图在 _archive_20261111/。
# 仍是"参考资料"，默认不带，--with-manual 才带。
MANUAL_DIR = "参考资料/真机说明书/"

# 10-16 冻结快照：含 6 处旧版核心代码（signal 方向相反、旧 engine），是"组员解压后
# 从「分发」目录里拿错代码"的经典来源。**默认排除**，组长自己留档时用 --with-snapshot。
SNAPSHOT_DIRS = ("组员分发_20261016/", "组员分发zip_20261016/")

# 被取代的答辩 PPT 已全部归档到 `项目汇报/答辩_脑机接口提醒层/_旧版/`（2026-11-12），
# `_旧版` 整目录由 SKIP_DIR_PREFIX 排除，因此不再需要按文件名单独挡。
# 终版只有一份：`答辩_脑机接口提醒层_终版.pptx`（+ 同名 .pdf 兜底）。
SUPERSEDED_PPTX: set[str] = set()


def is_watchlab_runtime_data(rel: str) -> bool:
    if not rel.startswith(WATCHLAB_DATA):
        return False
    rest = rel[len(WATCHLAB_DATA):]
    if rest == WATCHLAB_DATA_KEEP:
        return False
    # sessions.db / sessions.db-wal / sessions/ / _archive_* 全部视为运行时数据
    return True


def collect_files(with_data: bool, with_manual: bool, out_path: str,
                  with_snapshot: bool = False):
    """返回 (待打包 [(绝对路径, 相对路径)], 被排除的说明书照片 [绝对路径])。"""
    picked: list[tuple[str, str]] = []
    skipped_manual: list[str] = []
    out_abs = os.path.abspath(out_path)
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = sorted(
            d for d in dirnames
            if d not in SKIP_DIR_NAMES and not d.startswith(SKIP_DIR_PREFIX)
        )
        for name in sorted(filenames):
            if name in SKIP_FILE_NAMES or name.endswith(SKIP_SUFFIX):
                continue
            full = os.path.join(dirpath, name)
            if os.path.abspath(full) == out_abs:
                continue
            rel = os.path.relpath(full, ROOT).replace(os.sep, "/")
            if not with_manual and rel.startswith(MANUAL_DIR):
                skipped_manual.append(full)
                continue
            if not with_snapshot and rel.startswith(SNAPSHOT_DIRS):
                continue
            if rel in SUPERSEDED_PPTX:
                continue
            # v3.16：assets/raw_* 是 ComfyUI 生成的原图（828 KB），运行时只用处理后的
            # 透明线稿；原图留在本地便于复现/manifest 审计，不进包。
            if rel.startswith("组员文件/任务一/assets/raw_"):
                continue
            # 受试者原始数据一律不带（EEG/HRV 属敏感数据，外发前必须挡住）：
            #   .csv  = serial_bridge 的录制回放
            #   .jsonl = collect_real 的原始 dump（**旧版漏了这一类**，真机首采后才发现）
            # 例外：demo_ 前缀是 make_demo_csv.py 用 mock 合成的演示数据，不含任何受试者
            # 信息。包里若一个 CSV 都没有，「启动-实时面板」的"3=回放"就是空转，
            # 现场真机故障时也少一条退路（v3.16.2 加此白名单）。
            if (not with_data and rel.startswith("项目文件/data/")
                    and rel.lower().endswith((".csv", ".jsonl"))
                    and not os.path.basename(rel).startswith("demo_")):
                continue
            if not with_data and is_watchlab_runtime_data(rel):
                continue
            picked.append((full, rel))
    return picked, skipped_manual


def human(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
        n /= 1024.0
    return f"{n:.1f} GB"


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="打包脑机接口项目便携版")
    ap.add_argument("--out", help="输出 zip 路径（默认：项目上一级的 脑机接口项目_便携版_时间戳.zip）")
    ap.add_argument("--with-data", action="store_true", help="连 项目文件/data/*.csv|*.jsonl 一起打包（含受试者数据）")
    ap.add_argument("--with-manual", action="store_true",
                    help=f"连 {MANUAL_DIR} 说明书照片一起打包（约 +95 MB，JPEG 压不动）")
    ap.add_argument("--dry-run", action="store_true", help="只统计，不写文件")
    ap.add_argument("--with-snapshot", action="store_true",
                    help="连 组员分发_20261016/ 与 组员分发zip_20261016/ 旧快照一起打包"
                         "（默认排除：快照含旧版代码，组员容易从那里拿错）")
    args = ap.parse_args(argv)

    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = args.out or os.path.join(
        os.path.dirname(ROOT), f"脑机接口项目_便携版_{stamp}.zip")

    files, skipped_manual = collect_files(args.with_data, args.with_manual, out,
                                          with_snapshot=args.with_snapshot)
    total = sum(os.path.getsize(f) for f, _ in files)

    print(f"项目根：{ROOT}")
    print(f"文件数：{len(files)}")
    print(f"原始大小：{human(total)}")
    if not args.with_data:
        print("（已排除 项目文件/data/*.csv|*.jsonl；如需包含加 --with-data）")
    if not args.with_snapshot:
        print("（已排除 组员分发_20261016/ 与 组员分发zip_20261016/ 旧快照；"
              "含旧版代码，组长留档时加 --with-snapshot）")
    if skipped_manual:
        mb = sum(os.path.getsize(p) for p in skipped_manual)
        print(f"（已排除 {MANUAL_DIR}：{len(skipped_manual)} 张、{human(mb)}；"
              f"如需包含加 --with-manual）")

    # 完整性自检：少了运行时或核心代码，打出来的包就是废的
    need = ["runtime/python/python.exe", "runtime/python/python313._pth",
            "项目文件/state_engine.py", "项目文件/doctor.py"]
    present = {rel for _, rel in files}
    missing = [n for n in need if n not in present]
    if missing:
        print(f"[错误] 缺少关键文件，拒绝打包：{', '.join(missing)}")
        return 2

    if args.dry_run:
        print("（dry-run，未写出文件）")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for full, rel in files:
            zf.write(full, arcname=f"脑机接口项目/{rel}")

    size = os.path.getsize(out)
    print(f"已生成：{out}")
    print(f"压缩后：{human(size)}（压缩率 {size / total:.0%}）")
    print("解压后双击 启动-环境自检.cmd 即可验证。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
