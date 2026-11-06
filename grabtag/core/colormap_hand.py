"""手画多边形色图：一张参考图 + 若干多边形 -> 区域 ID 色图。

用法::

    grabtag hand-colormap --ref card.png --out cm.png ^
        --region "hair:0.30,0.05;0.70,0.05;0.72,0.30;0.28,0.30:#7fb3ff"
"""
import argparse
import sys

from PIL import Image, ImageDraw


def parse_region(spec):
    name, pts, color = spec.rsplit(":", 2)
    xy = []
    for pair in pts.split(";"):
        x, y = pair.split(",")
        xy.append((float(x), float(y)))
    color = color.strip()
    if not color.startswith("#") or len(color) != 7:
        raise SystemExit("颜色要写成 #RRGGBB：%r" % color)
    return name.strip(), xy, color


def main():
    ap = argparse.ArgumentParser(
        prog="grabtag hand-colormap",
        description="用手画多边形生成区域 ID 色图（区域 ID 图，不是材质图）")
    ap.add_argument("--ref", required=True, help="参考图（决定输出尺寸）")
    ap.add_argument("--out", required=True, help="输出色图路径")
    ap.add_argument("--region", action="append", required=True, metavar="NAME:PTS:COLOR",
                    help='"hair:0.3,0.05;0.7,0.05;0.7,0.3:#7fb3ff"')
    ap.add_argument("--base", default="#ffffff", help="底色（纯白 = 不参与任何区）")
    ap.add_argument("--preview", default="", help="可选：半透明预览图")
    a = ap.parse_args()

    im = Image.open(a.ref)
    W, H = im.size
    cm = Image.new("RGB", (W, H), a.base)
    dd = ImageDraw.Draw(cm)

    regions = [parse_region(r) for r in a.region]
    for name, pts, color in regions:
        px = [(int(round(x * W)), int(round(y * H))) for x, y in pts]
        if len(px) < 3:
            raise SystemExit("区域 %s 少于 3 个点" % name)
        dd.polygon(px, fill=color)
    cm.save(a.out)
    print("[hand] %d 区 -> %s (%dx%d)" % (len(regions), a.out, W, H))

    if a.preview:
        base = im.convert("RGB").resize((W, H))
        mask = cm.point(lambda v: 110 if v else 0).convert("L")
        base.paste(cm, (0, 0), mask)
        base.save(a.preview)
        print("[hand] preview -> %s" % a.preview)

    for name, _, color in regions:
        print("  %-16s %s" % (name, color))
    return 0


if __name__ == "__main__":
    sys.exit(main())
