# -*- coding: utf-8 -*-
"""UI 资产生成器（对接本地 ComfyUI · Qwen-Image 2.1 工作流）。

工作流来源：从 `D:/AI/ComfyUI_Original_New/ComfyUI/output/sh_00115_.png`
的内嵌 `prompt` 元数据中提取（ComfyUI 会把生成时的 API 节点图写进 PNG）。
节点结构（10 节点）：

    1 UNETLoader            qwen-image-2.1NVFP4.safetensors   (weight_dtype=default)
    2 CLIPLoader            qwen3vl_8b_int8_convrot.safetensors (type=qwen_image)
    3 VAELoader             qwen_image_2.1_vae_bf16.safetensors
    4 CLIPTextEncode(正向)  ← 2
    5 CLIPTextEncode(负向)  ← 2
    6 EmptySD3LatentImage   width/height/batch
    7 ModelSamplingAuraFlow shift=3.1                        ← 1
    8 KSampler              euler / simple / steps=26 / cfg=2.5 / denoise=1.0
    9 VAEDecode             ← 8, 3
   10 SaveImage             filename_prefix

用法：
    python gen_ui_assets.py --list                     # 列出资产清单
    python gen_ui_assets.py --only empty-remind-light  # 只生成一张
    python gen_ui_assets.py                            # 全部生成
    python gen_ui_assets.py --server http://127.0.0.1:8188

约定：
- 生成/调用 ComfyUI 只用标准库（urllib），不需要 requests / websocket
- **`--post` 后处理需要 Pillow**（便携运行时未内置；用系统 Python + requirements-optional.txt）
- 生成结果写入 `组员文件/任务一/assets/`，并更新 `assets/manifest.json`
- 每次生成都记录 prompt / seed / 尺寸 / 模型 / sha256，便于复现与审计

后处理（--post）：
  模型给不出指定的精确背景色（实测深色版出的是 #001520，而主题 --surface 是 #141D2E），
  直接贴图会露出一块方框。这里按亮度把背景抠成 alpha 通道，并把线稿统一到某个色值，
  产物是透明 PNG —— 放在任何底色上都不会有边界。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.normpath(os.path.join(HERE, "..", "组员文件", "任务一", "assets"))
MANIFEST = os.path.join(ASSET_DIR, "manifest.json")

# ------------------------------------------------------------------ 工作流
MODEL = {
    "unet": "qwen-image-2.1NVFP4.safetensors",
    "clip": "qwen3vl_8b_int8_convrot.safetensors",
    "clip_type": "qwen_image",
    "vae": "qwen_image_2.1_vae_bf16.safetensors",
}

# UI 空态插图：统一风格。负向词沿用你原工作流里那串，再补 UI 插画相关的排除项。
NEG_BASE = ("photo, photorealistic, 3d render, text, watermark, signature, logo, frame, border, "
            "blurry, low quality, deformed, colorful paint, oil painting, western art, cartoon, "
            "anime, oversaturated, clutter, gradient background, drop shadow, sticker outline")
STYLE = ("极简矢量线稿插画，单一浅灰蓝色线条，粗细均匀，圆头线帽，大量留白，平面设计，"
         "UI 空状态插图风格，构图居中，主体占画面 55%，四周留空，无文字，无边框，无水印")

# name, 尺寸, 种子, 主题(main=浅/mid=中/soft=柔), 画面描述
ASSETS = [
    {
        "name": "empty-remind-light",
        "w": 768, "h": 768, "seed": 20261111,
        "bg": "纯白背景 #FFFFFF，背景完全干净无任何纹理",
        "scene": "一只悬挂的细线铃铛，铃身下方垂着一条短短的静止摆锤，铃口朝下、没有声响；"
                 "铃铛右侧留空，整体安静",
        "file": "empty-remind-light.png",
    },
    {
        "name": "empty-remind-dark",
        "w": 768, "h": 768, "seed": 20261111,   # 与浅色同种子 → 构图一致，便于主题切换
        "bg": "深藏青背景 #141D2E，背景完全干净无任何纹理",
        "scene": "一只悬挂的细线铃铛，铃身下方垂着一条短短的静止摆锤，铃口朝下、没有声响；"
                 "铃铛右侧留空，整体安静",
        "file": "empty-remind-dark.png",
    },
]


def build_graph(prompt: str, negative: str, w: int, h: int, seed: int,
                steps: int = 26, cfg: float = 2.5, shift: float = 3.1,
                prefix: str = "ui_asset") -> dict:
    """按你现有工作流的结构组装 API 格式节点图。"""
    return {
        "1": {"class_type": "UNETLoader",
              "inputs": {"unet_name": MODEL["unet"], "weight_dtype": "default"}},
        "2": {"class_type": "CLIPLoader",
              "inputs": {"clip_name": MODEL["clip"], "type": MODEL["clip_type"]}},
        "3": {"class_type": "VAELoader", "inputs": {"vae_name": MODEL["vae"]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 0]}},
        "5": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 0]}},
        "6": {"class_type": "EmptySD3LatentImage",
              "inputs": {"width": w, "height": h, "batch_size": 1}},
        "7": {"class_type": "ModelSamplingAuraFlow",
              "inputs": {"model": ["1", 0], "shift": shift}},
        "8": {"class_type": "KSampler",
              "inputs": {"model": ["7", 0], "positive": ["4", 0], "negative": ["5", 0],
                         "latent_image": ["6", 0], "seed": seed, "steps": steps,
                         "cfg": cfg, "sampler_name": "euler", "scheduler": "simple",
                         "denoise": 1.0}},
        "9": {"class_type": "VAEDecode", "inputs": {"samples": ["8", 0], "vae": ["3", 0]}},
        "10": {"class_type": "SaveImage", "inputs": {"images": ["9", 0], "filename_prefix": prefix}},
    }


# ------------------------------------------------------------------ HTTP
def post_json(server: str, path: str, obj: dict) -> dict:
    data = json.dumps(obj).encode("utf-8")
    req = urllib.request.Request(server.rstrip("/") + path, data=data,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get_json(server: str, path: str) -> dict:
    with urllib.request.urlopen(server.rstrip("/") + path, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def get_bytes(server: str, path: str) -> bytes:
    with urllib.request.urlopen(server.rstrip("/") + path, timeout=120) as r:
        return r.read()


def wait_history(server: str, prompt_id: str, timeout: float = 600.0) -> dict:
    """轮询 /history 直到该任务完成。ComfyUI 完成后才会出现对应 key。"""
    t0 = time.time()
    dots = 0
    while time.time() - t0 < timeout:
        h = get_json(server, "/history/" + urllib.parse.quote(prompt_id))
        if prompt_id in h:
            return h[prompt_id]
        # 顺带看队列，避免在"排队中"时误判超时
        q = get_json(server, "/queue")
        running = len(q.get("queue_running", []))
        pending = len(q.get("queue_pending", []))
        if dots % 6 == 0:
            print("    …等待中 %.0fs（运行 %d / 排队 %d）" % (time.time() - t0, running, pending))
        dots += 1
        time.sleep(2.0)
    raise TimeoutError("生成超时（%.0fs）：%s" % (timeout, prompt_id))


# ------------------------------------------------------------------ 主流程
def gen_one(server: str, spec: dict, steps: int, cfg: float, dry: bool = False) -> dict | None:
    prompt = "%s。%s。%s" % (spec["scene"], STYLE, spec["bg"])
    neg = NEG_BASE
    name, w, h, seed = spec["name"], spec["w"], spec["h"], spec["seed"]
    print("· %-22s %dx%d seed=%d" % (name, w, h, seed))

    graph = build_graph(prompt, neg, w, h, seed, steps=steps, cfg=cfg,
                        prefix="ui_" + name.replace("-", "_"))
    if dry:
        print("    [dry-run] 节点数 %d，正向 prompt：%s" % (len(graph), prompt[:60] + "…"))
        return None

    t0 = time.time()
    try:
        resp = post_json(server, "/prompt", {"prompt": graph, "client_id": uuid.uuid4().hex})
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")[:800]
        raise SystemExit("提交失败 HTTP %s：%s" % (e.code, body))
    if resp.get("node_errors"):
        raise SystemExit("节点校验失败：%s" % json.dumps(resp["node_errors"], ensure_ascii=False)[:800])
    pid = resp["prompt_id"]
    print("    prompt_id=%s 已入队" % pid[:8])

    hist = wait_history(server, pid)
    imgs = []
    for node in hist.get("outputs", {}).values():
        imgs.extend(node.get("images", []))
    if not imgs:
        raise SystemExit("任务完成但没有输出图片：%s" % json.dumps(hist, ensure_ascii=False)[:500])

    img = imgs[0]
    q = urllib.parse.urlencode({"filename": img["filename"],
                                "subfolder": img.get("subfolder", ""),
                                "type": img.get("type", "output")})
    blob = get_bytes(server, "/view?" + q)
    os.makedirs(ASSET_DIR, exist_ok=True)
    out = os.path.join(ASSET_DIR, spec["file"])
    with open(out, "wb") as fh:
        fh.write(blob)
    dt = time.time() - t0
    sha = hashlib.sha256(blob).hexdigest()[:16]
    print("    ✓ %s  %.1f KB  %.1fs  sha256:%s" % (spec["file"], len(blob) / 1024, dt, sha))

    return {
        "name": name, "file": spec["file"], "width": w, "height": h, "seed": seed,
        "steps": steps, "cfg": cfg, "model": MODEL["unet"], "prompt": prompt,
        "negative": neg, "sha256_16": sha, "bytes": len(blob),
        "elapsed_s": round(dt, 1), "server": server,
        "workflow": "qwen-image-2.1 (UNETLoader+CLIPLoader+ModelSamplingAuraFlow 3.1)",
        "created": time.strftime("%Y-%m-%d %H:%M:%S"),
    }


def save_manifest(entry: dict) -> None:
    os.makedirs(ASSET_DIR, exist_ok=True)
    data = {"generator": "gen_ui_assets.py", "assets": []}
    if os.path.isfile(MANIFEST):
        try:
            data = json.load(open(MANIFEST, encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # 清单坏了不该阻断出图：重建一份新的，但把原因打出来（否则下次无从追查）
            print("  ⚠ manifest.json 解析失败（%s），按空清单重建：%s" % (type(exc).__name__, exc))
    data.setdefault("assets", [])
    data["assets"] = [a for a in data["assets"] if a.get("name") != entry["name"]]
    data["assets"].append(entry)
    data["assets"].sort(key=lambda a: a["name"])
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)


def keyout(src: str, dst: str, line_rgb: tuple[int, int, int],
           keep_raw: bool = True) -> dict:
    """把"纯色底 + 单色线稿"的图抠成透明 PNG。

    基准取法（踩过的坑）：最初用 min/max 当线色基准，实测浅色图的线芯亮度是 p1=191、
    最暗点是 178（噪点），导致线芯只映射到 83% 不透明、整张图发灰。
    改用分位数：背景取 p50，线色取"离背景更远的那一侧尾部"（p1 或 p99），
    这样线芯稳定落在 alpha=255。

    RGB 统一设为 line_rgb —— 原图抗锯齿会带出偏色，统一后线宽观感一致。
    """
    try:
        from PIL import Image
    except ImportError as exc:
        raise SystemExit("--post 需要 Pillow（便携运行时未内置）。\n"
                         "  系统 Python：py -m pip install --isolated Pillow\n"
                         "  或只生成不后处理：去掉 --post，直接拿 ComfyUI 原图。") from exc
    im = Image.open(src).convert("RGB")
    w, h = im.size
    gray = im.convert("L")
    hist = gray.histogram()
    total = sum(hist)
    acc, pct = 0, {}
    for v, c in enumerate(hist):
        acc += c
        for q in (1, 50, 99):
            if q not in pct and acc >= total * q / 100:
                pct[q] = v

    bg_l = pct[50]
    d_lo = bg_l - pct[1]          # 暗侧距离
    d_hi = pct[99] - bg_l         # 亮侧距离
    darker = d_lo >= d_hi
    ref = pct[1] if darker else pct[99]
    span = max(1, abs(bg_l - ref))

    px = im.load()
    lum_map = gray.load()
    out = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    op = out.load()
    n = 0
    for y in range(h):
        for x in range(w):
            l = lum_map[x, y]
            a = (bg_l - l) / span if darker else (l - bg_l) / span
            if a <= 0.05:
                continue                      # 背景（含噪点）
            if a >= 1.0:
                a = 1.0
            op[x, y] = (line_rgb[0], line_rgb[1], line_rgb[2], int(round(a * 255)))
            n += 1
    out.save(dst, optimize=True)
    return {"bg": "#%02x%02x%02x" % im.getpixel((2, 2)), "line_is_dark": darker,
            "line": "#%02x%02x%02x" % line_rgb, "ref_lum": ref, "bg_lum": bg_l,
            "opaque_px": n, "coverage": round(n / (w * h) * 100, 1),
            "file": os.path.basename(dst)}


def post_process() -> None:
    """对已生成的 raw 图做抠底，产出透明 PNG。"""
    os.makedirs(ASSET_DIR, exist_ok=True)
    plan = [
        ("empty-remind-light.png", (132, 150, 172)),  # 浅色主题线色（对白底约 3.4:1，装饰级但看得清）
        ("empty-remind-dark.png", (150, 170, 194)),   # 深色主题线色（深底上要更亮一点）
    ]
    data = {"generator": "gen_ui_assets.py --post", "assets": []}
    if os.path.isfile(MANIFEST):
        try:
            data = json.load(open(MANIFEST, encoding="utf-8"))
        except json.JSONDecodeError as exc:
            # 清单坏了不该阻断出图：重建一份新的，但把原因打出来（否则下次无从追查）
            print("  ⚠ manifest.json 解析失败（%s），按空清单重建：%s" % (type(exc).__name__, exc))
    data.setdefault("assets", [])
    for fn, rgb in plan:
        src = os.path.join(ASSET_DIR, fn)
        if not os.path.isfile(src):
            print("  [skip] 缺文件：%s" % fn)
            continue
        raw = os.path.join(ASSET_DIR, "raw_" + fn)
        if not os.path.isfile(raw):
            os.replace(src, raw)          # 原始生成图留档，便于换线色重抠
        dst = src
        info = keyout(raw, dst, rgb)
        # 透明图自身的大小与哈希（raw 的大小记在 manifest.bytes，两者别混）
        with open(dst, "rb") as fh:
            blob = fh.read()
        info["file_bytes"] = len(blob)
        info["sha256_16"] = hashlib.sha256(blob).hexdigest()[:16]
        info["raw_bytes"] = os.path.getsize(raw)
        print("  ✓ %-24s 背景 %s → 透明，覆盖率 %.1f%%，%d KB（线色 %s）"
              % (info["file"], info["bg"], info["coverage"],
                 round(len(blob) / 1024), info["line"]))
        for a in data["assets"]:
            if a.get("file") == fn:
                a["alpha"] = info
                break
    data["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(MANIFEST, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print("  清单已更新：%s" % os.path.relpath(MANIFEST, HERE))


def main() -> int:
    ap = argparse.ArgumentParser(description="生成 UI 资产（本地 ComfyUI · Qwen-Image 2.1）")
    ap.add_argument("--server", default="http://127.0.0.1:8188")
    ap.add_argument("--only", default="", help="只生成指定资产名")
    ap.add_argument("--steps", type=int, default=26)
    ap.add_argument("--cfg", type=float, default=2.5)
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--post", action="store_true", help="只做抠底后处理（不重新生成）")
    args = ap.parse_args()

    if args.post:
        post_process()
        return 0

    if args.list:
        print("可用资产：")
        for a in ASSETS:
            print("  %-22s %dx%d  seed=%d  → %s" % (a["name"], a["w"], a["h"], a["seed"], a["file"]))
        return 0

    if not args.dry_run:
        try:
            st = get_json(args.server, "/system_stats")
            print("ComfyUI %s | GPU: %s\n" % (
                st.get("system", {}).get("comfyui_version", "?"),
                (st.get("devices") or [{}])[0].get("name", "?")))
        except Exception as exc:
            print("✗ 连不上 ComfyUI（%s）：%s" % (args.server, exc))
            print("  请先启动：python main.py --listen 127.0.0.1 --port 8188")
            return 2

    todo = [a for a in ASSETS if not args.only or a["name"] == args.only]
    if not todo:
        print("没有匹配的资产名：%s" % args.only)
        return 1

    ok = 0
    for spec in todo:
        entry = gen_one(args.server, spec, args.steps, args.cfg, dry=args.dry_run)
        if entry:
            save_manifest(entry)
            ok += 1
    print("\n完成 %d/%d，清单：%s" % (ok, len(todo), os.path.relpath(MANIFEST, HERE)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
