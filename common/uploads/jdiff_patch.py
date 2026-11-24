"""Apply a JojoDiff patch. Pure Python, no external tool, no bundled binary.

Table MODs are often distributed as patches rather than whole tables, because many authors
do not permit redistribution of their work - the delta is the modder's, the table is not.
VPUniverse's VPURemix system is built on this. A `.dif` is therefore not installable on its
own: it needs the exact base table it was built against.

Ported from jojodiff-rs by Francis De Brabandere (MIT):
https://github.com/francisdb/jojodiff-rs

Structure follows that implementation: source and output positions are tracked as
integers rather than by seeking relatively, and an operand that turns out to be data is
carried forward as "pending" bytes instead of rewinding the patch stream. Both choices
mean the patch stream is only ever read forwards, which is what lets this work on a
non-seekable input later if that is ever wanted.

The format is six opcodes behind one escape byte:

    ESC 0xa7   introduces an operation; doubled when 0xa7 occurs in literal data
    EQL 0xa3   copy N bytes from the source
    DEL 0xa4   skip N bytes of the source
    BKT 0xa2   go back N bytes in the source
    MOD 0xa6   literal bytes that replace source bytes (both positions advance)
    INS 0xa5   literal bytes inserted (only the output position advances)

Since JojoDiff 0.8.5 a byte that is not ESC begins an implicit MOD, so a run of literal
data costs no opcode at all.
"""

from __future__ import annotations

from pathlib import Path
from typing import BinaryIO

ESC, MOD, INS, DEL, EQL, BKT = 0xA7, 0xA6, 0xA5, 0xA4, 0xA3, 0xA2
_OPERANDS = frozenset({EQL, DEL, BKT, MOD, INS})
_READ_NEXT = 0            # sentinel: no operand in hand, read one

_CHUNK = 1 << 20


class PatchError(RuntimeError):
    """The patch does not apply — most often the wrong source file."""


def _read_byte(stream: BinaryIO) -> int | None:
    b = stream.read(1)
    return b[0] if b else None


def _read_int(patch: BinaryIO) -> int:
    """Variable-width length, biased so the common small case costs one byte.

    0..251 -> n+1;  252 -> 253+next;  253 -> 16-bit;  254 -> 32-bit;  255 -> 64-bit.
    """
    first = _read_byte(patch)
    if first is None:
        raise PatchError("patch ended while reading a length")
    if first < 252:
        return first + 1
    width = {252: 1, 253: 2, 254: 4}.get(first, 8)
    raw = patch.read(width)
    if len(raw) != width:
        raise PatchError("patch ended mid-length")
    value = int.from_bytes(raw, "big")
    return 253 + value if first == 252 else value


def _copy_from_source(source: BinaryIO, out: BinaryIO, position: int, length: int) -> None:
    source.seek(position)
    remaining = length
    while remaining:
        chunk = source.read(min(remaining, _CHUNK))
        if not chunk:
            raise PatchError(
                "source ended during a copy — this patch expects a different source file")
        out.write(chunk)
        remaining -= len(chunk)


def _copy_data(patch: BinaryIO, out: BinaryIO, operand: int,
               pending1: int | None, pending2: int | None) -> tuple[int | None, int]:
    """Emit literal bytes until the next real operand.

    Returns (next operand or None at end of stream, bytes written).
    """
    written = 0
    if pending1 is not None:
        out.write(bytes((pending1,)))
        written += 1
        if pending1 == ESC and pending2 is not None:
            out.write(bytes((pending2,)))
            written += 1

    while True:
        current = _read_byte(patch)
        if current is None:
            return None, written

        if current == ESC:
            following = _read_byte(patch)
            if following is None:
                raise PatchError("ESC at end of patch stream")

            if following == ESC:
                out.write(bytes((ESC,)))       # doubled ESC: emit one
                written += 1
                continue
            if following not in _OPERANDS:
                out.write(bytes((ESC, following)))   # ESC that meant nothing: both data
                written += 2
                continue
            if following == operand:
                # ESC MOD inside a MOD (or INS inside INS) is meaningless: treat as data
                out.write(bytes((ESC,)))
                written += 1
                current = following
            else:
                return following, written

        out.write(bytes((current,)))
        written += 1


def apply_patch(source: BinaryIO, patch: BinaryIO, out: BinaryIO) -> None:
    source_pos = 0
    operand = _READ_NEXT

    while True:
        pending1 = pending2 = None

        if operand == _READ_NEXT:
            first = _read_byte(patch)
            if first is None:
                break
            if first == ESC:
                escaped = _read_byte(patch)
                if escaped is None:
                    raise PatchError("ESC at end of patch stream")
                if escaped in _OPERANDS:
                    operand = escaped
                else:
                    operand, pending1, pending2 = MOD, ESC, escaped
            else:
                # No ESC: an implicit MOD, and this byte is its first data byte.
                operand, pending1 = MOD, first

        if operand in (MOD, INS):
            following, written = _copy_data(patch, out, operand, pending1, pending2)
            if operand == MOD:
                source_pos += written        # MOD consumes source; INS does not
            if following is None:
                break
            operand = following
        elif operand == EQL:
            length = _read_int(patch)
            _copy_from_source(source, out, source_pos, length)
            source_pos += length
            operand = _READ_NEXT
        elif operand == DEL:
            source_pos += _read_int(patch)
            operand = _READ_NEXT
        elif operand == BKT:
            source_pos -= _read_int(patch)
            operand = _READ_NEXT
        else:
            raise PatchError(f"unknown operand 0x{operand:02x}")


def apply_patch_files(source: str | Path, patch: str | Path, out: str | Path) -> Path:
    src, pat, dst = Path(source), Path(patch), Path(out)
    with open(src, "rb") as s, open(pat, "rb") as p, open(dst, "wb") as o:
        apply_patch(s, p, o)
    return dst
