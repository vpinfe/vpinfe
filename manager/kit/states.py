"""A state column: one table of states builds everything a column says about them.

The cell, the formatter, the header tooltip, the filter, the sort and the legend all read
the same `Table`, so a word, a color or an order is changed in one place. `docs/conventions.md`
has the rule, under Manager: a state column draws every row.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from common.i18n import t
from manager.kit import grid, renderers

# What the state costs, which is its color: green is in place, amber is something VPinFE needs
# you to fix, red is something that stops play, quiet is a choice or a coverage gap, accent is
# the one that is active, and plain is a neutral fact.
ON, WARN, BAD, QUIET, ACCENT, PLAIN = "on", "warn", "bad", "quiet", "accent", ""
TONES = (ON, WARN, BAD, QUIET, ACCENT)

# A state drawn as a character, never a word: in place is a tick, not known yet is a question
# mark. The word is the tooltip.
TICK, UNKNOWN = "tick", "unknown"
_CHARACTER = {TICK: "✓", UNKNOWN: "?"}
_CHARACTER_CLASS = {TICK: "manager-tick", UNKNOWN: "manager-unknown"}

# The token of a state with nothing in the cell: the fact does not apply to the row.
BLANK = ""


@dataclass(frozen=True)
class State:
    """One answer a column can hold.

    `token` is what a row carries, `word` is title case and what the filter and the sort
    list, and `meaning` is the tooltip's half of *Word - meaning*. A TICK is green and an
    UNKNOWN is quiet whatever `tone` says."""

    token: str
    word: str
    meaning: str
    tone: str = PLAIN
    glyph: str = ""
    # A material icon drawn in place of the word, for a state a page already marks with one.
    icon: str = ""

    @property
    def shown_tone(self) -> str:
        return {TICK: ON, UNKNOWN: QUIET}.get(self.glyph, self.tone)

    def line(self) -> str:
        """*Word - meaning*, the header's line for this state and a filter row's tooltip."""
        return line(self.word, self.meaning)


def line(word: str, meaning: str) -> str:
    """*Word - meaning*: a header line, a filter row's tooltip and a cell's tooltip."""
    return t("manager.state.line", word=word, meaning=meaning) if meaning else word


def _order_js(rank: dict[str, int]) -> str:
    return ("(a, b) => { const rank = " + json.dumps(rank) + ";"
            " const at = v => v == null || !(String(v) in rank) ? 1e9 : rank[String(v)];"
            " return at(a) - at(b); }")


@dataclass(frozen=True)
class Table:
    """The states of one fact, most in place first, then Unknown.

    `blank` is the cell with nothing in it, named for what nothing means; it is last in
    the filter and the header and sorts after every state. `covers` is the header
    tooltip's first line, what the column is about."""

    covers: str
    states: tuple[State, ...]
    blank: State | None = None

    def __post_init__(self) -> None:
        tokens = [one.token for one in self.states]
        if len(set(tokens)) != len(tokens) or BLANK in tokens:
            raise ValueError("a state table's tokens are distinct and none is blank")
        if self.blank is not None and self.blank.token != BLANK:
            raise ValueError("the blank state's token is the empty string")

    @property
    def everything(self) -> tuple[State, ...]:
        return (*self.states, *([self.blank] if self.blank else []))

    def state(self, token: str) -> State | None:
        return next((one for one in self.everything if one.token == token), None)

    def word(self, token: str) -> str:
        found = self.state(token)
        return found.word if found else ""

    def header_tip(self) -> str:
        """What the column covers, then one line per state in order, blank last."""
        return "\n".join([*([self.covers] if self.covers else []),
                          *(one.line() for one in self.everything)])

    def choices(self) -> list[dict[str, Any]]:
        """The filter's rows, one per state in order, the cell's own character and color
        on each, the state's line as its tooltip. The count beside each is the filter's."""
        out = []
        for one in self.everything:
            row: dict[str, Any] = {"value": one.token, "label": one.word, "tip": one.line()}
            if one.glyph:
                row["glyph"] = _CHARACTER[one.glyph]
                row["glyphClass"] = _CHARACTER_CLASS[one.glyph]
            elif one.icon:
                row["glyph"] = one.icon
                row["glyphClass"] = renderers.MARK_CLASS
            elif one.shown_tone:
                row["tone"] = one.shown_tone
            out.append(row)
        return out

    def looks(self) -> dict[str, dict[str, str]]:
        """What the cell renderer draws for each token a cell can hold. A blank token has
        none: the cell is empty."""
        return {one.token: {"label": one.word, "tone": one.shown_tone,
                            "glyph": _CHARACTER.get(one.glyph, ""), "icon": one.icon,
                            "why": one.line()}
                for one in self.states}

    def rank(self) -> dict[str, int]:
        return {one.token: place for place, one in enumerate(self.everything)}

    def comparator(self) -> str:
        """Sorts by the table's order, with an unknown token after the last."""
        return _order_js(self.rank())

    def present(self, tokens: Iterable[str]) -> list[State]:
        """The states a legend names: those a row holds, in the table's order, that draw
        something. A blank or an absent state is not a line."""
        held = set(tokens)
        return [one for one in self.states if one.token in held]

    def column(self, field: str, header: str, width: int = 0, help: str = "",
               **extra: Any) -> dict[str, Any]:
        """The column: a cell that draws the state, a filter of its rows, a sort in its
        order and a header that says what each state means. `help` replaces the line the
        header opens with, `covers`, when the page has more to say than the table does."""
        said = {one.token: one.word for one in self.states}
        shown = (f"params => ({json.dumps(said)})[params.value] ?? ''")
        options: dict[str, Any] = {
            ":valueFormatter": shown,
            ":comparator": _order_js(self.rank()),
            **grid.choice_filter(self.choices(), formatted=True),
            **renderers.drawable("state", states=self.looks()),
        }
        tip = self.header_tip() if not help else "\n".join(
            [help, *(one.line() for one in self.everything)])
        return grid.column(field, header, width, help=tip, **options | extra)
