"""Putting a ROM set, a sound bank or a color set into a game.

The things that are neither the game file nor artwork live in a folder of their own, and
which folder is the registry's answer - the same one an upload gets. That is the point:
a library imported from another frontend has to put them where an upload would have, or
the two ways in disagree about where a game's ROM is.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import service_errors
from common.config import path_state
from common.extensions.context import ExtensionFiles
from common.extensions.contract import ContractError
from common.extensions.games import ExtensionGames
from common.failures import why
from tests.support.hung_folder import never_answering


class _Files(ExtensionFiles):
    def __init__(self, *roots: Path) -> None:
        super().__init__("probe", allowed=True)
        self._roots = tuple(str(one) for one in roots)

    def roots(self) -> tuple[str, ...]:
        return self._roots


class PutAssetCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.source = self.root / "source"
        self.source.mkdir()
        self.game = self.root / "Attack from Mars (Bally 1995)"
        self.game.mkdir()
        self.games = ExtensionGames("probe", ("games:read", "games:write"),
                                    _Files(self.source))
        self.games.folder = lambda game_id: str(self.game)  # type: ignore[method-assign]


class ManufacturerLogoTests(PutAssetCase):
    def setUp(self) -> None:
        super().setUp()
        from common.media.manufacturer_logos import configure_manufacturer_logos

        self.logos = self.root / "logos"
        self.logos.mkdir()
        configure_manufacturer_logos(self.logos)
        self.addCleanup(configure_manufacturer_logos, None)
        self.picture = self.source / "Williams.png"
        self.picture.write_bytes(b"png")
        self.writer = ExtensionGames("probe", ("config:write",), _Files(self.source))

    def test_a_logo_for_a_maker_is_placed_in_the_logos_folder(self) -> None:
        done = self.writer.put_manufacturer_logos(["Williams"], [("Williams", self.picture)])

        self.assertEqual(done["placed"], ["Williams.png"])
        self.assertTrue((self.logos / "williams.png").is_file())

    def test_it_needs_the_scope_that_writes_configuration(self) -> None:
        with self.assertRaises(ContractError):
            self.games.put_manufacturer_logos(["Williams"], [("Williams", self.picture)])

    def test_an_image_outside_what_the_extension_declared_is_refused(self) -> None:
        outside = self.root / "elsewhere.png"
        outside.write_bytes(b"png")

        with self.assertRaises(ContractError):
            self.writer.put_manufacturer_logos(["Williams"], [("Williams", outside)])
        self.assertEqual(list(self.logos.iterdir()), [])


class UnreachableFolderTests(PutAssetCase):
    def test_a_folder_that_does_not_answer_is_refused_by_every_writer(self) -> None:
        rom = self.source / "afm_113b.zip"
        rom.write_bytes(b"rom")
        picture = self.source / "wheel.png"
        picture.write_bytes(b"png")
        writers = {
            "asset": lambda: self.games.put_asset("g1", "rom", rom),
            "media": lambda: self.games.put_media("g1", "wheel", picture),
        }

        with mock.patch.object(path_state, "PROBE_SECONDS", 0.2), \
                never_answering(str(self.game)):
            for label, put in writers.items():
                with self.subTest(label), self.assertRaises(service_errors.BlockedError):
                    put()
        self.assertEqual(list(self.game.iterdir()), [])


class DestinationTests(PutAssetCase):
    def test_a_rom_lands_where_an_upload_would_have_put_it(self) -> None:
        rom = self.source / "afm_113b.zip"
        rom.write_bytes(b"rom")

        where = self.games.put_asset("g1", "rom", rom)

        self.assertEqual(where, "pinmame/roms/afm_113b.zip")
        self.assertTrue((self.game / where).is_file())

    def test_a_bank_keyed_on_a_rom_lands_under_it(self) -> None:
        bank = self.source / "afm"
        bank.mkdir()
        (bank / "altsound.csv").write_text("x")

        where = self.games.put_asset("g1", "altsound", bank, rom="afm_113b")

        self.assertEqual(where, "altsound/afm_113b/afm")
        self.assertTrue((self.game / where / "altsound.csv").is_file())

    def test_a_whole_folder_comes_across(self) -> None:
        """An asset arrives as a directory as often as a file."""
        pack = self.source / "pup"
        (pack / "deep").mkdir(parents=True)
        (pack / "deep" / "clip.mp4").write_text("video")

        where = self.games.put_asset("g1", "pup_pack", pack)

        self.assertTrue((self.game / where / "deep" / "clip.mp4").is_file())

    def test_a_kind_that_needs_a_rom_says_so(self) -> None:
        bank = self.source / "afm"
        bank.mkdir()

        with self.assertRaises(ValueError) as caught:
            self.games.put_asset("g1", "altsound", bank)
        self.assertEqual("altsound needs a ROM name", why(caught.exception))

    def test_a_kind_with_no_folder_of_its_own_is_refused(self) -> None:
        """A backglass sits beside the game file and is named after it, so "which
        folder" is the wrong question and a made-up answer would be worse."""
        glass = self.source / "x.directb2s"
        glass.write_bytes(b"glass")

        with self.assertRaises(ValueError) as caught:
            self.games.put_asset("g1", "backglass", glass)
        self.assertEqual("backglass has no folder of its own", why(caught.exception))

    def test_a_media_kind_nobody_has_is_named(self) -> None:
        art = self.source / "wheel.png"
        art.write_bytes(b"art")

        with self.assertRaises(ValueError) as caught:
            self.games.put_media("g1", "hologram", art)
        self.assertEqual("No media kind called hologram", why(caught.exception))


