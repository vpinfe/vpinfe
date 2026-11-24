"""What an upgrade of the program leaves in its settings file.

A plugin that was renamed or dropped takes its keys with it, and nothing upstream carries
the values across. A key counts as left behind only while the program has the plugin that
replaced it and lacks the one that wrote it, so a program whose plugins cannot be read
leaves nothing behind. A key whose replacement already holds a value is not offered: the
value somebody set there is the one in force.
"""

from __future__ import annotations

from common.apps.contract import MOVE, NAME, PLACE, TURN_ON, Leftover

from . import build
from . import ini as vini

SCORE_VIEW, NIXIE = "Plugin.ScoreView.", "Plugin.Nixie."
LEGACY, B2S = "Plugin.B2SLegacy.", "Plugin.B2S."

# The size B2SLegacy counts its DMD in, whatever the picture it sits on.
LEGACY_FRAME = (1024, 768)
# Each window with the words for its box: carried, placed automatically, replaced by B2S's.
_WINDOWS = (("Backglass", "dmd_backglass", "dmd_backglass_auto", "dmd_backglass_replaced"),
            ("ScoreView", "dmd_scoreview", "dmd_scoreview_auto", "dmd_scoreview_replaced"))
_ON = ("1", "true")
_OFF = ("0", "false")


def _on(said: str | None) -> bool:
    return (said or "").lower() in _ON


def _off(said: str | None) -> bool:
    return (said or "").lower() in _OFF


def _number(said: str | None) -> int:
    try:
        return int(float(said or 0))
    except (ValueError, OverflowError):
        return 0


def _moves(app: vini.Ini, present: frozenset[str], rev: int | None) -> list[Leftover]:
    """The settings that have a new name."""
    found: list[Leftover] = []

    def held(key: str) -> str:
        return app.value(key) or ""

    def free(key: str) -> bool:
        return not held(key)

    if "Nixie" in present and "ScoreView" not in present:
        if _off(held(SCORE_VIEW + "Enable")) and free(NIXIE + "Enable"):
            found.append(Leftover(SCORE_VIEW + "Enable", MOVE, "scoreview_enable",
                                  held(SCORE_VIEW + "Enable"), NIXIE + "Enable", "0",
                                  ((SCORE_VIEW + "Enable", held(SCORE_VIEW + "Enable")),)))
        if held(SCORE_VIEW + "LayoutFolder") and free(NIXIE + "LayoutFolder"):
            folder = held(SCORE_VIEW + "LayoutFolder")
            found.append(Leftover(SCORE_VIEW + "LayoutFolder", MOVE, "scoreview_folder",
                                  folder, NIXIE + "LayoutFolder", folder,
                                  ((SCORE_VIEW + "LayoutFolder", folder),)))
    if "B2S" in present and "B2SLegacy" not in present:
        if (held(LEGACY + "B2SDualMode") == "2" and free(B2S + "DualMode")
                and build.declares(B2S + "DualMode", rev, True)):
            found.append(Leftover(LEGACY + "B2SDualMode", MOVE, "dual_mode", "2",
                                  B2S + "DualMode", "2", ((LEGACY + "B2SDualMode", "2"),)))
        if held(LEGACY + "B2SHideGrill") and free(B2S + "ShowGrill"):
            hidden = _on(held(LEGACY + "B2SHideGrill"))
            found.append(Leftover(LEGACY + "B2SHideGrill", MOVE, "hide_grill",
                                  held(LEGACY + "B2SHideGrill"), B2S + "ShowGrill",
                                  "0" if hidden else "1",
                                  ((LEGACY + "B2SHideGrill", held(LEGACY + "B2SHideGrill")),)))
    return found


def _named(app: vini.Ini, present: frozenset[str]) -> list[Leftover]:
    """The settings nothing reads any more and nothing replaces, and the ones that turn a
    renderer off."""
    found: list[Leftover] = []
    if "B2S" not in present or "B2SLegacy" in present:
        return found
    for key, word in ((LEGACY + "B2SHideB2SDMD", "b2s_dmd"),
                      (LEGACY + "B2SHideDMD", "hide_dmd")):
        if _on(app.value(key)):
            found.append(Leftover(key, NAME, word, app.value(key) or ""))
    if _off(app.value(B2S + "Enable")):
        found.append(Leftover(B2S + "Enable", TURN_ON, "b2s_off", app.value(B2S + "Enable") or "",
                              B2S + "Enable", "1"))
    return found


_PARTS = ("Overlay", "AutoPos", "X", "Y", "W", "H")


def _live(app: vini.Ini, plugin: str, window: str) -> bool:
    """Whether this plugin holds a box for the window that is switched on, sized and placed by
    hand. An automatic box has no place of its own, so it replaces nothing."""
    prefix = f"{plugin}{window}DMD"
    return (_on(app.value(prefix + "Overlay")) and not _on(app.value(prefix + "AutoPos"))
            and _number(app.value(prefix + "W")) > 0 and _number(app.value(prefix + "H")) > 0)


def _placed(app: vini.Ini, plugin: str, frame: tuple[int, int] | None,
            over: str = "") -> list[Leftover]:
    """The DMD box this plugin's settings hold for each window, where one is switched on and
    sized. A box that was placed automatically has no place to carry, so it is only named.
    Where `over` is the plugin that replaced this one and holds a box for the same window that
    is switched on, sized and placed by hand, this plugin's box is only named too: what the
    file would take from it must not replace the live one."""
    found: list[Leftover] = []
    for window, word, automatic, replaced in _WINDOWS:
        prefix = f"{plugin}{window}DMD"
        keys = tuple(prefix + part for part in _PARTS)
        if not _on(app.value(prefix + "Overlay")):
            continue
        auto = _on(app.value(prefix + "AutoPos"))
        x, y, wide, high = (_number(app.value(prefix + part)) for part in "XYWH")
        if not auto and (wide <= 0 or high <= 0):
            continue
        if over and _live(app, over, window):
            found.append(Leftover(prefix, NAME, replaced))
        elif auto:
            found.append(Leftover(prefix, NAME, automatic))
        else:
            found.append(Leftover(prefix, PLACE, word, target=window,
                                  box=(x, y, wide, high), frame=frame,
                                  cut_if=LEGACY + "B2SHideGrill" if frame else "",
                                  drops=tuple((key, app.value(key) or "") for key in keys
                                              if app.value(key) is not None)))
    return found


def find(app: vini.Ini, present: frozenset[str] | None, rev: int | None,
         dmd_in_file: bool) -> tuple[Leftover, ...]:
    """Everything the settings file holds that the program, as installed, no longer reads.

    B2S's DMD box is left behind on a build that keeps the DMD in each backglass file, and
    B2SLegacy's once that plugin is gone as well.
    """
    if present is None:
        return ()
    found = _moves(app, present, rev)
    if "B2S" in present and dmd_in_file:
        found += _placed(app, B2S, None)
        if "B2SLegacy" not in present:
            found += _placed(app, LEGACY, LEGACY_FRAME, B2S)
    return (*found, *_named(app, present))

