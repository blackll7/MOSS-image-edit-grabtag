# Changelog

## 0.1.0 — 2026-11-06

First public cut: the two toolsets that were developed together.

**GrabTag**
- `plan`: bands / explicit bands / free rects / JSON spec → `crops/`, `montage.png`,
  `cm.png`, `regions.json`, `tags.json`
- `build`: `regions.json` → `layout.json` + `layout.audit.json`
- per-region mask refinement: GrabCut (default) and optional SAM prompts
  (`sharp` / `flat` / `line` / box / point) with automatic GrabCut fallback
- coverage audit: `kept` and `loss_hi` per region, priority-aware
- duplicate-colour guard (the exporter merges regions by colour)

**Colour maps**
- `colormap`: band / PCA / edge driven region-ID maps
- `depth-colormap`: depth-sliced regions (front / middle / plateau), dithering
- `chroma`: green-screen keyer (four-edge palette + double-constrained flood
  fill + soft alpha + despill), port of the chroma.js routine

**Packaging**
- `pip install -e .`, console script `grabtag`, `GRABTAG_SAM_CKPT` /
  `GRABTAG_INPUT_DIR` overrides, CI demo on Python 3.9 / 3.11 / 3.12
