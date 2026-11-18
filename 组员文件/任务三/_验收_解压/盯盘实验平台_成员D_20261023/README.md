# WatchLab · BCI 模拟盯盘实验平台

> 任务三交付物｜成员 D｜2026-10-23
> 项目：脑机接口数据在金融实践中的应用 · 第二阶段

## 这是什么

一个浏览器端的模拟盯盘实验平台。受试者在受控行情里自主买卖，服务端在后台接入脑电状态引擎；**有反馈轮**把状态提醒推给受试者，**无反馈轮**完全静默，最后用三项指标比较两轮差异。

它**不是**实盘炒股软件，也**不是**医疗或投资建议工具。

## 三条硬约束

1. **未来不可见**：价格与事件在服务端预生成，但只逐 tick 推送已经发生的数据；受试者快照/WS 不含未来价格、未来事件、随机种子。
2. **人类节奏**：正式实验固定 2 秒 / tick、单轮 10 分钟（300 个点），给受试者留出看盘与决策时间；界面采用真实交易终端的黑底、红涨青跌、黄色数字配色。
3. **反馈是唯一变量**：两轮使用同一随机种子、同一价格剧本、同一事件表；唯一差别是有/无状态提醒。

## 谁在用

| 角色 | 入口 | 做什么 |
|---|---|---|
| 受试者 | `/s/{6位会话码}` | 阅读指导语、勾选知情同意、进入盯盘、自主买卖 |
| 研究者 | `/research` | 三步走：建会话 → 发会话码 → 看指标/导出 |
| 评审/老板 | `/` + 演示按钮 | 3 分钟走通完整产品流程，不采集数据 |

## 快速启动

```powershell
cd 项目文件\盯盘实验平台_成员D_20261023
python -m pip install -r requirements.txt
python ..\盯盘任务_成员D_20261023.py
```

或双击 `run.bat` / 执行 `run.ps1`。默认打开 `http://127.0.0.1:8000/`。

- 研究者口令默认 `research-demo-token`，可用环境变量 `WATCHLAB_RESEARCH_TOKEN` 覆盖。
- 端口：`python ..\盯盘任务_成员D_20261023.py --port 8080`
- 不自动开浏览器：加 `--no-browser`

## 三种模式

| 模式 | tick | 单轮时长 | 数据 | 用途 |
|---|---|---|---|---|
| `experiment` 正式实验 | 2000ms | 600s | 全量落盘 | 受试者正式实验 |
| `test` 测试 | <2000ms | 自定 | 全量落盘 + 界面水印 | 研究者自检，不得给受试者 |
| `demo` 演示 | 2000ms | 180s | 不落实验日志 | 对外演示，自动成交，节奏与正式实验一致 |

研究者端创建会话时，`tick 间隔 < 2000ms` 会自动进入 `test` 模式并显示“测试模式·非正式实验”水印。

## 实验流程

1. 研究者创建会话：填受试者编号 → “一键建两轮”（同种子，顺序随机）；顺序、种子、时长、tick、引擎收在“高级设置（一般不用改）”里，默认 600s / 2000ms / Mock。
2. 把 `/s/{会话码}` 发给受试者；受试者阅读说明、勾选同意、进入。
3. 受试者点击“开始盯盘”→ 3 秒倒计时 → 服务端开始计时，按固定节奏（正式实验 2 秒）推送新价格。
4. 盘中三类事件弹窗：追涨诱导 / 突发消息 / 波动冲击，8 秒自动消失，不可关闭。
5. 有反馈轮按引擎状态推右上角提醒 + 中文语音；无反馈轮不推任何状态提醒。
6. 结束后页面提示“本轮完成”，研究者导出 zip（CSV + session.json）。

## 数据与指标

每个会话目录 `data/sessions/<session_id>/`：

| 文件 | 内容 |
|---|---|
| `prices.csv` | t, tick, price, segment |
| `decisions.csv` | 21 字段，含 engine_state / engine_conf / is_impulsive / trigger_event |
| `events.csv` | 弹窗时间、tick、类型、文案、关闭时间 |
| `states.csv` | 逐 tick 状态、置信度、是否冲动 |
| `interventions.csv` | 提醒时间、tick、状态、等级、话术、通道 |
| `session.json` | 元数据 + 最终指标 |

三项指标（与 T2 方案一致）：

- `calmness = 1 − 冲动决策数 / 总决策数`（无决策记 1.0）
- `focus_ratio = (focus + flow) 时长 / 有效盯盘时长`（剔除 neutral）
- `intervention_response_rate = 60 秒内状态改善次数 / 有效干预次数`

## 目录结构

```text
盯盘实验平台_成员D_20261023/
├── backend/
│   ├── config.py       # 全局口径、事件枚举、日志字段
│   ├── simulation.py   # 价格引擎、事件调度、账户
│   ├── engine.py       # MockEngine / state_engine 适配
│   ├── session.py      # 会话运行时：时钟、tick、交易、反馈、指标
│   ├── storage.py      # CSV + SQLite 双写、导出
│   └── main.py         # FastAPI 路由、页面、WebSocket
├── frontend/
│   ├── index.html      # 首页
│   ├── entry.html      # 受试者入场
│   ├── trading.html    # 交易页
│   ├── researcher.html # 研究者控制台
│   ├── assets/         # style.css / common.js / 各页脚本
│   └── vendor/         # ECharts 本地副本
├── data/               # 运行后生成：SQLite + 会话目录
├── requirements.txt
└── run.ps1 / run.bat
```

## 主要接口

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/sessions/by-code/{code}` | 公开元数据（不含种子/轮次条件） |
| POST | `/api/sessions/join` | 会话码换受试者 token |
| GET | `/api/sessions/{id}/snapshot` | 受试者快照（只到当前 tick） |
| POST | `/api/sessions/{id}/start` | 开始 |
| POST | `/api/sessions/{id}/trades` | 买入/卖出 |
| POST | `/api/research/sessions` | 研究者建会话（需口令） |
| GET | `/api/research/sessions/{id}` | 实时/历史详情 |
| GET | `/api/research/sessions/{id}/export` | 导出 zip |
| WS | `/ws/subject/{id}` / `/ws/research/{id}` | 逐 tick 推送 |

完整交互文档：服务启动后访问 `/docs`。

## 自检结论

验收脚本：`verify_e2e.py`（启动服务后运行 `python verify_e2e.py --base http://127.0.0.1:8010`）。

- 无反馈轮受试者 WS：450 tick、1 个事件、0 条状态提醒；快照与 WS 无 `state/conf/seed/round/order` 字段。
- 有反馈轮：同一 seed 下价格逐点一致，出现状态提醒与干预记录。
- `decisions.csv` 表头 21 列，引擎三字段实填。
- 浏览器验收：首页、演示流程、入场页、研究者端均通过，控制台无 JavaScript 报错。

## 边界

- 默认 MockEngine 只用于流程演示与自检，**不代表真实脑电判别准确率**。
- 真机（BrainLink）未到，`signal_quality` 留空占位，标注“待真机联调”。
- 60 秒归因窗口、冷却时长等为经验初值，待验证方案负责人确认。
- 结果仅作趋势性观察，不宣称强因果。
