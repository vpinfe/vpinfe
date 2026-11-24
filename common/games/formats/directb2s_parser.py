"""Reading a `.directb2s` backglass file directly.

The file is XML, but a big one: the lamp pictures are megabytes of base64 in attribute
text, and a general XML parser spends seconds decoding them. This scans the bytes for tags,
keeps every large value as an offset into the file, and decodes a picture only when one is
asked for. A bad attribute costs that fact, never the file; what could not be read is
named in `Backglass.problems`.
"""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import html
import io
import logging
import re
import threading
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple

from common.atomic_write import staged_for
from common.config.paths import CACHE_DIR
from common.games.formats import directb2s_displays
from common.media import sized_media

if TYPE_CHECKING:
    from PIL.Image import Image as PILImage

logger = logging.getLogger("vpinfe.common.games.formats.directb2s_parser")

# Where the pictures made from a file are kept, never beside the file itself.
ROOT = CACHE_DIR / "backglass"

Look = Literal["on", "off", "every"]
Version = Literal["authentic", "fantasy"]

LOOKS = ("on", "off", "every")
VERSIONS = ("authentic", "fantasy")

# A lamp or animation's DualMode: 0 is both versions, then the number of one.
_VERSION_NUMBER = {"authentic": 1, "fantasy": 2}

# What `problems` can hold.
NOT_B2S = "not_b2s"
UNREADABLE = "unreadable"
NO_PICTURE = "no_picture"
BAD_PICTURE = "bad_picture"
BAD_SCOREVIEW_ART = "bad_scoreview_art"

_TAG = re.compile(
    rb'<([A-Za-z][\w:.-]*)((?:\s+[A-Za-z_:][\w:.-]*\s*=\s*"[^"]*")*)\s*/?>')
_ATTRIBUTE = re.compile(rb'([A-Za-z_:][\w:.-]*)\s*=\s*"([^"]*)"')
# Line breaks inside base64 come through as character references.
_BASE64_NOISE = re.compile(rb"&#[xX]?[0-9A-Fa-f]+;|\s+")

# A value longer than this is a picture and stays an offset.
_SMALL = 256

_HEADERS = frozenset({
    "GrillHeight", "DualBackglass", "Author", "Artwork", "GameName", "NumberOfPlayers",
})
_IMAGES = frozenset({
    "BackglassImage", "BackglassOnImage", "BackglassOffImage", "DMDImage",
})

_MAX_FACTS = 512
# A lamp scaled past this many pixels is a damaged number, not a lamp: the biggest
# backglass art is a few million.
_MAX_LAMP_AREA = 64_000_000
_facts_lock = threading.Lock()
_facts: dict[str, tuple[str, Backglass]] = {}
_render_lock = threading.Lock()


class BackglassError(Exception):
    """The file holds no picture that could be drawn."""


@dataclass(frozen=True, slots=True)
class Backglass:
    """What a `.directb2s` says about itself. A value the file leaves out is None, or
    empty for text."""

    author: str
    artwork: str
    rom: str
    players: int | None
    grill: int
    grill_small: int | None
    two_versions: bool
    size: tuple[int, int] | None
    lamps: int
    still: bool
    scoreview_art: tuple[int, int] | None
    problems: tuple[str, ...]
    # The DMDs the file places, in pixels of its art; none where it places none.
    displays: tuple[directb2s_displays.Display, ...] = ()


class _Span(NamedTuple):
    start: int
    end: int


class _Lamp(NamedTuple):
    parent: str
    initial: bool
    visible: bool
    dual: int
    z: int
    x: int
    y: int
    width: int
    height: int
    image: _Span | None


@dataclass(slots=True)
class _Document:
    root: bool = False
    values: dict[str, str] = field(default_factory=dict)
    grill_small: int | None = None
    images: dict[str, _Span] = field(default_factory=dict)
    lamps: list[_Lamp] = field(default_factory=list)


