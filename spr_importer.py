"""Transactional importer for one custom Stone Age .spr resource package."""
from __future__ import annotations

import json
import struct
import zipfile
from pathlib import Path

from client_data import resources
from spr_package import FORMAT, VERSION, adrn_record, digest, read_at


def _package(path: Path) -> tuple[dict, bytes, bytes, dict[int, tuple[bytes, bytes]]]:
    if path.suffix.lower() != ".spr":
        raise ValueError("导入只接受 .spr 文件")
    with zipfile.ZipFile(path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
        if manifest.get("format") != FORMAT or manifest.get("version") != VERSION:
            raise ValueError("不是本工具导出的 .spr 资源包")
        sprite_index = archive.read("sprite/index.bin")
        sprite_data = archive.read("sprite/data.bin")
        if len(sprite_index) != 12 or digest(sprite_data) != manifest.get("sprite_sha256"):
            raise ValueError("动画资源校验失败")
        frames: dict[int, tuple[bytes, bytes]] = {}
        for item in manifest["frames"]:
            number = int(item["number"])
            index = archive.read(f"frames/{number}.adrn")
            real = archive.read(f"frames/{number}.real")
            if len(index) != 80 or real[:2] != b"RD" or digest(index) != item["adrn_sha256"] or digest(real) != item["real_sha256"]:
                raise ValueError(f"图片帧 {number} 校验失败")
            frames[number] = (index, real)
    return manifest, sprite_index, sprite_data, frames


def _existing_frame(adrn: Path, real: Path, number: int, source_index: bytes, source_real: bytes) -> bool:
    try:
        target_index, offset, size = adrn_record(adrn, number)
        target_real = read_at(real, offset, size)
        return target_index == source_index and target_real == source_real
    except ValueError:
        return False


def _rewrite_frame_references(block: bytes, mapping: dict[int, int]) -> bytes:
    result = bytearray(block)
    cursor = 0
    while cursor < len(result):
        if cursor + 12 > len(result):
            raise ValueError("动画块不完整")
        _direction, _action, _duration, count = struct.unpack_from("<HHII", result, cursor)
        cursor += 12
        if cursor + count * 10 > len(result):
            raise ValueError("动画帧超出动画块边界")
        for _ in range(count):
            source_number = struct.unpack_from("<I", result, cursor)[0]
            if source_number not in mapping:
                raise ValueError(f"动画引用了未打包的图片帧 {source_number}")
            struct.pack_into("<I", result, cursor, mapping[source_number])
            cursor += 10
    return bytes(result)


def _normalize_cg_attack_events(block: bytes) -> tuple[bytes, int]:
    """Set earlier marked attack frames to 10100 and the last to 10000."""
    result = bytearray(block)
    cursor = 0
    changed = 0
    while cursor < len(result):
        if cursor + 12 > len(result):
            raise ValueError("动画块不完整")
        _direction, action, _duration, count = struct.unpack_from("<HHII", result, cursor)
        cursor += 12
        if cursor + count * 10 > len(result):
            raise ValueError("动画帧超出动画块边界")
        event_offsets: list[int] = []
        for _ in range(count):
            event_offset = cursor + 8
            if action == 0 and struct.unpack_from("<H", result, event_offset)[0] != 0:
                event_offsets.append(event_offset)
            cursor += 10
        for index, event_offset in enumerate(event_offsets):
            value = 10000 if index == len(event_offsets) - 1 else 10100
            if struct.unpack_from("<H", result, event_offset)[0] != value:
                struct.pack_into("<H", result, event_offset, value)
                changed += 1
    return bytes(result), changed


def filename_image_number(package: Path) -> int:
    if package.suffix.lower() != ".spr" or not package.stem.isascii() or not package.stem.isdecimal():
        raise ValueError("资源包文件名必须是数字，例如 101819.spr")
    number = int(package.stem)
    if not 100000 <= number <= 132767:
        raise ValueError("此客户端的形象编号必须在 100000～132767 之间")
    return number


def sprite_exists(target_data: Path, number: int) -> bool:
    index = resources(target_data)["spradrn"].read_bytes()
    return any(row[0] == number for row in struct.iter_unpack("<III", index))


def delete_sprites(target_data: Path, numbers: list[int]) -> dict:
    """Remove sprite index rows while preserving shared frame data.

    Sprite animation and image records are append-only and may be shared by
    other sprites. Removing only the selected spradrn rows is therefore the
    safe reversible operation; the now-unreferenced bytes remain in the client
    files and can be reclaimed later by a separate compaction tool.
    """
    files = resources(target_data)
    requested = list(dict.fromkeys(numbers))
    if not requested:
        raise ValueError("没有选择要删除的形象")
    original_index = files["spradrn"].read_bytes()
    rows = list(struct.iter_unpack("<III", original_index))
    existing = {row[0] for row in rows}
    missing = [number for number in requested if number not in existing]
    if missing:
        raise ValueError("形象编号不存在：" + "、".join(str(number) for number in missing))
    updated_rows = [row for row in rows if row[0] not in requested]
    report = {
        "status": "deleted",
        "deleted_sprites": requested,
        "removed_rows": len(rows) - len(updated_rows),
        "spradrn": str(files["spradrn"]),
        "original_size": len(original_index),
    }
    try:
        files["spradrn"].write_bytes(b"".join(struct.pack("<III", *row) for row in updated_rows))
    except Exception:
        files["spradrn"].write_bytes(original_index)
        raise
    return report


def import_package(package: Path, target_data: Path, *, overwrite: bool = False) -> dict:
    """Import a .spr resource with append-only writes and a rollback manifest."""
    manifest, source_sprite_index, source_sprite_data, frames = _package(package)
    files = resources(target_data)
    spr_index = files["spradrn"].read_bytes()
    spr_rows = list(struct.iter_unpack("<III", spr_index))
    source_sprite = int(manifest["sprite"])
    target_sprite = filename_image_number(package)
    existing_sprite = next((row for row in spr_rows if row[0] == target_sprite), None)
    if existing_sprite is not None and not overwrite:
        raise FileExistsError(f"形象编号 {target_sprite} 已存在，需要确认覆盖")

    record_count = files["adrn"].stat().st_size // 80
    frame_mapping: dict[int, int] = {}
    new_frames: list[tuple[int, int, bytes, bytes]] = []
    next_number = record_count
    reused = 0
    for source_number, (source_index, source_real) in sorted(frames.items()):
        if _existing_frame(files["adrn"], files["real"], source_number, source_index, source_real):
            frame_mapping[source_number] = source_number
            reused += 1
        else:
            frame_mapping[source_number] = next_number
            new_frames.append((source_number, next_number, source_index, source_real))
            next_number += 1

    source = manifest.get("source", {})
    is_cg_package = isinstance(source, dict) and {"client", "set", "anime", "palette"}.issubset(source)
    normalized_attack_events = 0
    if is_cg_package:
        source_sprite_data, normalized_attack_events = _normalize_cg_attack_events(source_sprite_data)
    sprite_data = _rewrite_frame_references(source_sprite_data, frame_mapping)
    original_sizes = {name: path.stat().st_size for name, path in files.items()}
    report = {
        "status": "imported",
        "source_sprite": source_sprite,
        "target_sprite": target_sprite,
        "overwritten": existing_sprite is not None,
        "frames_added": len(new_frames),
        "frames_reused": reused,
        "attack_events_normalized": normalized_attack_events,
        "original_sizes": original_sizes,
        "frame_mapping": frame_mapping,
        "package": str(package),
    }
    try:
        real_offset = original_sizes["real"]
        with files["real"].open("ab") as real_file, files["adrn"].open("r+b") as adrn_file:
            for _source, target_number, source_index, source_real in new_frames:
                real_file.write(source_real)
                patched = bytearray(source_index)
                struct.pack_into("<III", patched, 0, target_number, real_offset, len(source_real))
                adrn_file.seek(target_number * 80)
                adrn_file.write(patched)
                real_offset += len(source_real)
        with files["spr"].open("ab") as sprite_file:
            sprite_file.write(sprite_data)
        _old_number, _old_offset, flags = struct.unpack("<III", source_sprite_index)
        updated_rows = [row for row in spr_rows if row[0] != target_sprite]
        updated_rows.append((target_sprite, original_sizes["spr"], flags))
        updated_rows.sort(key=lambda row: row[0])
        files["spradrn"].write_bytes(b"".join(struct.pack("<III", *row) for row in updated_rows))
    except Exception:
        for name, path in files.items():
            with path.open("r+b") as file:
                file.truncate(original_sizes[name])
                if name == "spradrn":
                    file.seek(0)
                    file.write(spr_index)
        raise
    return report
