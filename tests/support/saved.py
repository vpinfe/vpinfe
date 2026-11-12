"""A game's `.info` as the app reads it back, for tests that check what was saved.

The file on disk is schema 3 and groups a game's files under `vpinfe.manifest`; the
record the app works with keeps tables, assets and guides at the top level. A table's
own `.info` beside its `.vpx` is folded in, as the app reads it.
"""

from __future__ import annotations

import json
from pathlib import Path

from common.games import info_codec, info_file


def saved_record(path: str | Path) -> dict:
    return info_file.read_info(path)


def save_record(path: str | Path, record: dict) -> None:
    Path(path).write_text(json.dumps(info_codec.encode(record)), encoding="utf-8")
