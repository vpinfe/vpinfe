"""Reading a library that is in more than one place."""

from __future__ import annotations

import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common import mounts
from common.config import path_state
from common.games import game_repository
from common.games.library_folders import KIND_GAME, KIND_ROOT, Location
from tests.support.hung_folder import never_answering


def _game_folder(root: Path, name: str) -> Path:
    folder = root / name
    folder.mkdir(parents=True)
    (folder / f"{name}.vpx").write_text("")
    return folder


class ManyLocationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

        self.first = self.root / "share"
        self.second = self.root / "local"
        _game_folder(self.first, "One (Bally 1990)")
        _game_folder(self.second, "Two (Gottlieb 1975)")

        game_repository._PARSERS.clear()
        game_repository._AWAY.clear()
        self.addCleanup(game_repository._PARSERS.clear)
        self.addCleanup(game_repository._AWAY.clear)

    def _configured(self, *paths: Path, kind: str = KIND_ROOT) -> list[Location]:
        return [Location(location_id=f"loc{n}", path=str(path), kind=kind)
                for n, path in enumerate(paths)]

    def _with(self, *paths: Path, kind: str = KIND_ROOT):
        return mock.patch.object(game_repository.library_folders, "configured",
                                 return_value=self._configured(*paths, kind=kind))

    def test_a_location_that_is_one_game_reads_that_game(self) -> None:
        folder = self.first / "One (Bally 1990)"

        with self._with(folder, kind=KIND_GAME):
            games = game_repository.all_games()

        self.assertEqual([g.game_dir_name for g in games], ["One (Bally 1990)"])

    def test_the_same_folder_read_as_a_root_finds_nothing_in_it(self) -> None:
        folder = self.first / "One (Bally 1990)"

        with self._with(folder, kind=KIND_ROOT):
            self.assertEqual(game_repository.all_games(), [])

    def test_changing_the_kind_rereads_the_same_path(self) -> None:
        folder = self.first / "One (Bally 1990)"

        with self._with(folder, kind=KIND_ROOT):
            self.assertEqual(game_repository.all_games(), [])
        with self._with(folder, kind=KIND_GAME):
            self.assertEqual(len(game_repository.all_games()), 1)

    def test_every_location_is_read_and_each_game_says_where_it_came_from(self) -> None:
        with self._with(self.first, self.second):
            games = game_repository.all_games()

        self.assertEqual({g.game_dir_name: g.location_id for g in games},
                         {"One (Bally 1990)": "loc0", "Two (Gottlieb 1975)": "loc1"})

    def test_one_unreachable_location_does_not_empty_the_library(self) -> None:
        """The whole point: a share that has not mounted is one location reading as
        unreachable, not four hundred tables vanishing."""
        with self._with(self.root / "never-mounted", self.second):
            games = game_repository.all_games()

        self.assertEqual([g.game_dir_name for g in games], ["Two (Gottlieb 1975)"])

    def test_a_location_that_does_not_answer_is_skipped_within_the_bound(self) -> None:
        with self._with(self.first, self.second), never_answering(str(self.first)), \
                mock.patch.object(path_state, "MOUNT_SECONDS", 0.2):
            started = time.monotonic()
            games = game_repository.all_games()
            took = time.monotonic() - started

        self.assertEqual([g.game_dir_name for g in games], ["Two (Gottlieb 1975)"])
        self.assertLess(took, 1.0)

    def test_a_location_whose_share_is_not_mounted_is_not_read_as_empty(self) -> None:
        share = mounts.Origin(mounts.NFS, "nas.lan", "/export", str(self.first))

        def where(path: str, recorded: mounts.Origin | None) -> mounts.Where:
            return mounts.Where(share, connected=False) \
                if path.startswith(str(self.first)) else mounts.Where()

        with self._with(self.first, self.second), \
                mock.patch.object(mounts, "where", side_effect=where):
            games = game_repository.all_games()

        self.assertEqual([g.game_dir_name for g in games], ["Two (Gottlieb 1975)"])
        self.assertIn((str(self.first), KIND_ROOT), game_repository._AWAY)

    def test_an_empty_library_says_what_it_is_waiting_on(self) -> None:
        away = self.root / "not-mounted"
        with self._with(away):
            self.assertEqual(game_repository.all_games(), [])
            self.assertEqual(game_repository.waiting_for(), [Location("loc0", str(away)).name])

    def test_once_a_folder_answers_the_library_is_read_and_nothing_is_waited_on(
            self) -> None:
        later = self.root / "mounted-later"
        with self._with(later):
            game_repository.all_games()
            _game_folder(later, "Three (Stern 2016)")
            waiting = game_repository.waiting_for()
            games = game_repository.all_games()

        self.assertEqual(waiting, [])
        self.assertEqual([g.game_dir_name for g in games], ["Three (Stern 2016)"])

    def test_a_library_with_games_waits_on_nothing(self) -> None:
        with self._with(self.second, self.root / "not-mounted"):
            game_repository.all_games()
            self.assertEqual(game_repository.waiting_for(), [])

    def test_a_folder_on_a_share_is_waited_on_by_its_server(self) -> None:
        share = mounts.Origin(mounts.NFS, "nas.lan", "/export", str(self.root / "nas"))
        with self._with(self.root / "nas" / "a", self.root / "nas" / "b"), \
                mock.patch.object(mounts, "where",
                                  return_value=mounts.Where(share, connected=False)):
            game_repository.all_games()
            self.assertEqual(game_repository.waiting_for(), ["nas.lan"])

    def test_a_location_found_away_is_asked_again_on_reload_and_not_before(self) -> None:
        later = self.root / "mounted-later"
        with self._with(later, self.second):
            game_repository.all_games()
            _game_folder(later, "Three (Stern 2016)")
            before = game_repository.all_games()
            after = game_repository.all_games(reload=True)

        self.assertEqual([g.game_dir_name for g in before], ["Two (Gottlieb 1975)"])
        self.assertEqual(sorted(g.game_dir_name for g in after),
                         ["Three (Stern 2016)", "Two (Gottlieb 1975)"])

    def test_dropping_a_location_drops_its_games(self) -> None:
        with self._with(self.first, self.second):
            game_repository.all_games()
        with self._with(self.second):
            games = game_repository.all_games()

        self.assertEqual([g.game_dir_name for g in games], ["Two (Gottlieb 1975)"])
        self.assertEqual([path for path, _kind in game_repository._PARSERS],
                         [str(self.second)])

    def test_a_location_is_read_once_and_held(self) -> None:
        with self._with(self.first, self.second):
            game_repository.all_games()
            with mock.patch.object(game_repository, "GameParser") as built:
                game_repository.all_games()

        built.assert_not_called()

    def test_refreshing_one_folder_asks_the_location_holding_it(self) -> None:
        """Asking the wrong parser to re-read it would add the game to a location it is
        not in."""
        with self._with(self.first, self.second):
            game_repository.all_games()
            found = game_repository.refresh_game(self.second / "Two (Gottlieb 1975)")

        self.assertEqual([g.game_dir_name for g in found], ["Two (Gottlieb 1975)"])
        self.assertEqual([g.location_id for g in found], ["loc1"])


class RereadMediaSettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.clear)

    def _hold(self, *answers: bool) -> list[mock.Mock]:
        parsers = [mock.Mock(**{"read_media_settings.return_value": a}) for a in answers]
        for number, one in enumerate(parsers):
            game_repository._PARSERS[(f"/folder-{number}", KIND_ROOT)] = one
        return parsers

    def test_unchanged_settings_leave_the_library_alone(self) -> None:
        self._hold(False, False)
        with mock.patch.object(game_repository, "get_ini_config"), \
             mock.patch.object(game_repository, "all_games") as reread:
            self.assertFalse(game_repository.reread_media_settings())
        reread.assert_not_called()

    def test_one_location_under_other_settings_reads_the_library_again(self) -> None:
        parsers = self._hold(False, True)
        with mock.patch.object(game_repository, "get_ini_config"), \
             mock.patch.object(game_repository, "all_games") as reread:
            self.assertTrue(game_repository.reread_media_settings())
        reread.assert_called_once_with(reload=True)
        for one in parsers:
            one.read_media_settings.assert_called_once()


if __name__ == "__main__":
    unittest.main()
