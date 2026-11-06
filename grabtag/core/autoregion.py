#!/usr/bin/env python
"""moss_autoregion — 文字 → 框 → 掩码，产出 GrabTag 可用的 mask_dir。

用法
----
  python moss_autoregion.py --ref ref_kawaku.png --spec work/kawaku_spec.json --out work/autoreg_kawaku
  python moss_autoregion.py --ref c11_char.png --prompts "gold hairpin. black corset. purple shorts. red boots."

产出（--out 目录）
----------------
  mask_01.png …           每件一张二值掩码（白=该件），尺寸 = 画布
  manifest.json           [{name, file, color, rect, ref_tag, paint_prompt, score, area_pct}]
  overlay.png             彩色叠加，便于人眼核对
  report.json             原始检测（label/score/box）

下游
----
  python moss_region_caption.py plan --ref <同图> --mask-dir <本目录> --for layout --out <分区目录>
  → cm.png / regions.json / tags.json，再用 build 出 layout.json

依赖：transformers>=5（含 Sam2Model / AutoModelForZeroShotObjectDetection），torch。
权重：IDEA-Research/grounding-dino-tiny、facebook/sam2.1-hiera-tiny（首次自动下载到 HF_HOME）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#7fb3ff", "#5ac878", "#aa5adc", "#c99a2e", "#d94f8a", "#8fa93b",
           "#e2603d", "#e8a33d", "#4a7fd4", "#e94f5a", "#3fb6b5", "#6d5bd0",
           "#b07fd4", "#3fa9a0", "#d4a03f", "#7f8fd4"]

GDINO_ID = "IDEA-Research/grounding-dino-tiny"
SAM2_ID = "facebook/sam2.1-hiera-tiny"


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def font(size=18):
    for p in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf", "arial.ttf"):
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            continue
    return ImageFont.load_default()


def load_prompts(a):
    """从 --prompts / --tags / --spec 里取提示词，返回 [(name, text, ref_tag, paint_prompt)]。"""
    out = []
    if a.prompts:
        for i, t in enumerate([x.strip() for x in a.prompts.replace("\n", " ").split(".") if x.strip()]):
            out.append(("r%d" % (i + 1), t, t, ""))
        return out
    path = a.spec or a.tags
    if not path:
        return out
    with open(path, encoding="utf-8") as fh:
        d = json.load(fh)
    items = d.get("regions") or d.get("tags") or d if isinstance(d, list) else d.get("items", [])
    for i, it in enumerate(items):
        if not isinstance(it, dict):
            continue
        tag = (it.get("ref_tag") or it.get("tag") or "").strip()
        name = it.get("name") or it.get("id") or ("r%d" % (i + 1))
        if not tag:
            continue
        out.append((name, tag, tag, (it.get("paint_prompt") or it.get("caption") or "").strip()))
    return out


def _where_by_name(name, mask):
    """先按名字猜部件，再退回质心分带（质心对长袍/长发太粗）。"""
    n = (name or "").lower()
    table = [
        (("hairpin", "pin", "headdress", "crown", "hat"), "at the top of her head"),
        (("hair",), "falling from her head down her back"),
        (("boot", "shoe", "heel", "sandal"), "on her feet"),
        (("sock", "stocking", "tight", "legging"), "on her legs"),
        (("sash", "belt", "waistband", "girdle"), "at her waist"),
        (("skirt", "trouser", "pant", "shorts"), "over her hips and thighs"),
        (("robe", "dress", "gown", "bodice", "top", "jacket", "coat", "cloak", "cape"), "over her torso"),
        (("glove", "sleeve", "bracer"), "on her arms"),
        (("necklace", "choker", "collar"), "around her neck"),
        (("lotus", "flower", "brooch", "emblem", "ornament", "pendant", "gem", "jewel"), "on her chest"),
    ]
    for keys, phrase in table:
        if any(k in n for k in keys):
            return phrase
    return _where(mask)


def _where(mask):
    """按掩码质心给出一个英文落点短语（GDINO 只给框，不给"长在身上哪儿"）。"""
    ys, xs = np.where(mask)
    if len(xs) == 0:
        return "on the character"
    H, W = mask.shape
    cx, cy = xs.mean() / W, ys.mean() / H
    if cy < 0.12:
        v = "at the top of her head"
    elif cy < 0.32:
        v = "on her upper body"
    elif cy < 0.52:
        v = "at her waist"
    elif cy < 0.72:
        v = "on her hips and skirt"
    elif cy < 0.88:
        v = "on her legs"
    else:
        v = "on her feet"
    if abs(cx - 0.5) > 0.18 and cy >= 0.12:
        v = ("on her left side, " if cx < 0.5 else "on her right side, ") + v
    return v


def _resolve_ref(ref):
    """按 原样 → $GRABTAG_INPUT_DIR → 本机 ComfyUI/input 的顺序找参考图。"""
    cands = [ref]
    env = os.environ.get("GRABTAG_INPUT_DIR") or ""
    if env:
        cands.append(os.path.join(env, ref))
    cands.append(os.path.join(r"C:\MOSS-Deploy\ComfyUI\input", os.path.basename(ref)))
    for c in cands:
        if os.path.exists(c):
            return c
    raise SystemExit("找不到参考图 %r（试过：%s）" % (ref, " | ".join(cands)))


def main():
    ap = argparse.ArgumentParser("moss_autoregion", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ref", required=True, help="参考图（ComfyUI/input 下的文件名或绝对路径）")
    ap.add_argument("--out", required=True, help="输出目录")
    ap.add_argument("--spec", help="spec / regions.json / tags.json（取 ref_tag 当提示词）")
    ap.add_argument("--tags", help="同 --spec，二选一")
    ap.add_argument("--prompts", help="直接给短语，用 . 分隔")
    ap.add_argument("--size", default="", help="画布 WxH（默认 = 参考图尺寸）")
    ap.add_argument("--box-thresh", type=float, default=0.30)
    ap.add_argument("--text-thresh", type=float, default=0.25)
    ap.add_argument("--min-area", type=float, default=0.0005, help="掩码占画布比例低于此值丢弃")
    ap.add_argument("--iou-merge", type=float, default=0.80, help="框 IoU 高于此值视为重复")
    ap.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    a = ap.parse_args()

    import torch
    from transformers import (AutoModelForZeroShotObjectDetection, AutoProcessor,
                              Sam2Model, Sam2Processor)

    ref_path = _resolve_ref(a.ref)
    im = Image.open(ref_path).convert("RGB")
    W0, H0 = im.size
    if a.size:
        W, H = [int(v) for v in a.size.lower().split("x")]
    else:
        W, H = W0, H0
    print("[auto] ref %s -> canvas %dx%d" % (os.path.basename(ref_path), W, H))

    prompts = load_prompts(a)
    if not prompts:
        print("[auto] 没有提示词：用 --spec/--tags 或在 spec 里填 ref_tag，或用 --prompts 直接给")
        return 2
    print("[auto] %d 条提示词" % len(prompts))

    dev = a.device
    if dev == "auto":
        dev = "cuda" if torch.cuda.is_available() else "cpu"
    dt = torch.float16 if dev == "cuda" else torch.float32

    # ---- GDINO ----
    gd_proc = AutoProcessor.from_pretrained(GDINO_ID)
    gd = AutoModelForZeroShotObjectDetection.from_pretrained(GDINO_ID, dtype=dt).to(dev).eval()

    def detect(text):
        inputs = gd_proc(images=im, text=text + ".", return_tensors="pt").to(dev)
        if "pixel_values" in inputs and inputs["pixel_values"].dtype != dt:
            inputs["pixel_values"] = inputs["pixel_values"].to(dt)
        with torch.inference_mode():
            out = gd(**inputs)
        r = gd_proc.post_process_grounded_object_detection(
            out, inputs.input_ids, threshold=a.box_thresh, text_threshold=a.text_thresh,
            target_sizes=[im.size[::-1]])[0]
        boxes = r["boxes"].float().cpu().numpy()
        scores = r["scores"].float().cpu().numpy()
        return boxes, scores

    # 单次前向：把所有短语拼成类表（实测单短语会退化成整幅大框）
    text_all = " ".join(pp[1].rstrip(".") + "." for pp in prompts)
    inputs = gd_proc(images=im, text=text_all, return_tensors="pt").to(dev)
    if "pixel_values" in inputs and inputs["pixel_values"].dtype != dt:
        inputs["pixel_values"] = inputs["pixel_values"].to(dt)
    with torch.inference_mode():
        out = gd(**inputs)
    r = gd_proc.post_process_grounded_object_detection(
        out, inputs.input_ids, threshold=a.box_thresh, text_threshold=a.text_thresh,
        target_sizes=[im.size[::-1]])[0]
    labels = r.get("text_labels") or r["labels"]
    scores = r["scores"].float().cpu().numpy()
    boxes = r["boxes"].float().cpu().numpy()
    print("[auto] GDINO 原始检测 %d 条" % len(boxes))

    def norm(s):
        return "".join(ch for ch in s.lower() if ch.isalnum() or ch.isspace()).strip()

    dets = []
    for name, text, tag, pp in prompts:
        want = norm(text)
        wtok = set(want.split())
        best, bi = -1.0, -1
        for i, lb in enumerate(labels):
            got = norm(lb)
            if not got:
                continue
            hit = (got in want) or (want in got)
            if not hit:
                gt = set(got.split())
                hit = len(wtok & gt) >= max(1, min(2, len(wtok)))
            if hit and scores[i] > best:
                best, bi = float(scores[i]), i
        if bi < 0:
            print("[auto] %-24s 未匹配到检测（类表里没有相近标签）" % name)
            continue
        dets.append({"name": name, "text": text, "ref_tag": tag, "paint_prompt": pp,
                     "box": boxes[bi].tolist(), "score": float(scores[bi])})
        print("[auto] %-24s %.3f  <- \"%s\"  [%5.0f %5.0f %5.0f %5.0f]"
              % (name, scores[bi], labels[bi], *boxes[bi]))

    del gd
    if dev == "cuda":
        torch.cuda.empty_cache()

    # ---- 去重：IoU 高的留分数大的 ----
    def iou(b1, b2):
        x0, y0 = max(b1[0], b2[0]), max(b1[1], b2[1])
        x1, y1 = min(b1[2], b2[2]), min(b1[3], b2[3])
        inter = max(0.0, x1 - x0) * max(0.0, y1 - y0)
        a1 = (b1[2] - b1[0]) * (b1[3] - b1[1])
        a2 = (b2[2] - b2[0]) * (b2[3] - b2[1])
        return inter / max(1e-6, a1 + a2 - inter)

    dets.sort(key=lambda d: -d["score"])
    kept = []
    for d in dets:
        if any(iou(d["box"], k["box"]) > a.iou_merge for k in kept):
            print("[auto] 去重：%s 与已选框重叠" % d["name"])
            continue
        kept.append(d)

    if not kept:
        print("[auto] 没有可用框")
        return 2

    # ---- SAM2 ----
    s2_proc = Sam2Processor.from_pretrained(SAM2_ID)
    s2 = Sam2Model.from_pretrained(SAM2_ID, dtype=dt).to(dev).eval()
    boxes = np.array([d["box"] for d in kept], dtype=np.float32)
    si = s2_proc(images=im, input_boxes=[boxes.tolist()], return_tensors="pt").to(dev)
    if "pixel_values" in si and si["pixel_values"].dtype != dt:
        si["pixel_values"] = si["pixel_values"].to(dt)
    with torch.inference_mode():
        so = s2(**si, multimask_output=False)
    try:
        ms = s2_proc.post_process_masks(so.pred_masks.cpu(), original_sizes=si["original_sizes"].cpu().tolist(),
                                        reshaped_input_sizes=si["reshaped_input_sizes"].cpu().tolist())
        m = np.asarray(ms[0])
    except Exception:                                # transformers 5.x 键名差异
        m = so.pred_masks.cpu().float()[0, :, 0].numpy()
    if m.ndim == 4:
        m = m[:, 0]
    if m.shape[-2:] != (H0, W0):
        import torch.nn.functional as F
        t = torch.from_numpy(m).float().unsqueeze(1)
        m = F.interpolate(t, size=(H0, W0), mode="bilinear", align_corners=False).squeeze(1).numpy()
    m = m > 0.0
    del s2
    if dev == "cuda":
        torch.cuda.empty_cache()

    # ---- 输出 ----
    os.makedirs(a.out, exist_ok=True)
    overlay = im.convert("RGBA")
    layer = Image.new("RGBA", im.size, (0, 0, 0, 0))
    dl = ImageDraw.Draw(layer)
    man, rep = [], []
    for i, (d, mk) in enumerate(zip(kept, m)):
        ys, xs = np.where(mk)
        if len(xs) == 0:
            continue
        area_pct = float(mk.mean() * 100)
        if area_pct < a.min_area * 100:
            print("[auto] %-24s 掩码过小 %.2f%%，丢弃" % (d["name"], area_pct))
            continue
        col = PALETTE[i % len(PALETTE)]
        fn = "mask_%02d.png" % (i + 1)
        # 掩码画到目标画布
        mi = Image.fromarray((mk * 255).astype(np.uint8)).resize((W, H), Image.BILINEAR)
        arr = np.asarray(mi) > 127
        Image.fromarray((arr * 255).astype(np.uint8)).save(os.path.join(a.out, fn))
        x0, y0, x1, y1 = [int(v) for v in (xs.min(), ys.min(), xs.max(), ys.max())]
        _pp = (d["paint_prompt"] or "").strip()
        if not _pp:
            _pp = "%s, %s" % (d["ref_tag"], _where_by_name(d["ref_tag"], mk))
        man.append({"name": d["name"], "file": fn, "color": col,
                    "rect": [round(x0 / W0, 4), round(y0 / H0, 4), round(x1 / W0, 4), round(y1 / H0, 4)],
                    "ref_tag": d["ref_tag"], "paint_prompt": _pp,
                    "score": round(d["score"], 3), "area_pct": round(area_pct, 2)})
        rep.append({"label": d["text"], "score": round(d["score"], 3),
                    "box": [round(float(v), 1) for v in d["box"]], "area_pct": round(area_pct, 2)})
        rgba = np.zeros((H0, W0, 4), np.uint8)
        rgba[mk] = (*hex2rgb(col), 110)
        layer.alpha_composite(Image.fromarray(rgba, "RGBA"))
        dl.rectangle([x0, y0, x1, y1], outline=(*hex2rgb(col), 255), width=3)
        dl.text((x0 + 4, max(0, y0 - 22)), "%d. %s" % (i + 1, d["name"]), font=font(), fill=(*hex2rgb(col), 255))

    if not man:
        print("[auto] 没产出任何掩码")
        return 2
    Image.alpha_composite(overlay, layer).convert("RGB").save(os.path.join(a.out, "overlay.png"))
    json.dump(man, open(os.path.join(a.out, "manifest.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    json.dump(rep, open(os.path.join(a.out, "report.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n[auto] %d 件 -> %s" % (len(man), os.path.join(a.out, "manifest.json")))
    print("[auto] 下一步： python moss_region_caption.py plan --ref %s --mask-dir %s --for layout --out <目录>"
          % (os.path.basename(ref_path), a.out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
