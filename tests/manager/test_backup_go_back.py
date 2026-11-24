"""Go back to 2.x is offered while a copy it could put in place is listed."""

from __future__ import annotations

import unittest

from common.backups import copies
from manager.backups import backup_go_back, backups


def _row(**over) -> dict:
    found = {"id": "c", "subject_kind": copies.SUBJECT_GAME, "subject_id": "g1",
             "subject_name": "Attack from Mars",
             "kind": copies.KIND_GAME, "file": "a.info", "path": "/library/c",
             "taken": "2026-10-01T10:00:00Z", "written_by": copies.BY_2X,
             "reason": copies.REASON_UPGRADE, "note": "", "size": 1000, "gone": False, **over}
    return backups.rows([found], parts=True)[0]


class Available(unittest.TestCase):
    def test_a_games_2x_copy_offers_it(self) -> None:
        self.assertTrue(backup_go_back.available([_row()]))

    def test_the_collections_2x_copy_offers_it(self) -> None:
        self.assertTrue(backup_go_back.available(
            [_row(subject_kind=copies.SUBJECT_VPINFE, kind=copies.KIND_COLLECTIONS)]))

    def test_an_install_with_no_2x_copy_offers_nothing(self) -> None:
        self.assertFalse(backup_go_back.available([_row(written_by=copies.BY_3X)]))

    def test_a_copy_of_a_game_that_is_gone_has_nothing_to_go_back_to(self) -> None:
        self.assertFalse(backup_go_back.available([_row(gone=True)]))

    def test_a_2x_file_inside_a_backup_is_not_one_it_puts_back(self) -> None:
        self.assertFalse(backup_go_back.available([_row(within="z")]))

    def test_a_2x_settings_file_is_not_one_it_puts_back(self) -> None:
        self.assertFalse(backup_go_back.available(
            [_row(subject_kind=copies.SUBJECT_VPINFE, kind=copies.KIND_SETTINGS)]))


if __name__ == "__main__":
    unittest.main()
