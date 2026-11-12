"""A flyer and an instruction card are several pictures, read in order.

The number is part of the name - page 2 of a table's flyer is `(Flyer 2) Name.png` - so
reading is parsing a folder's listing and writing is naming. Both are tested against what
is on disk, because what a page means is which file a theme is handed.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import mock

from common.games import library_policy, media_lookup, media_placement
from common.games.asset_registry import match_media_kind
from common.games.media_placement import FIXED, UnplaceableError
from common.media.media_specs import (
    NAMED_TOKENS,
    SpecName,
    resolve_media_entries,
    resolve_media_pages,
    resolve_media_pages_by_table,
    spec_named,
)

FOLDER = "Doom (Bally 1994)"
TABLE = "Doom (Bally 1994) 1.2"


def _writes_vpinfes_names(case: unittest.TestCase) -> None:
    """These cases are about pages, so they pin the library to one spelling; which
    spelling a library chooses is test_media_names."""
    patch = mock.patch.object(
        library_policy, "media_token",
        side_effect=lambda kind, values=None: NAMED_TOKENS.get(kind, {}).get("vpinfe"))
    patch.start()
    case.addCleanup(patch.stop)


def _pages(medias, root=(), stem=TABLE, folder=FOLDER):
    game_dir = f"/games/{folder}"
    entries = resolve_media_entries(game_dir, set(root), set(medias), "table", stem)
    found = resolve_media_pages(game_dir, set(root), set(medias), entries, "table", stem)
    return {kind: [path.name for path in paths] for kind, paths in found.items()}


class ReadingTests(unittest.TestCase):
    def test_pages_come_after_page_one_in_number_order(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png", f"(Flyer 3) {FOLDER}.png",
                        f"(Flyer 2) {FOLDER}.png"])

        self.assertEqual(found["flyer"], [f"(Flyer) {FOLDER}.png",
                                          f"(Flyer 2) {FOLDER}.png",
                                          f"(Flyer 3) {FOLDER}.png"])

    def test_a_gap_in_the_numbers_is_closed(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png",
                        f"(Flyer 5) {FOLDER}.png"])

        self.assertEqual(found["flyer"][1:], [f"(Flyer 2) {FOLDER}.png",
                                              f"(Flyer 5) {FOLDER}.png"])

    def test_there_is_no_ceiling_on_the_number(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png", f"(Flyer 40) {FOLDER}.png"])

        self.assertEqual(found["flyer"][1], f"(Flyer 40) {FOLDER}.png")

    def test_the_fixed_tier_numbers_its_pages_too(self) -> None:
        found = _pages(["flyer.png", "flyer 2.png", "instructioncard.png",
                        "instructioncard 2.png"])

        self.assertEqual(found, {"flyer": ["flyer.png", "flyer 2.png"],
                                 "instruction_card": ["instructioncard.png",
                                                      "instructioncard 2.png"]})

    def test_a_kind_with_one_page_is_not_listed(self) -> None:
        self.assertEqual(_pages([f"(Flyer) {FOLDER}.png"]), {})

    def test_pages_follow_the_tier_where_page_one_resolves(self) -> None:
        """A table's own flyer brings its own pages and never borrows the folder's."""
        found = _pages([f"(Flyer) {TABLE}.png", f"(Flyer 2) {FOLDER}.png",
                        f"(Flyer) {FOLDER}.png", "flyer 2.png"])

        self.assertEqual(found, {})

    def test_a_table_brings_its_own_pages(self) -> None:
        found = _pages([f"(Flyer) {TABLE}.png", f"(Flyer 2) {TABLE}.png",
                        f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png",
                        f"(Flyer 3) {FOLDER}.png"])

        self.assertEqual(found["flyer"], [f"(Flyer) {TABLE}.png", f"(Flyer 2) {TABLE}.png"])

    def test_the_folders_pages_serve_a_table_with_no_flyer_of_its_own(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png"])

        self.assertEqual(found["flyer"], [f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png"])

    def test_no_page_one_means_no_pages_and_no_flyer(self) -> None:
        medias = [f"(Flyer 2) {FOLDER}.png", "flyer 2.png"]
        entries = resolve_media_entries(f"/games/{FOLDER}", set(), set(medias), "table", TABLE)

        self.assertIsNone(entries["flyer"].path)
        self.assertEqual(_pages(medias), {})

    def test_numbered_pages_do_not_make_a_tier_resolve_over_a_lower_one(self) -> None:
        """Pages at the folder tier, page 1 at the fixed one: the fixed page 1 shows, and
        nothing of the folder's pages comes with it."""
        found = _pages([f"(Flyer 2) {FOLDER}.png", "flyer.png"])

        self.assertEqual(found, {})

    def test_two_files_for_one_number_are_picked_like_page_one(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png", f"(GameInfo 2) {FOLDER}.png",
                        f"(Flyer 2) {FOLDER}.jpg", f"(Flyer 2) {FOLDER}.png"])

        self.assertEqual(found["flyer"][1], f"(Flyer 2) {FOLDER}.png")

    def test_an_alt_token_takes_a_number(self) -> None:
        found = _pages([f"(GameInfo) {FOLDER}.png", f"(GameInfo 2) {FOLDER}.png"])

        self.assertEqual(found["flyer"], [f"(GameInfo) {FOLDER}.png",
                                          f"(GameInfo 2) {FOLDER}.png"])

    def test_a_pages_extension_need_not_match_page_one(self) -> None:
        found = _pages([f"(InstructionCard) {FOLDER}.png",
                        f"(InstructionCard 2) {FOLDER}.jpg"])

        self.assertEqual(found["instruction_card"][1], f"(InstructionCard 2) {FOLDER}.jpg")

    def test_matching_is_case_insensitive(self) -> None:
        found = _pages([f"(flyer) {FOLDER.lower()}.png", f"(FLYER 2) {FOLDER.lower()}.png"],
                       folder=FOLDER.lower())

        self.assertEqual(len(found["flyer"]), 2)

    def test_a_number_in_the_stem_is_not_a_page(self) -> None:
        """`(Flyer) Doom 2.png` is page 1 of the table Doom 2, never page 2 of Doom."""
        found = _pages([f"(Flyer) {FOLDER} 2.png", f"(Flyer) {FOLDER}.png"], stem=FOLDER)

        self.assertEqual(found, {})

    def test_the_root_is_read_as_well_as_medias(self) -> None:
        found = _pages([f"(Flyer) {FOLDER}.png"], root=[f"(Flyer 2) {FOLDER}.png"])

        self.assertEqual(found["flyer"][1], f"(Flyer 2) {FOLDER}.png")

    def test_no_other_kind_has_pages(self) -> None:
        found = _pages([f"(Wheel) {FOLDER}.png", f"(Wheel 2) {FOLDER}.png", "wheel 2.png"])

        self.assertEqual(found, {})

    def test_each_table_is_read_for_itself(self) -> None:
        listing = {f"(Flyer) {TABLE}.png", f"(Flyer 2) {TABLE}.png", f"(Flyer) {FOLDER}.png"}

        found = resolve_media_pages_by_table(f"/games/{FOLDER}", set(), listing,
                                             [f"{TABLE}.vpx", f"{FOLDER}.vpx"])

        self.assertEqual(sorted(found[f"{TABLE}.vpx".lower()]), ["flyer"])
        self.assertEqual(found[f"{FOLDER}.vpx".lower()], {})


