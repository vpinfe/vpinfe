"""Which slot an unnamed picture's shape points at.

`fits` lists every slot the shape suits and `suggestion` names one only when the shape suits
exactly one, so a picture that is a backglass or a playfield by shape is never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops

# How far from a slot's usual proportions a picture can sit and still suit it, as a share
# of the ratio.
TOLERANCE = 0.05

# Width over height of what the library import writes for each kind.
_WIDE = 1920 / 1080
_DMD = 1080 / 542


@dataclass(frozen=True)
class Shape:
    width: int
    height: int
    transparent: bool

    @property
    def ratio(self) -> float:
        return self.width / self.height if self.height else 0.0


def _near(ratio: float, anchor: float) -> bool:
    return abs(ratio - anchor) <= anchor * TOLERANCE


def fits(shape: Shape) -> tuple[str, ...]:
    """The slots this shape suits, best first. A wheel is the only kind with see-through
    pixels, at any ratio."""
    if shape.transparent:
        return ("wheel",)
    if _near(shape.ratio, _WIDE):
        return ("backglass", "playfield")
    if _near(shape.ratio, _DMD):
        return ("scoreview",)
    return ()


def suggestion(shape: Shape) -> str:
    """The one slot the shape suits, or "" when it suits none or several."""
    slots = fits(shape)
    return slots[0] if len(slots) == 1 else ""


def read(path: Path) -> Shape | None:
    """The pixel size and transparency of a picture, or None when it cannot be read."""
    try:
        with Image.open(path) as image:
            width, height = image.size
            if "A" in image.getbands():
                alpha = image.getchannel("A")
            elif "transparency" in image.info:
                alpha = image.convert("RGBA").getchannel("A")
            else:
                alpha = None
            transparent = alpha is not None and ImageChops.invert(alpha).getbbox() is not None
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    return Shape(width, height, transparent)
