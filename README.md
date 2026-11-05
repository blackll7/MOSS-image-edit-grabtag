# GrabTag

**Region-level tagging for reference-image conditioned image models.**

Most reference-image workflows hand the model one picture and hope it keeps the
details. GrabTag takes the opposite route: it **cuts the reference into named
object regions, writes one caption per region, and emits a `layout.json`** —
so the model is told *this caption belongs to that box*, not *somewhere in this
picture there is a lotus*.

The repository also ships the **colour-map tooling** that goes with it: paint a
region-ID colour map (bands, boxes, depth slices) or key a green screen, and
feed it to a region-aware pipeline.

No model weights, no artwork, no framework lock-in: plain Python + Pillow, with
OpenCV / SAM optional.

---

## Quick start

```bash
pip install -r requirements.txt          # numpy + Pillow (+ opencv/scipy for masks)

python examples/make_demo.py examples/demo.png

# 1) cut the reference into regions
python grabtag.py plan --ref examples/demo.png \
    --spec examples/demo_spec.json --out build/demo

# 2) look at build/demo/cm.png and build/demo/montage.png, then edit the
#    captions inside build/demo/regions.json
# 3) export the layout
python grabtag.py build --dir build/demo

# -> build/demo/layout.json        (label / caption / bbox)
# -> build/demo/layout.audit.json  (adds coverage audit per region)
```

`layout.json` looks like:

```json
{"items": [
  {"label": "object",
   "caption": "a pale blue sleeveless bodice with a soft sheen, sitting at the waist",
   "bbox": [-52, -154, 133, 154]},
  ...
]}
```

`bbox` is **`yxyx` in pixels, shifted so that the canvas centre is `(0, 0)`**
(`y0, x0, y1, x1`). That form is what MOSS-Image-style layout encoders take.
Convert once at the edge if your model wants `xyxy`, normalised floats or
top-left pixels — the maths lives in `grabtag/core/layout.py::pixel_bbox`.

---


> **Read before hacking:** [`docs/MECHANISM.md`](docs/MECHANISM.md) — one page on how
> region tags (text channel) and colour maps (pixel channel) actually reach the model,
> plus the six symptoms that come from mixing them up.

## What `plan` does

`plan` has four ways to define regions:

| Flag | Meaning |
|---|---|
| `--bands N` | slice the figure into N horizontal bands (fastest baseline) |
| `--band a:b --band c:d ...` | explicit fractional bands |
| `--rect name=x0,y0,x1,y1 ...` | free boxes |
| `--spec FILE` | a JSON list of `{name, rect, color?, tag?, caption?, seed?, priority?, object?, aliases?, requires?, no_clamp?, sam?}` |

Optional mask refinement, per region:

* `--masker grabcut` — OpenCV GrabCut seeded by `seed` (default; needs opencv)
* `--masker sam-auto` — SAM + box/point prompts, falling back to GrabCut
  (needs `torch`, `segment-anything`, and `GRABTAG_SAM_CKPT`)
* `--fit bg --fit-thresh 0.06` — restrict painting to the figure silhouette

`plan` prints a **coverage audit** (`kept` = how much of a region survived
painting, `loss_hi` = how much of the loss was caused by regions with equal or
higher priority) and refuses duplicate colours, because the layout exporter
merges regions by colour.

## What `build` does

Reads `regions.json`, sorts items (`--sort small|area|y`), writes
`layout.json` plus `layout.audit.json`. Regions whose caption is still empty are
reported — a caption is what the model actually reads.

---

## Colour-map tools

```bash
# horizontal bands driven by a depth map
python grabtag.py colormap --depth pose_depth.png --bands 4 --out cm.png

# depth-sliced regions (front / middle / plateau)
python grabtag.py depth-colormap --depth pose_depth.png --region front --out cm.png

# green-screen keying -> RGBA + checker preview
python grabtag.py chroma shot.jpeg --rotate 90 --metric green --out rgba.png
```

The colour map is **not** a picture: every distinct colour is a region ID, and
the prompt has to say what that colour means (e.g. `the #7fb3ff area is a
faceted ice shell`). Paint order is `(priority, -area)`, so small props survive
large garments.

---

## Practical notes (learned the hard way)

1. **One region = one object.** Leaving a garment without its own region makes
   the model invent it.
2. **Colours must be unique.** Two regions with the same RGB become one item.
3. **Captions need a landmark.** `a lotus ornament` may land in the hair;
   `a lotus ornament printed flat on the chest, between the collarbones` does not.
4. **~12 regions is the sweet spot** for a full-body figure; past ~16 small props
   start disappearing.
5. **Narrow boxes do not split a person into several people; full-width boxes do.**
   Keep `--x-inset` around `0.08–0.12` for band mode.
6. **The colour map carries geometry, the caption carries material.** Changing a
   caption never changes a silhouette — repaint the map for that.

## Environment variables

| Var | Used by | Meaning |
|---|---|---|
| `GRABTAG_SAM_CKPT` | `--masker sam-*` | path to `sam_vit_b_*.pth` |
| `GRABTAG_INPUT_DIR` | `plan` (colour-map sync) | where the downstream loader expects images; defaults to `./ComfyUI/input` and silently skips if absent |

## Licence

MIT — see `LICENSE`. Third-party notes in `NOTICE.md`.
![regions](docs/images/demo-cm.png)

*A colour map produced by plan on the demo image: every flat colour is one
region ID. montage.png shows the same regions as numbered crops, ready to be
captioned one by one.*


