"""What an arrival is made of: each file claimed once, by the best answer there is.

A drop is read file by file. A file the target named is claimed for what it named; any
other is claimed by its type, then its name; a file nothing claimed is offered places or
left out with a reason. Every file lands in exactly one of those.
"""

from __future__ import annotations

import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from common.games import media_placement
from common.media.media_specs import MEDIA_SPECS, media_family
from common.uploads import arrival
from tests.media.test_arrival_scope import _Games
from tests.support import drops
from tests.support.uploads import make_zip

GAME = "Medieval Madness"


class _Arrivals(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)
        self.game = self.tmp / GAME
        self.game.mkdir()

    def zip(self, names: list[str]) -> Path:
        make_zip(self.tmp / "drop.zip", names)
        return self.tmp / "drop.zip"

    def read(self, source: Path, **target):
        return arrival.read(source, **target)

    def claimed(self, result) -> dict[str, tuple[str, str, str]]:
        """What each claimed member was claimed as: (kind, media kind, basis)."""
        return {claim.members[0]: (claim.kind, claim.media_kind, claim.basis)
                for claim in result.claims}


class ClaimTests(_Arrivals):
    def test_a_file_named_for_a_slot_is_claimed_for_it_on_basis_named(self) -> None:
        result = self.read(self.zip(["(Wheel) Medieval Madness.png"]))
        self.assertEqual(self.claimed(result),
                         {"(Wheel) Medieval Madness.png": ("media", "wheel", "named")})

    def test_an_unnamed_image_on_a_declared_slot_is_claimed_declared(self) -> None:
        picture = drops.png(self.tmp / "IMG_0001.png")
        result = self.read(picture, game_dir=self.game, media_kind="wheel")
        self.assertEqual(self.claimed(result),
                         {"IMG_0001.png": ("media", "wheel", "declared")})

    def test_an_unnamed_image_with_no_target_offers_every_image_slot_empty_ones_first(self) -> None:
        wheel = media_placement.target_name("wheel", GAME, ".png")
        drops.png(self.game / "medias" / wheel)
        result = self.read(drops.png(self.tmp / "IMG_0001.png"), game_dir=self.game)
        self.assertEqual(result.claims, ())
        (loose,) = result.unclaimed
        slots = [one.media_kind for one in loose.choices if one.kind == "media"]
        image_slots = {spec.kind for spec in MEDIA_SPECS
                       if spec.token and media_family(spec.kind) == "image"}
        self.assertEqual(set(slots), image_slots)
        self.assertEqual(slots[-1], "wheel")
        self.assertFalse(any(one.suggested for one in loose.choices))

    def test_a_keyword_must_be_a_whole_word(self) -> None:
        for name in ("Cabaret.png", "Pfister.png", "Tablet.png"):
            with self.subTest(name=name):
                result = self.read(self.zip([name]))
                self.assertEqual(result.claims, ())
                self.assertEqual([one.member for one in result.unclaimed], [name])
        for name, slot in (("Table.png", "playfield"), ("MM_pf.png", "playfield"),
                           ("Wheel 2.png", "wheel")):
            with self.subTest(name=name):
                result = self.read(self.zip([name]))
                self.assertEqual(self.claimed(result), {name: ("media", slot, "named")})

    def test_an_archive_is_read_member_by_member(self) -> None:
        result = self.read(drops.pack_zip(self.tmp))
        found = {(kind, media_kind) for kind, media_kind, _basis in self.claimed(result).values()}
        self.assertEqual(found, {("table", ""), ("backglass", ""), ("ini", ""), ("pov", ""),
                                 ("readme", ""), ("media", "wheel"), ("media", "backglass")})

    def test_a_wrapper_folder_and_os_junk_are_ignored_and_not_counted(self) -> None:
        names = ["Wrapper/(Wheel) Medieval Madness.png", "Wrapper/.DS_Store",
                 "__MACOSX/Wrapper/._x", "Thumbs.db", "desktop.ini", "Wrapper/._y"]
        result = self.read(self.zip(names))
        self.assertEqual(drops.accounted(result), ["Wrapper/(Wheel) Medieval Madness.png"])
        self.assertEqual(result.left_out, ())

    def test_the_parts_of_a_pack_are_named_for_the_table_in_the_pack(self) -> None:
        claimed = self.claimed(self.read(drops.pack_zip(self.tmp)))
        for member in ("Medieval Madness.directb2s", "Medieval Madness.ini",
                       "Medieval Madness.pov"):
            self.assertEqual(claimed[member][2], "named", member)

    def test_every_member_lands_in_exactly_one_place(self) -> None:
        sources = {
            "pack": drops.pack_zip(self.tmp),
            "wheels": drops.wheel_zip(self.tmp, 3),
            "flyers": drops.flyer_zip(self.tmp, 3),
            "mixed": drops.mixed_folder(self.tmp),
        }
        targets: tuple[dict[str, str], ...] = (
            {}, {"media_kind": "wheel"}, {"media_kind": "backglass"})
        for name, source in sources.items():
            for target in targets:
                with self.subTest(source=name, target=target):
                    result = self.read(source, game_dir=self.game, **target)
                    self.assertEqual(drops.accounted(result), drops.members(source))

    def test_a_text_file_that_is_not_a_readme_is_offered_unticked_to_keep_as_it_is(self) -> None:
        result = self.read(self.zip(["notes.txt"]))
        (one,) = result.pool
        self.assertEqual(one.members, ("notes.txt",))
        self.assertEqual((one.basis, one.extra), ("other", True))
        self.assertEqual((), result.left_out)

    def test_a_file_no_rule_knows_is_offered_unticked_to_keep_as_it_is(self) -> None:
        result = self.read(self.zip(["mystery.xyz"]))
        (one,) = result.pool
        self.assertEqual((one.basis, one.extra), ("other", True))
        self.assertEqual((), result.left_out)


