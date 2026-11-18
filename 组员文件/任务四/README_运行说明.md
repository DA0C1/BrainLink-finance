# 任务四 · 真机工具链 —— 运行说明（4.5 放置问题的处置）

> 交付物：`serial_bridge.py` / `data_recorder.py` / `verify_hardware.py`（2026-10-23 16:11 落盘）
>
> **2026-10-23 晚更新：模块导入问题已修复。** 在三脚本的 `from state_engine import` 之前注入 6 行
> 路径引导（只加 `sys.path`，**业务逻辑一行未动**），`--help` 已能正常输出。
>
> ⚠️ **但"能 `--help`"不等于"能跑"**（2026-10-23 22:00 实测补正，原写法夸大了）：三脚本都调用
> `SdkAdapter(engine, signal_min=...)`，而权威适配器在 `signal` 语义纠错后只接受 `signal_max`，真正执行必崩：
>
> ```
> $ python 组员文件/任务四/serial_bridge.py --mock
> TypeError: SdkAdapter.__init__() got an unexpected keyword argument 'signal_min'.
>            Did you mean 'signal_max'?
> ```
>
> `data_recorder.py`（调用处 `SdkAdapter(engine, signal_min=signal_min)`）同理。
> 这与上面的 `--signal-min` 是同一件事的两面——**旧口径不只是参数名不同，语义方向也是反的**
> （旧：`signal < min` 丢弃，即"越大越好"；新：`signal > max` 丢弃，即"越小越好"），
> 所以**不能靠改名修**，只能整体换权威版。
>
> ⚠️ **但本目录脚本含旧口径，禁止用于正式采集**：组员版 `serial_bridge.py` 仍使用 `--signal-min`
> （即 `signal` 越大越好的错误假设）；权威版已修正为 `--signal-max`（poorSignalLevel
> **0 = 最好、200 = 电极未接触皮肤、越大越差**）。正式采集一律走 `项目文件\` 权威链。
> 本目录保留原逻辑不改，是因为它是 4.1–4.4 的**验收证据**，改了证据就与代码脱节。
> 本目录现在的用途是：**复现组员当时的结论**，不是采集数据。

---

## 一、为什么不能直接跑

三个脚本在模块顶层依赖引擎与适配器：

```python
from state_engine import StateEngine
from sdk_adapter import SdkAdapter, SDKFrame
```

而 `state_engine.py` / `sdk_adapter.py` 位于 `项目文件\`，不在本目录。任务卡 4.5 要求交付到 `项目文件\`，实际交付到 `组员文件\任务四\`，因此：

```
$ python serial_bridge.py --mock
ModuleNotFoundError: No module named 'state_engine'
```

**实测三个文件均如此**（`serial_bridge.py` / `data_recorder.py` / `verify_hardware.py`，exit=1）。

处置：最初选择**不移动、不改写**（保留证据完整性）；后因"整个项目要能拷走即用"，
改为**只注入 6 行路径引导、不碰任何业务逻辑**——证据与逻辑仍对应，但脚本可以独立启动。

注入内容（三份文件相同）：

```python
import os as _os
import sys as _sys

_HERE = _os.path.dirname(_os.path.abspath(__file__))
_CORE = _os.path.abspath(_os.path.join(_HERE, "..", "..", "项目文件"))
if _os.path.isdir(_CORE) and _CORE not in _sys.path:
    _sys.path.insert(0, _CORE)   # 复用项目权威引擎；本目录不复制引擎实现，避免口径漂移
```

用 `_os` / `_sys` 别名是为了不与各文件已有的 `import os` / `import sys` 冲突。
**实测结果**：三个脚本 `--help` 均 `exit=0`，`ModuleNotFoundError` 消失。

> ⚠️ **`--help` 能出 ≠ 能跑**：实测 `serial_bridge.py --mock` 会直接崩溃（exit=1）——
> `TypeError: SdkAdapter.__init__() got an unexpected keyword argument 'signal_min'`。
> 原因：注入的路径引导把 `sdk_adapter` 解析到 `项目文件\` 的**权威版**（签名 `signal_max`），
> 组员版脚本仍按旧接口 `signal_min` 调用——两者签名不匹配。**本目录脚本仅作验收证据，
> 唯一可安全运行的命令是 `--help`；任何实际运行（含 --mock）请走 `项目文件\` 权威链。**

## 二、怎么跑（任选一种）

```bash
# 方式 1：临时注入依赖路径（Linux/macOS/Git Bash）
cd 组员文件/任务四
PYTHONPATH=../../项目文件 python serial_bridge.py --mock

