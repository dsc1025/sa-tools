#!/usr/bin/env python3
"""Inspect CrossGate animations and convert one to a Stone Age .spr package.

The output is consumed by spr_importer.py.  Source client files are read-only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import zipfile
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from client_data import resources
from export_sprite_preview import actions, sprite_range
from ro_to_stoneage_package import stone_frame
from sa_resource import SYSTEM_PALETTE, palette
from spr_package import FORMAT, VERSION, adrn_record, read_at


SETS = {
    "base": ("GraphicInfo_66.bin", "Graphic_66.bin", "AnimeInfo_4.bin", "Anime_4.bin"),
    "ex": ("GraphicInfoEx_5.bin", "GraphicEx_5.bin", "AnimeInfoEx_1.Bin", "AnimeEx_1.Bin"),
    "v3": ("GraphicInfoV3_19.bin", "GraphicV3_19.bin", "AnimeInfoV3_8.bin", "AnimeV3_8.bin"),
    "puk2": ("Puk2/GraphicInfo_PUK2_2.bin", "Puk2/Graphic_PUK2_2.bin", "Puk2/AnimeInfo_PUK2_4.bin", "Puk2/Anime_PUK2_4.bin"),
    "puk3": ("Puk3/GraphicInfo_PUK3_1.bin", "Puk3/Graphic_PUK3_1.bin", "Puk3/AnimeInfo_PUK3_2.bin", "Puk3/Anime_PUK3_2.bin"),
    "joy": ("GraphicInfo_Joy_125.bin", "Graphic_Joy_125.bin", "AnimeInfo_Joy_91.bin", "Anime_Joy_91.bin"),
    "joy_ch1": ("GraphicInfo_Joy_CH1.bin", "Graphic_Joy_CH1.bin", "AnimeInfo_Joy_CH1.Bin", "Anime_Joy_CH1.bin"),
    "joy_ex": ("GraphicInfo_Joy_EX_111.bin", "Graphic_Joy_EX_111.bin", "AnimeInfo_Joy_EX_107.bin", "Anime_Joy_EX_107.bin"),
}

# CrossGate and Stone Age number the same eight compass directions from
# different starting points. These mappings were verified side-by-side in the
# two client viewers.
CG_TO_SA_DIRECTION = {5: 0, 6: 1, 7: 2, 0: 3, 1: 4, 2: 5, 3: 6, 4: 7}

# Stone Age standard actions: attack, hurt, death, idle, move, special, guard.
# CG action 1 is a second idle variant and is used only when action 0 is absent.
CG_TO_SA_ACTION = {5: 0, 8: 1, 10: 2, 0: 3, 1: 3, 3: 4, 6: 9, 9: 10}
CG_ACTION_PRIORITY = {0: 0, 1: 1}


@dataclass(frozen=True)
class GraphicInfo:
    number: int
    address: int
    size: int
    x: int
    y: int
    width: int
    height: int
    map_id: int


def paths(root: Path, set_name: str) -> tuple[Path, Path, Path, Path]:
    try:
        names = SETS[set_name]
    except KeyError as error:
        raise ValueError(f"unknown set {set_name!r}; choose: {', '.join(SETS)}") from error
    result = tuple(root / name for name in names)
    missing = [str(path) for path in result if not path.is_file()]
    if missing:
        raise FileNotFoundError("missing CrossGate files: " + ", ".join(missing))
    return result  # type: ignore[return-value]


def anime_rows(path: Path) -> list[tuple[int, int, int]]:
    return [(number, offset, count) for number, offset, count, _pad in struct.iter_unpack("<IIHH", path.read_bytes())]


def graphic_info(path: Path, number: int) -> GraphicInfo:
    with path.open("rb") as file:
        file.seek(number * 40)
        raw = file.read(40)
    if len(raw) != 40:
        raise ValueError(f"graphic {number} is outside {path.name}")
    values = struct.unpack("<IIIiiiiBBB5sI", raw)
    info = GraphicInfo(values[0], values[1], values[2], values[3], values[4], values[5], values[6], values[11])
    # Animation frames normally address the record index. Some custom clients
    # instead address the id; make the common mismatch explicit.
    if info.number != number and info.number != 0:
        raise ValueError(f"graphic index {number} contains id {info.number}; id lookup is required")
    return info


def cg_palette(path: Path) -> list[tuple[int, int, int]]:
    raw = path.read_bytes()
    if len(raw) != 708:
        raise ValueError(f"unexpected CrossGate palette size: {path}")
    colours = SYSTEM_PALETTE.copy()
    colours[16:252] = [tuple(raw[index:index + 3]) for index in range(0, 708, 3)]
    return colours


def decode_cg_rle(payload: bytes, expected_size: int) -> bytes:
    """Decode JSS RLE using CG's transparent index 0.

    A few official blocks decode one padding byte beyond GraphicInfo's stated
    dimensions, so decode complete runs and crop that harmless tail.
    """
    out = bytearray()
    cursor = 0
    while cursor < len(payload) and len(out) < expected_size:
        code = payload[cursor]
        cursor += 1
        family, low = code >> 4, code & 15
        if family in (0, 1, 2):
            if family == 0:
                length = low
            elif family == 1:
                length = (low << 8) | payload[cursor]
                cursor += 1
            else:
                length = (low << 16) | (payload[cursor] << 8) | payload[cursor + 1]
                cursor += 2
            out.extend(payload[cursor:cursor + length])
            cursor += length
        elif family in (8, 9, 10):
            colour = payload[cursor]
            cursor += 1
            if family == 8:
                length = low
            elif family == 9:
                length = (low << 8) | payload[cursor]
                cursor += 1
            else:
                length = (low << 16) | (payload[cursor] << 8) | payload[cursor + 1]
                cursor += 2
            out.extend(bytes((colour,)) * length)
        elif family in (12, 13, 14):
            if family == 12:
                length = low
            elif family == 13:
                length = (low << 8) | payload[cursor]
                cursor += 1
            else:
                length = (low << 16) | (payload[cursor] << 8) | payload[cursor + 1]
                cursor += 2
            out.extend(b"\0" * length)
        else:
            out.append(code)
    if len(out) < expected_size:
        raise ValueError(f"CG RLE decoded {len(out)} bytes; expected at least {expected_size}")
    return bytes(out[:expected_size])


def read_graphic(info_path: Path, data_path: Path, number: int, colours: list[tuple[int, int, int]]) -> tuple[Image.Image, GraphicInfo]:
    info = graphic_info(info_path, number)
    with data_path.open("rb") as file:
        file.seek(info.address)
        block = file.read(info.size)
    if len(block) != info.size or block[:2] != b"RD":
        raise ValueError(f"graphic {number} has an invalid or truncated RD block")
    version = block[2]
    header_size = 20 if version >= 2 else 16
    palette_size = struct.unpack_from("<I", block, 16)[0] if version >= 2 else 0
    expected = info.width * info.height + palette_size
    payload = block[header_size:]
    decoded = decode_cg_rle(payload, expected) if version & 1 else payload
    if len(decoded) < expected:
        raise ValueError(f"graphic {number} decoded to {len(decoded)} bytes; expected {expected}")
    pixels = decoded[:info.width * info.height]
    frame_colours = colours
    if palette_size:
        palette_bytes = decoded[-palette_size:]
        frame_colours = [tuple(palette_bytes[i:i + 3]) for i in range(0, len(palette_bytes) - 2, 3)]
        frame_colours.extend([(0, 0, 0)] * (256 - len(frame_colours)))
    rgba = bytearray()
    for value in pixels:
        red, green, blue = frame_colours[value]
        rgba.extend((red, green, blue, 0 if value == 0 else 255))
    # CrossGate stores scanlines bottom-up, like Stone Age REAL. Pillow and the
    # review UI use the conventional top-down orientation.
    image = Image.frombytes("RGBA", (info.width, info.height), bytes(rgba))
    return image.transpose(Image.Transpose.FLIP_TOP_BOTTOM), info


def read_animation(info_path: Path, data_path: Path, number: int) -> list[dict]:
    # Some official base AnimeInfo files contain the same sequence table three
    # times. The earlier copies are known to be stale; the last record wins.
    row = next((row for row in reversed(anime_rows(info_path)) if row[0] == number), None)
    if row is None:
        raise ValueError(f"animation {number} does not exist in {info_path.name}")
    _number, offset, count = row
    result = []
    with data_path.open("rb") as file:
        file.seek(offset)
        for _ in range(count):
            raw = file.read(12)
            if len(raw) != 12:
                raise ValueError("animation header is truncated")
            direction, action, duration, frame_count = struct.unpack("<HHII", raw)
            extra = file.read(8)
            if len(extra) == 8 and struct.unpack_from("<i", extra, 4)[0] == -1:
                _palette_no, reversed_flag, _sentinel = struct.unpack("<HHi", extra)
            else:
                file.seek(-len(extra), 1)
                reversed_flag = 0
            frames = []
            for _frame in range(frame_count):
                frame_raw = file.read(10)
                if len(frame_raw) != 10:
                    raise ValueError("animation frame is truncated")
                graphic, x, y, flag = struct.unpack("<IhhH", frame_raw)
                frames.append({"graphic": graphic, "x": x, "y": y, "flag": flag})
            if reversed_flag:
                frames.reverse()
            result.append({"direction": direction, "action": action, "duration": duration, "frames": frames})
    return result


def sha(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def stoneage_actions(source_actions: list[dict]) -> tuple[list[dict], list[dict]]:
    """Map CG directions/actions and return selected blocks plus skipped blocks."""
    selected: dict[tuple[int, int], tuple[int, dict]] = {}
    skipped: list[dict] = []
    for item in source_actions:
        cg_direction, cg_action = item["direction"], item["action"]
        if cg_direction not in CG_TO_SA_DIRECTION or cg_action not in CG_TO_SA_ACTION:
            skipped.append(item)
            continue
        key = (CG_TO_SA_DIRECTION[cg_direction], CG_TO_SA_ACTION[cg_action])
        priority = CG_ACTION_PRIORITY.get(cg_action, 0)
        current = selected.get(key)
        if current is None or priority < current[0]:
            if current is not None:
                skipped.append(current[1])
            selected[key] = (priority, item)
        else:
            skipped.append(item)
    result = []
    for (direction, action), (_priority, source) in sorted(selected.items()):
        result.append({**source, "direction": direction, "action": action,
                       "cg_direction": source["direction"], "cg_action": source["action"]})
    return result, skipped


def build(root: Path, set_name: str, anime_number: int, target_data: Path, output: Path, sprite: int, palette_number: int) -> None:
    graphic_index, graphic_data, anime_index, anime_data = paths(root, set_name)
    cg_colours = cg_palette(root / "pal" / f"palet_{palette_number:02d}.cgp")
    target_colours = palette(target_data / "pal" / "Palet_1.sap")
    native = resources(target_data)
    template_start, template_end = sprite_range(native["spradrn"], native["spr"], 100330)
    template_bitmap = actions(native["spr"], template_start, template_end)[0]["frames"][0]["bitmap"]
    adrn_template, template_offset, template_size = adrn_record(native["adrn"], template_bitmap)
    real_reserved = read_at(native["real"], template_offset, template_size)[3]
    template_flags = next(flags for number, _offset, flags in struct.iter_unpack("<III", native["spradrn"].read_bytes()) if number == 100330)

    raw_actions = read_animation(anime_index, anime_data, anime_number)
    source_actions, skipped_actions = stoneage_actions(raw_actions)
    if not source_actions:
        raise ValueError("animation has no actions supported by the Stone Age mapping")
    frame_records: list[tuple[int, bytes, bytes]] = []
    rendered: dict[tuple[int, int, int], int] = {}
    lookup: dict[tuple[int, int, int], int] = {}
    animation = bytearray()
    next_frame = sprite * 1000
    real_offset = 0
    for item in source_actions:
        animation.extend(struct.pack("<HHII", item["direction"], item["action"], item["duration"], len(item["frames"])))
        for frame in item["frames"]:
            key = (frame["graphic"], frame["x"], frame["y"])
            bitmap = rendered.get(key)
            if bitmap is None:
                image, info = read_graphic(graphic_index, graphic_data, frame["graphic"], cg_colours)
                bitmap = next_frame
                adrn, real = stone_frame(image, (info.x + frame["x"], info.y + frame["y"]), target_colours, bitmap, real_offset, lookup, adrn_template, real_reserved)
                frame_records.append((bitmap, adrn, real))
                rendered[key] = bitmap
                next_frame += 1
                real_offset += len(real)
            animation.extend(struct.pack("<IhhH", bitmap, 0, 0, frame["flag"]))

    manifest = {
        "format": FORMAT, "version": VERSION, "sprite": sprite,
        "frame_count": len(frame_records), "sprite_sha256": sha(bytes(animation)),
        "conversion_revision": 2,
        "source": {"client": str(root), "set": set_name, "anime": anime_number, "palette": palette_number},
        "direction_mapping": CG_TO_SA_DIRECTION,
        "action_mapping": CG_TO_SA_ACTION,
        "selected_actions": len(source_actions),
        "skipped_actions": [{"direction": item["direction"], "action": item["action"]} for item in skipped_actions],
        "frames": [],
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sprite/index.bin", struct.pack("<III", sprite, 0, template_flags))
        archive.writestr("sprite/data.bin", animation)
        for number, adrn, real in frame_records:
            manifest["frames"].append({"number": number, "adrn_sha256": sha(adrn), "real_sha256": sha(real), "size": len(real)})
            archive.writestr(f"frames/{number}.adrn", adrn)
            archive.writestr(f"frames/{number}.real", real)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"created {output} from CrossGate animation {anime_number}: {len(source_actions)} actions, {len(frame_records)} frames")


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect or convert CrossGate animations for Stone Age 2.5")
    sub = parser.add_subparsers(dest="command", required=True)
    listing = sub.add_parser("list")
    listing.add_argument("--cg", type=Path, required=True)
    listing.add_argument("--set", choices=SETS, required=True)
    listing.add_argument("--limit", type=int, default=50)
    convert = sub.add_parser("convert")
    convert.add_argument("anime", type=int)
    convert.add_argument("--cg", type=Path, required=True)
    convert.add_argument("--set", choices=SETS, required=True)
    convert.add_argument("--data", type=Path, required=True)
    convert.add_argument("--output", type=Path, required=True)
    convert.add_argument("--sprite", type=int, required=True)
    convert.add_argument("--palette", type=int, choices=range(16), default=0)
    args = parser.parse_args()
    if args.command == "list":
        _gi, _gd, ai, _ad = paths(args.cg, args.set)
        for number, _offset, count in anime_rows(ai)[:args.limit]:
            print(f"{number}\t{count}")
    else:
        if not 100000 <= args.sprite <= 132767:
            raise ValueError("Stone Age sprite number must be 100000..132767")
        build(args.cg, args.set, args.anime, args.data, args.output, args.sprite, args.palette)


if __name__ == "__main__":
    main()
