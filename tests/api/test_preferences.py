"""How a UI arrangement is stored, and what it is worth.

A column layout someone tuned is not the same as a dismissed notice: losing it is a
real annoyance, so the write is atomic and the file is left alone by the 3.0 state
reset. Both of those are asserted here, because both are invisible until they fail.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from common.config import ui_preferences
from common.failures import why
from common.i18n import t
from tests.support.skips import needs_posix_permissions

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]

LAYOUT = {"columns": [{"colId": "title", "width": 240}], "sort": "year"}


class StorageTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "ui-preferences.json"
        patcher = patch.object(ui_preferences, "PREFERENCES_PATH", self.path)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_an_unset_scope_is_empty_rather_than_missing(self) -> None:
        """A first run asks before it has ever written, so this is the normal case."""
        self.assertEqual(ui_preferences.get("games"), {})

    def test_a_layout_survives_being_written_and_read(self) -> None:
        ui_preferences.put("games", LAYOUT)

        self.assertEqual(ui_preferences.get("games"), LAYOUT)

    def test_scopes_do_not_overwrite_each_other(self) -> None:
        ui_preferences.put("games", LAYOUT)
        ui_preferences.put("assets", {"columns": []})

        self.assertEqual(ui_preferences.get("games"), LAYOUT)
        self.assertEqual(ui_preferences.get("assets"), {"columns": []})

    def test_the_file_carries_its_own_schema(self) -> None:
        ui_preferences.put("games", LAYOUT)

        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8"))["schema"], 1)

    def test_an_unreadable_file_reads_as_empty_rather_than_raising(self) -> None:
        """Losing a layout is an annoyance; refusing to start is not acceptable."""
        self.path.write_text("{ not json", encoding="utf-8")

        self.assertEqual(ui_preferences.get("games"), {})

    def test_a_failed_write_leaves_no_temp_file_behind(self) -> None:
        """The write is atomic through a temp file, and the reset sweeps
        `.vpinfe_write_*`. One left behind after every failure would accumulate."""
        ui_preferences.put("games", LAYOUT)
        with patch("json.dumps", side_effect=ValueError("boom")):
            with self.assertRaises(ValueError):
                ui_preferences.put("games", {"columns": []})

        leftovers = list(self.path.parent.glob(".vpinfe_write_*"))
        self.assertEqual(leftovers, [], "a temp file survived a failed write")
        self.assertEqual(ui_preferences.get("games"), LAYOUT, "the old value stands")

    @needs_posix_permissions
    def test_a_folder_it_cannot_write_is_named(self) -> None:
        folder = self.path.parent
        folder.chmod(0o555)
        self.addCleanup(folder.chmod, 0o755)

        with self.assertRaises(OSError) as caught:
            ui_preferences.put("games", LAYOUT)

        self.assertEqual(why(caught.exception),
                         t("said.why.no_permission_at", path=str(folder)))


class ResetTests(unittest.TestCase):
    def test_the_reset_does_not_throw_away_a_ui_arrangement(self) -> None:
        """Deliberate, and stated in ui_preferences' own docstring: a 3.0 state reset is
        about 3.0 data, not about how somebody likes their tables. Asserted so that
        adding the file to CONFIG_FILES has to be a decision rather than a tidy-up."""
        from common.games.revert_3x import CONFIG_FILES

        self.assertNotIn("ui-preferences.json", CONFIG_FILES)


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class ApiTests(unittest.TestCase):
    def setUp(self) -> None:
        import httpapi

        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        patcher = patch.object(ui_preferences, "PREFERENCES_PATH",
                               Path(self.tmp.name) / "ui-preferences.json")
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_reading_a_scope_nobody_has_written(self) -> None:
        body = self.client.get("/preferences/games").json()

        self.assertEqual(body, {"scope": "games", "value": {}})

    def test_a_layout_round_trips_over_the_wire(self) -> None:
        written = self.client.put("/preferences/games", json=LAYOUT).json()

        self.assertEqual(written, {"scope": "games", "value": LAYOUT})
        self.assertEqual(self.client.get("/preferences/games").json()["value"], LAYOUT)

    def test_the_scopes_are_enrolled_in_core(self) -> None:
        """An endpoint whose scope is not in CORE answers nothing on a default install."""
        from httpapi import scopes

        self.assertIn(scopes.PREFERENCES_READ, scopes.CORE)
        self.assertIn(scopes.PREFERENCES_WRITE, scopes.CORE)
