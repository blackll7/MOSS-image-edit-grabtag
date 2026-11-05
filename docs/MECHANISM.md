# How GrabTag actually influences a model

> **Channel**: both — words describe *what*, colour maps describe *where*.

A one-page channel map. Read this before changing anything: most confusion comes
from mixing up the two channels below.

> **Field names (2026-11-06)**: the reference-side label is `ref_tag` (was `tag`),
> the generation-side instruction is `paint_prompt` (was `caption`), and the global
> line is `summary.text` (was `summary.caption`). The code reads the old names and
> writes only the new ones, so old working directories keep loading.


## Two channels, never interchangeable

| | text channel (prompt) | pixel channel (latent conditioning) |
|---|---|---|
| carrier | an English sentence | a PNG the same size as the canvas |
| produced by | your prompt, `--region-prompt`, `--layout` caption lines | `plan`'s `cm.png`, `mk_colormap.py`, `mk_depth_colormap.py`, `--layout`'s generated map, region masks |
| what it can say | material, colour, cut, trim, *and* where it sits ("on the chest") | which pixel belongs to which region ID |
| what it cannot say | exact boundaries | any meaning ("this is lace" needs words) |
| typical failure | caption without a landmark -> element drifts elsewhere | flat colours merged -> two regions become one item |

**Rule of thumb**

- want *finer detail* -> edit **words**;
- want *this thing at this place* -> use a **colour map / bbox**;
- GrabTag at full strength uses both: pixels decide **where**, words decide **what**.

## What each command actually changes

### `plan --for ref` (reference side, text only)

```
reference image
  -> crops/r1.png ..        (for you / your VLM to look at)
  -> montage.png            (numbered, one glance)
  -> tags.json              [{name, tag, crop, color, area}]
  -> regions.json           same + caption
```

No colour map is produced. Nothing here reaches the model on its own: a tag becomes
effective only when something *writes it into a prompt*.

### `plan --for layout` / `colormap` / `depth-colormap` (pixel side only)

Writes `cm.png`, a region-ID image the size of your canvas. Colours carry no meaning,
they only carry IDs; and the downstream exporter **merges colours closer than ~40**,
so two similar colours collapse into one item. Give it words ("colour key") or the
model knows the region exists but not what it is.

### `build` -> `layout.json`

`[{label, paint_prompt, bbox}]`, with `bbox` as **yxyx pixels, origin at canvas centre**
(e.g. `[-52,-154,133,153]`). Audit data (`kept`, `loss_hi`, warnings) goes to
`layout.audit.json` — never into `layout.json`, which downstream code may feed to a
text encoder verbatim.

## Symptom -> cause

| symptom | wrong reading | actual cause |
|---|---|---|
| ornament appears on the head instead of the chest | "the coordinates are off" | the caption has no landmark; bbox only fixes *where a region is*, not *what may move* |
| three people side by side | "the layout made three subjects" | the colour bands spanned the full canvas width; inset them so the bbox hugs the figure |
| two garments fused into one | "the model ignores me" | the exporter merged two near-identical colours |
| editing the tag text changed nothing | "tags are broken" | nothing reads `tags.json`; a prompt builder has to do it |
| the reference was ignored entirely | "reference conditioning is unsupported" | the reference was dropped by that code path, or too many references were attached |

## Verify without generating

```bash
# 1) all artefacts present
ls build/demo/{cm.png,montage.png,regions.json,tags.json,layout.json,layout.audit.json}

# 2) layout.json holds exactly three keys per item
python -c "import json;d=json.load(open('build/demo/layout.json'));print(len(d['items']),[sorted(i) for i in d['items']][:2])"

# 3) number of distinct colours == number of items
python -c "from PIL import Image;im=Image.open('build/demo/cm.png').convert('RGB');print(len({p for p in im.getdata() if p!=(255,255,255)}))"
```

Then look at `montage.png`: if you cannot name a crop at a glance, the split is wrong
(change the band cuts or use a spec), not the caption.

## Do not

1. Feed `tags.json` to a prompt-file option — the JSON text becomes the prompt.
2. Put audit fields into `layout.json`.
3. Pass absolute paths as colour maps when the consumer is ComfyUI (it only accepts
   file names inside its own `input/`).
4. Attach four or more reference images and expect stability.
5. Assume a colour map adds knowledge; it only adds spatial constraints.
