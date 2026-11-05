"""用深度图切出"轮廓级"分区色图（实验）。

思路：depth 图是逐像素距离，同一个物体表面在 depth 上连续；于是可以用
  ① 深度区间（band）：把某一层深度整体染成一个分区；
  ② 种子连通（seed）：从某一点出发，取"深度相近且连通"的像素 —— 这会把一条肢体、
     一块布从背景里干净地抠出来，边界跟着真实轮廓走，而不是手画的多边形。
输出与出图同尺寸的色图 + 预览 + json 语义表。

用法：
  python mk_depth_colormap.py --depth mossdepth_s4_ice_pose.png --seed 1180,120 --tol 0.06 \
      --out cm_depth.png --size 1152x688 --color 170,90,220 --base 127,179,255
"""
import argparse
import json
import os

import numpy as np
from PIL import Image

try:
    from scipy import ndimage as ndi
    HAVE_SCIPY = True
except Exception:  # noqa: BLE001
    HAVE_SCIPY = False


def load_depth(path):
    im = Image.open(path).convert("L")
    d = np.asarray(im, dtype=np.float32) / 255.0
    return d, im.size  # near = 亮（本工程统一约定）


def region_seed(d, seed, tol):
    """从 seed 出发，取深度差 < tol 的连通域（壳层）。"""
    sx, sy = seed
    d0 = d[sy, sx]
    cand = np.abs(d - d0) <= tol
    if not HAVE_SCIPY:
        return cand
    lab, n = ndi.label(cand)
    if n == 0:
        return cand
    keep = lab[sy, sx]
    return lab == keep


def region_front(d, seed, tol):
    """"该物体之前的一切"：d >= d_seed - tol 的连通域（实心，不是壳）。"""
    sx, sy = seed
    thresh = d[sy, sx] - tol
    cand = d >= thresh
    if not HAVE_SCIPY:
        return cand
    lab, n = ndi.label(cand)
    if n == 0:
        return cand
    return lab == lab[sy, sx]


def region_plateau(d, seed, tol, grad_tol=0.02):
    """"同一块光滑表面"：深度接近 + 梯度小 + 连通到种子；再填洞。"""
    sx, sy = seed
    d0 = d[sy, sx]
    if HAVE_SCIPY:
        gy, gx = np.gradient(d)
        grad = np.hypot(gx, gy)
    else:
        grad = np.zeros_like(d)
    cand = (np.abs(d - d0) <= tol) & (grad <= grad_tol)
    if not HAVE_SCIPY:
        return cand
    lab, n = ndi.label(cand)
    if n == 0:
        return cand
    m = lab == lab[sy, sx]
    return ndi.binary_fill_holes(m)


def region_band(d, lo, hi):
    return (d >= lo) & (d <= hi)


def clean(mask, min_area_frac=0.002):
    if not HAVE_SCIPY:
        return mask
    mask = ndi.binary_closing(mask, np.ones((5, 5), bool))
    mask = ndi.binary_opening(mask, np.ones((3, 3), bool))
    lab, n = ndi.label(mask)
    if n == 0:
        return mask
    sizes = ndi.sum(mask, lab, range(1, n + 1))
    keep = np.zeros_like(mask)
    thr = min_area_frac * mask.size
    for i, s in enumerate(sizes, start=1):
        if s >= thr:
            keep |= (lab == i)
    return keep


def parse_color(txt):
    txt = txt.strip()
    if txt.startswith("#"):
        txt = txt[1:]
        return tuple(int(txt[i:i + 2], 16) for i in (0, 2, 4))
    return tuple(int(v) for v in txt.split(","))


def dither_mask(shape, weight, block):
    """按块随机（确定性哈希）保留 weight 比例，块大小 >= 16px，避免 VAE 8x 下采样把图案平均掉。"""
    H, W = shape
    if weight >= 0.999:
        return np.ones((H, W), bool)
    if weight <= 0.001:
        return np.zeros((H, W), bool)
    nb_y, nb_x = (H + block - 1) // block, (W + block - 1) // block
    yy, xx = np.mgrid[0:nb_y, 0:nb_x]
    h = (xx * 73856093) ^ (yy * 19349663) ^ 0x9E3779B9
    h = (h ^ (h >> 13)) * 1274126177
    rnd = ((h ^ (h >> 16)) & 0xFFFFFFFF) / float(0xFFFFFFFF)
    keep_blocks = rnd < weight
    keep = np.repeat(np.repeat(keep_blocks, block, axis=0), block, axis=1)[:H, :W]
    return keep


