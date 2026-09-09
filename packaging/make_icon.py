#!/usr/bin/env python3
"""Generate the AntLighting application icon from scratch.

Pure standard library: it rasterises the same ant-and-lightning mark used by
the QML mascot and writes ``packaging/AntLighting.png`` plus a multi-resolution
``packaging/AntLighting.ico`` for the Windows build.  No image library is
required and the output is deterministic, so the icon is reproducible.

Usage:  python packaging/make_icon.py
"""

from __future__ import annotations

import math
import os
import struct
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))

# Colours (RGB, 0-255).
AMBER = (255, 179, 0)
AMBER_DEEP = (230, 132, 0)
INK = (26, 26, 32)
PAPER = (255, 255, 255)
TRANSPARENT = (0, 0, 0, 0)

SIZES = (16, 24, 32, 48, 64, 128, 256)


class Canvas:
    """A tiny RGBA raster with antialiased circle/line primitives."""

    def __init__(self, size: int) -> None:
        self.size = size
        self.px = [TRANSPARENT] * (size * size)

    def put(self, x: int, y: int, color: tuple[int, int, int, int]) -> None:
        if 0 <= x < self.size and 0 <= y < self.size:
            self.px[y * self.size + x] = color

    def get(self, x: int, y: int) -> tuple[int, int, int, int]:
        if 0 <= x < self.size and 0 <= y < self.size:
            return self.px[y * self.size + x]
        return TRANSPARENT

    # -- coverage-based drawing (4x4 supersampling per pixel) ---------------
    def _cover(self, x: int, y: int, inside) -> float:
        hits = 0
        for sy in range(4):
            for sx in range(4):
                if inside(x + (sx + 0.5) / 4.0, y + (sy + 0.5) / 4.0):
                    hits += 1
        return hits / 16.0

    def _blend(self, x: int, y: int, color: tuple[int, int, int], alpha: float) -> None:
        if alpha <= 0.0:
            return
        r, g, b, a = self.get(x, y)
        out_a = a / 255.0 + alpha * (1.0 - a / 255.0)
        if out_a <= 0.0:
            self.put(x, y, (color[0], color[1], color[2], int(alpha * 255)))
            return
        nr = int((color[0] * alpha + r * (a / 255.0) * (1 - alpha)) / out_a)
        ng = int((color[1] * alpha + g * (a / 255.0) * (1 - alpha)) / out_a)
        nb = int((color[2] * alpha + b * (a / 255.0) * (1 - alpha)) / out_a)
        self.put(x, y, (min(255, nr), min(255, ng), min(255, nb), min(255, int(out_a * 255))))

    def disc(self, cx: float, cy: float, r: float, color) -> None:
        for y in range(max(0, int(cy - r - 2)), min(self.size, int(cy + r + 3))):
            for x in range(max(0, int(cx - r - 2)), min(self.size, int(cx + r + 3))):
                cov = self._cover(x, y, lambda px, py: (px - cx) ** 2 + (py - cy) ** 2 <= r * r)
                if cov:
                    self._blend(x, y, color, cov)

    def stroke(self, x1: float, y1: float, x2: float, y2: float, width: float, color) -> None:
        hw = width / 2.0
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy) or 1.0
        # Bounding box of the capsule.
        min_x, max_x = min(x1, x2) - hw, max(x1, x2) + hw
        min_y, max_y = min(y1, y2) - hw, max(y1, y2) + hw
        for y in range(max(0, int(min_y - 1)), min(self.size, int(max_y + 2))):
            for x in range(max(0, int(min_x - 1)), min(self.size, int(max_x + 2))):

                def inside(px: float, py: float) -> bool:
                    t = max(0.0, min(1.0, ((px - x1) * dx + (py - y1) * dy) / (length * length)))
                    proj_x, proj_y = x1 + t * dx, y1 + t * dy
                    return (px - proj_x) ** 2 + (py - proj_y) ** 2 <= hw * hw

                cov = self._cover(x, y, inside)
                if cov:
                    self._blend(x, y, color, cov)

    def polygon(self, points: list[tuple[float, float]], color) -> None:
        xs = [p[0] for p in points]
        ys = [p[1] for p in points]
        n = len(points)

        def inside(px: float, py: float) -> bool:
            hit = False
            j = n - 1
            for i in range(n):
                xi, yi = points[i]
                xj, yj = points[j]
                if (yi > py) != (yj > py) and px < (xj - xi) * (py - yi) / ((yj - yi) or 1e-9) + xi:
                    hit = not hit
                j = i
            return hit

        for y in range(max(0, int(min(ys) - 1)), min(self.size, int(max(ys) + 2))):
            for x in range(max(0, int(min(xs) - 1)), min(self.size, int(max(xs) + 2))):
                cov = self._cover(x, y, inside)
                if cov:
                    self._blend(x, y, color, cov)


