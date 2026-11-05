"""GrabTag — region-level tagging for reference-image conditioned diffusion.

Two independent tools live here:

* **GrabTag**  (``plan`` / ``build``): cut a reference image into object regions,
  give each region a caption, and emit a ``layout.json`` that a layout-aware
  image model can follow.
* **Colour-map tools** (``colormap`` / ``depth-colormap`` / ``chroma``): paint a
  region-ID colour map (or key a green screen) that a region-aware diffusion
  pipeline can consume.
"""

__version__ = "0.1.0"
