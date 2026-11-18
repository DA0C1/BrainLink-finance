# -*- coding: utf-8 -*-
"""冗余代码扫描：找出「定义了但没人用」的函数 / 方法。

做法：Python 用 ast 收集每个文件里的函数名，再在整个项目（py+js+html+md）里
搜引用；JS 用正则收集具名函数。框架隐式调用的入口用白名单排除。

输出按「文件:行 名字」列出，供人工判定是保留（对外 API/测试辅助）还是删除。
用法：python _deadcode.py
"""
from __future__ import annotations

import ast
import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
SCAN_DIRS = [ROOT, os.path.join(ROOT, "..", "组员文件", "任务一")]

# 隐式调用 / 对外入口 / 测试用，不算冗余
WHITELIST = {
    "main", "do_GET", "do_HEAD", "do_POST", "handle_error", "log_message",
    "__init__", "__enter__", "__exit__", "__repr__", "__str__", "__call__",
    "setup", "teardown", "run", "check",
}


def py_files():
    for base in SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for f in sorted(os.listdir(base)):
            if f.endswith(".py"):
                yield os.path.join(base, f)


def js_files():
    d = os.path.join(ROOT, "..", "组员文件", "任务一", "js")
    if os.path.isdir(d):
        for f in sorted(os.listdir(d)):
            if f.endswith(".js"):
                yield os.path.join(d, f)


def corpus() -> str:
    """全部可引用文本（py/js/html/md），递归收集，排除运行时与备份。"""
    buf = []
    skip = ("runtime", "__pycache__", "_bak", "组员分发", "node_modules", "参考资料")
    for base in SCAN_DIRS:
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in skip]
            if any(k in dirpath for k in skip):
                continue
            for f in filenames:
                if f.endswith((".py", ".js", ".html", ".md")):
                    try:
                        buf.append(io.open(os.path.join(dirpath, f), encoding="utf-8",
                                           errors="ignore").read())
                    except OSError as exc:      # 单个文件读不了不影响整轮统计，但留痕便于排查
                        print("[skip] %s: %s" % (f, exc))
    return "\n".join(buf)


def py_defs(path: str):
    src = io.open(path, encoding="utf-8", errors="ignore").read()
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    out = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            out.append((node.name, node.lineno, "top"))
        elif isinstance(node, ast.ClassDef):
            for sub in node.body:
                if isinstance(sub, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    out.append((f"{node.name}.{sub.name}", sub.lineno, "method"))
    return out


def js_defs(path: str):
    src = io.open(path, encoding="utf-8", errors="ignore").read()
    out = []
    for m in re.finditer(r"^\s*(?:async\s+)?function\s+([A-Za-z_$][\w$]*)\s*\(", src, re.M):
        line = src[:m.start()].count("\n") + 1
        out.append((m.group(1), line, "js"))
    for m in re.finditer(r"^\s*(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s*)?\(", src, re.M):
        line = src[:m.start()].count("\n") + 1
        out.append((m.group(1), line, "js-fn"))
    return out


def main() -> None:
    text = corpus()
    rows = []

    for p in py_files():
        for name, line, kind in py_defs(p):
            short = name.split(".")[-1]
            if short in WHITELIST or short.startswith("_"):
                continue
            # 统计除定义处以外的出现次数（含被其他模块 import/调用）
            n = len(re.findall(r"\b%s\b" % re.escape(short), text))
            if n <= 1:
                rows.append((os.path.relpath(p, ROOT), line, name, "Python", n))

    for p in js_files():
        for name, line, kind in js_defs(p):
            if name in WHITELIST:
                continue
            n = len(re.findall(r"\b%s\b" % re.escape(name), text))
            if n <= 1:
                rows.append((os.path.relpath(p, ROOT), line, name, "JS", n))

    print("未发现引用（全项目文本计数 ≤1）的函数：%d 个\n" % len(rows))
    if rows:
        print("%-26s %6s %-30s %-7s %s" % ("文件", "行", "名字", "语言", "出现次数"))
        for f, l, name, kind, n in sorted(rows):
            print("%-26s %6d %-30s %-7s %d" % (f, l, name, kind, n))
        print("\n注意：计数为 1 = 只有定义本身。可能是①真冗余 ②对外 API ③被字符串/反射调用，需人工判定。")


if __name__ == "__main__":
    main()