class LoneSidecarTests(_Games):
    def _lone(self, name: str) -> Path:
        path = self.tmp / name
        path.write_bytes(b"x" * 16)
        return path

    def test_a_lone_pov_is_claimed_and_asks_which_table(self) -> None:
        result = arrival.read(self._lone("anything.pov"), game_dir=self.many)
        self.assertEqual({(c.kind, c.basis) for c in result.claims}, {("pov", "named")})
        self.assertEqual([one["id"] for one in result.tables], ["t1", "t2", "t3"])
        self.assertEqual([one["id"] for one in result.tables if one["default"]], ["t1"])

    def test_a_lone_pov_in_a_game_with_one_table_asks_nothing(self) -> None:
        result = arrival.read(self._lone("anything.pov"), game_dir=self.solo)
        self.assertEqual([c.kind for c in result.claims], ["pov"])
        self.assertEqual(result.tables, ())

    def test_a_lone_vbs_is_never_placed_unseen(self) -> None:
        result = arrival.read(self._lone("fix.vbs"), game_dir=self.many)
        self.assertEqual(result.claims, ())
        (one,) = result.unclaimed
        labels = [choice.label for choice in one.choices]
        self.assertEqual(labels, ["Script for Alpha", "Script for Bravo", "Script for Charlie",
                                  "Skip"])
        self.assertFalse(any(choice.suggested for choice in one.choices))


