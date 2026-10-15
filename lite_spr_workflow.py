"""Export filtered sprite packages and build a separate LITE resource set."""
from __future__ import annotations

import json
import shutil
import struct
import tempfile
import zipfile
from pathlib import Path

from client_data import resources
from spr_importer import _package
from spr_package import FORMAT, VERSION, adrn_record, digest, read_at


SPR_DIRECTORY = Path(__file__).resolve().parent / "spr"


def build_lite(source: Path, report, limit: int | None = 10) -> dict:
    data = source.expanduser().resolve() / "data"
    files = resources(data)
    rows = list(struct.iter_unpack("<III", files["spradrn"].read_bytes()))
    rows = rows[:limit] if limit is not None else rows
    if not rows:
        raise ValueError("源形象索引为空")
    if len({row[0] for row in rows}) != len(rows):
        raise ValueError("本批形象编号重复，不能生成同名 SPR 包")
    SPR_DIRECTORY.mkdir(exist_ok=True)
    output = SPR_DIRECTORY.parent / "data"
    if output.resolve() == data.resolve():
        raise ValueError("源 data 不能与生成目录相同，请选择原始客户端目录")
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"输出路径不是目录：{output}")
    packages = []
    source_size = files["spr"].stat().st_size
    for index, (number, offset, flags) in enumerate(rows, 1):
        report(50 * (index - 1) / len(rows), f"正在导出 SPR {number}（{index}/{len(rows)}），源偏移 {offset}")
        blocks = []
        images = set()
        pairs = set()
        with files["spr"].open("rb") as stream:
            if offset > source_size:
                raise ValueError(f"SPR {number}：源动画偏移越界")
            stream.seek(offset)
            for _ in range(flags & 0xFFFF):
                header = stream.read(12)
                if len(header) != 12:
                    raise ValueError(f"SPR {number}：动画头不完整")
                direction, action, _duration, count = struct.unpack("<HHII", header)
                if count * 10 > source_size - stream.tell():
                    raise ValueError(f"SPR {number}：方向 {direction} 动作 {action} 帧越界")
                if direction not in (3, 7) or action not in (0, 3):
                    stream.seek(count * 10, 1)
                    continue
                if not count or (direction, action) in pairs:
                    raise ValueError(f"SPR {number}：方向 {direction} 动作 {action} 无帧或重复")
                pairs.add((direction, action))
                frames = stream.read(count * 10)
                bitmaps = [row[0] for row in struct.iter_unpack("<IhhH", frames)]
                if 0xFFFFFFFF in bitmaps:
                    report(50 * (index - 1) / len(rows), f"SPR {number} 方向 {direction} 动作 {action}：保留原有 0xFFFFFFFF 帧标记")
                images.update(bitmap for bitmap in bitmaps if bitmap != 0xFFFFFFFF)
                blocks.append(header + frames)
        if not blocks:
            report(50 * index / len(rows), f"跳过 SPR {number}：无匹配方向和动作")
            continue
        sprite = b"".join(blocks)
        manifest = {"format": FORMAT, "version": VERSION, "sprite": number,
                    "frame_count": len(images), "sprite_sha256": digest(sprite),
                    "source": {key: path.name for key, path in files.items()}, "frames": []}
        target = SPR_DIRECTORY / f"{number}.spr"
        with tempfile.TemporaryDirectory(prefix="export-", dir=SPR_DIRECTORY) as temporary:
            staged = Path(temporary) / target.name
            with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                archive.writestr("sprite/index.bin", struct.pack("<III", number, 0, (flags & 0xFFFF0000) | len(blocks)))
                archive.writestr("sprite/data.bin", sprite)
                for image_index, bitmap in enumerate(sorted(images), 1):
                    report(50 * (index - 1 + image_index / max(1, len(images))) / len(rows),
                           f"导出 SPR {number}：图片 {bitmap}（{image_index}/{len(images)}）")
                    try:
                        record, real_offset, size = adrn_record(files["adrn"], bitmap)
                        block = read_at(files["real"], real_offset, size)
                        if block[:2] != b"RD":
                            raise ValueError("REAL 图片标识不是 RD")
                    except Exception as error:
                        raise ValueError(f"SPR {number}，图片 {bitmap}：{error}") from error
                    archive.writestr(f"frames/{bitmap}.adrn", record)
                    archive.writestr(f"frames/{bitmap}.real", block)
                    manifest["frames"].append({"number": bitmap, "size": size,
                                               "adrn_sha256": digest(record), "real_sha256": digest(block)})
                archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
            staged.replace(target)
        packages.append(target)
        report(50 * index / len(rows), f"已导出 {target}，{len(blocks)} 条动作，{len(images)} 张图片")
    if not packages:
        raise ValueError("本批没有可导出的形象")
    report(50, "本批 SPR 导出完成，开始仅从这些包生成新 BIN")
    image_keys = {}
    with tempfile.TemporaryDirectory(prefix="build-", dir=SPR_DIRECTORY) as temporary:
        staged = Path(temporary) / "data"
        staged.mkdir()
        with (staged / files["spr"].name).open("wb") as spr, \
                (staged / files["spradrn"].name).open("wb") as spradrn, \
                (staged / files["adrn"].name).open("wb") as adrn, \
                (staged / files["real"].name).open("wb") as real:
            for index, package in enumerate(packages, 1):
                report(50 + 45 * (index - 1) / len(packages), f"正在重建 SPR {package.stem}（{index}/{len(packages)}）")
                manifest, sprite_index, sprite, frames = _package(package)
                mapping = {}
                for bitmap, (record, block) in frames.items():
                    key = (record[12:], digest(block))
                    if key not in image_keys:
                        image_number = len(image_keys)
                        if real.tell() > 0xFFFFFFFF:
                            raise ValueError("REAL 偏移超出 32 位范围")
                        patched = bytearray(record)
                        struct.pack_into("<III", patched, 0, image_number, real.tell(), len(block))
                        adrn.write(patched)
                        real.write(block)
                        image_keys[key] = image_number
                    mapping[bitmap] = image_keys[key]
                patched = bytearray(sprite)
                cursor = 0
                _number, _offset, flags = struct.unpack("<III", sprite_index)
                for _ in range(flags & 0xFFFF):
                    if cursor + 12 > len(patched):
                        raise ValueError(f"{package.name}：动画头不完整")
                    count = struct.unpack_from("<I", patched, cursor + 8)[0]
                    cursor += 12
                    if cursor + count * 10 > len(patched):
                        raise ValueError(f"{package.name}：帧越界")
                    for _ in range(count):
                        bitmap = struct.unpack_from("<I", patched, cursor)[0]
                        if bitmap != 0xFFFFFFFF:
                            if bitmap not in mapping:
                                raise ValueError(f"{package.name}：图片 {bitmap} 未打包")
                            struct.pack_into("<I", patched, cursor, mapping[bitmap])
                        cursor += 10
                if cursor != len(patched) or spr.tell() > 0xFFFFFFFF:
                    raise ValueError(f"{package.name}：动画长度或偏移无效")
                spradrn.write(struct.pack("<III", int(manifest["sprite"]), spr.tell(), flags))
                spr.write(patched)
        if (data / "pal").is_dir():
            shutil.copytree(data / "pal", staged / "pal")
        real_size = (staged / files["real"].name).stat().st_size
        output.mkdir(exist_ok=True)
        for path in files.values():
            report(95, f"写入生成文件：{output / path.name}（同名文件覆盖）")
            (staged / path.name).replace(output / path.name)
        if (staged / "pal").is_dir():
            shutil.copytree(staged / "pal", output / "pal", dirs_exist_ok=True)
    return {"packages": len(packages), "images": len(image_keys), "real_size": real_size, "output": output}


def summary(result: dict) -> str:
    return (f"SPR 导出目录：{SPR_DIRECTORY}\n独立 BIN 目录：{result['output']}\n"
            f"形象 {result['packages']} 个，去重后图片 {result['images']} 张，REAL {result['real_size']:,} 字节\n"
            "源客户端未修改；请用查看器打开独立 BIN 目录验证。")
