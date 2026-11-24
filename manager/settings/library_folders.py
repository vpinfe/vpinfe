"""What the Folders page and the Library Importer's button share: the state words and the
importer's import action.

What the disk says is read on every draw and never stored. A stored answer is wrong the
moment a mount drops, and a mount dropping is the case plural locations exist to report.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from weakref import WeakKeyDictionary

from nicegui import run, ui

from common.config import path_state
from common.i18n import t
from manager.kit import offload
from manager.kit.data import Library
from manager.kit.on_page import on_page

IMPORTER = "library_importer"
# The kind of job the importer's run is, which names the extension.
KIND_IMPORT = f"{IMPORTER}.import"

STATE_WORDS = {path_state.OK: "word.ready", path_state.READ_ONLY: "word.read_only",
               path_state.NOT_CONNECTED: "word.not_connected",
               path_state.MISSING: "word.not_found", path_state.WRONG_KIND: "word.not_found",
               path_state.NOT_ANSWERING: "word.not_answering"}
# What each state costs, for its chip. Anything not listed is a location that cannot be read.
STATE_TIERS = {path_state.OK: "on", path_state.READ_ONLY: "warn"}

# Each open page's library, so an import started anywhere on it (the Folders button, a
# report's Try Again) can refresh what it changed.
_LIBRARIES: WeakKeyDictionary[Any, Library] = WeakKeyDictionary()

ASKING = {"updates": {"ask_where_new_games_go": True}}
NOT_ASKING = {"updates": {"ask_where_new_games_go": False}}


def state_of(row: dict[str, Any]) -> str:
    """The fewest true words. Why is in the row's reason."""
    word = STATE_WORDS.get(str(row.get("state") or ""))
    return t(word) if word else ""


async def import_action(library: Library) -> dict | None:
    """The Library Importer's import, when that extension is running - a button that
    refuses is worse than none, so an extension that is off or gone offers nothing."""
    try:
        found = await offload.io(library.extensions)
    except Exception:  # noqa: BLE001 - a list that will not load is no action
        return None
    return import_action_in(found)


def import_action_in(extensions: list[dict]) -> dict | None:
    """The import among these extensions' actions, if the importer is one of them."""
    importer = next((one for one in extensions if one.get("name") == IMPORTER), {})
    return next((one for one in importer.get("actions") or [] if one.get("key") == "import"),
                None)


def hold_library(library: Library) -> None:
    """Make this the library that an import started on the current page refreshes."""
    _LIBRARIES[ui.context.client] = library


@on_page
async def open_importer(action: dict, rerender: Callable[[], None] | None, *,
                        values: dict[str, Any] | None = None, until: str = "") -> None:
    """The import dialog, then the page's library read again and the page redrawn: an import
    adds games, tables and media, and can add a library folder. `values` are answers it
    starts with, and `until` the step it is walked to before it is shown."""
    from manager.extensions import ext_action

    page = ui.context.client
    if not await ext_action.open_action(IMPORTER, action, values=values, until=until):
        return
    library = _LIBRARIES.get(page)
    if library is not None:
        await run.io_bound(library.refresh_after_import)
    if rerender is not None:
        rerender()
