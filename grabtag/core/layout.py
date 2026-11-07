#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""moss_layout.py —— 从分区色图导出 MOSS Layout JSON + 方位冲突 lint。

借鉴 ukr8b3g-cmyk/MossImage-BBOX-Prompter 的 prompt_builder.py：
  * layout schema: {"summary":{"text":...},"items":[{label,paint_prompt,bbox}],
                    "bbox_coord_space":"abs_centralized","bbox_format":"yxyx"}
  * pixel_bbox() 用 floor/ceil，保证框不退化成零面积
  * position_hint() / position_conflict()：方位词自动生成 + 与手写方位词比对

用法：
  python moss_layout.py --colormap cm_multi.png --summary "a woman on a bed" ^
      --item "#aa5adc|object|raised trouser leg of sea water, wvsrg, foam" ^
      --item "#5ac878|object|lower trouser leg of mint silk" ^
      --item "#e68c3c|object|orange leather sash with a buckle" ^
      --out cm_multi.layout.json --pretty

  # 只做 lint（不导出）：
  python moss_layout.py --colormap cm_multi.png --prompt-file base.txt
"""
import argparse
import json
import math
import os
import re
import sys

import numpy as np
from PIL import Image

# MossImage-BBOX-Prompter 的七个标准槽位色，按亮度/色相排序无关，只作对照
SLOTS = {
    "red": (255, 64, 64),
    "blue": (64, 128, 255),
    "yellow": (255, 216, 64),
    "green": (90, 200, 120),
    "magenta": (220, 90, 200),
    "cyan": (80, 220, 220),
    "orange": (230, 140, 60),
}
LABELS = ("character", "object", "text")

POS_WORDS = re.compile(r"\b(left|right|top|bottom|upper|lower|center|middle)\b", re.I)
POS_CN = ("左上", "右上", "左下", "右下", "上部", "下部", "上侧", "下侧", "中央", "中心", "左侧", "右侧")


def hex2rgb(txt):
    txt = txt.strip().lstrip("#")
    if len(txt) == 6:
        return tuple(int(txt[i:i + 2], 16) for i in (0, 2, 4))
    raise ValueError(f"bad colour: {txt!r}")


def rgb2hex(rgb):
    return "#%02x%02x%02x" % tuple(int(v) for v in rgb)


def nearest_slot(rgb):
    best, dist = None, 1e9
    for name, ref in SLOTS.items():
        d = sum((a - b) ** 2 for a, b in zip(rgb, ref))
        if d < dist:
            best, dist = name, d
    return best, math.sqrt(dist)


def pixel_bbox(x0, y0, x1, y1, W, H):
    """canvas xyxy -> centralized yxyx（floor/ceil 防零面积）"""
    return [math.floor(y0) - H // 2, math.floor(x0) - W // 2,
            math.ceil(y1) - H // 2, math.ceil(x1) - W // 2]


def position_hint(x0, y0, x1, y1, W, H):
    cx, cy = (x0 + x1) / (2 * W), (y0 + y1) / (2 * H)
    horizontal = "left" if cx < .34 else "right" if cx > .66 else "center"
    vertical = "upper" if cy < .34 else "lower" if cy > .66 else "middle"
    return f"{vertical}-{horizontal} area"


def position_terms(text):
    words = {w.lower() for w in POS_WORDS.findall(text or "")}
    left = "left" in words or "左侧" in text or "左" in text
    right = "right" in words or "右侧" in text or "右" in text
    top = bool(words & {"top", "upper"}) or any(w in (text or "") for w in ("上部", "上侧", "左上", "右上"))
    bottom = bool(words & {"bottom", "lower"}) or any(w in (text or "") for w in ("下部", "下侧", "左下", "右下"))
    center = "center" in words or "middle" in words or any(w in (text or "") for w in ("中央", "中心"))
    return left, right, top, bottom, center


def position_conflict(terms, x0, y0, x1, y1, W, H):
    left, right, top, bottom, center = terms
    cx, cy = (x0 + x1) / (2 * W), (y0 + y1) / (2 * H)
    return ((left and cx > .66) or (right and cx < .34) or (top and cy > .66)
            or (bottom and cy < .34) or (center and (cx < .34 or cx > .66)))


def colour_regions(arr):
    """按纯色聚类：返回 {rgb: mask}（忽略近黑背景与近白）"""
    H, W = arr.shape[:2]
    flat = arr.reshape(-1, 3)
    uniq, counts = np.unique(flat, axis=0, return_counts=True)
    out = {}
    for rgb, cnt in zip(uniq, counts):
        if cnt < 0.0005 * H * W:         # 太小（0.05%，够留下 1 px 级配饰）
            continue
        if max(rgb) < 16 or min(rgb) > 240:   # 近黑/近白当背景
            continue
        mask = np.all(arr == rgb, axis=-1)
        out[tuple(int(v) for v in rgb)] = (mask, float(cnt) / (H * W))
    return out


def parse_item(txt):
    parts = [p.strip() for p in txt.split("|")]
    if len(parts) < 3:
        raise ValueError(f"--item 需要 颜色|标签|描述：{txt!r}")
    colour, label, caption = parts[0], parts[1].lower(), parts[2]   # 第三段 = paint_prompt
    if label not in LABELS:
        raise ValueError(f"标签只能是 {LABELS}：{label!r}")
    return hex2rgb(colour), label, caption


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--colormap", required=True)
    ap.add_argument("--out", default="", help="输出 layout json 路径")
    ap.add_argument("--summary", default="", help="整图 summary（全局一句话）")
    ap.add_argument("--item", action="append", default=[],
                    help="颜色|标签|描述，可重复（标签 character/object/text）")
    ap.add_argument("--prompt-file", default="", help="只做 lint：读主提示词")
    ap.add_argument("--prompt", default="", help="只做 lint：主提示词文本")
    ap.add_argument("--skip-base", action="store_true", default=True,
                    help="把占画面 >50%% 的底色区当背景跳过（默认开）")
    ap.add_argument("--keep-base", dest="skip_base", action="store_false")
    ap.add_argument("--pretty", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--sort", choices=["area", "y", "small"], default="area",
                    help="items 输出顺序：area=面积降序（默认），y=从上到下")
    args = ap.parse_args(argv)

    arr = np.asarray(Image.open(args.colormap).convert("RGB"))
    H, W = arr.shape[:2]
    regions = colour_regions(arr)
    if not regions:
        sys.exit("[layout] 色图里没找到有效纯色分区")

    items, warnings = [], []
    given = {}
    for spec in args.item:
        rgb, label, caption = parse_item(spec)
        given[rgb] = (label, caption)

    if args.sort == "y":
        order = sorted(regions.items(), key=lambda kv: int(np.where(kv[1][0])[0].min()))
    elif args.sort == "small":
        order = sorted(regions.items(), key=lambda kv: kv[1][1])
    else:
        order = sorted(regions.items(), key=lambda kv: -kv[1][1])
    for rgb, (mask, frac) in order:
        ys, xs = np.where(mask)
        x0, x1 = int(xs.min()), int(xs.max()) + 1
        y0, y1 = int(ys.min()), int(ys.max()) + 1
        bbox = pixel_bbox(x0, y0, x1, y1, W, H)
        hint = position_hint(x0, y0, x1, y1, W, H)
        slot, dist = nearest_slot(rgb)
        if args.skip_base and frac > 0.5 and rgb not in given:
            if not args.quiet:
                print(f"[layout] {rgb2hex(rgb)}  area={frac*100:5.1f}%  -> base colour, skipped")
            continue
        label, caption = given.get(rgb, ("object", ""))

        if not caption:
            warnings.append(f"{rgb2hex(rgb)}: 没有给 paint_prompt（--item 第三段）")
        if bbox[0] == bbox[2] or bbox[1] == bbox[3]:
            warnings.append(f"{rgb2hex(rgb)}: 框在导出精度下退化成零面积")
        if x0 <= 0 or y0 <= 0 or x1 >= W or y1 >= H:
            warnings.append(f"{rgb2hex(rgb)}: 框贴边或越界（{W}x{H}）")

        items.append({"label": label, "paint_prompt": caption, "bbox": bbox,
                      "_debug": {"colour": rgb2hex(rgb), "area": round(frac, 4),
                                 "hint": hint, "slot": slot, "slot_dist": round(dist, 1)}})
        if not args.quiet:
            print(f"[layout] {rgb2hex(rgb)}  area={frac*100:5.1f}%  slot≈{slot}(Δ{dist:.0f})  "
                  f"{label:9s}  {hint:20s} bbox={bbox}")

    layout = {"summary": {"text": args.summary},
              "items": [{k: v for k, v in it.items() if k != "_debug"} for it in items],
              "bbox_coord_space": "abs_centralized", "bbox_format": "yxyx"}
    text = json.dumps(layout, ensure_ascii=args.pretty is False, indent=2 if args.pretty else None,
                      separators=None if args.pretty else (",", ":"))

    # lint：主提示词里的方位词 vs 每个框的实际位置
    prompt = args.prompt
    if args.prompt_file:
        with open(args.prompt_file, "r", encoding="utf-8") as fh:
            prompt = fh.read()
    if prompt:
        scene_terms = position_terms(prompt)
        for it in items:
            x0 = it["bbox"][1] + W // 2
            x1 = it["bbox"][3] + W // 2
            y0 = it["bbox"][0] + H // 2
            y1 = it["bbox"][2] + H // 2
            # 只比场景级方位词，避免逐框重复报警
            if position_conflict(scene_terms, x0, y0, x1, y1, W, H):
                warnings.append(f"{it['_debug']['colour']}: 提示词里的方位词与该框位置冲突"
                                f"（框在 {it['_debug']['hint']}）")

    for w in warnings:
        print(f"[warn] {w}")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print(f"[layout] 写出 {args.out}（{len(items)} 项，{len(warnings)} 条 warning）")
    else:
        print(text)


if __name__ == "__main__":
    main()
