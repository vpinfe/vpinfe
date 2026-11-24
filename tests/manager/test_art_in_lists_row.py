"""The Thumbnails row in a list grid's view menu, opened on grids built in-process."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Callable
from functools import partial
from typing import Any
from unittest import mock
from unittest.mock import Mock

from nicegui import core, ui

from common.i18n import t
from manager.kit.data import Library
from manager.library import assets, collections, games, media, tageditor

SETTINGS = "/manager?view=settings&page=vpinfe.manager"
GAME = {"id": "g-1", "name": "Some Game", "folder": "/games/Some Game"}


def _library() -> Library:
    client = Mock()
    client.games.return_value = [GAME]
    client.library_game_collections.return_value = {}
    client.all_media.return_value = []
    client.preferences.return_value = {}
    client.library_policy.return_value = {}
    library = Library(client)
    library.games = [GAME]
    library.media = library._shared_media()
    library.load_game_collections()
    library.load_kept_kinds()
    return library


def _answered(*_args: Any, **_kwargs: Any) -> asyncio.Future:
    """What the grid says back, with no browser to say it: nothing hidden, no filter."""
    said = asyncio.get_running_loop().create_future()
    said.set_result([])
    return said


def _text(element: ui.element) -> str:
    return " ".join(str(getattr(one, "text", "")) for one in (element, *element.descendants())
                    if getattr(one, "text", ""))


class TheViewMenu(unittest.TestCase):
    def setUp(self) -> None:
        self.library = _library()
        for patcher in (mock.patch.object(ui, "run_javascript"),
                        mock.patch.object(ui, "notify"),
                        mock.patch.object(ui.aggrid, "run_grid_method",
                                          side_effect=_answered)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _links_in_menu(self, draw: Callable[[], None]) -> list[tuple[str, str]]:
        """Each row of the opened view menu that goes somewhere, as `(words, address)`."""
        holder = ui.card()

        async def inside() -> list[tuple[str, str]]:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with holder:
                    draw()
                button = next(one for one in holder.descendants()
                              if isinstance(one, ui.button)
                              and "manager-view-menu" in one.classes)
                for listener in button._event_listeners.values():
                    assert listener.handler is not None
                    listener.handler(None)
                menu = next(one for one in button.descendants() if isinstance(one, ui.menu))
                for _ in range(200):
                    if menu.default_slot.children:
                        break
                    await asyncio.sleep(0.01)
                self.assertTrue(menu.default_slot.children, "the menu was never filled")
                return [(_text(one), str(one.props["href"])) for one in menu.descendants()
                        if one.props.get("href")]
            finally:
                core.loop = was

        return asyncio.run(inside())

    def test_each_list_grid_links_to_the_setting(self) -> None:
        grids = {
            "games": lambda: games.build(self.library.game_rows(),
                                         self.library.kinds_present(), self.library,
                                         lambda _row: None, {"view": "games"}),
            "tables": lambda: games.build_tables([{"id": "t-1", "game_id": "g-1"}],
                                                 self.library, lambda _row: None,
                                                 {"view": "tables"}),
            "media": lambda: media.build([], self.library, lambda _row: None,
                                         {"view": "media"}),
            "assets": lambda: assets.build([], self.library, lambda _row: None,
                                           {"view": "assets"}),
            "collections": lambda: collections.build([], self.library, lambda _row: None,
                                                     {"view": "collections"}),
        }
        for name, draw in grids.items():
            with self.subTest(grid=name):
                self.assertEqual(self._links_in_menu(draw),
                                 [(t("manager.games.thumbnails"), SETTINGS)])

    def test_a_grid_that_is_not_a_list_has_none(self) -> None:
        tag = {"id": "classic", "tag": "Classic", "same": "classic", "games": 1}
        draw = partial(tageditor.build, [tag], self.library, lambda _row: None,
                       {"view": "tags"})

        self.assertEqual(self._links_in_menu(draw), [])


if __name__ == "__main__":
    unittest.main()
