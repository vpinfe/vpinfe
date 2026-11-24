"""What VPinFE could not write to a game's .info, read back until it can be.

Real read-only folders: the failure is the one a share or a locked folder gives.
"""

from __future__ import annotations

import json
from unittest import mock

from common.games import unwritten
from common.games.game_metadata import keep_game_meta, load_game_meta, persist_game_meta
from common.games.info_file import MetaConfig
from tests.support.library import TempTree, fake_game, game_info, write_game
from tests.support.skips import needs_posix_permissions

NAME = "Locked Game (Original 2024)"


@needs_posix_permissions
class HeldTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, NAME, info=game_info("Locked Game"))
        self.info = self.folder / f"{NAME}.info"
        self.game = fake_game(self.folder, NAME, meta=MetaConfig(str(self.info)).data)
        self.folder.chmod(0o555)
        self.addCleanup(self.folder.chmod, 0o755)
        cleared = mock.patch.dict(unwritten._HELD, clear=True)
        cleared.start()
        self.addCleanup(cleared.stop)
        logged = mock.patch.object(unwritten, "logger")
        self.logged = logged.start()
        self.addCleanup(logged.stop)

    def _keep_title(self, title: str) -> None:
        config = load_game_meta(self.game)
        config["Info"]["Title"] = title
        keep_game_meta(self.game, config)

    def _on_disk(self) -> dict:
        return json.loads(self.info.read_text(encoding="utf-8"))

    def test_every_read_of_the_file_reads_what_is_held(self) -> None:
        self._keep_title("Held")

        self.assertEqual(MetaConfig(str(self.info)).data["Info"]["Title"], "Held")
        self.assertEqual(self._on_disk()["Info"]["Title"], "Locked Game")

    def test_a_file_changed_on_disk_is_read_as_it_is_there(self) -> None:
        self._keep_title("Held")
        self.folder.chmod(0o755)
        self.info.write_text(json.dumps(game_info("Written Elsewhere")), encoding="utf-8")
        self.folder.chmod(0o555)

        self.assertEqual(MetaConfig(str(self.info)).data["Info"]["Title"],
                         "Written Elsewhere")
        self.assertEqual(unwritten.reasons([str(self.folder)]), {})

    def test_a_write_that_succeeds_carries_what_was_held(self) -> None:
        self._keep_title("Held")
        self.folder.chmod(0o755)

        MetaConfig(str(self.info)).set_table_hidden(f"{NAME}.vpx", True)

        self.assertEqual(self._on_disk()["Info"]["Title"], "Held")
        self.assertEqual(unwritten.reasons([str(self.folder)]), {})

    def test_a_persons_edit_it_cannot_write_still_fails_and_is_not_held(self) -> None:
        config = load_game_meta(self.game)
        config["Info"]["Title"] = "Edited"

        with self.assertRaises(PermissionError):
            persist_game_meta(self.game, config)

        self.assertEqual(MetaConfig(str(self.info)).data["Info"]["Title"], "Locked Game")
        self.assertEqual(unwritten.reasons([str(self.folder)]), {})

    def test_the_first_failure_is_a_warning_and_the_next_is_not(self) -> None:
        self._keep_title("Held")
        self._keep_title("Held again")

        self.assertEqual(self.logged.warning.call_count, 1)
        self.assertEqual(self.logged.debug.call_count, 1)
