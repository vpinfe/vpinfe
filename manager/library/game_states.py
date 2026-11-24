"""The states the Games and Tables grids hold, one `states.Table` per fact.

A row carries the token and the grid draws the word, so a word or a color is changed here and
the cell, the filter, the sort and the header tooltip follow.
"""

from __future__ import annotations

from common.games import identity_claims, ties
from common.i18n import t
from manager.kit import game_tables, states, verbs

INSTALLED, NOT_INSTALLED, UNKNOWN = "installed", "missing", "unknown"
HIDDEN, PRIVATE, MISSING = game_tables.HIDDEN_CELL, game_tables.PRIVATE_CELL, "missing"
ONLY_TABLE = "only"


def _blank(word: str, meaning: str) -> states.State:
    return states.State("", t(word), t(meaning))


def rom() -> states.Table:
    """Whether the ROM a table names is installed; blank where it names none."""
    return states.Table(
        covers=t("manager.table_state.rom.covers"),
        states=(
            states.State(INSTALLED, t("word.installed"),
                         t("manager.table_state.rom.installed"), glyph=states.TICK),
            states.State(NOT_INSTALLED, t("manager.workbench.not_installed"),
                         t("manager.table_state.rom.not_installed"), tone=states.WARN),
            states.State(UNKNOWN, t("word.unknown"), t("manager.table_state.rom.unknown"),
                         glyph=states.UNKNOWN)),
        blank=_blank("manager.table_state.rom.none", "manager.table_state.rom.none.help"))


def release() -> states.Table:
    """How a table's VPS release was picked, most deliberate first; blank where VPS has none
    to pick for it."""
    return states.Table(
        covers=t("manager.games.release.help"),
        states=(
            states.State(identity_claims.USER, t("manager.games.release_user"),
                         t("manager.table_state.release.user")),
            states.State(identity_claims.EXACT, t("manager.games.release_exact"),
                         t("manager.table_state.release.exact")),
            states.State(identity_claims.DECLARED, t("manager.games.release_declared"),
                         t("manager.table_state.release.declared")),
            states.State(identity_claims.CONSTRUCTION, t("manager.games.release_construction"),
                         t("manager.table_state.release.construction")),
            states.State(ties.TIED, t("manager.games.release_tied"),
                         t("manager.table_state.release.tied")),
            states.State(ties.UNTIED, t("manager.games.release_untied"),
                         t("manager.table_state.release.untied"), tone=states.WARN)),
        blank=_blank("manager.games.release_none", "manager.table_state.release.none"))


def default() -> states.Table:
    """Whether a table is its game's default, and how that was decided. A game with one table
    holds `ONLY_TABLE`, which is in no choice and draws nothing."""
    locked, automatic = game_tables.DEFAULT_WORDS[game_tables.CHOSEN], \
        game_tables.DEFAULT_WORDS[game_tables.DERIVED]
    return states.Table(
        covers=t("manager.games.default.help"),
        states=(
            states.State(game_tables.CHOSEN, locked[0], locked[1], icon=verbs.LOCKED),
            states.State(game_tables.DERIVED, automatic[0], automatic[1])),
        blank=_blank("manager.games.not_default", "manager.table_state.default.none"))


def hidden(*, game: bool) -> states.Table:
    """Whether the frontend leaves a table out. On a game it is amber, because a game with
    nothing to offer is something to fix; on a table it is a choice."""
    if game:
        covers, meaning, none = (t("manager.table_state.hidden_game.cover"),
                                 t("manager.table_state.hidden_game.help"),
                                 t("manager.table_state.hidden_game.none"))
    else:
        covers, meaning, none = (t("manager.table_state.hidden.cover"),
                                 t("manager.table_state.hidden.help"),
                                 t("manager.table_state.hidden.none"))
    return states.Table(
        covers=covers,
        states=(states.State(HIDDEN, game_tables.HIDDEN_WORDS[0], meaning,
                             tone=states.WARN if game else states.QUIET),),
        blank=states.State("", game_tables.HIDDEN_WORDS[1], none))


def private() -> states.Table:
    return states.Table(
        covers=t("manager.games.private_game.help"),
        states=(states.State(PRIVATE, game_tables.PRIVATE_WORDS[0], game_tables.PRIVATE_HELP,
                             tone=states.QUIET),),
        blank=states.State("", game_tables.PRIVATE_WORDS[1],
                           t("manager.table_state.private.none")))


def missing() -> states.Table:
    """Whether a table's file is on disk. Missing is red: the table cannot be played."""
    return states.Table(
        covers=t("manager.games.missing.help"),
        states=(states.State(MISSING, game_tables.FILE_WORDS[0],
                             t("manager.table_state.missing.help"), tone=states.BAD),),
        blank=states.State("", game_tables.FILE_WORDS[1],
                           t("manager.table_state.missing.none")))


def vps_match() -> states.Table:
    """Whether a game is matched to a VPS entry. Unmatched is amber: VPinFE can look
    nothing up for it. Cleared is the user's own answer, so it is quiet."""
    return states.Table(
        covers="",
        states=(
            states.State(game_tables.VPS_MATCHED, t("manager.game_tables.matched"),
                         t("manager.table_state.vps.matched"), glyph=states.TICK),
            states.State(game_tables.VPS_CLEARED, t("manager.workbench.no_match"),
                         t("manager.table_state.vps.cleared"), tone=states.QUIET),
            states.State(game_tables.VPS_UNMATCHED, t("manager.game_tables.unmatched"),
                         t("manager.table_state.vps.unmatched"), tone=states.WARN)))


def how_matched() -> states.Table:
    """How the match in force was made; blank where there is none."""
    said = game_tables.HOW_MATCHED
    return states.Table(
        covers="",
        states=(
            states.State("auto", said["auto"], t("manager.table_state.how.auto")),
            states.State("import", said["import"], t("manager.table_state.how.import")),
            states.State("user", said["user"], t("manager.table_state.how.user"))),
        blank=_blank("manager.game_tables.unmatched", "manager.table_state.how.none"))


def vps_entry() -> states.Table:
    """Whether another game is matched to the same VPS entry, which is how a duplicate
    shows."""
    return states.Table(
        covers=t("manager.games.vps_entry.help"),
        states=(states.State(game_tables.VPS_SHARED, t("manager.games.vps_shared"),
                             t("manager.table_state.entry.shared"), tone=states.WARN),),
        blank=_blank("manager.games.vps_alone", "manager.table_state.entry.none"))


def asset() -> states.Table:
    """Whether a game has the file of one asset kind. One table for every asset column."""
    return states.Table(
        covers="",
        states=(
            states.State(game_tables.ASSET_PRESENT, t("word.present"),
                         t("manager.table_state.asset.present"), glyph=states.TICK),
            states.State(game_tables.ASSET_MISSING, t("manager.games.missing"),
                         t("manager.table_state.asset.missing"), tone=states.QUIET)))
