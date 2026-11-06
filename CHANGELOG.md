# Changelog

## 0.1.2 — 2026-11-06

- **`autoregion`** — GroundingDINO-tiny → SAM2.1-tiny: text prompts in, per-part masks out
  (`mask_XX.png` + `manifest.json` + `overlay.png`). Same-class duplicate removal on by
  default (`--keep-duplicates` to disable); optional `--summary` is reused as the style line.
- **`plan --mask-dir`** — consume an external mask set (autoregion output or hand-made) instead
  of seeds/GrabCut; masks are painted largest-first so small ornaments survive.
- **`hand-colormap`** — declares regions as polygons (`NAME:PTS:COLOR`), no hard-coded paths.
- `layout.py` keeps regions down to 0.05% of the canvas (was 0.2%), so 1-px-scale accessories
  (hairpins, clasps) are no longer dropped.
- `moss_layout`/`plan` accept the new `manifest.json` dict shape **and** the old list shape.
- Wording rule baked into autoregion: small ornaments are described **positively**
  (`just a golden feather-fan hairpin of plain metal`) — negative phrasing
  (`not a flower`) makes the model draw the flower.
- Repo images resized (671 KB → 636 KB); palette colour maps are never rescaled.

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
