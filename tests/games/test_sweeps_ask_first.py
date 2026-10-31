"""A sweep over many games asks the disk about each folder first. A tag change refuses
whole and writes nothing; the others leave the games they cannot write to and say how
many."""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest import mock

from common import service_errors
from common.config import path_state
from common.games import (
    auto_match,
    game_repository,
    library_folders,
    library_ops,
)
from common.games.game_metadata import carrying
from common.i18n import t
from tests.support.hung_folder import never_answering
from tests.support.library import TempTree, fake_game, write_game

GOOD = "Harbor Lights (Original 2024)"
DEAD = "Sunken Reef (Original 2023)"
PLAIN = "Plain (Original 2022)"


def _info(name: str, tags: list[str]) -> dict:
    return {"Info": {"Name": name}, "User": {"Tags": tags}}


class _Sweep(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.library = self.root / "games"
        self.library.mkdir()
        self.good = write_game(self.library, GOOD, info=_info("Harbor Lights", ["old"]))
        self.dead = write_game(self.library, DEAD, info=_info("Sunken Reef", ["old"]))
        self.plain = write_game(self.library, PLAIN, info=_info("Plain", []))
        self.games = [fake_game(self.good, GOOD, meta=_info("Harbor Lights", ["old"])),
                      fake_game(self.dead, DEAD, meta=_info("Sunken Reef", ["old"])),
                      fake_game(self.plain, PLAIN, meta=_info("Plain", []))]
        self.before = {p: p.read_bytes() for p in
                       (self.good / f"{GOOD}.info", self.dead / f"{DEAD}.info")}
        store = library_folders.LocationStore(self.root / "config")
        store.save([library_folders.Location(location_id="l1", path=str(self.library))], "")
        for patcher in (
                mock.patch.object(library_folders, "get_location_store", return_value=store),
                mock.patch.object(path_state, "PROBE_SECONDS", 0.2),
                mock.patch.object(game_repository, "all_games", return_value=self.games),
                mock.patch.object(game_repository, "catalog",
                                  return_value={g.game_dir_name: g for g in self.games})):
            patcher.start()
            self.addCleanup(patcher.stop)

    def assert_unchanged(self) -> None:
        for path, was in self.before.items():
            self.assertEqual(path.read_bytes(), was, path)


class BlockedGamesTests(_Sweep):
    def test_a_folder_that_does_not_answer_is_named_with_the_share_it_hangs_off(self) -> None:
        with never_answering(str(self.dead)):
            blocked = library_folders.blocked_games(self.games)
        ((game, said),) = blocked
        self.assertIs(game, self.games[1])
        self.assertIn(library_folders.Location("l1", str(self.library)).name, said)

    def test_every_folder_that_answers_is_left_out(self) -> None:
        self.assertEqual(library_folders.blocked_games(self.games), [])

    def test_a_game_with_no_folder_is_blocked(self) -> None:
        nowhere = fake_game("", "Nowhere")
        self.assertEqual([g for g, _ in library_folders.blocked_games([nowhere])], [nowhere])


class CarryingTests(_Sweep):
    def test_only_the_games_with_the_tag_are_named(self) -> None:
        self.assertEqual(carrying(self.games, ["old"]), self.games[:2])

    def test_a_tag_on_a_table_counts(self) -> None:
        meta = {"Info": {"Name": "T"}, "tables": {"a.vpx": {"user": {"tags": ["old"]}}}}
        game = fake_game(self.plain, PLAIN, meta=meta)
        self.assertEqual(carrying([game], ["old"]), [game])


class TagSweepTests(_Sweep):
    def test_a_merge_with_one_carrier_unreachable_refuses_whole_and_writes_nothing(self) -> None:
        with never_answering(str(self.dead)), \
                self.assertRaises(service_errors.BlockedError) as caught:
            library_ops.merge_tags(["old"], "new")
        self.assertEqual(str(caught.exception),
                         t("error.tags.sweep_blocked", count=1, names=DEAD))
        self.assertEqual([one["game"] for one in caught.exception.details], [DEAD])
        self.assert_unchanged()

    def test_a_drop_refuses_the_same_way(self) -> None:
        with never_answering(str(self.dead)), \
                self.assertRaises(service_errors.BlockedError):
            library_ops.drop_tag("old")
        self.assert_unchanged()

    def test_an_unreachable_game_that_does_not_carry_the_tag_is_no_reason_to_refuse(
            self) -> None:
        with never_answering(str(self.plain)), \
                mock.patch.object(library_ops, "retag_library", return_value=2) as swept, \
                mock.patch.object(library_ops.tag_registry, "moved"):
            self.assertEqual(library_ops.merge_tags(["old"], "new"), {"changed": 2})
        swept.assert_called_once()

    def test_a_tag_merged_into_itself_changes_nothing_so_refuses_nothing(self) -> None:
        with never_answering(str(self.dead)), \
                mock.patch.object(library_ops.tag_registry, "moved"):
            self.assertEqual(library_ops.merge_tags(["old"], "old"), {"changed": 0})

    def test_a_merge_with_every_carrier_reachable_goes_ahead(self) -> None:
        with mock.patch.object(library_ops.tag_registry, "moved"):
            self.assertEqual(library_ops.merge_tags(["old"], "new"), {"changed": 2})
        self.assertIn("new", Path(self.good / f"{GOOD}.info").read_text(encoding="utf-8"))


class AutoMatchSweepTests(_Sweep):
    def test_a_game_that_cannot_be_written_to_is_left_out_and_counted(self) -> None:
        counts = {"games": 2, "changed": 0, "unmatched": 2, "yours": 0}
        with never_answering(str(self.dead)), \
                mock.patch.object(auto_match, "match_again",
                                  return_value=(counts, [])) as matching:
            said = library_ops.auto_match([GOOD, DEAD])
        self.assertEqual([g.game_dir_name for g in matching.call_args.args[0]], [GOOD])
        self.assertEqual(said["skipped"], 1)

    def test_nothing_is_skipped_when_every_folder_answers(self) -> None:
        with mock.patch.object(auto_match, "match_again",
                               return_value=({"games": 2}, [])) as matching:
            said = library_ops.auto_match([GOOD, DEAD])
        self.assertEqual(len(matching.call_args.args[0]), 2)
        self.assertEqual(said["skipped"], 0)


if __name__ == "__main__":
    unittest.main()
