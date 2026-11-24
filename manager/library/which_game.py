"""Which game a drop on the library is for, when it brought no table.

A drop that names no game and holds no table cannot make one, and says nothing about
which existing game it is for. The games are offered, the ones the files' names point at
first, and the answer goes on as a drop on that game's row.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from pathlib import PurePosixPath
from typing import Any

from common.games.game_names import of_game, squashed
from common.i18n import t
from manager.kit import dialog as frame
from manager.kit import offload, panel, thumbnails, verbs
from manager.kit.on_page import on_page


def _mentions(games: Sequence[dict[str, Any]], files: Sequence[str]) -> dict[str, int]:
    """For each game the files name, how many of them do. Each name is read once, so a
    drop of thousands of entries over a library of thousands of games stays cheap."""
    stems = Counter(squashed(PurePosixPath(one).stem) for one in files)
    counts: dict[str, int] = {}
    for game in games:
        plain = of_game(str(game.get("name") or ""))
        found = sum(many for stem, many in stems.items() if plain and plain in stem)
        if found:
            counts[str(game["id"])] = found
    return counts


def ranked(games: Sequence[dict[str, Any]], files: Sequence[str]) -> list[dict[str, Any]]:
    """The games, those more of the files name first and the rest as the library has
    them."""
    counts = _mentions(games, files)
    return sorted(games, key=lambda game: -counts.get(str(game["id"]), 0))


def named(games: Sequence[dict[str, Any]], files: Sequence[str]) -> str:
    """The id of the one game the files name, or "" where they name none or several. A
    name inside a longer one that is also named ("Taxi" in "Crazy Taxi") is the longer
    one's."""
    counts = _mentions(games, files)
    found = {str(game["id"]): of_game(str(game.get("name") or ""))
             for game in games if str(game["id"]) in counts}
    sure = [one for one, plain in found.items()
            if not any(plain != other and plain in other for other in found.values())]
    return sure[0] if len(sure) == 1 else ""


def _arranged(games: Sequence[dict[str, Any]],
              files: Sequence[str]) -> tuple[list[dict[str, Any]], str]:
    return ranked(games, files), named(games, files)


@on_page
async def ask(library: Any, analysis: dict[str, Any]) -> dict[str, Any] | None:
    """The game chosen, or None where the drop is cancelled."""
    files = [str(one) for one in analysis.get("files") or []]
    games, preselected = await offload.io(_arranged, library.games, files)
    chosen: dict[str, str] = {"id": preselected}
    with frame.opened(t("manager.which_game.title"), ignore_outside_click=True) as box:
        picker = panel.GamePicker(games, set(), label=t("word.game"), art=library.game_art()) \
            .props("outlined hide-selected fill-input input-debounce=0 clearable") \
            .classes("w-full")
        (library.thumbnail_look() or thumbnails.Look()).in_panel() \
            .apply_to_options(picker, "manager-picker-popup")
        with frame.footer():
            frame.cancel(lambda: box.submit(None))
            go = frame.answer(t("word.continue"), lambda: box.submit(chosen["id"]),
                              icon=verbs.NEXT)
        go.set_enabled(bool(chosen["id"]))

        def picked() -> None:
            chosen["id"] = str(picker.value or "")
            go.set_enabled(bool(chosen["id"]))

        if chosen["id"]:
            picker.value = chosen["id"]
        picker.on_value_change(picked)
        frame.focus(box, picker)

    said = await box
    return next((one for one in games if str(one["id"]) == said), None) if said else None
