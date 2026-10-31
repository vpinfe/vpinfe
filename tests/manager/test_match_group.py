"""What a Match group says when there is no record to draw."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import Mock, patch

from common.i18n import t
from manager.kit import panel
from manager.library.match_group import game_match_gap
from manager.workbench import core, game_table
from manager.workbench.game_table import release_match_gap


class AGame(unittest.TestCase):
    def test_an_id_the_catalog_lacks_is_not_in_vps(self) -> None:
        said, level, _ = game_match_gap("SHAREDID", declared=False, held=True)
        self.assertEqual(("manager.workbench.not_listed", "warn"), (said, level))

    def test_with_no_catalog_the_match_is_unknown_rather_than_wrong(self) -> None:
        said, level, why = game_match_gap("abc", declared=False, held=False)
        self.assertEqual(("word.unknown", "unknown"), (said, level))
        self.assertEqual("manager.workbench.no_vps_data", why)

    def test_no_id_is_not_matched_whether_or_not_there_is_a_catalog(self) -> None:
        for held in (True, False):
            self.assertEqual("manager.workbench.not_matched",
                             game_match_gap("", declared=False, held=held)[0])

    def test_a_declared_none_is_quiet(self) -> None:
        self.assertEqual("off", game_match_gap("", declared=True, held=True)[1])


class ATable(unittest.TestCase):
    def test_nothing_recorded_is_quiet(self) -> None:
        self.assertEqual(("manager.workbench.not_matched", "off", ""),
                         release_match_gap("entry", "", held=True))

    def test_an_unmatched_game_says_which_comes_first(self) -> None:
        self.assertEqual("manager.workbench.releases_unmatched",
                         release_match_gap("", "", held=True)[2])

    def test_a_release_the_catalog_lacks_is_not_in_vps(self) -> None:
        self.assertEqual("manager.workbench.not_listed",
                         release_match_gap("entry", "gone", held=True)[0])

    def test_with_no_catalog_the_release_is_unknown(self) -> None:
        self.assertEqual("word.unknown", release_match_gap("entry", "x", held=False)[0])

    def test_a_list_that_could_not_be_read_is_unknown_and_says_so(self) -> None:
        self.assertEqual(("word.unknown", "unknown", "manager.workbench.vps_failed"),
                         release_match_gap("entry", "x", held=True, read=False))


async def _now(callback: Any, *args: Any, **kwargs: Any) -> Any:
    return callback(*args, **kwargs)


class ATableWhoseReleasesCouldNotBeRead(unittest.IsolatedAsyncioTestCase):
    async def test_is_unknown_with_why_on_the_chip_rather_than_not_in_vps(self) -> None:
        for failing in ("vps_releases", "vps_catalog_held"):
            with self.subTest(failing=failing):
                library = Mock()
                library.vps_releases.return_value = []
                library.vps_catalog_held.return_value = True
                getattr(library, failing).side_effect = TimeoutError("timed out")
                context = {"library": library, "game": {"vps_id": "entry"}}

                with patch("manager.kit.offload.run.io_bound", new=_now), \
                        patch.object(panel, "state",
                                     new=lambda text, level, hint="": (text, level, hint)), \
                        patch.object(panel, "intro", new=lambda text: ("intro", text)), \
                        patch.object(game_table, "change_match", return_value="change"), \
                        self.assertLogs("vpinfe.manager.workbench", level="WARNING"):
                    rows = await game_table._release_match(
                        context, {"id": "t1", "source": {"vps_file_id": "gone"}})

                self.assertEqual(rows[1:], [
                    (core.FULL, (t("word.unknown"), "unknown", t("said.why.timed_out"))),
                    ("intro", t("manager.workbench.vps_failed")),
                    (core.FULL, "change")])


if __name__ == "__main__":
    unittest.main()
