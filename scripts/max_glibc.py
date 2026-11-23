"""Report the highest GLIBC version each ELF library in a directory needs.

Usage: max_glibc.py <directory> [--limit 2.38]

Reads the version-needs table (.gnu.version_r) of every ELF file under the
directory, so no objdump or readelf is needed.  Symlinks are skipped, so each
library is listed once.  With --limit, exits 1 when any library needs a newer
GLIBC than that.
"""

import argparse
import struct
import sys
from pathlib import Path

SHT_GNU_VERNEED = 0x6FFFFFFE


def _version(text):
    return tuple(int(part) for part in text.split("."))


def glibc_needs(path):
    """Return the set of GLIBC_x.y version strings an ELF64 LE file requires."""
    data = Path(path).read_bytes()
    if data[:4] != b"\x7fELF" or data[4] != 2 or data[5] != 1:
        return None
    shoff = struct.unpack_from("<Q", data, 0x28)[0]
    shentsize, shnum = struct.unpack_from("<HH", data, 0x3A)
    sections = []
    for i in range(shnum):
        base = shoff + i * shentsize
        _name, kind, _flags, _addr, off, size, link, info = struct.unpack_from(
            "<IIQQQQII", data, base
        )
        sections.append((kind, off, size, link, info))
    found = set()
    for kind, off, _size, link, info in sections:
        if kind != SHT_GNU_VERNEED:
            continue
        stroff = sections[link][1]
        pos = off
        for _ in range(info):
            _ver, cnt, _file, aux, nxt = struct.unpack_from("<HHIII", data, pos)
            apos = pos + aux
            for _ in range(cnt):
                _hash, _flags, _other, name, anext = struct.unpack_from("<IHHII", data, apos)
                end = data.index(b"\0", stroff + name)
                label = data[stroff + name : end].decode("ascii")
                if label.startswith("GLIBC_") and label[6:7].isdigit():
                    found.add(label)
                apos += anext
            pos += nxt
    return found


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory")
    parser.add_argument("--limit", help="fail if any library needs a newer GLIBC")
    args = parser.parse_args()

    root = Path(args.directory)
    rows = []
    for path in sorted(root.rglob("*")):
        if path.is_symlink() or not path.is_file():
            continue
        needs = glibc_needs(path)
        if needs is None:
            continue
        top = max((_version(n[6:]) for n in needs), default=())
        rows.append((path.relative_to(root), top))

    overall = ()
    for rel, top in rows:
        shown = ".".join(str(p) for p in top) if top else "none"
        print(f"{shown:>8}  {rel}")
        overall = max(overall, top)
    if not rows:
        print("no ELF files found", file=sys.stderr)
        return 2
    shown = ".".join(str(p) for p in overall) if overall else "none"
    print(f"highest GLIBC needed: {shown}")
    if args.limit and overall > _version(args.limit):
        print(f"FAIL: above the limit of {args.limit}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
