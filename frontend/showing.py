"""The windows' side of being asked from outside to show a collection or move the wheel.

Both go out as the messages a theme already follows, and each window does the rest the
way it would for core's own picker - so nothing here touches the shared view.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from common.games import game_identity, game_repository
from common.games.collection_store import BUILTIN_ALL, public_name
from common.host import frontend_state
from common.i18n import t
from common.service_errors import NotFoundError

if TYPE_CHECKING:
    from frontend.device_channel import DeviceChannel
    from frontend.library_resolver import LibraryResolver


def register(ws_bridge: DeviceChannel, library: LibraryResolver) -> None:
    def show(collection: str) -> None:
        if collection and collection != BUILTIN_ALL and library.stored(collection) is None:
            raise NotFoundError(t("error.collections.not_found", name=collection))
        ws_bridge.send_event_all_with_iframe({
            "type": "TableDataChange", "index": 0,
            "collection": public_name(collection) or "None"})

    def move_to(game_id: str) -> None:
        for index, entry in enumerate(library.entries):
            if entry.game is not None and game_identity.game_id(entry.game) == game_id:
                # The table's id is what a row is named by on the window, the game's
                # only where the table has none.
                ws_bridge.send_event_all_with_iframe({"type": "TableIndexUpdate", "index": index,
                                                      "id": entry.table_id or game_id})
                return
        raise NotFoundError(t("error.frontend.not_on_screen",
                              game=game_repository.called(game_id)))

    def windows_changed(connected: int) -> None:
        if connected:
            frontend_state.started(public_name(library.current_collection))
        else:
            frontend_state.stopped()

    ws_bridge.on_windows_changed(windows_changed)
    frontend_state.register_driver(show, move_to)
