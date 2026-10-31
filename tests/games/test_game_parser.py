"""Reading a library folder: what the parser hands back, and what it notices on disk."""

from __future__ import annotations

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from common.games.game_parser import GameParser
from common.games.game_repository import game_to_row
from common.i18n import t
from tests.support.library import TempTree, write_game


class MediaSettingsTests(unittest.TestCase):
    """The games resolve their media under the settings the parser last took."""

    def _parser(self, variant: str = "", sets: dict | None = None) -> GameParser:
        parser = GameParser.__new__(GameParser)
        parser.playfieldvariant = variant
        parser.active_sets = sets or {}
        return parser

    def _read(self, parser: GameParser, *, variant: str, wheelset: str) -> bool:
        with mock.patch("common.games.game_parser.MediaConfig.from_config",
                        return_value=SimpleNamespace(playfield_variant=variant,
                                                     wheelset=wheelset)), \
             mock.patch("common.games.game_parser.active_set_for",
                        side_effect=lambda _kind, configured: configured):
            return parser.read_media_settings(mock.Mock())

    def test_a_different_wheel_set_is_a_change(self) -> None:
        parser = self._parser()
        self.assertTrue(self._read(parser, variant="", wheelset="Neon"))
        self.assertEqual(parser.active_sets, {"wheel": "Neon"})

    def test_the_same_settings_are_not(self) -> None:
        parser = self._parser("night", {"wheel": "Neon"})
        self.assertFalse(self._read(parser, variant="night", wheelset="Neon"))

    def test_a_different_playfield_variant_is_a_change(self) -> None:
        parser = self._parser("")
        self.assertTrue(self._read(parser, variant="night", wheelset=""))
        self.assertEqual(parser.playfieldvariant, "night")

    def test_dropping_the_wheel_set_is_a_change(self) -> None:
        parser = self._parser("", {"wheel": "Neon"})
        self.assertTrue(self._read(parser, variant="", wheelset=""))
        self.assertEqual(parser.active_sets, {})


