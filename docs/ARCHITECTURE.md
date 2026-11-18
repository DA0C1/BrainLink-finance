# 架构与模块边界（v3.16）

## 一、分层

```
采集 / 回放源                传输 / 编排                 判据                         呈现
─────────────────────────────────────────────────────────────────────────────────────────
tgam_parser.py   ─┐
sdk_adapter.py    ├─► state_engine.py ──► analyze_real / output_demo
collect_real.py   │      （Python 引擎）        （离线报告）
serial_bridge.py  │
                  └─► live_bridge.py ──SSE──► engine.js ──► app.js ──► index.html/css
                       （HTTP/SSE）           （JS 引擎，与 py 1:1）   （UI 编排）
frameio.py  ── 两条回放路径共用的唯一 CSV→帧转换
mockdata.py ── 演示相位/合成帧（生产引擎不再背演示数据）
```

依赖方向单向：parser/adapter → engine → bridge/UI。`frameio` 只依赖标准库，被
`serial_bridge` 与 `live_bridge` 共同引用；`mockdata` 延迟导入 `state_engine`，避免
`python state_engine.py` 时出现两份模块。

## 二、为什么有 Python 和 JS 两套引擎

- Python 引擎：离线分析、校准基线、验收脚本、真实数据复算；
- JS 引擎：浏览器内实时判别，断网/无 Python 也能演示。
- 两者必须逐位一致：`verify_parity.py` 5 道闸门（常量 / 基线 / 伪迹 / 轨迹 / 参数）
  + `analyze_real.py --parity` 真实轨迹 172/172。
- **改判据必须两侧同时改**；只改一侧会被对拍闸门拦下。常量清单在
  `verify_parity.PARAM_KEYS`，新增常量要同步登记。

## 三、前端模块现状

| 文件 | 行数（约） | 职责 |
|---|---|---|
| `js/util.js` | 60 | 存储/格式化/转义/防抖（v3.16 从 app.js 拆出，无依赖） |
| `js/chart.js` | 189 | 理智度曲线绘制（v3.16 从 app.js 拆出，configure 注入 el/S） |
| `js/icons.js` | 17 符号 | 单色 SVG 图标 |
| `js/engine.js` | 1324 | 判据、校准、RMSSD、伪迹（与 Python 对拍） |
| `js/mock.js` | — | 演示帧生成 |
| `js/bridge.js` | — | SSE 客户端、队列/间隔/token |
| `js/app.js` | 2311 | UI 编排：DOM 绑定、渲染、校准流程、提醒、设置、导入导出 |

`app.js` 仍是单体（90+ 函数）。v3.16 已拆出 `util.js` 与 `chart.js`；后续建议按
`calibration.js`（三段采集/导入导出）、`reminders.js`（等级/Toast/语音）、
`settings.js`（表单/诊断）分批拆。
**不要在判据改动的同一提交里做大拆分**——两者混在一起无法定位回归来源。

拆分方式保持 classic script（`<script src>`，无构建、`file://` 可开）：
每个文件顶层声明函数/常量，按 `util → icons → engine → mock → bridge → 业务模块 → app` 顺序加载。

## 四、扩展点（改哪里）

| 要加什么 | 必须改的文件 | 验证 |
|---|---|---|
| 新状态 | `state_engine.PRIORITY`、`engine.js K.PRIORITY/STATE_META`、README §8、`verify_parity.PARAM_KEYS` | `verify_parity` / `verify_engine` / e2e 8 格 |
| 新帧字段 | `state_engine.Frame`、`engine.js Frame`、`live_bridge.FRAME_KEYS`、`frameio`、`CSV_FIELDS` | `verify_frontend` F5（14 字段）/ `verify_data` D9 |
| 新判据常量 | 两侧常量 + `verify_parity.PARAM_KEYS` | 对拍闸门 1 |
| 新 CSV 列 | `serial_bridge.CSV_FIELDS`、`export_csv`（自动继承）、`frameio` | `verify_data` D9 / `verify_frontend` |
| 新 HTTP 端点 | `live_bridge.Handler.do_GET` + 安全头/token | `verify_integration` |

## 五、非目标

- 不做多用户/云端同步：数据只在本机，便携包不带 `data/`。
- 不引入前端框架/打包器：`file://` 直接可开是答辩现场的硬要求。
- 不追求 8 态都达到临床级：证据强度分级见 README §8.1，C 档只说"与该状态一致的模式"。
