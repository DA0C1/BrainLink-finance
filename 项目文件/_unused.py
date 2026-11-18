# -*- coding: utf-8 -*-
"""未使用资源扫描：CSS 类 / JS 常量 / Python 常量。

只做「定义处之外零引用」的机械判定，输出候选清单供人工确认：
- 有些类是运行时拼出来的（`'is-' + x`），会被误判，需看引用形态
- 有些常量是对外口径（如 K.* 给其他文件用），需看是否被 import
"""
from __future__ import annotations

import io
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
UI = os.path.join(ROOT, "..", "组员文件", "任务一")


def read(p):
    return io.open(p, encoding="utf-8", errors="ignore").read()


def walk_text(*roots, exts=(".py", ".js", ".html", ".md", ".css"), skip=("runtime", "__pycache__", "_bak", "组员分发", "node_modules")):
    for base in roots:
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in skip]
            if any(k in dirpath for k in skip):
                continue
            for f in filenames:
                if f.endswith(exts):
                    yield os.path.join(dirpath, f)


# ---------------------------------------------------------------- CSS 类
def css_classes(path):
    src = read(path)
    src = re.sub(r"/\*[\s\S]*?\*/", "", src)
    # 先摘掉 url(...) 里的内容：assets/empty-remind-light.png 会被误当成类名 .png
    src = re.sub(r"url\([^)]*\)", "", src)
    return sorted({m.group(1) for m in re.finditer(r"\.([a-zA-Z][\w-]*)", src)})


def check_css():
    css = os.path.join(UI, "css", "styles.css")
    classes = css_classes(css)
    # 引用面：HTML + 所有 JS
    refs = "\n".join(read(p) for p in [os.path.join(UI, "index.html")]
                     + [os.path.join(UI, "js", f) for f in os.listdir(os.path.join(UI, "js")) if f.endswith(".js")])
    unused = []
    for c in classes:
        # 类名可能出现在 class="a b"、classList.add('x')、模板字符串里
        if re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(c), refs):
            continue
        unused.append(c)
    return classes, unused


# ---------------------------------------------------------------- JS 常量
def check_js_const():
    eng = os.path.join(UI, "js", "engine.js")
    src = read(eng)
    m = re.search(r"const K = \{([\s\S]*?)\n  \};", src)
    if not m:
        return [], []
    keys = re.findall(r"^\s{4}([A-Z][A-Z0-9_]*)\s*:", m.group(1), re.M)
    allsrc = "\n".join(read(p) for p in walk_text(UI))
    unused = [k for k in keys if len(re.findall(r"\b%s\b" % k, allsrc)) <= 1]
    return keys, unused


# ---------------------------------------------------------------- Python 常量
def check_py_const():
    se = os.path.join(ROOT, "state_engine.py")
    src = read(se)
    names = re.findall(r"^([A-Z][A-Z0-9_]+)\s*=", src, re.M)
    allsrc = "\n".join(read(p) for p in walk_text(ROOT, UI))
    unused = [n for n in names if len(re.findall(r"\b%s\b" % n, allsrc)) <= 1]
    return names, unused


print("=== CSS 类 ===")
cls, un_css = check_css()
print("定义 %d 个，未被 HTML/JS 引用 %d 个" % (len(cls), len(un_css)))
if un_css:
    print("  " + ", ".join("." + c for c in un_css))

print("\n=== engine.js 常量 K ===")
k, un_k = check_js_const()
print("定义 %d 个，零引用 %d 个" % (len(k), len(un_k)))
if un_k:
    print("  " + ", ".join(un_k))

print("\n=== state_engine.py 模块常量 ===")
pyn, un_py = check_py_const()
print("定义 %d 个，零引用 %d 个" % (len(pyn), len(un_py)))
if un_py:
    print("  " + ", ".join(un_py))
