#!/usr/bin/env python3
"""Build the installer/desktop-shortcut icon from the generated brand PNG.

Reads ``assets/vpn_icon.png`` (produced by the image generator), makes the flat
outer background transparent, and writes a multi-size ``packaging/shortcut.ico``
(PNG-embedded, valid for Vista+) plus a transparent preview PNG.

Requires Pillow:  pip install Pillow
Usage:  python tools/make_shortcut_icon.py
"""

from __future__ import annotations

import io
import os

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SRC = os.path.join(ROOT, "assets", "vpn_icon.png")
OUT_ICO = os.path.join(ROOT, "packaging", "shortcut.ico")
OUT_PREVIEW = os.path.join(ROOT, "assets", "vpn_icon_preview.png")

SIZES = (16, 24, 32, 48, 64, 128, 256)


def _make_transparent(img: Image.Image) -> Image.Image:
    """Flood-fill the flat outer background (from each corner) to transparent."""
    img = img.convert("RGBA")
    for corner in ((0, 0), (img.width - 1, 0), (0, img.height - 1),
                   (img.width - 1, img.height - 1)):
        ImageDraw.floodfill(img, corner, (0, 0, 0, 0), thresh=40)
    return img


def _encode_ico(pngs: dict[int, bytes]) -> bytes:
    entries = sorted(pngs)
    header = struct_pack("<HHH", 0, 1, len(entries))
    offset = 6 + 16 * len(entries)
    directory = b""
    payload = b""
    for size in entries:
        data = pngs[size]
        dim = 0 if size >= 256 else size
        directory += struct_pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    return header + directory + payload


def struct_pack(fmt: str, *args) -> bytes:
    import struct

    return struct.pack(fmt, *args)


def main() -> int:
    src = Image.open(SRC)
    base = _make_transparent(src)

    pngs: dict[int, bytes] = {}
    for size in SIZES:
        resized = base.resize((size, size), Image.LANCZOS)
        buf = io.BytesIO()
        resized.save(buf, format="PNG")
        pngs[size] = buf.getvalue()

    with open(OUT_ICO, "wb") as handle:
        handle.write(_encode_ico(pngs))
    base.save(OUT_PREVIEW, format="PNG")

    print(f"wrote {OUT_ICO} ({len(SIZES)} sizes, {os.path.getsize(OUT_ICO)} bytes)")
    print(f"wrote {OUT_PREVIEW}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