def _attributes(data: bytes, start: int, end: int) -> dict[str, _Span]:
    return {
        match.group(1).decode("ascii"): _Span(match.start(2), match.end(2))
        for match in _ATTRIBUTE.finditer(data, start, end)
    }


def _text(data: bytes, span: _Span | None) -> str | None:
    if span is None or span.end - span.start > _SMALL:
        return None
    raw = bytes(data[span.start:span.end])
    return html.unescape(raw.decode("utf-8", "replace")).strip()


def _int(data: bytes, span: _Span | None) -> int | None:
    text = _text(data, span)
    if text is None:
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return int(float(text))
        except (ValueError, OverflowError):
            return None


def _scan(data: bytes) -> _Document:
    document = _Document()
    for match in _TAG.finditer(data):
        name = match.group(1).decode("ascii")
        if name == "DirectB2SData":
            document.root = True
        elif name in _HEADERS or name in _IMAGES:
            attributes = _attributes(data, match.start(2), match.end(2))
            value = attributes.get("Value")
            if name in _IMAGES:
                if value is not None and name not in document.images:
                    document.images[name] = value
                continue
            text = _text(data, value)
            if text is not None and name not in document.values:
                document.values[name] = text
            if name == "GrillHeight" and document.grill_small is None:
                document.grill_small = _int(data, attributes.get("Small"))
        elif name == "Bulb":
            attributes = _attributes(data, match.start(2), match.end(2))
            document.lamps.append(_Lamp(
                parent=_text(data, attributes.get("Parent")) or "",
                initial=_int(data, attributes.get("InitialState")) == 1,
                visible=_int(data, attributes.get("Visible")) != 0,
                dual=_int(data, attributes.get("DualMode")) or 0,
                z=_int(data, attributes.get("ZOrder")) or 0,
                x=_int(data, attributes.get("LocX")) or 0,
                y=_int(data, attributes.get("LocY")) or 0,
                width=_int(data, attributes.get("Width")) or 0,
                height=_int(data, attributes.get("Height")) or 0,
                image=attributes.get("Image"),
            ))
    return document


@contextlib.contextmanager
def _opened(path: Path) -> Iterator[bytes | None]:
    """The file's bytes, or None for one with nothing in it. Read, never mapped: a file
    that shrinks under a mapping takes the whole process down with SIGBUS."""
    yield path.read_bytes() or None


def _decode(data: bytes, span: _Span) -> bytes:
    """The bytes of a base64 value. A damaged one raises binascii.Error, a ValueError."""
    raw = bytes(data[span.start:span.end]).replace(b"&#xD;&#xA;", b"")
    try:
        return base64.b64decode(raw, validate=True)
    except binascii.Error:
        pass
    return base64.b64decode(_BASE64_NOISE.sub(b"", raw), validate=True)


def _image_size(picture: bytes) -> tuple[int, int]:
    from PIL import Image

    with Image.open(io.BytesIO(picture)) as opened:
        size = opened.size
        opened.verify()
    return size


def _dark_span(document: _Document) -> _Span | None:
    return document.images.get("BackglassOffImage") or document.images.get("BackglassImage")


def _facts_of(document: _Document, data: bytes) -> Backglass:
    problems: list[str] = []
    size = None
    dark = _dark_span(document)
    if dark is None:
        problems.append(NO_PICTURE)
    else:
        try:
            size = _image_size(_decode(data, dark))
        except Exception:
            problems.append(BAD_PICTURE)
    scoreview = None
    art = document.images.get("DMDImage")
    if art is not None:
        try:
            scoreview = _image_size(_decode(data, art))
        except Exception:
            problems.append(BAD_SCOREVIEW_ART)
    values = document.values
    try:
        players = int(values["NumberOfPlayers"])
    except (KeyError, ValueError):
        players = None
    try:
        grill = max(0, int(values["GrillHeight"]))
    except (KeyError, ValueError):
        grill = 0
    return Backglass(
        author=values.get("Author", ""),
        artwork=values.get("Artwork", ""),
        rom=values.get("GameName", ""),
        players=players,
        grill=grill,
        grill_small=document.grill_small,
        two_versions=values.get("DualBackglass") == "1",
        size=size,
        lamps=sum(1 for lamp in document.lamps if lamp.parent != "DMD"),
        still="BackglassOffImage" not in document.images
        and not any(lamp.parent != "DMD" and lamp.visible and lamp.image is not None
                    for lamp in document.lamps),
        scoreview_art=scoreview,
        problems=tuple(problems),
        displays=directb2s_displays.read(data),
    )


