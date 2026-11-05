"""Draw a copyright-free stand-in reference so the tools can be smoke-tested.

    python examples/make_demo.py examples/demo.png
"""
import sys

from PIL import Image, ImageDraw

W, H = 768, 1024


def main(out="examples/demo.png"):
    im = Image.new("RGB", (W, H), (246, 244, 240))
    d = ImageDraw.Draw(im)

    # hair behind the head
    d.ellipse([(W // 2 - 96, 96), (W // 2 + 96, 300)], fill=(58, 48, 44))
    # head
    d.ellipse([(W // 2 - 62, 120), (W // 2 + 62, 268)], fill=(236, 206, 188))
    # neck + shoulders
    d.rectangle([(W // 2 - 18, 250), (W // 2 + 18, 296)], fill=(228, 196, 178))
    # bodice
    d.polygon([(W // 2 - 118, 300), (W // 2 + 118, 300),
               (W // 2 + 92, 470), (W // 2 - 92, 470)], fill=(176, 206, 232))
    # skirt
    d.polygon([(W // 2 - 96, 466), (W // 2 + 96, 466),
               (W // 2 + 150, 640), (W // 2 - 150, 640)], fill=(150, 186, 220))
    # legs
    d.rectangle([(W // 2 - 62, 636), (W // 2 - 12, 880)], fill=(236, 206, 188))
    d.rectangle([(W // 2 + 12, 636), (W // 2 + 62, 880)], fill=(236, 206, 188))
    # boots
    d.rectangle([(W // 2 - 66, 872), (W // 2 - 8, 952)], fill=(52, 58, 72))
    d.rectangle([(W // 2 + 8, 872), (W // 2 + 66, 952)], fill=(52, 58, 72))

    im.save(out)
    print("wrote", out, im.size)


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "examples/demo.png")
