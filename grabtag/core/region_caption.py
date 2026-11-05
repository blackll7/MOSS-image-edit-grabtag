#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""moss_region_caption.py —— 从参考图切区、出拼图、生成 layout JSON。

两段式：机械部分交给脚本，判断部分交给看图的一方（人或 VLM）。

  plan  切区 → crops/ + montage.png + cm.png（色图）+ regions.json（paint_prompt 留空）
        把 montage.png 交给看图的一方，把每段的外观描述填进 regions.json 的
        caption（只写可见外观：材质/颜色/图案/饰物/廓形，≤25 词，不写身份、
        姿势、背景、镜头词）。

  build 读填好的 regions.json → 调 moss_layout.py → layout.json
        可直接喂：moss_run.py --t2i --depth-map D.png --color-map <cm> \
                              --prompt-file layout.json

三种分区方式（`--fit` 决定是否贴人）：

  1) 等分横带      --bands 3 --fit bg
  2) 自动按体形切  --bands 3 --fit bg --split auto     ← 腰/裙摆为界，不再等分
  3) 部件级框      --rect "0.25,0.05,0.78,0.28" --rect "0.20,0.28,0.82,0.52" ... \
                   --fit bg                            ← 每块单独给框，最可控

`--fit bg|depth` 会把色图按人物轮廓逐行收边（并且**保留发丝/腿间空隙**，
不会把中间填成一块），每侧再向内收 `--fit-erode` 像素。`--min-area` 去掉
火星、发丝尖这类零碎块。

用法：
  python moss_region_caption.py plan  --ref s5_char.png --bands 3 --fit bg \
         --out work/reg --summary "<整图一句话>"
  python moss_region_caption.py plan  --ref card.png --fit bg \
         --rect "0.25,0.05,0.78,0.28" --rect "0.20,0.28,0.82,0.52" \
         --rect "0.10,0.52,0.90,1.00" --out work/reg2
  python moss_region_caption.py build --dir work/reg

边界：3 段是默认；实测 4 段以上互相污染（见 MANUAL-分区色图 §8），需要更细
就改用 `--rect` 拆部件、分两次生成，而不是继续加带。
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

import numpy as np
from PIL import Image, ImageDraw

PALETTE = ["#7fb3ff", "#5ac878", "#aa5adc", "#e8a33d", "#e2603d", "#3fb6b5",
           "#d94f8a", "#8fa93b", "#6d5bd0", "#c99a2e"]
HERE = os.path.dirname(os.path.abspath(__file__))
LAYOUT_TOOL = os.path.join(HERE, "moss_layout.py")
INPUT_DIR = os.environ.get("GRABTAG_INPUT_DIR", "")  # 出图侧要读的目录；留空 = 不同步


def hex2rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------- 分区定义

def parse_bands(spec, n):
    if spec:
        out = []
        for s in spec:
            a, b = s.split(":")
            out.append((float(a), float(b)))
        return out
    if n == 1:
        return [(0.05, 0.95)]
    step = 0.90 / n
    return [(round(0.05 + i * step, 4), round(0.05 + (i + 1) * step, 4)) for i in range(n)]


def fit_mask(a, W, H):
    """H×W 布尔掩码：True=人物。--fit none 返回 None。"""
    if a.fit == "none":
        return None
    src = a.fit_src or a.ref
    im = Image.open(src).convert("RGB").resize((W, H), Image.LANCZOS)
    arr = np.asarray(im, dtype=np.float32)
    if a.fit == "bg":
        corners = np.concatenate([
            arr[:6, :6].reshape(-1, 3), arr[:6, -6:].reshape(-1, 3),
            arr[-6:, :6].reshape(-1, 3), arr[-6:, -6:].reshape(-1, 3),
        ])
        bg = np.median(corners, axis=0)
        return np.abs(arr - bg).max(axis=2) > a.fit_thresh * 255.0
    return (arr.mean(axis=2) / 255.0) > a.fit_thresh  # depth：亮=近=人


def clean_mask(mask, min_area_frac):
    """剔除零碎块（火星、发丝尖），保留主连通体。"""
    if mask is None or min_area_frac <= 0:
        return mask
    try:
        from scipy import ndimage
    except Exception:
        return mask
    lab, n = ndimage.label(mask)
    if n <= 1:
        return mask
    sizes = ndimage.sum(mask, lab, range(1, n + 1))
    big = max(float(sizes.max()), 1.0)
    keep = np.zeros_like(mask)
    for i, s in enumerate(sizes, start=1):
        if s >= min_area_frac * big:
            keep |= (lab == i)
    return keep