def build_multi(d, regions, base, W, H, dither_block=16):
    """regions: [(x, y, mode, tol, color)]，按顺序叠加（后者覆盖前者）。"""
    out = np.zeros((H, W, 3), np.uint8)
    out[:, :] = base
    log = []
    for reg in regions:
        x, y, mode, tol, col = reg[:5]
        weight = reg[5] if len(reg) > 5 else 1.0
        if mode == "front":
            m = region_front(d, (x, y), tol)
        elif mode == "plateau":
            m = region_plateau(d, (x, y), tol)
        elif mode == "seed":
            m = region_seed(d, (x, y), tol)
        else:  # band 用 tol 当 lo
            m = region_band(d, tol, 1.01)
        m = clean(m)
        m_img = Image.fromarray((m * 255).astype(np.uint8)).resize((W, H), Image.NEAREST)
        mm = np.asarray(m_img) > 127
        if weight < 0.999:
            mm = mm & dither_mask((H, W), weight, dither_block)
        out[mm] = col
        log.append({"seed": [x, y], "mode": mode, "tol": tol, "color": list(col), "weight": weight,
                    "depth": round(float(d[min(y, d.shape[0] - 1), min(x, d.shape[1] - 1)]), 3),
                    "area_pct": round(float(mm.mean() * 100), 2)})
        print(f"[region] ({x},{y}) {mode} tol={tol} -> {mm.mean()*100:.1f}%  color={col}")
    return out, log


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--depth", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--preview", default="")
    ap.add_argument("--size", default="1152x688", help="输出尺寸 WxH，应与出图一致")
    ap.add_argument("--mode", default="plateau", choices=["seed", "band", "front", "plateau"])
    ap.add_argument("--seed", default="1180,120", help="种子像素 x,y（原图坐标）")
    ap.add_argument("--tol", type=float, default=0.06, help="深度相似容差（0–1）")
    ap.add_argument("--lo", type=float, default=0.80)
    ap.add_argument("--hi", type=float, default=1.01)
    ap.add_argument("--color", default="170,90,220")
    ap.add_argument("--base", default="127,179,255")
    ap.add_argument("--grad-tol", type=float, default=0.02, help="plateau 模式下的梯度上限")
    ap.add_argument("--dilate", type=int, default=0, help="区域外扩像素（补边用）")
    ap.add_argument("--dither-block", type=int, default=16, help="分区权重<1 时的棋盘块大小（像素），建议 ≥16（VAE 8 倍下采样会平均掉小块）")
    ap.add_argument("--region", action="append", default=[],
                    help="多分区：x,y,mode,tol,color —— 可重复；mode=front|seed|plateau|band，"
                         "color 写 R,G,B 或 #rrggbb。按出现顺序叠加，后者覆盖前者")
    args = ap.parse_args()

    d, (w0, h0) = load_depth(args.depth)
    print(f"[depth] {os.path.basename(args.depth)} {w0}x{h0}  range {d.min():.3f}..{d.max():.3f}")

    W, H = [int(v) for v in args.size.lower().split("x")]
    base = parse_color(args.base)

    if args.region:
        regions = []
        for spec in args.region:
            parts = spec.split(",")
            x, y, mode, tol, col = parts[:5]
            w = float(parts[5]) if len(parts) > 5 else 1.0
            regions.append((int(x), int(y), mode.strip(), float(tol), parse_color(col), w))
        out, log = build_multi(d, regions, base, W, H, args.dither_block)
        Image.fromarray(out).save(args.out)
        if args.preview:
            Image.fromarray(out).resize((W // 2, H // 2), Image.NEAREST).save(args.preview)
        json.dump({"depth": os.path.basename(args.depth), "base": list(base), "regions": log},
                  open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
        print(f"[out] {args.out}")
        return

    if args.mode == "seed":
        sx, sy = [int(v) for v in args.seed.split(",")]
        sx = max(0, min(w0 - 1, sx))
        sy = max(0, min(h0 - 1, sy))
        mask = region_seed(d, (sx, sy), args.tol)
        print(f"[seed] ({sx},{sy}) depth={d[sy, sx]:.3f} tol={args.tol} -> {mask.mean()*100:.1f}% raw")
    elif args.mode == "front":
        sx, sy = [int(v) for v in args.seed.split(",")]
        sx = max(0, min(w0 - 1, sx))
        sy = max(0, min(h0 - 1, sy))
        mask = region_front(d, (sx, sy), args.tol)
        print(f"[front] ({sx},{sy}) depth={d[sy, sx]:.3f} thresh={d[sy, sx]-args.tol:.3f} -> {mask.mean()*100:.1f}% raw")
    elif args.mode == "plateau":
        sx, sy = [int(v) for v in args.seed.split(",")]
        sx = max(0, min(w0 - 1, sx))
        sy = max(0, min(h0 - 1, sy))
        mask = region_plateau(d, (sx, sy), args.tol, args.grad_tol)
        print(f"[plateau] ({sx},{sy}) depth={d[sy, sx]:.3f} tol={args.tol} grad<={args.grad_tol} -> {mask.mean()*100:.1f}% raw")
    else:
        mask = region_band(d, args.lo, args.hi)
        print(f"[band] {args.lo:.2f}..{args.hi:.2f} -> {mask.mean()*100:.1f}% raw")

    mask = clean(mask)
    if args.dilate > 0:
        mask = ndi.binary_dilation(mask, np.ones((args.dilate * 2 + 1,) * 2, bool))

    m_img = Image.fromarray((mask * 255).astype(np.uint8)).resize((W, H), Image.NEAREST)
    m = np.asarray(m_img) > 127

    base = tuple(int(v) for v in args.base.split(","))
    col = tuple(int(v) for v in args.color.split(","))
    out = np.zeros((H, W, 3), np.uint8)
    out[:, :] = base
    out[m] = col

    Image.fromarray(out).save(args.out)
    if args.preview:
        Image.fromarray(out).resize((W // 2, H // 2), Image.NEAREST).save(args.preview)
    print(f"[out] {args.out}  区域占比 {m.mean()*100:.1f}%")

    meta = {
        "mode": args.mode,
        "depth": os.path.basename(args.depth),
        "seed": args.seed if args.mode == "seed" else None,
        "tol": args.tol if args.mode == "seed" else None,
        "band": [args.lo, args.hi] if args.mode == "band" else None,
        "area_pct": round(float(m.mean() * 100), 2),
        "colors": {"base": list(base), "region": list(col)},
    }
    json.dump(meta, open(os.path.splitext(args.out)[0] + ".json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
