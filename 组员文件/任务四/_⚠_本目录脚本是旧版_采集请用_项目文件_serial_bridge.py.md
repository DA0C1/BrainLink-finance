# ⚠ 本目录脚本是旧版，不要用来采集

```
本目录：组员文件/任务四/           ← 2026-10-23 的交付与验收证据
权威版：项目文件/serial_bridge.py   ← 采集/录制/回放一律用这个
```

## 为什么

| 本目录的脚本 | 问题 |
|---|---|
| `serial_bridge.py` | 调 `SdkAdapter(engine, signal_min=...)`，而权威适配器只接受 `signal_max` → **一跑必崩** `TypeError`。`--help` 能过，所以很容易以为没问题 |
| `data_recorder.py` | 同一处旧接口，同样必崩 |
| `verify_hardware.py` | 依赖上面两个 |

**关键**：`signal_min` 与 `signal_max` 不只是参数名不同，**语义方向是反的**
（poorSignalLevel 越小越好）。所以**不能靠改名修**，只能整体换权威版。

## 那要采集怎么办

- 补测 5 段：双击根目录 `启动-真机补测采集.cmd`
- 录 CSV / 回放：双击 `启动-数据采集.cmd`，或 `python 项目文件\serial_bridge.py --port COMx --record`
- 这两个入口内部都已锁定权威版，不会走到本目录

## 本目录为什么还留着

任务四的验收证据（含当初的问题记录）。**只读，不要改、不要运行。**
