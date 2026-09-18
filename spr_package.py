#!/usr/bin/env python3
"""Export one character sprite resource to a portable .spr package and validate imports.

A .spr package is a ZIP container with a strict manifest.  It contains exactly
one sprite/character image number and every ADRN/REAL block used by it.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import zipfile
from pathlib import Path

from client_data import resources
from export_sprite_preview import actions, sprite_range


FORMAT = "stoneage-single-sprite"
VERSION = 1


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def read_at(path: Path, offset: int, size: int) -> bytes:
    with path.open("rb") as file:
        file.seek(offset)
        result = file.read(size)
    if len(result) != size:
        raise ValueError(f"Unexpected end of {path.name}")
    return result


def adrn_record(path: Path, number: int) -> tuple[bytes, int, int]:
    raw = read_at(path, number * 80, 80)
    image_number, offset, size = struct.unpack_from("<III", raw)
    if image_number != number or not size:
        raise ValueError(f"Image frame {number} is not valid in {path.name}")
    return raw, offset, size


def package_frames(sprite_actions: list[dict]) -> list[int]:
    return sorted({frame["bitmap"] for action in sprite_actions for frame in action["frames"]})


def export_package(source_data: Path, sprite: int, output: Path) -> None:
    if output.suffix.lower() != ".spr":
        raise ValueError("Export filename must end with .spr")
    files = resources(source_data)
    start, end = sprite_range(files["spradrn"], files["spr"], sprite)
    sprite_block = read_at(files["spr"], start, end - start)
    sprite_index = read_at(files["spradrn"], next(i * 12 for i, row in enumerate(struct.iter_unpack("<III", files["spradrn"].read_bytes())) if row[0] == sprite), 12)
    sprite_actions = actions(files["spr"], start, end)
    frames = package_frames(sprite_actions)
    manifest = {
        "format": FORMAT,
        "version": VERSION,
        "sprite": sprite,
        "frame_count": len(frames),
        "sprite_sha256": digest(sprite_block),
        "source": {name: path.name for name, path in files.items()},
        "frames": [],
    }
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("sprite/index.bin", sprite_index)
        archive.writestr("sprite/data.bin", sprite_block)
        for number in frames:
            index, offset, size = adrn_record(files["adrn"], number)
            real = read_at(files["real"], offset, size)
            if real[:2] != b"RD":
                raise ValueError(f"Frame {number} has an invalid REAL header")
            manifest["frames"].append({"number": number, "adrn_sha256": digest(index), "real_sha256": digest(real), "size": size})
            archive.writestr(f"frames/{number}.adrn", index)
            archive.writestr(f"frames/{number}.real", real)
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
    print(f"SUCCESS: exported sprite {sprite} with {len(frames)} image frames")
    print(f"PACKAGE: {output}")


def validate_package(package: Path, target_data: Path) -> int:
    if package.suffix.lower() != ".spr":
        print("FAIL: import accepts only .spr files")
        return 2
    try:
        with zipfile.ZipFile(package) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
                raise ValueError("unsupported .spr package format")
            sprite = int(manifest["sprite"])
            sprite_data = archive.read("sprite/data.bin")
            if digest(sprite_data) != manifest["sprite_sha256"]:
                raise ValueError("sprite data checksum mismatch")
            if len(manifest["frames"]) != manifest["frame_count"]:
                raise ValueError("frame count does not match manifest")
            files = resources(target_data)
            target_index = files["spradrn"].read_bytes()
            existing_sprite = next((row for row in struct.iter_unpack("<III", target_index) if row[0] == sprite), None)
            conflicts: list[str] = []
            existing: list[str] = []
            if existing_sprite is not None:
                start = existing_sprite[1]
                start, end = sprite_range(files["spradrn"], files["spr"], sprite)
                if read_at(files["spr"], start, end - start) == sprite_data:
                    existing.append(f"sprite {sprite} is already identical")
                else:
                    conflicts.append(f"sprite number {sprite} already exists with different animation data")
            for frame in manifest["frames"]:
                number = int(frame["number"])
                try:
                    current_index, offset, size = adrn_record(files["adrn"], number)
                    current_real = read_at(files["real"], offset, size)
                    if digest(current_index) == frame["adrn_sha256"] and digest(current_real) == frame["real_sha256"]:
                        existing.append(f"frame {number} is already identical")
                    else:
                        conflicts.append(f"frame number {number} already exists with different image data")
                except ValueError:
                    pass
    except (OSError, ValueError, KeyError, json.JSONDecodeError, zipfile.BadZipFile) as error:
        print(f"FAIL: {error}")
        return 2
    for message in existing[:5]:
        print(f"WARNING: {message}")
    if len(existing) > 5:
        print(f"WARNING: {len(existing) - 5} additional identical resources")
    for message in conflicts[:5]:
        print(f"CONFLICT: {message}")
    if len(conflicts) > 5:
        print(f"CONFLICT: {len(conflicts) - 5} additional conflicts")
    if conflicts:
        print("FAIL: import blocked; choose new numbers or resolve the listed conflicts")
        return 1
    if existing:
        print("SUCCESS: package is valid; identical resources require no import")
    else:
        print("SUCCESS: package is valid and has no target-number conflicts")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Export one Stone Age sprite as .spr or validate a .spr import.")
    command = parser.add_subparsers(dest="command", required=True)
    export = command.add_parser("export")
    export.add_argument("sprite", type=int)
    export.add_argument("--data", type=Path, required=True, help="source client data directory")
    export.add_argument("--output", type=Path, required=True)
    validate = command.add_parser("validate")
    validate.add_argument("package", type=Path)
    validate.add_argument("--data", type=Path, required=True, help="target client data directory")
    args = parser.parse_args()
    if args.command == "export":
        export_package(args.data, args.sprite, args.output)
    else:
        sys.exit(validate_package(args.package, args.data))


if __name__ == "__main__":
    main()
