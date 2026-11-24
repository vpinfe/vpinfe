"""What a media file is, read from its own header: format, pixel size, running time,
and a video's codec.

Headers only. Nothing here decodes a frame, and every read is bounded, because a library
on a share pays for each byte.
"""

from __future__ import annotations

import logging
import math
import struct
import threading
from collections import OrderedDict
from collections.abc import Iterator
from pathlib import Path
from typing import IO, Any

logger = logging.getLogger("vpinfe.common.games.media_probe")

# Enough for an ID3 tag's worth of padding to be skipped and the first frames read.
_MP3_WINDOW = 64 * 1024
# An Ogg page is at most 65,307 bytes, so the last one starts within this of the end.
_OGG_TAIL = 65_536 + 27

# A sample entry's type, as `GET /frontend/browser` names the format.
_VIDEO_CODECS = {b"avc1": "h264", b"avc3": "h264", b"hvc1": "hevc", b"hev1": "hevc",
                 b"vp09": "vp9", b"av01": "av1"}

# A WebM track's CodecID, named as the sample entries above are.
_WEBM_CODECS = {"V_VP9": "vp9", "V_AV1": "av1", "V_MPEG4/ISO/AVC": "h264",
                "V_MPEGH/ISO/HEVC": "hevc"}

_KEPT = 4096
_kept: OrderedDict[tuple[str, int, int], dict[str, Any]] = OrderedDict()
_kept_lock = threading.Lock()


def probe(path: Path) -> dict[str, Any]:
    """`format`, `width`, `height`, `duration_s` and `video_codec`, each None where the
    file does not say. Never raises."""
    try:
        stat = path.stat()
    except OSError:
        return _probe(path, 0)
    key = (str(path), stat.st_size, stat.st_mtime_ns)
    with _kept_lock:
        held = _kept.get(key)
        if held is not None:
            _kept.move_to_end(key)
            return dict(held)
    facts = _probe(path, stat.st_size)
    with _kept_lock:
        _kept[key] = facts
        while len(_kept) > _KEPT:
            _kept.popitem(last=False)
    return dict(facts)


def _probe(path: Path, size: int) -> dict[str, Any]:
    facts: dict[str, Any] = {"format": None, "width": None, "height": None,
                             "duration_s": None, "video_codec": None}
    try:
        with path.open("rb") as handle:
            head = handle.read(16)
            if head[4:8] in (b"ftyp", b"moov", b"mdat", b"free", b"wide"):
                facts.update(_mp4(handle, size))
            elif head[:4] == _EBML_MAGIC:
                facts.update(_webm(handle, size))
            elif head[:4] == b"OggS":
                facts.update(_ogg(handle, size))
            elif head[:3] == b"ID3" or (head[:1] == b"\xff" and head[1] & 0xE0 == 0xE0):
                facts.update(_mp3(handle, size))
            elif head[:5] == b"%PDF-":
                facts["format"] = "PDF"
            else:
                facts.update(_image(path))
    except Exception:
        logger.debug("Could not read the header of %s", path, exc_info=True)
    if not facts["format"] and path.suffix:
        facts["format"] = path.suffix[1:].upper()
    return facts


def _image(path: Path) -> dict[str, Any]:
    from PIL import Image
    try:
        with Image.open(path) as img:
            return {"format": img.format, "width": img.size[0], "height": img.size[1]}
    except Exception:
        return {}


# --- MP4 ---------------------------------------------------------------------------

