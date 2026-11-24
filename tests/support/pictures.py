"""Pictures made in memory, for a test that needs bytes a browser would upload."""

from __future__ import annotations

import io

from PIL import Image

RED = (200, 30, 30)
BLUE = (30, 30, 200)
GREEN = (30, 200, 30)


def png(width: int, height: int, color: tuple[int, int, int] = RED, fmt: str = "PNG") -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, format=fmt)
    return out.getvalue()


def banded(width: int, height: int) -> bytes:
    """Red on the left sixth, green on the right sixth, blue between: a centre square of a
    picture twice as wide as it is tall is all blue."""
    picture = Image.new("RGB", (width, height), BLUE)
    edge = width // 6
    picture.paste(RED, (0, 0, edge, height))
    picture.paste(GREEN, (width - edge, 0, width, height))
    out = io.BytesIO()
    picture.save(out, format="PNG")
    return out.getvalue()


def animation() -> bytes:
    """A GIF of two frames, red first."""
    frames = [Image.new("RGB", (40, 20), color) for color in (RED, BLUE)]
    out = io.BytesIO()
    frames[0].save(out, format="GIF", save_all=True, append_images=frames[1:])
    return out.getvalue()
