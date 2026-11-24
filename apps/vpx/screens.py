"""Visual Pinball's half of the screens answer: where its windows are, and what to tell it.

Values in, ini keys out. Nothing here reads or writes a file, and a screen is only the
facts `Seen` lists, so Setup, a change in Settings and VPX's own Displays page all get the
same keys from the same arrangement. A key someone set in VPX is never overwritten by a
derived one: those are written only where VPX holds nothing.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from typing import Any, Protocol

from common.apps.contract import TableOrder

from . import build

PLAYFIELD = "playfield"
BACKGLASS = "backglass"
SCOREVIEW = "scoreview"
TOPPER = "topper"

# What a part holds when VPX draws it inside the playfield's window instead of on a screen.
INSIDE = "inside"

# A screen's number, INSIDE, or None for no screen.
Placement = int | str | None

DISPLAY = {PLAYFIELD: "Player.PlayfieldDisplay", BACKGLASS: "Backglass.BackglassDisplay",
           SCOREVIEW: "ScoreView.ScoreViewDisplay", TOPPER: "Topper.TopperDisplay"}
# The playfield always shows, so it has no output mode.
OUTPUT = {BACKGLASS: "Backglass.BackglassOutput", SCOREVIEW: "ScoreView.ScoreViewOutput",
          TOPPER: "Topper.TopperOutput"}
DISABLED, FLOATING, EMBEDDED = "0", "1", "2"
# The keys that place a part's window: where it sits, and how big. Drawn inside the
# playfield they are in the playfield window's pixels, and a floating window's values left
# in them cover the table.
REGION = {part: tuple(f"{key.removesuffix('Output')}{one}"
                      for one in ("WndX", "WndY", "Width", "Height"))
          for part, key in OUTPUT.items()}

VIEW_CAB_ROTATION = "TableOverride.ViewCabRotation"

# The view mode: 1 plays the cabinet view, and 0 and 2 the desktop view, which only 0 lets
# a table's Full Single Screen setup replace. A file that does not say is 0.
VIEW_MODE = "Player.BGSet"
CABINET_VIEW = "1"


def plays_cabinet_view(flat: Mapping[str, str]) -> bool:
    """Whether the launcher's ini has Visual Pinball play the cabinet view."""
    return str(flat.get(VIEW_MODE) or "").strip() == CABINET_VIEW

# The Displays group's headings, by the part each is for, and the cabinet's own.
WINDOW_HEADINGS = {"playfield": PLAYFIELD, "backglass": BACKGLASS, "scoreview": SCOREVIEW,
                   "topper": TOPPER}
CABINET_HEADING = "cabinet"
# The playfield screen's width and height in centimeters.
SCREEN_SIZE = ("Player.ScreenWidth", "Player.ScreenHeight")

# PUP over the backglass file's DMD art over the score display.
PRIORITIES = {"ScoreView.Priority.ScoreView": "1", "ScoreView.Priority.PUP": "3",
              "ScoreView.Priority.B2SLegacyDMD": "2"}
# Full DMD: the DMD drawn over the whole Score View, with the backglass's DMD strip hidden.
FULL_DMD = {"Plugin.B2SLegacy.ScoreViewDMDOverlay": "1",
            "Plugin.B2SLegacy.ScoreViewDMDAutoPos": "1", "Plugin.B2SLegacy.B2SHideGrill": "1"}
# The same, for B2S as the renderer where it keeps the DMD's place in the ini.
FULL_DMD_B2S = {"Plugin.B2S.ScoreViewDMDOverlay": "1", "Plugin.B2S.ScoreViewDMDAutoPos": "1",
                "Plugin.B2S.ShowGrill": "0"}
PRIORITY = "ScoreView.Priority."
# Every key a render priority is written to, whichever plugins draw: what is read back
# before a derived value may be written.
ALL_PRIORITIES = frozenset({*PRIORITIES, *(f"{PRIORITY}{one}" for one in ("B2S", "Nixie"))})
LEGACY, B2S_ID, NIXIE, SCOREVIEW_ID, PUP = "B2SLegacy", "B2S", "Nixie", "ScoreView", "PUP"

ZEDMD, PIN2DMD, PIXELCADE = "ZeDMD", "PIN2DMD", "Pixelcade"
DMD_KINDS = (ZEDMD, PIN2DMD, PIXELCADE)
DMD_UTIL = "Plugin.DMDUtil."
# Windows drives a panel through DmdExt, and DMDUtil beside it is unchecked.
DMD_UTIL_PLATFORMS = ("linux", "darwin")

