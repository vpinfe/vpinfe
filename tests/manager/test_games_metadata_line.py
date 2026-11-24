"""The line above the Games and Tables grids, over a library written to disk and read by
the scan."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Awaitable, Callable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock
from unittest.mock import Mock

from nicegui import core, ui

from common.games import game_identity, game_repository, library_ops, unwritten
from common.games.game_parser import GameParser
from common.games.info_maintenance import upgrade_library
from common.i18n import t
from manager.kit.data import Library
from manager.library import games
from manager.overview import needs
from manager.settings import sections
from tests.support import lines
from tests.support.library import game_info, write_game
from tests.support.skips import needs_posix_permissions

KEPT = "Kept Game (Original 2024)"
BROKEN = "Malformed Info (Original 2024)"
OLDER = "Older Game (Original 2024)"
LOCKED = "Locked Game (Original 2024)"
GAME = {"id": "g-1", "name": KEPT, "folder": f"/games/{KEPT}"}
LEGACY = {"Info": {"Title": "Older Game", "Rom": "older"},
          "VPXFile": {"filename": f"{OLDER}.vpx", "filehash": "abc", "rom": "older"}}


def _press(within: ui.element, label: str) -> None:
    button = next(one for one in within.descendants()
                  if isinstance(one, ui.button) and one.text == label)
    for listener in button._event_listeners.values():
        assert listener.handler is not None
        listener.handler(None)


async def _now(callback: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class TheLineAboveTheGrid(unittest.TestCase):
    def setUp(self) -> None:
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.locked: set[str] = set()
        write_game(self.root, KEPT, info=game_info(KEPT))
        client = Mock()
        client.games.return_value = [GAME]
        client.library_game_collections.return_value = {}
        client.all_media.return_value = []
        client.preferences.return_value = {}
        client.library_policy.return_value = {}
        client.info_maintenance.side_effect = self._metadata
        self.library = Library(client)
        self.library.games = [GAME]
        self.library.media = self.library._shared_media()
        self.library.load_game_collections()
        self.library.load_kept_kinds()
        self.opened: list[needs.Opens] = []
        self.redrawn = 0
        self.state: dict[str, Any] = {"view": "games", "open_on": self._open_on}
        for patcher in (mock.patch.object(ui, "run_javascript"),
                        mock.patch.object(ui, "notify"),
                        mock.patch.dict(unwritten._HELD, clear=True),
                        mock.patch.object(unwritten, "logger")):
            patcher.start()
            self.addCleanup(patcher.stop)

    async def _open_on(self, opens: needs.Opens) -> None:
        self.opened.append(opens)

    def _metadata(self) -> dict[str, Any]:
        parser = GameParser(self.root)
        parsed = parser.get_all_games()
        # Only the locked ones: an id written to a writable 2.x `.info` upgrades it.
        game_identity.ensure_unique_ids(
            [one for one in parsed if one.game_dir_name in self.locked], order=[])
        with mock.patch.object(game_repository, "all_games", return_value=parsed), \
                mock.patch.object(game_repository, "unreadable_games",
                                  return_value=parser.get_unreadable_games()):
            return library_ops.info_maintenance()

    def _lock(self, name: str, info: dict[str, Any]) -> Path:
        folder = write_game(self.root, name, info=info)
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)
        self.locked.add(name)
        return folder

    def _plant_broken(self) -> None:
        (write_game(self.root, BROKEN) / f"{BROKEN}.info").write_text(
            '{"Info": {"Title": "broken",,,}', encoding="utf-8")

    def _build(self) -> None:
        games.build(self.library.game_rows(), self.library.kinds_present(),
                    self.library, lambda _row: None, self.state)

    def _drawn(self, then: Callable[[ui.element], Awaitable[None]] | None = None
               ) -> ui.element:
        """The page as the Manager draws it, after the library is read."""
        self.library.read_metadata_state()
        holder = ui.card()

        async def inside() -> None:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with holder:
                    self._build()
                if then is not None:
                    await then(holder)
            finally:
                core.loop = was
        asyncio.run(inside())
        return holder

    def _line(self, holder: ui.element) -> list[str]:
        return [one.text for one in holder.descendants()
                if isinstance(one, ui.label) and "manager-attention-line" in one.classes]

    async def _list(self, holder: ui.element, which: str) -> ui.dialog | None:
        """The folder list a Needs You row opens, over the page, after the library is read.
        None where the list has emptied and the page was redrawn instead."""
        self.library.read_metadata_state()
        layout = holder.client.layout
        before = {one for one in layout.descendants() if isinstance(one, ui.dialog)}
        with holder:
            await sections.metadata_folders(self.library, self.state, which, self._redraw)
        [*opened] = [one for one in layout.descendants()
                     if isinstance(one, ui.dialog) and one not in before]
        for box in opened:
            self.addCleanup(box.delete)
        return opened[0] if opened else None

    def _redraw(self) -> None:
        self.redrawn += 1

    def _folders(self, box: ui.dialog | None) -> dict[str, str]:
        return lines.folders(box) if box is not None else {}

    def test_a_folder_it_could_not_read_is_said_above_the_grid(self) -> None:
        self._plant_broken()

        self.assertEqual([t("manager.sections.unlisted", count=1)],
                         self._line(self._drawn()))

    def test_a_game_on_an_older_format_is_said_above_the_grid(self) -> None:
        write_game(self.root, OLDER, info=LEGACY)

        self.assertEqual([t("manager.sections.older_format", count=1)],
                         self._line(self._drawn()))

    def test_a_library_with_nothing_wrong_draws_no_line(self) -> None:
        holder = self._drawn()

        self.assertEqual([], [one for one in holder.descendants()
                              if "manager-attention" in one.classes])

    def _stop(self, *checks: str) -> None:
        patcher = mock.patch.object(self.library, "stopped", return_value=set(checks))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_a_check_that_is_stopped_leaves_its_count_out_of_the_line(self) -> None:
        self._plant_broken()
        write_game(self.root, OLDER, info=LEGACY)
        self._stop("older_format")

        self.assertEqual([t("manager.sections.unlisted", count=1)],
                         self._line(self._drawn()))

    def test_unreadable_folders_stopped_leave_them_out_of_the_line(self) -> None:
        self._plant_broken()
        write_game(self.root, OLDER, info=LEGACY)
        self._stop("unreadable")

        self.assertEqual([t("manager.sections.older_format", count=1)],
                         self._line(self._drawn()))

    @needs_posix_permissions
    def test_both_checks_stopped_still_name_the_folders_it_could_not_write(self) -> None:
        self._plant_broken()
        write_game(self.root, OLDER, info=LEGACY)
        self._lock(LOCKED, game_info(LOCKED))
        self._stop("unreadable", "older_format")

        self.assertEqual([t("manager.sections.not_written", count=1)],
                         self._line(self._drawn()))

    def test_every_check_stopped_with_nothing_unwritten_draws_no_line(self) -> None:
        self._plant_broken()
        write_game(self.root, OLDER, info=LEGACY)
        self._stop("unreadable", "older_format")
        holder = self._drawn()

        self.assertEqual([], [one for one in holder.descendants()
                              if "manager-attention" in one.classes])

    def test_show_names_the_folder_and_why(self) -> None:
        self._plant_broken()
        named: dict[str, str] = {}

        async def show(holder: ui.element) -> None:
            named.update(self._folders(await self._list(holder, "unreadable")))

        self._drawn(show)

        self.assertEqual({BROKEN: t("error.games.info_bad_line", line=1)}, named)

    def test_show_opens_overview(self) -> None:
        self._plant_broken()

        async def show(holder: ui.element) -> None:
            _press(holder, t("manager.sections.show"))

        self._drawn(show)

        self.assertEqual([needs.Opens("overview")], self.opened)

    def test_an_upgrade_that_leaves_nothing_takes_the_line_away(self) -> None:
        write_game(self.root, OLDER, info=LEGACY)
        api = Mock()
        api.upgrade_info.return_value = {"id": "u-1"}
        seen: dict[str, Any] = {}

        async def ended(_client: Any, _job_id: str) -> dict[str, Any]:
            return {"state": "done", "result": upgrade_library(self.root)}

        async def upgrade(holder: ui.element) -> None:
            seen["before"] = self._line(holder)
            body = await self._list(holder, "older_format")
            assert body is not None
            seen["named"] = list(self._folders(body))
            _press(body, t("word.upgrade"))
            for _ in range(50):
                await asyncio.sleep(0)
            seen["redrawn"] = self.redrawn
            with holder:
                again = ui.card()
            with again:
                self._build()
            seen["after"] = self._line(again)

        with mock.patch("manager.kit.confirm.ask", mock.AsyncMock(return_value=True)), \
                mock.patch("manager.kit.api.ApiClient", return_value=api), \
                mock.patch("nicegui.run.io_bound", new=_now), \
                mock.patch.object(sections, "_ended", ended):
            self._drawn(upgrade)

        self.assertEqual({"before": [t("manager.sections.older_format", count=1)],
                          "named": [OLDER], "redrawn": 1, "after": []}, seen)

    @needs_posix_permissions
    def test_a_game_whose_folder_could_not_be_written_is_said_above_the_grid(self) -> None:
        self._lock(LOCKED, game_info(LOCKED))

        self.assertEqual([t("manager.sections.not_written", count=1)],
                         self._line(self._drawn()))

    @needs_posix_permissions
    def test_show_names_the_folder_it_could_not_write_and_why(self) -> None:
        folder = self._lock(LOCKED, game_info(LOCKED))
        named: dict[str, str] = {}

        async def show(holder: ui.element) -> None:
            named.update(self._folders(await self._list(holder, "unwritten")))

        self._drawn(show)

        self.assertEqual({LOCKED: t("said.why.no_permission_at", path=str(folder))}, named)

    @needs_posix_permissions
    def test_a_locked_game_on_an_older_format_is_counted_once_as_not_written(self) -> None:
        self._lock(OLDER, LEGACY)
        seen: dict[str, Any] = {}

        async def show(holder: ui.element) -> None:
            seen["line"] = self._line(holder)
            seen["unwritten"] = list(self._folders(await self._list(holder, "unwritten")))
            seen["opened_older"] = await self._list(holder, "older_format") is not None
            seen["redrawn"] = self.redrawn

        self._drawn(show)

        self.assertEqual({"line": [t("manager.sections.not_written", count=1)],
                          "unwritten": [OLDER], "opened_older": False, "redrawn": 1}, seen)

    @needs_posix_permissions
    def test_a_folder_upgrade_could_not_write_is_named_once(self) -> None:
        write_game(self.root, OLDER, info=LEGACY)
        self._lock(LOCKED, LEGACY)
        api = Mock()
        api.upgrade_info.return_value = {"id": "u-1"}
        seen: list[tuple[list[str], list[str]]] = []

        async def ended(_client: Any, _job_id: str) -> dict[str, Any]:
            return {"state": "done", "result": upgrade_library(self.root)}

        async def named(holder: ui.element) -> tuple[list[str], list[str]]:
            older = await self._list(holder, "older_format")
            return (list(self._folders(older)),
                    list(self._folders(await self._list(holder, "unwritten"))))

        async def upgrade(holder: ui.element) -> None:
            seen.append(await named(holder))
            older = await self._list(holder, "older_format")
            assert older is not None
            _press(older, t("word.upgrade"))
            for _ in range(50):
                await asyncio.sleep(0)
            seen.append(await named(holder))

        with mock.patch("manager.kit.confirm.ask", mock.AsyncMock(return_value=True)), \
                mock.patch("manager.kit.api.ApiClient", return_value=api), \
                mock.patch("nicegui.run.io_bound", new=_now), \
                mock.patch.object(sections, "_ended", ended):
            self._drawn(upgrade)

        self.assertEqual([([OLDER], [LOCKED]), ([], [LOCKED])], seen)


class TheLineAboveTheTablesGrid(TheLineAboveTheGrid):
    def _build(self) -> None:
        games.build_tables(self.library.table_rows(), self.library, lambda _row: None,
                           self.state)


if __name__ == "__main__":
    unittest.main()
