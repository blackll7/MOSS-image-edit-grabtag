# -*- coding: utf-8 -*-
"""mk_colormap.py —— 从深度图自动生成 MOSS 用的「分区色图」（color_map）。

原理：深度图里主体离镜头近、亮；背景暗。脚本自动挑一个阈值，使"单一主体连通域"最大，
再沿 --mode 指定的方向按**等面积**切 N 段，每段填一个纯色 —— 输出就是 MOSSImageControl 的 color_map。

三个切分方向：
  y   —— 按图像行切（默认）。躺着的人：上/下 ≈ 上半身/下半身
  x   —— 按图像列切。站着的人：左/右
  pca —— 沿身体主轴切（姿态最"正确"，但肢体交叉时会歪）

用法：
    python mk_colormap.py --depth mossdepth_pose.png --out cm_auto.png --bands 3
    python mk_colormap.py --depth d.png --out cm.png --mode pca --bands 4 \
        --colors "127,179,255;255,200,64;170,90,220;90,200,120" --preview cm_p.png

注意：颜色只是「区域 ID」，没有语义；语义来自 moss_run.py 的 --region-prompt 颜色键文字。
"""
import argparse
import sys

import numpy as np
from PIL import Image

DEFAULT_COLORS = [
    (127, 179, 255),   # 浅蓝
    (255, 200, 64),    # 金
    (170, 90, 220),    # 紫
    (90, 200, 120),    # 绿
    (255, 120, 90),    # 珊瑚
]


def load_gray(path):
    im = Image.open(path)
    if im.mode in ("RGBA", "LA"):
        bg = Image.new("RGBA", im.size, (255, 255, 255, 255))
        im = Image.alpha_composite(bg, im.convert("RGBA"))
    return np.asarray(im.convert("L"), dtype=np.float32)


def auto_mask(gray, thr=None, max_coverage=0.42, merge_ratio=0.05, verbose=True):
    """自动挑阈值：让"最大连通域"尽可能大，同时总覆盖率不超过 max_coverage。"""
    try:
        from scipy import ndimage
    except Exception:
        ndimage = None

    if thr is not None or ndimage is None:
        t = float(thr if thr is not None else np.percentile(gray, 78.0))
        mask = gray > t
        return mask, t, int(mask.sum())

    best = (None, -1.0, 0)
    for t in range(60, 245, 5):
        m = gray > t
        cov = float(m.mean())
        if cov == 0.0 or cov > max_coverage:
            continue
        lab, n = ndimage.label(m)
        if n == 0:
            continue
        sizes = np.bincount(lab.ravel())[1:]
        largest = int(sizes.max())
        if largest > best[1]:
            best = (t, largest, n)

    t = best[0] if best[0] is not None else float(np.percentile(gray, 78.0))
    m = gray > t
    lab, n = ndimage.label(m)
    if n == 0:
        return m, t, 0
    sizes = np.bincount(lab.ravel())[1:]
    keep = np.zeros(n + 1, dtype=bool)
    keep[1:] = sizes >= max(merge_ratio * sizes.max(), 500)
    mask = keep[lab]
    if verbose:
        print("   auto threshold = %d（保留 %d 个连通域，最大 %d px）"
              % (t, int((sizes >= max(merge_ratio * sizes.max(), 500)).sum()), int(sizes.max())))
    return mask, float(t), int(mask.sum())


def axis_coord(mask, mode):
    h, w = mask.shape
    if mode == "y":
        return np.repeat(np.arange(h, dtype=np.float32)[:, None], w, axis=1)
    if mode == "x":
        return np.repeat(np.arange(w, dtype=np.float32)[None, :], h, axis=0)
    ys, xs = np.nonzero(mask)
    pts = np.stack([xs, ys], axis=1).astype(np.float64)
    c = pts.mean(axis=0)
    p = pts - c
    cov = p.T @ p / max(len(p), 1)
    w_, v = np.linalg.eigh(cov)
    axis = v[:, int(np.argmax(w_))]
    gx = np.repeat(np.arange(w, dtype=np.float32)[None, :], h, axis=0) - c[0]
    gy = np.repeat(np.arange(h, dtype=np.float32)[:, None], w, axis=1) - c[1]
    return gx * axis[0] + gy * axis[1]