# An embedded strip leaves a twentieth of the window's shorter side clear on each side.
_MARGIN = 20

# `name [x, y]`: where the screen sits on the desktop.
_POSITIONED = re.compile(r"(?P<name>.+?)\s*\[\s*(?P<x>-?\d+),\s*(?P<y>-?\d+)\s*\]")


class Seen(Protocol):
    """A connected screen."""

    @property
    def number(self) -> int: ...
    @property
    def name(self) -> str: ...
    @property
    def x(self) -> int: ...
    @property
    def y(self) -> int: ...
    @property
    def width(self) -> int: ...
    @property
    def height(self) -> int: ...


@dataclass(frozen=True)
class Written:
    """Ini keys to set, and the parts left out: those VPX has no name for the screen of, and
    those asked to go inside a playfield whose size is not known."""
    keys: dict[str, str]
    unnamed: tuple[str, ...] = ()
    # Parts asked to go inside a playfield whose window has no known size.
    unsized: tuple[str, ...] = ()


def display_id(screen: Seen, logged: Sequence[str] = ()) -> str:
    """The id VPX stores for this screen, or "" where nothing says what it calls it.

    The name VPX logged at the screen's position wins: SDL names a panel from its
    connector, and the model name the OS reports only stands in for that.
    """
    at = (screen.x, screen.y)
    for said in logged:
        found = _POSITIONED.fullmatch(said.strip())
        if found and (int(found["x"]), int(found["y"])) == at:
            return f"{found['name']} [{screen.x}, {screen.y}]"
    model = screen.name.strip()
    return f"{model} [{screen.x}, {screen.y}]" if model else ""


def screen_for(stored: str, screens: Sequence[Seen]) -> Seen | None:
    """The connected screen a stored id names. A position names one exactly; a name alone,
    as older builds store it, names one only where a single screen's model is in it."""
    said = str(stored or "").strip()
    if not said:
        return None
    found = _POSITIONED.fullmatch(said)
    if found:
        where = (int(found["x"]), int(found["y"]))
        return next((one for one in screens if (one.x, one.y) == where), None)
    named = [one for one in screens
             if one.name.strip() and one.name.strip().casefold() in said.casefold()]
    return named[0] if len(named) == 1 else None


def placed(values: Mapping[str, str], screens: Sequence[Seen]) -> dict[str, Placement]:
    """Where VPX has each part, from the values of its ini (a blank is ""). A window that
    is not Floating is on no screen, and a Floating one on a screen not connected is too."""
    def number(part: str) -> int | None:
        found = screen_for(values.get(DISPLAY[part]) or "", screens)
        return found.number if found is not None else None

    held: dict[str, Placement] = {PLAYFIELD: number(PLAYFIELD)}
    for part, key in OUTPUT.items():
        mode = (values.get(key) or "").strip()
        held[part] = (INSIDE if mode == EMBEDDED else number(part) if mode == FLOATING
                      else None)
    return held


def embedded_region(playfield: Seen) -> tuple[int, int, int, int]:
    """Where a part drawn inside the playfield's window sits: x, y, width and height in the
    window's pixels. A strip at the DMD's 4:1 along the window's bottom edge, centered, as
    wide as the window's shorter side less a margin each side."""
    span = min(playfield.width, playfield.height)
    margin = span // _MARGIN
    width = span - 2 * margin
    height = width // 4
    return (playfield.width - width) // 2, playfield.height - height - margin, width, height


def windows(moved: Mapping[str, Placement], screens: Sequence[Seen],
            logged: Sequence[str] = (), playfield: Seen | None = None) -> Written:
    """The Display and Output keys that put each moved part where it now is, and for a part
    drawn inside the playfield the region of `playfield`'s window it takes.

    A part whose screen VPX has no name for is left out whole: an Output of Floating with
    no Display opens on the primary display, which is no answer to the screen picked.
    Inside a playfield of no known size it is left out too, for want of a region.
    """
    keys: dict[str, str] = {}
    unnamed: list[str] = []
    unsized: list[str] = []
    for part, where in moved.items():
        if part not in DISPLAY:
            continue
        if where is None:
            keys.update({DISPLAY[PLAYFIELD]: ""} if part == PLAYFIELD
                        else {OUTPUT[part]: DISABLED})
        elif where == INSIDE:
            if part == PLAYFIELD:
                continue
            if playfield is None:
                unsized.append(part)
                continue
            keys[OUTPUT[part]] = EMBEDDED
            keys.update(zip(REGION[part], map(str, embedded_region(playfield)), strict=True))
        else:
            screen = next((one for one in screens if one.number == where), None)
            said = display_id(screen, logged) if screen is not None else ""
            if not said:
                unnamed.append(part)
                continue
            keys[DISPLAY[part]] = said
            if part != PLAYFIELD:
                keys[OUTPUT[part]] = FLOATING
    return Written(keys, tuple(unnamed), tuple(unsized))


