#!/usr/bin/env python3
"""Extend the Stone Age 2.5 ADRN table in a PE32 client.

The 2.5 client keeps its 80-byte ADRN records in a statically reserved
300000-record table.  Changing the seven bounds checks alone is unsafe: the
next record would overwrite the end of the .data section.  This tool adds a
zero-initialized PE section, moves the ADRN table references to that section,
and keeps the larger bounds checks.

It intentionally writes a new output file.  No input file is modified.
"""
from __future__ import annotations

import argparse
import struct
from pathlib import Path


IMAGE_SCN_CNT_UNINITIALIZED_DATA = 0x00000080
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000


def u16(data: bytearray | bytes, offset: int) -> int:
    return struct.unpack_from("<H", data, offset)[0]


def u32(data: bytearray | bytes, offset: int) -> int:
    return struct.unpack_from("<I", data, offset)[0]


def put16(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<H", data, offset, value)


def put32(data: bytearray, offset: int, value: int) -> None:
    struct.pack_into("<I", data, offset, value)


def align(value: int, boundary: int) -> int:
    return (value + boundary - 1) // boundary * boundary


def pe_checksum(data: bytearray, checksum_offset: int) -> int:
    """Calculate the PE checksum used by CheckSumMappedFile."""
    old = u32(data, checksum_offset)
    put32(data, checksum_offset, 0)
    total = 0
    length = len(data)
    even_length = length & ~1
    for offset in range(0, even_length, 2):
        total += u16(data, offset)
        total = (total & 0xFFFF) + (total >> 16)
    if length & 1:
        total += data[-1]
        total = (total & 0xFFFF) + (total >> 16)
    total = (total & 0xFFFF) + (total >> 16)
    result = (total + length) & 0xFFFFFFFF
    put32(data, checksum_offset, old)
    return result


def checksum_and_write(data: bytearray, path: Path, checksum_offset: int) -> None:
    put32(data, checksum_offset, 0)
    put32(data, checksum_offset, pe_checksum(data, checksum_offset))
    path.write_bytes(data)


def patch_client(source: Path, output: Path, capacity: int) -> dict[str, int | str]:
    if capacity <= 300000:
        raise ValueError("capacity must be greater than 300000")
    data = bytearray(source.read_bytes())
    if data[:2] != b"MZ":
        raise ValueError("input is not an MZ executable")
    pe = u32(data, 0x3C)
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("input is not a PE executable")
    section_count_offset = pe + 6
    section_count = u16(data, section_count_offset)
    optional = pe + 24
    if u16(data, optional) != 0x10B:
        raise ValueError("only PE32 clients are supported")
    section_alignment = u32(data, optional + 32)
    file_alignment = u32(data, optional + 36)
    image_base = u32(data, optional + 28)
    size_of_image_offset = optional + 56
    checksum_offset = optional + 64
    size_of_headers = u32(data, optional + 60)
    section_table = optional + 224

    sections: list[dict[str, int | str]] = []
    data_section: dict[str, int | str] | None = None
    last_section: dict[str, int | str] | None = None
    for index in range(section_count):
        offset = section_table + index * 40
        name = bytes(data[offset:offset + 8]).split(b"\0", 1)[0].decode("ascii", "replace")
        section = {
            "header": offset,
            "name": name,
            "virtual_size": u32(data, offset + 8),
            "virtual_address": u32(data, offset + 12),
            "raw_size": u32(data, offset + 16),
            "raw_pointer": u32(data, offset + 20),
            "characteristics": u32(data, offset + 36),
        }
        sections.append(section)
        if name == ".data":
            data_section = section
        last_section = section
    if data_section is None or last_section is None:
        raise ValueError(".data section was not found")

    # The extra section header must fit in the existing PE header page.
    new_header = section_table + section_count * 40
    if new_header + 40 > size_of_headers:
        raise ValueError("there is no room for an additional section header")

    old_base = 0x02F3D620
    old_limit = 300000
    record_size = 80
    new_base_rva = align(
        int(last_section["virtual_address"]) + max(
            int(last_section["virtual_size"]), int(last_section["raw_size"])
        ),
        section_alignment,
    )
    new_base = image_base + new_base_rva
    table_bytes = capacity * record_size
    new_image_end = align(new_base_rva + table_bytes, section_alignment)

    # The new section is zero-initialized (a BSS-like section); no file bytes
    # are appended, which keeps the launcher's exact-size check satisfied.
    name = b".adrn\0\0\0"
    data[new_header:new_header + 8] = name
    put32(data, new_header + 8, table_bytes)
    put32(data, new_header + 12, new_base_rva)
    put32(data, new_header + 16, 0)
    put32(data, new_header + 20, 0)
    put32(data, new_header + 24, 0)
    put32(data, new_header + 28, 0)
    put16(data, new_header + 32, 0)
    put16(data, new_header + 34, 0)
    put32(
        data,
        new_header + 36,
        IMAGE_SCN_CNT_UNINITIALIZED_DATA | IMAGE_SCN_MEM_READ | IMAGE_SCN_MEM_WRITE,
    )

    put16(data, section_count_offset, section_count + 1)
    put32(data, size_of_image_offset, new_image_end)

    # Move every direct reference to the old ADRN table.  These are absolute
    # PE32 addresses embedded in the client code; the fields are within the
    # first 0x40 bytes of the 80-byte record layout.
    refs = 0
    for offset in range(0, len(data) - 3):
        value = u32(data, offset)
        if old_base <= value < old_base + 0x40:
            put32(data, offset, new_base + (value - old_base))
            refs += 1
    if refs != 11:
        raise ValueError(f"expected 11 ADRN table references, found {refs}")

    # Also remove the original 300000 guards if the input is an unpatched
    # client.  The current direct client already has these changed, but this
    # makes the tool usable on the original executable too.
    old_limit_bytes = struct.pack("<I", old_limit)
    new_limit_bytes = struct.pack("<I", capacity)
    text_start = int(sections[0]["raw_pointer"])
    text_end = text_start + int(sections[0]["raw_size"])
    limit_replacements = 0
    cursor = text_start
    while cursor <= text_end - 4:
        if data[cursor:cursor + 4] == old_limit_bytes:
            data[cursor:cursor + 4] = new_limit_bytes
            limit_replacements += 1
        cursor += 1
    if limit_replacements not in (0, 7):
        raise ValueError(f"unexpected 300000 guard count: {limit_replacements}")

    # Keep the PE checksum coherent for tools that inspect it.
    put32(data, checksum_offset, 0)
    put32(data, checksum_offset, pe_checksum(data, checksum_offset))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(data)
    return {
        "source": str(source),
        "output": str(output),
        "capacity": capacity,
        "old_base": f"0x{old_base:08X}",
        "new_base": f"0x{new_base:08X}",
        "new_section_rva": f"0x{new_base_rva:08X}",
        "new_image_size": f"0x{new_image_end:08X}",
        "table_bytes": table_bytes,
        "table_references": refs,
        "limit_replacements": limit_replacements,
        "file_size": len(data),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Extend the Stone Age 2.5 ADRN table")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--capacity", type=int, default=1_000_000)
    args = parser.parse_args()
    result = patch_client(args.source, args.output, args.capacity)
    for key, value in result.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