def detect_landmarks(mask, n):
    """按人物宽度曲线切带：最细处=腰，腰下最宽处=裙摆/胯。"""
    rows = np.flatnonzero(mask.any(axis=1))
    if rows.size < 10:
        return parse_bands([], n)
    Hf = float(mask.shape[0])
    top, bot = int(rows[0]), int(rows[-1])
    h = bot - top + 1
    width = np.array([mask[y].sum() for y in range(top, bot + 1)], dtype=np.float32)
    k = max(3, (h // 40) | 1)
    ws = np.convolve(width, np.ones(k, np.float32) / k, mode="same")

    def arg_px(i0, i1, fn):
        i0, i1 = max(top, int(i0)), min(bot, int(i1))
        if i1 <= i0:
            return i0
        return i0 + int(fn(ws[i0 - top:i1 - top]))

    if n == 3:
        waist = arg_px(top + 0.28 * h, top + 0.55 * h, np.argmin)
        low = arg_px(waist + 1, top + 0.90 * h, np.argmax)
        return [(round(top / Hf, 4), round(waist / Hf, 4)),
                (round(waist / Hf, 4), round(low / Hf, 4)),
                (round(low / Hf, 4), round(min(1.0, bot / Hf + 0.005), 4))]
    step = h / float(n)
    return [(round((top + i * step) / Hf, 4), round((top + (i + 1) * step) / Hf, 4))
            for i in range(n)]


def seed_object_mask(rgb, seed_xy, rect_xyxy, iters=6, clamp=None):
    """给定中心点，用 GrabCut 把该物体的轮廓抠出来（返回布尔掩码）。

    rgb         : 画布尺寸的参考图（H×W×3, uint8）
    seed_xy     : (x, y) 像素，视觉识别给出的物体中心
    rect_xyxy   : 搜索框 (x0, y0, x1, y1)，框外一定是背景
    clamp       : 可选，人物掩码；结果会被限制在人物内
    """
    try:
        import cv2
    except Exception:
        return None
    h, w = rgb.shape[:2]
    x0, y0, x1, y1 = rect_xyxy
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w - 1, x1), min(h - 1, y1)
    if x1 - x0 < 8 or y1 - y0 < 8:
        return None

    gcut = np.full((h, w), cv2.GC_BGD, np.uint8)
    gcut[y0:y1, x0:x1] = cv2.GC_PR_BGD
    gcut[y0:y1, x0:x1] = cv2.GC_PR_FGD
    r = max(3, int(min(w, h) * 0.008))
    cv2.circle(gcut, (int(seed_xy[0]), int(seed_xy[1])), r, cv2.GC_FGD, -1)

    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    bgd, fgd = np.zeros((1, 65), np.float64), np.zeros((1, 65), np.float64)
    try:
        cv2.grabCut(bgr, gcut, None, bgd, fgd, iters, cv2.GC_INIT_WITH_MASK)
    except Exception:
        return None
    obj = np.isin(gcut, [cv2.GC_FGD, cv2.GC_PR_FGD])

    # 只保留包含中心点的那一块，避免抓走旁边同色物件
    try:
        from scipy import ndimage
        lab, n = ndimage.label(obj)
        if n > 1:
            sid = lab[int(seed_xy[1]), int(seed_xy[0])]
            if sid == 0:  # 中心点落在背景上 → 取最近的块
                ys, xs = np.nonzero(obj)
                if ys.size:
                    k = np.argmin((xs - seed_xy[0]) ** 2 + (ys - seed_xy[1]) ** 2)
                    sid = lab[ys[k], xs[k]]
            if sid > 0:
                obj = (lab == sid)
    except Exception:
        pass

    if clamp is not None:
        obj = obj & clamp
    return obj


# ---------------------------------------------------------------- SAM 掩码
_SAM = {}
SAM_DEFAULT_CKPT = os.environ.get("GRABTAG_SAM_CKPT", "sam_vit_b_01ec64.pth")


COMFY_PROBE_PORT = int(os.environ.get("MOSS_COMFY_PORT", "8190"))
_DEV_NOTE = set()


def _note(msg):
    """同一句设备提示只打一次，免得逐件刷屏。"""
    if msg not in _DEV_NOTE:
        _DEV_NOTE.add(msg)
        print(msg)


def _comfy_running(port=None, timeout=0.3):
    """ComfyUI 是否在跑：在跑就意味着生成后显存会被它的模型常驻占住。"""
    import urllib.request
    port = port or COMFY_PROBE_PORT
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/system_stats" % port, timeout=timeout) as r:
            return r.status == 200
    except Exception:
        return False


def _sam_load(ckpt, device="auto"):
    # 设备解析：auto = 先看 ComfyUI 在不在跑，再看显存余量；显式 cuda 只警告不改
    _want = (device or "auto").lower()
    try:
        import torch as _t
        if not _t.cuda.is_available():
            device = "cpu"
        else:
            _free_b, _tot_b = _t.cuda.mem_get_info()
            _free_mb = _free_b / 1024 ** 2
            if _want == "cuda":
                device = "cuda"
                if _free_b < 1.5 * 1024 ** 3:
                    _note("[sam] 注意：显存只剩 %.0f MB，仍按 --sam-device cuda 走 GPU" % _free_mb)
            elif _comfy_running():
                device = "cpu"
                _note("[sam] 检测到 ComfyUI :%d 在跑（生成后其模型常驻显存），SAM 自动走 CPU；"
                      "要用 GPU 请显式 --sam-device cuda" % COMFY_PROBE_PORT)
            elif _free_b < 1.5 * 1024 ** 3:
                device = "cpu"
                _note("[sam] 显存只剩 %.0f MB / %.0f MB，改走 CPU" % (_free_mb, _tot_b / 1024 ** 2))
            else:
                device = "cuda"
                _note("[sam] 显存余量 %.0f MB，走 GPU" % _free_mb)
    except Exception:
        device = "cpu"
    key = os.path.abspath(ckpt or SAM_DEFAULT_CKPT)
    if key in _SAM:
        return _SAM[key]
    try:
        import torch
        from segment_anything import sam_model_registry, SamPredictor
    except Exception as e:  # 没装就老老实实退 GrabCut
        print("[sam] 不可用（%s），退回 GrabCut" % e)
        _SAM[key] = None
        return None
    if not os.path.exists(key):
        print("[sam] 找不到权重 %s，退回 GrabCut" % key)
        _SAM[key] = None
        return None
    want = (device or "auto").lower()
    devs = ([("cuda" if torch.cuda.is_available() else "cpu")] if want == "auto" else [want])
    if want == "auto" and torch.cuda.is_available():
        devs = ["cuda", "cpu"]           # cuda 放不下就落 CPU
    t0 = time.time()
    sam = None
    for dev in devs:
        try:
            sam = sam_model_registry["vit_b"](checkpoint=key).to(dev).eval()
            break
        except Exception as e:            # OOM / 权重损坏 / 无 CUDA：换下一档
            print("[sam] %s 载入失败（%s）" % (dev, str(e).splitlines()[0][:80]))
            sam = None
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
    if sam is None:
        print("[sam] 全部设备都载入失败，退回 GrabCut")
        _SAM[key] = None
        return None
    _SAM[key] = SamPredictor(sam)
    _SAM["__dev__"] = dev
    print("[sam] 载入 %-28s %.1fs  dev=%s" % (os.path.basename(key), time.time() - t0, dev))
    return _SAM[key]


def _sam_predictor(rgb, ckpt, cache_key, device="auto"):
    pred = _sam_load(ckpt, device)
    if pred is None:
        return None
    if _SAM.get("__image__") != cache_key:
        t0 = time.time()
        try:
            pred.set_image(rgb)
        except Exception as e:
            if _SAM.get("__dev__") == "cuda":
                print("[sam] 编码 OOM（%s），模型转 CPU 重试" % str(e).splitlines()[0][:60])
                try:
                    import torch
                    torch.cuda.empty_cache()
                    pred.model.to("cpu").eval()
                    _SAM["__dev__"] = "cpu"
                    pred = type(pred)(pred.model)
                    pred.set_image(rgb)
                except Exception as e2:
                    print("[sam] CPU 也失败（%s），本轮不用 SAM" % str(e2).splitlines()[0][:60])
                    return None
            else:
                print("[sam] 编码失败（%s）" % str(e).splitlines()[0][:60])
                return None
        _SAM["__image__"] = cache_key
        print("[sam] 编码参考图 %.2fs  dev=%s" % (time.time() - t0, _SAM.get("__dev__")))
    return pred


def sam_object_mask(rgb, seed_xy, rect_xyxy, clamp=None, kind="bp", ckpt="", cache_key="", device="auto"):
    """SAM 版 seed_object_mask：同入参，返回布尔掩码。

    kind: box | point | bp(=box+point, 默认) | sharp(=bp) | flat(bp+框外负点) | line(框内沿走向加点)
    """
    pred = _sam_predictor(rgb, ckpt, cache_key, device)
    if pred is None:
        return None
    from scipy import ndimage as ndi
    x0, y0, x1, y1 = [int(v) for v in rect_xyxy]
    sx, sy = int(seed_xy[0]), int(seed_xy[1])
    box = np.array([[x0, y0, x1, y1]], dtype=float)[0]
    pts, labs, use_box = [[sx, sy]], [1], True
    if kind == "point":
        use_box = False
    elif kind == "flat":
        m = max(6, int(0.06 * min(x1 - x0, y1 - y0)))
        pts += [[x0 - m, y0 - m], [x1 + m, y0 - m], [x0 - m, y1 + m], [x1 + m, y1 + m]]
        labs += [0, 0, 0, 0]
    elif kind == "line":
        if (x1 - x0) >= (y1 - y0):
            pts += [[int(x0 + 0.15 * (x1 - x0)), sy], [int(x0 + 0.85 * (x1 - x0)), sy]]
        else:
            pts += [[sx, int(y0 + 0.15 * (y1 - y0))], [sx, int(y0 + 0.85 * (y1 - y0))]]
        labs += [1, 1]
    H, W = rgb.shape[:2]
    pts = np.array([[min(max(px, 0), W - 1), min(max(py, 0), H - 1)] for px, py in pts], dtype=float)
    labs = np.array(labs, dtype=int)
    def _predict(p, **kw):
        try:
            return p.predict(**kw)
        except Exception as e:
            msg = str(e).lower()
            if "out of memory" not in msg and "cuda" not in msg:
                raise
            import torch
            print("[sam] predict 显存不足（%s），清缓存并在 CPU 重试该件" % type(e).__name__)
            try:
                torch.cuda.empty_cache()
            except Exception:
                pass
            p.model.to("cpu")
            p.device = torch.device("cpu")
            p.is_image_set = False
            return p.predict(**kw)

    masks, scores, _ = _predict(pred,point_coords=pts, point_labels=labs,
                                    box=box if use_box else None, multimask_output=True)
    best = int(np.argmax(scores))
    m = masks[best]
    lbl, n = ndi.label(m)
    if n > 1:
        ids = lbl[min(max(sy, 0), H - 1), min(max(sx, 0), W - 1)]
        m = (lbl == ids) if ids > 0 else (lbl == (int(np.argmax(ndi.sum(m, lbl, range(1, n + 1)))) + 1))
    m = m.astype(bool)
    if clamp is not None:
        m = m & clamp
    return m


def row_runs(row, x0, x1, er):
    """一行掩码拆成若干连续段（保留空隙），每段向内收 er 像素。"""
    if x1 <= x0:
        return []
    seg = row[x0:x1]
    if not seg.any():
        return []
    edges = np.flatnonzero(np.diff(np.concatenate(([0], seg.astype(np.int8), [0]))))
    out = []
    for k in range(0, edges.size, 2):
        a0 = x0 + int(edges[k]) + er
        a1 = x0 + int(edges[k + 1]) - 1 - er
        if a1 - a0 >= 4:
            out.append((a0, a1))
    return out


# ---------------------------------------------------------------- plan

# ---------------- caption 自检（模板见 MANUAL-GrabTag §6.4）----------------
_LINT_BAN = {
    "girl": "身份词", "woman": "身份词", "lady": "身份词", "boy": "身份词",
    "standing": "姿势词", "sitting": "姿势词", "lying": "姿势词", "kneeling": "姿势词",
    "pose": "姿势词", "action": "姿势词",
    "close-up": "镜头词", "closeup": "镜头词", "camera": "镜头词", "shot": "镜头词",
    "background": "背景词", "backdrop": "背景词", "stage": "背景词",
    "lighting": "光照词", "backlight": "光照词", "shadow": "光照词",
    "beautiful": "质量词", "ornate": "质量词", "intricate": "质量词",
    "detailed": "质量词", "masterpiece": "质量词", "8k": "质量词", "4k": "质量词",
    "realistic": "质量词", "photorealistic": "质量词", "anime": "画风词",
    "illustration": "画风词", "hand-drawn": "画风词",
}
_LINT_ANCHOR = (" on ", " at ", " across ", " along ", " from ", " over ", " around ",
                " behind ", " above ", " below ", " beneath ", " between ",
                " hanging", " attached", " tied", " wrapped", " draped",
                " printed", " embroidered", " set with", " centred", " centered")
_LINT_COLOR = (" black", " white", " red", " crimson", " blue", " navy", " green", " teal",
               " gold", " golden", " silver", " purple", " violet", " lilac", " pink",
               " orange", " yellow", " brown", " cream", " ivory", " bronze", " copper",
               " jade", " emerald", " turquoise", " grey", " gray", " beige")



def _pick(d, *keys):
    """兼容旧字段名：按顺序取第一个非空值。"""
    for k in keys:
        v = d.get(k) if isinstance(d, dict) else None
        if v:
            return v
    return ""

def lint_caption(text):
    """返回 (level, 原因列表)；level: ok / warn / fail"""
    t = (text or "").strip()
    if not t:
        return "warn", ["paint_prompt 为空"]
    low = " " + t.lower() + " "
    words = len(re.findall(r"[A-Za-z0-9\'-]+", t))
    reasons, level = [], "ok"

    if words > 18:
        reasons.append("太长 %d 词（≤18）" % words)
        level = "fail"
    elif words > 14:
        reasons.append("偏长 %d 词（建议 ≤14）" % words)
        level = "warn"

    hits = sorted({v for k, v in _LINT_BAN.items() if k in low})
    if hits:
        reasons.append("含不该写的词类别：" + "/".join(hits))
        level = "fail"

    if not any(a in low for a in _LINT_ANCHOR):
        reasons.append("缺落点（写清长在哪块身体/哪块布上）")
        level = "fail"

    if not any(c in low for c in _LINT_COLOR):
        reasons.append("没写颜色")
        if level == "ok":
            level = "warn"

    if low.count(" and ") >= 2:
        reasons.append("含 %d 个 and —— 可能塞了多件东西（一区一物）" % low.count(" and "))
        if level == "ok":
            level = "warn"

    return level, reasons


def print_lint(regions, strict=False):
    rows = [(r.get("id", "?"), r.get("paint_prompt", "")) for r in regions]
    if not rows:
        return False
    bad = 0
    print("[lint] paint_prompt 自检（模板：≤18 词 / 只写一件 / 必带落点 / 带颜色）")
    for name, cap in rows:
        level, reasons = lint_caption(cap)
        if level == "ok":
            continue
        bad += level == "fail"
        mark = "FAIL" if level == "fail" else "warn"
        print("  %-10s %s  %s" % (name, mark, "；".join(reasons)))
    if bad == 0:
        print("  -> 全部通过")
    else:
        print("  -> %d 条不达标，改完再 build" % bad)
    return strict and bad > 0


def cmd_plan(a):
    ref = Image.open(a.ref).convert("RGB")
    W0, H0 = ref.size
    W, H = (int(v) for v in a.size.lower().split("x"))
    os.makedirs(a.out, exist_ok=True)
    crop_dir = os.path.join(a.out, "crops")
    os.makedirs(crop_dir, exist_ok=True)

    mask = clean_mask(fit_mask(a, W, H), a.min_area)
    er = max(0, a.fit_erode)
    ins = max(0.0, min(0.45, a.x_inset))
    rgb_cnv = None

    # 四种分区 → 统一成 zones: (y0f, y1f, x0f, x1f)  分数坐标；x 为 None = 用 inset
    spec_items = []
    if a.spec:
        with open(a.spec, "r", encoding="utf-8") as fh:
            spec_items = json.load(fh)
        zones, split = [], "spec"
        for it in spec_items:
            x0f, y0f, x1f, y1f = it["rect"]
            zones.append((y0f, y1f, x0f, x1f))
        if not zones:
            print("[plan] spec 里没有 rect")
            return 2
        if any(it.get("seed") for it in spec_items):
            rgb_cnv = np.asarray(Image.open(a.fit_src or a.ref).convert("RGB")
                                 .resize((W, H), Image.LANCZOS), dtype=np.uint8)
        # 画序：priority 小的先画、大的后画（后画的赢）；同 priority 时大面积先画
        if a.paint != "asis":
            def _area(it):
                x0, y0, x1, y1 = it["rect"]
                return (x1 - x0) * (y1 - y0)

            def _key(it):
                pri = int(it.get("priority", 0))
                area = _area(it)
                return (pri, -area) if a.paint == "big-first" else (pri, area)

            spec_items = sorted(spec_items, key=_key)
            zones = [(it["rect"][1], it["rect"][3], it["rect"][0], it["rect"][2]) for it in spec_items]
    elif a.rect:
        zones, split = [], "rect"
        for s in a.rect:
            p = [float(v) for v in s.replace(" ", "").split(",")]
            if len(p) == 4:
                zones.append((p[1], p[3], p[0], p[2]))
        if not zones:
            print("[plan] --rect 解析为空，检查格式 x0,y0,x1,y1")
            return 2
    elif a.band:
        zones = [(b0, b1, None, None) for b0, b1 in parse_bands(a.band, len(a.band))]
        split = "manual-band"
    elif a.split == "auto" and mask is not None:
        zones = [(b0, b1, None, None) for b0, b1 in detect_landmarks(mask, a.bands)]
        split = "auto"
    else:
        zones = [(b0, b1, None, None) for b0, b1 in parse_bands([], a.bands)]
        split = "even"

    cm = Image.new("RGB", (H, H * 0 + H), "#ffffff") if False else Image.new("RGB", (W, H), "#ffffff")
    d = ImageDraw.Draw(cm)
    painted = np.zeros((H, W), dtype=bool)
    own_masks = {}
    regions, thumbs = [], []

    # ---- 物件数：按"同件多词算一个"统计，硬上限 --max-items ----
    max_items = int(getattr(a, "max_items", 20) or 20)
    if spec_items:
        groups = {}
        for it in spec_items:
            canon = (it.get("object") or it.get("name") or "").strip().lower()
            g = groups.setdefault(canon, [])
            g.append(it.get("name") or canon)
            for al in (it.get("aliases") or []):
                if al not in g:
                    g.append(al)
        n_items = len([k for k in groups if k])
        merged = ["%s ← %s" % (k, "/".join(v)) for k, v in groups.items() if len(v) > 1]
        print("[plan] 物件数 %d（上限 %d，区域 %d 块）%s"
              % (n_items, max_items, len(spec_items),
                 ("；同件多词已合并：" + "；".join(merged)) if merged else ""))
        if n_items > max_items:
            print("[plan] 物件数超过上限 %d —— 请合并同一物体（同 object 名 / aliases），或拆成多次出图："
                  % max_items)
            for k, v in groups.items():
                print("        %-14s %s" % (k, ", ".join(v)))
            return 2

    # ---- 几何表（每区的画布像素框）----
    geoms = []
    for i, (y0f, y1f, x0f, x1f) in enumerate(zones):
        it = spec_items[i] if spec_items else {}
        rid = it.get("name") or ("r%d" % (i + 1))
        color = it.get("color") or PALETTE[i % len(PALETTE)]
        y0, y1 = max(0, int(H * y0f)), min(H, int(H * y1f))
        if x0f is None:
            x0, x1 = int(W * ins), int(W * (1 - ins))
        else:
            x0, x1 = max(0, int(W * x0f)), min(W, int(W * x1f))
        geoms.append({"it": it, "rid": rid, "color": color,
                      "y0f": y0f, "y1f": y1f, "x0f": x0f, "x1f": x1f,
                      "x0": x0, "x1": x1, "y0": y0, "y1": y1})

    # ---- 颜色唯一性自检（手册 v2.0 §4.3：moss_layout 按颜色归并，撞色会静默少 item）----
    _seen = {}
    for g in geoms:
        _seen.setdefault(g["color"].lower(), []).append(g["rid"])
    _dups = {c: rs for c, rs in _seen.items() if len(rs) > 1}
    if _dups:
        print("[plan] 颜色撞车 —— %d 组区域用了同一个色值，导出时会静默合并成更少的 items："
              % len(_dups))
        for c, rs in _dups.items():
            print("        %s ← %s" % (c, ", ".join(rs)))
        print("        改 spec 里这些区的 color（或删掉 color 交给自动配色），保证每块唯一")
        return 2
    _rgb = {g["color"].lower(): hex2rgb(g["color"]) for g in geoms}
    if len(_rgb) > 1:
        _ks = list(_rgb)
        for _ci in range(len(_ks)):
            for _cj in range(_ci + 1, len(_ks)):
                _d = float(np.linalg.norm(np.array(_rgb[_ks[_ci]], float)
                                          - np.array(_rgb[_ks[_cj]], float)))
                if _d < 40:
                    print("[plan] 提醒：%s 与 %s 色差仅 %.0f（<40），色图上难分辨"
                          % (_ks[_ci], _ks[_cj], _d))
    for g in geoms:
        _r, _g, _b = _rgb[g["color"].lower()]
        if _r > 235 and _g > 235 and _b > 235:
            print("[plan] 警告：%s 的色值 %s 接近纯白，会被当背景挖掉" % (g["rid"], g["color"]))

    # ---- requires 预取：父件掩码先算好，子件 clamp 到父件里 ----
    rect_mask = {}
    for g in geoms:
        m = np.zeros((H, W), dtype=bool)
        m[g["y0"]:g["y1"], g["x0"]:g["x1"]] = True
        if mask is not None and not g["it"].get("no_clamp"):
            m &= mask
        rect_mask[g["rid"]] = m

    seed_mask = {}
    pending = [g for g in geoms if g["it"].get("seed") and rgb_cnv is not None]

    masker = getattr(a, "masker", "grabcut")
    sam_ckpt = getattr(a, "sam_ckpt", "") or SAM_DEFAULT_CKPT
    sam_kind = getattr(a, "sam_kind", "auto")
    sam_key = "%s|%dx%d" % (os.path.abspath(a.ref), W, H)

    def _grab(g, clamp, how="grabcut"):
        sx = int(g["it"]["seed"][0] * W)
        sy = int(g["it"]["seed"][1] * H)
        rect = (g["x0"], g["y0"], g["x1"], g["y1"])
        if how == "grabcut":
            obj = seed_object_mask(rgb_cnv, (sx, sy), rect, clamp=clamp)
        else:
            obj = sam_object_mask(rgb_cnv, (sx, sy), rect, clamp=clamp, kind=how,
                                  ckpt=sam_ckpt, cache_key=sam_key,
                                  device=getattr(a, "sam_device", "auto"))
        if obj is None or obj.sum() < 40:
            return None
        if how != "grabcut":
            # 越界率过高 / 相对 rect 面积过小 → 这一档判为失败
            rm = np.zeros((H, W), bool)
            rm[rect[1]:rect[3], rect[0]:rect[2]] = True
            out = float((obj & ~rm).sum()) / max(1, int(obj.sum()))
            fill = float(obj.sum()) / max(1, int(rm.sum()))
            if out > 0.40 or fill < 0.04:
                return None
        g["_how"] = how
        return obj

    def _mask(g, clamp):
        want = (g["it"].get("sam") or sam_kind)
        if want in ("grabcut", "off", "none"):
            chain = ["grabcut"]
        elif masker == "grabcut":
            chain = ["grabcut"]
        elif masker == "sam-auto":
            chain = [("bp" if want == "auto" else want), "box", "grabcut"]
        else:  # sam-bp / sam-box / sam-point
            chain = [masker.split("sam-", 1)[1], "grabcut"]
        for how in chain:
            obj = _grab(g, clamp, how)
            if obj is not None:
                return obj
        g["_how"] = "none"
        return None

    for _pass in range(4):
        if not pending:
            break
        nxt = []
        for g in pending:
            req = g["it"].get("requires") or []
            if not all((r in seed_mask) or (r in rect_mask) for r in req):
                nxt.append(g)
                continue
            pm = None
            if req:
                layers = [seed_mask.get(r) if r in seed_mask else rect_mask.get(r) for r in req]
                layers = [x for x in layers if x is not None]
                if layers:
                    pm = np.logical_and.reduce(layers)
                    if not pm.any():
                        print("[plan] %s: 父件掩码为空（%s），忽略 requires" % (g["rid"], ",".join(req)))
                        pm = None
            clamp = None if g["it"].get("no_clamp") else mask
            if pm is not None:
                clamp = pm if clamp is None else (clamp & pm)
            obj = _mask(g, clamp)
            if obj is None:
                print("[plan] %s: 掩码 <40px 或质量不达标，退回矩形" % g["rid"])
            else:
                seed_mask[g["rid"]] = obj
        if len(nxt) == len(pending):
            for g in nxt:
                print("[plan] %s: requires %s 无法解析，按无 requires 处理"
                      % (g["rid"], g["it"].get("requires")))
                clamp = None if g["it"].get("no_clamp") else mask
                obj = _mask(g, clamp)
                if obj is not None:
                    seed_mask[g["rid"]] = obj
            break
        pending = nxt

    for i, g in enumerate(geoms):
        rid, color, it = g["rid"], g["color"], g["it"]
        y0f, y1f, x0f, x1f = g["y0f"], g["y1f"], g["x0f"], g["x1f"]
        x0, x1, y0, y1 = g["x0"], g["x1"], g["y0"], g["y1"]

        obj = seed_mask.get(rid)
        own = np.zeros((H, W), dtype=bool)
        src_mask = obj if obj is not None else mask
        if src_mask is None:
            own[y0:y1, x0:x1] = True
            d.rectangle([x0, y0, x1 - 1, y1 - 1], fill=hex2rgb(color))
            span = (x0, x1)
        else:
            lo, hi = W, 0
            for y in range(y0, y1):
                for a0, a1 in row_runs(src_mask[y], x0, x1, er):
                    own[y, a0:a1] = True
                    d.rectangle([a0, y, a1, y], fill=hex2rgb(color))
                    lo, hi = min(lo, a0), max(hi, a1)
            span = (lo, hi) if hi > lo else (x0, x1)
        own_n = int(own.sum())
        own_masks[rid] = own
        painted |= own

        crop = ref.crop((int(x0f * W0) if x0f is not None else 0, int(H0 * y0f),
                         int(x1f * W0) if x1f is not None else W0, int(H0 * y1f)))
        crop_path = os.path.join(crop_dir, rid + ".png")
        crop.save(crop_path)
        t = crop.copy()
        t.thumbnail((520, 520))
        thumbs.append((rid, t))
        regions.append({
            "id": rid,
            "color": color,
            "rect": [round(x0f if x0f is not None else 0.0, 4), round(y0f, 4),
                     round(x1f if x1f is not None else 1.0, 4), round(y1f, 4)],
            "span": [int(span[0]), int(span[1])],
            "crop": os.path.relpath(crop_path, a.out),
            "paint_prompt": _pick(it, "paint_prompt", "caption").strip(),
            "ref_tag": _pick(it, "ref_tag", "tag").strip(),
            "priority": int(it.get("priority", 0)),
            "masker": g.get("_how", ""),
            "own_px": own_n,
        })

    # ---- 字段错位提示：tag（参考侧）与 caption（生成侧）不通用 ----
    _no_tag = [r["id"] for r in regions if not r.get("ref_tag") and r.get("paint_prompt")]
    _no_cap = [r["id"] for r in regions if r.get("ref_tag") and not r.get("paint_prompt")]
    if a.mode == "ref" and _no_tag:
        print("[plan] 提示：%d 个区只有 paint_prompt 没有 ref_tag(%s) —— 参考侧要的是 ref_tag（这块是什么），"
              "paint_prompt 是生成侧写法（必带落点），两者不通用"
              % (len(_no_tag), ", ".join(_no_tag[:6])))
    if a.mode == "layout" and _no_cap:
        print("[plan] 提示：%d 个区只有 ref_tag 没有 paint_prompt(%s) —— 生成侧要的是 paint_prompt（这块画什么 + 落点）"
              % (len(_no_cap), ", ".join(_no_cap[:6])))

    cm_path = os.path.join(a.out, "cm.png")
    if a.mode == "ref":
        cm_input = None
    else:
        cm.save(cm_path)

    # ComfyUI 的 LoadImage 只接受 input/ 下的相对文件名
    cm_input = ""
    tag = os.path.basename(os.path.abspath(a.out.rstrip("\\/"))) or "reg"
    # 只有显式给了 GRABTAG_INPUT_DIR（或该目录本来就存在）才同步，且不主动建目录：
    # 独立使用时不该在仓库里长出一个 ComfyUI/input。
    if a.mode != "ref" and INPUT_DIR and os.path.isdir(INPUT_DIR):
        try:
            cm_input = "regcap_%s.png" % tag
            cm.save(os.path.join(INPUT_DIR, cm_input))
        except OSError as e:
            cm_input = ""
            print("[plan] 警告：色图没能同步到 %s（%s），出图时需自己拷过去" % (INPUT_DIR, e))

    # ---- 拼图：网格排布，每格左上角序号 + 区名，便于逐格打标 ----
    try:
        from PIL import ImageFont
        _fs = 20
        font = None
        for _fp in (r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf",
                    r"C:\Windows\Fonts\arial.ttf"):
            if os.path.exists(_fp):
                font = ImageFont.truetype(_fp, _fs)
                break
    except Exception:
        font = None
    COL, CELL, PAD = 4, 300, 8
    rows = (len(thumbs) + COL - 1) // COL
    sheet = Image.new("RGB", (COL * (CELL + PAD) + PAD, rows * (CELL + PAD) + PAD), "white")
    dd = ImageDraw.Draw(sheet)
    for i, (rid, t) in enumerate(thumbs):
        cx = PAD + (i % COL) * (CELL + PAD)
        cy = PAD + (i // COL) * (CELL + PAD)
        box = t.copy()
        box.thumbnail((CELL, CELL))
        sheet.paste(box, (cx + (CELL - box.width) // 2, cy + (CELL - box.height) // 2))
        tag_txt = "%d·%s" % (i + 1, rid)
        if font is not None:
            bb = dd.textbbox((0, 0), tag_txt, font=font)
            w, h = bb[2] - bb[0], bb[3] - bb[1]
        else:
            w, h = 7 * len(tag_txt), 11
        dd.rectangle([cx, cy, cx + w + 10, cy + h + 8], fill="black")
        dd.text((cx + 5, cy + 4 - (bb[1] if font is not None else 0)), tag_txt,
                fill="white", font=font)
    montage_path = os.path.join(a.out, "montage.png")
    sheet.save(montage_path)

    # 覆盖体检：最终色图里，每个区自己的像素还剩多少（= 有没有被更高优先级区吃掉）
    cm_arr = np.asarray(cm.convert("RGB"), dtype=int) if a.mode != "ref" else None
    colour_pri = {}
    for r in regions:
        colour_pri.setdefault(r["color"], int(r.get("priority", 0)))
    for r in (regions if a.mode != "ref" else []):
        own = own_masks.get(r["id"])
        if own is None:
            continue
        mine = np.all(cm_arr == np.array(hex2rgb(r["color"]), dtype=int), axis=2)
        own_n = int(own.sum())
        pri = int(r.get("priority", 0))
        kept = float((mine & own).sum()) / max(1, own_n)
        # 被"更高优先级"的区盖掉属设计（小件压在大件上）；被同级或更低级盖掉才是真问题
        hi = np.zeros_like(own)
        for c2, p2 in colour_pri.items():
            if p2 >= pri:
                hi |= np.all(cm_arr == np.array(hex2rgb(c2), dtype=int), axis=2)
        loss_hi = float((own & ~mine & hi).sum()) / max(1, own_n)
        r["own_px"] = own_n
        r["kept"] = round(kept, 3)
        r["loss_hi"] = round(loss_hi, 3)
    rows = [(r["id"], r.get("priority", 0), r.get("own_px", 0), r.get("kept", 1.0))
            for r in regions if "kept" in r]
    # 只有"高优先级、且丢失的像素不是被更高优先级的区拿走"才是真问题
    bad = [(r["id"], r["priority"], r["kept"], r.get("loss_hi", 0.0)) for r in regions
           if r.get("kept", 1.0) < 0.9 and r.get("priority", 0) >= 5
           and (1.0 - r.get("kept", 1.0) - r.get("loss_hi", 0.0)) > 0.10]
    if rows and not getattr(a, "quiet", False):
        bad_ids = {b[0] for b in bad}
        print("[plan] 覆盖体检（kept = 最终色图里自己还留着像素的比例）")
        for rid, pri, own, kept in sorted(rows, key=lambda x: (x[3], -x[1])):
            if kept >= 0.9:
                flag = ""
            elif rid in bad_ids:
                flag = "  <- 危险：被同级/更低优先级盖掉"
            elif pri >= 5:
                flag = "  (被更高优先级小件覆盖，设计内)"
            else:
                flag = "  (基底被盖，正常)"
            print("   %-9s pri=%-2d own=%6d  kept=%5.1f%%%s" % (rid, pri, own, kept * 100, flag))
        if bad:
            print("   -> %d 项危险：%s（提高它们的 priority，或缩小压住它的大件 rect）"
                  % (len(bad), ", ".join("%s(pri=%d, %.0f%%)" % (i, p, k * 100) for i, p, k, _ in bad)))
        else:
            print("   -> 所有高优先级物件都保住了（被更高优先级小件覆盖属设计）")

    meta = {
        "ref": os.path.abspath(a.ref),
        "ref_size": [W0, H0],
        "size": [W, H],
        "x_inset": ins,
        "fit": a.fit,
        "fit_erode": er,
        "min_area": a.min_area,
        "split": split,
        "summary": a.summary,
        "cm": os.path.relpath(cm_path, a.out),
        "cm_input": cm_input,
        "montage": os.path.relpath(montage_path, a.out),
        "regions": regions,
    }
    with open(os.path.join(a.out, "regions.json"), "w", encoding="utf-8") as fh:
        json.dump(meta, fh, ensure_ascii=False, indent=2)
    if print_lint(regions, strict=getattr(a, "lint_strict", False)):
        return 2
    tags = [{"name": r["id"], "ref_tag": r["ref_tag"], "crop": r["crop"],
              "color": r["color"], "area": r.get("own_px", 0)}
            for r in meta["regions"] if r.get("ref_tag")]
    if tags and a.mode != "layout":
        tp = os.path.join(a.out, "tags.json")
        with open(tp, "w", encoding="utf-8") as fh:
            json.dump({"ref": meta["ref"], "size": meta.get("size"),
                       "summary": meta.get("summary", ""), "tags": tags},
                      fh, ensure_ascii=False, indent=2)
        print("[plan] 参考侧 tag : %s（%d 件）" % (tp, len(tags)))

    print("[plan] 参考图 %dx%d → 画布 %dx%d，%d 区，fit=%s，切法=%s"
          % (W0, H0, W, H, len(regions), a.fit, split))
    for r in regions:
        sw = (r["span"][1] - r["span"][0]) / float(W) * 100.0
        print("  %s %s rect=%s span=%s(%.0f%%宽) crop=%s"
              % (r["id"], r["color"], r["rect"], r["span"], sw, r["crop"]))
    print("[plan] 拼图      : %s" % montage_path)
    if a.mode == "ref":
        print("[plan] 模式      : ref（只出裁片与 tags.json；裁片要复制到 ComfyUI/input/ 才能当参考图）")
    else:
        print("[plan] 色图      : %s" % cm_path)
        if cm_input:
            print("[plan] 已同步到   : %s（--color-map 用这个名字）" % os.path.join(INPUT_DIR, cm_input))
    print("[plan] 待填 JSON : %s" % os.path.join(a.out, "regions.json"))
    return 0


# ---------------------------------------------------------------- build


def _run_layout(argv):
    """同包内调用 layout 模块（独立仓库里没有并排的 moss_layout.py）。"""
    try:
        from . import layout as _layout
    except ImportError:                                   # 允许直接 python region_caption.py
        import layout as _layout
    old = sys.argv
    sys.argv = [old[0]] + list(argv)
    try:
        res = _layout.main()
        return 0 if res is None else int(res)
    finally:
        sys.argv = old


def cmd_build(a):
    meta_path = os.path.join(a.dir, "regions.json")
    with open(meta_path, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    missing = [r["id"] for r in meta["regions"] if not r.get("paint_prompt", "").strip()]
    if missing:
        print("[build] 这些区还没有 paint_prompt：%s" % ", ".join(missing))
        return 2

    out = a.layout or os.path.join(a.dir, "layout.json")
    cm = os.path.join(a.dir, meta.get("cm") or "")
    if not meta.get("cm") or not os.path.exists(cm):
        print("[build] 这个目录没有色图（大概是 --for ref 生成的）——"
              "要出 layout 请用 --for layout 或 --for both 重跑 plan；"
              "只要参考侧 tags 就直接用 tags.json，不必 build。")
        return 2
    cmd = ["--colormap", cm, "--out", out, "--pretty"]
    if meta.get("summary"):
        cmd += ["--summary", meta["summary"]]
    for r in meta["regions"]:
        cmd += ["--item", "%s|object|%s" % (r["color"], r["paint_prompt"].strip())]
    if a.quiet:
        cmd.append("--quiet")
    if getattr(a, "sort", "area") and a.sort != "area":
        cmd += ["--sort", a.sort]

    rc = _run_layout(cmd)
    if rc == 0:
        with open(out, "r", encoding="utf-8") as fh:
            lay = json.load(fh)
        print("[build] layout 就绪：%s（%d items）" % (out, len(lay.get("items", []))))

        # ---- 覆盖体检回写：把 pri>=5 且 kept<90% 的区标进 layout.audit.json ----
        by_id = {r["id"]: r for r in meta["regions"]}
        warn = []
        for it in lay.get("items", []):
            cap = _pick(it, "paint_prompt", "caption").strip()
            hit = None
            for r in meta["regions"]:
                if cap and cap == (r.get("paint_prompt") or "").strip():
                    hit = r
                    break
            if not hit:
                continue
            kept = hit.get("kept")
            pri = hit.get("priority", 0)
            it["_region"] = hit["id"]
            if kept is not None:
                it["_kept"] = kept
            loss_hi = hit.get("loss_hi", 0.0) or 0.0
            if kept is not None and pri >= 5 and kept < 0.9 and (1.0 - kept - loss_hi) > 0.10:
                it["_warn"] = "kept %.0f%% at priority %d, %.0f%% of the loss is not from higher-priority regions" % (kept * 100, pri, (1.0 - kept - loss_hi) * 100)
                warn.append((hit["id"], pri, kept))
        audit = os.path.join(os.path.dirname(out), "layout.audit.json")
        with open(audit, "w", encoding="utf-8") as fh:
            json.dump(lay, fh, ensure_ascii=False, indent=2)
        if warn:
            print("[build] 覆盖体检：%d 个高优先级区被吃掉 —— %s"
                  % (len(warn), ", ".join("%s(pri=%d,%.0f%%)" % (i, p, k * 100) for i, p, k in warn)))
            print("[build]  → 提高 priority / 收紧压住它的大件 rect / 换 seed。明细见 %s" % audit)
        else:
            print("[build] 覆盖体检：高优先级区全部保住（kept>=90%）")
        print("[build] 出图示例：")
        print("  python moss_run.py --port 8190 --t2i --depth-map D.png \\")
        print("    --color-map %s --prompt-file %s --w %d --h %d --steps 24"
              % (meta.get("cm_input") or meta["cm"], out, meta["size"][0], meta["size"][1]))
    return rc


def main():
    ap = argparse.ArgumentParser(description="参考图切区 → 打标 → layout JSON"
                     " ｜ 通道：plan --for ref = 文本（tag）；--for layout = 像素（色图）；"
                     "build 只产出结构，不直接生效")
    sub = ap.add_subparsers(dest="cmd")

    p1 = sub.add_parser("plan",
                      help="切区并生成色图/拼图/待填 JSON"
                      " ｜ 通道：--for ref 只出裁片+tags（文本）；--for layout 出色图（像素）")
    p1.add_argument("--ref", required=True, help="参考图（角色卡/立绘）")
    p1.add_argument("--out", required=True, help="工作目录")
    p1.add_argument("--bands", type=int, default=3, help="横带数量（默认 3）")
    p1.add_argument("--band", action="append", default=[],
                    help="自定义带 a:b（0–1，可重复；给了就忽略 --bands/--split）")
    p1.add_argument("--rect", action="append", default=[],
                    help="部件级框 x0,y0,x1,y1（0–1，可重复；给了就忽略横带）")
    p1.add_argument("--quiet", action="store_true")
    p1.add_argument("--paint", choices=["big-first", "small-first", "asis"],
                   default="big-first",
                   help="spec 画序：小件后画=优先保住（默认 big-first）")
    p1.add_argument("--spec", default="",
                    help="JSON 清单：[{name,rect:[x0,y0,x1,y1],color?,ref_tag?,paint_prompt?}]，"
                         "按顺序绘制、后画的覆盖先画的（允许框相互碰撞）")
    p1.add_argument("--size", default="768x1024", help="画布尺寸 WxH（默认 768x1024）")
    p1.add_argument("--max-items", type=int, default=20,
                    help="物件数硬上限（同件多词按一个算：同 object 名或 aliases，默认 20）")
    p1.add_argument("--x-inset", type=float, default=0.0,
                    help="矩形带左右各收进比例（0–0.45）；--fit 生效时通常不用")
    p1.add_argument("--fit", choices=["none", "bg", "depth"], default="none",
                    help="按人物轮廓逐行收边：bg=抠白底，depth=用深度图，none=纯矩形")
    p1.add_argument("--fit-src", default="", help="抠图用的图（默认=参考图）")
    p1.add_argument("--fit-thresh", type=float, default=0.10,
                    help="bg 为色差阈值(0–1，默认 0.10)；depth 为亮度阈值")
    p1.add_argument("--fit-erode", type=int, default=4,
                    help="轮廓每侧向内收的像素（默认 4）")
    p1.add_argument("--min-area", type=float, default=0.02,
                    help="剔除小于主连通体该比例的碎块（默认 0.02；0=关）")
    p1.add_argument("--split", choices=["auto", "even"], default="auto",
                    help="auto=按体形切（腰/裙摆为界，需 --fit）；even=等分")
    p1.add_argument("--summary", default="", help="整图 summary（全局一句话）")
    p1.add_argument("--masker", choices=["grabcut", "sam-auto", "sam-bp", "sam-box", "sam-point"],
                    default="grabcut",
                    help="抠形算法：grabcut(默认) / sam-auto(SAM 逐级兜底) / sam-bp / sam-box / sam-point")
    p1.add_argument("--sam-ckpt", default="", help="SAM 权重路径（默认 %s）" % SAM_DEFAULT_CKPT)
    p1.add_argument("--sam-device", choices=["auto", "cuda", "cpu"], default="auto",
                    help="SAM 跑在哪：auto=有显存就 cuda、否则 cpu（默认）")
    p1.add_argument("--sam-kind", choices=["auto", "sharp", "flat", "line", "box", "point"], default="auto",
                    help="SAM 提示方式；auto≈bp。sharp=尖端小件、flat=平贴纹样、line=带状")
    p1.add_argument("--lint-strict", action="store_true", dest="lint_strict",
                    help="paint_prompt 自检有不达标项时以退出码 2 结束")
    p1.add_argument("--for", dest="mode", choices=["ref", "layout", "both"], default="both",
                    help="做什么：ref=只出裁片+tags.json（参考侧）；layout=只出色图+layout（生成侧）；both=都出（默认）")
    p1.set_defaults(func=cmd_plan)

    p2 = sub.add_parser("build",
                      help="读填好的 regions.json，导出 layout.json"
                      " ｜ 通道：只产出结构（label/paint_prompt/bbox），本身不改变任何生成结果")
    p2.add_argument("--dir", required=True, help="plan 的工作目录")
    p2.add_argument("--layout", default="", help="输出路径（默认 <dir>/layout.json）")
    p2.add_argument("--quiet", action="store_true")
    p2.add_argument("--sort", choices=["area", "y", "small"], default="area",
                    help="items 顺序：area=按面积降序（默认）；y=按上→下")
    p2.set_defaults(func=cmd_build)

    a = ap.parse_args()
    if not getattr(a, "func", None):
        ap.print_help()
        return 1
    return a.func(a)


if __name__ == "__main__":
    sys.exit(main())
