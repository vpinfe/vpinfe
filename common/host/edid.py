"""The parts of a monitor's EDID block that VPinFE reads: its model name and physical size."""

from __future__ import annotations

from dataclasses import dataclass

_HEADER = bytes([0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00])
_SIZE = 128

# Four 18-byte descriptors follow the fixed fields. One that starts with three zero bytes is
# text rather than a timing, and its fourth byte says which.
_DESCRIPTORS = range(54, 126, 18)
_MODEL_NAME = 0xFC


@dataclass(frozen=True)
class Edid:
    name: str
    width_cm: int
    height_cm: int


def parse(raw: bytes) -> Edid | None:
    """What the first block says, or None for bytes that are not an EDID.

    The checksum is not enforced: a block that fails it still names its monitor, and
    refusing it would leave a screen unnamed over a byte nothing here reads.
    """
    if len(raw) < _SIZE or raw[:8] != _HEADER:
        return None
    return Edid(name=_model_name(raw), width_cm=raw[21], height_cm=raw[22])


def _model_name(block: bytes) -> str:
    for start in _DESCRIPTORS:
        descriptor = block[start:start + 18]
        if descriptor[:3] == b"\x00\x00\x00" and descriptor[3] == _MODEL_NAME:
            # Ends at a line feed and is padded after it with spaces, up to 13 characters.
            text = descriptor[5:18].split(b"\n", 1)[0].decode("cp437", errors="replace")
            name = "".join(ch for ch in text if ch.isprintable()).strip()
            if name:
                return name
    return ""