class GameParserTests(unittest.TestCase):
    def test_game_parser_accessors_return_copies(self) -> None:
        parser = GameParser.__new__(GameParser)
        parser.games = [SimpleNamespace(name="one")]  # type: ignore[list-item]
        parser.missing_games = [{"folder": "missing"}]

        games = parser.get_all_games()
        missing = parser.get_missing_games()
        games.clear()
        missing[0]["folder"] = "changed"

        self.assertEqual(len(parser.games), 1)
        self.assertEqual(parser.missing_games[0]["folder"], "missing")

    def test_tableparser_detects_directb2s_case_insensitively(self) -> None:
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            with_b2s = root / "With B2S (Bally 1990)"
            with_b2s.mkdir()
            (with_b2s / "With B2S (Bally 1990).vpx").write_text("")
            (with_b2s / "With B2S (Bally 1990).DirectB2S").write_text("")

            without_b2s = root / "No B2S (Bally 1991)"
            without_b2s.mkdir()
            (without_b2s / "No B2S (Bally 1991).vpx").write_text("")

            parser = GameParser(root)
            by_name = {t.game_dir_name: t for t in parser.get_all_games()}

            self.assertTrue(by_name["With B2S (Bally 1990)"].b2s_exists)
            self.assertFalse(by_name["No B2S (Bally 1991)"].b2s_exists)

            # game_to_row mirrors the flag for the UI
            self.assertTrue(game_to_row(by_name["With B2S (Bally 1990)"])["b2s_exists"])
            self.assertFalse(game_to_row(by_name["No B2S (Bally 1991)"])["b2s_exists"])

    def test_a_folder_with_a_record_and_no_table_is_still_an_entry(self) -> None:
        """Its media, its curation and its play history are all still here. Refusing it
        would mean an entry disappears the moment the thing that plays it does."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "Nothing To Play (Original 2024)"
            folder.mkdir()
            (folder / "Nothing To Play (Original 2024).info").write_text("{}")

            by_name = {g.game_dir_name: g for g in GameParser(root).get_all_games()}

            self.assertIn("Nothing To Play (Original 2024)", by_name)
            self.assertEqual(by_name["Nothing To Play (Original 2024)"].table_files, [])

    def test_a_table_with_no_record_is_still_an_entry(self) -> None:
        """The bootstrap, and it has to stay: discovery writes the record on the first
        pass that sees the folder, and it only ever sees folders that are already
        games. Refusing this one would leave it invisible with nothing able to fix it."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "No Record Yet (Bally 1990)"
            folder.mkdir()
            (folder / "No Record Yet (Bally 1990).vpx").write_text("")

            parser = GameParser(root)
            by_name = {g.game_dir_name: g for g in parser.get_all_games()}

            self.assertIn("No Record Yet (Bally 1990)", by_name)
            self.assertEqual([row["folder"] for row in parser.get_missing_games()],
                             ["No Record Yet (Bally 1990)"])

    def test_a_folder_with_neither_is_not_an_entry(self) -> None:
        """A Backglasses folder sitting beside the games is not a game."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "Backglasses").mkdir()
            (root / "Backglasses" / "something.directb2s").write_text("")

            self.assertEqual(GameParser(root).get_all_games(), [])

    def test_an_entry_with_no_table_names_no_table(self) -> None:
        """Empty rather than the folder: `game_dir / ""` is the folder itself, and a
        reader that stats it would be told there is a table here."""
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            folder = root / "Nothing To Play (Original 2024)"
            folder.mkdir()
            (folder / "Nothing To Play (Original 2024).info").write_text("{}")

            game = GameParser(root).get_all_games()[0]

            self.assertEqual(game.full_path_vpx_file, "")
            self.assertIsNotNone(game.creation_time)


class ParallelScanTests(TempTree):
    """The scan reads folders in parallel, and must not become a race.

    Reading a folder is almost entirely waiting - on the network share a real library
    lives on, directory listings were 6.5s of a 7.4s scan - so threads cover the wait.
    What they must not change is the answer: the library is sorted, and a scan whose
    order depended on which network read returned first would make the wheel's order a
    race, and put a folder's problems in another folder's report.
    """

    def _library(self, count: int):
        for i in range(count):
            write_game(self.root, f"Game {i:03}", info={"Info": {"Name": f"Game {i:03}"}})
        return GameParser(str(self.root))

    def test_the_order_is_the_sorted_order_not_the_finishing_order(self) -> None:
        parser = self._library(40)
        names = [g.game_dir_name for g in parser.get_all_games()]
        self.assertEqual(names, sorted(names))

    def test_the_same_library_scans_the_same_way_every_time(self) -> None:
        self._library(40)
        runs = {tuple(g.game_dir_name for g in GameParser(str(self.root)).get_all_games())
                for _ in range(5)}
        self.assertEqual(len(runs), 1, "the scan is not deterministic")

    def test_a_small_library_skips_the_pool_and_agrees_with_it(self) -> None:
        """Below the threshold the pool costs more than it saves, so it is not used -
        and the two paths must not disagree about what they found."""
        from common.games import game_parser

        self._library(4)
        serial = [g.game_dir_name for g in GameParser(str(self.root)).get_all_games()]
        original = game_parser._PARALLEL_SCAN_THRESHOLD
        game_parser._PARALLEL_SCAN_THRESHOLD = 1
        try:
            parallel = [g.game_dir_name for g in GameParser(str(self.root)).get_all_games()]
        finally:
            game_parser._PARALLEL_SCAN_THRESHOLD = original
        self.assertEqual(serial, parallel)

    def test_a_folders_problems_are_reported_against_that_folder(self) -> None:
        """Two threads appending to one list is how this would go wrong quietly."""
        for i in range(20):
            write_game(self.root, f"Fine {i:03}", info={"Info": {"Name": f"Fine {i:03}"}})
        for i in range(6):
            write_game(self.root, f"NoInfo {i:03}")

        parser = GameParser(str(self.root))
        missing = {row["folder"] for row in parser.missing_games}

        self.assertEqual(len(missing), 6, parser.missing_games)
        self.assertTrue(all(name.startswith("NoInfo") for name in missing), missing)
        for row in parser.missing_games:
            self.assertTrue(row["path"].endswith(row["folder"]),
                            f"{row['folder']} reported against {row['path']}")


class UnreadableInfoTests(TempTree):
    """A `.info` the scan cannot read leaves out its own folder and no other."""

    FINE = "Fine (Original 2024)"

    def _scan_beside(self, name: str, content: bytes,
                     mode: int = 0o644) -> tuple[list[str | None], dict[str, str]]:
        write_game(self.root, self.FINE, info={"Info": {"Title": "Fine"}})
        info = write_game(self.root, name) / f"{name}.info"
        info.write_bytes(content)
        info.chmod(mode)
        self.addCleanup(info.chmod, 0o644)
        if os.access(info, os.R_OK) != bool(mode & 0o400):
            self.skipTest("this user reads files whatever their mode")
        parser = GameParser(str(self.root))
        return ([game.game_dir_name for game in parser.get_all_games()],
                {row["folder"]: row["error"] for row in parser.get_unreadable_games()})

    def test_an_empty_one(self) -> None:
        loaded, said = self._scan_beside("Empty (Original 2024)", b"")

        self.assertEqual([self.FINE], loaded)
        self.assertEqual({"Empty (Original 2024)": t("error.games.info_empty")}, said)

    def test_one_that_is_not_json(self) -> None:
        loaded, said = self._scan_beside("Malformed (Original 2024)",
                                         b'{"Info": {"Title": "broken",,,}\n')

        self.assertEqual([self.FINE], loaded)
        self.assertEqual({"Malformed (Original 2024)":
                          t("error.games.info_bad_line", line=1)}, said)

    def test_one_holding_a_list_or_a_string(self) -> None:
        for content in (b'["Info", "Title"]', b'"just a name"', b"7", b"null"):
            with self.subTest(content=content):
                loaded, said = self._scan_beside("Shaped (Original 2024)", content)

                self.assertEqual([self.FINE], loaded)
                self.assertEqual({"Shaped (Original 2024)":
                                  t("error.games.info_wrong_shape")}, said)

    def test_one_not_saved_as_utf8(self) -> None:
        loaded, said = self._scan_beside("Latin (Original 2024)",
                                         '{"Info": {"Title": "Café"}}'.encode("cp1252"))

        self.assertEqual([self.FINE], loaded)
        self.assertEqual({"Latin (Original 2024)": t("error.games.info_not_utf8")}, said)

    def test_one_it_may_not_open(self) -> None:
        name = "Locked (Original 2024)"
        loaded, said = self._scan_beside(name, b"{}", mode=0)

        self.assertEqual([self.FINE], loaded)
        info = self.root / name / f"{name}.info"
        self.assertEqual({name: t("said.why.no_permission_at", path=str(info))}, said)


if __name__ == "__main__":
    unittest.main()