class DeclaredSlotTests(_Arrivals):
    def test_a_declared_slot_takes_an_archive_holding_one_fitting_image(self) -> None:
        result = self.read(self.zip(["IMG_0001.png"]), game_dir=self.game, media_kind="wheel")
        self.assertEqual(self.claimed(result),
                         {"IMG_0001.png": ("media", "wheel", "declared")})
        self.assertEqual(result.refusal, "")

    def test_a_folder_and_an_archive_are_taken_the_same_way(self) -> None:
        folder = self.tmp / "folder"
        drops.png(folder / "IMG_0001.png")
        as_folder = self.read(folder, game_dir=self.game, media_kind="wheel")
        as_archive = self.read(self.zip(["IMG_0001.png"]), game_dir=self.game,
                               media_kind="wheel")
        self.assertEqual(self.claimed(as_folder), self.claimed(as_archive))

    def test_what_does_not_fit_the_slot_is_offered_beside_it_unticked(self) -> None:
        result = self.read(self.zip(["IMG_0001.png", "backglass.mp4"]), game_dir=self.game,
                           media_kind="wheel")
        by_member = {claim.members[0]: claim for claim in result.claims}
        self.assertEqual((by_member["IMG_0001.png"].media_kind, by_member["IMG_0001.png"].extra),
                         ("wheel", False))
        self.assertEqual((by_member["backglass.mp4"].media_kind, by_member["backglass.mp4"].extra),
                         ("backglass_video", True))

    def test_a_declared_slot_with_nothing_that_fits_refuses_in_words(self) -> None:
        result = self.read(self.zip(["clip.mp4", "mystery.xyz"]), game_dir=self.game,
                           media_kind="wheel")
        self.assertEqual(result.refusal, "Nothing here fits Wheel (a picture)")
        self.assertEqual([claim for claim in result.claims if not claim.extra], [])

    def test_another_slots_picture_is_an_extra_not_forced_into_the_slot(self) -> None:
        result = self.read(self.zip(["IMG_0001.png", "bg.png"]), game_dir=self.game,
                           media_kind="wheel")
        by_member = {claim.members[0]: claim for claim in result.claims}
        self.assertEqual((by_member["bg.png"].media_kind, by_member["bg.png"].extra),
                         ("backglass", True))

    def test_a_declared_folder_kind_takes_the_whole_arrival_with_no_marker(self) -> None:
        folder = self.tmp / "snd"
        for name in ("a.ogg", "b.ogg"):
            (folder / name).parent.mkdir(parents=True, exist_ok=True)
            (folder / name).write_bytes(b"x" * 16)
        result = self.read(folder, game_dir=self.game, asset_kind="altsound", rom_name="mm")
        (claim,) = result.claims
        self.assertEqual((claim.kind, claim.basis, sorted(claim.members)),
                         ("altsound", "declared", ["a.ogg", "b.ogg"]))

    def test_a_declared_rom_keyed_kind_for_a_game_with_no_rom_is_blocked_and_says_so(self) -> None:
        folder = self.tmp / "snd"
        folder.mkdir()
        (folder / "a.ogg").write_bytes(b"x" * 16)
        result = self.read(folder, game_dir=self.game, asset_kind="altsound", rom_name="")
        self.assertEqual(result.claims, ())
        (one,) = result.left_out
        self.assertEqual(one.reason_key, "error.uploads.no_rom")

    def test_several_files_for_a_kind_that_holds_one_are_a_group(self) -> None:
        names = ["a.pov", "b.pov"]
        result = self.read(self.zip(names), game_dir=self.game, asset_kind="pov")
        (group,) = result.groups
        self.assertEqual((group.kind, sorted(c.member for c in group.candidates)),
                         ("pov", names))

    def test_a_vni_set_is_planned_one_file_each_with_every_member_known(self) -> None:
        names = ["mm.vni", "mm.pal"]
        result = self.read(self.zip(names), game_dir=self.game, asset_kind="altcolor_vni",
                           rom_name="mm")
        built = arrival.plan(result, game_dir=self.game, rom_name="mm")
        self.assertEqual(sorted(item.member for item in built.items), sorted(names))

    def test_a_declared_readme_takes_a_lone_file_of_any_suffix(self) -> None:
        notes = self.tmp / "release.nfo"
        notes.write_bytes(b"x" * 16)
        result = self.read(notes, game_dir=self.game, asset_kind="readme")
        (claim,) = result.claims
        self.assertEqual((claim.kind, claim.basis), ("readme", "declared"))
        built = arrival.plan(result, game_dir=self.game)
        self.assertEqual(Path(built.items[0].destination).name, "readme.nfo")

    def test_a_declared_readme_in_an_archive_takes_the_text_in_it(self) -> None:
        result = self.read(self.zip(["notes.txt", "table.png"]), game_dir=self.game,
                           asset_kind="readme")
        self.assertEqual(self.claimed(result)["notes.txt"], ("readme", "", "declared"))
        self.assertTrue(next(c for c in result.claims if c.members == ("table.png",)).extra)

    def test_a_folder_with_nothing_in_it_is_refused_for_a_whole_folder_kind(self) -> None:
        folder = self.tmp / "empty"
        folder.mkdir()
        (folder / ".DS_Store").write_bytes(b"x")
        result = self.read(folder, game_dir=self.game, asset_kind="altsound", rom_name="mm")
        self.assertEqual(result.claims, ())
        self.assertNotEqual(result.refusal, "")


