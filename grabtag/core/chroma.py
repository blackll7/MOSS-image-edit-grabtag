"""绿幕抠像：移植 dsh-game-material-master 的 chroma.js 思路。

两件事分开做：
  1) 空间背景掩码 = 四边采样建调色板（16^3 分箱 + 单链聚类丢小簇）
     + 洪水填充双约束（全局在调色板里 + 局部与邻居色差 <= local）
  2) 颜色 alpha = greenness = G - max(R,B)，keyLow/keyHigh 之间线性，
     背景掩码处直接置 0；再按 despill 压掉溢出的绿。

用法：
  python moss_chroma.py <图片> [--out out.png] [--preview p.png]
                        [--local 12] [--global 40] [--low 14] [--high 80]
                        [--despill 0.65] [--rotate 90|270|0]
"""
import argparse
from collections import deque, defaultdict

import numpy as np
from PIL import Image

PALETTE_GRID = 16
PALETTE_CELL = 256 // PALETTE_GRID
CLUSTER_STEP = 2 * PALETTE_CELL          # 32：格心距离 <= 2 格算同一片背景


def _cell_of(px):
    return (int(px[0]) // PALETTE_CELL, int(px[1]) // PALETTE_CELL, int(px[2]) // PALETTE_CELL)


def build_background_palette(rgb, border=3):
    """四边框上的像素 → 分箱计数 → 单链聚类（BFS 连 2 格内相邻箱）→ 丢小簇。"""
    h, w = rgb.shape[:2]
    parts = [rgb[:border].reshape(-1, 3), rgb[-border:].reshape(-1, 3),
             rgb[:, :border].reshape(-1, 3), rgb[:, -border:].reshape(-1, 3)]
    samples = np.concatenate(parts, axis=0)

    counts = defaultdict(int)
    for px in samples[::max(1, len(samples) // 20000)]:
        counts[_cell_of(px)] += 1
    total = sum(counts.values())

    kept = {}
    seen = set()
    for start in counts:
        if start in seen:
            continue
        queue = deque([start])
        seen.add(start)
        cluster, size = [], 0
        while queue:
            cell = queue.popleft()
            cluster.append(cell)
            size += counts.get(cell, 0)
            for dr in (-2, -1, 0, 1, 2):
                for dg in (-2, -1, 0, 1, 2):
                    for db in (-2, -1, 0, 1, 2):
                        nb = (cell[0] + dr, cell[1] + dg, cell[2] + db)
                        if nb in seen or not (0 <= nb[0] < PALETTE_GRID and
                                              0 <= nb[1] < PALETTE_GRID and
                                              0 <= nb[2] < PALETTE_GRID):
                            continue
                        if nb in counts:
                            seen.add(nb)
                            queue.append(nb)
        if size / total >= 0.02:            # 只保留占比 >=2% 的簇
            for cell in cluster:
                kept[cell] = size
    return set(kept), len(samples), total


def palette_has(palette, px, tol_bins=0):
    c = _cell_of(px)
    if c in palette:
        return True
    if tol_bins:
        for dr in range(-tol_bins, tol_bins + 1):
            for dg in range(-tol_bins, tol_bins + 1):
                for db in range(-tol_bins, tol_bins + 1):
                    if (c[0] + dr, c[1] + dg, c[2] + db) in palette:
                        return True
    return False


def segment_background(rgb, palette, local=12, tol_bins=0):
    """四边种子 + 双约束洪水填充。返回 bool 掩码（True=背景）。"""
    h, w = rgb.shape[:2]
    bg = np.zeros((h, w), bool)
    local_sq = local * local
    stack = []

    def push(y, x):
        if bg[y, x]:
            return
        if not palette_has(palette, rgb[y, x], tol_bins):
            return
        bg[y, x] = True
        stack.append((y, x))

    for x in range(w):
        push(0, x)
        push(h - 1, x)
    for y in range(h):
        push(y, 0)
        push(y, w - 1)

    while stack:
        y, x = stack.pop()
        cur = rgb[y, x].astype(np.int32)
        for dy, dx in ((0, -1), (0, 1), (-1, 0), (1, 0)):
            ny, nx = y + dy, x + dx
            if not (0 <= ny < h and 0 <= nx < w) or bg[ny, nx]:
                continue
            px = rgb[ny, nx]
            if not palette_has(palette, px, tol_bins):
                continue
            d = px.astype(np.int32) - cur
            if int(d @ d) > local_sq:
                continue
            bg[ny, nx] = True
            stack.append((ny, nx))
    return bg



def spill_extend(rgb, bg, gmin=12):
    """把与背景 8 连通、且仍偏绿的溢光/过渡像素并进背景（角色是紫粉色，不会被吃掉）。"""
    h, w = rgb.shape[:2]
    r = rgb[..., 0].astype(np.int16)
    g = rgb[..., 1].astype(np.int16)
    b = rgb[..., 2].astype(np.int16)
    greenish = ((g - np.maximum(r, b)) >= gmin) | ((g - b >= 45) & (r - b >= 25) & (g >= 120))
    out = bg.copy()
    stack = [(y, x) for y, x in zip(*np.where(bg))]
    while stack:
        y, x = stack.pop()
        for dy in (-1, 0, 1):
            for dx in (-1, 0, 1):
                ny, nx = y + dy, x + dx
                if 0 <= ny < h and 0 <= nx < w and not out[ny, nx] and greenish[ny, nx]:
                    out[ny, nx] = True
                    stack.append((ny, nx))
    return out


def erode_alpha(alpha, k=1):
    if k <= 0:
        return alpha
    m = alpha > 8
    for _ in range(k):
        m = m & np.roll(m, 1, 0) & np.roll(m, -1, 0) & np.roll(m, 1, 1) & np.roll(m, -1, 1)
    return np.where(m, alpha, 0.0)


def key_green(rgb, bg, low=14, high=80, despill=0.65):
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    other = np.maximum(r, b)
    greenness = g - other

    alpha = np.ones_like(greenness)
    mid = (greenness > low) & (greenness < high)
    alpha[mid] = 1.0 - (greenness[mid] - low) / max(1, high - low)
    alpha[greenness >= high] = 0.0
    alpha[bg] = 0.0

    out_g = g.copy()
    spill = (despill > 0) & (g > other)
    if spill.any():
        w = despill * (1.0 - alpha)
        out_g = np.where(spill, g - (g - other) * w, g)

    rgba = np.dstack([r, out_g, b, alpha * 255.0]).clip(0, 255).astype(np.uint8)
    return rgba



def key_yellow(rgb, bg, low=45, high=100, despill=0.65):
    """黄绿幕专用：蓝通道缺失量 = min(R,G) - B 才是"幕色"度量。"""
    r = rgb[..., 0].astype(np.float32)
    g = rgb[..., 1].astype(np.float32)
    b = rgb[..., 2].astype(np.float32)
    yellowness = np.minimum(r, g) - b

    alpha = np.ones_like(yellowness)
    mid = (yellowness > low) & (yellowness < high)
    alpha[mid] = 1.0 - (yellowness[mid] - low) / max(1, high - low)
    alpha[yellowness >= high] = 0.0
    alpha[bg] = 0.0

    out_r, out_g = r.copy(), g.copy()
    k = (despill * (1.0 - alpha)).clip(0, 1)
    lift = (np.minimum(r, g) - b) * k
    out_r = np.where(lift > 0, r - lift * 0.5, r)
    out_g = np.where(lift > 0, g - lift * 0.5, g)
    return np.dstack([out_r, out_g, b, alpha * 255.0]).clip(0, 255).astype(np.uint8)


def checker(w, h, size=16):
    a = np.zeros((h, w, 3), np.uint8)
    ys, xs = np.mgrid[0:h, 0:w]
    a[...] = 255
    block = ((ys // size + xs // size) % 2).astype(bool)
    a[block] = 205
    return a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('image')
    ap.add_argument('--out', default='')
    ap.add_argument('--preview', default='')
    ap.add_argument('--local', type=int, default=12)
    ap.add_argument('--global-tol', type=int, default=40, dest='gtol')
    ap.add_argument('--low', type=int, default=14)
    ap.add_argument('--high', type=int, default=80)
    ap.add_argument('--despill', type=float, default=0.65)
    ap.add_argument('--tol-bins', type=int, default=0, dest='tol_bins')
    ap.add_argument('--metric', choices=['green', 'yellow'], default='green')
    ap.add_argument('--spill-extend', type=int, default=12, dest='spill_extend')
    ap.add_argument('--shrink', type=int, default=1)
    ap.add_argument('--rotate', type=int, default=0)
    a = ap.parse_args()

    im = Image.open(a.image).convert('RGB')
    if a.rotate:
        im = im.transpose(Image.ROTATE_270 if a.rotate == 90 else Image.ROTATE_90)
    rgb = np.asarray(im)

    palette, samples, kept = build_background_palette(rgb)
    bg = segment_background(rgb, palette, local=a.local, tol_bins=a.tol_bins)
    frac = bg.mean()
    print('palette bins kept=%d  samples=%d  background=%.1f%%' % (len(palette), samples, frac * 100))
    use_seg = (frac > 0.12 and frac < 0.985)
    print('spatial segmentation %s' % ('USED' if use_seg else 'skipped (fraction out of range)'))
    if not use_seg:
        bg = np.zeros_like(bg)
    else:
        bg = spill_extend(rgb, bg, gmin=a.spill_extend)

    if a.metric == 'yellow':
        rgba = key_yellow(rgb, bg, low=a.low, high=a.high, despill=a.despill)
    else:
        rgba = key_green(rgb, bg, low=a.low, high=a.high, despill=a.despill)
    if a.shrink:
        rgba[..., 3] = erode_alpha(rgba[..., 3].astype(np.float32), a.shrink).astype(np.uint8)
    alpha = rgba[..., 3]
    print('alpha: 0=%d  soft=%d  255=%d' % ((alpha == 0).sum(), ((alpha > 0) & (alpha < 255)).sum(), (alpha == 255).sum()))

    if a.out:
        Image.fromarray(rgba, 'RGBA').save(a.out)
        print('out ->', a.out)
    if a.preview:
        chk = checker(rgba.shape[1], rgba.shape[0])
        af = (alpha.astype(np.float32) / 255)[..., None]
        comp = (rgba[..., :3].astype(np.float32) * af + chk * (1 - af)).clip(0, 255).astype(np.uint8)
        Image.fromarray(comp).save(a.preview)
        print('preview ->', a.preview)


if __name__ == '__main__':
    main()
