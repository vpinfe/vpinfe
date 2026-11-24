"""The Match group's build row carries VPS's note on a mod."""

from __future__ import annotations

import unittest

from manager.workbench import game_match


class TheNoteOnALinkedMod(unittest.TestCase):
    def test_a_mod_shows_what_vps_says_it_changes(self) -> None:
        for release in ({"comment": " Brighter inserts ", "mod_of": {"vps_file_id": "x"}},
                        {"comment": " Brighter inserts ", "features": ["MOD"]}):
            with self.subTest(release=release):
                self.assertEqual(("Brighter inserts",), game_match._mod_note(release))

    def test_a_plain_release_and_a_mod_without_a_note_show_none(self) -> None:
        self.assertEqual((), game_match._mod_note({"comment": "Final release"}))
        self.assertEqual((), game_match._mod_note({"comment": " ", "features": ["MOD"]}))
        self.assertEqual((), game_match._mod_note({}))


if __name__ == "__main__":
    unittest.main()
