# Examples

* ``make_demo.py`` — draws ``demo.png``, a copyright-free stand-in reference.
* ``demo_spec.json`` — a four-region spec (hair / bodice / skirt / boots).

```bash
python examples/make_demo.py examples/demo.png
python grabtag.py plan --ref examples/demo.png --spec examples/demo_spec.json --out build/demo
#   -> build/demo/{cm.png, montage.png, regions.json, crops/*.png}
python grabtag.py build --dir build/demo
#   -> build/demo/{layout.json, layout.audit.json}
```

Inspect ``build/demo/cm.png``: the colour blocks must sit on the parts they
describe. If they do not, tighten the ``rect`` values (they are fractions of the
image size) and re-run ``plan``.
