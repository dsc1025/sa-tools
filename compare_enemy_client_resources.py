#!/usr/bin/env python3
"""Compare server enemy templates against 2.5 and 8.0 client animation indexes."""

from __future__ import annotations

import argparse
import csv
import struct
from pathlib import Path


ROOT = Path(r"C:\Work\SA")
DEFAULT_ENEMYBASE = ROOT / "sa-server" / "2.5" / "gmsv" / "data" / "enemybase.txt"
DEFAULT_25_INDEX = ROOT / "SA2.5" / "stoneage2.5" / "data" / "spradrn_5.bin"
DEFAULT_80_INDEX = ROOT / "SA8.0" / "data" / "spradrn_115.bin"

# Confirmed from the server's enemybase format: column 6 is the template ID;
# column 36 is the client base image/animation number.
TEMPLATE_ID_COLUMN = 6
IMAGE_NUMBER_COLUMN = 36


def sprite_ids(path: Path) -> set[int]:
    data = path.read_bytes()
    if len(data) % 12:
        raise ValueError(f"Unexpected index size: {path}")
    return {image_number for image_number, _offset, _flags in struct.iter_unpack("<III", data)}


def enemybase_rows(path: Path) -> list[tuple[int, str, str, int]]:
    rows: list[tuple[int, str, str, int]] = []
    for line_number, line in enumerate(path.read_text(encoding="gbk", errors="replace").splitlines(), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        fields = [value.strip() for value in line.split(",")]
        if len(fields) <= IMAGE_NUMBER_COLUMN:
            continue
        try:
            image_number = int(fields[IMAGE_NUMBER_COLUMN])
        except ValueError:
            continue
        rows.append((line_number, fields[0], fields[TEMPLATE_ID_COLUMN], image_number))
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Find server enemy graphics missing from the 2.5 client.")
    parser.add_argument("--enemybase", type=Path, default=DEFAULT_ENEMYBASE)
    parser.add_argument("--client25-index", type=Path, default=DEFAULT_25_INDEX)
    parser.add_argument("--client80-index", type=Path, default=DEFAULT_80_INDEX)
    parser.add_argument("--output", type=Path, default=Path(__file__).parent / "resource-list" / "enemybase-client-comparison.csv")
    args = parser.parse_args()

    ids_25 = sprite_ids(args.client25_index)
    ids_80 = sprite_ids(args.client80_index)
    output_rows = []
    for line, name, template_id, image_number in enemybase_rows(args.enemybase):
        present_25 = image_number in ids_25
        present_80 = image_number in ids_80
        output_rows.append((line, name, template_id, image_number, "yes" if present_25 else "no", "yes" if present_80 else "no"))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file)
        writer.writerow(["enemybase_line", "template_name", "template_id", "image_number", "in_client_2.5", "in_client_8.0"])
        writer.writerows(output_rows)

    missing_25 = [row for row in output_rows if row[4] == "no"]
    importable = [row for row in missing_25 if row[5] == "yes"]
    print(f"Server enemybase templates checked: {len(output_rows)}")
    print(f"Missing from 2.5 client: {len(missing_25)}")
    print(f"Available in 8.0 and importable: {len(importable)}")
    print(f"Output: {args.output}")


if __name__ == "__main__":
    main()
