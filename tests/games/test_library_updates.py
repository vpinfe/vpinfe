"""The games of the library with a later version of something they hold: what moved, what a
person's marks leave off, and what the shelf and the count are made of."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from common.games import library_updates
from common.online import sources, vps_marks, vps_updates
from common.online.sources import Source

CATALOG = {"entries": {
    "g1": {"tableFiles": [{"id": "t1", "version": "1.2"}, {"id": "t9", "version": "3.0"}],
           "b2sFiles": [{"id": "b1", "version": "2.1"}]},
    "g2": {"tableFiles": [{"id": "t2", "version": "1.0"}]}}}


def _tie(release: str, version: str) -> dict[str, str]:
    return {"vps_file_id": release, "vps_version": version}


def _table(tie: dict[str, str], **entry: Any) -> dict[str, Any]:
    return {"source": tie, **entry}


def _game(folder: str, vps_id: str, *entries: dict[str, Any], root: Path) -> SimpleNamespace:
    tables = {f"id{n}": {"id": f"id{n}", "filename": f"{folder} {n}.vpx", **one}
              for n, one in enumerate(entries)}
    return SimpleNamespace(meta_config={"Info": {"VPSId": vps_id}, "tables": tables},
                           game_dir_name=folder, full_path_game=str(root / folder))


def _ledger(root: Path, folder: str, files: dict[str, dict[str, str]], *,
            on_disk: bool = True) -> None:
    (root / folder).mkdir(exist_ok=True)
    (root / folder / f"{folder}.info").write_text(json.dumps(
        {"assets": {name: {"source": source} for name, source in files.items()}}))
    for name in files if on_disk else ():
        (root / folder / name).parent.mkdir(parents=True, exist_ok=True)
        (root / folder / name).write_bytes(b"x")


class _Library(unittest.TestCase):
    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.games: dict[str, SimpleNamespace] = {}
        self.later: dict[tuple[str, str], dict[str, Any]] = {}
        library_updates._located = None
        self.addCleanup(setattr, library_updates, "_located", None)
        for patcher in (
                patch.object(library_updates.vps_releases, "index", return_value=CATALOG),
                patch.object(library_updates.game_repository, "catalog",
                             side_effect=lambda: self.games),
                patch.object(library_updates.game_service, "find_vps_release",
                             side_effect=lambda one: {"id": one} if one else {}),
                patch.object(library_updates.game_service, "release_fits", return_value=True),
                patch.object(library_updates.game_service, "matched_vps_entry",
                             return_value={}),
                patch.object(library_updates, "game_title",
                             side_effect=lambda game: game.game_dir_name),
                patch.object(library_updates.sources, "upgrade_kinds",
                             side_effect=lambda: frozenset({"script"})),
                patch.object(library_updates.sources, "upgrade_sources", return_value=[]),
                patch.object(library_updates.sources, "upgrades",
                             side_effect=lambda who, kind, among=None: self.later.get(
                                 (who["folder"], kind), {}))):
            patcher.start()
            self.addCleanup(patcher.stop)


class FoundTests(_Library):
    def test_a_table_tied_before_its_release_went_up_a_version_is_an_update(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")), root=self.root)

        [one] = library_updates.found()

        self.assertEqual(("a", "g1", "Alien"), (one["game_id"], one["vps_id"], one["game"]))
        [move] = one["moves"]
        self.assertEqual(("vps", "tableFiles", "t1", "Alien 0.vpx", "1.0", "1.2"),
                         (move["source"], move["kind"], move["release_id"], move["file"],
                          move["then"], move["now"]))

    def test_a_table_tied_at_the_version_listed_now_is_none(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.2")), root=self.root)

        self.assertEqual([], library_updates.found())

    def test_a_tie_with_no_version_recorded_is_none(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table({"vps_file_id": "t1"}), root=self.root)

        self.assertEqual([], library_updates.found())

    def test_the_version_of_the_file_itself_is_never_the_one_compared(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.2"), version="0.1"),
                                root=self.root)

        self.assertEqual([], library_updates.found())

    def test_a_newer_file_of_the_release_under_another_name_takes_the_old_ones_place(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0"), version="0.1"),
                                _table({}, version="1.2"), root=self.root)

        self.assertEqual([], library_updates.found())

    def test_a_newer_file_tied_to_the_release_takes_the_old_ones_place(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0"), version="0.1"),
                                _table(_tie("t1", "1.2")), root=self.root)

        self.assertEqual([], library_updates.found())

    def test_a_file_below_the_listed_version_leaves_the_move(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")),
                                _table({}, version="1.1"), root=self.root)

        [one] = library_updates.found()

        [move] = one["moves"]
        self.assertEqual("Alien 0.vpx", move["file"])

    def test_a_file_that_is_not_the_release_does_not_take_the_place(self) -> None:
        for other in (_table(_tie("t9", "3.0"), version="1.2"),
                      _table({"vps_file_id": "", "confirmed_by": "user"}, version="1.2"),
                      _table({"patch": "applied"}, version="1.2")):
            with self.subTest(other=other["source"]):
                self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")), other,
                                        root=self.root)

                self.assertEqual(1, len(library_updates.found()))

    def test_an_absent_file_does_not_take_the_place(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")),
                                _table({}, version="1.2", absent_since="x"), root=self.root)

        self.assertEqual(1, len(library_updates.found()))

    def test_a_table_removed_from_the_folder_is_none(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0"), absent_since="x"),
                                root=self.root)

        self.assertEqual([], library_updates.found())

    def test_a_build_the_table_cannot_be_is_none(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")), root=self.root)

        with patch.object(library_updates.game_service, "release_fits", return_value=False):
            self.assertEqual([], library_updates.found())

    def test_a_file_in_the_ledger_tied_to_a_release_that_went_up_is_an_update(self) -> None:
        self.games["a"] = _game("Alien", "g1", root=self.root)
        _ledger(self.root, "Alien", {"backglass/Alien.directb2s": _tie("b1", "2.0")})

        [one] = library_updates.found()

        [move] = one["moves"]
        self.assertEqual(("b2sFiles", "b1", "Alien.directb2s", "2.0", "2.1"),
                         (move["kind"], move["release_id"], move["file"], move["then"],
                          move["now"]))

    def test_a_game_with_several_moves_is_one_entry(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")), root=self.root)
        _ledger(self.root, "Alien", {"Alien.directb2s": _tie("b1", "2.0")})

        [one] = library_updates.found()

        self.assertEqual(["t1", "b1"], [move["release_id"] for move in one["moves"]])

    def test_a_file_tied_in_a_game_matched_to_no_entry_is_still_an_update(self) -> None:
        self.games["a"] = _game("Alien", "", root=self.root)
        _ledger(self.root, "Alien", {"Alien.directb2s": _tie("b1", "2.0")})

        [one] = library_updates.found()

        self.assertEqual(("g1", ["b1"]), (one["vps_id"], [m["release_id"] for m in one["moves"]]))

    def test_a_file_deleted_from_the_folder_leaves_no_update(self) -> None:
        self.games["a"] = _game("Alien", "g1", root=self.root)
        _ledger(self.root, "Alien", {"backglass/Alien.directb2s": _tie("b1", "2.0")},
                on_disk=False)

        self.assertEqual([], library_updates.found())

    def test_tying_the_release_again_clears_the_update(self) -> None:
        tie = _tie("t1", "1.0")
        self.games["a"] = _game("Alien", "g1", _table(tie), root=self.root)
        self.assertEqual(1, len(library_updates.found()))

        library_updates.ties.tie(tie, "t1", "user", {"id": "t1", "version": "1.2"})

        self.assertEqual([], library_updates.found())

    def test_two_tables_tied_to_one_release_are_one_move(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")),
                                _table(_tie("t1", "1.0")), root=self.root)

        [one] = library_updates.found()

        self.assertEqual(["t1"], [move["release_id"] for move in one["moves"]])

    def test_tables_tied_to_one_release_at_different_versions_are_two_moves(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")),
                                _table(_tie("t1", "1.1")), root=self.root)

        [one] = library_updates.found()

        self.assertEqual(["1.0", "1.1"], [move["then"] for move in one["moves"]])

    def test_a_table_and_a_file_tied_to_the_same_release_are_one_move(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("b1", "2.0")), root=self.root)
        _ledger(self.root, "Alien", {"Alien.directb2s": _tie("b1", "2.0")})

        [one] = library_updates.found()

        self.assertEqual(["b1"], [move["release_id"] for move in one["moves"]])

    def test_versions_are_compared_as_numbers(self) -> None:
        CATALOG["entries"]["g2"]["tableFiles"][0]["version"] = "1.10"
        self.addCleanup(CATALOG["entries"]["g2"]["tableFiles"][0].update, version="1.0")
        for then, now, update in (("1.9", "1.10", True), ("1.10", "1.9", False),
                                  ("1.0.0", "1", False), ("1", "1.0.0", False),
                                  ("2.0", "1", False)):
            with self.subTest(then=then, now=now):
                CATALOG["entries"]["g2"]["tableFiles"][0]["version"] = now
                library_updates._located = None
                self.games["a"] = _game("Alien", "g2", _table(_tie("t2", then)), root=self.root)

                self.assertEqual(update, bool(library_updates.found()))

    def test_a_version_that_does_not_read_as_a_number_is_none(self) -> None:
        for then, now in (("FizX3.3V1", "1.10"), ("1.0.1", "WIP")):
            with self.subTest(then=then, now=now):
                CATALOG["entries"]["g2"]["tableFiles"][0]["version"] = now
                self.addCleanup(CATALOG["entries"]["g2"]["tableFiles"][0].update, version="1.0")
                library_updates._located = None
                self.games["a"] = _game("Alien", "g2", _table(_tie("t2", then)), root=self.root)

                self.assertEqual([], library_updates.found())

    def test_a_source_that_says_a_fix_exists_is_a_move_with_the_folder_in_file(self) -> None:
        self.games["a"] = _game("Alien Folder", "g1", root=self.root)
        self.later[("Alien Folder", "script")] = {"folder": "Alien Folder"}

        [one] = library_updates.found()

        [move] = one["moves"]
        self.assertEqual(("script", "", "Alien Folder"),
                         (move["kind"], move["release_id"], move["file"]))

    def test_a_replaced_picture_is_a_move_with_the_slot_and_the_hash_it_is_offered_as(self) -> None:
        self.games["a"] = _game("Alien Folder", "g1", root=self.root)
        self.later[("Alien Folder", "wheel")] = {
            "vpinmediadb": {"source": "vpinmediadb", "size": "9", "md5": "abc"}}

        with patch.object(library_updates.sources, "upgrade_kinds",
                          return_value=frozenset({"wheel"})):
            [one] = library_updates.found()

        [move] = one["moves"]
        self.assertEqual(("vpinmediadb", "wheel", "", "g1", "", "abc"),
                         (move["source"], move["kind"], move["release_id"], move["vps_id"],
                          move["now"], move["md5"]))

    def test_the_sources_are_resolved_once_for_the_whole_library_and_not_for_each_game(
            self) -> None:
        for n in range(5):
            self.games[f"g{n}"] = _game(f"Game {n}", f"v{n}", root=self.root)
        resolved = [Source(id="s", name="S", upgrades=lambda who, kind: {})]

        with patch.object(library_updates.sources, "upgrade_sources",
                          return_value=resolved) as asked, \
                patch.object(library_updates.sources, "upgrades",
                             return_value={}) as upgrades:
            library_updates.found()

        asked.assert_called_once_with()
        self.assertTrue(upgrades.call_args_list)
        self.assertTrue(all(call.args[2] is resolved for call in upgrades.call_args_list))

    def test_a_game_that_cannot_be_read_costs_its_own_tile_only(self) -> None:
        self.games["a"] = _game("Alien", "g1", _table(_tie("t1", "1.0")), root=self.root)
        self.games["b"] = SimpleNamespace(meta_config=None, game_dir_name=None,
                                          full_path_game=None)

        with patch.object(library_updates, "of_game",
                          side_effect=[RuntimeError("unreadable"), None]):
            self.assertEqual([], library_updates.found())

        self.assertEqual(["a"], [one["game_id"] for one in library_updates.found()])


class OneGameTests(unittest.TestCase):
    """One game asked about reads only that game, and a pass over the library reads each."""

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        later = Source(id="stand-in", name="Stand-in", kinds=frozenset({"wheel"}),
                       upgrades=lambda who, kind: (
                           {"folder": who["folder"]} if who["folder"] in ("Game 1", "Game 3")
                           else {}))
        self.games = {f"g{n}": SimpleNamespace(meta_config={}, game_dir_name=f"Game {n}",
                                               full_path_game=str(self.root / f"Game {n}"))
                      for n in range(5)}
        library_updates._located = None
        self.addCleanup(setattr, library_updates, "_located", None)
        for patcher in (
                patch.object(library_updates.sources, "upgrade_sources",
                             return_value=[later]),
                patch.object(library_updates.vps_releases, "index",
                             return_value={"entries": {}}),
                patch.object(library_updates.game_repository, "catalog",
                             return_value=self.games),
                patch.object(library_updates.game_service, "matched_vps_entry",
                             return_value={}),
                patch.object(library_updates, "game_title",
                             side_effect=lambda game: game.game_dir_name)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def test_a_pass_over_the_library_finds_each_game_the_source_names(self) -> None:
        self.assertEqual(["g1", "g3"], [one["game_id"] for one in library_updates.found()])

    def test_one_game_asked_about_looks_at_that_game_alone(self) -> None:
        with patch.object(library_updates, "of_game",
                          wraps=library_updates.of_game) as looked:
            found = library_updates.found_for("g3")

        self.assertEqual(["g3"], [one["game_id"] for one in found])
        self.assertEqual(["g3"], [call.args[1] for call in looked.call_args_list])

    def test_a_game_the_library_does_not_hold_has_none(self) -> None:
        self.assertEqual([], library_updates.found_for("nothing"))


class ShelfTests(unittest.TestCase):
    def move(self, release: str = "t1", *, now: str = "1.2", vps_id: str = "g1") -> dict:
        return {"source": "vps", "kind": "tableFiles", "release_id": release,
                "vps_id": vps_id, "file": "x.vpx", "then": "1.0", "now": now}

    def untied(self) -> dict:
        return {"source": "vps", "kind": "b2sFiles", "release_id": "", "vps_id": "",
                "file": "Folder", "then": "", "now": ""}

    def one(self, game_id: str, name: str, *moves: dict, vps_id: str = "g1") -> dict:
        return {"game_id": game_id, "vps_id": vps_id, "game": name, "img_url": "",
                "moves": list(moves)}

    def marks(self, *held: tuple[str, str, str, str]) -> vps_marks.Marks:
        return vps_marks.Marks([{"state": state, "scope": scope, "id": one, "detail": detail,
                                 "since": "x"} for state, scope, one, detail in held])

    def test_the_games_are_by_name_and_counted_once_each(self) -> None:
        found = [self.one("b", "beta", self.move(), self.move("t2")),
                 self.one("a", "Alpha", self.move("t3"))]

        shelf = vps_updates.shelf(found, vps_marks.Marks())

        self.assertEqual((2, ["a", "b"]), (shelf["count"], shelf["game_ids"]))

    def test_the_shelf_is_cut_but_the_count_and_the_ids_are_not(self) -> None:
        found = [self.one(f"g{n:02d}", f"Game {n:02d}", self.move()) for n in range(20)]

        shelf = vps_updates.shelf(found, vps_marks.Marks(), cut=vps_updates.SHELF_SHOWN)

        self.assertEqual(vps_updates.SHELF_SHOWN, len(shelf["games"]))
        self.assertEqual((20, 20), (shelf["count"], len(shelf["game_ids"])))

    def test_a_skipped_version_leaves_the_move_and_a_later_one_returns(self) -> None:
        found = [self.one("a", "Alien", self.move())]
        skipped = self.marks((vps_marks.IGNORED, vps_marks.VERSION, "t1", "1.2"))

        self.assertEqual(0, vps_updates.shelf(found, skipped)["count"])
        later = [self.one("a", "Alien", self.move(now="1.3"))]
        self.assertEqual(1, vps_updates.shelf(later, skipped)["count"])

    def test_ignored_updates_leave_every_later_version_of_the_release(self) -> None:
        ignored = self.marks((vps_marks.IGNORED, vps_marks.UPDATES, "t1", ""))

        for now in ("1.2", "1.3", "9.0"):
            with self.subTest(now=now):
                self.assertEqual(
                    0, vps_updates.shelf([self.one("a", "Alien", self.move(now=now))],
                                         ignored)["count"])

    def test_ignoring_updates_to_one_release_leaves_the_games_other_moves(self) -> None:
        found = [self.one("a", "Alien", self.move(), self.move("b1"))]
        ignored = self.marks((vps_marks.IGNORED, vps_marks.UPDATES, "t1", ""))

        [one] = vps_updates.games(found, ignored)

        self.assertEqual(["b1"], [move["release_id"] for move in one["moves"]])

    def test_an_ignored_release_or_game_leaves_the_move(self) -> None:
        found = [self.one("a", "Alien", self.move())]

        for held in ((vps_marks.IGNORED, vps_marks.RELEASE, "t1", ""),
                     (vps_marks.IGNORED, vps_marks.GAME, "g1", "")):
            with self.subTest(held=held):
                self.assertEqual(0, vps_updates.shelf(found, self.marks(held))["count"])

    def test_a_skipped_or_ignored_move_is_kept_and_labelled_when_asked_for(self) -> None:
        found = [self.one("a", "Alien", self.move(), self.move("b1"))]
        held = (self.marks((vps_marks.IGNORED, vps_marks.VERSION, "t1", "1.2")),
                self.marks((vps_marks.IGNORED, vps_marks.UPDATES, "t1", "")))

        for marks, label in zip(held, (vps_updates.SKIPPED, vps_updates.IGNORED), strict=True):
            with self.subTest(label=label):
                [one] = vps_updates.games(found, marks, with_set_aside=True)
                self.assertEqual({"t1": label, "b1": ""},
                                 {move["release_id"]: move["set_aside"] for move in one["moves"]})

    def test_a_released_or_game_ignored_move_is_gone_even_when_set_aside_is_asked_for(self) -> None:
        found = [self.one("a", "Alien", self.move())]

        for held in ((vps_marks.IGNORED, vps_marks.RELEASE, "t1", ""),
                     (vps_marks.IGNORED, vps_marks.GAME, "g1", "")):
            with self.subTest(held=held):
                self.assertEqual([], vps_updates.games(found, self.marks(held),
                                                       with_set_aside=True))

    def test_the_moves_are_not_labelled_unless_asked_for(self) -> None:
        [one] = vps_updates.games([self.one("a", "Alien", self.move())], vps_marks.Marks())

        self.assertNotIn("set_aside", one["moves"][0])

    def test_a_wanted_release_is_still_an_update(self) -> None:
        found = [self.one("a", "Alien", self.move())]

        self.assertEqual(1, vps_updates.shelf(
            found, self.marks((vps_marks.WANTED, vps_marks.RELEASE, "t1", "")))["count"])

    def test_a_move_with_no_release_is_left_only_by_ignoring_its_game(self) -> None:
        found = [self.one("a", "Alien", self.untied())]

        self.assertEqual(1, vps_updates.shelf(
            found, self.marks((vps_marks.IGNORED, vps_marks.UPDATES, "t1", "")))["count"])
        self.assertEqual(0, vps_updates.shelf(
            found, self.marks((vps_marks.IGNORED, vps_marks.GAME, "g1", "")))["count"])

    def test_a_move_for_a_game_matched_to_nothing_stays_as_nothing_can_ignore_it(self) -> None:
        found = [self.one("a", "Alien", self.untied(), vps_id="")]

        self.assertEqual(1, vps_updates.shelf(found, vps_marks.Marks())["count"])

    def picture(self, kind: str = "wheel", md5: str = "abc") -> dict:
        return {"source": "vpinmediadb", "kind": kind, "release_id": "", "vps_id": "g1",
                "file": "Folder", "then": "", "now": "", "md5": md5}

    def test_a_picture_shows_until_it_is_skipped_or_its_slot_ignored(self) -> None:
        found = [self.one("a", "Alien", self.picture())]

        self.assertEqual(1, vps_updates.shelf(found, vps_marks.Marks())["count"])
        for held in ((vps_marks.IGNORED, vps_marks.VERSION, "g1:wheel", "abc"),
                     (vps_marks.IGNORED, vps_marks.UPDATES, "g1:wheel", ""),
                     (vps_marks.IGNORED, vps_marks.GAME, "g1", "")):
            with self.subTest(held=held):
                self.assertEqual(0, vps_updates.shelf(found, self.marks(held))["count"])

    def test_a_newer_picture_returns_after_the_one_skipped(self) -> None:
        skipped = self.marks((vps_marks.IGNORED, vps_marks.VERSION, "g1:wheel", "abc"))

        self.assertEqual(1, vps_updates.shelf(
            [self.one("a", "Alien", self.picture(md5="def"))], skipped)["count"])

    def test_skipping_one_slot_leaves_the_others(self) -> None:
        found = [self.one("a", "Alien", self.picture(), self.picture("backglass"))]
        skipped = self.marks((vps_marks.IGNORED, vps_marks.UPDATES, "g1:wheel", ""))

        [one] = vps_updates.games(found, skipped)

        self.assertEqual(["backglass"], [move["kind"] for move in one["moves"]])


class UpgradeKindsTests(unittest.TestCase):
    def source(self, **more: Any) -> Source:
        return Source(id="s", name="S", upgrades=lambda who, kind: {"later": "9"}, **more)

    def run_with(self, source: Source, kind: str) -> dict[str, Any]:
        with patch.object(sources, "upgrade_sources", return_value=[source]):
            return sources.upgrades({"folder": "x"}, kind)

    def test_a_source_is_asked_the_kinds_it_declares_for_upgrades(self) -> None:
        source = self.source(upgrades_for=frozenset({"script"}))

        self.assertEqual({"s": {"later": "9"}}, self.run_with(source, "script"))
        self.assertEqual({}, self.run_with(source, "tableFiles"))

    def test_a_source_that_declares_none_is_asked_the_kinds_it_serves(self) -> None:
        source = self.source(kinds=frozenset({"tableFiles"}))

        self.assertEqual({"s": {"later": "9"}}, self.run_with(source, "tableFiles"))
        self.assertEqual({}, self.run_with(source, "b2sFiles"))

    def test_the_kinds_to_ask_are_what_the_enabled_sources_declare(self) -> None:
        mixed = [self.source(upgrades_for=frozenset({"script"})),
                 self.source(kinds=frozenset({"tableFiles"}))]

        with patch.object(sources, "upgrade_sources", return_value=mixed):
            self.assertEqual(frozenset({"script", "tableFiles"}), sources.upgrade_kinds())


if __name__ == "__main__":
    unittest.main()
