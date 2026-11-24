"""What a few typed letters could mean in Discovery: a person, a game, a maker or a theme.

A person is found by any name they are credited under, so `vpinworkshop` finds VPW.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from common.online.vps_releases import person_key

AUTHOR = "author"
GAME = "game"
MAKER = "maker"
THEME = "theme"
GROUPS = (AUTHOR, GAME, MAKER, THEME)
SHOWN = {AUTHOR: 5, GAME: 5, MAKER: 3, THEME: 3}


def _rank(text: str, name: str) -> int | None:
    """0 for a name that starts with `text`, 1 for a word that does, 2 for any other
    place it occurs, None for no match."""
    key = person_key(name)
    if key.startswith(text):
        return 0
    if any(word.startswith(text) for word in key.split()):
        return 1
    return 2 if text in key else None


def _best(text: str, names: Sequence[str]) -> int | None:
    ranks = [one for one in (_rank(text, name) for name in names) if one is not None]
    return min(ranks) if ranks else None


def find(text: str, people: Sequence[Mapping[str, Any]],
         rows: Sequence[Mapping[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Up to a few matches per group, best first.

    Each is `{"name", "count"}`: an author's releases, a maker's or theme's games. A game
    carries its `id` and `said` (maker and year, as the games grid shows them), and an
    author the alias that matched, where it was not their name."""
    wanted = person_key(text)
    out: dict[str, list[dict[str, Any]]] = {group: [] for group in GROUPS}
    if not wanted:
        return out

    ranked = []
    for one in people:
        rank = _best(wanted, [one["name"], *one["aliases"]])
        if rank is None:
            continue
        via = "" if _rank(wanted, one["name"]) is not None else next(
            name for name in one["aliases"] if _rank(wanted, name) is not None)
        ranked.append((rank, -one["releases"], one["name"].casefold(),
                       {"name": one["name"], "count": one["releases"], "via": via}))
    out[AUTHOR] = [item for *_key, item in sorted(ranked, key=lambda r: r[:3])][:SHOWN[AUTHOR]]

    games = sorted(((rank, str(row["name"]).casefold(), row)
                    for row in rows
                    if (rank := _rank(wanted, str(row.get("name") or ""))) is not None),
                   key=lambda r: r[:2])
    out[GAME] = [{"name": row["name"], "count": 1, "id": row["id"],
                  "said": str(row.get("said") or "")}
                 for _rank_, _name, row in games[:SHOWN[GAME]]]

    makers = Counter(str(row["manufacturer"]) for row in rows if row.get("manufacturer"))
    themes: Counter[str] = Counter(word for row in rows for word in row.get("themes") or ())
    for group, counted in ((MAKER, makers), (THEME, themes)):
        found = sorted(((_rank(wanted, name), -count, name.casefold(), name, count)
                        for name, count in counted.items()
                        if _rank(wanted, name) is not None),
                       key=lambda r: r[:3])
        out[group] = [{"name": name, "count": count}
                      for *_key, name, count in found[:SHOWN[group]]]
    return out
