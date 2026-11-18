# 答辩 PPT · 文件说明

> 12 页 / 11 分 05 秒 / 16:9 / 字体锁 Microsoft YaHei
> **上台只用两个文件：`_终版.pptx` 和 `_终版.pdf`。**

---

## 一、三类文件

| 类别 | 文件 | 说明 |
|---|---|---|
| **成品** | `答辩_脑机接口提醒层_终版.pptx` | 主件，含每页讲者备注 |
| | `答辩_脑机接口提醒层_终版.pdf` | 兜底，逐页 1:1，不依赖字体；PPT 打不开时放它 |
| **源** | `slides_v2/01.slide … 12.slide` | 12 页模板（中文可读，图片用占位符） |
| | `assets_src/*.jpg` | 4 张图（封面线稿、主面板、盯盘平台、校准） |
| | `build_slides.py` | 把模板 + 图合成可导入的 DSL |
| | `DESIGN.md` | 版式规范、配色、逐页字段表、讲述分工 |
| **历史** | `_旧版/` | 10 页旧版、含测试页的中间产物、旧幻灯片源 |
| | `assets/` | 早期截图原图（PNG），仅供重制 |

> `_旧版/` 整目录不进便携包，也不要在答辩现场打开。

---

## 二、改一页的正确姿势

```bash
cd 项目汇报/答辩_脑机接口提醒层

# 1. 改模板
#    slides_v2/NN.slide —— 只改文字/坐标，别动 notes 的结构

# 2. 图有变动时才需要重新合成（否则可跳过）
python build_slides.py

# 3. 写回 PPT（页码从 0 开始，第 N 页 → --page-index N-1）
slidep upsert-dsl 答辩_脑机接口提醒层_终版.pptx --dsl-file .build/NN.slide --page-index <N-1>

# 4. 查版式（必须 ok:true）
slidep lint 答辩_脑机接口提醒层_终版.pptx --dsl-file .build/NN.slide

# 5. 看一眼
slidep screenshot 答辩_脑机接口提醒层_终版.pptx --page-index <N-1> --out .preview/check.png
```

改完记得**重出 PDF**（否则兜底件是旧的）：

```bash
python - <<'PY'
from PIL import Image
fs=[f'.preview/fin{i:02d}.png' for i in range(1,13)]   # 12 页需先全部重渲染
im=Image.open(fs[0]).convert('RGB')
im.save('答辩_脑机接口提醒层_终版.pdf', save_all=True,
        append_images=[Image.open(f).convert('RGB') for f in fs[1:]], resolution=144.0)
PY
```

---

## 三、四条硬约束（改之前先读）

1. **不要用 `Image` 组件**：转换器会写 1145 字节占位图。图一律走
   `backgroundImage: 'url(data:image/jpeg;base64,...)'`，由 `build_slides.py` 注入。
2. **不要让元素超出父容器**：`slidep lint` 报 `child-containment` 就是超了，必须修到 `ok:true`。
3. **改文案先改模板**，别直接编辑 pptx —— 下次重建会被覆盖。
4. **12 页是定稿页数**。加页要同步改：讲稿时长、当天流程的讲序表、PDF。

---

## 四、当前口径

| 项 | 值 |
|---|---|
| 页数 | 12 |
| 讲述时长 | 11:05（11:05 必须进第 12 页） |
| 画布 | 1280×720（导出 16:9） |
| 主色 | `#1E4FA8`；强调红 `#D9534F`（全篇 ≤3 处） |
| 字体 | Microsoft YaHei（全篇 1270 个文本段显式指定） |
| 体积 | pptx 462 KB / pdf 1.8 MB |
