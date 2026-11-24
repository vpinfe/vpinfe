"""What the catalog offers for a game's empty media slots.

The two vocabularies share names for different things, which is the whole of this:
`backglass` is a picture among media and a `.directb2s` among assets, and VPS lists the
second. Counted against the tile, the map would mark the picture with the count of a
file that is not a picture.
"""

from __future__ import annotations

import unittest
from unittest.mock import Mock

from common.media.media_specs import media_label_map
from manager.kit.data import Library

STATE = [
    {"kind": "b2sFiles", "ours": ["backglass"], "held_in": "asset",
     "held": True, "listed": 7, "obtainable": 7, "why_not": []},
    {"kind": "topperFiles", "ours": ["topper", "topper_video"], "held_in": "media",
     "held": False, "listed": 4, "obtainable": 3, "why_not": ["collection"]},
    {"kind": "povFiles", "ours": ["pov"], "held_in": "asset",
     "held": False, "listed": 2, "obtainable": 0, "why_not": ["collection"]},
    {"kind": "ruleFiles", "ours": ["rule_sheet"], "held_in": "media",
     "held": False, "listed": 2, "obtainable": 2, "why_not": []},
    {"kind": "wheelArtFiles", "ours": ["wheel"], "held_in": "media",
     "held": True, "listed": 11, "obtainable": 0, "why_not": ["collection"]},
]


class OfferedMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.library = Library.__new__(Library)
        self.library._client = Mock(vps_state=Mock(return_value=STATE))

    def test_only_media_kinds_reach_the_map(self) -> None:
        """An asset kind sharing a media kind's name would mark the wrong tile."""
        offered = self.library.offered_media("g1")

        self.assertNotIn("backglass", offered)
        self.assertNotIn("pov", offered)

    def test_one_of_theirs_marks_both_of_ours(self) -> None:
        offered = self.library.offered_media("g1")

        self.assertEqual(offered["topper"], 3)
        self.assertEqual(offered["topper_video"], 3)

    def test_a_kind_the_catalog_only_has_as_a_folder_offers_nothing(self) -> None:
        """Eleven records and none of them a file. Marking a slot from `listed` would
        send somebody to a folder to rummage in and call it a download."""
        self.assertEqual(self.library.offered_media("g1")["wheel"], 0)

    def test_the_count_is_files_not_records(self) -> None:
        offered = self.library.offered_media("g1")

        self.assertEqual(offered["rule_sheet"], 2)


class KeptKindsTests(unittest.TestCase):
    """What the Manager asks for before it enumerates anything.

    Stored as what is hidden, read as what is kept. That direction is the point: a kind
    added in a later version is in nobody's hidden list, so it arrives switched on.

    Held by the library rather than by an install, so two installs reading one library read
    one answer instead of each carrying a copy.
    """

    def _library(self, policy: dict) -> Library:
        """The library's policy rather than this install's config: what a library
        collects is one answer for every device reading it."""
        library = Library.__new__(Library)
        library._client = Mock(library_policy=Mock(return_value=policy))
        library._kept = None
        return library

    def test_a_hidden_kind_is_not_kept(self) -> None:
        kept = self._library({"hidden_media_kinds": ["topper"]}).load_kept_kinds()

        self.assertNotIn("topper", kept["media"])
        self.assertIn("wheel", kept["media"])

    def test_a_kind_nobody_hid_is_kept(self) -> None:
        """Including every kind a config written by an older build never mentioned."""
        kept = self._library({}).load_kept_kinds()

        self.assertEqual(kept["media"], set(media_label_map()))

    def test_the_rom_can_be_hidden(self) -> None:
        """An EM table declares none, and required-ness belongs to the kind while
        whether it applies belongs to the table."""
        kept = self._library({"hidden_asset_kinds": ["rom"]}).load_kept_kinds()

        self.assertNotIn("rom", kept["asset"])

    def test_alt_color_and_alt_sound_are_kept_by_the_names_a_game_carries(self) -> None:
        kept = self._library({}).load_kept_kinds()

        self.assertLessEqual({"alt_color", "alt_sound"}, kept["asset"])

    def test_alt_color_goes_only_when_serum_and_vni_both_do(self) -> None:
        serum = self._library({"hidden_asset_kinds": ["altcolor_serum"]}).load_kept_kinds()
        both = self._library({"hidden_asset_kinds": ["altcolor_serum", "altcolor_vni"]})

        self.assertIn("alt_color", serum["asset"])
        self.assertNotIn("alt_color", both.load_kept_kinds()["asset"])

    def test_the_comma_string_the_ini_holds_reads_the_same(self) -> None:
        kept = self._library({"hidden_media_kinds": "topper, wheel"}).load_kept_kinds()

        self.assertNotIn("topper", kept["media"])
        self.assertNotIn("wheel", kept["media"])

    def test_hiding_a_kind_this_build_never_heard_of_changes_nothing(self) -> None:
        """A config written by a newer build must not subtract a name from a set that
        does not contain it and leave the reader short."""
        kept = self._library({"hidden_media_kinds": ["nonesuch"]}).load_kept_kinds()

        self.assertEqual(kept["media"], set(media_label_map()))


class HeldKeptKindsTests(unittest.TestCase):
    """A draw is on the event loop, where a read over HTTP waits on the process making it."""

    def setUp(self) -> None:
        self.client = Mock()
        self.client.library_policy.return_value = {"hidden_media_kinds": ["topper"]}
        self.library = Library(self.client)

    def test_a_draw_asks_nothing(self) -> None:
        self.library.load_kept_kinds()

        kept = self.library.kept_kinds()

        self.assertNotIn("topper", kept["media"])
        self.assertEqual(self.client.library_policy.call_count, 1)

    def test_before_the_read_every_kind_is_kept(self) -> None:
        self.assertEqual(self.library.kept_kinds()["media"], set(media_label_map()))
        self.client.library_policy.assert_not_called()

    def test_a_config_write_leaves_them_held(self) -> None:
        self.library.load_kept_kinds()

        self.library.put_config({"relative_dates": False})
        self.library.load_kept_kinds()

        self.assertNotIn("topper", self.library.kept_kinds()["media"])
        self.assertEqual(self.client.library_policy.call_count, 1)

    def test_a_policy_write_holds_what_it_answered(self) -> None:
        self.library.load_kept_kinds()
        self.client.put_library_policy.return_value = {"hidden_media_kinds": ["wheel"]}

        self.library.put_library_policy({"hidden_media_kinds": ["wheel"]})

        kept = self.library.kept_kinds()["media"]
        self.assertEqual(("topper" in kept, "wheel" in kept), (True, False))
        self.assertEqual(self.client.library_policy.call_count, 1)

    def test_a_policy_write_that_fails_leaves_them_as_they_were(self) -> None:
        self.library.load_kept_kinds()
        self.client.put_library_policy.side_effect = OSError("refused")

        with self.assertRaises(OSError):
            self.library.put_library_policy({"hidden_media_kinds": []})

        self.assertNotIn("topper", self.library.kept_kinds()["media"])


if __name__ == "__main__":
    unittest.main()