def _boxes(handle: IO[bytes], start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """(type, body start, box end) for each box between two offsets, by seeking."""
    at = start
    while at + 8 <= end:
        handle.seek(at)
        header = handle.read(8)
        if len(header) < 8:
            return
        size, kind = struct.unpack(">I4s", header)
        skip = 8
        if size == 1:
            wide = handle.read(8)
            if len(wide) < 8:
                return
            size, skip = struct.unpack(">Q", wide)[0], 16
        elif size == 0:
            size = end - at
        if size < skip:
            return
        yield kind, at + skip, at + size
        at += size


def _mp4(handle: IO[bytes], size: int) -> dict[str, Any]:
    facts: dict[str, Any] = {"format": "MP4"}
    moov = next(((body, end) for kind, body, end in _boxes(handle, 0, size)
                 if kind == b"moov"), None)
    if moov is None:
        return facts
    for kind, body, end in _boxes(handle, *moov):
        if kind == b"mvhd":
            facts["duration_s"] = _mvhd(handle, body)
        elif kind == b"trak":
            if not facts.get("width"):
                tkhd = next((start for inner, start, _ in _boxes(handle, body, end)
                             if inner == b"tkhd"), None)
                if tkhd is not None:
                    width, height = _tkhd(handle, tkhd)
                    if width and height:
                        facts["width"], facts["height"] = width, height
            if not facts.get("video_codec"):
                facts["video_codec"] = _video_codec(handle, body, end)
    return facts


def _video_codec(handle: IO[bytes], body: int, end: int) -> str | None:
    """The codec of a track's first sample entry, where it is a video one a browser is
    asked about."""
    at = (body, end)
    for wanted in (b"mdia", b"minf", b"stbl", b"stsd"):
        found = next(((start, stop) for kind, start, stop in _boxes(handle, *at)
                      if kind == wanted), None)
        if found is None:
            return None
        at = found
    # Past the full box's version, flags and entry count, to the first entry's type.
    handle.seek(at[0] + 8)
    entry = handle.read(8)
    return _VIDEO_CODECS.get(entry[4:8]) if len(entry) == 8 else None


def _mvhd(handle: IO[bytes], body: int) -> float | None:
    handle.seek(body)
    version = handle.read(4)[0]
    if version == 1:
        handle.seek(16, 1)
        scale, length = struct.unpack(">IQ", handle.read(12))
        unknown = 0xFFFFFFFFFFFFFFFF
    else:
        handle.seek(8, 1)
        scale, length = struct.unpack(">II", handle.read(8))
        unknown = 0xFFFFFFFF
    return length / scale if scale and length and length != unknown else None


def _tkhd(handle: IO[bytes], body: int) -> tuple[int, int]:
    handle.seek(body)
    version = handle.read(4)[0]
    # To the 16.16 width and height, past the ids, times, layer, volume and matrix.
    handle.seek((32 if version == 1 else 20) + 52, 1)
    width, height = struct.unpack(">II", handle.read(8))
    return width >> 16, height >> 16


# --- WebM --------------------------------------------------------------------------

_EBML_MAGIC = b"\x1a\x45\xdf\xa3"
_SEGMENT, _INFO, _TRACKS, _CLUSTER = 0x18538067, 0x1549A966, 0x1654AE6B, 0x1F43B675
_TIMECODE_SCALE, _DURATION = 0x2AD7B1, 0x4489
_TRACK_ENTRY, _TRACK_TYPE, _CODEC_ID, _VIDEO = 0xAE, 0x83, 0x86, 0xE0
_PIXEL_WIDTH, _PIXEL_HEIGHT = 0xB0, 0xBA
_TRACK_TYPE_VIDEO = 1
# Nanoseconds one timecode unit lasts, where Info does not say.
_DEFAULT_TIMECODE_SCALE = 1_000_000
# Elements read at one level before the walk gives up, so a file of nothing but padding
# ends it.
_EBML_ELEMENTS = 1024


def _vint_length(first: int) -> int:
    """Bytes in an EBML variable-length integer, from its first byte; 9 where invalid."""
    return 9 - first.bit_length() if first else 9


def _elements(handle: IO[bytes], start: int, end: int) -> Iterator[tuple[int, int, int]]:
    """(id, body start, body end) for each EBML element between two offsets, by seeking.
    An element of unknown size runs to `end`."""
    at = start
    for _ in range(_EBML_ELEMENTS):
        if at + 2 > end:
            return
        handle.seek(at)
        head = handle.read(12)
        if not head:
            return
        id_length = _vint_length(head[0])
        if id_length > 4 or len(head) <= id_length:
            return
        size_length = _vint_length(head[id_length])
        if size_length > 8 or len(head) < id_length + size_length:
            return
        raw = int.from_bytes(head[id_length:id_length + size_length], "big")
        unknown = (1 << 7 * size_length) - 1
        length = raw & unknown
        body = at + id_length + size_length
        stop = end if length == unknown else min(body + length, end)
        yield int.from_bytes(head[:id_length], "big"), body, stop
        at = stop


def _uint(handle: IO[bytes], body: int, end: int) -> int:
    handle.seek(body)
    return int.from_bytes(handle.read(max(0, min(end - body, 8))), "big")


def _float(handle: IO[bytes], body: int, end: int) -> float | None:
    handle.seek(body)
    data = handle.read(max(0, min(end - body, 8)))
    if len(data) == 4:
        return float(struct.unpack(">f", data)[0])
    return float(struct.unpack(">d", data)[0]) if len(data) == 8 else None


def _webm(handle: IO[bytes], size: int) -> dict[str, Any]:
    facts: dict[str, Any] = {"format": "WEBM"}
    segment = next(((body, end) for ident, body, end in _elements(handle, 0, size)
                    if ident == _SEGMENT), None)
    if segment is None:
        return facts
    scale, length, tracked = _DEFAULT_TIMECODE_SCALE, None, False
    for ident, body, end in _elements(handle, *segment):
        if ident == _INFO:
            for inner, start, stop in _elements(handle, body, end):
                if inner == _TIMECODE_SCALE:
                    scale = _uint(handle, start, stop) or scale
                elif inner == _DURATION:
                    length = _float(handle, start, stop)
        elif ident == _TRACKS:
            facts.update(_webm_video(handle, body, end))
            tracked = True
        elif ident == _CLUSTER:
            # Media starts here, and an unknown-size cluster would swallow what follows.
            break
        if tracked and length is not None:
            break
    if length is not None and math.isfinite(length) and length > 0:
        facts["duration_s"] = length * scale / 1e9
    return facts


def _webm_video(handle: IO[bytes], body: int, end: int) -> dict[str, Any]:
    """The first video track's codec and pixel size, where Tracks has one."""
    for ident, start, stop in _elements(handle, body, end):
        if ident != _TRACK_ENTRY:
            continue
        kind, codec, width, height = 0, "", 0, 0
        for inner, first, last in _elements(handle, start, stop):
            if inner == _TRACK_TYPE:
                kind = _uint(handle, first, last)
            elif inner == _CODEC_ID:
                handle.seek(first)
                codec = handle.read(min(last - first, 64)).rstrip(b"\0").decode("latin-1")
            elif inner == _VIDEO:
                for field, at, to in _elements(handle, first, last):
                    if field == _PIXEL_WIDTH:
                        width = _uint(handle, at, to)
                    elif field == _PIXEL_HEIGHT:
                        height = _uint(handle, at, to)
        if kind == _TRACK_TYPE_VIDEO:
            return {"video_codec": _WEBM_CODECS.get(codec),
                    "width": width or None, "height": height or None}
    return {}


# --- MP3 ---------------------------------------------------------------------------

_MP3_RATES = {3: (44100, 48000, 32000), 2: (22050, 24000, 16000), 0: (11025, 12000, 8000)}
_MP3_KBPS = {
    (3, 3): (0, 32, 64, 96, 128, 160, 192, 224, 256, 288, 320, 352, 384, 416, 448),
    (3, 2): (0, 32, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320, 384),
    (3, 1): (0, 32, 40, 48, 56, 64, 80, 96, 112, 128, 160, 192, 224, 256, 320),
    (2, 3): (0, 32, 48, 56, 64, 80, 96, 112, 128, 144, 160, 176, 192, 224, 256),
    (2, 2): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
    (2, 1): (0, 8, 16, 24, 32, 40, 48, 56, 64, 80, 96, 112, 128, 144, 160),
}


def _frame(data: bytes, at: int) -> dict[str, Any] | None:
    """The MPEG audio frame header at `at`, or None where there is not one."""
    if at + 4 > len(data) or data[at] != 0xFF or data[at + 1] & 0xE0 != 0xE0:
        return None
    version = (data[at + 1] >> 3) & 3
    layer = (data[at + 1] >> 1) & 3
    rate_index = (data[at + 2] >> 2) & 3
    bitrate_index = data[at + 2] >> 4
    if version == 1 or layer == 0 or rate_index == 3 or bitrate_index in (0, 15):
        return None
    table = _MP3_KBPS[(3 if version == 3 else 2, layer)]
    kbps = table[bitrate_index]
    rate = _MP3_RATES[version][rate_index]
    padding = (data[at + 2] >> 1) & 1
    samples = 384 if layer == 3 else (1152 if version == 3 or layer == 2 else 576)
    length = ((12 * kbps * 1000 // rate + padding) * 4 if layer == 3
              else samples // 8 * kbps * 1000 // rate + padding)
    return {"version": version, "rate": rate, "kbps": kbps, "samples": samples,
            "length": length, "mono": data[at + 3] >> 6 == 3}


def _mp3(handle: IO[bytes], size: int) -> dict[str, Any]:
    facts: dict[str, Any] = {"format": "MP3"}
    handle.seek(0)
    head = handle.read(10)
    start = 0
    if head[:3] == b"ID3" and len(head) == 10:
        held = (head[6] & 0x7F) << 21 | (head[7] & 0x7F) << 14 | (head[8] & 0x7F) << 7 \
            | head[9] & 0x7F
        start = 10 + held + (10 if head[5] & 0x10 else 0)
    handle.seek(start)
    data = handle.read(_MP3_WINDOW)
    for at in range(len(data) - 4):
        frame = _frame(data, at)
        # Two in a row, because a lone 0xFFE pattern turns up inside audio data too.
        if frame is None or (at + frame["length"] + 4 <= len(data)
                             and _frame(data, at + frame["length"]) is None):
            continue
        side = (32 if not frame["mono"] else 17) if frame["version"] == 3 \
            else (17 if not frame["mono"] else 9)
        tag = data[at + 4 + side:at + 8 + side]
        if tag in (b"Xing", b"Info"):
            flags = struct.unpack(">I", data[at + 8 + side:at + 12 + side])[0]
            if flags & 1:
                frames = struct.unpack(">I", data[at + 12 + side:at + 16 + side])[0]
                facts["duration_s"] = frames * frame["samples"] / frame["rate"]
                return facts
        if data[at + 36:at + 40] == b"VBRI":
            frames = struct.unpack(">I", data[at + 50:at + 54])[0]
            facts["duration_s"] = frames * frame["samples"] / frame["rate"]
            return facts
        audio = size - start - at
        handle.seek(max(0, size - 128))
        if handle.read(3) == b"TAG":
            audio -= 128
        facts["duration_s"] = audio * 8 / (frame["kbps"] * 1000)
        return facts
    return facts


# --- Ogg ---------------------------------------------------------------------------

def _ogg(handle: IO[bytes], size: int) -> dict[str, Any]:
    facts: dict[str, Any] = {"format": "OGG"}
    handle.seek(0)
    first = handle.read(4096)
    packet = first[27 + first[26]:] if len(first) > 27 else b""
    if packet[:7] == b"\x01vorbis":
        rate, skip = struct.unpack("<I", packet[12:16])[0], 0
    elif packet[:8] == b"OpusHead":
        # Opus counts in 48 kHz whatever the input was, less the samples it pre-skips.
        rate, skip = 48_000, struct.unpack("<H", packet[10:12])[0]
    else:
        return facts
    handle.seek(max(0, size - _OGG_TAIL))
    tail = handle.read()
    at = tail.rfind(b"OggS")
    if at < 0 or at + 14 > len(tail) or not rate:
        return facts
    granule = struct.unpack("<q", tail[at + 6:at + 14])[0]
    if granule > skip:
        facts["duration_s"] = (granule - skip) / rate
    return facts