def differs(vpinfe: Mapping[str, Placement], vpx: Mapping[str, Placement]) -> list[str]:
    """The parts VPX has somewhere other than VPinFE does. A topper differs only where
    VPinFE has a screen for one: with none, VPX's is as the person set it."""
    return [part for part in (TOPPER, BACKGLASS, SCOREVIEW, PLAYFIELD)
            if vpinfe.get(part) != vpx.get(part)
            and (part != TOPPER or vpinfe.get(TOPPER) is not None)]


def holds_nothing(values: Mapping[str, str]) -> frozenset[str]:
    """The parts whose window the ini has no value for: neither the key that places it nor
    the screen it names is set. An Output of 0 is a value."""
    return frozenset(
        part for part in DISPLAY
        if not any(str(values.get(key) or "").strip()
                   for key in (DISPLAY[part], OUTPUT.get(part, ""))))


def followed(before: Mapping[str, Placement], moved: Mapping[str, Placement],
             vpx: Mapping[str, Placement],
             blank: Collection[str] = ()) -> dict[str, Placement]:
    """Of the parts VPinFE moved, those VPX has where VPinFE had them, and those in `blank`
    that VPinFE gives a screen: a part VPX has set apart stays where it was put.

    A topper VPinFE no longer has a screen for stays too, unless a part that moved takes
    its screen, which leaves it with none.
    """
    found = {part: where for part, where in moved.items()
             if (vpx.get(part) == before.get(part) or part in blank and where is not None)
             and (part != TOPPER or where is not None)}
    held = vpx.get(TOPPER)
    if TOPPER not in found and isinstance(held, int) and held in found.values():
        found[TOPPER] = None
    return found


# The edge of the output that faces the player when VPinFE turns its page clockwise by
# this many degrees: the page's own bottom, carried round with it.
_FACING = {0.0: "bottom", 90.0: "left", 180.0: "top", 270.0: "right"}
# `ViewCabRotation` by the edge of the output that the flipper end of the table faces, for
# the output as VPX sees it, after the OS has turned it.
_PORTRAIT = {"bottom": 0.0, "left": 90.0, "top": 180.0, "right": 270.0}
_LANDSCAPE = {"right": 0.0, "bottom": 90.0, "left": 180.0, "top": 270.0}


def _edges(playfield: Seen | None) -> dict[str, float] | None:
    """The turn that faces each edge, or None where the shape is not one of the two: a
    square output is neither portrait nor landscape."""
    if playfield is None or playfield.width == playfield.height:
        return None
    return _PORTRAIT if playfield.height > playfield.width else _LANDSCAPE


def view_cab_rotation(playfield: Seen | None, rotation: str) -> str | None:
    """VPX's `ViewCabRotation` for a playfield whose page VPinFE turns by `rotation`, or
    None where the answer says nothing. The flipper end of the table goes to the edge that
    faces the player.
    """
    edges = _edges(playfield)
    turned = turn_of(rotation)
    facing = _FACING.get(turned) if turned is not None else None
    return str(edges[facing]) if edges is not None and facing is not None else None


def vpinfe_rotation(playfield: Seen | None, turn: str) -> str | None:
    """The VPinFE rotation that goes with VPX's `ViewCabRotation` on this output, or None
    where VPX holds a turn that faces none of the four edges."""
    edges = _edges(playfield)
    if edges is None:
        return None
    held = turn_of(turn)
    facing = next((edge for edge, value in edges.items() if value == held), None)
    return next((str(int(degrees)) for degrees, edge in _FACING.items() if edge == facing),
                None)


def turn_of(value: str | None) -> float | None:
    """A `ViewCabRotation` as a number: blank is VPX's own 0."""
    try:
        return float(str(value or "").strip() or 0)
    except ValueError:
        return None