class NamingTests(unittest.TestCase):
    def setUp(self) -> None:
        _writes_vpinfes_names(self)

    def test_a_page_is_read_back_with_its_number(self) -> None:
        self.assertEqual(spec_named(f"(Flyer 2) {FOLDER}.png"), SpecName("flyer", FOLDER, 2))
        self.assertEqual(spec_named(f"(RuleCard 12) {FOLDER}.jpg"),
                         SpecName("instruction_card", FOLDER, 12))

    def test_a_name_with_no_number_is_page_one(self) -> None:
        self.assertEqual(spec_named(f"(Flyer) {FOLDER}.png"), SpecName("flyer", FOLDER, 1))

    def test_a_number_after_the_token_of_a_kind_without_pages_is_a_stem(self) -> None:
        self.assertIsNone(spec_named(f"(Wheel 2) {FOLDER}.png"))

    def test_page_one_is_not_written_with_a_number(self) -> None:
        self.assertIsNone(spec_named(f"(Flyer 1) {FOLDER}.png"))

    def test_an_upload_named_for_a_page_is_that_kind(self) -> None:
        self.assertEqual(match_media_kind(f"(Flyer 2) {FOLDER}.png"), "flyer")
        self.assertEqual(match_media_kind(f"(GameHelp 3) {FOLDER}.png"), "instruction_card")
        self.assertIsNone(match_media_kind(f"(Topper 2) {FOLDER}.png"))

    def test_target_names_carry_the_number_inside_the_token(self) -> None:
        self.assertEqual(media_placement.target_name("flyer", TABLE, ".png"),
                         f"(Flyer) {TABLE}.png")
        self.assertEqual(media_placement.target_name("flyer", TABLE, ".png", 2),
                         f"(Flyer 2) {TABLE}.png")
        self.assertEqual(media_placement.target_name("flyer", FIXED, ".png", 2),
                         "flyer 2.png")

    def test_a_kind_without_pages_takes_none(self) -> None:
        with self.assertRaises(UnplaceableError):
            media_placement.target_name("wheel", FOLDER, ".png", 2)


