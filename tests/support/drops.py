"""Things to drop: archives and folders with real pictures in them.

The arrival tests read what a drop is made of, so the pictures here have real pixels
(the engine reads their size) and everything else is filler. Each builder returns the
path of what it made.
"""

from __future__ import annotations

import os
import zipfile
from pathlib import Path

from PIL import Image

JUNK_NAMES = {".ds_store", "thumbs.db", "desktop.ini"}


def png(path, width: int = 64, height: int = 64, alpha: bool = False) -> Path:
    """A real PNG of this size."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "RGBA" if alpha else "RGB"
    Image.new(mode, (width, height), (200, 30, 30, 128) if alpha else (200, 30, 30)).save(
        path, "PNG")
    return path


def png_size(handle) -> tuple[int, int]:
    """The pixel size of the picture in an open file."""
    with Image.open(handle) as image:
        return image.size


def png_bytes(width: int, height: int, alpha: bool = False) -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGBA" if alpha else "RGB", (width, height)).save(buffer, "PNG")
    return buffer.getvalue()


def _zip(path: Path, members: dict[str, bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)
    return path


def wheel_zip(directory, n: int = 3) -> Path:
    """`n` wheels for Medieval Madness: the first is `(Wheel) Medieval Madness.png`, the
    rest carry a number. Sizes differ so a candidate can be told by its pixels."""
    members = {}
    for k in range(1, n + 1):
        name = "(Wheel) Medieval Madness.png" if k == 1 else f"(Wheel) Medieval Madness {k}.png"
        members[name] = png_bytes(100 * k, 50 * k)
    return _zip(Path(directory) / "wheels.zip", members)


def flyer_zip(directory, n: int = 3) -> Path:
    """`n` flyers: `Flyer.png`, `Flyer 2.png`, `Flyer 3.png`."""
    members = {("Flyer.png" if k == 1 else f"Flyer {k}.png"): png_bytes(60, 90)
               for k in range(1, n + 1)}
    return _zip(Path(directory) / "flyers.zip", members)


def pack_zip(directory) -> Path:
    """A table with what goes with it, a named wheel and backglass, two text files (one a
    README) and junk."""
    members = {
        "Medieval Madness.vpx": b"x" * 16,
        "Medieval Madness.directb2s": b"x" * 16,
        "Medieval Madness.ini": b"x" * 16,
        "Medieval Madness.pov": b"x" * 16,
        "(Wheel) Medieval Madness.png": png_bytes(512, 512),
        "bg.png": png_bytes(1920, 1080),
        "readme.txt": b"about this table",
        "notes.txt": b"x" * 16,
        "__MACOSX/._x": b"x" * 16,
    }
    return _zip(Path(directory) / "pack.zip", members)


def mixed_folder(directory) -> Path:
    """A folder of loose files, some a table's own, some media, some neither."""
    folder = Path(directory) / "mixed"
    folder.mkdir(parents=True, exist_ok=True)
    png(folder / "(Wheel) Medieval Madness.png", 400, 200)
    png(folder / "IMG_0001.png", 800, 800)
    (folder / "Medieval Madness.directb2s").write_bytes(b"x" * 16)
    (folder / "notes.txt").write_bytes(b"x" * 16)
    (folder / "mystery.xyz").write_bytes(b"x" * 16)
    (folder / ".DS_Store").write_bytes(b"x" * 16)
    return folder


def members(path) -> list[str]:
    """What an arrival holds that is somebody's file: every file under a folder or in an
    archive, by its path inside it, without OS junk. A lone file is its own name."""
    path = Path(path)
    if path.is_file() and path.suffix.lower() != ".zip":
        return [path.name]
    if path.is_file():
        with zipfile.ZipFile(path) as archive:
            names = [one.filename for one in archive.infolist() if not one.is_dir()]
    else:
        names = [(Path(root) / name).relative_to(path).as_posix()
                 for root, _dirs, files in os.walk(path) for name in files]
    return sorted(name for name in (n.replace("\\", "/") for n in names) if not _junk(name))


def _junk(name: str) -> bool:
    parts = [part.lower() for part in name.split("/")]
    return (any(part in JUNK_NAMES or part == "__macosx" for part in parts)
            or parts[-1].startswith("._"))


def accounted(arrival) -> list[str]:
    """Every member the arrival placed somewhere: claimed, grouped, unclaimed or left out."""
    found = [member for claim in arrival.claims for member in claim.members]
    found += [candidate.member for group in arrival.groups for candidate in group.candidates]
    found += [one.member for one in arrival.unclaimed]
    found += [one.member for one in arrival.left_out]
    return sorted(found)
