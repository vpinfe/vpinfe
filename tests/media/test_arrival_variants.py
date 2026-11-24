"""Several files for one place: a choice among variants, and pages in order.

Two files never both write one destination. Where two or more claims land on one
single-valued place they are a group, and nothing is written for it until one is picked
(or "none of these" is). A paged kind is not a group: its files are the pages.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games import media_placement
from common.uploads import arrival
from common.uploads.asset_import_service import execute_import_plan, select_plan_items
from tests.support import drops
from tests.support.uploads import make_zip

GAME = "Medieval Madness"
FIRST = "(Wheel) Medieval Madness.png"


class _Variants(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game = self.tmp / GAME
        self.game.mkdir()
        (self.game / f"{GAME}.vpx").write_bytes(b"x")

    def zip(self, names: list[str]) -> Path:
        make_zip(self.tmp / "drop.zip", names)
        return self.tmp / "drop.zip"

    def read(self, source: Path, **target):
        return arrival.read(source, game_dir=self.game, **target)


class GroupTests(_Variants):
    def test_three_wheels_are_one_group_of_three_with_no_pick(self) -> None:
        result = self.read(drops.wheel_zip(self.tmp, 3))
        (group,) = result.groups
        self.assertEqual((group.kind, group.media_kind, len(group.candidates)),
                         ("media", "wheel", 3))
        self.assertEqual(result.claims, ())

    def test_the_one_candidate_named_for_the_slot_and_the_game_is_preselected(self) -> None:
        result = self.read(self.zip([FIRST, "(Wheel) Pinbot.png", "Wheel 2.png"]))
        (group,) = result.groups
        self.assertEqual(group.preselected, FIRST)

    def test_two_named_for_the_slot_and_the_game_preselect_nothing(self) -> None:
        result = self.read(self.zip([FIRST, "(Wheel) Medieval Madness.jpg", "Wheel 2.png"]))
        (group,) = result.groups
        self.assertEqual(group.preselected, "")

    def test_unnamed_files_on_a_declared_slot_are_a_group_with_nothing_preselected(self) -> None:
        result = self.read(self.zip(["a.png", "b.png", "c.png"]), media_kind="wheel")
        (group,) = result.groups
        self.assertEqual((len(group.candidates), group.preselected), (3, ""))
        self.assertEqual({c.basis for c in group.candidates}, {"declared"})

    def test_wheels_and_backglasses_in_one_archive_are_two_independent_groups(self) -> None:
        result = self.read(self.zip(["(Wheel) A.png", "(Wheel) B.png",
                                     "(Backglass) A.png", "(Backglass) B.png"]))
        self.assertEqual(sorted(group.media_kind for group in result.groups),
                         ["backglass", "wheel"])
        self.assertEqual({len(group.candidates) for group in result.groups}, {2})

    def test_a_candidate_carries_its_pixel_size_loose_and_inside_an_archive(self) -> None:
        folder = self.tmp / "loose"
        drops.png(folder / "(Wheel) A.png", 120, 60)
        drops.png(folder / "(Wheel) B.png", 300, 150)
        loose = self.read(folder)
        archived = self.read(drops.wheel_zip(self.tmp, 3))
        self.assertEqual(sorted((c.width, c.height) for c in loose.groups[0].candidates),
                         [(120, 60), (300, 150)])
        self.assertEqual(sorted((c.width, c.height) for c in archived.groups[0].candidates),
                         [(100, 50), (200, 100), (300, 150)])


class PagesTests(_Variants):
    def _pages(self, result) -> list[tuple[str, int, bool]]:
        return sorted((claim.members[0], claim.page, claim.replaces) for claim in result.claims)

    def test_flyers_are_pages_not_variants_taken_in_name_order(self) -> None:
        reversed_zip = self.zip(["Flyer 3.png", "Flyer 2.png", "Flyer.png"])
        result = self.read(reversed_zip)
        self.assertEqual(result.groups, ())
        self.assertEqual(self._pages(result),
                         [("Flyer 2.png", 2, False), ("Flyer 3.png", 3, False),
                          ("Flyer.png", 1, False)])

    def test_a_drop_on_page_two_replaces_two_and_three_and_adds_four(self) -> None:
        for page in (1, 2, 3):
            drops.png(self.game / "medias"
                         / media_placement.target_name("flyer", GAME, ".png", page))
        result = self.read(self.zip(["A.png", "B.png", "C.png"]), media_kind="flyer", page=2)
        self.assertEqual(self._pages(result),
                         [("A.png", 2, True), ("B.png", 3, True), ("C.png", 4, False)])

    def test_a_drop_on_a_paged_kinds_cell_starts_at_page_one(self) -> None:
        result = self.read(self.zip(["A.png", "B.png", "C.png"]), media_kind="flyer")
        self.assertEqual([claim.page for claim in sorted(result.claims,
                                                        key=lambda c: c.members[0])],
                         [1, 2, 3])


class PageOrderTests(_Variants):
    """Pages follow the names, however the pool came to hold them, and a page left out does
    not leave a gap."""

    def _written(self, built) -> dict[str, int]:
        return {item.member: item.page for item in built.items}

    def test_a_file_assigned_after_the_first_reading_takes_its_place_in_the_order(self) -> None:
        source = self.zip(["Flyer.png", "Flyer 3.png", "Another.png"])
        found = self.read(source)
        found = arrival.apply(found, [{"member": "Another.png", "kind": "media",
                                       "media_kind": "flyer"}])
        built = arrival.plan(found, game_dir=self.game)
        self.assertEqual(self._written(built),
                         {"Another.png": 1, "Flyer.png": 2, "Flyer 3.png": 3})

    def test_a_page_unticked_in_the_middle_closes_the_gap_and_the_rest_are_written(self) -> None:
        source = drops.flyer_zip(self.tmp, 3)
        built = arrival.plan(self.read(source), game_dir=self.game)
        keep = [index for index, item in enumerate(built.items) if item.member != "Flyer 2.png"]
        narrowed = select_plan_items(built, keep)
        self.assertEqual(sorted(item.page for item in narrowed.items), [1, 2])
        execute_import_plan(narrowed, source)
        self.assertEqual(media_placement.page_count(self.game, "flyer", GAME), 2)

    def test_the_first_page_unticked_still_starts_at_the_page_the_drop_began_at(self) -> None:
        source = drops.flyer_zip(self.tmp, 3)
        built = arrival.plan(self.read(source), game_dir=self.game)
        keep = [index for index, item in enumerate(built.items) if item.member != "Flyer.png"]
        narrowed = select_plan_items(built, keep)
        self.assertEqual(sorted(item.page for item in narrowed.items), [1, 2])


class OneWriteTests(_Variants):
    """Two files never both write one destination, and a pick writes only its own."""

    def _import(self, source: Path, **plan):
        result = self.read(source)
        built = arrival.plan(result, game_dir=self.game, **plan)
        execute_import_plan(built, source)
        return built

    def test_an_unanswered_group_is_refused_and_writes_nothing(self) -> None:
        source = drops.wheel_zip(self.tmp, 3)
        with self.assertRaises(arrival.ChooseOneError):
            self._import(source)
        self.assertFalse((self.game / "medias").exists())

    def test_two_files_never_both_write_one_destination(self) -> None:
        source = drops.wheel_zip(self.tmp, 3)
        result = self.read(source)
        key = result.groups[0].key
        built = arrival.plan(result, game_dir=self.game, picks={key: FIRST})
        destinations = [item.destination for item in built.items]
        self.assertEqual(len(destinations), len(set(destinations)))

    def test_a_pick_writes_only_that_file(self) -> None:
        source = drops.wheel_zip(self.tmp, 3)
        chosen = "(Wheel) Medieval Madness 2.png"
        key = self.read(source).groups[0].key
        self._import(source, picks={key: chosen})
        written = sorted(path.name for path in (self.game / "medias").iterdir())
        self.assertEqual(written, [media_placement.target_name("wheel", GAME, ".png")])
        with open(self.game / "medias" / written[0], "rb") as handle:
            size = drops.png_size(handle)
        self.assertEqual(size, (200, 100))

    def test_two_readmes_that_would_both_be_the_game_readme_write_one(self) -> None:
        source = self.zip(["a/readme.txt", "b/readme.txt"])
        built = arrival.plan(self.read(source), game_dir=self.game)
        self.assertEqual(len(built.items), 1)
        (refused,) = built.blocked
        self.assertEqual(refused.asset.entries[0].arcname, "b/readme.txt")

    def test_an_extras_group_and_a_group_for_the_same_place_have_their_own_keys(self) -> None:
        names = ["(Wheel) A.png"] + [f"(Backglass) {n}.png" for n in "ABCD"]
        found = self.read(self.zip(names), media_kind="wheel")
        found = arrival.apply(found, [
            {"member": f"(Backglass) {n}.png", "kind": "media", "media_kind": "backglass"}
            for n in "AB"])
        backglass = [group for group in found.groups if group.media_kind == "backglass"]
        self.assertEqual(sorted(group.extra for group in backglass), [False, True])
        self.assertEqual(len({group.key for group in backglass}), 2)

    def test_none_of_these_writes_nothing_and_is_a_complete_answer(self) -> None:
        source = drops.wheel_zip(self.tmp, 3)
        key = self.read(source).groups[0].key
        built = arrival.plan(self.read(source), game_dir=self.game, picks={key: ""})
        self.assertEqual(built.items, ())


if __name__ == "__main__":
    unittest.main()
