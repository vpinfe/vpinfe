"""What the VPS pickers show in place of a list.

A picker over what VPS lists opens empty for three different reasons - VPS lists
nothing, there is no catalog, or the read failed - and each is a different thing to do
next, so each says its own.
"""

import unittest
from typing import Any
from unittest.mock import Mock, patch

from common.i18n import t
from manager.workbench import game_match

NONE_LISTED = "none listed"
RECORDS = [{"vps_file_id": "topper_1", "version": "1.0"}]


def _context(records=None, vps_id="mm_1997x", held=True):
    library = Mock()
    library.vps_releases = Mock(return_value=list(RECORDS if records is None else records))
    library.vps_catalog_held = Mock(return_value=held)
    return {"library": library, "game": {"vps_id": vps_id}, "game_id": "g1"}


class PickerListTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.offloaded: list[Any] = []

        async def _worker(callback: Any, *args: Any, **kwargs: Any) -> Any:
            self.offloaded.append(callback)
            return callback(*args, **kwargs)

        patcher = patch("manager.kit.offload.run.io_bound", new=_worker)
        patcher.start()
        self.addCleanup(patcher.stop)

    async def test_a_list_is_shown_with_nothing_in_its_place(self) -> None:
        context = _context()

        listed = await game_match._listed_by_vps(context, NONE_LISTED, "topperFiles")

        self.assertEqual((RECORDS, "", ""), listed)
        context["library"].vps_releases.assert_called_once_with("mm_1997x", "topperFiles")
        self.assertIn(context["library"].vps_releases, self.offloaded)

    async def test_vps_listing_none_says_so(self) -> None:
        listed = await game_match._listed_by_vps(_context(records=[]), NONE_LISTED)

        self.assertEqual(([], NONE_LISTED, ""), listed)

    async def test_no_catalog_is_not_vps_listing_none(self) -> None:
        listed = await game_match._listed_by_vps(_context(records=[], held=False),
                                                NONE_LISTED)

        self.assertEqual(([], t("manager.workbench.no_vps_data"), ""), listed)

    async def test_a_read_that_fails_says_it_could_not_read(self) -> None:
        for failing in ("vps_releases", "vps_catalog_held"):
            with self.subTest(failing=failing):
                context = _context(records=[])
                getattr(context["library"], failing).side_effect = TimeoutError(
                    "read timed out")

                with self.assertLogs("vpinfe.manager.workbench", level="WARNING"):
                    listed = await game_match._listed_by_vps(context, NONE_LISTED)

                self.assertEqual(
                    ([], t("manager.workbench.vps_failed"),
                     t("said.why.timed_out")),
                    listed)

    async def test_a_game_with_no_vps_entry_asks_nothing_of_the_list(self) -> None:
        context = _context(vps_id="")

        listed = await game_match._listed_by_vps(context, NONE_LISTED)

        self.assertEqual(([], NONE_LISTED, ""), listed)
        context["library"].vps_releases.assert_not_called()


if __name__ == "__main__":
    unittest.main()
