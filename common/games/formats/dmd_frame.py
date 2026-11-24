"""Where Visual Pinball's Automatic puts the DMD on a backglass picture.

`find` is a port of `DMDOverlay::SearchDmdSubFrame` (vpinball/vpinball, plugins/b2s): the
largest dark rectangle in the art, fitted to the DMD's aspect ratio, found by halving a
luminance threshold over seven passes. It runs once on the picture and its numbers are
written as a plain position.

The result is in Visual Pinball's own units, not the picture's: the picture's rows are
scanned from its top, as the texture is held in memory, and the answer is turned over by
`height - 1 - y - h`.

The answer is the last successful pass's frame. A picture over `LARGE` pixels runs the
passes on a shrunk copy, then repeats only that last threshold at full size around the
frame the copy found, so it matches the full-size passes wherever that window holds the
whole dark rectangle.
"""

from __future__ import annotations

import math
import re
from array import array
from struct import pack, unpack
from typing import NamedTuple

from PIL import Image, ImageFilter

PASSES = 7
THIRD = 3
LARGE = 1_000_000
SHRUNK = 90_000
MARGIN = 4

Box = tuple[int, int, int, int]
TALL = re.compile(b"\x01+")


class Frame(NamedTuple):
    x: int
    y: int
    width: int
    height: int


class Plane(NamedTuple):
    """Luminance of a picture, or of the part of it that starts at `(x, y)`."""

    data: array
    width: int
    x: int
    y: int


def find(image: Image.Image, aspect: float = 4.0) -> Frame | None:
    """The DMD's frame on `image`, or None where no dark rectangle is wide enough: a third
    of the picture's width, and more than six paddings by three."""
    rgb = image.convert("RGB")
    wide, high = rgb.size
    padding = (2 * wide) // 1920
    ratio = _single(aspect)
    step = _step(wide, high)
    if step == 1:
        plane = _luminance(rgb, (0, 0, wide, high))
        best, _ = _passes(plane, (0, 0, wide, high), wide, padding, padding, ratio)
    else:
        best = _refined(rgb, step, padding, ratio)
    if best[2] == 0 or best[3] == 0:
        return None
    x, y, across, down = best
    return Frame(x, high - 1 - y - down, across, down)


def _step(wide: int, high: int) -> int:
    """How many pixels to a side the first search reads as one."""
    if wide * high <= LARGE:
        return 1
    return math.ceil(math.sqrt(wide * high / SHRUNK))


