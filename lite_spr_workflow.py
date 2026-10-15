"""Export filtered sprite packages and build a separate LITE resource set."""
from __future__ import annotations

import json
import struct
import tempfile
import zipfile
from pathlib import Path

from client_data import resources
from spr_importer import _package
from spr_package import FORMAT, VERSION, adrn_record, digest, read_at


SPR_DIRECTORY = Path(__file__).resolve().parent / "spr"
BIN_NAMES = {"spradrn": "spradrn_lite.bin", "spr": "spr_lite.bin",
             "adrn": "adrn_lite.bin", "real": "real_lite.bin"}


def export_sprites(source: Path, report, numbers: set[int] | None = None) -> dict:
    data = source.expanduser().resolve() / "data"
    files = resources(data)
    rows = list(struct.iter_unpack("<III", files["spradrn"].read_bytes()))
    occurrences = {}
    for index, (number, offset, flags) in enumerate(rows):
        occurrences.setdefault(number, []).append((index, offset, flags))
    targets = sorted(numbers) if numbers is not None else list(occurrences)
    if not targets:
        raise ValueError("没有需要导出的形象")
    SPR_DIRECTORY.mkdir(exist_ok=True)
    source_size = files["spr"].stat().st_size
    failures = []
    skipped = []
    exported = 0
    for index, number in enumerate(targets, 1):
        entries = occurrences.get(number, [])
        try:
            if not entries:
                raise ValueError(f"索引文件 {files['spradrn']} 中不存在该编号")
            if len(entries) > 1:
                locations = "；".join(
                    f"索引第 {row + 1} 条，字节位置 {row * 12} (0x{row * 12:X})，"
                    f"SPR 偏移 {offset} (0x{offset:X})，动作数 {flags & 0xFFFF}，flags=0x{flags:08X}"
                    for row, offset, flags in entries)
                raise ValueError(f"重复编号，出现 {len(entries)} 次。索引文件 {files['spradrn']}；{locations}")
            _row, offset, flags = entries[0]
            reason = _export_sprite(files, source_size, number, offset, flags, index, len(targets), report)
            if reason is None:
                exported += 1
            else:
                skipped.append((number, reason))
        except Exception as error:
            reason = str(error)
            failures.append((number, reason))
            report(100 * index / len(targets), f"异常跳过 SPR {number}：{reason}")
    report(100, f"导出报告：共 {len(targets)} 个，成功 {exported}，组合缺失跳过 {len(skipped)}，异常 {len(failures)}")
    for number, reason in skipped + failures:
        report(100, f"报告详情：SPR {number}：{reason}")
    if skipped or failures:
        report(100, "注意：跳过的形象未覆盖或删除已有同名 SPR；生成 BIN 前请确认 spr 目录中的旧包是否需要保留。")
    return {"operation": "export", "packages": exported, "output": SPR_DIRECTORY,
            "total": len(targets), "skipped": skipped, "failures": failures}


