"""The In Use column of the Media and Assets grids: whether a file is the one being used."""

from __future__ import annotations

from common.i18n import t
from manager.kit import states

IN_USE = "in_use"
UNUSED = "unused"
ORPHAN = "orphan"
MISSING = "missing"


def table(unused_meaning: str) -> states.Table:
    """Media's Unused is covered by a more specific file and Assets' by nothing that
    resolves to it, so the page says which."""
    return states.Table(
        covers="",
        states=(
            states.State(IN_USE, t("word.in_use"), t("manager.file_state.in_use"),
                         glyph=states.TICK),
            states.State(UNUSED, t("manager.media_ownership.unused"), t(unused_meaning),
                         tone=states.QUIET),
            states.State(ORPHAN, t("manager.media_ownership.orphan"),
                         t("manager.file_state.orphan"), tone=states.WARN),
            states.State(MISSING, t("manager.media_ownership.missing"),
                         t("manager.file_state.missing"), tone=states.QUIET)))