# 方式 2：Windows PowerShell
cd 组员文件\任务四
$env:PYTHONPATH = "..\..\项目文件"; python serial_bridge.py --mock

# 方式 3：cmd
cd 组员文件\任务四
set PYTHONPATH=..\..\项目文件 && python serial_bridge.py --mock
```

三个脚本的可用入口：

| 脚本 | 命令 | 作用 |
|---|---|---|
| `serial_bridge.py` | `--mock` | ⚠️ 实测会崩（`signal_min` 签名不匹配），**勿运行**；仅 `--help` 可用。无真机自检能力见 `项目文件\serial_bridge.py` |
| `data_recorder.py` | `record --output x.csv` / `playback --input x.csv` | 录制到 CSV / 回放喂回引擎 |
| `verify_hardware.py` | `--mock` | 五项断言（V3→V1→V2→V4→V5） |

## 三、阶段 B 真机联调请走权威链

本目录是**交付证据**，不是实操入口。真机现场统一使用 `项目文件\` 下的权威工具：

| 用途 | 权威文件 | 相对本目录交付版多出的能力 |
|---|---|---|
| 串口采集 + 录制 + 回放 | `项目文件\serial_bridge.py` | 逐行 flush（断电不丢尾部）；虚拟时钟注入（mock/回放时间轴正确）；**CSV 含 rr1–rr3 同批三值**；回放按批恢复 `onRR` 语义 |
| 推浏览器实时看 | `项目文件\live_bridge.py` | SSE 零依赖推流、三源（mock/replay/serial）、心跳、路径穿越防护 |
| 五项真机验证 | `项目文件\verify_hw.py` | V4 走**引擎真实代码路径**（可切换 `ARTIFACT_EMG_GATE` 实测）；V1 含 **RMSSD 可达性**断言；含反例断言 |

## 四、一处已互相校正的口径（如实记录）

组员版 `data_recorder.py` 在 **RR 完整性**上比原权威版更正确：

- 录制：`_rr_pending` 保留同一次 `onRR` 的三个间期（`rr1/rr2/rr3` 三列）
- 回放：`adapter.onRR(rr1, rr2, rr3)` 整批喂回

原权威版 `serial_bridge.py` 只落盘最后 1 个间期，且回放写成 `onRR(rr, 0, 0)` —— 每个 RR 各占一个批次，导致 `_rmssd` 内相邻对全部跨批 `continue`、`diffs` 为空、**RMSSD 恒 nan**（stress/anxiety 的关键特征在回放路径上静默失效，而"RR 已入缓冲"这类弱断言照样通过）。

已于 v3.2 按组员版口径修正权威版：CSV 增 `rr1/rr2/rr3`、回放整批恢复、`verify_hw` V1 增 RMSSD 可达性断言 + 反例。

**结论**：平行实现不是重复劳动——这一处是组员版对、组长版错，已吸收。

## 五、⚠ 已知过时口径（2026-10-23 后的组员版请勿直接采信）

本目录三个脚本的 `signal` 门控**方向是反的**，权威链已修正，组员版保持原样（作为 4.1–4.4 证据不动）：

- 组员版：`signal_min` 做下限过滤 + `signal >= 200` 判「良好」
- **实际语义**：`signal` 是 TGAM poorSignalLevel，**0 最好、200 电极未接触皮肤，越大越差**
- 后果：照组员版跑，真机上会**丢掉所有好帧、只放行脱戴帧**；而离线合成数据用 `signal=200/230`
  （超出 0–200 上限）恰好让错误判据永远通过

权威版已改为 `--signal-max`（数值越小门控越严，0=不过滤）。
**实操请一律走 `项目文件/`**，本目录脚本仅作交付证据留存。详见 `我的文件（组长）/阶段B真机联调执行清单_组长E_20261023.md` §1.4。

---

*补记：组长 E · 2026-10-23*