def _read_uncached(path: Path) -> Backglass:
    try:
        with _opened(path) as data:
            if data is None:
                return _empty((NOT_B2S,))
            document = _scan(data)
            if not document.root:
                return _empty((NOT_B2S,))
            return _facts_of(document, data)
    except OSError as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return _empty((UNREADABLE,))


def _empty(problems: tuple[str, ...]) -> Backglass:
    return Backglass(
        author="", artwork="", rom="", players=None, grill=0, grill_small=None,
        two_versions=False, size=None, lamps=0, still=False, scoreview_art=None,
        problems=problems)


def read(path: Path) -> Backglass | None:
    """What the file says, or None when it is not there. A file that cannot be read
    comes back with its `problems` set, and nothing else."""
    path = Path(path)
    token = sized_media.version(path)
    if token is None:
        return None
    key = str(path)
    with _facts_lock:
        held = _facts.get(key)
    if held is not None and held[0] == token:
        return held[1]
    facts = _read_uncached(path)
    if UNREADABLE in facts.problems:
        return facts
    with _facts_lock:
        _facts.pop(key, None)
        _facts[key] = (token, facts)
        while len(_facts) > _MAX_FACTS:
            _facts.pop(next(iter(_facts)))
    return facts


def damaged(facts: Backglass) -> bool:
    """Whether the picture cannot be drawn. A score view picture that is bad costs only
    that picture."""
    return any(problem != BAD_SCOREVIEW_ART for problem in facts.problems)


def _folder(path: Path) -> Path:
    return ROOT / hashlib.blake2b(
        str(path.resolve()).encode("utf-8"), digest_size=8).hexdigest()


def _cached(path: Path, stem: str, suffix: str, make: Callable[[Path], None]) -> Path:
    """The picture `stem` made from this version of the file, made by `make(target)` on
    first ask. Pictures of an older version are removed."""
    path = path.resolve()
    folder = _folder(path)
    token = sized_media.version(path)
    if token is None:
        raise BackglassError(path)
    target = folder / f"{stem}-{token}{suffix}"
    if target.is_file():
        return target
    with _render_lock:
        # The version is read again once the lock is held, so a render that waited never
        # writes an older file's picture under a newer file's name.
        token = sized_media.version(path)
        if token is None:
            raise BackglassError(path)
        target = folder / f"{stem}-{token}{suffix}"
        if target.is_file():
            return target
        folder.mkdir(parents=True, exist_ok=True)
        with staged_for(target) as partial:
            make(partial)
    for older in folder.glob(f"{stem}-*{suffix}"):
        if older != target:
            older.unlink(missing_ok=True)
    return target


def _flat(picture: PILImage) -> PILImage:
    """The picture on black, so nothing behind it shows through."""
    from PIL import Image

    if picture.mode == "RGB":
        return picture
    flat = Image.new("RGB", picture.size, (0, 0, 0))
    rgba = picture.convert("RGBA")
    flat.paste(rgba, (0, 0), rgba.getchannel("A"))
    return flat


