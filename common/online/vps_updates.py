"""Updates for Your Games: the library's games with a later version of something they hold,
one tile each, minus whatever a person has skipped or ignored.

`library_updates.found` says what moved; this applies the marks. A move of a tied release is
left out when its game, the release, its updates or that version of it is ignored. A move
another source reports names no release, so only ignoring its game leaves it out; a
replaced picture (it carries an `md5`) is also left out by the marks the Discovery pane
writes for it.
"""

from __future__ import annotations

from typing import Any

from common.online import picture_changes, vps_marks

# How many tiles the shelf on Home shows. The nav count and See All hold every game.
SHELF_SHOWN = 12

# What `set_aside` names: the one version skipped, or every later version ignored.
SKIPPED = "skipped"
IGNORED = "ignored"


def wanted(move: dict[str, Any], marks: vps_marks.Marks) -> bool:
    """Whether a move still shows: no mark on its game, release, updates or version."""
    release_id = move["release_id"]
    if not release_id:
        if move.get("md5"):
            return not picture_changes.set_aside(move, marks)
        return not marks.game_ignored(move["vps_id"])
    return not (marks.release_ignored(release_id, move["vps_id"])
                or marks.ignored(vps_marks.UPDATES, release_id)
                or marks.ignored(vps_marks.VERSION, release_id, move["now"]))


def set_aside(move: dict[str, Any], marks: vps_marks.Marks) -> str:
    """How the person's own marks hide a move of a tied release: SKIPPED for that version,
    IGNORED for the release's updates, "" for neither. A release or game that is ignored is
    hidden outright and has neither."""
    release_id = move["release_id"]
    if not release_id or marks.release_ignored(release_id, move["vps_id"]):
        return ""
    if marks.ignored(vps_marks.UPDATES, release_id):
        return IGNORED
    if marks.ignored(vps_marks.VERSION, release_id, move["now"]):
        return SKIPPED
    return ""


def games(found: list[dict[str, Any]], marks: vps_marks.Marks | None = None,
          *, with_set_aside: bool = False) -> list[dict[str, Any]]:
    """Every game that still has a move showing, each with those moves, by name. With
    `with_set_aside` a move the person skipped or ignored stays, labelled by its `set_aside`
    ("" on the ones showing)."""
    marks = marks or vps_marks.Marks()
    out = []
    for one in found:
        if one["vps_id"] and marks.game_ignored(one["vps_id"]):
            continue
        if with_set_aside:
            shown = [{**move, "set_aside": set_aside(move, marks)} for move in one["moves"]
                     if wanted(move, marks) or set_aside(move, marks)]
        else:
            shown = [move for move in one["moves"] if wanted(move, marks)]
        if shown:
            out.append({**one, "moves": shown})
    out.sort(key=lambda one: (one["game"].casefold(), one["game_id"]))
    return out


def shelf(found: list[dict[str, Any]], marks: vps_marks.Marks | None = None, *,
          cut: int = 0, with_set_aside: bool = False) -> dict[str, Any]:
    """The shelf: how many games, their ids (what See All opens) and the games, the first
    `cut` of them where `cut` is set."""
    held = games(found, marks, with_set_aside=with_set_aside)
    return {"count": len(held), "game_ids": [one["game_id"] for one in held],
            "games": held[:cut] if cut else held}