class OnDiskTests(unittest.TestCase):
    def setUp(self) -> None:
        _writes_vpinfes_names(self)
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / FOLDER
        (self.root / "medias").mkdir(parents=True)
        self._made = 0

    def _source(self, ext: str = ".png") -> Path:
        self._made += 1
        path = self.root / f"upload{self._made}{ext}"
        path.write_bytes(f"bytes{self._made}".encode())
        return path

    def _flyer(self, *numbers: int, stem: str = FOLDER, ext: str = ".png") -> None:
        for number in numbers:
            media_placement.place(self.root, "flyer", stem, self._source(ext), number)

    def _medias(self) -> list[str]:
        return sorted(p.name for p in (self.root / "medias").iterdir())

    def _bytes(self, name: str) -> str:
        return (self.root / "medias" / name).read_bytes().decode()


class PlacingTests(OnDiskTests):
    def test_page_two_goes_beside_page_one(self) -> None:
        self._flyer(1, 2)

        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer) {FOLDER}.png"])

    def test_placing_page_one_leaves_the_other_pages_alone(self) -> None:
        self._flyer(1, 2)

        media_placement.place(self.root, "flyer", FOLDER, self._source(".jpg"), 1)

        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer) {FOLDER}.jpg"])

    def test_placing_a_page_replaces_that_page_only(self) -> None:
        self._flyer(1, 2, 3)

        media_placement.place(self.root, "flyer", FOLDER, self._source(".jpg"), 2)

        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.jpg", f"(Flyer 3) {FOLDER}.png",
                                          f"(Flyer) {FOLDER}.png"])

    def test_the_highest_page_is_one_past_the_last(self) -> None:
        self._flyer(1, 2, 3, 4)

        media_placement.place(self.root, "flyer", FOLDER, self._source(), 5)

        self.assertEqual(media_placement.page_count(self.root, "flyer", FOLDER), 5)

    def test_beyond_that_is_refused_and_nothing_is_written(self) -> None:
        self._flyer(1, 2)
        before = self._medias()

        with self.assertRaises(UnplaceableError):
            media_placement.place(self.root, "flyer", FOLDER, self._source(), 4)
        self.assertEqual(self._medias(), before)

    def test_with_no_page_one_the_first_file_is_page_one(self) -> None:
        landed = media_placement.place(self.root, "flyer", FOLDER, self._source(), 3)

        self.assertEqual(landed.name, f"(Flyer) {FOLDER}.png")

    def test_a_page_after_a_gap_goes_after_the_last_number(self) -> None:
        self._flyer(1, 2)
        (self.root / "medias" / f"(Flyer 2) {FOLDER}.png").rename(
            self.root / "medias" / f"(Flyer 5) {FOLDER}.png")

        landed = media_placement.place(self.root, "flyer", FOLDER, self._source(), 3)

        self.assertEqual(landed.name, f"(Flyer 6) {FOLDER}.png")

    def test_displaced_names_what_a_page_would_take(self) -> None:
        self._flyer(1, 2)

        going = media_placement.displaced(self.root, "flyer", FOLDER, ".jpg", 2)

        self.assertEqual([p.name for p in going], [f"(Flyer 2) {FOLDER}.png"])
        self.assertEqual(media_placement.displaced(self.root, "flyer", FOLDER, ".jpg", 3), [])

    def test_a_kind_without_pages_refuses_page_two(self) -> None:
        with self.assertRaises(UnplaceableError):
            media_placement.place(self.root, "wheel", FOLDER, self._source(), 2)

    def test_the_fixed_tier_is_written_with_its_number(self) -> None:
        media_placement.place(self.root, "flyer", FIXED, self._source(), 1)

        media_placement.place(self.root, "flyer", FIXED, self._source(), 2)

        self.assertEqual(self._medias(), ["flyer 2.png", "flyer.png"])

    def test_page_one_is_found_at_the_tier_the_slot_shows(self) -> None:
        self.assertIsNone(media_placement.page_tier(self.root, "flyer", TABLE))
        self._flyer(1, stem=FIXED)
        self.assertEqual(media_placement.page_tier(self.root, "flyer", TABLE), FIXED)
        self._flyer(1, stem=FOLDER)
        self.assertEqual(media_placement.page_tier(self.root, "flyer", TABLE), FOLDER)
        self._flyer(1, stem=TABLE)
        self.assertEqual(media_placement.page_tier(self.root, "flyer", TABLE), TABLE)