def _draw(path: Path, look: str, version: str, cut_grill: bool, target: Path) -> None:
    from PIL import Image

    number = _VERSION_NUMBER[version]
    with _opened(path) as data:
        if data is None:
            raise BackglassError(path)
        document = _scan(data)
        dark = _dark_span(document)
        if dark is None or not document.root:
            raise BackglassError(path)
        lit = None
        if look == "on" and "BackglassOffImage" in document.images:
            lit = document.images.get("BackglassOnImage")
        try:
            picture = Image.open(io.BytesIO(_decode(data, lit or dark)))
            picture.load()
        except Exception as exc:
            raise BackglassError(path) from exc
        canvas = _flat(picture)
        if lit is None and look != "off":
            two_versions = document.values.get("DualBackglass") == "1"
            for lamp in sorted(document.lamps, key=lambda one: one.z):
                if lamp.parent == "DMD" or not lamp.visible or lamp.image is None:
                    continue
                if two_versions and lamp.dual not in (0, number):
                    continue
                if look == "on" and not lamp.initial:
                    continue
                _lay(canvas, data, lamp)
        if cut_grill:
            height = _grill_height(document)
            if 0 < height < canvas.height:
                canvas = canvas.crop((0, 0, canvas.width, canvas.height - height))
        canvas.save(target, format="PNG", compress_level=3)


def _grill_height(document: _Document) -> int:
    try:
        return max(0, int(document.values.get("GrillHeight", "0")))
    except ValueError:
        return 0


def _lay(canvas: PILImage, data: bytes, lamp: _Lamp) -> None:
    """One lamp's picture over the backglass where it sits. A lamp that cannot be decoded
    is left out; the rest of the picture still draws."""
    from PIL import Image

    assert lamp.image is not None
    try:
        with Image.open(io.BytesIO(_decode(data, lamp.image))) as opened:
            bulb = opened.convert("RGBA")
    except Exception:
        return
    if lamp.width > 0 and lamp.height > 0 and bulb.size != (lamp.width, lamp.height):
        if lamp.width * lamp.height > _MAX_LAMP_AREA:
            return
        bulb = bulb.resize((lamp.width, lamp.height), Image.Resampling.LANCZOS)
    try:
        canvas.paste(bulb.convert("RGB"), (lamp.x, lamp.y), bulb.getchannel("A"))
    except (OverflowError, ValueError):
        return


def picture(path: Path, look: Look = "on", version: Version = "authentic",
            cut_grill: bool = False) -> Path:
    """The backglass picture as a cached file.

    `off` is the art as the file holds it, `on` the lamps the author marked lit at
    start laid over it (or the file's own lit picture when it has one), `every` all of
    them. `version` picks which lamps a two-version file draws. `cut_grill` drops the
    bottom `grill` pixels. Raises BackglassError for a file with no picture to draw, and
    RuntimeError for a look or version that is not one of LOOKS and VERSIONS.
    """
    if look not in LOOKS:
        raise RuntimeError(f"unknown look {look!r}")
    if version not in VERSIONS:
        raise RuntimeError(f"unknown version {version!r}")
    path = Path(path)
    stem = f"{look}-{version}-{'cut' if cut_grill else 'whole'}"
    return _cached(
        path, stem, ".png",
        lambda target: _draw(path, look, version, cut_grill, target))


def scoreview_art(path: Path) -> Path | None:
    """The art drawn on the score view window as a cached file, or None when the file has
    none or it cannot be read."""
    path = Path(path)
    facts = read(path)
    if facts is None or facts.scoreview_art is None:
        return None
    from PIL import Image

    def make(target: Path) -> None:
        with _opened(path) as data:
            if data is None:
                raise BackglassError(path)
            span = _scan(data).images.get("DMDImage")
            if span is None:
                raise BackglassError(path)
            raw = _decode(data, span)
        with Image.open(io.BytesIO(raw)) as opened:
            opened.convert("RGBA").save(target, format="PNG", compress_level=3)

    try:
        return _cached(path, "scoreview", ".png", make)
    except (BackglassError, ValueError, OSError):
        return None
