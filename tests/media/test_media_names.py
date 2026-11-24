"""Protects: (a) a person's flyer or card keeps the name it has when it is replaced.

A new flyer or instruction card is spelled the way the library chose, Visual Pinball's
name or VPinFE's. A file already at the place keeps its spelling, so a Replace and a new
page never rename what a person or another frontend put there.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.games import library_policy, media_placement

FOLDER = "Attack from Mars (Bally 1995)"
VPS = {"flyer_name": "visual_pinball", "instruction_card_name": "visual_pinball"}
OWN = {"flyer_name": "vpinfe", "instruction_card_name": "vpinfe"}


def _choosing(values: dict):
    return mock.patch.object(library_policy, "_config_values", return_value=values)


class ChoiceTests(unittest.TestCase):
    def test_a_library_that_chose_nothing_gets_visual_pinballs_names(self) -> None:
        with _choosing({}):
            self.assertEqual(library_policy.media_token("flyer"), "(GameInfo)")
            self.assertEqual(library_policy.media_token("instruction_card"), "(GameHelp)")

    def test_each_kind_holds_its_own_choice(self) -> None:
        with _choosing({"flyer_name": "vpinfe", "instruction_card_name": "visual_pinball"}):
            self.assertEqual(library_policy.media_token("flyer"), "(Flyer)")
            self.assertEqual(library_policy.media_token("instruction_card"), "(GameHelp)")

    def test_an_unknown_choice_is_the_default(self) -> None:
        with _choosing({"flyer_name": "nonesuch"}):
            self.assertEqual(library_policy.media_token("flyer"), "(GameInfo)")

    def test_a_kind_with_no_choice_has_none(self) -> None:
        self.assertIsNone(library_policy.media_token("wheel"))


class WritingTests(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name) / FOLDER
        (self.root / "medias").mkdir(parents=True)
        self._made = 0

    def _source(self, ext: str = ".png") -> Path:
        self._made += 1
        path = self.root / f"upload{self._made}{ext}"
        path.write_bytes(b"x")
        return path

    def _medias(self) -> list[str]:
        return sorted(p.name for p in (self.root / "medias").iterdir())

    def _place(self, kind: str, page: int = 1, ext: str = ".png") -> Path:
        return media_placement.place(self.root, kind, FOLDER, self._source(ext), page)

    def test_a_new_file_takes_the_libraries_choice(self) -> None:
        for choice, flyer, card in ((VPS, "(GameInfo)", "(GameHelp)"),
                                    (OWN, "(Flyer)", "(InstructionCard)")):
            with self.subTest(choice=choice), _choosing(choice):
                self.assertEqual(self._place("flyer").name, f"{flyer} {FOLDER}.png")
                self.assertEqual(self._place("instruction_card").name, f"{card} {FOLDER}.png")
                for path in (self.root / "medias").iterdir():
                    path.unlink()

    def test_a_replace_keeps_the_name_already_there(self) -> None:
        for held, other in (("(GameInfo)", OWN), ("(Flyer)", VPS)):
            with self.subTest(held=held), _choosing(other):
                (self.root / "medias" / f"{held} {FOLDER}.png").write_bytes(b"old")

                landed = self._place("flyer", ext=".jpg")

                self.assertEqual(landed.name, f"{held} {FOLDER}.jpg")
                self.assertEqual(self._medias(), [landed.name])
                landed.unlink()

    def test_a_new_page_takes_the_name_of_page_one(self) -> None:
        (self.root / "medias" / f"(GameHelp) {FOLDER}.png").write_bytes(b"old")

        with _choosing(OWN):
            landed = self._place("instruction_card", page=2)

        self.assertEqual(landed.name, f"(GameHelp 2) {FOLDER}.png")

    def test_a_choice_changed_later_renames_nothing(self) -> None:
        with _choosing(VPS):
            first = self._place("flyer")
        with _choosing(OWN):
            again = self._place("flyer")

        self.assertEqual(first, again)
        self.assertEqual(self._medias(), [f"(GameInfo) {FOLDER}.png"])

    def test_a_name_we_do_not_write_becomes_the_choice(self) -> None:
        (self.root / "medias" / f"(RuleCard) {FOLDER}.png").write_bytes(b"old")

        with _choosing(OWN):
            landed = self._place("instruction_card")

        self.assertEqual(landed.name, f"(InstructionCard) {FOLDER}.png")
        self.assertEqual(self._medias(), [landed.name])

    def test_the_shared_default_has_no_choice(self) -> None:
        with _choosing(VPS):
            landed = media_placement.place(self.root, "flyer", media_placement.FIXED,
                                           self._source())

        self.assertEqual(landed.name, "flyer.png")


if __name__ == "__main__":
    unittest.main()
