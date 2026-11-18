# 项目文件/tools — 浏览器端验证工具（v3.16）

这些脚本原来散落在开发机的 WorkBuddy 目录里（换机器就丢），v3.16 收进项目。
它们**不进便携包也能用**，但需要 Node ≥ 22（内置 `WebSocket`）和一个开了
`--remote-debugging-port=9222` 的 Edge/Chrome。

## 前置

```bash
# 1) 静态服务（mock 模式，不需要桥）
cd 组员文件/任务一
python -m http.server 8771 --bind 127.0.0.1

# 2) 浏览器（任选其一，必须开 9222）
msedge.exe --remote-debugging-port=9222 --headless=new about:blank
```

真机/回放 e2e 还需要数据桥：

```bash
python 项目文件/live_bridge.py --source mock --port 8791
node 项目文件/tools/ui_e2e.mjs http://127.0.0.1:8791/
```

## 脚本

| 脚本 | 作用 | 依赖 |
|---|---|---|
| `ui_e2e.mjs <url>` | 真实点击 → 数据流动 / 四数据源 / 抽屉 / 无 JS 异常 / 无横向溢出 | 数据桥 + CDP |
| `ui_responsive.mjs [outdir] [url]` | 15 档断点渲染 + 几何量测 + 截图 | 8771 静态服务 + CDP |
| `ui_drawer.mjs [outdir] [url]` | 窄屏抽屉/弹窗适配审计 | 8771 + CDP |
| `ui_shot.mjs <url> [out] [w] [h]` | 单页截图（支持 `SHOT_ACT`/`SHOT_VIEWPORT` 环境变量） | CDP |
| `a11y_audit.mjs <url> [theme]` | 关键选择器 WCAG 对比度 | CDP |
| `verify_calib_e2e.mjs` | 校准失败不落盘 / 成功落盘 | 8771 + CDP |
| `engine.test.cjs` | 引擎纯函数单测（`node --test`） | 无 |

## 约定

- 输出默认写到系统临时目录（`os.tmpdir()`），不再写死 `/tmp`（Windows 上不存在）。
- 截图验收 `fixed` 层（抽屉/角标）时用 `SHOT_VIEWPORT=1`；整页截图对 fixed 不可靠。
- UI 脚本不进 `verify_all.py` 的默认快检（需要浏览器）；CI/本地全量时单独跑。