class ScoreAndSavedPlaceTests(PutAssetCase):
    def test_high_scores_and_settings_land_under_pinmame(self) -> None:
        nvram = self.source / "afm_113b.nv"
        cfg = self.source / "afm_113b.cfg"
        nvram.write_bytes(b"nv")
        cfg.write_bytes(b"cfg")

        self.assertEqual(self.games.put_asset("g1", "nvram", nvram), "pinmame/nvram/afm_113b.nv")
        self.assertEqual(self.games.put_asset("g1", "cfg", cfg), "pinmame/cfg/afm_113b.cfg")

    def test_a_file_a_script_saved_lands_in_user(self) -> None:
        saved = self.source / "Scores.txt"
        saved.write_text("100")

        self.assertEqual(self.games.put_asset("g1", "user", saved), "user/Scores.txt")

    def test_scripts_and_a_dmd_folder_land_beside_the_table(self) -> None:
        script = self.source / "Granny.vbs"
        script.write_text("x")
        dmd = self.source / "Taxi.UltraDMD"
        dmd.mkdir()
        (dmd / "a.png").write_bytes(b"png")

        self.assertEqual(self.games.put_asset("g1", "scripts", script), "scripts/Granny.vbs")
        self.assertEqual(self.games.put_asset("g1", "dmd_content", dmd), "Taxi.UltraDMD")
        self.assertTrue((self.game / "Taxi.UltraDMD" / "a.png").is_file())

    def test_a_name_map_line_is_where_the_alias_reader_looks(self) -> None:
        from common.games.asset_resolver import read_alias_map

        where = self.games.put_rom_alias("g1", "taxi_l1", "taxi_l4")

        self.assertEqual(where, "pinmame/alias.txt")
        self.assertEqual(read_alias_map(str(self.game)).get("taxi_l1"), "taxi_l4")

    def test_a_name_map_the_game_already_holds_is_not_written_over(self) -> None:
        self.games.put_rom_alias("g1", "taxi_l1", "taxi_l4")

        with self.assertRaises(FileExistsError):
            self.games.put_rom_alias("g1", "taxi_l2", "taxi_l5")
        self.assertEqual((self.game / "pinmame" / "alias.txt").read_text(), "taxi_l1,taxi_l4\n")

    def test_a_name_map_line_needs_both_names_and_no_comma(self) -> None:
        for alias, rom in (("", "taxi_l4"), ("taxi_l1", ""), ("a,b", "taxi_l4")):
            with self.subTest(alias=alias, rom=rom), self.assertRaises(ValueError):
                self.games.put_rom_alias("g1", alias, rom)

    def test_a_reader_cannot_write_a_name_map(self) -> None:
        reader = ExtensionGames("reader", ("games:read",), _Files(self.source))
        reader.folder = lambda game_id: str(self.game)  # type: ignore[method-assign]

        with self.assertRaises(ContractError):
            reader.put_rom_alias("g1", "taxi_l1", "taxi_l4")

    def test_the_new_places_are_in_the_list_of_kinds(self) -> None:
        kinds = {"nvram", "cfg", "user", "scripts", "dmd_content"}
        self.assertTrue(kinds <= set(self.games.asset_kinds()))

    def test_a_score_placed_there_is_where_the_score_reader_looks(self) -> None:
        from common.games import score_parser

        nvram = self.source / "afm_113b.nv"
        nvram.write_bytes(b"nv")
        self.games.put_asset("g1", "nvram", nvram)

        with mock.patch.object(score_parser, "resolve_rom_name", side_effect=lambda one: one):
            found = score_parser.resolve_score_input_path("afm_113b", str(self.game))

        self.assertEqual(found, str(self.game / "pinmame" / "nvram" / "afm_113b.nv"))


