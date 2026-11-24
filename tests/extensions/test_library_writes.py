"""An extension's writes land, against a real folder on disk.

The rest of the extension suite asserts the table of operations and the scope gate. This
asserts the other half - that calling one changes the `.info` the way an HTTP client's
call to the same endpoint would - because the two are meant to be one implementation and
a test that only counts names cannot see them come apart.

`set_default_table` is here first because it came apart. The host handed the route a
`TableDefault(table_id=...)` and the model's field is `table`, so Pydantic's default
`extra='ignore'` dropped the keyword and the field took its empty default - which the
model's own docstring says clears the choice. An extension setting a default silently
cleared it, and the frontend then looked like it was picking a table on its own.
"""

from __future__ import annotations

import json
import unittest
from typing import cast
from unittest.mock import patch

import httpapi
from common.extensions.context import ExtensionFiles
from common.extensions.games import ExtensionGames, withdraw_all
from tests.support.library import TempTree, fake_game, write_game

NO_FILES = cast(ExtensionFiles, None)
GAME_ID = "Ext000000001"
FOLDER = "Medieval Madness (Williams 1997)"
DESKTOP = f"{FOLDER}.vpx"
VR = f"{FOLDER} - VR.vpx"

INFO = {
    "Info": {"Name": "Medieval Madness", "Manufacturer": "Williams", "Year": "1997"},
    "VPinFE": {"game_id": GAME_ID},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": DESKTOP},
        "tbl0000002": {"id": "tbl0000002", "filename": VR},
    },
}


class ExtensionWriteTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        withdraw_all()
        self.addCleanup(withdraw_all)
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False,
                                 files={DESKTOP: b"vpx", VR: b"vpx"})
        game = fake_game(self.folder, FOLDER, meta=INFO)
        patcher = patch("common.games.game_repository.catalog",
                        return_value={GAME_ID: game})
        patcher.start()
        self.addCleanup(patcher.stop)
        # Building the app is what offers the library to the host.
        httpapi.create_api_app()
        self.extension = ExtensionGames("tester", ("games:read", "games:write"), NO_FILES)

    def _vpinfe(self) -> dict:
        held = json.loads((self.folder / f"{FOLDER}.info").read_text(encoding="utf-8"))
        return held.get("vpinfe", {})

    def test_setting_a_default_table_records_the_table_that_was_asked_for(self) -> None:
        self.extension.set_default_table(GAME_ID, "tbl0000002")

        self.assertEqual(self._vpinfe().get("default_table"), "tbl0000002")

    def test_setting_a_default_table_answers_with_it_chosen(self) -> None:
        answered = self.extension.set_default_table(GAME_ID, "tbl0000002")
        chosen = {row["id"]: row["default"] for row in answered["tables"]}

        self.assertEqual(chosen, {"tbl0000001": False, "tbl0000002": True})

    def test_a_default_already_set_is_replaced_and_not_cleared(self) -> None:
        """The failure this exists for looked like a no-op from the outside: the write
        succeeded, reported nothing wrong, and left the game with no choice at all."""
        self.extension.set_default_table(GAME_ID, "tbl0000001")
        self.extension.set_default_table(GAME_ID, "tbl0000002")

        self.assertEqual(self._vpinfe().get("default_table"), "tbl0000002")

    def test_naming_no_table_is_what_clears_it(self) -> None:
        self.extension.set_default_table(GAME_ID, "tbl0000002")
        self.extension.set_default_table(GAME_ID, "")

        self.assertNotIn("default_table", self._vpinfe())

    def test_a_table_this_game_does_not_have_is_refused(self) -> None:
        """And the refusal reaches the extension as a refusal, not as a written record."""
        from common import service_errors

        with self.assertRaises(service_errors.NotFoundError):
            self.extension.set_default_table(GAME_ID, "tbl0009999")
        self.assertNotIn("default_table", self._vpinfe())

    def test_a_folder_the_share_hands_back_decomposed_is_still_the_one_made(self) -> None:
        """A share stores `é` as `e` and a combining accent, and the folder was made with
        the composed letter, so a plain path compare finds nothing and the import fails."""
        import unicodedata

        made = self.root / "Caf\u00e9"
        made.mkdir()
        listed = fake_game(unicodedata.normalize("NFD", str(made)), "Caf\u00e9", meta=INFO)

        with patch("common.games.game_service.create_game", return_value=made), \
                patch("common.games.game_repository.all_games", return_value=[listed]), \
                patch("common.games.game_identity.ensure_id", return_value=GAME_ID):
            self.assertEqual(self.extension.create("Caf\u00e9"), GAME_ID)

    def test_rating_a_game_lands_on_the_record(self) -> None:
        self.assertEqual(self.extension.rate_game(GAME_ID, 4), {"rating": 4})

    def test_an_extension_reads_what_the_endpoint_serves(self) -> None:
        """The endpoint is this call plus the model. Asserting that the extension's answer
        validates into the same model, with the same content, is what says so."""
        from httpapi import games as games_api
        from httpapi import models

        mine = models.TableList.model_validate(self.extension.game_tables(GAME_ID))

        self.assertEqual(mine, games_api.get_games(GAME_ID))


if __name__ == "__main__":
    unittest.main()
