"""EDID blocks as the files under `tests/fixtures/edid/` hold them.

The blocks are laid out from the VESA field map, one case per file, in the hex a
`xrandr --props` dump prints. They are not captures from hardware.
"""

from __future__ import annotations

from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "edid"


def load(name: str) -> bytes:
    return bytes.fromhex("".join((FIXTURES / f"{name}.hex").read_text().split()))
