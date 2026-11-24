"""A plain list of games as entries, in the order given - for the capture harnesses only.

The app has no such thing: a view is a collection, so `resolve` answers everything it
asks. The harnesses hand over a fixed list and need it back in that order, and
`parity_capture` runs against a master checkout where none of this exists.

`visible_entries` is called rather than copied, so the harness cannot disagree with the
app about which table a game shows.
"""

from __future__ import annotations

from common.games.collection_resolver import Entry, visible_entries


def entries_for(games) -> list[Entry]:
    out: list[Entry] = []
    for game in games:
        offered = visible_entries(game)
        if offered:
            out.append(Entry(game=game, table=offered[0], siblings=len(offered)))
    return out