def parse_colors(spec):
    if not spec:
        return DEFAULT_COLORS
    out = []
    for chunk in spec.split(";"):
        chunk = chunk.strip()
        if not chunk:
            continue
        r, g, b = (int(x) for x in chunk.split(","))
        out.append((r, g, b))
    return out or DEFAULT_COLORS


def main():
    ap = argparse.ArgumentParser(description="从深度图生成分区色图（MOSS color_map）")
    ap.add_argument("--depth", required=True, help="输入深度图（近亮远暗）")
    ap.add_argument("--out", required=True, help="输出分区色图 PNG")
    ap.add_argument("--mode", default="y", choices=["y", "x", "pca"], help="切分方向（默认 y=按行）")
    ap.add_argument("--bands", type=int, default=3, help="切几段（默认 3）")
    ap.add_argument("--edges", default="", help="自定义分位切点，如 0.35,0.7（给了就忽略 --bands）")
    ap.add_argument("--colors", default="", help="每段颜色，分号分隔，如 '127,179,255;255,200,64'")
    ap.add_argument("--subject", action="store_true",
                    help="只给主体上色（需深度图背景干净）；默认整幅画面按带划分")
    ap.add_argument("--thr", type=float, default=None, help="手动指定主体阈值（0-255）")
    ap.add_argument("--max-coverage", type=float, default=0.42, help="主体最大覆盖率（默认 0.42）")
    ap.add_argument("--preview", default="", help="另存预览图")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    gray = load_gray(args.depth)
    if not args.quiet:
        print("[mk_colormap] %s" % args.depth)
    if args.subject:
        mask, thr, n_pix = auto_mask(gray, args.thr, args.max_coverage, verbose=not args.quiet)
    else:
        mask = np.ones(gray.shape, dtype=bool); thr = float("nan"); n_pix = int(gray.size)

    if n_pix < 200:
        sys.exit("[mk_colormap] 主体像素太少（%d），试 --thr 或 --max-coverage" % n_pix)

    coord = axis_coord(mask, args.mode)
    vals = coord[mask]
    lo, hi = float(vals.min()), float(vals.max())
    if hi - lo < 1e-6:
        sys.exit("[mk_colormap] 坐标退化，无法分段")

    if args.edges:
        edges = [float(x) for x in args.edges.split(",") if x.strip()]
    else:
        n = max(2, args.bands)
        edges = [i / n for i in range(1, n)]
    # 等面积：按掩码内坐标的分位点切
    cuts = [float(np.percentile(vals, 100.0 * e)) for e in sorted(edges)]

    colors = parse_colors(args.colors)
    rgb = np.zeros((gray.shape[0], gray.shape[1], 3), dtype=np.uint8)
    bins = [-np.inf] + cuts + [np.inf]
    stats = []
    for i in range(len(bins) - 1):
        sel = mask & (coord >= bins[i]) & (coord < bins[i + 1])
        cnt = int(sel.sum())
        stats.append(cnt)
        if cnt:
            rgb[sel] = colors[i % len(colors)]

    Image.fromarray(rgb).save(args.out)
    if args.preview:
        prev = (rgb.astype(np.float32) * 0.85 + 20).clip(0, 255).astype(np.uint8)
        Image.fromarray(prev).save(args.preview)

    if not args.quiet:
        print("   mode=%s  thr=%s  pixels=%d  %d bands -> %s"
              % (args.mode, ("-" if not args.subject else "%.0f" % thr), n_pix, len(stats), args.out))
        for i, cnt in enumerate(stats):
            print("   band %d  %5.1f%%  rgb%s" % (i + 1, 100.0 * cnt / max(n_pix, 1), colors[i % len(colors)]))


if __name__ == "__main__":
    main()
