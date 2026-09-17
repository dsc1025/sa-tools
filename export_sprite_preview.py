#!/usr/bin/env python3
"""Export one Stone Age 2.5 sprite action as transparent PNG frames.

Example:
  python export_sprite_preview.py 100250 --direction 0 --action 0
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

from sa_resource import palette, png_rgba, read_image


ROOT = Path(r"C:\Work\SA\SA2.5")
DATA = ROOT / "stoneage2.5" / "data"


def sprite_range(index_path: Path, sprite_path: Path, image_number: int) -> tuple[int, int]:
    raw = index_path.read_bytes()
    records = list(struct.iter_unpack("<III", raw))
    for index, (number, offset, _meta) in enumerate(records):
        if number == image_number:
            end = records[index + 1][1] if index + 1 < len(records) else sprite_path.stat().st_size
            return offset, end
    raise ValueError(f"Sprite {image_number} does not exist in {index_path.name}")


def actions(sprite_path: Path, start: int, end: int) -> list[dict]:
    with sprite_path.open("rb") as file:
        file.seek(start)
        block = file.read(end - start)
    result = []
    cursor = 0
    while cursor < len(block):
        direction, action, duration, count = struct.unpack_from("<HHII", block, cursor)
        cursor += 12
        frames = []
        for _ in range(count):
            bitmap, x, y, sound = struct.unpack_from("<IhhH", block, cursor)
            cursor += 10
            frames.append({"bitmap": bitmap, "x": x, "y": y, "sound": sound})
        result.append({"direction": direction, "action": action, "duration": duration, "frames": frames})
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Export an individual 2.5 sprite action as PNG frames.")
    parser.add_argument("sprite", type=int, help="sprite/character image number")
    parser.add_argument("--direction", type=int, default=0)
    parser.add_argument("--action", type=int, default=0)
    parser.add_argument("--data", type=Path, default=DATA)
    parser.add_argument("--palette", type=int, choices=range(16), default=1, help="palette number; 1 is the normal daytime palette")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    output = args.output or (Path(__file__).parent / "exports" / f"sprite_{args.sprite}" / f"dir_{args.direction}_action_{args.action}")
    start, end = sprite_range(args.data / "spradrn_5.bin", args.data / "spr_4.bin", args.sprite)
    selected = next((item for item in actions(args.data / "spr_4.bin", start, end) if item["direction"] == args.direction and item["action"] == args.action), None)
    if selected is None:
        raise ValueError("That direction/action combination is not present for this sprite")
    output.mkdir(parents=True, exist_ok=True)
    palettes: dict[int, list[tuple[int, int, int]]] = {}
    manifest = {"sprite": args.sprite, "direction": args.direction, "action": args.action, "palette": args.palette, "duration": selected["duration"], "frames": []}
    for index, frame in enumerate(selected["frames"]):
        info, pixels = read_image(args.data / "adrn_15.bin", args.data / "real_15.bin", frame["bitmap"])
        colours = palettes.setdefault(args.palette, palette(args.data / "pal" / f"Palet_{args.palette}.sap"))
        filename = f"frame_{index:02d}_bitmap_{info.number}.png"
        png_rgba(output / filename, info.width, info.height, pixels, colours)
        manifest["frames"].append({**frame, "adrn_x": info.x, "adrn_y": info.y, "width": info.width, "height": info.height, "file": filename})
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Exported {len(selected['frames'])} frames to {output}")


if __name__ == "__main__":
    main()
