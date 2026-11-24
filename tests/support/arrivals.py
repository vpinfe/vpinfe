"""Builders the drop tests share: a Manager library holding a few games, and real pictures."""

from __future__ import annotations

import struct
import zlib
from pathlib import Path
from typing import Any
from unittest.mock import Mock

from manager.kit.data import Library

GAME = {"id": "g-1", "name": "Some Game", "folder": "/games/Some Game"}
OTHER = {"id": "g-2", "name": "Other Game", "folder": "/games/Other Game"}


def library(games: list[dict] | None = None, tables: list[dict] | None = None,
            media: list[dict] | None = None,
            assets: list[dict] | None = None) -> tuple[Library, Mock]:
    """A Library over a Mock client, holding what the lists say, with the client beside it
    so a test can count what was read."""
    client = Mock()
    client.library_policy.return_value = {}
    client.all_tables.return_value = tables if tables is not None else []
    client.all_media.return_value = media if media is not None else []
    client.all_assets.return_value = assets if assets is not None else []
    held = Library(client)
    held.games = games if games is not None else [GAME]
    return held, client


def png(path: Path, width: int = 8, height: int = 8, *, alpha: bool = False) -> Path:
    """A real picture of that size. Its pixels are flat color: it is the shape that is
    read, never the content."""
    channels, kind = (4, 6) if alpha else (3, 2)
    row = b"\x00" + bytes([200, 100, 50, 255][:channels]) * width

    def chunk(name: bytes, body: bytes) -> bytes:
        crc = zlib.crc32(name + body) & 0xFFFFFFFF
        return struct.pack(">I", len(body)) + name + body + struct.pack(">I", crc)

    header = struct.pack(">IIBBBBB", width, height, 8, kind, 0, 0, 0)
    data: Any = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header)
                 + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b""))
    path.write_bytes(data)
    return path
