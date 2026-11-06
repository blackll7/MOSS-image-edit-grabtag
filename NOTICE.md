# Third-party notes

* ``grabtag/core/chroma.py`` is a Python port of the chroma-keying routine in the
  open-source **dsh-game-material-master** plugin (its ``chroma.js``: four-edge
  palette sampling + double-constrained flood fill + soft alpha + despill).
  The port keeps the same parameter semantics (``low`` / ``high`` / ``despill`` /
  ``local``) so results stay comparable.
* ``grabtag/core/region_caption.py`` uses OpenCV GrabCut and, optionally,
  Meta's **segment-anything** (SAM) for mask refinement. SAM weights are not
  bundled — point ``GRABTAG_SAM_CKPT`` at your own checkpoint.
* Nothing in this repository ships model weights or third-party artwork. The
  demo image is drawn from scratch by ``examples/make_demo.py``.
* ``docs/images/kawaku-reference.png`` is a sample illustration supplied by the
  author for documentation; ``docs/images/kawaku-colormap.png`` is produced from
  it by ``grabtag plan``. Neither is required to run the tools.
