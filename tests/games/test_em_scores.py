"""The score file an EM table's script names, and what its scores are kept under."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

from common.games import em_scores, game_play_service
from tests.support.library import TempTree, fake_game, write_game

SCORES = "7500\n7000\n6000\n5500\n5000\nAAA\nZZZ\nXXX\nABC\nBBB\n"
FOLDER = "Example (Maker 1971)"


def _names(*files: str):
    """The script says `files`, as `strings_in` gives them."""
    return patch.object(em_scores, "_strings_of", return_value="\n".join(files))


class FindTests(TempTree):
    def _game(self, **user: str):
        folder = write_game(self.root, FOLDER, files={
            f"user/{name}": text.encode() for name, text in user.items()})
        return fake_game(folder, FOLDER)

    def _entry(self, rom: str = ""):
        return {"id": "t1", "filename": f"{FOLDER}.vpx", "rom": rom}

    def test_the_file_the_script_names_is_read_as_scores(self) -> None:
        game = self._game(**{"Scores_71VPX.txt": SCORES})

        with _names("scores_71vpx.txt"):
            found = em_scores.find(game, self._entry("gtb_2001"))

        assert found is not None
        self.assertEqual(found.path.name, "Scores_71VPX.txt")
        self.assertEqual([(one.initials, one.score) for one in found.entries][:2],
                         [("AAA", 7500), ("ZZZ", 7000)])

    def test_a_file_the_script_does_not_name_is_not_tried(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with _names("other.txt"):
            self.assertIsNone(em_scores.find(game, self._entry()))

    def test_a_named_file_with_no_scores_in_it_is_passed_over(self) -> None:
        game = self._game(**{"LMEMTables.txt": "1\n", "Scores.txt": SCORES})

        with _names("lmemtables.txt", "scores.txt"):
            found = em_scores.find(game, self._entry())

        assert found is not None
        self.assertEqual(found.path.name, "Scores.txt")

    def test_of_two_that_read_the_newest_is_the_tables(self) -> None:
        game = self._game(**{"Old.txt": SCORES, "New.txt": SCORES.replace("7500", "9900")})
        user = Path(game.full_path_game or "") / "user"
        os.utime(user / "Old.txt", (1_000_000, 1_000_000))

        with _names("old.txt", "new.txt"):
            found = em_scores.find(game, self._entry())

        assert found is not None
        self.assertEqual((found.path.name, found.entries[0].score), ("New.txt", 9900))

    def test_a_folder_with_no_text_file_never_reads_the_table(self) -> None:
        game = self._game()

        with patch.object(em_scores, "_strings_of") as read:
            self.assertIsNone(em_scores.find(game, self._entry()))

        read.assert_not_called()

    def test_a_table_that_cannot_be_read_has_no_score_file(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with patch.object(em_scores, "_strings_of", side_effect=OSError("gone")):
            self.assertIsNone(em_scores.find(game, self._entry()))

    def test_a_table_with_a_rom_is_kept_under_its_rom_without_reading_its_script(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with patch.object(em_scores, "_strings_of") as read:
            self.assertEqual(em_scores.key_of(game, self._entry("gtb_2001")), "gtb_2001")

        read.assert_not_called()

    def test_a_file_already_kept_under_is_taken_before_a_newer_one(self) -> None:
        game = self._game(**{"Kept.txt": SCORES, "Newer.txt": SCORES})
        user = Path(game.full_path_game or "") / "user"
        os.utime(user / "Kept.txt", (1_000_000, 1_000_000))

        with _names("kept.txt", "newer.txt"):
            self.assertEqual(em_scores.key_of(game, self._entry()), "Newer.txt")
            with patch.object(em_scores, "_kept_names", return_value={"Kept.txt"}):
                self.assertEqual(em_scores.key_of(game, self._entry()), "Kept.txt")

    def test_a_launch_keeps_reading_the_file_it_started_under(self) -> None:
        game = self._game(**{"A.txt": SCORES, "Other.txt": SCORES.replace("7500", "9900")})
        user = Path(game.full_path_game or "") / "user"
        os.utime(user / "A.txt", (1_000_000, 1_000_000))

        with _names("a.txt", "other.txt"):
            reading, path = game_play_service.parse_score_from_nvram(
                game, "A.txt", initials="", entry=self._entry())

        assert reading is not None
        self.assertEqual((Path(path or "").name, reading["entries"][0]["score"]),
                         ("A.txt", 7500))

    def test_a_table_with_no_rom_is_kept_under_its_score_files_name(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with _names("scores.txt"):
            self.assertEqual(em_scores.key_of(game, self._entry()), "Scores.txt")
        self.assertEqual(em_scores.key_of(self._game(), self._entry()), "")

    def test_a_rom_with_a_source_is_read_from_it_not_from_the_script(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with _names("scores.txt"), patch("common.games.score_parser.read_rom_with_source",
                                         return_value=(5, "/nv/gtb_2001.nv")):
            self.assertEqual(em_scores.read_with_source(game, self._entry("gtb_2001")),
                             (5, "/nv/gtb_2001.nv"))

    def test_a_rom_with_no_source_falls_to_the_score_file(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with _names("scores.txt"):
            entries, path = em_scores.read_with_source(game, self._entry("gtb_2001"))

        self.assertEqual((entries[0].score, Path(path).name), (7500, "Scores.txt"))

    def test_a_rom_the_map_knows_does_not_fall_to_another_file(self) -> None:
        game = self._game(**{"Other.txt": SCORES})

        with _names("other.txt"), patch.object(em_scores, "in_the_map", return_value=True), \
                patch("common.games.score_parser.read_rom_with_source",
                      side_effect=FileNotFoundError("no file yet")):
            with self.assertRaises(FileNotFoundError):
                em_scores.read_with_source(game, self._entry("okies_tornado"))

    def test_nothing_to_read_is_a_missing_file(self) -> None:
        with self.assertRaises(FileNotFoundError):
            em_scores.read_with_source(self._game(), self._entry())

    def test_a_launch_reads_a_table_with_no_rom_under_the_files_name(self) -> None:
        game = self._game(**{"Scores.txt": SCORES})

        with _names("scores.txt"):
            reading, path = game_play_service.parse_score_from_nvram(
                game, "Scores.txt", initials="", entry=self._entry())

        assert reading is not None
        self.assertEqual((reading["rom"], reading["score_kind"], Path(path or "").name),
                         ("Scores.txt", "txt", "Scores.txt"))
        self.assertEqual(reading["entries"][0]["score"], 7500)
