#!/usr/bin/env python3
"""_lint.py — 项目自带的轻量静态检查（无第三方依赖）。

为什么需要它：本项目此前靠"跑测试"兜底，但测试只覆盖**被调用到的路径**。
下面这些问题测试永远看不见，却会在真机现场或答辩追问时暴露：

  1. 未使用的导入 / 常量（复制粘贴残留，读代码的人会以为它有用）
  2. 同一作用域内的重复定义（后者静默覆盖前者，行为取决于书写顺序）
  3. 全项目无人引用的函数（死代码，改判据时容易改错那一份）
  4. 可变默认参数（def f(x=[])，跨调用共享状态）
  5. 裸 except / except...pass（吞掉异常，故障现场无任何线索）
  6. TODO/FIXME/XXX 遗留标记
  7. docstring 里写的数字与常量实际值不符（文档漂移的代码侧入口）

用法：
    python _lint.py                 # 检查 项目文件/ 与上级的 打包便携版.py
    python _lint.py --all           # 连 组员文件/ 一起查
    python _lint.py 某文件.py        # 只查一个

退出码：0 = 无 ERROR（WARN 不算失败）；1 = 有 ERROR。
"""

from __future__ import annotations

import ast
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SKIP_DIRS = {"_bak_engine_v33", "__pycache__", "runtime", "_验收_解压"}

# 检查结果：(级别, 文件, 行号, 规则, 说明)
Rows: list[tuple[str, str, int, str, str]] = []


def add(level, path, line, rule, msg):
    Rows.append((level, path, line, rule, msg))


def rel(path: str) -> str:
    try:
        return os.path.relpath(path, os.path.dirname(HERE)).replace("\\", "/")
    except ValueError:
        return path


# ------------------------------------------------------------------ 采集
def py_files(root: str) -> list[str]:
    out = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fn in filenames:
            if fn.endswith(".py"):
                out.append(os.path.join(dirpath, fn))
    return sorted(out)


def read(path: str) -> str:
    with io.open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


# ------------------------------------------------------------------ 规则
def check_imports(path, tree, src):
    """未使用的导入。特别关注 `import x` / `from x import y` 后 y 从未出现。"""
    bound = {}          # name -> (lineno, 原文)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                name = (a.asname or a.name).split(".")[0]
                bound[name] = (node.lineno, f"import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.module == "__future__":       # 编译期语义，不产生名字引用
                continue
            for a in node.names:
                if a.name == "*":
                    continue
                bound[a.asname or a.name] = (node.lineno, f"from {node.module} import {a.name}")
    # 有 # noqa 的行豁免（这类导入是"故意只为触发模块级副作用/依赖检查"）
    noqa = {i for i, ln in enumerate(src.splitlines(), 1) if "noqa" in ln.lower()}
    used = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            used.add(node.id)
    for name, (line, text) in bound.items():
        if name in used or name in noqa or line in noqa:
            continue
        add("WARN", path, line, "unused-import", f"{text}（{name} 全文件未使用）")


# @x.setter / @x.getter / @x.deleter 是属性的合法"再定义"，不是重复
_PROP_DECOR = {"setter", "getter", "deleter"}


def _is_prop_redef(node) -> bool:
    for d in node.decorator_list:
        if isinstance(d, ast.Attribute) and d.attr in _PROP_DECOR:
            return True
    return False


def check_dup_defs(path, tree):
    """同一作用域重复定义函数/类（后者静默覆盖前者）。"""
    def scan(body, scope):
        seen = {}
        for node in body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                if _is_prop_redef(node):
                    continue
                key = (type(node).__name__, node.name)
                if key in seen:
                    add("ERROR", path, node.lineno, "duplicate-def",
                        f"{scope}{node.name} 在第 {seen[key]} 行已定义，此处重复（后者覆盖前者）")
                else:
                    seen[key] = node.lineno
                scan(node.body, f"{scope}{node.name}.")
    scan(tree.body, "")


def check_mutable_default(path, tree):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for d in list(node.args.defaults) + [x for x in node.args.kw_defaults if x]:
                if isinstance(d, (ast.List, ast.Dict, ast.Set)):
                    add("ERROR", path, node.lineno, "mutable-default",
                        f"{node.name}() 用可变对象当默认参数（跨调用共享，会被上一次调用污染）")


def check_bare_except(path, tree, src):
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            if node.type is None:
                add("WARN", path, node.lineno, "bare-except", "裸 except（连 KeyboardInterrupt/SystemExit 一起吞）")
            body_empty = all(isinstance(s, ast.Pass) for s in node.body)
            # 豁免：控制流类异常（Ctrl+C / SystemExit / 协程取消）本就是"正常结束路径"，
            # 空 pass 是公认正当写法，记日志反而制造噪音。
            def _ctl_name(e) -> bool:
                if isinstance(e, ast.Name):
                    return e.id in ("KeyboardInterrupt", "SystemExit", "CancelledError")
                if isinstance(e, ast.Attribute):          # asyncio.CancelledError
                    return e.attr in ("CancelledError", "KeyboardInterrupt", "SystemExit")
                return False

            ctl_exit = _ctl_name(node.type)
            if not ctl_exit and isinstance(node.type, ast.Tuple):
                # except (asyncio.CancelledError, Exception): 这类组合写法同样豁免
                ctl_exit = any(_ctl_name(e) for e in node.type.elts)
            if body_empty and node.type is not None and not ctl_exit:
                # 仅报"完全没有注释/日志"的静默吞掉
                lines = src.splitlines()
                seg = "\n".join(lines[node.lineno - 1: node.end_lineno or node.lineno])
                if not re.search(r"#|log|print|warn", seg, re.I):
                    add("ERROR", path, node.lineno, "silent-except",
                        "except 后直接 pass，无注释无日志（故障现场没有线索）")


def check_markers(path, src):
    if os.path.basename(path) == "_lint.py":     # 本文件自身的规则串不算遗留标记
        return
    for i, line in enumerate(src.splitlines(), 1):
        m = re.search(r"\b(TODO|FIXME|XXX|HACK)\b", line)
        if m:
            add("WARN", path, i, "marker", line.strip()[:100])


def check_unused_module_defs(files):
    """全项目无人引用的顶层函数/类。导出用的（__all__ / 名字在文档中被引用）不报。"""
    defs = {}       # name -> (path, line)
    for path in files:
        try:
            tree = ast.parse(read(path))
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) and not node.name.startswith("_"):
                defs.setdefault(node.name, (path, node.lineno))
    corpus = {p: read(p) for p in files}
    for name, (path, line) in defs.items():
        hits = 0
        for p, s in corpus.items():
            hits += len(re.findall(rf"\b{re.escape(name)}\b", s))
        # 定义本身算 1 次；只有 1 次 = 谁都没用
        if hits <= 1:
            add("WARN", path, line, "dead-code", f"{name} 全项目无引用")


