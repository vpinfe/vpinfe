import atexit
import contextlib
import json
import shutil
import tempfile
import unittest
from dataclasses import asdict
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

_test_config_dir = Path(tempfile.mkdtemp(prefix="vpinfe-score-parser-test-"))
atexit.register(shutil.rmtree, _test_config_dir, ignore_errors=True)
(_test_config_dir / "roms.json").write_text(
    json.dumps(
        {
            "agent777": {"scoretype": "HIGH SCORE", "decoder": "dummy"},
            "aar_101": {"scoretype": "Leaderboard", "decoder": "dummy"},
            "Matrix": {"scoretype": "HIGH SCORE", "decoder": "dummy"},
        }
    ),
    encoding="utf-8",
)

from common.config import paths

paths.USER_ROMS_PATH = _test_config_dir / "roms.json"
paths.USER_CONFIG_PATH = _test_config_dir / "vpinfe.ini"
from common.games import score_parser
from common.games.score_parser import ParsedEntry, result_to_jsonable
from common.players import players

score_parser.USER_ROMS_PATH = paths.USER_ROMS_PATH


@contextlib.contextmanager
def _up(*initials: str):
    """A roster with one kept player up for each set of initials given."""
    with TemporaryDirectory() as folder:
        roster = players.Roster(Path(folder) / "players.json")
        roster.set_who_is_up([roster.add_player(initials=one).player_id
                              for one in initials])
        with mock.patch.object(score_parser, "get_roster", lambda: roster):
            yield


