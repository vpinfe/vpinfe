"""Naming the VPS record a file is bound to, in the panel.

The id is what gets stored and it is not something to put on screen, so the panel
resolves it against the spreadsheet. What this covers is the answers that resolution
has, because two of them look like nothing going wrong, and the one it gives when it
cannot look.
"""

import unittest
from typing import Any
from unittest.mock import Mock, patch

from common.online import vps_kinds
from manager.library import file_acts
from manager.workbench import game_match

RECORD = "wheel_KtY1"

# Wheel art, not a backglass: `backglass` is a picture among media and a .directb2s
# among assets, so it is the one media kind name that must not reach a VPS listing.
WHEELS = [
    {"vps_file_id": RECORD, "version": "3.0", "authors": ["wildman", "hauntfreaks"]},
    {"vps_file_id": "wheel_Other", "version": "1.1", "authors": []},
]

UNNAMED = "Matched to a published file"


def _context(releases=None, vps_id="mm_1997x", held=True):
    library = Mock()
    library.vps_releases = Mock(
        return_value=list(WHEELS if releases is None else releases))
    library.vps_catalog_held = Mock(return_value=held)
    return {"library": library, "game": {"vps_id": vps_id}, "game_id": "g1"}


class MatchLineTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.offloaded: list[Any] = []

        async def _worker(callback: Any, *args: Any, **kwargs: Any) -> Any:
            self.offloaded.append(callback)
            return callback(*args, **kwargs)

        patcher = patch("manager.kit.offload.run.io_bound", new=_worker)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_bound_file_is_named_by_what_a_person_can_compare(self) -> None:
        line = await game_match._match_line(_context(), "wheel", RECORD)

        self.assertEqual(line, "Matched to 3.0 · wildman, hauntfreaks")

    async def test_the_records_are_read_on_a_worker(self) -> None:
        context = _context()
        await game_match._match_line(context, "wheel", RECORD)

        self.assertIn(context["library"].vps_releases, self.offloaded)

    async def test_nothing_is_said_when_nobody_has_said_anything(self) -> None:
        """Absent is the ordinary state - almost every file is unbound, so a line here
        would be on nearly every slot and tell nobody anything."""
        self.assertEqual(await game_match._match_line(_context(), "wheel", None), "")

    async def test_a_record_the_spreadsheet_dropped_still_says_so(self) -> None:
        """Silence here would read as unbound, and the difference matters: no update
        can ever be reported for a file bound to something no longer listed."""
        line = await game_match._match_line(_context(releases=[]), "wheel", RECORD)

        self.assertEqual(line, "Matched to a file the spreadsheet no longer lists")

    async def test_a_read_that_fails_says_only_what_is_known(self) -> None:
        context = _context()
        context["library"].vps_releases.side_effect = TimeoutError("read timed out")

        with self.assertLogs("vpinfe.manager.workbench", level="WARNING"):
            line = await game_match._match_line(context, "wheel", RECORD)

        self.assertEqual(line, UNNAMED)

    async def test_no_catalog_is_not_a_dropped_record(self) -> None:
        line = await game_match._match_line(_context(releases=[], held=False),
                                           "wheel", RECORD)

        self.assertEqual(line, UNNAMED)

    async def test_a_game_with_no_vps_entry_asks_nothing(self) -> None:
        context = _context(vps_id="")
        line = await game_match._match_line(context, "wheel", RECORD)

        self.assertEqual(line, UNNAMED)
        context["library"].vps_releases.assert_not_called()

    async def test_a_kind_vps_does_not_publish_says_nothing(self) -> None:
        line = await game_match._match_line(_context(), "playfield", RECORD)

        self.assertEqual(line, "")

    def test_the_backglass_picture_is_not_offered_the_backglass_asset(self) -> None:
        """The two vocabularies share the word. `backglass` is a picture among media
        and a .directb2s among assets, and VPS lists the second - so matching a media
        slot by name alone offered it the assets that view is explicitly not about."""
        self.assertIsNone(file_acts.listed_as("backglass", vps_kinds.MEDIA))
        self.assertIsNotNone(file_acts.listed_as("backglass", vps_kinds.ASSET))

    def test_the_media_kinds_vps_does_list_still_match(self) -> None:
        for kind in ("wheel", "topper", "rule_sheet"):
            with self.subTest(kind=kind):
                self.assertIsNotNone(file_acts.listed_as(kind, vps_kinds.MEDIA))

    async def test_the_records_asked_for_are_that_kind_s(self) -> None:
        """One entry answers a different list per kind, and the local kind is not the
        name to ask by - `topper` and `topper_video` are one list upstream."""
        context = _context()
        await game_match._match_line(context, "topper", RECORD)

        context["library"].vps_releases.assert_called_once_with(
            "mm_1997x", "topperFiles")


class KindMapTests(unittest.TestCase):
    def test_every_local_kind_maps_back_to_the_listing_that_holds_it(self) -> None:
        for listing in vps_kinds.KINDS:
            for ours in listing.ours:
                with self.subTest(kind=ours):
                    self.assertIs(vps_kinds.BY_OURS[ours], listing)

    def test_two_of_ours_share_one_of_theirs(self) -> None:
        """The reason the map is needed at all: a local kind cannot address VPS."""
        self.assertIs(vps_kinds.BY_OURS["altcolor_serum"],
                      vps_kinds.BY_OURS["altcolor_vni"])


if __name__ == "__main__":
    unittest.main()
