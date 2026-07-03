#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = ["pillow>=10", "httpx>=0.27"]
# ///
"""Annotate one wide photo of a shelf bank into one boxed/labeled image per
shelf — the "this is Shelf 2" wayfinding photos that get uploaded as each
Homebox location's primary image.

Two ways to say where the shelves are:

  Auto-band  (the common "N shelves stacked / in a row" case — no coordinates):
    ./annotate_location_photos.py shelves.jpg --rows 3 --label Shelf
    ./annotate_location_photos.py wall.jpg    --cols 3 --label Cabinet

  Explicit regions (irregular layouts) via a sidecar JSON:
    ./annotate_location_photos.py --spec shelves.json
    # shelves.json:
    # {"source": "shelves.jpg",
    #  "regions": [{"label": "Shelf 2", "box": [x, y, w, h], "location": "Shelf 2"}]}

Each output is a copy of the full photo with a bright box around one shelf and a
label banner. Add --dim to darken everything outside the box. Add --upload to
push each image straight to its Homebox location as the primary photo (band mode
uses the label as the location name; region mode uses each region's "location").

Homebox config for --upload is read from env (HOMEBOX_URL / HOMEBOX_TOKEN) or the
sibling homebox-mcp/.env, exactly like server.py.
"""
from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

DEFAULT_COLOR = "#FF2D55"  # high-contrast pink-red
FONT_CANDIDATES = [
    "/usr/share/fonts/dejavu-sans-fonts/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/liberation-sans-fonts/LiberationSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "/usr/share/fonts/google-noto-vf/NotoSans[wght].ttf",
]
# names to glob for if none of the explicit paths exist
FONT_GLOBS = ["LiberationSans-Bold.ttf", "DejaVuSans-Bold.ttf", "NotoSans*.ttf"]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "region"


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    paths = list(FONT_CANDIDATES)
    fonts_root = Path("/usr/share/fonts")
    if fonts_root.exists():
        for pattern in FONT_GLOBS:
            paths.extend(str(p) for p in fonts_root.rglob(pattern))
    for path in paths:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size)
            except OSError:
                pass
    try:                                  # Pillow >= 10.1 scales the bundled font
        return ImageFont.load_default(size)
    except TypeError:
        return ImageFont.load_default()


def _bands(w: int, h: int, rows: int, cols: int) -> list[tuple[int, int, int, int]]:
    """Equal horizontal bands (rows) or vertical bands (cols) as (x, y, w, h)."""
    if rows:
        step = h / rows
        return [(0, round(i * step), w, round((i + 1) * step) - round(i * step))
                for i in range(rows)]
    step = w / cols
    return [(round(i * step), 0, round((i + 1) * step) - round(i * step), h)
            for i in range(cols)]