def rotation_differs(vpinfe: str | None, current: str) -> bool:
    """Whether VPX turns the playfield other than the answer says. An answer that says
    nothing differs from nothing."""
    return vpinfe is not None and turn_of(current) != turn_of(vpinfe)


def rotation_follows(before: str | None, current: str) -> bool:
    """Whether VPX still holds what the answer gave it, so a new answer moves it: the
    value before, or VPX's own 0 where the answer gave none."""
    return turn_of(current) == (0.0 if before is None else turn_of(before))


def two_screens_without_dmd(on: Mapping[str, Placement]) -> bool:
    """Whether exactly two screens are in use and neither is the Score View's."""
    used = {on.get(part) for part in (PLAYFIELD, BACKGLASS, SCOREVIEW)
            if isinstance(on.get(part), int)}
    return len(used) == 2 and not isinstance(on.get(SCOREVIEW), int)


def _installed(program: Mapping[str, Any] | None) -> set[str] | None:
    """The plugin ids of a program, or None where they are not known."""
    found = (program or {}).get("plugins")
    return None if found is None else set(found)


def _draws(plugin: str, fallback: str, present: set[str], program: Mapping[str, Any] | None,
           current: Mapping[str, str]) -> bool:
    """Whether the plugin is installed and its Enable is on, as `backglass_ops.renderer`
    reads it: the ini's own, else the build's default, else `fallback`."""
    key = f"Plugin.{plugin}.Enable"
    said = (str(current.get(key) or "").strip()
            or str(((program or {}).get("defaults") or {}).get(key) or "") or fallback)
    return plugin in present and said.lower() in ("1", "true")


def render_priorities(on: Mapping[str, Placement], program: Mapping[str, Any] | None = None,
                      current: Mapping[str, str] | None = None) -> dict[str, str]:
    """Blank priorities draw two screens with no DMD screen: B2S draws the backglass and the
    Score View plugin an embedded DMD.

    Given the installed `program`, what each plugin installed is told: none for a program
    with no plugins, today's order where B2SLegacy draws, and otherwise PUP over the
    score display's plugin over B2S, which is Nixie from the build that renamed Score View,
    and B2S over Score View before it.
    """
    if two_screens_without_dmd(on):
        return {}
    present = _installed(program)
    if present is None:
        return dict(PRIORITIES)
    if not present:
        return {}
    if _draws(LEGACY, "1", present, program, current or {}):
        return dict(PRIORITIES)
    order = (PUP, NIXIE, B2S_ID) if NIXIE in present else (PUP, B2S_ID, SCOREVIEW_ID)
    return {f"{PRIORITY}{one}": str(len(order) - at)
            for at, one in enumerate(order) if one in present}


def full_dmd(on: Mapping[str, Placement], program: Mapping[str, Any] | None = None,
             current: Mapping[str, str] | None = None) -> dict[str, str]:
    """Full DMD wherever a screen is the DMD. Never turned off: with the Score View gone
    the overlay has no window to draw on.

    Given the installed `program`, the keys of the renderer that draws: B2SLegacy's, B2S's
    where it keeps the DMD's place in the ini, and nothing where the backglass file keeps
    it or no plugin draws.
    """
    if not isinstance(on.get(SCOREVIEW), int):
        return {}
    present = _installed(program)
    if present is None:
        return dict(FULL_DMD)
    if _draws(LEGACY, "1", present, program, current or {}):
        return dict(FULL_DMD)
    if _draws(B2S_ID, "0", present, program, current or {}) and build.declares(
            f"Plugin.{B2S_ID}.ScoreViewDMDOverlay", (program or {}).get("build")):
        return dict(FULL_DMD_B2S)
    return {}


def derived(on: Mapping[str, Placement], current: Mapping[str, str],
            program: Mapping[str, Any] | None = None) -> dict[str, str]:
    """Render priorities and Full DMD for an arrangement, of those VPX holds no value for."""
    wanted = {**render_priorities(on, program, current), **full_dmd(on, program, current)}
    return {key: value for key, value in wanted.items()
            if not str(current.get(key) or "").strip()}


B2S_RANK, NIXIE_RANK = f"{PRIORITY}{B2S_ID}", f"{PRIORITY}{NIXIE}"


def _rank(value: object) -> int | None:
    try:
        return int(str(value or "").strip())
    except ValueError:
        return None