class ShapeSuggestionTests(_Arrivals):
    """An unnamed picture with no declared slot: its shape orders the places and, when it
    suits one slot only, names it."""

    def offered(self, source: Path, **target) -> tuple[list[str], list[str]]:
        """The media slots offered for the one unclaimed file, in order, and the ones marked
        suggested."""
        (loose,) = self.read(source, game_dir=self.game, **target).unclaimed
        slots = [one.media_kind for one in loose.choices if one.kind == "media"]
        return slots, [one.media_kind for one in loose.choices if one.suggested]

    def picture(self, width: int, height: int, alpha: bool = False,
                name: str = "IMG_0001.png") -> Path:
        path = self.tmp / name
        path.write_bytes(drops.png_bytes(width, height, alpha=alpha))
        return path

    def test_a_see_through_picture_is_suggested_as_a_wheel_and_offered_first(self) -> None:
        slots, suggested = self.offered(self.picture(700, 331, alpha=True))
        self.assertEqual(["wheel"], suggested)
        self.assertEqual("wheel", slots[0])

    def test_a_picture_the_shape_of_a_dmd_is_suggested_as_the_scoreview(self) -> None:
        slots, suggested = self.offered(self.picture(1080, 542))
        self.assertEqual(["scoreview"], suggested)
        self.assertEqual("scoreview", slots[0])

    def test_a_widescreen_picture_leads_with_what_it_could_be_and_suggests_nothing(self) -> None:
        slots, suggested = self.offered(self.picture(1920, 1080))
        self.assertEqual([], suggested)
        self.assertEqual(["backglass", "playfield"], slots[:2])

    def test_a_picture_no_rule_fits_keeps_the_plain_list_and_suggests_nothing(self) -> None:
        plain, _ = self.offered(self.picture(800, 800))
        slots, suggested = self.offered(self.picture(800, 800, name="IMG_0002.png"))
        self.assertEqual([], suggested)
        self.assertEqual(plain, slots)

    def test_a_slot_the_game_already_has_is_not_suggested(self) -> None:
        wheel = media_placement.target_name("wheel", GAME, ".png")
        drops.png(self.game / "medias" / wheel)
        slots, suggested = self.offered(self.picture(700, 331, alpha=True))
        self.assertEqual([], suggested)
        self.assertEqual("wheel", slots[-1])

    def test_a_file_that_is_not_a_picture_is_offered_the_plain_list(self) -> None:
        path = self.tmp / "IMG_0001.png"
        path.write_bytes(b"x" * 16)
        _, suggested = self.offered(path)
        self.assertEqual([], suggested)

    def test_a_picture_inside_an_archive_is_read_the_same_way(self) -> None:
        archive = self.tmp / "drop.zip"
        with zipfile.ZipFile(archive, "w") as held:
            held.writestr("IMG_0001.png", drops.png_bytes(700, 331, alpha=True))
        slots, suggested = self.offered(archive)
        self.assertEqual(["wheel"], suggested)
        self.assertEqual("wheel", slots[0])

    def test_two_pictures_that_suit_one_slot_are_neither_suggested_for_it(self) -> None:
        archive = self.tmp / "drop.zip"
        with zipfile.ZipFile(archive, "w") as held:
            held.writestr("IMG_0001.png", drops.png_bytes(700, 331, alpha=True))
            held.writestr("IMG_0002.png", drops.png_bytes(500, 200, alpha=True))
        result = self.read(archive, game_dir=self.game)
        self.assertEqual(2, len(result.unclaimed))
        self.assertEqual([], [one for loose in result.unclaimed for one in loose.choices
                              if one.suggested])

    def test_a_slot_the_drop_already_names_is_not_suggested(self) -> None:
        archive = self.tmp / "drop.zip"
        with zipfile.ZipFile(archive, "w") as held:
            held.writestr(f"(Wheel) {GAME}.png", drops.png_bytes(400, 200))
            held.writestr("IMG_0001.png", drops.png_bytes(700, 331, alpha=True))
        result = self.read(archive, game_dir=self.game)
        (loose,) = result.unclaimed
        self.assertEqual([], [one for one in loose.choices if one.suggested])

    def test_a_suggestion_is_never_a_claim(self) -> None:
        result = self.read(self.picture(700, 331, alpha=True), game_dir=self.game)
        self.assertEqual((), result.claims)
        self.assertEqual(1, len(result.unclaimed))


if __name__ == "__main__":
    unittest.main()
