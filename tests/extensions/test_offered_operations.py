"""What core lets an extension do to the library.

An extension used to reach eight things where an HTTP client reached twenty-five, which
is backwards: games are the thing extensions are mostly for. Core hands the host the same
services the routes call, so the two cannot answer differently.

The route's scope gate lives in its `dependencies=[...]` and only fires for a real
request, so going straight to the service does not carry it. The gate is re-applied here,
against the manifest, which is what makes declaring a scope mean something.
"""

from __future__ import annotations

import unittest
from typing import cast

import httpapi
from common.extensions.context import ExtensionFiles
from common.extensions.contract import ContractError
from common.extensions.games import ExtensionGames, offered, withdraw_all

NO_FILES = cast(ExtensionFiles, None)


class OfferedTests(unittest.TestCase):
    def setUp(self) -> None:
        withdraw_all()
        self.addCleanup(withdraw_all)
        httpapi.create_api_app()

    def test_building_the_api_offers_the_library_to_extensions(self) -> None:
        """Wired where the app is built, so a test app offers what the real one does."""
        self.assertIn("list_games", offered())
        self.assertIn("rate_table", offered())

    def test_an_extension_reaches_what_its_manifest_declared(self) -> None:
        reader = ExtensionGames("reader", ("games:read",), NO_FILES)

        self.assertIn("list_games", reader.reaches())
        self.assertNotIn("rate_table", reader.reaches())

    def test_writing_needs_the_write_scope(self) -> None:
        reader = ExtensionGames("reader", ("games:read",), NO_FILES)

        with self.assertRaises(ContractError):
            reader.rate_table("g1", "t1", 5)

    def test_something_core_never_offered_is_not_reachable(self) -> None:
        """And it says what is on offer, because the alternative is an author guessing
        at names against an AttributeError."""
        anyone = ExtensionGames("anyone", ("games:read", "games:write"), NO_FILES)

        with self.assertRaises(AttributeError) as caught:
            anyone.delete_the_whole_library("please")
        self.assertIn("list_games", str(caught.exception))

    def test_no_extension_can_mark_a_game_private_or_not(self) -> None:
        """Turning Private off would send a game's data without the person saying so."""
        writer = ExtensionGames("writer", ("games:read", "games:write"), NO_FILES)

        self.assertFalse([name for name in offered() if "private" in name])
        self.assertIn("set_favorite", writer.reaches())

    def test_launching_is_not_granted_by_being_allowed_to_write(self) -> None:
        """It takes over the cabinet rather than editing a record, and the scope
        vocabulary said so before extensions existed. An extension that starts a game
        asks for that by name, and whoever installs it reads it by name."""
        writer = ExtensionGames("writer", ("games:read", "games:write"), NO_FILES)

        self.assertNotIn("launch_game", writer.reaches())
        with self.assertRaises(ContractError):
            writer.launch_game("g1")

    def test_an_extension_that_asked_to_launch_can(self) -> None:
        launcher = ExtensionGames("scheduler", ("launch:invoke",), NO_FILES)

        self.assertIn("launch_game", launcher.reaches())

    def test_launching_alone_grants_nothing_else(self) -> None:
        """The three are separate on purpose: a scheduler that can start a table should
        not thereby be able to rewrite one."""
        launcher = ExtensionGames("scheduler", ("launch:invoke",), NO_FILES)

        self.assertEqual(launcher.reaches(), ("launch_game",))

    def test_looking_in_the_catalog_is_a_read_and_matching_is_a_write(self) -> None:
        reader = ExtensionGames("reader", ("games:read",), NO_FILES)
        writer = ExtensionGames("writer", ("games:read", "games:write"), NO_FILES)

        self.assertIn("vps_catalog", reader.reaches())
        self.assertIn("vps_settle", reader.reaches())
        self.assertNotIn("match_game", reader.reaches())
        self.assertIn("match_game", writer.reaches())

    def test_matching_to_an_entry_the_catalog_lacks_is_refused_in_words(self) -> None:
        from unittest.mock import patch

        from common import service_errors
        from common.games import game_ops

        with patch("common.games.game_ops.game_lens.game_for_writing"), \
                patch("common.games.game_service.load_vpsdb", return_value=[]):
            with self.assertRaises(service_errors.NotFoundError) as caught:
                game_ops.match_game("g1", "nope")
        self.assertEqual(str(caught.exception), "No VPS entry has that id")

    def test_with_matching_switched_off_the_catalog_is_not_fetched(self) -> None:
        from unittest.mock import patch

        reader = ExtensionGames("reader", ("games:read",), NO_FILES)
        with patch("common.games.auto_match.enabled", return_value=False), \
                patch("common.online.vpsdb_sync.ensure_catalog") as fetch:
            found = reader.vps_catalog()

        self.assertEqual(found, {"enabled": False, "ready": False})
        fetch.assert_not_called()

    def test_the_named_methods_still_win(self) -> None:
        """`kinds` is wrapped for a reason and is not one of the offered names; the
        lookup must not shadow it."""
        anyone = ExtensionGames("anyone", ("games:read",), NO_FILES)

        self.assertTrue(callable(anyone.kinds))
        self.assertNotIn("kinds", offered())


if __name__ == "__main__":
    unittest.main()