class TestScoreParser(unittest.TestCase):
    def test_get_roms_path_resolves_to_the_user_config_copy(self) -> None:
        with TemporaryDirectory() as temp_dir:
            roms_path = Path(temp_dir) / "roms.json"
            roms_path.write_text('{"foo": {"scoretype": "HIGH SCORE"}}', encoding="utf-8")
            with mock.patch.object(score_parser, "USER_ROMS_PATH", roms_path):
                self.assertEqual(score_parser.get_roms_candidate_paths(), [roms_path])
                self.assertEqual(score_parser.get_roms_path(), roms_path)

    def test_get_roms_path_error_names_the_path_it_checked(self) -> None:
        with TemporaryDirectory() as temp_dir:
            missing_path = Path(temp_dir) / "roms.json"
            with mock.patch.object(score_parser, "USER_ROMS_PATH", missing_path):
                with self.assertRaises(FileNotFoundError) as ctx:
                    score_parser.get_roms_path()

        self.assertIn(str(missing_path), str(ctx.exception))

    def test_result_to_jsonable_returns_direct_score_payload_for_scalar_scores(self) -> None:
        result = result_to_jsonable("agent777", 123456)

        self.assertEqual(
            result,
            {
                "rom": "agent777",
                "resolved_rom": "agent777",
                "score_kind": "HIGH SCORE",
                "value": 123456,
            },
        )

    def test_result_to_jsonable_filters_empty_entries(self) -> None:
        result = result_to_jsonable(
            "aar_101",
            [
                ParsedEntry(section="HIGH SCORES", rank=1, initials="AAA", score=1000),
                ParsedEntry(section="HIGH SCORES", rank=2, initials="", score=None),
            ],
        )

        self.assertEqual(
            result,
            {
                "rom": "aar_101",
                "resolved_rom": "aar_101",
                "score_kind": "Leaderboard",
                "entries": [
                    {
                        "section": "HIGH SCORES",
                        "rank": 1,
                        "initials": "AAA",
                        "score": 1000,
                        "value_prefix": None,
                        "value_suffix": None,
                        "value_format": None,
                        "extra_lines": [],
                        "multiline": False,
                    }
                ],
            },
        )

    def test_result_to_jsonable_uses_ini_score_type_for_ini_sources(self) -> None:
        result = result_to_jsonable(
            "Matrix",
            [ParsedEntry(section="Scores", rank=1, initials="NEO", score=424242)],
            "/tmp/VPReg.ini",
        )

        self.assertEqual(
            result,
            {
                "rom": "Matrix",
                "resolved_rom": "Matrix",
                "score_kind": "ini",
                "entries": [
                    {
                        "section": "Scores",
                        "rank": 1,
                        "initials": "NEO",
                        "score": 424242,
                        "value_prefix": None,
                        "value_suffix": None,
                        "value_format": None,
                        "extra_lines": [],
                        "multiline": False,
                    }
                ],
            },
        )

    def test_a_blank_score_takes_the_initials_of_the_one_player_up(self) -> None:
        with _up("OWN"):
            result = result_to_jsonable(
                "aar_101",
                [ParsedEntry(section="HIGH SCORES", rank=1, initials="", score=1000)],
            )

        self.assertEqual(
            result,
            {
                "rom": "aar_101",
                "resolved_rom": "aar_101",
                "score_kind": "Leaderboard",
                "entries": [
                    {
                        "section": "HIGH SCORES",
                        "rank": 1,
                        "initials": "OWN",
                        "score": 1000,
                        "value_prefix": None,
                        "value_suffix": None,
                        "value_format": None,
                        "extra_lines": [],
                        "multiline": False,
                    }
                ],
            },
        )

    def test_with_several_up_a_blank_score_takes_nobody_s_initials(self) -> None:
        """Whichever of them it was, the others did not score it."""
        with _up("OWN", "ABC"):
            self.assertEqual(score_parser.get_default_initials(), "")

    def test_a_player_up_with_no_initials_gives_none(self) -> None:
        with _up(""):
            self.assertEqual(score_parser.get_default_initials(), "")

    def test_initials_given_outright_win_over_who_is_up(self) -> None:
        with _up("OWN"):
            result = result_to_jsonable(
                "aar_101",
                [ParsedEntry(section="HIGH SCORES", rank=1, initials="", score=1000)],
                initials="")

        assert result is not None
        self.assertEqual(result["entries"][0]["initials"], "")

    def test_result_to_jsonable_preserves_existing_initials(self) -> None:
        with _up("OWN"):
            result = result_to_jsonable(
                "aar_101",
                [ParsedEntry(section="HIGH SCORES", rank=1, initials="AAA", score=1000)],
            )

        assert result is not None
        self.assertEqual(result["entries"][0]["initials"], "AAA")

    def test_result_to_jsonable_does_not_fill_blank_non_score_entries(self) -> None:
        with _up("OWN"):
            result = result_to_jsonable(
                "aar_101",
                [ParsedEntry(section="HIGH SCORES", rank=1, initials="",
                             extra_lines=["SPECIAL"])],
            )

        assert result is not None
        self.assertEqual(result["entries"][0]["initials"], "")

    def test_resolve_score_input_path_prefers_nvram_for_game_directory(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_dir = Path(temp_dir)
            nvram_path = game_dir / "pinmame" / "nvram" / "agent777.nv"
            vpreg_path = game_dir / "user" / "VPReg.ini"
            nvram_path.parent.mkdir(parents=True)
            vpreg_path.parent.mkdir(parents=True)
            nvram_path.write_bytes(b"nv")
            vpreg_path.write_text("[Dummy]\n", encoding="utf-8")

            resolved = score_parser.resolve_score_input_path("agent777", str(game_dir))

        self.assertEqual(resolved, str(nvram_path))

    def test_resolve_score_input_path_falls_back_to_vpreg_ini(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_dir = Path(temp_dir)
            vpreg_path = game_dir / "user" / "VPReg.ini"
            vpreg_path.parent.mkdir(parents=True)
            vpreg_path.write_text("[Dummy]\n", encoding="utf-8")

            resolved = score_parser.resolve_score_input_path("agent777", str(game_dir))

        self.assertEqual(resolved, str(vpreg_path))

    def test_resolve_rom_name_matches_rom_entries_case_insensitively(self) -> None:
        self.assertEqual(score_parser.resolve_rom_name("matrix"), "Matrix")

    def test_resolve_score_input_path_matches_nvram_case_insensitively(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_dir = Path(temp_dir)
            nvram_path = game_dir / "pinmame" / "nvram" / "Matrix.nv"
            nvram_path.parent.mkdir(parents=True)
            nvram_path.write_bytes(b"nv")

            resolved = score_parser.resolve_score_input_path("matrix", str(game_dir))

        self.assertEqual(resolved, str(nvram_path))

    def test_read_rom_with_source_returns_resolved_special_text_path(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_dir = Path(temp_dir)
            text_path = game_dir / "user" / "OKIES.txt"
            text_path.parent.mkdir(parents=True)
            text_path.write_text("123456\n", encoding="utf-8")

            with mock.patch.object(score_parser, "decode_special_text_score_file",
                                   return_value=123456) as decoder:
                result, resolved = score_parser.read_rom_with_source(
                    "OKIES_TornadoRally", str(game_dir))

        decoder.assert_called_once_with("OKIES_TornadoRally", str(text_path))
        self.assertEqual(result, 123456)
        self.assertEqual(resolved, str(text_path))

    def test_read_rom_with_source_parses_expressway_score_text(self) -> None:
        with TemporaryDirectory() as temp_dir:
            game_dir = Path(temp_dir)
            text_path = game_dir / "user" / "Expressway.txt"
            text_path.parent.mkdir(parents=True)
            text_path.write_text("playerscore1    10100\n", encoding="utf-8")

            result, resolved = score_parser.read_rom_with_source("Expressway", str(game_dir))

        self.assertEqual(resolved, str(text_path))
        self.assertEqual(result, [ParsedEntry(section="", rank=None, initials="", score=10100)])

    def test_decode_ini_file_skips_blank_scores(self) -> None:
        with TemporaryDirectory() as temp_dir:
            ini_path = Path(temp_dir) / "VPReg.ini"
            ini_path.write_text(
                "\n".join(
                    [
                        "[Aerosmith]",
                        "Score1Name = AAA",
                        "Score1 = ",
                        "Score2Name = BBB",
                        "Score2 = 12,345",
                        "Hiscore = ",
                        "[Other]",
                        "Highscore = 100",
                    ]
                ),
                encoding="utf-8",
            )

            result = score_parser.decode_ini_file(str(ini_path))

        self.assertEqual(
            result,
            [
                ParsedEntry(section="Score", rank=2, initials="BBB", score=12345),
                ParsedEntry(section="Other", rank=None, initials="", score=100),
            ],
        )


def _table(*entries: tuple[str, str, int]) -> dict:
    """A reading as result_to_jsonable gives one: (section, initials, score), ranked in
    the order given."""
    return {"rom": "aar_101", "resolved_rom": "aar_101", "score_kind": "Leaderboard",
            "entries": [asdict(ParsedEntry(section=section, rank=rank, initials=initials,
                                           score=score))
                        for rank, (section, initials, score) in enumerate(entries, 1)]}


def _found(before: dict | None, after: dict | None) -> list[tuple[str, int | None]]:
    return [(entry["initials"], entry["score"])
            for entry in score_parser.new_entries(before, after)]


class NewThisGameTests(unittest.TestCase):
    def test_a_new_score_is_the_only_new_entry_though_it_moves_the_rest(self) -> None:
        before = _table(("HS", "AAA", 300), ("HS", "BBB", 200), ("HS", "CCC", 100))
        after = _table(("HS", "AAA", 300), ("HS", "ABC", 250), ("HS", "BBB", 200))

        self.assertEqual(_found(before, after), [("ABC", 250)])

    def test_an_old_entry_that_changes_section_is_not_new(self) -> None:
        """A machine that moves its old champion down into the high scores."""
        before = _table(("GRAND CHAMPION", "AAA", 900), ("HS", "BBB", 500))
        after = _table(("GRAND CHAMPION", "ABC", 950), ("HS", "AAA", 900),
                       ("HS", "BBB", 500))

        self.assertEqual(_found(before, after), [("ABC", 950)])

    def test_the_same_score_again_is_new_once_for_each_time(self) -> None:
        before = _table(("HS", "", 100))
        after = _table(("HS", "", 100), ("HS", "", 100), ("HS", "", 100))

        self.assertEqual(_found(before, after), [("", 100), ("", 100)])

    def test_nothing_is_new_without_a_reading_before_the_game(self) -> None:
        """A first game writes the whole factory table, default initials and all."""
        self.assertEqual(_found(None, _table(("HS", "AAA", 300))), [])

    def test_a_one_number_reading_is_new_when_the_number_changed(self) -> None:
        before = {"rom": "agent777", "value": 1000}

        self.assertEqual(_found(before, {**before, "value": 1500}), [("", 1500)])
        self.assertEqual(_found(before, dict(before)), [])

    def test_two_kinds_of_reading_are_not_compared(self) -> None:
        self.assertEqual(_found({"rom": "agent777", "value": 1000},
                                _table(("HS", "AAA", 300))), [])


class EntriesWithInitialsTests(unittest.TestCase):
    def test_only_a_blank_score_takes_them(self) -> None:
        entries = _table(("HS", "", 300), ("HS", "???", 200), ("HS", "AAA", 100))["entries"]
        entries.append(asdict(ParsedEntry(section="HS", rank=4, initials="",
                                          extra_lines=["SPECIAL"])))

        given = score_parser.entries_with_initials(entries, "OWN")

        self.assertEqual([entry["initials"] for entry in given], ["OWN", "???", "AAA", ""])


if __name__ == "__main__":
    unittest.main()
