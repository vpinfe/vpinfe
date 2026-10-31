"""Which way a recorded frame turns on its way from the screen to the file.

A `Turn` is one of the square's eight symmetries: mirrored left to right where `flip`,
then rotated `ccw` degrees counter-clockwise.
"""

from __future__ import annotations

from dataclasses import dataclass

BOTTOM_RIGHT = "bottom_right"
BOTTOM_LEFT = "bottom_left"
UPRIGHT = "upright"
ORIENTATIONS = (BOTTOM_RIGHT, BOTTOM_LEFT, UPRIGHT)


@dataclass(frozen=True)
class Turn:
    flip: bool = False
    ccw: int = 0

    def then(self, after: Turn) -> Turn:
        """This turn followed by `after`. A mirror reverses the rotation before it."""
        ccw = after.ccw + (-self.ccw if after.flip else self.ccw)
        return Turn(self.flip != after.flip, ccw % 360)

    @property
    def filters(self) -> list[str]:
        """As FFmpeg video filters, in order."""
        found = ["hflip"] if self.flip else []
        return found + {90: ["transpose=2"], 180: ["hflip", "vflip"],
                        270: ["transpose=1"]}.get(self.ccw, [])

    def size(self, width: int, height: int) -> tuple[int, int]:
        return (height, width) if self.ccw in (90, 270) else (width, height)


NONE = Turn()

# The stored orientation from the table upright. Bottom at the right is the upright table
# turned a quarter counter-clockwise.
_STORED = {BOTTOM_RIGHT: Turn(ccw=90), BOTTOM_LEFT: Turn(ccw=270), UPRIGHT: NONE}

# Wayland's output transforms, as sway's IPC names them and Hyprland numbers them.
_SWAY = {"normal": NONE, "90": Turn(ccw=90), "180": Turn(ccw=180), "270": Turn(ccw=270),
         "flipped": Turn(True), "flipped-90": Turn(True, 90),
         "flipped-180": Turn(True, 180), "flipped-270": Turn(True, 270)}
_HYPRLAND = (NONE, Turn(ccw=90), Turn(ccw=180), Turn(ccw=270),
             Turn(True), Turn(True, 90), Turn(True, 180), Turn(True, 270))


def sway_transform(said: object) -> Turn:
    """An output's `transform` in sway's IPC, as the turn from its buffer to the screen.
    One it does not name is taken as none."""
    return _SWAY.get(str(said or "normal"), NONE)


def hyprland_transform(said: object) -> Turn:
    try:
        return _HYPRLAND[int(str(said))]
    except (ValueError, IndexError):
        return NONE


def wayland_transform(said: object) -> Turn:
    """A `wl_output` transform by number, 0 to 7, as Mutter and Hyprland both give it."""
    return hyprland_transform(said)


# X11's and KDE's own words for a quarter turn. Which way each one turns is the desktop's
# word taken as printed, until a really turned screen has been read against it.
_QUARTER = {"normal": NONE, "left": Turn(ccw=90), "inverted": Turn(ccw=180),
            "right": Turn(ccw=270)}
_KSCREEN = {1: "normal", 2: "left", 4: "inverted", 8: "right"}


def xrandr_turn(word: str, axes: str = "") -> Turn:
    """An output's `normal`, `left`, `inverted` or `right`, with the axes xrandr says it is
    reflected on: `X axis` mirrors, `Y axis` mirrors and turns half way, and both together
    are a half turn."""
    turn = _QUARTER.get(word, NONE)
    both = "X and Y axis" in axes
    flip = ("X axis" in axes or "Y axis" in axes) and not both
    half = both or ("Y axis" in axes and not both)
    return Turn(turn.flip != flip, (turn.ccw + (180 if half else 0)) % 360)


def degrees_turn(degrees: object) -> Turn:
    """A rotation an operating system states in degrees (Windows' `dmDisplayOrientation` as
    0 to 270, macOS's `CGDisplayRotation`), taken as counter-clockwise. One that is not a
    quarter turn is taken as none."""
    try:
        value = int(str(degrees))
    except ValueError:
        return NONE
    return Turn(ccw=value) if value in (90, 180, 270) else NONE


def kscreen_word(value: object) -> str:
    """KScreen's rotation, number or name, as the word X11 uses for it, or "" for one it
    does not know."""
    if isinstance(value, bool):
        return ""
    if isinstance(value, int):
        return _KSCREEN.get(value, "")
    said = str(value or "").strip().lower()
    return said if said in _QUARTER else ""


def playfield(transform: Turn, rotation: int, orientation: str) -> Turn:
    """`rotation` is the clockwise turn VPinFE's UI makes to face the player, so the
    same number counter-clockwise undoes it."""
    upright = transform.then(Turn(ccw=int(rotation) % 360))
    return upright.then(_STORED.get(orientation, _STORED[BOTTOM_RIGHT]))


def screen(transform: Turn) -> Turn:
    """Any other window, stored as its screen shows it."""
    return transform
