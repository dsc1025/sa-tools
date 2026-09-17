"""Read-only Stone Age 2.5 resource decoder.

The REAL file is always read through seek/read for the exact ADRN block; it is
never loaded as a whole.
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


ADRN_SIZE = 80


@dataclass(frozen=True)
class ImageInfo:
    number: int
    offset: int
    size: int
    x: int
    y: int
    width: int
    height: int
    palette: int


def read_adrn(path: Path, number: int) -> ImageInfo:
    with path.open("rb") as file:
        file.seek(number * ADRN_SIZE)
        row = file.read(ADRN_SIZE)
    if len(row) != ADRN_SIZE:
        raise ValueError(f"ADRN image {number} is outside the index")
    image_no, offset, size, x, y, width, height, _east, _south, palette, _unknown = struct.unpack_from("<IIIiiIIBBBB", row)
    if image_no != number or not size:
        raise ValueError(f"ADRN image {number} is empty or does not match its index")
    return ImageInfo(number, offset, size, x, y, width, height, palette)


def decode_rle(payload: bytes, expected_size: int) -> bytes:
    """Decode the JSS/Stone Age 8-bit RLE stream.

    Background runs are emitted as palette index 253, the conventional
    transparent colour for exported previews.
    """
    out = bytearray()
    cursor = 0

    def take(count: int) -> bytes:
        nonlocal cursor
        if cursor + count > len(payload):
            raise ValueError("RLE stream ended before a complete run")
        value = payload[cursor:cursor + count]
        cursor += count
        return value

    while cursor < len(payload) and len(out) < expected_size:
        code = payload[cursor]
        cursor += 1
        family, low = code >> 4, code & 0x0F
        if family == 0:
            out.extend(take(low))
        elif family == 1:
            length = (low << 8) | take(1)[0]
            out.extend(take(length))
        elif family == 2:
            extra = take(2)
            length = (low << 16) | (extra[0] << 8) | extra[1]
            out.extend(take(length))
        elif family == 8:
            out.extend(take(1) * low)
        elif family == 9:
            colour, extra = take(2)
            out.extend(bytes((colour,)) * ((low << 8) | extra))
        elif family == 10:
            colour, high, low_byte = take(3)
            out.extend(bytes((colour,)) * ((low << 16) | (high << 8) | low_byte))
        elif family == 12:
            out.extend(bytes((253,)) * low)
        elif family == 13:
            out.extend(bytes((253,)) * ((low << 8) | take(1)[0]))
        elif family == 14:
            high, low_byte = take(2)
            out.extend(bytes((253,)) * ((low << 16) | (high << 8) | low_byte))
        else:
            # Values outside the RLE control families are literal colour bytes.
            out.append(code)
    if len(out) != expected_size:
        raise ValueError(f"RLE decoded {len(out)} bytes; expected {expected_size}")
    return bytes(out)


def read_image(adrn: Path, real: Path, number: int) -> tuple[ImageInfo, bytes]:
    info = read_adrn(adrn, number)
    with real.open("rb") as file:
        file.seek(info.offset)
        block = file.read(info.size)
    if len(block) != info.size:
        raise ValueError("REAL block is truncated")
    magic, compressed, width, _pad1, height, _pad2, total_size = struct.unpack_from("<2sHHHHHI", block)
    if magic != b"RD" or total_size != info.size or (width, height) != (info.width, info.height):
        raise ValueError("REAL header does not match ADRN")
    pixels = block[16:] if compressed == 0 else decode_rle(block[16:], width * height)
    if len(pixels) != width * height:
        raise ValueError("Unexpected raw pixel length")
    return info, pixels


SYSTEM_PALETTE = [(0, 0, 0)] * 256
SYSTEM_PALETTE[:16] = [(0,0,0),(0,0,128),(0,128,0),(0,128,128),(128,0,0),(128,0,128),(128,128,0),(192,192,192),(192,220,192),(240,202,166),(0,0,222),(0,95,255),(160,255,255),(210,95,0),(255,210,80),(40,225,40)]
SYSTEM_PALETTE[240:256] = [(150,195,245),(95,160,225),(70,125,195),(30,85,155),(55,65,70),(30,35,40),(240,251,255),(164,160,160),(128,128,128),(0,0,255),(0,255,0),(0,255,255),(255,0,0),(255,0,255),(255,255,0),(255,255,255)]


def palette(path: Path) -> list[tuple[int, int, int]]:
    colours = SYSTEM_PALETTE.copy()
    raw = path.read_bytes()
    if len(raw) != 708:
        raise ValueError(f"Unexpected palette size: {path}")
    colours[16:252] = [tuple(raw[i:i + 3]) for i in range(0, 708, 3)]
    return colours


def png_rgba(path: Path, width: int, height: int, pixels: bytes, colours: list[tuple[int, int, int]]) -> None:
    # REAL data is bottom-up; PNG expects top-down rows.
    rows = []
    for source_y in range(height - 1, -1, -1):
        row = bytearray(b"\0")
        for colour_index in pixels[source_y * width:(source_y + 1) * width]:
            red, green, blue = colours[colour_index]
            row.extend((red, green, blue, 0 if colour_index == 253 else 255))
        rows.append(bytes(row))
    data = zlib.compress(b"".join(rows), level=9)
    def chunk(kind: bytes, content: bytes) -> bytes:
        return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content) & 0xFFFFFFFF)
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)) + chunk(b"IDAT", data) + chunk(b"IEND", b""))