def annotate(src: Image.Image, box: tuple[int, int, int, int], label: str,
             color: str, width: int, dim: bool) -> Image.Image:
    """Return a copy of `src` with `box` outlined and `label` banner drawn."""
    x, y, bw, bh = box
    x2, y2 = x + bw, y + bh
    img = src.convert("RGB").copy()

    if dim:
        shade = Image.new("RGB", img.size, (0, 0, 0))
        img = Image.blend(img, shade, 0.55)
        img.paste(src.convert("RGB").crop((x, y, x2, y2)), (x, y))

    draw = ImageDraw.Draw(img)
    # clamp the rectangle slightly inside the image so the stroke is visible
    inset = width // 2
    rx, ry = max(inset, x + inset), max(inset, y + inset)
    rx2 = min(img.width - 1 - inset, x2 - inset)
    ry2 = min(img.height - 1 - inset, y2 - inset)
    draw.rectangle([rx, ry, rx2, ry2], outline=color, width=width)

    # label banner pinned to the top-left of the box
    font = _font(max(16, img.height // 22))
    tb = draw.textbbox((0, 0), label, font=font)
    tw, th = tb[2] - tb[0], tb[3] - tb[1]
    pad = max(6, th // 3)
    bx, by = rx, ry
    # if the box hugs the top edge, drop the banner just inside it
    draw.rectangle([bx, by, bx + tw + 2 * pad, by + th + 2 * pad], fill=color)
    draw.text((bx + pad, by + pad - tb[1]), label, fill="white", font=font)
    return img


def _load_env() -> tuple[str, str]:
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    return (os.environ.get("HOMEBOX_URL", "http://localhost:7745").rstrip("/"),
            os.environ.get("HOMEBOX_TOKEN", ""))


def _location_id(api: str, token: str, name: str) -> str | None:
    import httpx
    r = httpx.get(f"{api}/api/v1/entities/tree",
                  headers={"Authorization": token}, timeout=30.0)
    r.raise_for_status()
    found = {"id": None}

    def walk(nodes):
        for n in nodes or []:
            if n.get("name", "").lower() == name.lower() and found["id"] is None:
                found["id"] = n.get("id")
            walk(n.get("children"))

    walk(r.json() or [])
    return found["id"]


def upload(path: Path, location: str, primary: bool = True) -> dict:
    """Upload `path` to the named Homebox location as a photo attachment."""
    import httpx
    api, token = _load_env()
    if not token:
        return {"error": "HOMEBOX_TOKEN not set (env or homebox-mcp/.env)"}
    loc_id = _location_id(api, token, location)
    if not loc_id:
        return {"error": f"no location named '{location}'"}
    ctype = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    files = {"file": (path.name, path.read_bytes(), ctype)}
    data = {"name": path.name, "type": "photo"}
    if primary:
        data["primary"] = "true"
    r = httpx.post(f"{api}/api/v1/entities/{loc_id}/attachments",
                   headers={"Authorization": token},
                   files=files, data=data, timeout=60.0)
    r.raise_for_status()
    return {"ok": True, "location": location, "uploaded": path.name}


def build_regions(args) -> tuple[Path, list[dict]]:
    """Return (source_path, regions) from either --spec or band-mode args."""
    if args.spec:
        spec = json.loads(Path(args.spec).read_text())
        source = Path(args.source or spec["source"])
        if not source.is_absolute():
            source = Path(args.spec).resolve().parent / source
        regions = []
        for r in spec["regions"]:
            regions.append({
                "label": r["label"],
                "box": tuple(r["box"]),
                "location": r.get("location", r["label"]),
            })
        return source, regions

    if not args.source:
        sys.exit("error: SOURCE image is required in band mode")
    if bool(args.rows) == bool(args.cols):
        sys.exit("error: pass exactly one of --rows or --cols (or use --spec)")

    source = Path(args.source)
    with Image.open(source) as im:
        w, h = im.size
    boxes = _bands(w, h, args.rows, args.cols)
    regions = []
    for i, box in enumerate(boxes):
        name = f"{args.label} {args.start + i}".strip()
        regions.append({"label": name, "box": box, "location": name})
    return source, regions


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?", help="wide source photo")
    ap.add_argument("--spec", help="JSON file with explicit regions")
    ap.add_argument("--rows", type=int, default=0, help="split into N horizontal bands")
    ap.add_argument("--cols", type=int, default=0, help="split into N vertical bands")
    ap.add_argument("--label", default="Shelf", help="band-mode label prefix")
    ap.add_argument("--start", type=int, default=1, help="band-mode start index")
    ap.add_argument("--dim", action="store_true", help="darken outside the box")
    ap.add_argument("--color", default=DEFAULT_COLOR, help="box/banner color")
    ap.add_argument("--width", type=int, default=0, help="stroke width (auto if 0)")
    ap.add_argument("--out", default="annotated", help="output directory")
    ap.add_argument("--upload", action="store_true",
                    help="upload each image to its Homebox location as primary photo")
    args = ap.parse_args()

    source, regions = build_regions(args)
    if not source.exists():
        sys.exit(f"error: source not found: {source}")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with Image.open(source) as base:
        base.load()
        width = args.width or max(4, base.width // 250)
        for r in regions:
            img = annotate(base, r["box"], r["label"], args.color, width, args.dim)
            dest = out_dir / f"{_slug(r['label'])}.jpg"
            img.save(dest, quality=90)
            line = f"wrote {dest}"
            if args.upload:
                res = upload(dest, r["location"])
                line += f"  → upload: {res.get('ok') and 'ok' or res.get('error')}"
            print(line)


if __name__ == "__main__":
    main()