def _export_sprite(files, source_size, number, offset, flags, index, total, report):
    report(100 * (index - 1) / total, f"正在导出 SPR {number}（{index}/{total}），源偏移 {offset}")
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
                report(100 * (index - 1) / total, f"SPR {number} 方向 {direction} 动作 {action}：保留原有 0xFFFFFFFF 帧标记")
            images.update(bitmap for bitmap in bitmaps if bitmap != 0xFFFFFFFF)
            blocks.append(header + frames)
    missing = {(3, 0), (3, 3), (7, 0), (7, 3)} - pairs
    if missing:
        reason = "缺少必需组合：" + "、".join(f"方向 {direction}/动作 {action}" for direction, action in sorted(missing))
        report(100 * index / total, f"跳过 SPR {number}：{reason}")
        return reason
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
                report(100 * (index - 1 + image_index / max(1, len(images))) / total,
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
    report(100 * index / total, f"已导出 {target}，{len(blocks)} 条动作，{len(images)} 张图片")
    return None


def optimize_sprite(package: Path, report, overwrite: bool = False) -> dict:
    target = SPR_DIRECTORY / package.name
    if target.exists() and not overwrite:
        raise FileExistsError(f"同名文件已存在：{target}")
    report(0, f"读取原始 SPR：{package}")
    manifest, sprite_index, sprite, frames = _package(package)
    number, _offset, flags = struct.unpack("<III", sprite_index)
    cursor = 0
    blocks = []
    pairs = set()
    images = set()
    for index in range(flags & 0xFFFF):
        start = cursor
        if cursor + 12 > len(sprite):
            raise ValueError(f"{package.name}：第 {index + 1} 条动画头不完整")
        direction, action, _duration, count = struct.unpack_from("<HHII", sprite, cursor)
        cursor += 12
        end = cursor + count * 10
        if end > len(sprite):
            raise ValueError(f"{package.name}：方向 {direction} 动作 {action} 帧越界")
        if direction in (3, 7) and action in (0, 3):
            if not count or (direction, action) in pairs:
                raise ValueError(f"{package.name}：方向 {direction} 动作 {action} 无帧或重复")
            pairs.add((direction, action))
            images.update(row[0] for row in struct.iter_unpack("<IhhH", sprite[cursor:end]) if row[0] != 0xFFFFFFFF)
            blocks.append(sprite[start:end])
        cursor = end
    if cursor != len(sprite):
        raise ValueError(f"{package.name}：动画记录数与数据长度不符")
    missing = {(3, 0), (3, 3), (7, 0), (7, 3)} - pairs
    if missing:
        raise ValueError("缺少必需组合：" + "、".join(f"方向 {direction}/动作 {action}" for direction, action in sorted(missing)))
    if not images.issubset(frames):
        raise ValueError("保留动作引用了包中不存在的图片：" + "、".join(map(str, sorted(images - frames.keys()))))
    filtered = b"".join(blocks)
    result_manifest = dict(manifest)
    result_manifest.update(frame_count=len(images), sprite_sha256=digest(filtered), frames=[])
    SPR_DIRECTORY.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="optimize-", dir=SPR_DIRECTORY) as temporary:
        staged = Path(temporary) / package.name
        with zipfile.ZipFile(staged, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("sprite/index.bin", struct.pack("<III", number, 0, (flags & 0xFFFF0000) | len(blocks)))
            archive.writestr("sprite/data.bin", filtered)
            for index, bitmap in enumerate(sorted(images), 1):
                report(95 * index / len(images), f"优化 {package.name}：保留图片 {bitmap}（{index}/{len(images)}）")
                record, block = frames[bitmap]
                archive.writestr(f"frames/{bitmap}.adrn", record)
                archive.writestr(f"frames/{bitmap}.real", block)
                result_manifest["frames"].append({"number": bitmap, "size": len(block),
                                                  "adrn_sha256": digest(record), "real_sha256": digest(block)})
            archive.writestr("manifest.json", json.dumps(result_manifest, ensure_ascii=False, indent=2))
        if target.exists() and not overwrite:
            raise FileExistsError(f"同名文件已存在：{target}")
        staged.replace(target)
    report(100, f"单个 SPR 优化完成：{target}")
    return {"operation": "optimize", "output": target, "images": len(images), "size": target.stat().st_size}


def optimize_sprites(packages: list[tuple[Path, bool]], report, skipped: list[tuple[Path, str]]) -> dict:
    completed = []
    failures = []
    for package, reason in skipped:
        report(0, f"跳过 {package}：{reason}")
    for index, (package, overwrite) in enumerate(packages, 1):
        report(100 * (index - 1) / len(packages), f"正在优化 {package}（{index}/{len(packages)}）")
        try:
            result = optimize_sprite(package,
                                     lambda percent, text: report(100 * (index - 1 + percent / 100) / len(packages), text),
                                     overwrite=overwrite)
            completed.append((package, result["output"]))
        except Exception as error:
            failures.append((package, str(error)))
            report(100 * index / len(packages), f"异常跳过 {package}：{error}")
    report(100, f"批量 SPR 优化报告：共 {len(packages) + len(skipped)} 个，成功 {len(completed)}，取消覆盖跳过 {len(skipped)}，异常 {len(failures)}")
    for package, target in completed:
        report(100, f"成功：{package} → {target}")
    for package, reason in skipped + failures:
        report(100, f"未处理：{package}：{reason}")
    return {"operation": "optimize_batch", "output": SPR_DIRECTORY, "completed": completed,
            "skipped": skipped, "failures": failures, "total": len(packages) + len(skipped)}


def generate_bins(report) -> dict:
    output = SPR_DIRECTORY.parent / "data"
    if output.exists() and not output.is_dir():
        raise NotADirectoryError(f"输出路径不是目录：{output}")
    if not SPR_DIRECTORY.is_dir():
        raise FileNotFoundError(f"SPR 目录不存在：{SPR_DIRECTORY}")
    packages = sorted((path for path in SPR_DIRECTORY.iterdir() if path.is_file() and path.suffix.lower() == ".spr"),
                      key=lambda path: path.name.lower())
    if not packages:
        raise ValueError(f"没有找到 SPR 包：{SPR_DIRECTORY}")
    report(0, f"读取 SPR 目录：{SPR_DIRECTORY}，共 {len(packages)} 个包")
    palette_path = output / "Palet_1.sap"
    report(0, f"调色板路径：{palette_path}；" + ("保留已有文件" if palette_path.is_file() else "未找到，请自行放入；不影响生成 BIN"))
    image_keys = {}
    sprite_numbers = set()
    with tempfile.TemporaryDirectory(prefix="build-", dir=SPR_DIRECTORY) as temporary:
        staged = Path(temporary) / "data"
        staged.mkdir()
        with (staged / BIN_NAMES["spr"]).open("wb") as spr, \
                (staged / BIN_NAMES["spradrn"]).open("wb") as spradrn, \
                (staged / BIN_NAMES["adrn"]).open("wb") as adrn, \
                (staged / BIN_NAMES["real"]).open("wb") as real:
            for index, package in enumerate(packages, 1):
                report(95 * (index - 1) / len(packages), f"正在重建 SPR {package.stem}（{index}/{len(packages)}）")
                manifest, sprite_index, sprite, frames = _package(package)
                sprite_number = int(manifest["sprite"])
                if sprite_number in sprite_numbers:
                    raise ValueError(f"{package.name}：形象编号 {sprite_number} 与其他 SPR 包重复")
                sprite_numbers.add(sprite_number)
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
        real_size = (staged / BIN_NAMES["real"]).stat().st_size
        output.mkdir(exist_ok=True)
        for name in BIN_NAMES.values():
            report(95, f"写入生成文件：{output / name}（同名文件覆盖）")
            (staged / name).replace(output / name)
    return {"operation": "generate", "packages": len(packages), "images": len(image_keys), "real_size": real_size, "output": output}


def summary(result: dict) -> str:
    if result["operation"] == "optimize_batch":
        return (f"批量 SPR 优化完成：{result['output']}\n"
                f"共 {result['total']} 个，成功 {len(result['completed'])}，取消覆盖跳过 {len(result['skipped'])}，异常 {len(result['failures'])}。\n"
                "成功文件、跳过文件及错误原因见窗口日志末尾的批量优化报告。")
    if result["operation"] == "optimize":
        return (f"单个 SPR 优化完成：{result['output']}\n仅保留方向 3、7 × 动作 0、3 四种组合。\n"
                f"图片 {result['images']} 张，文件大小 {result['size']:,} 字节。")
    if result["operation"] == "export":
        return (f"SPR 导出完成：{result['output']}\n共 {result['packages']} 个形象包。\n"
                f"请求 {result['total']} 个，组合缺失跳过 {len(result['skipped'])} 个，异常 {len(result['failures'])} 个。\n"
                "异常编号及原因见窗口日志末尾的导出报告。\n"
                "未生成 BIN，源客户端未修改。")
    return (f"SPR 导出目录：{SPR_DIRECTORY}\n独立 BIN 目录：{result['output']}\n"
            f"形象 {result['packages']} 个，去重后图片 {result['images']} 张，REAL {result['real_size']:,} 字节\n"
            f"调色板使用 {result['output'] / 'Palet_1.sap'}，已有文件不修改；缺失时需自行放入。")
