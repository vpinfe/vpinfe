"""Where a new game folder is created.

The location marked "create new games here" is a choice somebody made and a row on
screen says so. Writing to a different one because that row went read-only makes the
screen a lie, and they find out by looking for a game where they expected it.
"""

from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory

from common.games import library_folders
from common.i18n import t
from tests.support.library import own_locations
from tests.support.skips import needs_posix_permissions


class _Destinations(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        from pathlib import Path
        self.root = Path(self.tmp.name)
        for name in ("first", "second", "single"):
            (self.root / name).mkdir()
        self.store = own_locations(self, self.root / "config")

    def _save(self, *rows, write_to=""):
        self.store.save(list(rows), write_to)

    def _root(self, location_id, name, kind="root"):
        return library_folders.Location(location_id=location_id,
                                  path=str(self.root / name), kind=kind)


class ChoiceTests(_Destinations):
    def test_the_chosen_location_is_where_a_new_game_goes(self) -> None:
        self._save(self._root("l1", "first"), self._root("l2", "second"),
                   write_to="l2")

        self.assertEqual(library_folders.destination().path, str(self.root / "second"))

    def test_with_nothing_chosen_the_first_writable_root_answers(self) -> None:
        """An install that has never been asked still has to be able to create."""
        self._save(self._root("l1", "first"), self._root("l2", "second"))

        self.assertEqual(library_folders.destination().path, str(self.root / "first"))

    def test_a_one_time_override_wins(self) -> None:
        self._save(self._root("l1", "first"), self._root("l2", "second"),
                   write_to="l1")

        self.assertEqual(library_folders.destination("l2").path, str(self.root / "second"))


class RefusalTests(_Destinations):
    def test_a_one_time_override_that_is_gone_is_refused_by_name(self) -> None:
        """Rather than quietly becoming a different folder."""
        self._save(self._root("l1", "first"), write_to="l1")

        found = library_folders.destination("missing")

        self.assertIsNone(found.location)
        self.assertEqual(found.reason, t("error.library_folders.default_removed"))

    def test_a_choice_whose_location_was_removed_is_no_choice(self) -> None:
        """Removing the folder new games went to leaves the list as the way to say
        where, so the first writable root answers rather than every create failing."""
        self._save(self._root("l1", "first"), write_to="removed")

        found = library_folders.destination()

        self.assertEqual(found.path, str(self.root / "first"))

    @needs_posix_permissions
    def test_and_a_read_only_one_is_refused_too(self) -> None:
        import os
        os.chmod(self.root / "second", 0o500)
        self.addCleanup(os.chmod, self.root / "second", 0o700)
        second = self._root("l2", "second")
        self._save(self._root("l1", "first"), second, write_to="l2")

        found = library_folders.destination()

        self.assertIsNone(found.location)
        self.assertEqual(found.reason, t("error.path.read_only", name=second.name))

    def test_an_unreachable_one_says_why_once(self) -> None:
        gone = self._root("l2", "never-mounted")
        self._save(self._root("l1", "first"), gone, write_to="l2")

        found = library_folders.destination()

        self.assertEqual(found.reason, t("error.path.not_reachable", name=gone.name,
                                         reason=t("error.path.nothing_there")))

    def test_a_refusal_offers_the_writable_ones_instead(self) -> None:
        """What a caller needs on a refusal is not only that it failed - it is where
        this could go instead, so somebody can send it there."""
        self._save(self._root("l1", "first"), write_to="l1")

        found = library_folders.destination("missing")

        self.assertEqual([one.location_id for one in found.alternatives], ["l1"])

    def test_a_single_game_folder_cannot_hold_a_new_game(self) -> None:
        single = self._root("l2", "single", kind="game")
        self._save(self._root("l1", "first"), single, write_to="l2")

        found = library_folders.destination()

        self.assertIsNone(found.location)
        self.assertEqual(found.reason,
                         t("error.library_folders.single_game", name=single.name))

    def test_an_override_that_cannot_be_written_to_is_refused_in_its_turn(self) -> None:
        """It does not fall back either. An override is somebody naming a place."""
        self._save(self._root("l1", "first"), write_to="l1")

        found = library_folders.destination("nosuch")

        self.assertIsNone(found.location)

    def test_nowhere_writable_at_all_says_so(self) -> None:
        self._save()

        found = library_folders.destination()

        self.assertIsNone(found.location)
        self.assertEqual(found.reason, t("error.library_folders.nowhere_to_create"))


class ImportTests(_Destinations):
    def test_an_import_creates_under_the_chosen_location(self) -> None:
        """Not under the configured root. Locations replaced that key, and reading it
        meant the control on screen stored a choice nothing acted on."""
        from common.uploads.asset_import_service import _new_games_under

        self._save(self._root("l1", "first"), self._root("l2", "second"),
                   write_to="l2")

        self.assertEqual(_new_games_under(), str(self.root / "second"))

    def test_with_no_locations_it_is_refused_rather_than_guessed(self) -> None:
        from common.uploads.asset_import_service import _new_games_under

        self._save()

        with self.assertRaises(ValueError):
            _new_games_under()


if __name__ == "__main__":
    unittest.main()
