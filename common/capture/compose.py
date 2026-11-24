"""Every screen in one picture."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import adapters
from .geometry import Turn

# Left to right along the top.
ROW = (adapters.BACKGLASS, adapters.SCOREVIEW)


@dataclass(frozen=True)
class Box:
    window: str
    x: int
    y: int
    width: int
    height: int


def layout(sizes: Mapping[str, tuple[int, int]]) -> tuple[tuple[int, int], list[Box]]:
    """Where each screen goes, from the size of each as it is turned. The canvas is the
    playfield's width, or the row's own where there is no playfield."""
    row = [window for window in ROW if window in sizes]
    playfield = sizes.get(adapters.PLAYFIELD)
    tallest = max((sizes[window][1] for window in row), default=0)
    widths = [sizes[window][0] * tallest / sizes[window][1] for window in row]
    natural = sum(widths)
    width = playfield[0] if playfield else round(natural)
    scale = width / natural if natural else 0.0
    height = round(tallest * scale)
    boxes: list[Box] = []
    x = 0
    for index, window in enumerate(row):
        right = width if index == len(row) - 1 else round(sum(widths[:index + 1]) * scale)
        boxes.append(Box(window, x, 0, right - x, height))
        x = right
    if playfield:
        boxes.append(Box(adapters.PLAYFIELD, 0, height, playfield[0], playfield[1]))
    return (width, height + (playfield[1] if playfield else 0)), boxes


_TRANSPOSE = {90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180,
              270: Image.Transpose.ROTATE_270}


def turned(image: Image.Image, turn: Turn) -> Image.Image:
    """`turn` applied: mirrored first where it flips, then rotated counter-clockwise."""
    if turn.flip:
        image = image.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    return image.transpose(_TRANSPOSE[turn.ccw]) if turn.ccw in _TRANSPOSE else image


def compose(stills: Mapping[str, Path], turns: Mapping[str, Turn]) -> Image.Image:
    """One picture of the screens in `stills`, each turned as `turns` says."""
    shown: dict[str, Image.Image] = {}
    for window, path in stills.items():
        with Image.open(path) as opened:
            shown[window] = turned(opened.convert("RGB"), turns.get(window, Turn()))
    size, boxes = layout({window: image.size for window, image in shown.items()})
    canvas = Image.new("RGB", size)
    for box in boxes:
        image = shown[box.window]
        if image.size != (box.width, box.height):
            image = image.resize((box.width, box.height), Image.Resampling.LANCZOS)
        canvas.paste(image, (box.x, box.y))
    return canvas