def _b2s_over_nixie(program: Mapping[str, Any] | None,
                    launcher: Mapping[str, str]) -> dict[str, str]:
    """The launcher's ranks for B2S and Nixie with each other's number, or {} where this
    program has no such order to give: B2S must draw and keep the DMD in the file beside
    Nixie, and the launcher must rank Nixie over B2S."""
    present = _installed(program)
    if (present is None or not {B2S_ID, NIXIE} <= present
            or not (program or {}).get("dmd_in_file")
            or _draws(LEGACY, "1", present, program, launcher)
            or not _draws(B2S_ID, "0", present, program, launcher)):
        return {}
    b2s, nixie = _rank(launcher.get(B2S_RANK)), _rank(launcher.get(NIXIE_RANK))
    if b2s is None or nixie is None or nixie <= b2s:
        return {}
    return {B2S_RANK: str(nixie), NIXIE_RANK: str(b2s)}


def table_order(program: Mapping[str, Any] | None, launcher: Mapping[str, str],
                own: Mapping[str, str], in_file: bool,
                written: Mapping[str, str] | None = None) -> TableOrder:
    """What one table's own settings should hold for the order B2S and Nixie draw in, given
    whether its backglass file places a Score View DMD, and what an earlier call recorded as
    `written`.

    The launcher ranks Nixie over B2S, so every table has a live DMD; a table whose file
    places a Score View DMD ranks B2S over Nixie, to show that art with the live DMD on it.
    A pair is the table's to change only while it is exactly the one recorded: any other rank
    held for either key is the table's own and is left alone, and so is a pair somebody set
    that happens to equal ours before one was recorded. A record that no longer matches what
    is held is answered as none to keep.
    """
    held = {key: str(own.get(key) or "").strip() for key in (B2S_RANK, NIXIE_RANK)}
    wanted = _b2s_over_nixie(program, launcher) if in_file else {}
    kept = {key: value for key, value in (written or {}).items() if value}
    if kept and held == {key: kept.get(key, "") for key in held}:
        if wanted == held:
            return TableOrder(ours=held)
        return TableOrder(write=wanted or dict.fromkeys(held, ""), ours=wanted)
    if wanted and not any(held.values()):
        return TableOrder(write=wanted, ours=wanted)
    return TableOrder(ours={}) if kept else TableOrder()


def answer(moved: Mapping[str, Placement], now: Mapping[str, Placement],
           screens: Sequence[Seen], logged: Sequence[str], current: Mapping[str, str],
           turn: tuple[str, str] | None = None,
           program: Mapping[str, Any] | None = None) -> Written:
    """Every key the answer sets for the parts that `moved`, `now` being the whole
    arrangement after. `turn` is the playfield's (orientation, rotation) where the answer
    gives VPX one.

    A part left inside a playfield that moved is written again for the new window, and a
    part that leaves the inside has its region blanked.
    """
    playfield = next((one for one in screens if one.number == now.get(PLAYFIELD)), None)
    inside = ({part: INSIDE for part, where in now.items()
               if part in OUTPUT and where == INSIDE}
              if PLAYFIELD in moved and playfield is not None else {})
    written = windows({**inside, **moved}, screens, logged, playfield)
    keys = dict(written.keys)
    for part, where in moved.items():
        if (part in OUTPUT and where != INSIDE and part not in written.unnamed
                and str(current.get(OUTPUT[part]) or "").strip() == EMBEDDED):
            keys.update(dict.fromkeys(REGION[part], ""))
    if turn is not None and (value := view_cab_rotation(playfield, turn[1])) is not None:
        keys[VIEW_CAB_ROTATION] = value
    keys.update(derived(now, current, program))
    return replace(written, keys=keys)


def dmd_util(kind: str | None, *, platform: str | None = None) -> dict[str, str]:
    """`[Plugin.DMDUtil]` for a panel of this kind: the plugin on, and that kind on. No
    kind writes nothing."""
    where = sys.platform if platform is None else platform
    if kind not in DMD_KINDS or not where.startswith(DMD_UTIL_PLATFORMS):
        return {}
    return {f"{DMD_UTIL}Enable": "1", f"{DMD_UTIL}{kind}": "1"}


def dmd_kind(current: Mapping[str, str]) -> str:
    """The panel kind VPX's ini has on, or "" for none."""
    return next((kind for kind in DMD_KINDS
                 if str(current.get(f"{DMD_UTIL}{kind}") or "").strip() == "1"), "")
