"""Two game folders holding one id, and who answers for it.

A folder carries its id, so this means the same game is in the library twice - a copy,
or one tree reached through two configured locations. The old answer was to re-mint the
loser, which is a write to somebody's file to settle something only they can decide: it
makes the copy a different game for good, and anything that named it goes with it.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

from common.games import game_identity


def _game(game_id: str, path: str, location: str):
    return types.SimpleNamespace(
        full_path_game=path,
        game_dir_name=path.rsplit("/", 1)[-1],
        location_id=location,
        meta_config={"vpinfe": {"game_id": game_id}} if game_id else {},
    )


class PriorityTests(unittest.TestCase):
    def test_the_earlier_location_answers_for_the_id(self) -> None:
        first = _game("gid0000001", "/tables/AFM", "loc1")
        second = _game("gid0000001", "/backup/AFM", "loc2")

        found = game_identity.resolve_ids([second, first], order=["loc1", "loc2"])

        self.assertIs(found.by_id["gid0000001"], first)
        self.assertEqual([one.path for one in found.shadowed], ["/backup/AFM"])

    def test_reordering_the_locations_changes_which_one(self) -> None:
        """Which is the whole point of the list being a priority: somebody who put the
        share above the local copy gets the share."""
        first = _game("gid0000001", "/tables/AFM", "loc1")
        second = _game("gid0000001", "/backup/AFM", "loc2")

        found = game_identity.resolve_ids([first, second], order=["loc2", "loc1"])

        self.assertIs(found.by_id["gid0000001"], second)
        self.assertEqual([one.path for one in found.shadowed], ["/tables/AFM"])

    def test_two_in_one_location_go_by_path(self) -> None:
        """No priority to appeal to, so the tie is broken the one way that answers the
        same on every run. Directory order does not."""
        later = _game("gid0000001", "/tables/b-copy", "loc1")
        earlier = _game("gid0000001", "/tables/a-copy", "loc1")

        found = game_identity.resolve_ids([later, earlier], order=["loc1"])

        self.assertIs(found.by_id["gid0000001"], earlier)

    def test_a_location_nobody_ranked_goes_last(self) -> None:
        """It is not part of the arrangement somebody made, so it cannot outrank it."""
        ranked = _game("gid0000001", "/tables/AFM", "loc1")
        stray = _game("gid0000001", "/elsewhere/AFM", "unknown")

        found = game_identity.resolve_ids([stray, ranked], order=["loc1"])

        self.assertIs(found.by_id["gid0000001"], ranked)


class NothingIsWrittenTests(unittest.TestCase):
    def test_a_clash_never_rewrites_a_record(self) -> None:
        """The whole reason this changed. Re-minting settles it by editing a file, and
        which folder should keep the id is not ours to decide."""
        first = _game("gid0000001", "/tables/AFM", "loc1")
        second = _game("gid0000001", "/backup/AFM", "loc2")

        with mock.patch.object(game_identity, "ensure_id") as wrote:
            game_identity.resolve_ids([first, second], order=["loc1", "loc2"])

        wrote.assert_not_called()

    def test_but_a_folder_with_no_id_still_gets_one(self) -> None:
        """Not a clash, and a game with no id cannot be addressed at all."""
        fresh = _game("", "/tables/New", "loc1")

        with mock.patch.object(game_identity, "keep_game_meta") as wrote:
            found = game_identity.resolve_ids([fresh], order=["loc1"])

        wrote.assert_called_once()
        written = wrote.call_args.args[1]["vpinfe"]["game_id"]
        self.assertEqual(list(found.by_id), [written])


class ReportTests(unittest.TestCase):
    def test_a_shadowed_folder_says_what_is_answering_instead(self) -> None:
        """So the report can name both sides rather than only the loser."""
        first = _game("gid0000001", "/tables/AFM", "loc1")
        second = _game("gid0000001", "/backup/AFM", "loc2")

        one = game_identity.resolve_ids([first, second],
                                        order=["loc1", "loc2"]).shadowed[0]

        self.assertEqual(one.used_path, "/tables/AFM")
        self.assertEqual(one.used_location_id, "loc1")
        self.assertEqual(one.location_id, "loc2")

    def test_a_location_can_be_asked_what_of_its_own_is_shadowed(self) -> None:
        """Which is the count its row carries - the synced-library case, where the
        answer is four hundred and that is the finding."""
        games = [_game("gid0000001", "/tables/AFM", "loc1"),
                 _game("gid0000002", "/tables/MM", "loc1"),
                 _game("gid0000001", "/backup/AFM", "loc2"),
                 _game("gid0000002", "/backup/MM", "loc2")]

        found = game_identity.resolve_ids(games, order=["loc1", "loc2"])

        self.assertEqual(len(found.under("loc2")), 2)
        self.assertEqual(found.under("loc1"), ())

    def test_a_library_with_no_clash_reports_none(self) -> None:
        games = [_game("gid0000001", "/tables/AFM", "loc1"),
                 _game("gid0000002", "/tables/MM", "loc1")]

        self.assertEqual(game_identity.resolve_ids(games, order=["loc1"]).shadowed, ())


if __name__ == "__main__":
    unittest.main()
