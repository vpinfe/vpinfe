"""Resolving only some media kinds answers each of them as resolving all of them does."""

from __future__ import annotations

import unittest

from common.media.media_specs import MEDIA_SPECS, resolve_media_entries

FOLDER = "Cactus Canyon (Bally 1998)"
TABLE = "Cactus Canyon (Bally 1998) - VPW 1.2"

LISTINGS = {
    "nothing": [],
    "default wheel": ["wheel.png"],
    "logo only": ["logo.png"],
    "own wheel and logo": [f"(Wheel) {TABLE}.png", "logo.png"],
    "set wheel": ["wheels/classic/wheel.png", "wheel.png"],
    "set and logo": ["wheels/classic/wheel.png", "logo.png"],
    "fss only": ["fss.png"],
    "everything": [f"(Wheel) {FOLDER}.png", "wheel.png", "bg.png", "dmd.png", "logo.png",
                   "table.png", "fss.png", "cab.png"],
}
ACTIVE_SETS = (None, {"wheel": "classic"}, {"wheel": "logo"})


class OnlySomeKinds(unittest.TestCase):
    def _each(self, listing, active):
        medias = set(listing)
        whole = resolve_media_entries(f"/games/{FOLDER}", set(), medias, "table", TABLE,
                                      active)
        for spec in MEDIA_SPECS:
            with self.subTest(kind=spec.kind, listing=listing, active=active):
                part = resolve_media_entries(f"/games/{FOLDER}", set(), medias, "table",
                                             TABLE, active, only=[spec.kind])
                self.assertEqual(part, {spec.kind: whole[spec.kind]})

    def test_one_kind_is_answered_as_the_whole_set_answers_it(self) -> None:
        for listing in LISTINGS.values():
            for active in ACTIVE_SETS:
                self._each(listing, active)

    def test_asking_for_nothing_answers_nothing(self) -> None:
        self.assertEqual(resolve_media_entries(f"/games/{FOLDER}", set(), {"wheel.png"},
                                               only=[]), {})

    def test_a_generator_of_kinds_is_read_once(self) -> None:
        got = resolve_media_entries(f"/games/{FOLDER}", set(), {"wheel.png", "bg.png"},
                                    only=(kind for kind in ("wheel", "backglass")))
        self.assertEqual(set(got), {"wheel", "backglass"})


if __name__ == "__main__":
    unittest.main()
