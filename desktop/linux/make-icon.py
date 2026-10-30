#!/usr/bin/env python3
"""Draw the app icon as a PNG, stdlib only (#200).

    python desktop/linux/make-icon.py OUT.png [--size 256]

A rev gauge on a dark rounded square: a light arc with a red redline, a white
needle and a red hub. Drawn per pixel with 4x4 supersampling, so it needs no
Pillow in the build container and no binary file in the repository.
"""

from __future__ import annotations

import argparse
import math
import struct
import zlib
from pathlib import Path

BG = (22, 24, 29)
ARC = (226, 230, 236)
RED = (229, 50, 45)
NEEDLE = (255, 255, 255)
SWEEP = 270.0  # degrees, opening at the bottom
START = 225.0  # the arc starts bottom-left and runs clockwise
REDLINE = 0.75  # the last quarter of the sweep
NEEDLE_AT = 0.62


def _in_rounded_square(x: float, y: float, size: float, radius: float) -> bool:
    if not (0 <= x <= size and 0 <= y <= size):
        return False
    cx = min(max(x, radius), size - radius)
    cy = min(max(y, radius), size - radius)
    return (x - cx) ** 2 + (y - cy) ** 2 <= radius**2


def _segment_distance(px: float, py: float, ax: float, ay: float, bx: float, by: float) -> float:
    dx, dy = bx - ax, by - ay
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def colour_at(x: float, y: float, size: float) -> tuple[int, int, int] | None:
    """The topmost layer's colour at a point, or None outside the icon."""
    s = size / 256.0
    if not _in_rounded_square(x, y, size, 52 * s):
        return None
    cx, cy = 128 * s, 140 * s
    dx, dy = x - cx, cy - y  # y up
    r = math.hypot(dx, dy)
    # The hub, then the needle, then the arc: topmost first.
    if r <= 6 * s:
        return BG
    if r <= 15 * s:
        return RED
    angle = math.radians(START - SWEEP * NEEDLE_AT)
    tip = (cx + 80 * s * math.cos(angle), cy - 80 * s * math.sin(angle))
    if _segment_distance(x, y, cx, cy, *tip) <= 4.5 * s:
        return NEEDLE
    if 76 * s <= r <= 96 * s:
        along = (START - math.degrees(math.atan2(dy, dx))) % 360.0
        if along <= SWEEP:
            return RED if along / SWEEP >= REDLINE else ARC
    return BG


def render(size: int, samples: int = 4) -> bytes:
    rows = bytearray()
    step = 1.0 / samples
    offsets = [(i + 0.5) * step for i in range(samples)]
    total = samples * samples
    for py in range(size):
        rows.append(0)  # filter: none
        for px in range(size):
            r = g = b = hits = 0
            for oy in offsets:
                for ox in offsets:
                    c = colour_at(px + ox, py + oy, size)
                    if c is not None:
                        r += c[0]
                        g += c[1]
                        b += c[2]
                        hits += 1
            if hits:
                rows += bytes((r // hits, g // hits, b // hits, round(255 * hits / total)))
            else:
                rows += b"\0\0\0\0"
    return bytes(rows)


def png(size: int, raw: bytes) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + kind + data
                + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)  # 8-bit RGBA
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("out", type=Path)
    parser.add_argument("--size", type=int, default=256)
    args = parser.parse_args()
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(png(args.size, render(args.size)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
