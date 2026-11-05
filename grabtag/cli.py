"""One command surface for all GrabTag tools."""
from __future__ import annotations

import sys
from importlib import import_module

USAGE = """grabtag - region tagging and region-ID colour maps for diffusion models

Channel reminder: `plan --for ref` and every caption write into the TEXT channel
(a prompt); `plan --for layout`, `colormap`, `depth-colormap` and `chroma` write into
the PIXEL channel (a region-ID image). `build` only exports structure. Mixing the two
up is the single most common mistake - see docs/MECHANISM.md.

  grabtag plan    --ref IMAGE [--bands N | --spec FILE | --rect ...] --out DIR
                  cut the reference into regions, write crops / cm.png / regions.json
  grabtag build   --dir DIR [--sort small|area|y]
                  read regions.json (captions filled in) and write layout.json
  grabtag layout  --colormap CM.png [--out layout.json]
                  colour map -> layout.json without the GrabTag wrapper
  grabtag colormap --depth D.png --bands N --out CM.png
                  band/depth driven colour map
  grabtag depth-colormap --depth D.png --region front --out CM.png
                  depth-sliced colour map
  grabtag chroma  INPUT [--out RGBA.png] [--metric green|yellow]
                  green-screen keyer (writes RGBA + checker preview)

Every subcommand forwards the remaining flags to its own ``--help``.
"""

_ROUTES = {
    "layout": "layout",
    "colormap": "colormap",
    "chroma": "chroma",
    "depth-colormap": "depth_colormap",
    "depthmap": "depth_colormap",
}


def _run(module: str, argv: list[str]) -> int:
    mod = import_module("grabtag.core." + module)
    old = sys.argv
    sys.argv = [old[0]] + argv
    try:
        out = mod.main()
        return 0 if out is None else int(out)
    finally:
        sys.argv = old


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    if cmd in ("plan", "build"):
        return _run("region_caption", [cmd] + rest)
    if cmd in _ROUTES:
        return _run(_ROUTES[cmd], rest)
    print("unknown command: %s\n" % cmd)
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
