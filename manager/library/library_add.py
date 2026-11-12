"""The + on the Games, Tables, Media and Assets pages.

What it brings in is handled as a file dropped on the page's empty space is, so there is one
conversation for every way in. A table the person asks to leave where it is has no upload
to go through: it makes its game here.
"""

from __future__ import annotations

import logging
import re
from functools import partial
from typing import Any

from nicegui import ui

from common import apps
from common.failures import why
from common.i18n import t
from manager.kit import offload, panel
from manager.kit.on_page import on_page
from manager.library import import_dialog, mediasource, uploads

logger = logging.getLogger("vpinfe.manager.library.library_add")

# The page, and the catalog key of what its Add is called.
NAMES = {"games": "manager.add.games", "tables": "manager.add.tables",
         "media": "manager.add.media", "assets": "manager.add.assets"}


def add_action(library: Any, state: dict[str, Any], view: str, *, empty: bool) -> Any:
    """The Add of `view`'s bar: its icon, or on an empty page the full button."""
    return panel.add_action([(t(NAMES[view]), partial(open_dialog, library, state, view))],
                            empty=empty)


def open_dialog(library: Any, state: dict[str, Any], view: str) -> None:
    mediasource.open_library_sources(
        library, t(NAMES[view]), partial(_took, state),
        tables=view in ("games", "tables"),
        referenced=partial(_game_from_table, library, state),
        find=_discovery(state, view))


def _discovery(state: dict[str, Any], view: str) -> tuple[str, Any] | None:
    """The way to Discovery, on the pages that make a game. Games words it for a game with
    no table yet, which Discovery's Add to Library makes."""
    if view not in ("games", "tables"):
        return None
    words = t("manager.add.find_game" if view == "games" else "manager.add.find_table")
    return words, partial(state["go"], "discovery")


async def _took(state: dict[str, Any], drop: uploads.Drop) -> None:
    await state["took_a_drop"](drop)


@on_page
async def _game_from_table(library: Any, state: dict[str, Any], path: str) -> None:
    """A game named for the table, pointing at the file where it lies. Where the game is
    made and the file refused, the game stays: it is on disk and the list shows it."""
    where = await import_dialog.ask_where(library)
    if where is None:
        return
    name = apps.strip_suffix(re.split(r"[\\/]", path.rstrip("\\/"))[-1])
    made = False
    try:
        game = await offload.io(library.create_game, name, where)
        made = True
        await offload.io(library.add_referenced_table, str(game["id"]), path)
    except Exception as exc:  # noqa: BLE001 - the reason belongs on screen
        logger.warning("manager: a game could not be made from %s", path, exc_info=True)
        ui.notify(t("said.add_failed"), caption=why(exc), type="negative")
    else:
        ui.notify(t("manager.add.added", name=name), type="positive")
    if made:
        state["redraw"]()
