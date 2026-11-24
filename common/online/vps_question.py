"""A question asked of Discovery's games: which of them have a release that fits.

A question is a plain dict so a saved view can hold it. Every key is optional and an
absent or empty one asks nothing:

- `kind`: one VPS listing, such as `b2sFiles`.
- `by`: person names, as `people` shows them; a release fits when any of them made it.
- `mod`: `originals` (not a mod) or `mods`.
- `feature`, `format`: a release fits when it has any of the words.
- `since`: a window in days, such as `7`; a release fits when VPS added it within that many
  days of today.
- `held`: `have` or `missing`, of the release itself.
- `maker`, `theme`: about the game, not a release.
- `owned`: `yes` or `no`, whether the library has the game.

Game-level keys and `kind` narrow the games; the release-level keys must all be met by one
release, so a game that has a Wildman backglass and someone else's mod does not fit
"Wildman's mods".
"""

from __future__ import annotations

import datetime
from collections.abc import Mapping, Sequence
from typing import Any

from common.online import vps_formats
from common.online.vps_releases import person_key

RELEASE_KEYS = ("by", "mod", "feature", "format", "since", "held")
GAME_KEYS = ("maker", "theme", "owned")
KEYS = ("kind", *RELEASE_KEYS, *GAME_KEYS)
LIST_KEYS = ("by", "feature", "format")


def _days(window: Any) -> int:
    """A window as a count of days, 0 for anything that is not one a date can be reckoned from."""
    text = str(window or "").strip()
    if not (text.isascii() and text.isdigit() and len(text) <= 5):
        return 0
    return int(text)


def clean(question: Mapping[str, Any] | None) -> dict[str, Any]:
    """The question with every empty key dropped and the lists as lists of strings."""
    out: dict[str, Any] = {}
    for key in KEYS:
        value = (question or {}).get(key)
        if key in LIST_KEYS:
            value = [str(one) for one in (value or []) if str(one).strip()]
        else:
            value = str(value or "").strip()
            if key == "since":
                value = str(_days(value) or "")
        if value:
            out[key] = value
    return out


def narrows_releases(question: Mapping[str, Any]) -> bool:
    return any(_days(question[key]) if key == "since" else question.get(key)
               for key in RELEASE_KEYS if key in question)


def _cutoff(question: Mapping[str, Any]) -> str:
    """The earliest date a release can have been added and still be inside the question's
    window, as today's reading of it; empty when the question has no window."""
    days = _days(question.get("since"))
    if not days:
        return ""
    return (datetime.date.today() - datetime.timedelta(days=days)).isoformat()


def _fits_release(question: Mapping[str, Any], release: Mapping[str, Any],
                  wanted: set[int], cutoff: str) -> bool:
    if wanted and not wanted.intersection(release.get("by") or ()):
        return False
    mod = question.get("mod")
    if mod and bool(release.get("mod")) != (mod == "mods"):
        return False
    features = question.get("feature")
    if features and not set(features).intersection(release.get("features") or ()):
        return False
    formats = question.get("format")
    if formats and not vps_formats.fits(release.get("format"), formats):
        return False
    if cutoff and not (release.get("added") or "") >= cutoff:
        return False
    held = question.get("held")
    return not held or bool(release.get("held")) == (held == "have")


def _fits_game(question: Mapping[str, Any], row: Mapping[str, Any]) -> bool:
    if question.get("maker") and row.get("manufacturer") != question["maker"]:
        return False
    if question.get("theme") and question["theme"] not in (row.get("themes") or ()):
        return False
    owned = question.get("owned")
    if owned and bool(row.get("is_in_library")) != (owned == "yes"):
        return False
    kind = question.get("kind")
    return not kind or kind in (row.get("listed") or ())


Releases = Mapping[str, Sequence[Mapping[str, Any]]]


def person_named(name: str, people: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    """The person a credited name belongs to, under whichever spelling it is written."""
    key = person_key(name)
    return next((one for one in people
                 if key in {person_key(one["name"]), *(person_key(a) for a in one["aliases"])}),
                None)


def _wanted(question: Mapping[str, Any], people: Sequence[Mapping[str, Any]]) -> set[int]:
    named = {id(one) for name in question.get("by") or ()
             if (one := person_named(name, people)) is not None}
    return {at for at, one in enumerate(people) if id(one) in named}


def fitting(question: Mapping[str, Any], people: Sequence[Mapping[str, Any]],
            row: Mapping[str, Any], releases: Releases,
            wanted: set[int] | None = None) -> list[tuple[str, Mapping[str, Any]]]:
    """The releases of a game that fit, as (listing, release); empty where the game does not.

    `releases` is the game's, by listing. A question that asks nothing of releases fits a
    game by its game-level keys alone, and then every release of the kind asked for, or of
    any kind, is listed as fitting. `wanted` is `_wanted` for the question, which a caller
    going over many games works out once."""
    if not _fits_game(question, row):
        return []
    if wanted is None:
        wanted = _wanted(question, people)
    if question.get("by") and not wanted:
        return []
    kind = question.get("kind")
    ask = narrows_releases(question)
    cutoff = _cutoff(question)
    return [(listing, one) for listing, found in releases.items()
            if not kind or listing == kind
            for one in found if not ask or _fits_release(question, one, wanted, cutoff)]


def games_found(question: Mapping[str, Any], people: Sequence[Mapping[str, Any]],
                rows: Sequence[Mapping[str, Any]], releases: Mapping[str, Releases]
                ) -> dict[str, list[tuple[str, Mapping[str, Any]]]]:
    """Each fitting game's id and the releases of it that fit; `releases` is by game id.

    A game with no release fits a question that asks nothing of releases, with none listed."""
    ask = narrows_releases(question)
    wanted = _wanted(question, people)
    out: dict[str, list[tuple[str, Mapping[str, Any]]]] = {}
    for row in rows:
        mine = releases.get(str(row["id"])) or {}
        found = fitting(question, people, row, mine, wanted)
        if found:
            out[str(row["id"])] = found
        elif not ask and not mine and _fits_game(question, row):
            out[str(row["id"])] = []
    return out


def games_fitting(question: Mapping[str, Any], people: Sequence[Mapping[str, Any]],
                  rows: Sequence[Mapping[str, Any]],
                  releases: Mapping[str, Releases]) -> dict[str, int]:
    """Each fitting game's id and how many releases of it fit."""
    return {game: len(found)
            for game, found in games_found(question, people, rows, releases).items()}


def vocabulary(releases: Mapping[str, Releases]) -> dict[str, list[str]]:
    """The features and table formats the releases carry, most used first."""
    count: dict[str, dict[str, int]] = {"feature": {}, "format": {}}
    for game in releases.values():
        for found in game.values():
            for release in found:
                for word in release.get("features") or ():
                    count["feature"][word] = count["feature"].get(word, 0) + 1
                if release.get("format"):
                    count["format"][release["format"]] = \
                        count["format"].get(release["format"], 0) + 1
    return {key: sorted(found, key=lambda word: (-found[word], word))
            for key, found in count.items()}
