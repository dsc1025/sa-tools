#!/usr/bin/env python3
"""Convert one indexed RO 2.1 SPR + 2.5 ACT pair into a Stone Age test package."""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from io import BytesIO
from pathlib import Path

from PIL import Image

from ro_sprite_preview import composite_frame, read_act, read_spr
from sa_resource import palette, decode_rle
from client_data import resources
from export_sprite_preview import actions, sprite_range
from spr_package import FORMAT, VERSION, adrn_record, read_at


# Stone Age action meanings: attack, hurt, death, idle, move, special, guard.
ACTION_CODES = (0, 2, 1, 10, 3, 4, 9)


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def nearest_index(colour: tuple[int, int, int], colours: list[tuple[int, int, int]]) -> int:
    red, green, blue = colour
    return min(range(252), key=lambda index: (red - colours[index][0]) ** 2 + (green - colours[index][1]) ** 2 + (blue - colours[index][2]) ** 2)


def encode_rle(pixels: bytes) -> bytes:
    """Encode Stone Age's transparent-colour (253) runs and literal spans."""
    output = bytearray()
    cursor = 0
    while cursor < len(pixels):
        if pixels[cursor] == 253:
            end = cursor + 1
            while end < len(pixels) and pixels[end] == 253:
                end += 1
            length = end - cursor
            while length:
                chunk = min(length, 0xFFFFF)
                if chunk <= 15:
                    output.append(0xC0 | chunk)
                elif chunk <= 0xFFF:
                    output.extend((0xD0 | (chunk >> 8), chunk & 0xFF))
                else:
                    output.extend((0xE0 | (chunk >> 16), (chunk >> 8) & 0xFF, chunk & 0xFF))
                length -= chunk
            cursor = end
            continue
        end = cursor + 1
        while end < len(pixels) and pixels[end] != 253:
            end += 1
        literal = pixels[cursor:end]
        while literal:
            chunk, literal = literal[:0xFFF], literal[0xFFF:]
            if len(chunk) <= 15:
                output.append(len(chunk))
            else:
                output.extend((0x10 | (len(chunk) >> 8), len(chunk) & 0xFF))
            output.extend(chunk)
        cursor = end
    return bytes(output)


def stone_frame(image, offset, colours, number: int, real_offset: int, lookup: dict[tuple[int, int, int], int], adrn_template: bytes, real_reserved: int) -> tuple[bytes, bytes]:
    rgba = image.convert("RGBA")
    # The original client stores every 8-bit scanline on a 4-byte boundary.
    aligned_width = (rgba.width + 3) & ~3
    if aligned_width != rgba.width:
        aligned = Image.new("RGBA", (aligned_width, rgba.height))
        aligned.alpha_composite(rgba)
        rgba = aligned
    pixels = bytearray()
    # Stone Age REAL data is stored bottom-up.
    flipped = rgba.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    for red, green, blue, alpha in flipped.getdata():
        if alpha < 128:
            pixels.append(253)
            continue
        key = (red, green, blue)
        if key not in lookup:
            lookup[key] = nearest_index(key, colours)
        pixels.append(lookup[key])
    compressed = encode_rle(bytes(pixels))
    if decode_rle(compressed, len(pixels)) != bytes(pixels):
        raise ValueError("Stone Age RLE roundtrip failed")
    use_compressed = len(compressed) < len(pixels)
    payload = compressed if use_compressed else bytes(pixels)
    real = struct.pack("<2sBBIII", b"RD", int(use_compressed), real_reserved, rgba.width, rgba.height, 16 + len(payload)) + payload
    adrn = bytearray(adrn_template)
    struct.pack_into("<IIIiiII", adrn, 0, number, real_offset, len(real), *offset, rgba.width, rgba.height)
    return bytes(adrn), real


def build(spr: Path, act: Path, target_data: Path, output: Path, sprite: int) -> None:
    if not 100000 <= sprite <= 132767:
        raise ValueError("this client only supports sprite numbers 100000..132767")
    source_images = read_spr(spr)
    source_actions, intervals = read_act(act, with_intervals=True)
    if len(source_actions) != 48:
        raise ValueError(f"expected 48 RO actions (6 groups × 8 directions), got {len(source_actions)}")
    colours = palette(target_data / "pal" / "Palet_1.sap")
    native = resources(target_data)
    template_start, template_end = sprite_range(native["spradrn"], native["spr"], 100330)
    template_bitmap = actions(native["spr"], template_start, template_end)[0]["frames"][0]["bitmap"]
    adrn_template, template_real_offset, template_real_size = adrn_record(native["adrn"], template_bitmap)
    real_reserved = read_at(native["real"], template_real_offset, template_real_size)[3]
    template_flags = next(flags for number, _offset, flags in struct.iter_unpack("<III", native["spradrn"].read_bytes()) if number == 100330)
    frame_records: list[tuple[int, bytes, bytes]] = []
    colour_lookup: dict[tuple[int, int, int], int] = {}
    rendered_frames: dict[tuple[int, int, bytes], int] = {}
    animation = bytearray()
    # Keep package frame IDs outside the client's existing frame range so the
    # validator can prove the package imports cleanly before remapping it.
    number = sprite * 1000
    real_offset = 0
    # RO groups: idle, move, attack, hurt, death, special.
    # Stone actions: attack, hurt, death, idle, move, special, guard.
    # RO has no guard group, so guard safely reuses idle.
    groups = (2, 4, 3, 0, 0, 1, 5)
    for direction in range(8):
        for action_code, group in zip(ACTION_CODES, groups):
            frames = source_actions[group * 8 + direction]
            duration = max(1, round(intervals[group * 8 + direction] * 25 * len(frames)))
            animation.extend(struct.pack("<HHII", direction, action_code, duration, len(frames)))
            for layers in frames:
                image, offset = composite_frame(layers, source_images)
                key = (image.width, image.height, offset, image.tobytes())
                bitmap = rendered_frames.get(key)
                if bitmap is None:
                    bitmap = number
                    adrn, real = stone_frame(image, offset, colours, bitmap, real_offset, colour_lookup, adrn_template, real_reserved)
                    frame_records.append((bitmap, adrn, real))
                    rendered_frames[key] = bitmap
                    real_offset += len(real)
                    number += 1
                animation.extend(struct.pack("<IhhH", bitmap, 0, 0, 0))
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "sprite": sprite,
        "frame_count": len(frame_records),
        "sprite_sha256": sha(bytes(animation)),
        "conversion_revision": 3,
        "action_mapping": {str(code): group for code, group in zip(ACTION_CODES, groups)},
        "alignment": "RO centres preserved; transparent right padding makes every scanline a native 4-byte multiple",
        "source": {"ro_spr": str(spr), "ro_act": str(act), "note": "RO groups 0..5 = idle, walk, attack, hurt, die, special"},
        "frames": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sprite/index.bin", struct.pack("<III", sprite, 0, template_flags))
        archive.writestr("sprite/data.bin", animation)
        for frame_number, adrn, real in frame_records:
            manifest["frames"].append({"number": frame_number, "adrn_sha256": sha(adrn), "real_sha256": sha(real), "size": len(real)})
            archive.writestr(f"frames/{frame_number}.adrn", adrn)
            archive.writestr(f"frames/{frame_number}.real", real)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"created {output} with {len(frame_records)} frames and 56 animation blocks")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("spr", type=Path)
    parser.add_argument("act", type=Path)
    parser.add_argument("--data", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--sprite", type=int, default=130134)
    args = parser.parse_args()
    build(args.spr, args.act, args.data, args.output, args.sprite)


if __name__ == "__main__":
    main()
