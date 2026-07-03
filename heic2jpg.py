#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "pillow-heif>=0.16"]
# ///
"""Convert HEIC/HEIF images to JPEG.

Apple HEIC doesn't render in browsers, Homebox, or Claude Code's image reader,
so the bulk intake pipeline converts HEIC frames to JPEG before feeding them to
vision (and for ad-hoc conversion). pillow-heif bundles libheif, so there's no
system package to install — just run it.

Usage:
  ./heic2jpg.py FILE...                  # writes FILE.jpg next to each input
  ./heic2jpg.py --outdir DIR FILE...     # writes JPEGs into DIR instead
  ./heic2jpg.py --quality 85 FILE...     # JPEG quality (default 90)

Prints the path of each JPEG written. Non-HEIC inputs are converted too (any
format Pillow can open), so it doubles as a generic "→ jpg" helper.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pillow_heif
from PIL import Image


def main() -> int:
    ap = argparse.ArgumentParser(description="Convert HEIC/HEIF (or any image) to JPEG.")
    ap.add_argument("files", nargs="+", help="input image files")
    ap.add_argument("--outdir", help="write JPEGs here (default: next to each input)")
    ap.add_argument("--quality", type=int, default=90, help="JPEG quality (default 90)")
    args = ap.parse_args()

    pillow_heif.register_heif_opener()
    outdir = Path(args.outdir).expanduser() if args.outdir else None
    if outdir:
        outdir.mkdir(parents=True, exist_ok=True)

    rc = 0
    for f in args.files:
        p = Path(f).expanduser()
        try:
            img = Image.open(p).convert("RGB")
        except Exception as e:  # noqa: BLE001
            print(f"skip {p}: {e}", file=sys.stderr)
            rc = 1
            continue
        dest = (outdir or p.parent) / (p.stem + ".jpg")
        img.save(dest, "JPEG", quality=args.quality)
        print(dest)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
