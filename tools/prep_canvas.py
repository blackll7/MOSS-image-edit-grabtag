#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""prep_canvas.py —— MOSS-SOP v2 的 S1 归一（2026-11-08 定稿做法）

规则（与 SOP §S1「★ 现行推荐」一致）：
  1. 参考图裁掉 alpha 空白（getbbox）；RGBA 合成白底，绝不让 alpha 读成黑；
  2. 画布高 = 参考裁剪高在 --max-mp 内的最大 16 倍数；宽 = 高 × 深度宽高比（取 16 倍数）；
  3. 参考图 contain 进画布居中补白（等比、NEAREST，不变形）；
  4. 深度图**原样**输出（不缩放、不补黑、不 tight）—— 若源图是实景背景，另用 --depth-black-bg 压黑。

用法：
  python prep_canvas.py --ref ref.png --depth dep.png --outdir <run目录> [--max-mp 2.0]
                        [--depth-black-bg]
产物：
  <outdir>/ref_norm.png     参考（画布尺寸，居中补白）
  <outdir>/depth_raw.png    深度（原尺寸，原样；需要时已压黑）
  <outdir>/prep.json        {canvas:[W,H], ratio, ref_size, depth_size, mp, nq, n_ref, ...}
"""
# 路径来自环境变量，便于换机器：
#   MOSS_HOME  ComfyUI 部署根（默认 C:\MOSS-Deploy）
#   MOSS_RUNS  run 目录根（默认 ./runs）
import os as _os
MOSS_HOME = _os.environ.get("MOSS_HOME", r"C:\MOSS-Deploy")
MOSS_RUNS = _os.environ.get("MOSS_RUNS", _os.path.join(_os.getcwd(), "runs"))
import argparse, json, os, sys
from PIL import Image


def r16(v):
    return max(16, int(v) // 16 * 16)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ref", required=True)
    ap.add_argument("--depth", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--max-mp", type=float, default=2.0)
    ap.add_argument("--depth-black-bg", action="store_true",
                    help="深度图背景压黑（只在源图是实景背景时用；白模/近黑背景不要开）")
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    # ---- 1. 参考图：裁 alpha 空白 + 合白底 ----
    ref = Image.open(a.ref)
    ref_raw_size = ref.size
    bbox = None
    if ref.mode == "RGBA":
        bbox = ref.split()[-1].getbbox()
        if bbox:
            ref = ref.crop(bbox)
        bg = Image.new("RGB", ref.size, (255, 255, 255))
        bg.paste(ref, mask=ref.split()[-1])
        ref = bg
    else:
        ref = ref.convert("RGB")

    # ---- 2. 深度图：读原尺寸 ----
    dep = Image.open(a.depth)
    dep_raw_size = dep.size
    ratio = dep.width / dep.height

    # ---- 3. 画布：高 = 参考高在 max_mp 内的最大 16 倍数；宽 = 高 × 深度比例 ----
    max_px = a.max_mp * 1e6
    H = int((max_px / ratio) ** 0.5)          # 令 W*H = ratio*H^2 <= max_px
    H = r16(min(H, ref.height))
    while r16(H * ratio) * H > max_px:          # 保证面积不超
        H -= 16
    H = max(16, H)
    W = r16(H * ratio)

    # ---- 4. 参考 contain 进画布居中补白 ----
    k = min(W / ref.width, H / ref.height)
    rn = ref.resize((max(16, int(ref.width * k)), max(16, int(ref.height * k))), Image.NEAREST)
    canvas = Image.new("RGB", (W, H), (255, 255, 255))
    canvas.paste(rn, ((W - rn.width) // 2, (H - rn.height) // 2))
    refp = os.path.join(a.outdir, "ref_norm.png")
    canvas.save(refp)

    # ---- 5. 深度原样（可选压黑） ----
    dep_out = dep
    bg_val = None
    if a.depth_black_bg:
        import numpy as np
        arr = np.asarray(dep.convert("L")).astype("float32")
        hh, ww = arr.shape
        bh, bw = max(1, int(hh * 0.05)), max(1, int(ww * 0.05))
        edge = np.concatenate([arr[:bh].ravel(), arr[-bh:].ravel(), arr[:, :bw].ravel(), arr[:, -bw:].ravel()])
        bg_val = float(np.median(edge))
        if bg_val > 4:
            arr = np.clip((arr - bg_val) / max(1.0, 255.0 - bg_val) * 255.0, 0, 255)
            arr[arr < 8] = 0
            dep_out = Image.fromarray(arr.astype("uint8"), mode="L")
    depp = os.path.join(a.outdir, "depth_raw.png")
    dep_out.save(depp)

    # ---- 6. prep.json ----
    info = {
        "canvas": [W, H],
        "ratio_canvas": round(W / H, 4),
        "ratio_depth": round(ratio, 4),
        "ref_src": list(ref_raw_size),
        "ref_alpha_bbox": list(bbox) if bbox else None,
        "ref_on_canvas": [rn.width, rn.height, (W - rn.width) // 2, (H - rn.height) // 2],
        "depth_src": list(dep_raw_size),
        "depth_output": list(dep_out.size),
        "depth_passthrough": dep_out.size == dep_raw_size,
        "depth_black_bg": a.depth_black_bg,
        "depth_bg_median": bg_val,
        "mp": round(W * H / 1e6, 3),
        "nq": (W // 16) * (H // 16),
        "n_ref": (W // 16) * (H // 16),
        "ref_kv_gib": round(28 * 2 * (W // 16) * (H // 16) * 4096 * 2 / 1024 ** 3, 2),
        "ref_kv_gib_pool2": round(28 * 2 * ((W // 16) * (H // 16) // 4) * 4096 * 2 / 1024 ** 3, 2),
    }
    json.dump(info, open(os.path.join(a.outdir, "prep.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=2)

    print("参考图   ： %dx%d → 裁 alpha %s → 画布内 %dx%d（居中留白 %d,%d）"
          % (ref_raw_size[0], ref_raw_size[1], bbox, rn.width, rn.height,
             (W - rn.width) // 2, (H - rn.height) // 2))
    print("深度图   ： %dx%d 原样输出%s（比例 %.4f）"
          % (dep_raw_size[0], dep_raw_size[1], "，已压黑 bg=%.0f" % bg_val if (bg_val and bg_val > 4) else "", ratio))
    print("画布     ： --w %d --h %d（比例 %.4f，%.2f MP）" % (W, H, W / H, W * H / 1e6))
    print("token    ： nq=%d，ref_kv %.2f GiB → pool2 %.2f GiB" % (info["nq"], info["ref_kv_gib"], info["ref_kv_gib_pool2"]))
    print("产物     ： %s" % refp)
    print("           %s" % depp)


if __name__ == "__main__":
    main()
