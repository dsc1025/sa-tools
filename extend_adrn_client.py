#!/usr/bin/env python3
"""Expand a Stone Age 2.5 client and configure its server endpoint.

The 2.5 client keeps its 80-byte ADRN records in a statically reserved
300000-record table. Changing only the bounds checks is unsafe: later records
would overwrite adjacent data. This tool adds a zero-initialized PE section,
moves every ADRN-table reference there (including the map-colour cache
generator), updates the table bounds, and patches the client's connection
endpoint directly into the executable.

It writes the expanded executable to a separate output file, leaving the source
executable unchanged. The server endpoint is entered when the script runs; no
serverlist.ini or SACH-MX0.30/server.ini is used.
"""
from __future__ import annotations

import argparse
import ipaddress
import struct
from pathlib import Path


IMAGE_SCN_CNT_UNINITIALIZED_DATA = 0x00000080
IMAGE_SCN_MEM_READ = 0x40000000
IMAGE_SCN_MEM_WRITE = 0x80000000

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SOURCE = PROJECT_ROOT / "SA2.5" / "sa_2903.exe"
DEFAULT_OUTPUT = PROJECT_ROOT / "SA2.5" / "sa_2903.expanded.exe"
DEFAULT_SERVER_IP = "127.0.0.1"
DEFAULT_SERVER_PORT = 9065
DEFAULT_SERVER_NAME = "StoneAge"

CLIENT_IMAGE_BASE = 0x00400000
SERVER_CONNECT_HOOK_VA = 0x00462D80
SERVER_CONNECT_VA = 0x004569D6
SERVER_CONNECT_CALL_VAS = (0x0043A534, 0x0043A744, 0x0043AA61)


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


def patch_server_connection(
    data: bytearray,
    image_base: int,
    server_ip: str,
    port: int,
) -> None:
    """Embed the configured server endpoint in the client's connect hook."""
    if image_base != CLIENT_IMAGE_BASE:
        raise ValueError(f"unexpected client image base: 0x{image_base:08X}")
    if not 1 <= port <= 65535:
        raise ValueError("server port must be between 1 and 65535")
    address = ipaddress.IPv4Address(server_ip)

    hook_offset = SERVER_CONNECT_HOOK_VA - image_base
    hook_end = hook_offset + 32
    if hook_offset < 0 or hook_end > len(data):
        raise ValueError("server connection hook is outside the client image")
    if any(data[hook_offset:hook_end]):
        raise ValueError("server connection hook padding is not empty")

    # The sockaddr stores the port in network byte order. The hook compares
    # that 16-bit value and replaces the IPv4 address for the matching port.
    port_word = ((port & 0xFF) << 8) | ((port >> 8) & 0xFF)
    hook = bytearray((0x8B, 0x44, 0x24, 0x08, 0x66, 0x81, 0x78, 0x02))
    hook.extend(struct.pack("<H", port_word))
    hook.extend((0x75, 0x07, 0xC7, 0x40, 0x04))
    hook.extend(address.packed)
    jump_relative = SERVER_CONNECT_VA - (SERVER_CONNECT_HOOK_VA + len(hook) + 5)
    hook.append(0xE9)
    hook.extend(struct.pack("<i", jump_relative))
    data[hook_offset:hook_offset + len(hook)] = hook

    for call_va in SERVER_CONNECT_CALL_VAS:
        call_offset = call_va - image_base
        if call_offset < 0 or call_offset + 5 > len(data) or data[call_offset] != 0xE8:
            raise ValueError(f"unexpected server connect call at 0x{call_va:08X}")
        old_relative = struct.unpack_from("<i", data, call_offset + 1)[0]
        if call_va + 5 + old_relative != SERVER_CONNECT_VA:
            raise ValueError(f"unexpected server connect target at 0x{call_va:08X}")
        struct.pack_into(
            "<i", data, call_offset + 1,
            SERVER_CONNECT_HOOK_VA - (call_va + 5),
        )

def patch_client(
    source: Path,
    output: Path,
    capacity: int,
    server_ip: str,
    port: int,
) -> dict[str, int | str]:
    if capacity <= 300000:
        raise ValueError("capacity must be greater than 300000")
    if source.resolve() == output.resolve():
        raise ValueError("source and output must be different files")
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
    already_expanded = False
    for index in range(section_count):
        offset = section_table + index * 40
        name = bytes(data[offset:offset + 8]).split(b"\0", 1)[0].decode("ascii", "replace")
        already_expanded = already_expanded or name == ".adrn"
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
    if already_expanded:
        raise ValueError("input client is already ADRN-expanded")

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

    # The minimap colour-cache builder walks the ADRN table through the final
    # DWORD in each 80-byte record. These two pointers sit at offset 0x4C,
    # outside the 0x00..0x3F range handled above. If they remain on the old
    # table, a cache rebuild reads the zero-filled original region and writes
    # an all-zero auto.dat.
    old_colour_start = old_base + 0x4C
    old_colour_end = old_base + old_limit * record_size + 0x4C
    new_colour_start = new_base + 0x4C
    new_colour_end = new_base + old_limit * record_size + 0x4C
    for old_address, new_address, label in (
        (old_colour_start, new_colour_start, "map-colour table start"),
        (old_colour_end, new_colour_end, "map-colour table end"),
    ):
        old_bytes = struct.pack("<I", old_address)
        offset = data.find(old_bytes)
        if offset < 0 or data.find(old_bytes, offset + 1) >= 0:
            raise ValueError(f"expected exactly one {label} reference at 0x{old_address:08X}")
        put32(data, offset, new_address)

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

    patch_server_connection(data, image_base, server_ip, port)

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
        "server_connect_hooks": len(SERVER_CONNECT_CALL_VAS),
        "server": f"{server_ip}:{port}",
        "map_colour_start": f"0x{new_colour_start:08X}",
        "map_colour_end": f"0x{new_colour_end:08X}",
        "limit_replacements": limit_replacements,
        "file_size": len(data),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Expand a Stone Age 2.5 client and configure its server"
    )
    parser.add_argument("source", nargs="?", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("output", nargs="?", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--capacity", type=int, default=1_000_000)
    args = parser.parse_args()

    try:
        server_ip_text = input(f"服务器 IP [{DEFAULT_SERVER_IP}]: ").strip()
        server_ip = str(ipaddress.IPv4Address(server_ip_text or DEFAULT_SERVER_IP))
    except ipaddress.AddressValueError as error:
        parser.error(f"无效的 IPv4 地址: {error}")
    try:
        port_text = input(f"服务器端口 [{DEFAULT_SERVER_PORT}]: ").strip()
        port = int(port_text) if port_text else DEFAULT_SERVER_PORT
    except ValueError:
        parser.error("服务器端口必须是数字")
    if not 1 <= port <= 65535:
        parser.error("服务器端口范围必须是 1 到 65535")
    server_name = input(f"服务器名称 [{DEFAULT_SERVER_NAME}]: ").strip()
    if not server_name:
        server_name = DEFAULT_SERVER_NAME

    result = patch_client(args.source, args.output, args.capacity, server_ip, port)
    for key, value in result.items():
        print(f"{key}={value}")
    print(f"server_name={server_name}")


if __name__ == "__main__":
    main()
