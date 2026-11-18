# `_bak_engine_v33/` — v3.4 改造前的留证快照（只读）

**这两个文件不是现行代码，不要运行、不要改。**

| 文件 | 是什么 |
|---|---|
| `state_engine_v33.py` | `项目文件/state_engine.py` 在 v3.4 算法层重设计**之前**的原样快照 |
| `engine_v33.js` | `组员文件/任务一/js/engine.js` 同期快照 |

## 为什么留着

`项目文件/_audit_engine.py` 用它产出"旧 → 新"的量化对照（设计文档 §九 v3.4、答辩口径里的
所有数字都来自这里）：

```
runtime\python\python.exe _audit_engine.py                          # 审现行引擎
runtime\python\python.exe _audit_engine.py _bak_engine_v33/state_engine_v33.py   # 审旧快照
```

删掉它，这些数字就只能"凭记忆引用"——本项目已经吃过一次"文档承诺、代码无此机制"的亏
（§四 的 `SLEEP_MIGRATE_S`），所以宁可留证。

## 现行权威

- Python：`项目文件/state_engine.py`
- JS：`组员文件/任务一/js/engine.js`（唯一例外：JS 侧权威，改判别逻辑必须与 Python 同改并跑 `verify_parity.py`）
