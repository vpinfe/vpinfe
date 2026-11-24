"""Protects: (b) the play numbers a theme reads follow the setting and never blend two sources."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from apps.vpx.plays import VPXPlays
from common import apps
from common.games import collection_resolver, entry_lens, launchers, play_numbers
from common.games.collection_resolver import Entry
from common.games.game_metadata import default_table_entry, play_record
from common.games.game_parser import GameParser
from common.games.launchers import Launcher, LauncherStore
from common.games.play_numbers import PlayNumbers
from common.games.tables import table_entries
from frontend import game_state
from tests.support.library import TempTree, game_info, write_game

FOLDER = "Cactus"
SHARED = "Cactus.vpx"
VR = "Cactus VR.vpx"
FLAT = "Flat.vpx"
OTHER = "Other"
LAUNCHER_LAST = "2024-01-02T03:04:05+00:00"
LAUNCHER_SHOWN = "2024-01-02T03:04:05Z"


def _record() -> dict:
    return game_info(
        "Cactus", game_id="Plays00001",
        User={"StartCount": 40, "LastRun": 1700000000, "RunTime": 60},
        tables={"a": {"id": "a", "filename": SHARED,
                      "user": {"start_count": 5, "last_run": 1700000100,
                               "run_time_seconds": 120}},
                "b": {"id": "b", "filename": VR,
                      "user": {"start_count": 2, "last_run": 1700000200,
                               "run_time_seconds": 30}}})


def _vpx(**numbers: object) -> bytes:
    return json.dumps({"VPX": numbers}).encode()


def _shown(plays: PlayNumbers, meta: dict, folder: Path, table: dict | None = None) -> dict:
    found = plays.counters(meta, str(folder), table)
    assert found is not None
    return found


class _Case(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=_record(), files={
            VR: b"vr",
            "Cactus VR.vpxinfo": b"",  # not a companion: nothing may read it
        })
        (self.game,) = GameParser(str(self.root)).get_all_games()
        self.meta = self.game.meta_config or {}
        self.tables = dict(table_entries(self.meta))


class SettingTests(_Case):
    def test_the_whole_game_answers_for_nothing(self) -> None:
        self.assertIsNone(PlayNumbers("game").counters(self.meta, str(self.folder)))

    def test_the_default_leaves_the_user_fields_as_they_were(self) -> None:
        self.assertEqual("game", play_numbers.current_mode())
        wire = game_state._entry_row(Entry(self.game, self.tables["b"], 2), {})
        self.assertEqual(play_record(self.meta), wire["game"]["user"])

    def test_the_table_that_plays_answers_with_its_own_numbers(self) -> None:
        shown = _shown(PlayNumbers("table"), self.meta, self.folder, self.tables["b"])

        self.assertEqual(2, shown["play_count"])
        self.assertEqual(30, shown["play_time_seconds"])
        self.assertEqual("2023-11-14T22:16:40Z", shown["last_played"])

    def test_a_game_is_its_default_table(self) -> None:
        default = _shown(PlayNumbers("table"), self.meta, self.folder)
        named = _shown(PlayNumbers("table"), self.meta, self.folder, self.tables["a"])

        chosen = default_table_entry(self.meta)["id"]
        named = _shown(PlayNumbers("table"), self.meta, self.folder, self.tables[chosen])

        self.assertEqual(named, default)

    def test_a_tables_last_run_is_listed_as_a_stamp_not_an_epoch(self) -> None:
        wire = entry_lens.entry_resource(Entry(self.game, self.tables["b"], 2))

        self.assertEqual("2023-11-14T22:16:40Z", wire["table"]["user"]["last_played"])

    def test_rating_stays_the_games(self) -> None:
        with mock.patch.object(play_numbers, "current_mode", return_value="table"):
            wire = game_state._entry_row(Entry(self.game, self.tables["b"], 2), {})

        self.assertEqual(2, wire["game"]["user"]["play_count"])
        self.assertEqual(play_record(self.meta)["rating"], wire["game"]["user"]["rating"])
        self.assertEqual(play_record(self.meta)["tags"], wire["game"]["user"]["tags"])


class LauncherTests(_Case):
    def setUp(self) -> None:
        super().setUp()
        store = LauncherStore(self.root / "launchers")
        store.put(Launcher("l1", app="vpx", display_name="One"))
        patch = mock.patch.object(launchers, "get_launcher_store", return_value=store)
        patch.start()
        self.addCleanup(patch.stop)

    def _vr_info(self, **numbers: object) -> None:
        (self.folder / "Cactus VR.info").write_bytes(_vpx(**numbers))

    def test_the_program_counts_where_it_keeps_a_count(self) -> None:
        self._vr_info(RunCount=9, RunTime=600, LastRun=LAUNCHER_LAST)

        shown = _shown(PlayNumbers("launcher"), self.meta, self.folder, self.tables["b"])

        self.assertEqual({"last_played": LAUNCHER_SHOWN, "play_count": 9,
                          "play_time_seconds": 600}, shown)

    def test_a_table_the_program_never_ran_falls_back_to_vpinfes_number(self) -> None:
        shown = _shown(PlayNumbers("launcher"), self.meta, self.folder, self.tables["b"])

        self.assertEqual(2, shown["play_count"])
        self.assertEqual(30, shown["play_time_seconds"])

    def test_the_program_count_is_never_added_to_vpinfes(self) -> None:
        self._vr_info(RunCount=9)

        shown = _shown(PlayNumbers("launcher"), self.meta, self.folder, self.tables["b"])

        self.assertEqual(9, shown["play_count"])

    def test_a_sort_by_plays_reads_each_entrys_own_table(self) -> None:
        a, b = Entry(self.game, self.tables["a"], 2), Entry(self.game, self.tables["b"], 2)
        for mode, expected in (("game", ["a", "b"]), ("table", ["a", "b"])):
            with self.subTest(mode=mode), \
                    mock.patch.object(play_numbers, "current_mode", return_value=mode):
                ordered = collection_resolver._ordered([b, a], "play_count", descending=True)
                self.assertEqual(expected, [e.table_id for e in ordered])

        self._vr_info(RunCount=99)
        with mock.patch.object(play_numbers, "current_mode", return_value="launcher"):
            ordered = collection_resolver._ordered([a, b], "play_count", descending=True)
        self.assertEqual(["b", "a"], [e.table_id for e in ordered])


class VPXReaderTests(TempTree):
    def _entry(self, folder: Path, table: str) -> apps.Entry:
        return apps.Entry(game_dir=str(folder), table=str(folder / table))

    def _folder(self, name: str, files: dict[str, bytes]) -> Path:
        return write_game(self.root, name, vpx=False, files=files)

    def test_a_table_reads_its_own_file(self) -> None:
        folder = self._folder("One", {"One.vpx": b"", "One.info": _vpx(
            RunCount=3, RunTime=90, LastRun=LAUNCHER_LAST)})

        found = VPXPlays().record(self._entry(folder, "One.vpx"), {})

        assert found is not None
        self.assertEqual((3, 90, LAUNCHER_LAST), (found.count, found.seconds, found.last))

    def test_the_folders_file_answers_for_a_folder_with_one_table(self) -> None:
        folder = self._folder("Solo", {"Flat.vpx": b"", "Solo.info": _vpx(RunCount=4)})

        found = VPXPlays().record(self._entry(folder, "Flat.vpx"), {})

        assert found is not None
        self.assertEqual(4, found.count)

    def test_the_folders_file_is_not_one_tables_count_when_there_are_two(self) -> None:
        folder = self._folder("Pair", {"A.vpx": b"", "B.vpx": b"", "Pair.info": _vpx(RunCount=8)})

        self.assertIsNone(VPXPlays().record(self._entry(folder, "A.vpx"), {}))

    def test_a_file_without_a_vpx_section_has_no_count(self) -> None:
        folder = self._folder("Bare", {"Bare.vpx": b"", "Bare.info": b"{}"})

        self.assertIsNone(VPXPlays().record(self._entry(folder, "Bare.vpx"), {}))

    def test_the_older_spelling_of_the_count_is_read_too(self) -> None:
        folder = self._folder("Old", {"Old.vpx": b"", "Old.info": _vpx(StartCount=6)})

        found = VPXPlays().record(self._entry(folder, "Old.vpx"), {})

        assert found is not None
        self.assertEqual(6, found.count)

    def test_a_changed_file_is_read_again(self) -> None:
        folder = self._folder("Moving", {"Moving.vpx": b"", "Moving.info": _vpx(RunCount=1)})
        entry = self._entry(folder, "Moving.vpx")
        VPXPlays().record(entry, {})

        (folder / "Moving.info").write_bytes(_vpx(RunCount=22, RunTime=5))
        found = VPXPlays().record(entry, {})

        assert found is not None
        self.assertEqual(22, found.count)

    def test_a_table_named_like_its_folder_is_not_given_the_folders_count(self) -> None:
        folder = self._folder(
            "Pair", {"Pair.vpx": b"", "B.vpx": b"", "Pair.info": _vpx(RunCount=8)})

        self.assertIsNone(VPXPlays().record(self._entry(folder, "Pair.vpx"), {}))
        self.assertIsNone(VPXPlays().record(self._entry(folder, "B.vpx"), {}))

    def test_a_table_named_like_its_folder_answers_when_it_is_the_only_one(self) -> None:
        folder = self._folder("Lone", {"Lone.vpx": b"", "Lone.info": _vpx(RunCount=7)})

        found = VPXPlays().record(self._entry(folder, "Lone.vpx"), {})

        assert found is not None
        self.assertEqual(7, found.count)

    def test_a_table_kept_elsewhere_reads_beside_itself_not_beside_the_game(self) -> None:
        game = self._folder("Home", {"Home.vpx": b"", "Home.info": _vpx(RunCount=50)})
        away = self._folder("Away", {"Table.vpx": b"", "Table.info": _vpx(RunCount=3)})
        entry = apps.Entry(game_dir=str(game), table=str(away / "Table.vpx"))

        found = VPXPlays().record(entry, {})

        assert found is not None
        self.assertEqual(3, found.count)

    def test_a_table_kept_elsewhere_with_no_file_of_its_own_is_not_the_games(self) -> None:
        game = self._folder("Home", {"Home.vpx": b"", "Home.info": _vpx(RunCount=50)})
        away = self._folder("Away", {"Table.vpx": b""})
        entry = apps.Entry(game_dir=str(game), table=str(away / "Table.vpx"))

        self.assertIsNone(VPXPlays().record(entry, {}))

    def test_a_count_too_large_to_hold_reads_as_zero_rather_than_failing(self) -> None:
        huge = b'{"VPX": {"RunCount": 1e999}}'
        folder = self._folder("Huge", {"Huge.vpx": b"", "Huge.info": huge})

        found = VPXPlays().record(self._entry(folder, "Huge.vpx"), {})

        assert found is not None
        self.assertEqual(0, found.count)


class AppliesAtNextOpenTests(_Case):
    def test_a_view_built_under_another_answer_is_derived_again(self) -> None:
        with mock.patch.object(play_numbers, "current_mode", return_value="game"):
            PlayNumbers()
            self.assertFalse(play_numbers.changed_since_built())
        with mock.patch.object(play_numbers, "current_mode", return_value="table"):
            self.assertTrue(play_numbers.changed_since_built())
            PlayNumbers()
            self.assertFalse(play_numbers.changed_since_built())