def _refined(rgb: Image.Image, step: int, padding: int, ratio: float) -> Box:
    """The frame the shrunk passes found, searched again at full size around itself at the
    threshold that found it; every pass at full size where that threshold finds none."""
    wide, high = rgb.size
    small = rgb.convert("L").filter(ImageFilter.MaxFilter(2 * (step // 2) + 1)).reduce(step)
    small_wide, small_high = small.size
    rough, limit = _passes(Plane(array("f", tuple(small.tobytes())), small_wide, 0, 0),
                           (0, 0, small_wide, small_high), small_wide, padding // step,
                           padding / step, ratio)
    if rough[2] == 0 or rough[3] == 0:
        return rough
    x, y, across, down = (value * step for value in rough)
    around, above = across // 4 + MARGIN * step, down // 4 + MARGIN * step
    left, first = max(0, x - around), max(0, y - above)
    window = (left, first, min(wide, x + across + around) - left,
              min(high, y + down + above) - first)
    plane = _luminance(rgb, window)
    sub, _ = _largest_dark(plane, window, limit, wide, padding, padding, ratio)
    if sub[3] > 0:
        return sub
    return _passes(plane, window, wide, padding, padding, ratio)[0]


def _passes(plane: Plane, search: Box, wide: int, padding: int, room: float, ratio: float
            ) -> tuple[Box, float]:
    """The best frame over the halving passes inside `search`, and the threshold of the pass
    that found it; `(0, 0, 0, 0)` for none."""
    low, top = 0.0, 256.0
    best, found = (0, 0, 0, 0), top
    for _ in range(PASSES):
        limit = (low + top) * 0.5
        sub, next_search = _largest_dark(plane, search, limit, wide, padding, room, ratio)
        if sub[3] > 0:
            top = found = limit
            best = sub
            search = next_search
        else:
            low = limit
    return best, found


def _single(value: float) -> float:
    """A value as the C++ holds it, a 32-bit float."""
    return float(unpack("f", pack("f", value))[0])


def _luminance(rgb: Image.Image, area: Box) -> Plane:
    """Rec.601 luma of each pixel of `area`, rounded to a 32-bit float after each operation,
    as the original computes it. A grey on a threshold's own value falls the same side."""
    left, first, across, down = area
    data = rgb.crop((left, first, left + across, first + down)).tobytes()
    red = [_single(_single(0.299) * value) for value in range(256)]
    green = [_single(_single(0.587) * value) for value in range(256)]
    blue = [_single(_single(0.114) * value) for value in range(256)]
    partial = array("f", [red[r] + green[g] for r, g in zip(data[0::3], data[1::3], strict=True)])
    return Plane(array("f", [low + blue[b] for low, b in zip(partial, data[2::3], strict=True)]),
                 across, left, first)


def _largest_dark(plane: Plane, search: Box, limit: float, wide: int, padding: int,
                  room: float, ratio: float) -> tuple[Box, Box]:
    """The best frame of one pass, and the area the next pass searches: every rectangle the
    pass considered, joined. A box counts when it is wider than six `room` and taller than
    three; `padding` is what a frame gives up inside it; `wide` is the whole picture's."""
    left, first, across, down = search
    wide_f = float(wide)
    narrow, short = 6 * room, 3 * room
    heights = [0] * across
    best = (0, 0, 0, 0)
    best_score = _score(best, wide_f)
    near, far, top, bottom = wide, 0, first + down, 0
    for y in range(first, first + down):
        start = (y - plane.y) * plane.width + left - plane.x
        heights = [h + 1 if value < limit else 0
                   for h, value in zip(heights, plane.data[start:start + across], strict=True)]
        tall = bytes(h > short for h in heights)
        for run in TALL.finditer(tall):
            if run.end() - run.start() <= narrow:
                continue
            for x, span, height in _boxes(heights, run.start(), run.end(), narrow, short):
                x += left
                near, far = min(near, x), max(far, x + span)
                top, bottom = min(top, y - height + 1), y + 1
                frame = _fit((x, y - height + 1, span, height), padding, ratio)
                if THIRD * frame[2] >= wide and (score := _score(frame, wide_f)) > best_score:
                    best, best_score = frame, score
    if far == 0:
        return best, (0, 0, 0, 0)
    return best, (near, top, far - near, bottom - top)


def _boxes(heights: list[int], start: int, end: int, narrow: float, short: float
           ) -> list[tuple[int, int, int]]:
    """The boxes of one row's run of columns taller than `short`, in the order the stack
    pops them: `(x, width, height)`, relative to the row's search area."""
    found = []
    stack: list[int] = []
    for x in range(start, end + 1):
        while stack and (x == end or heights[stack[-1]] > heights[x]):
            height = heights[stack.pop()]
            span = x - start if not stack else x - stack[-1] - 1
            if span > narrow and height > short:
                found.append((start if not stack else stack[-1] + 1, span, height))
        if x < end:
            stack.append(x)
    return found


def _fit(box: Box, padding: int, ratio: float) -> Box:
    """The box less its padding, narrowed or shortened to the DMD's aspect ratio and
    centered in what is left."""
    x, y, across, down = box[0] + padding, box[1] + padding, box[2] - 2 * padding, \
        box[3] - 2 * padding
    shape = across / down
    if (shape > ratio) if abs(shape - ratio) > 1e-4 else (_single(shape) > ratio):
        fitted = _cut(down * ratio)
        return x + (across - fitted) // 2, y, fitted, down
    fitted = _cut(across / ratio)
    return x, y + (down - fitted) // 2, across, fitted


def _cut(value: float) -> int:
    """`int(_single(value))`; the 32-bit rounding only matters within a hair of a whole
    number, so the conversion is skipped everywhere else."""
    return int(value) if abs(value - round(value)) > 1e-3 else int(_single(value))


def _score(frame: Box, wide: float) -> float:
    """Large, and near the picture's center."""
    return frame[2] / wide - 1.0 * abs(0.5 - (frame[0] + frame[2] // 2) / wide)
