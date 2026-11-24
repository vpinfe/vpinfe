"""A small OLE compound file writer, enough to make a `VPReg.stg` for a test.

Version 3 layout: one FAT sector, every stream in the mini stream, siblings chained by
their right links. Not a general writer - sixty-odd entries at most, values under 4096.
"""

from __future__ import annotations

import struct

END, FREE, FAT_SECTOR = 0xFFFFFFFE, 0xFFFFFFFF, 0xFFFFFFFD
SECTOR, MINI = 512, 64


def _entry(name: str, kind: int, left: int = FREE, right: int = FREE, child: int = FREE,
           start: int = END, size: int = 0) -> bytes:
    raw = name.encode("utf-16-le")
    return (raw.ljust(64, b"\0") + struct.pack("<HBBIII", len(raw) + 2, kind, 1, left, right,
                                               child)
            + b"\0" * 36 + struct.pack("<II", start, size) + b"\0" * 4)


def _pad(data: bytes, unit: int) -> bytes:
    return data + b"\0" * (-len(data) % unit)


def stg_bytes(sets: dict[str, dict[str, str]]) -> bytes:
    """The compound file holding `sets` as set -> {value name: value}, values as UTF-16."""
    mini = bytearray()
    minifat: list[int] = []

    def stream(value: str) -> tuple[int, int]:
        data = value.encode("utf-16-le")
        first = len(minifat)
        count = max(1, -(-len(data) // MINI))
        minifat.extend(list(range(first + 1, first + count)) + [END])
        mini.extend(_pad(data, MINI))
        return first, len(data)

    # entry 0 is the root; storages and streams follow in the order they are written
    storages: list[tuple[str, list[tuple[str, int, int]]]] = []
    for name, values in sets.items():
        storages.append((name, [(key, *stream(val)) for key, val in values.items()]))
    count = 1 + sum(1 + len(streams) for _, streams in storages)
    placed: list[bytes] = [b""] * count
    at = 1
    for index, (name, streams) in enumerate(storages):
        mine = at
        first_stream = mine + 1 if streams else FREE
        right = mine + 1 + len(streams) if index + 1 < len(storages) else FREE
        placed[mine] = _entry(name, 1, right=right, child=first_stream)
        for number, (key, start, size) in enumerate(streams):
            nxt = mine + 2 + number if number + 1 < len(streams) else FREE
            placed[mine + 1 + number] = _entry(key, 2, right=nxt, start=start, size=size)
        at += 1 + len(streams)

    mini_data = _pad(bytes(mini), SECTOR)
    mini_sectors = len(mini_data) // SECTOR
    dir_sectors = -(-count * 128 // SECTOR)
    minifat_data = _pad(b"".join(struct.pack("<I", one) for one in minifat), SECTOR)
    minifat_sectors = len(minifat_data) // SECTOR
    # sector 0 FAT, then the directory, the mini FAT, the mini stream container
    first_dir = 1
    first_minifat = first_dir + dir_sectors
    first_mini = first_minifat + minifat_sectors
    total = first_mini + mini_sectors

    def chain(first: int, length: int) -> list[int]:
        return list(range(first + 1, first + length)) + [END]

    placed[0] = _entry("Root Entry", 5, child=1 if storages else FREE,
                       start=first_mini if mini_sectors else END, size=len(mini))
    directory = _pad(b"".join(placed), SECTOR)
    fat = [FAT_SECTOR] + chain(first_dir, dir_sectors) + chain(first_minifat, minifat_sectors)
    fat += chain(first_mini, mini_sectors) if mini_sectors else []
    assert len(fat) == total and total <= SECTOR // 4
    fat_data = _pad(b"".join(struct.pack("<I", one) for one in fat), SECTOR)

    header = (bytes.fromhex("D0CF11E0A1B11AE1") + b"\0" * 16
              + struct.pack("<HHHHH", 0x3E, 3, 0xFFFE, 9, 6) + b"\0" * 6
              + struct.pack("<IIIIIIIII", 0, 1, first_dir, 0, 4096, first_minifat,
                            minifat_sectors, END, 0)
              + struct.pack("<I", 0) + struct.pack("<I", FREE) * 108)
    return _pad(header, SECTOR) + fat_data + directory + minifat_data + mini_data
