"""Reading a `VPReg.stg` and writing the sets in it as a `VPReg.ini`."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.games import score_parser
from common.games.formats import vpreg
from tests.support.ole import stg_bytes

SETS = {
    "Taxi": {"HighScore1": "50000", "HighScore1Name": "ABC"},
    "Medieval": {"Hi": "1000"},
    "Odd": {"Fine": "ok", "Two Lines": "a\nb", "Bad=Name": "x", "[x]": "y"},
}


class VpregCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.stg = self.root / "VPReg.stg"
        self.stg.write_bytes(stg_bytes(SETS))


class ReadingTests(VpregCase):
    def test_every_set_comes_back_with_its_values(self) -> None:
        self.assertEqual(vpreg.sets_in(self.stg), SETS)

    def test_a_file_that_is_not_one_answers_nothing(self) -> None:
        bad = self.root / "bad.stg"
        bad.write_bytes(b"not an ole file")

        self.assertEqual(vpreg.sets_in(bad), {})
        self.assertEqual(vpreg.sets_in(self.root / "missing.stg"), {})


class WritingTests(VpregCase):
    def test_only_the_named_sets_are_written(self) -> None:
        text = vpreg.ini_text(vpreg.sets_in(self.stg), ["Medieval"])

        self.assertEqual(text, "[Medieval]\nHi=1000\n")

    def test_the_sets_come_in_the_order_asked_for(self) -> None:
        text = vpreg.ini_text(vpreg.sets_in(self.stg), ["Taxi", "Medieval"])

        self.assertEqual(
            text, "[Taxi]\nHighScore1=50000\nHighScore1Name=ABC\n\n[Medieval]\nHi=1000\n")

    def test_a_set_to_skip_is_left_out_in_any_case(self) -> None:
        text = vpreg.ini_text(vpreg.sets_in(self.stg), ["Taxi", "Medieval"], skip=["TAXI"])

        self.assertEqual(text, "[Medieval]\nHi=1000\n")

    def test_the_sets_an_ini_holds_are_listed_lowercased(self) -> None:
        self.assertEqual(vpreg.sections_in("[Taxi]\nHi=1\n\n [Medieval ]\nx=2\n"),
                         {"taxi", "medieval"})

    def test_a_set_the_file_lacks_adds_nothing(self) -> None:
        self.assertEqual(vpreg.ini_text(vpreg.sets_in(self.stg), ["Nobody"]), "")

    def test_a_line_the_file_cannot_hold_is_left_out(self) -> None:
        text = vpreg.ini_text(vpreg.sets_in(self.stg), ["Odd"])

        self.assertEqual(text, "[Odd]\nFine=ok\n")


class VPinFEReadsItTests(VpregCase):
    def test_the_scores_in_a_written_ini_show(self) -> None:
        game = self.root / "Taxi (Williams 1988)"
        (game / "user").mkdir(parents=True)
        (game / "user" / "VPReg.ini").write_text(
            vpreg.ini_text(vpreg.sets_in(self.stg), ["Taxi"]), encoding="utf-8")

        with mock.patch.object(score_parser, "resolve_rom_name", side_effect=lambda one: one):
            where = score_parser.resolve_score_input_path("taxi_l4", str(game))
        entries = score_parser.decode_ini_file(where)

        self.assertEqual(where, str(game / "user" / "VPReg.ini"))
        self.assertEqual([(one.initials, one.score) for one in entries], [("ABC", 50000)])
