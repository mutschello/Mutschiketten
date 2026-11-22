"""Page encoding for the SUPVAN / Katasymbol T50 family (T50M Pro).

Wire format reverse-engineered by heeen/supvan-cups (MIT) and documented by
zpan45/t50-windows-driver and KamilBakDev/katasymbol-t50m-print (both MIT).
This is an independent re-implementation of that format.

Coordinate system used here (the "head image"):
    * numpy bool array, shape (feed_rows, across_dots), True = black
    * across_dots = across the 384-dot print head (8 dots/mm, 48 mm)
    * feed_rows   = along the paper feed
"""

from __future__ import annotations

import lzma
import struct
from dataclasses import dataclass, field

import numpy as np

from .lzma_raw import compress_alone

DOTS_PER_MM = 8
HEAD_DOTS = 384
HEAD_BYTES = HEAD_DOTS // 8
PRINT_BUF_SIZE = 4096
PRINT_BUF_HEADER = 14
MAX_BUF_DATA = PRINT_BUF_SIZE - PRINT_BUF_HEADER - 8  # 4074, room for stride checksums
MARGIN_DOTS = 8
MAX_DENSITY = 15
MAX_BLOCK = PRINT_BUF_SIZE
PER_BUFFER_THRESHOLD = 4000
MAX_SPEED = 60
SPLIT_PAGE_SPEED = 20


# ----------------------------------------------------------------- compression
def compress_stdlib(data: bytes) -> bytes:
    """Python lzma (always writes an end marker) with the firmware header patch."""
    filters = [{"id": lzma.FILTER_LZMA1, "dict_size": 8192, "lc": 3, "lp": 0, "pb": 2, "nice_len": 128}]
    out = bytearray(lzma.compress(data, format=lzma.FORMAT_ALONE, filters=filters))
    out[5:13] = struct.pack("<Q", len(data))
    return bytes(out)


def compress(data: bytes, method: str = "native") -> bytes:
    if method == "stdlib":
        return compress_stdlib(data)
    z = compress_alone(data)
    if len(z) > MAX_BLOCK:  # very dense data: liblzma packs a bit tighter
        alt = compress_stdlib(data)
        if len(alt) < len(z):
            return alt
    return z


# ----------------------------------------------------------------- bitmap
def center_on_head(img: np.ndarray) -> np.ndarray:
    """Pad (or symmetrically crop) the across axis to exactly HEAD_DOTS."""
    rows, width = img.shape
    out = np.zeros((rows, HEAD_DOTS), dtype=bool)
    offset = (HEAD_DOTS - width) // 2
    if offset >= 0:
        out[:, offset:offset + width] = img
    else:
        out[:] = img[:, -offset:-offset + HEAD_DOTS]
    return out


def pack_columns(img: np.ndarray) -> bytes:
    """Head image -> firmware layout: one 48-byte line per feed row, LSB first.

    The print head counts its dots from the opposite side of the design's
    left edge, so each line is reversed before packing. Without this the
    labels come out mirrored left-to-right (confirmed by a test print on a
    T50M Pro).
    """
    return np.packbits(center_on_head(img)[:, ::-1], axis=1, bitorder="little").tobytes()


# ----------------------------------------------------------------- buffers
def _page_bits(page_st: bool, page_end: bool, prt_end: bool, density: int) -> bytes:
    b0 = (0x02 if page_st else 0) | (0x04 if page_end else 0) | (0x08 if prt_end else 0)
    b1 = ((density & 0x0F) << 2) | (1 << 6)  # nodu = density, mat = 1
    return bytes((b0, b1))


def build_buffer(chunk: bytes, cols: int, *, page_st: bool, page_end: bool, prt_end: bool,
                 density: int, margin_top: int = MARGIN_DOTS, margin_bottom: int = MARGIN_DOTS) -> bytes:
    buf = bytearray(PRINT_BUF_SIZE)
    buf[2:4] = _page_bits(page_st, page_end, prt_end, density)
    buf[4:6] = cols.to_bytes(2, "little")
    buf[6] = HEAD_BYTES
    buf[8:10] = max(1, margin_top).to_bytes(2, "little")
    buf[10:12] = max(1, margin_bottom).to_bytes(2, "little")
    buf[12] = min(density, MAX_DENSITY)
    buf[PRINT_BUF_HEADER:PRINT_BUF_HEADER + len(chunk)] = chunk
    data_end = cols * HEAD_BYTES + PRINT_BUF_HEADER
    chk = sum(buf[2:14])
    for i in range(1, data_end // 256 + 1):
        chk += buf[i * 256 - 1]
    buf[0:2] = (chk & 0xFFFF).to_bytes(2, "little")
    return bytes(buf)


def _segments(img: np.ndarray, density: int, last_page: bool, method: str):
    """Yield (buffer, compressed) pairs. Buffers shrink when data is too dense,
    so every compressed block stays within the printer's 4 KiB limit."""
    density = max(0, min(MAX_DENSITY, int(density)))
    packed = pack_columns(img)
    cols_img = img.shape[0] - 2 * MARGIN_DOTS
    if cols_img <= 0:
        raise ValueError("Etikett zu kurz")
    max_cols = MAX_BUF_DATA // HEAD_BYTES
    out = []
    done = 0
    while done < cols_img:
        n = min(max_cols, cols_img - done)
        while True:
            first, last = done == 0, done + n >= cols_img
            start = (MARGIN_DOTS + done) * HEAD_BYTES
            buf = build_buffer(packed[start:start + n * HEAD_BYTES], n, page_st=first, page_end=last,
                               prt_end=last and last_page, density=density)
            z = compress(buf, method)
            if len(z) <= MAX_BLOCK or n <= 8:
                break
            n = max(8, int(n * 0.85))
        if len(z) > MAX_BLOCK:
            raise ValueError("Motiv zu detailreich für den Drucker. Bitte Bild vereinfachen "
                             "oder bei Bildern „harte Schwelle“ statt Rasterung wählen.")
        out.append((buf, z))
        done += n
    return out


def build_buffers(img: np.ndarray, density: int, last_page: bool = True) -> list[bytes]:
    """Split a head image into 4096-byte print buffers.

    The first and last MARGIN_DOTS feed rows are not transmitted; the header
    tells the firmware to feed them blank (as the vendor tools do).
    """
    return [b for b, _ in _segments(img, density, last_page, "native")]


@dataclass
class Page:
    blocks: list[bytes] = field(default_factory=list)
    speed: int = MAX_SPEED


def encode_page(img: np.ndarray, density: int, last_page: bool = True, method: str = "native") -> Page:
    """Pack buffers into LZMA blocks like the vendor's USB desktop tool."""
    segs = _segments(img, density, last_page, method)
    singles = [z for _, z in segs]
    if sum(map(len, singles)) <= PER_BUFFER_THRESHOLD:
        whole = compress(b"".join(b for b, _ in segs), method)
        if len(whole) <= MAX_BLOCK:
            return Page([whole], MAX_SPEED)
    return Page(singles, SPLIT_PAGE_SPEED if len(singles) > 1 else MAX_SPEED)