class SavedSetsTests(PutAssetCase):
    def setUp(self) -> None:
        super().setUp()
        from tests.support.ole import stg_bytes

        self.stg = self.source / "VPReg.stg"
        self.stg.write_bytes(stg_bytes({"Taxi": {"Hi": "5"}, "Medieval": {"Hi": "9"}}))

    def test_only_the_named_sets_become_the_games_own_ini(self) -> None:
        wrote = self.games.put_saved_sets("g1", self.stg, ["Taxi"])

        self.assertEqual(wrote, 1)
        self.assertEqual((self.game / "user" / "VPReg.ini").read_text(), "[Taxi]\nHi=5\n")

    def test_the_sets_a_file_holds_are_listed(self) -> None:
        self.assertEqual(sorted(self.games.saved_sets(self.stg)), ["Medieval", "Taxi"])

    def test_a_set_the_file_lacks_writes_nothing(self) -> None:
        self.assertEqual(self.games.put_saved_sets("g1", self.stg, ["Nobody"]), 0)
        self.assertFalse((self.game / "user").exists())

    def test_a_game_that_has_played_keeps_what_its_ini_holds(self) -> None:
        (self.game / "user").mkdir()
        (self.game / "user" / "VPReg.ini").write_text("[taxi]\nHi=1\n")

        wrote = self.games.put_saved_sets("g1", self.stg, ["Taxi", "Medieval"])

        self.assertEqual(wrote, 1)
        self.assertEqual((self.game / "user" / "VPReg.ini").read_text(),
                         "[taxi]\nHi=1\n\n[Medieval]\nHi=9\n")

    def test_a_second_build_adds_the_sets_the_first_did_not_have(self) -> None:
        self.games.put_saved_sets("g1", self.stg, ["Taxi"])

        self.assertEqual(self.games.put_saved_sets("g1", self.stg, ["Medieval"]), 1)
        self.assertEqual(self.games.put_saved_sets("g1", self.stg, ["Taxi", "Medieval"]), 0)
        self.assertEqual((self.game / "user" / "VPReg.ini").read_text(),
                         "[Taxi]\nHi=5\n\n[Medieval]\nHi=9\n")

    def test_it_will_not_read_outside_what_it_declared(self) -> None:
        stray = self.root / "VPReg.stg"
        stray.write_bytes(self.stg.read_bytes())

        with self.assertRaises(ContractError):
            self.games.put_saved_sets("g1", stray, ["Taxi"])


