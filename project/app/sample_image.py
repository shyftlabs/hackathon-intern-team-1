"""Dependency-free generation of valid sample product photos (base64 PNG).

No Pillow in the venv, so we hand-encode small but *valid* RGB PNGs the vision
model can actually process. Two variants:
  * clean    — a product silhouette on a neutral background
  * damaged  — same, with a dark scuff/tear streak across it

These are used as the default upload in the CLI demo and the web UI so the
Fraud (vision) lane has a real image to inspect.
"""

from __future__ import annotations

import base64
import struct
import zlib

_W = _H = 128


def _png(rgb_fn) -> bytes:
    raw = bytearray()
    for y in range(_H):
        raw.append(0)  # filter type 0 (None) per scanline
        for x in range(_W):
            r, g, b = rgb_fn(x, y)
            raw += bytes((r & 255, g & 255, b & 255))

    def chunk(typ: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + typ
            + data
            + struct.pack(">I", zlib.crc32(typ + data) & 0xFFFFFFFF)
        )

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", _W, _H, 8, 2, 0, 0, 0)  # 8-bit RGB
    idat = zlib.compress(bytes(raw), 9)
    return sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", idat) + chunk(b"IEND", b"")


def _in_boot(x: int, y: int) -> bool:
    # Crude L-shaped boot silhouette in the centre of the frame.
    shaft = 40 <= x <= 70 and 24 <= y <= 92
    foot = 40 <= x <= 104 and 84 <= y <= 104
    return shaft or foot


def _clean_rgb(x: int, y: int):
    if _in_boot(x, y):
        return (120, 78, 44)  # leather brown
    return (228, 230, 233)  # neutral studio grey


def _damaged_rgb(x: int, y: int):
    # Same boot, but with a dark diagonal scuff/tear across the shaft.
    if _in_boot(x, y) and abs((x - 40) - (y - 24)) < 4:
        return (28, 24, 22)  # dark scuff
    return _clean_rgb(x, y)


def clean_product_png_b64() -> str:
    return base64.b64encode(_png(_clean_rgb)).decode()


def damaged_product_png_b64() -> str:
    return base64.b64encode(_png(_damaged_rgb)).decode()


# Pre-rendered defaults (cheap to compute once at import).
SAMPLE_CLEAN_B64 = clean_product_png_b64()
SAMPLE_DAMAGED_B64 = damaged_product_png_b64()
