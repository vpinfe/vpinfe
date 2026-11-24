"""The states Discovery's columns hold, one `states.Table` per fact.

The grid, the Releases tab, the pane's rail and the wall read these, so a word or a color
is changed here. Before the library has been compared with VPS the row carries
`NOT_COMPARED`, which every counted column draws as a question mark.
"""

from __future__ import annotations

from common.i18n import t
from common.online import vps_marks
from manager.kit import states, verbs
from manager.library.discovery_ask import MATCHED, NOT_COMPARED, UNMATCHED

MISSING = "missing"
YES, NO = "yes", "no"
HAVE = "have"
MOD = "mod"


def _not_compared() -> states.State:
    return states.State(NOT_COMPARED, t("manager.discovery.state.not_compared"),
                        t("manager.discovery.state.not_compared.help"), glyph=states.UNKNOWN)


def kind() -> states.Table:
    """A file kind's cell: whether the game's file is matched to a release VPS lists."""
    return states.Table(
        covers="",
        states=(
            states.State(MATCHED, t("manager.discovery.matched"),
                         t("manager.discovery.kind.matched"), glyph=states.TICK),
            states.State(UNMATCHED, t("manager.view.unmatched"),
                         t("manager.discovery.kind.unmatched"), tone=states.WARN),
            states.State(MISSING, t("manager.view.missing"),
                         t("manager.discovery.kind.missing"), tone=states.QUIET),
            _not_compared()),
        blank=states.State("", t("manager.discovery.state.not_listed"),
                           t("manager.discovery.kind.not_listed")))


def yes_no(yes: str, no: str, *, compared: bool = True,
           blank: states.State | None = None) -> states.Table:
    """A Yes and No column: a tick for Yes, the word for No. `compared` is False for a column
    that reads the same before the library is counted."""
    return states.Table(
        covers="",
        states=(states.State(YES, t("word.yes"), t(yes), glyph=states.TICK),
                states.State(NO, t("word.no"), t(no)),
                *([_not_compared()] if compared else [])),
        blank=blank)


def in_library() -> states.Table:
    return yes_no("manager.discovery.in_library.yes", "manager.discovery.in_library.no")


def playable() -> states.Table:
    return yes_no("manager.discovery.playable.yes", "manager.discovery.playable.no",
                  compared=False,
                  blank=states.State("", t("manager.discovery.playable.blank"),
                                     t("manager.discovery.playable.blank.help")))


def matched() -> states.Table:
    return yes_no("manager.discovery.matched.yes", "manager.discovery.matched.no")


def other_version() -> states.Table:
    return yes_no("manager.discovery.other_version.yes", "manager.discovery.other_version.no")


def gap() -> states.Table:
    """Anything Missing: Missing where a kind VPS lists is not held, else Nothing Missing."""
    return states.Table(
        covers="",
        states=(states.State(MISSING, t("manager.view.missing"),
                             t("manager.discovery.gaps.missing"), tone=states.QUIET),
                _not_compared()),
        blank=states.State("", t("manager.discovery.gaps.none"),
                           t("manager.discovery.gaps.none.help")))


def held() -> states.Table:
    """A release on the Releases tab: whether the library holds it."""
    return states.Table(
        covers="",
        states=(states.State(HAVE, t("manager.view.have"),
                             t("manager.discovery.releases.held.have"), glyph=states.TICK),
                states.State(MISSING, t("manager.view.missing"),
                             t("manager.discovery.releases.held.missing"), tone=states.QUIET),
                _not_compared()))


def mod() -> states.Table:
    return states.Table(
        covers="",
        states=(states.State(MOD, t("manager.games.mod"),
                             t("manager.discovery.releases.mod.is")),),
        blank=states.State("", t("manager.discovery.ask.original"),
                           t("manager.discovery.releases.mod.original")))


def marks(neither: str = "") -> states.Table:
    """Wanted keeps its bookmark and Ignored is the quiet word. `neither` names the rows that
    are marked neither, for a grid whose view keeps them; empty offers no such row."""
    return states.Table(
        covers="",
        states=(states.State(vps_marks.WANTED, t("manager.discovery.mark.wanted"),
                             t("manager.discovery.mark.wanted.help"), icon=verbs.WANTED),
                states.State(vps_marks.IGNORED, t("manager.discovery.mark.ignored"),
                             t("manager.discovery.mark.ignored.help"), tone=states.QUIET)),
        blank=states.State("", neither, t("manager.discovery.mark.neither.help"))
        if neither else None)
