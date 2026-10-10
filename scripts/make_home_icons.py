"""Draw the site's home-screen icon: four rising bars on dark ink.

    python3 scripts/make_home_icons.py

Writes dashboard/assets/icons/{favicon.svg, apple-touch-icon.png, icon-192.png,
icon-512.png}. Standard library only (no Pillow / cairo on the dev machine or
in CI), which is possible because the mark is nothing but rounded rectangles.
One geometry (BARS) feeds both the SVG and the PNG rasteriser, so they cannot
drift apart; tests/test_home_screen_icon.py regenerates into a temp dir and
checks the committed files are byte-identical.

Colours are the dashboard's own tokens (dashboard/templates/css/_foundation.css.j2):
--beige-900 (ink), --green-400 and --beige-100. The squares are full-bleed on
purpose: iOS rounds the corners of a touch icon itself.
"""
from __future__ import annotations

import struct
import sys
import zlib
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "dashboard" / "assets" / "icons"

GRID = 100                              # the drawing is on a 100 x 100 grid
INK = (0x1F, 0x1C, 0x15)                # --beige-900
GREEN = (0x8F, 0xA7, 0x7A)              # --green-400
SAND = (0xF5, 0xF0, 0xE6)               # --beige-100

# (x, y, width, height, colour): rising rank, the leader in sand.
_W, _GAP, _LEFT, _BASE = 13, 6, 15, 80
_HEIGHTS = (22, 34, 48, 62)
BARS = [
    (_LEFT + i * (_W + _GAP), _BASE - h, _W, h, SAND if i == len(_HEIGHTS) - 1 else GREEN)
    for i, h in enumerate(_HEIGHTS)
]
RADIUS = 4

PNG_SIZES = {"apple-touch-icon.png": 180, "icon-192.png": 192, "icon-512.png": 512}


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


def svg() -> str:
    rects = "\n".join(
        f'  <rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{RADIUS}" fill="{_hex(c)}"/>'
        for x, y, w, h, c in BARS)
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}">\n'
            f'  <rect width="{GRID}" height="{GRID}" fill="{_hex(INK)}"/>\n{rects}\n</svg>\n')


def _inside(px: float, py: float, x: float, y: float, w: float, h: float, r: float) -> bool:
    """Point in a rounded rectangle (grid units)."""
    if not (x <= px <= x + w and y <= py <= y + h):
        return False
    cx = min(max(px, x + r), x + w - r)
    cy = min(max(py, y + r), y + h - r)
    return (px - cx) ** 2 + (py - cy) ** 2 <= r * r


def png(size: int, samples: int = 4) -> bytes:
    """Antialiased PNG (samples x samples supersampling), opaque RGB."""
    scale = GRID / size
    step = 1 / samples
    rows = []
    for j in range(size):
        row = bytearray([0])                                    # filter type: none
        for i in range(size):
            r = g = b = 0
            for sj in range(samples):
                for si in range(samples):
                    px = (i + (si + 0.5) * step) * scale
                    py = (j + (sj + 0.5) * step) * scale
                    colour = INK
                    for x, y, w, h, c in BARS:
                        if _inside(px, py, x, y, w, h, RADIUS):
                            colour = c
                            break
                    r += colour[0]
                    g += colour[1]
                    b += colour[2]
            n = samples * samples
            row += bytes((round(r / n), round(g / n), round(b / n)))
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    header = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)   # 8-bit RGB
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


def write_all(out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    path = out_dir / "favicon.svg"
    path.write_text(svg())
    written.append(path)
    for name, size in PNG_SIZES.items():
        path = out_dir / name
        path.write_bytes(png(size))
        written.append(path)
    return written


if __name__ == "__main__":
    for p in write_all(Path(sys.argv[1]) if len(sys.argv) > 1 else OUT_DIR):
        print(p)