class BoundsTests(PutAssetCase):
    def test_it_will_not_take_something_outside_what_it_declared(self) -> None:
        stray = self.root / "stray.zip"
        stray.write_bytes(b"rom")

        with self.assertRaises(ContractError):
            self.games.put_asset("g1", "rom", stray)

    def test_it_will_not_write_over_what_is_already_there(self) -> None:
        rom = self.source / "afm_113b.zip"
        rom.write_bytes(b"rom")
        where = self.games.put_asset("g1", "rom", rom)

        with self.assertRaises(FileExistsError) as caught:
            self.games.put_asset("g1", "rom", rom)
        self.assertEqual(f"Something is already at {self.game / where}",
                         why(caught.exception))

    def test_media_fills_an_empty_slot_and_leaves_a_held_one_when_asked_not_to_replace(
            self) -> None:
        mine = self.source / "mine.png"
        mine.write_bytes(b"mine")
        theirs = self.source / "theirs.png"
        theirs.write_bytes(b"theirs")
        landed = self.games.put_media("g1", "wheel", mine, replace=False)

        with self.assertRaises(FileExistsError):
            self.games.put_media("g1", "wheel", theirs, replace=False)
        self.assertEqual(b"mine", (self.game / "medias" / landed).read_bytes())

        self.games.put_media("g1", "wheel", theirs)
        self.assertEqual(b"theirs", (self.game / "medias" / landed).read_bytes())

    def _picture(self, name: str) -> Path:
        picture = self.source / name
        picture.write_bytes(name.encode())
        return picture

    def test_a_page_goes_beside_the_page_one_the_slot_shows(self) -> None:
        self.games.put_media("g1", "flyer", self._picture("one.png"), table_stem="Attack v2")

        landed = self.games.put_media("g1", "flyer", self._picture("two.png"),
                                      table_stem="Attack v2", page=2)

        self.assertEqual(landed, "(GameInfo 2) Attack v2.png")

    def test_a_page_added_to_a_downloaded_flyer_is_written_at_the_fixed_name(self) -> None:
        (self.game / "medias").mkdir()
        (self.game / "medias" / "flyer.png").write_bytes(b"downloaded")

        landed = self.games.put_media("g1", "flyer", self._picture("two.png"), page=2)

        self.assertEqual(landed, "flyer 2.png")

    def test_with_no_page_one_the_first_file_is_page_one(self) -> None:
        landed = self.games.put_media("g1", "flyer", self._picture("three.png"), page=3)

        self.assertEqual(landed, f"(GameInfo) {self.game.name}.png")

    def test_a_page_past_the_next_one_is_refused(self) -> None:
        self.games.put_media("g1", "flyer", self._picture("one.png"))

        with self.assertRaises(ValueError):
            self.games.put_media("g1", "flyer", self._picture("four.png"), page=4)

    def test_filling_only_an_empty_page_leaves_a_held_one(self) -> None:
        self.games.put_media("g1", "flyer", self._picture("one.png"))
        self.games.put_media("g1", "flyer", self._picture("two.png"), page=2)

        with self.assertRaises(FileExistsError):
            self.games.put_media("g1", "flyer", self._picture("again.png"),
                                 replace=False, page=2)
        landed = self.games.put_media("g1", "flyer", self._picture("three.png"),
                                      replace=False, page=3)
        self.assertEqual(landed, f"(GameInfo 3) {self.game.name}.png")

    def test_a_kind_without_pages_refuses_one(self) -> None:
        with self.assertRaises(ValueError):
            self.games.put_media("g1", "wheel", self._picture("w.png"), page=2)

    def test_placing_needs_the_write_scope(self) -> None:
        rom = self.source / "afm_113b.zip"
        rom.write_bytes(b"rom")
        reader = ExtensionGames("reader", ("games:read",), _Files(self.source))
        reader.folder = lambda game_id: str(self.game)  # type: ignore[method-assign]

        with self.assertRaises(ContractError):
            reader.put_asset("g1", "rom", rom)


if __name__ == "__main__":
    unittest.main()
