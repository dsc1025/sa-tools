#!/usr/bin/env python3
"""List Stone Age 2.5 animation resources without changing client data."""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path


DEFAULT_ROOT = Path(r"C:\Work\SA\SA2.5")
DEFAULT_DATA = DEFAULT_ROOT / "stoneage2.5" / "data"
DEFAULT_ENEMYBASE = Path(r"C:\Work\SA\sa-server\2.5\gmsv\data\enemybase.txt")


def read_sprite_index(index_path: Path) -> list[tuple[int, int, int, int]]:
    data = index_path.read_bytes()
    if len(data) % 12:
        raise ValueError(f"{index_path} is {len(data)} bytes; expected 12-byte records")
    return [
        (record, image_number, sprite_offset, flags)
        for record, (image_number, sprite_offset, flags) in enumerate(
            struct.iter_unpack("<III", data)
        )
    ]


def read_enemy_links(enemybase_path: Path, image_numbers: set[int]) -> list[tuple[str, int, int]]:
    if not enemybase_path.exists():
        return []
    # Existing server data is GBK/CP936.
    lines = enemybase_path.read_text(encoding="gbk", errors="replace").splitlines()
    links: set[tuple[str, int, int]] = set()
    for line_number, line in enumerate(lines, 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [value.strip() for value in line.split(",")]
        for field in fields:
            try:
                value = int(field)
            except ValueError:
                continue
            if value in image_numbers:
                links.add((fields[0], value, line_number))
    return sorted(links, key=lambda row: (row[1], row[0], row[2]))


def write_csv(path: Path, header: list[str], rows: list[tuple]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(header)
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="List 2.5 sprite index records and server links.")
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA, help="2.5 client data directory")
    parser.add_argument("--enemybase", type=Path, default=DEFAULT_ENEMYBASE, help="server enemybase.txt")
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "resource-list")
    args = parser.parse_args()

    index_path = args.data / "spradrn_5.bin"
    records = read_sprite_index(index_path)
    image_numbers = {row[1] for row in records}
    links = read_enemy_links(args.enemybase, image_numbers)
    args.output.mkdir(parents=True, exist_ok=True)

    write_csv(
        args.output / "all-sprite-images.csv",
        ["index_record", "image_number", "sprite_offset", "record_flags"],
        sorted(((r, n, o, f"0x{flags:08X}") for r, n, o, flags in records), key=lambda row: row[1]),
    )
    write_csv(args.output / "server-enemy-image-links.csv", ["enemy_name", "image_number", "enemybase_line"], links)
    summary = [
        "Stone Age 2.5 resource list",
        f"Animation index: {index_path}",
        f"Index records: {len(records)}",
        f"Unique image numbers: {len(image_numbers)}",
        f"Range: {min(image_numbers)} - {max(image_numbers)}",
        f"Server enemybase links: {len(links)}",
        "",
        "spradrn contains pets, NPCs, players, mounts, and effects; it has no pet flag.",
        "enemybase links show resources used by current server data, not a guaranteed catchable-pet list.",
    ]
    (args.output / "summary.txt").write_text("\n".join(summary) + "\n", encoding="utf-8-sig")
    print(f"Listed {len(records)} animation records ({len(image_numbers)} unique image numbers).")
    print(f"Server enemybase links: {len(links)}.")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
