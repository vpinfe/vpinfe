"""A game folder VPinFE may not write to still shows, and the rest of the library loads.

Read from real 2.x folders with no id, one of them made read-only, through the API.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import ids
from common.games import game_repository, table_identity, unwritten
from common.games.library_folders import KIND_ROOT, Location
from tests.support.library import TempTree, write_game
from tests.support.skips import needs_posix_permissions

LEGACY = {"Info": {"Title": "Sample Game", "Rom": "sample"},
          "VPXFile": {"filename": "Sample Game.vpx", "filehash": "abc", "rom": "sample"}}
LOCKED = "Locked Game (Original 2024)"
KEPT = "Sample Game (Original 2024)"


@needs_posix_permissions
class LockedFolderTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        for name in (LOCKED, KEPT):
            write_game(self.root, name, info=LEGACY)
        self.locked = self.root / LOCKED
        self.locked.chmod(0o555)
        self.addCleanup(self.locked.chmod, 0o755)

        held = mock.patch.object(
            game_repository.library_folders, "configured",
            return_value=[Location(location_id="test", path=str(self.root),
                                   kind=KIND_ROOT)])
        held.start()
        self.addCleanup(held.stop)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)
        cleared = mock.patch.dict(unwritten._HELD, clear=True)
        cleared.start()
        self.addCleanup(cleared.stop)
        logged = mock.patch.object(unwritten.logger, "warning")
        self.logged = logged.start()
        self.addCleanup(logged.stop)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _listed(self) -> dict[str, str]:
        answer = self.client.get("/games")
        self.assertEqual(answer.status_code, 200, answer.text)
        return {Path(one["folder"]).name: one["id"] for one in answer.json()["games"]}

    def _on_disk(self, name: str) -> str:
        info = json.loads((self.root / name / f"{name}.info").read_text(encoding="utf-8"))
        return str((info.get("vpinfe") or {}).get("game_id") or "")

    def test_the_library_lists_with_the_locked_game_in_it(self) -> None:
        listed = self._listed()

        self.assertEqual(sorted(listed), [LOCKED, KEPT])
        self.assertTrue(all(listed.values()), listed)

    def test_the_games_after_it_get_their_ids(self) -> None:
        listed = self._listed()

        self.assertEqual(listed[KEPT], self._on_disk(KEPT))
        self.assertEqual(self._on_disk(LOCKED), "")

    def test_the_write_that_failed_is_logged_with_its_game(self) -> None:
        self._listed()

        self.assertEqual([one.args[1] for one in self.logged.call_args_list], [LOCKED])

    def test_the_locked_game_answers_to_one_id_while_it_is_held(self) -> None:
        first = self._listed()[LOCKED]

        self.assertEqual(self.client.get(f"/games/{first}").status_code, 200)
        self.assertEqual(self._listed()[LOCKED], first)

    def test_a_refresh_hands_it_the_same_id(self) -> None:
        first = self._listed()[LOCKED]

        game_repository.refresh_games()

        self.assertEqual(self._listed()[LOCKED], first)
        self.assertEqual([one.args[1] for one in self.logged.call_args_list], [LOCKED])

    def test_the_id_it_held_is_the_one_written_once_it_can_be(self) -> None:
        first = self._listed()[LOCKED]
        self.locked.chmod(0o755)

        game_repository.refresh_games()

        self.assertEqual(self._listed()[LOCKED], first)
        self.assertEqual(self._on_disk(LOCKED), first)

    def _restart(self) -> None:
        unwritten._HELD.clear()
        game_repository._PARSERS.clear()
        game_repository.all_games(reload=True)

    def _tables(self, name: str) -> dict[str, str]:
        games = game_repository.all_games()
        table_identity.ensure_unique_table_ids(games)
        game = next(one for one in games if one.game_dir_name == name)
        return table_identity.table_ids(game)

    def test_the_id_is_made_from_its_location_and_its_path_inside_it(self) -> None:
        self.assertEqual(self._listed()[LOCKED], ids.derive_id("test", LOCKED))

    def test_it_has_the_same_id_after_a_restart(self) -> None:
        first = self._listed()[LOCKED]

        self._restart()

        self.assertEqual(self._listed()[LOCKED], first)

    def test_its_tables_have_the_same_ids_after_a_restart(self) -> None:
        self._listed()
        first = self._tables(LOCKED)

        self._restart()

        self.assertTrue(first)
        self.assertNotEqual(list(first), list(first.values()), "ids, not the names they replace")
        self.assertEqual(self._tables(LOCKED), first)

    def test_the_id_it_had_before_a_restart_is_written_once_it_can_be(self) -> None:
        first = self._listed()[LOCKED]
        tables = self._tables(LOCKED)
        self._restart()
        self.locked.chmod(0o755)

        game_repository.refresh_games()

        self.assertEqual(self._listed()[LOCKED], first)
        self.assertEqual(self._on_disk(LOCKED), first)
        self.assertEqual(self._tables(LOCKED), tables)

    def test_it_counts_as_not_written_and_not_as_one_to_upgrade(self) -> None:
        self._listed()

        info = self.client.get("/library/info").json()

        self.assertEqual((info["pending_upgrade"], info["pending_games"]), (0, []))
        self.assertEqual([one["folder"] for one in info["unwritten"]], [LOCKED])
