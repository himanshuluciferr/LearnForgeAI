"""Generates the two Teams icons.

Written by hand rather than with Pillow so the repo gains no dependency for two PNGs. Teams
requires colour 192x192 and outline 32x32, and the outline must be transparent with white
content or it renders as a filled block in the app bar.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ACCENT = (47, 111, 235)


def chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )


def png(width: int, height: int, pixel) -> bytes:
    """`pixel(x, y)` returns RGBA. Rows carry a leading filter byte, which is what makes raw
    PNG writable without a library."""
    raw = b"".join(
        b"\x00" + b"".join(bytes(pixel(x, y)) for x in range(width)) for y in range(height)
    )
    header = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(raw, 9))
        + chunk(b"IEND", b"")
    )


def outside_rounded(x: int, y: int, size: int, radius: int) -> bool:
    """Standard rounded-rectangle test: distance from the nearest corner centre, where a pixel
    in the straight edges has zero offset on one axis and is never cut."""
    dx = max(radius - x, x - (size - 1 - radius), 0)
    dy = max(radius - y, y - (size - 1 - radius), 0)
    return dx * dx + dy * dy > radius * radius


def colour_icon(size: int = 192) -> bytes:
    """A rounded square in the accent colour with a white 'L' cut into it."""
    bar_x, bar_w = size * 5 // 16, size * 3 // 32
    bar_y, bar_h = size * 5 // 16, size * 3 // 8
    foot_w = size * 3 // 8
    radius = size // 6

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        if outside_rounded(x, y, size, radius):
            return (0, 0, 0, 0)
        upright = bar_x <= x < bar_x + bar_w and bar_y <= y < bar_y + bar_h
        foot = bar_x <= x < bar_x + foot_w and bar_y + bar_h - bar_w <= y < bar_y + bar_h
        return (255, 255, 255, 255) if upright or foot else (*ACCENT, 255)

    return png(size, size, pixel)


def outline_icon(size: int = 32) -> bytes:
    """Transparent with white content, which is the only thing Teams renders here."""
    bar_x, bar_w = 11, 3
    bar_y, bar_h = 8, 14
    foot_w = 11

    def pixel(x: int, y: int) -> tuple[int, int, int, int]:
        upright = bar_x <= x < bar_x + bar_w and bar_y <= y < bar_y + bar_h
        foot = bar_x <= x < bar_x + foot_w and bar_y + bar_h - bar_w <= y < bar_y + bar_h
        return (255, 255, 255, 255) if upright or foot else (0, 0, 0, 0)

    return png(size, size, pixel)


if __name__ == "__main__":
    (HERE / "color.png").write_bytes(colour_icon())
    (HERE / "outline.png").write_bytes(outline_icon())
    print(f"wrote {HERE / 'color.png'} and {HERE / 'outline.png'}")