class RemovingTests(OnDiskTests):
    def test_removing_the_kind_leaves_pages_that_show_nothing(self) -> None:
        for number in (2, 3):
            (self.root / "medias" / f"(Flyer {number}) {FOLDER}.png").write_bytes(b"x")

        self.assertEqual(media_placement.remove(self.root, "flyer", FOLDER), [])
        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer 3) {FOLDER}.png"])

    def test_removing_a_page_renumbers_the_ones_after_it(self) -> None:
        self._flyer(1, 2, 3, 4)
        third = self._bytes(f"(Flyer 3) {FOLDER}.png")
        fourth = self._bytes(f"(Flyer 4) {FOLDER}.png")

        gone = media_placement.remove(self.root, "flyer", FOLDER, 2)

        self.assertEqual(gone, [f"medias/(Flyer 2) {FOLDER}.png"])
        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer 3) {FOLDER}.png",
                                          f"(Flyer) {FOLDER}.png"])
        self.assertEqual(self._bytes(f"(Flyer 2) {FOLDER}.png"), third)
        self.assertEqual(self._bytes(f"(Flyer 3) {FOLDER}.png"), fourth)

    def test_removing_page_one_makes_page_two_the_new_page_one(self) -> None:
        self._flyer(1, 2)
        second = self._bytes(f"(Flyer 2) {FOLDER}.png")

        media_placement.remove(self.root, "flyer", FOLDER, 1)

        self.assertEqual(self._medias(), [f"(Flyer) {FOLDER}.png"])
        self.assertEqual(self._bytes(f"(Flyer) {FOLDER}.png"), second)

    def test_removing_the_kind_takes_every_page(self) -> None:
        self._flyer(1, 2, 3)
        self._flyer(1, stem=TABLE)

        gone = media_placement.remove(self.root, "flyer", FOLDER)

        self.assertEqual(len(gone), 3)
        self.assertEqual(self._medias(), [f"(Flyer) {TABLE}.png"])

    def test_removable_names_what_remove_takes(self) -> None:
        self._flyer(1, 2)

        self.assertEqual(len(media_placement.removable(self.root, "flyer", FOLDER)), 2)
        self.assertEqual([p.name for p in media_placement.removable(self.root, "flyer",
                                                                    FOLDER, 2)],
                         [f"(Flyer 2) {FOLDER}.png"])

    def test_the_folder_tier_reaches_the_default_only_where_it_holds_nothing(self) -> None:
        (self.root / "medias" / "wheel.png").write_bytes(b"x")
        other = "(Wheel) Other.png"
        (self.root / "medias" / other).write_bytes(b"x")

        self.assertEqual(media_placement.reached(self.root, "wheel", FOLDER), media_placement.FIXED)
        self.assertEqual(media_placement.reached(self.root, "wheel", "Other"), "Other")
        media_placement.place(self.root, "wheel", FOLDER, self._source())
        self.assertEqual(media_placement.reached(self.root, "wheel", FOLDER), FOLDER)

    def test_the_default_reached_is_the_one_the_playfield_variant_names(self) -> None:
        (self.root / "medias" / "table.png").write_bytes(b"x")

        self.assertEqual(media_placement.reached(self.root, "playfield", FOLDER, "table"),
                         media_placement.FIXED)
        self.assertEqual(media_placement.reached(self.root, "playfield", FOLDER, "fss"), FOLDER)

    def test_a_page_that_is_not_there_is_refused(self) -> None:
        self._flyer(1, 2)

        with self.assertRaises(UnplaceableError):
            media_placement.remove(self.root, "flyer", FOLDER, 3)

    def test_a_gap_is_closed_when_a_page_goes(self) -> None:
        self._flyer(1, 2, 3)
        (self.root / "medias" / f"(Flyer 3) {FOLDER}.png").rename(
            self.root / "medias" / f"(Flyer 7) {FOLDER}.png")

        media_placement.remove(self.root, "flyer", FOLDER, 1)

        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer) {FOLDER}.png"])

    def test_an_alt_token_keeps_its_spelling_when_renumbered(self) -> None:
        self._flyer(1, 2)
        (self.root / "medias" / f"(Flyer 2) {FOLDER}.png").rename(
            self.root / "medias" / f"(GameInfo 3) {FOLDER}.png")

        media_placement.remove(self.root, "flyer", FOLDER, 1)

        self.assertEqual(self._medias(), [f"(GameInfo) {FOLDER}.png"])