def check_module_constants(path, tree, src):
    """模块级常量定义后从未被读取（拼写错或早期版本残留）。"""
    assigned = []
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    assigned.append((t.id, node.lineno))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if node.target.id.isupper():
                assigned.append((node.target.id, node.lineno))
    lines = src.splitlines()
    for name, line in assigned:
        # 统计除定义行外的出现次数
        occ = len(re.findall(rf"\b{re.escape(name)}\b", src))
        if occ <= 1:
            # 定义了但"故意无消费方"的接口预留常量：注释里写明理由的不报
            ctx = "".join(lines[max(0, line - 4):line])
            if re.search(r"预留|保留为|无消费方|接口位|暂未使用", ctx):
                continue
            add("WARN", path, line, "dead-const", f"常量 {name} 定义后未被任何代码读取")


DOC_NUM = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:×|倍)")


def check_doc_number_drift(path, tree, src):
    """docstring 里写 'N×' 而模块常量是别的值 → 文档与代码漂移。

    本项目踩过一次：`ratio_artifact` 的 docstring 写"8×"，常量实际已改 6×。
    """
    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
            if isinstance(node.value.value, (int, float)):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        consts[t.id] = node.value.value
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.ClassDef)):
            doc = ast.get_docstring(node)
            if not doc:
                continue
            for m in DOC_NUM.finditer(doc):
                num = float(m.group(1))
                for cname, cval in consts.items():
                    if abs(num - float(cval)) < 1e-9:
                        break
                else:
                    # 只在该文件确实有 RATIO 类常量时才提示可能漂移
                    ratios = [v for k, v in consts.items() if "RATIO" in k]
                    if ratios and num in (8.0, 15.0, 6.0, 5.0):
                        add("WARN", path, getattr(node, "lineno", 1), "doc-drift",
                            f"文档写 {num:g}×，但文件内 RATIO 类常量为 {ratios}——请核对")


# ------------------------------------------------------------------ 主流程
def lint_one(path):
    src = read(path)
    try:
        tree = ast.parse(src, filename=path)
    except SyntaxError as e:
        add("ERROR", path, e.lineno or 0, "syntax", f"语法错误：{e.msg}")
        return
    check_imports(path, tree, src)
    check_dup_defs(path, tree)
    check_mutable_default(path, tree)
    check_bare_except(path, tree, src)
    check_markers(path, src)
    check_module_constants(path, tree, src)
    check_doc_number_drift(path, tree, src)


def main(argv):
    targets = []
    args = [a for a in argv[1:] if not a.startswith("-")]
    if args:
        targets = [os.path.abspath(a) for a in args]
    else:
        targets = py_files(HERE)
        parent = os.path.join(os.path.dirname(HERE), "打包便携版.py")
        if os.path.exists(parent):
            targets.append(parent)
        if "--all" in argv:
            targets += py_files(os.path.join(os.path.dirname(HERE), "组员文件"))

    for p in targets:
        lint_one(p)

    if "--all" in argv or not args:
        check_unused_module_defs([p for p in targets if p.startswith(HERE)])

    errs = [r for r in Rows if r[0] == "ERROR"]
    warns = [r for r in Rows if r[0] == "WARN"]
    for level, path, line, rule, msg in sorted(Rows, key=lambda r: (r[1], r[2])):
        print(f"[{level:5s}] {rel(path)}:{line}  {rule:16s} {msg}")
    print(f"\n{len(targets)} 个文件 · ERROR {len(errs)} · WARN {len(warns)}")
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
