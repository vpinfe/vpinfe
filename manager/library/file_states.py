"""The In Use column of the Media and Assets grids: whether a file is the one being used."""

from __future__ import annotations

from common.i18n import t
from manager.kit import states

IN_USE = "in_use"
UNUSED = "unused"
ORPHAN = "orphan"
MISSING = "missing"
UNSUPPORTED = "unsupported"
WONT_PLAY = "wont_play"


def table(unused_meaning: str, *, converts: bool = False) -> states.Table:
    """Media's Unused is covered by a more specific file and Assets' by nothing that
    resolves to it, so the page says which. Only media converts, so only its table has
    the two states conversion answers."""
    convertible = (
        states.State(WONT_PLAY, t("manager.media_ownership.wont_play"),
                     t("manager.file_state.wont_play"), tone=states.WARN),
        states.State(UNSUPPORTED, t("manager.media_ownership.unsupported"),
                     t("manager.file_state.unsupported"), tone=states.WARN)) if converts else ()
    return states.Table(
        covers="",
        states=(
            states.State(IN_USE, t("word.in_use"), t("manager.file_state.in_use"),
                         glyph=states.TICK),
            states.State(UNUSED, t("manager.media_ownership.unused"), t(unused_meaning),
                         tone=states.QUIET),
            states.State(ORPHAN, t("manager.media_ownership.orphan"),
                         t("manager.file_state.orphan"), tone=states.WARN),
            *convertible,
            states.State(MISSING, t("manager.media_ownership.missing"),
                         t("manager.file_state.missing"), tone=states.QUIET)))