class MovingTests(OnDiskTests):
    def test_a_page_swaps_numbers_with_its_neighbour(self) -> None:
        self._flyer(1, 2, 3)
        first = self._bytes(f"(Flyer) {FOLDER}.png")
        second = self._bytes(f"(Flyer 2) {FOLDER}.png")

        landed = media_placement.move_page(self.root, "flyer", FOLDER, 2, 1)

        self.assertEqual(landed.name, f"(Flyer) {FOLDER}.png")
        self.assertEqual(self._bytes(f"(Flyer) {FOLDER}.png"), second)
        self.assertEqual(self._bytes(f"(Flyer 2) {FOLDER}.png"), first)
        self.assertEqual(len(self._medias()), 3)

    def test_the_swap_keeps_each_pages_extension(self) -> None:
        self._flyer(1)
        self._flyer(2, ext=".jpg")

        media_placement.move_page(self.root, "flyer", FOLDER, 1, 2)

        self.assertEqual(self._medias(), [f"(Flyer 2) {FOLDER}.png", f"(Flyer) {FOLDER}.jpg"])

    def test_a_page_past_the_ends_is_refused(self) -> None:
        self._flyer(1, 2)

        with self.assertRaises(UnplaceableError):
            media_placement.move_page(self.root, "flyer", FOLDER, 2, 3)


class ScanTests(unittest.TestCase):
    def test_a_scanned_game_records_its_pages_for_the_game_and_for_each_table(self) -> None:
        import configparser

        from common.games.game_parser import GameParser
        from tests.support.library import write_game

        with TemporaryDirectory() as tmp:
            write_game(tmp, FOLDER, medias={f"(Flyer) {FOLDER}.png": b"1",
                                            f"(Flyer 2) {FOLDER}.png": b"2",
                                            f"(Wheel) {FOLDER}.png": b"w"})
            config = configparser.ConfigParser()
            config.read_dict({"Settings": {"gamerootdir": tmp}, "Media": {}})
            parser = GameParser(tmp, config)  # type: ignore[arg-type]

            game = parser.get_game(0)

        media_pages = game.media_pages
        media_pages_by_table = game.media_pages_by_table
        assert media_pages is not None
        assert media_pages_by_table is not None
        names = [Path(path).name for path in media_pages["flyer"]]
        self.assertEqual(names, [f"(Flyer) {FOLDER}.png", f"(Flyer 2) {FOLDER}.png"])
        self.assertEqual(set(media_pages), {"flyer"})
        self.assertEqual(media_pages_by_table[f"{FOLDER}.vpx".lower()]["flyer"],
                         media_pages["flyer"])


class LookupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        root = Path(self._dir.name)
        self.files = [root / name for name in ("a.png", "b.png", "c.png")]
        for path in self.files:
            path.write_bytes(b"x")
        paths = [str(path) for path in self.files]
        self.game = SimpleNamespace(
            game_dir_name=FOLDER, meta_config={}, flyer_image_path=paths[0],
            media_pages={"flyer": paths}, media_pages_by_table=None,
            media_by_table=None, full_path_vpx_file="",
        )
        self.games = [self.game]
        patch = mock.patch.object(media_lookup, "game_for_table", return_value=self.game)
        patch.start()
        self.addCleanup(patch.stop)

    def test_page_one_is_the_file_a_kind_has_always_answered_with(self) -> None:
        self.assertEqual(media_lookup.media_path(self.games, "t", "flyer"), self.files[0])

    def test_a_later_page_is_its_own_file_and_one_past_the_last_is_none(self) -> None:
        self.assertEqual(media_lookup.media_path(self.games, "t", "flyer", 2), self.files[1])
        self.assertIsNone(media_lookup.media_path(self.games, "t", "flyer", 4))

    def test_every_page_is_listed_in_order(self) -> None:
        self.assertEqual(media_lookup.media_pages(self.games, "t", "flyer"), self.files)

    def test_a_kind_without_pages_has_page_one_only(self) -> None:
        self.game.wheel_image_path = str(self.files[0])

        self.assertEqual(media_lookup.media_pages(self.games, "t", "wheel"), [self.files[0]])
        self.assertIsNone(media_lookup.media_path(self.games, "t", "wheel", 2))


if __name__ == "__main__":
    unittest.main()
