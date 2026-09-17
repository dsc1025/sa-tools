#!/usr/bin/env python3
"""Inspect a Stone Age 2.5 animation block from spradrn_5.bin and spr_4.bin.

This is an analysis tool. It reads only the sprite index and animation package.
Each decoded frame's first value is the image reference that will next be looked
up in adrn_15.bin.
"""

from __future__ import annotations

import argparse
import struct
from pathlib import Path


DATA = Path(r"C:\Work\SA\SA2.5\stoneage2.5\data")


def index_records(path: Path) -> list[tuple[int, int, int]]:
    data = path.read_bytes()
    if len(data) % 12:
        raise ValueError("spradrn index is not aligned to 12-byte records")
    return list(struct.iter_unpack("<III", data))


def inspect(image_number: int, index_path: Path, sprite_path: Path) -> tuple[list[tuple[int, int, int]], int, int]:
    records = index_records(index_path)
    record_index = next((i for i, row in enumerate(records) if row[0] == image_number), None)
    if record_index is None:
        raise ValueError(f"Image number {image_number} is not in {index_path.name}")
    start = records[record_index][1]
    end = records[record_index + 1][1] if record_index + 1 < len(records) else sprite_path.stat().st_size
    if end < start:
        raise ValueError("Sprite offsets are not sorted; this inspector requires sorted records")
    return records, start, end


def main() -> None:
    parser = argparse.ArgumentParser(description="Print actions and frame image references for one 2.5 character animation.")
    parser.add_argument("image_number", type=int, help="client character image number, e.g. 100250")
    parser.add_argument("--data", type=Path, default=DATA)
    args = parser.parse_args()
    index_path = args.data / "spradrn_5.bin"
    sprite_path = args.data / "spr_4.bin"
    _records, start, end = inspect(args.image_number, index_path, sprite_path)
    block = sprite_path.read_bytes()[start:end]
    cursor = 0
    action_index = 0
    print(f"Image number: {args.image_number}")
    print(f"spr_4.bin range: {start}..{end} ({len(block)} bytes)")
    while cursor < len(block):
        if len(block) - cursor < 12:
            raise ValueError(f"Trailing {len(block) - cursor} bytes at offset {cursor}")
        action_group, action_code, duration, frame_count = struct.unpack_from("<HHII", block, cursor)
        cursor += 12
        byte_count = frame_count * 10
        if cursor + byte_count > len(block):
            raise ValueError(f"Action {action_index} declares {frame_count} frames outside its block")
        frames = list(struct.iter_unpack("<IHHH", block[cursor:cursor + byte_count]))
        cursor += byte_count
        image_refs = [frame[0] for frame in frames]
        print(
            f"action={action_index:02d} code=0x{action_code:08X} duration={duration} "
            f"frames={frame_count} adrn_refs={min(image_refs)}..{max(image_refs)}"
        )
        action_index += 1
    print(f"Actions: {action_index}; referenced adrn frames: {sum(1 for _ in [])}")


if __name__ == "__main__":
    main()
