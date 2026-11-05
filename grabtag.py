#!/usr/bin/env python
"""Shim so the tools run straight from a checkout: ``python grabtag.py plan ...``."""
from grabtag.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