def draw_icon(size: int) -> Canvas:
    """Draw the mark: an amber rounded square with a white ant and a lightning bolt."""
    c = Canvas(size)
    s = float(size)

    # Rounded-square background.
    radius = s * 0.22
    margin = 0.0

    def rounded(px: float, py: float) -> bool:
        x = min(max(px, margin + radius), s - margin - radius)
        y = min(max(py, margin + radius), s - margin - radius)
        return (px - x) ** 2 + (py - y) ** 2 <= radius * radius

    for y in range(size):
        for x in range(size):
            cov = c._cover(x, y, rounded)
            if cov:
                c._blend(x, y, AMBER, cov)

    # Subtle bottom shading band for depth.
    for y in range(int(s * 0.72), size):
        for x in range(size):
            if c.get(x, y)[3]:
                shade = (y - s * 0.72) / (s * 0.28)
                c._blend(x, y, AMBER_DEEP, 0.35 * shade)

    w = s / 100.0  # unit stroke width

    # -- the ant, drawn in ink on the amber field --------------------------
    ink = INK
    body_y = s * 0.60
    c.disc(s * 0.36, body_y - s * 0.13, s * 0.085, ink)          # head
    c.disc(s * 0.36, body_y + s * 0.02, s * 0.075, ink)          # thorax
    c.disc(s * 0.36, body_y + s * 0.20, s * 0.115, ink)          # abdomen
    c.stroke(s * 0.36, body_y - s * 0.06, s * 0.36, body_y + s * 0.12, s * 0.055, ink)

    # antennae
    c.stroke(s * 0.325, body_y - s * 0.20, s * 0.25, body_y - s * 0.31, s * 0.030, ink)
    c.stroke(s * 0.395, body_y - s * 0.20, s * 0.47, body_y - s * 0.31, s * 0.030, ink)
    c.disc(s * 0.25, body_y - s * 0.31, s * 0.022, ink)
    c.disc(s * 0.47, body_y - s * 0.31, s * 0.022, ink)

    # legs (three per side)
    for i, dy in enumerate((-0.03, 0.07, 0.17)):
        c.stroke(s * 0.305, body_y + s * dy, s * 0.17, body_y + s * (dy + 0.09), s * 0.028, ink)
        c.stroke(s * 0.415, body_y + s * dy, s * 0.54, body_y + s * (dy + 0.09), s * 0.028, ink)

    # -- lightning bolt, raised to the upper right --------------------------
    bolt = [
        (s * 0.76, s * 0.08),
        (s * 0.60, s * 0.42),
        (s * 0.72, s * 0.42),
        (s * 0.58, s * 0.76),
        (s * 0.82, s * 0.36),
        (s * 0.70, s * 0.36),
        (s * 0.86, s * 0.08),
    ]
    c.polygon(bolt, PAPER)

    # At small sizes the bolt detail disappears; keep it bold and simple.
    if size <= 24:
        c.polygon(bolt, PAPER)

    return c


# ------------------------------------------------------------------- encoders
def encode_png(canvas: Canvas) -> bytes:
    size = canvas.size
    raw = bytearray()
    for y in range(size):
        raw.append(0)  # filter: none
        for x in range(size):
            r, g, b, a = canvas.get(x, y)
            raw += bytes((r, g, b, a))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + kind
            + data
            + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 9))
        + chunk(b"IEND", b"")
    )


def encode_ico(pngs: dict[int, bytes]) -> bytes:
    """Build an ICO that embeds PNG payloads (valid since Windows Vista)."""
    entries = sorted(pngs)
    count = len(entries)
    header = struct.pack("<HHH", 0, 1, count)
    offset = 6 + 16 * count
    directory = b""
    payload = b""
    for size in entries:
        data = pngs[size]
        dimension = 0 if size >= 256 else size
        directory += struct.pack("<BBBBHHII", dimension, dimension, 0, 0, 1, 32, len(data), offset)
        payload += data
        offset += len(data)
    return header + directory + payload


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    pngs: dict[int, bytes] = {}
    for size in SIZES:
        pngs[size] = encode_png(draw_icon(size))

    largest = max(SIZES)
    png_path = os.path.join(HERE, "AntLighting.png")
    ico_path = os.path.join(HERE, "AntLighting.ico")
    # The QML window and taskbar icon reuse the same raster.
    ui_icon = os.path.join(HERE, os.pardir, "antlighting", "ui", "icon.png")
    ui_icon = os.path.abspath(ui_icon)
    os.makedirs(os.path.dirname(ui_icon), exist_ok=True)
    with open(png_path, "wb") as handle:
        handle.write(pngs[largest])
    with open(ico_path, "wb") as handle:
        handle.write(encode_ico(pngs))
    with open(ui_icon, "wb") as handle:
        handle.write(pngs[largest])

    print(f"wrote {png_path} ({largest}x{largest}, {len(pngs[largest])} bytes)")
    print(f"wrote {ico_path} ({len(SIZES)} sizes, {os.path.getsize(ico_path)} bytes)")
    print(f"wrote {ui_icon} ({largest}x{largest})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
