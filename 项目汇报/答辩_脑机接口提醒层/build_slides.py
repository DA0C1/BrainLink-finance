"""把 slides_v2 里的模板 + assets_src 里的图，合成可导入的 DSL。

模板中用 __IMG_XXX__ 占位，构建时替换成 data URI。
用法：python build_slides.py
输出：.build/NN.slide
"""
import base64
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
TPL_DIR = os.path.join(HERE, "slides_v2")
IMG_DIR = os.path.join(HERE, "assets_src")
OUT_DIR = os.path.join(HERE, ".build")

IMAGES = {
    "__IMG_COVER__": ("cover_line.jpg", "image/jpeg"),
    "__IMG_CALIB__": ("ui_calib.jpg", "image/jpeg"),
    "__IMG_MAIN__": ("ui_main.jpg", "image/jpeg"),
    "__IMG_WATCHLAB__": ("watchlab.jpg", "image/jpeg"),
}


def data_uri(name, mime):
    path = os.path.join(IMG_DIR, name)
    with open(path, "rb") as f:
        return "data:%s;base64,%s" % (mime, base64.b64encode(f.read()).decode("ascii"))


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    uris = {k: data_uri(*v) for k, v in IMAGES.items()}
    for name in sorted(os.listdir(TPL_DIR)):
        if not name.endswith(".slide"):
            continue
        with open(os.path.join(TPL_DIR, name), encoding="utf-8") as f:
            text = f.read()
        used = []
        for key, uri in uris.items():
            if key in text:
                text = text.replace(key, uri)
                used.append(key.strip("_"))
        with open(os.path.join(OUT_DIR, name), "w", encoding="utf-8") as f:
            f.write(text)
        print("%s  %6.1f KB  %s" % (name, len(text) / 1024, ",".join(used) or "-"))


if __name__ == "__main__":
    main()
