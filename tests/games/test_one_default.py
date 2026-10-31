"""Every place that picks a game's table for you picks the same one.

The fixture is built so a site with any other rule gives a different answer: the newest
table is hidden, and the next newest sorts last by name.
"""

from __future__ import annotations

from pathlib import Path
from unittest import mock

from common.games import collection_resolver, export_bundle, game_metadata, table_lens
from common.games.collection_resolver import Entry
from common.games.game_parser import GameParser
from common.games.tables import entry_filename
from common.host import launch
from frontend import game_state
from httpapi.models import TableRowList
from tests.support.library import TempTree, write_game

FOLDER = "Example (Bally 1990)"
ALPHA, BRAVO, CHARLIE = "Alpha.vpx", "Bravo.vpx", "Charlie.vpx"

INFO = {
    "Info": {"Title": "Example", "Manufacturer": "Bally", "Year": "1990"},
    "vpinfe": {"schema": 2, "game_id": "Game00000001"},
    "tables": {
        "tbl0000001": {"id": "tbl0000001", "filename": ALPHA,
                       "added": "2024-01-01T00:00:00Z"},
        "tbl0000002": {"id": "tbl0000002", "filename": BRAVO, "hidden": True,
                       "added": "2026-01-01T00:00:00Z"},
        "tbl0000003": {"id": "tbl0000003", "filename": CHARLIE,
                       "added": "2025-01-01T00:00:00Z"},
    },
}


class OneDefaultTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.folder = write_game(self.root, FOLDER, info=INFO, vpx=False, files={
            ALPHA: b"vpx", BRAVO: b"vpx", CHARLIE: b"vpx",
            "(Wheel) Alpha.png": b"\x89PNG alpha wheel"})
        (self.game,) = GameParser(str(self.root)).get_all_games()

    def test_the_scan_points_at_it(self) -> None:
        assert self.game.full_path_vpx_file is not None
        self.assertEqual(Path(self.game.full_path_vpx_file).name, CHARLIE)

    def test_the_metadata_row_names_it(self) -> None:
        self.assertEqual(game_metadata.default_table(self.game.meta_config)[0], CHARLIE)

    def test_a_launch_with_no_file_plays_it(self) -> None:
        self.assertEqual(entry_filename(launch._resolve_entry(self.game, None)[1]), CHARLIE)

    def test_the_wheel_offers_it_first(self) -> None:
        offered = collection_resolver.visible_entries(self.game)

        self.assertEqual([entry_filename(e) for e in offered], [CHARLIE, ALPHA])
        self.assertEqual(game_state._default_id(self.game), "tbl0000003")

    def test_the_rest_lens_flags_it(self) -> None:
        rows = table_lens.table_rows(self.game, {"game_dir": str(self.folder)})

        self.assertEqual([r["filename"] for r in rows if r["default"]], [CHARLIE])

    def test_the_rest_lens_flags_where_a_cleared_choice_goes(self) -> None:
        """With a table chosen, the automatic pick is still named."""
        assert self.game.meta_config is not None
        self.game.meta_config["vpinfe"]["default_table"] = "tbl0000001"

        rows = table_lens.table_rows(self.game, {"game_dir": str(self.folder)})

        self.assertEqual([r["filename"] for r in rows if r["default"]], [ALPHA])
        self.assertEqual([r["filename"] for r in rows if r["automatic"]], [CHARLIE])

    def test_the_rest_lens_says_which_table_the_game_is_locked_to(self) -> None:
        assert self.game.meta_config is not None
        self.game.meta_config["vpinfe"]["default_table"] = "tbl0000001"

        rows = table_lens.table_rows(self.game, {"game_dir": str(self.folder)})

        self.assertEqual([r["filename"] for r in rows if r["locked"]], [ALPHA])

    def test_the_rest_lens_locks_nothing_with_no_choice_recorded(self) -> None:
        rows = table_lens.table_rows(self.game, {"game_dir": str(self.folder)})

        self.assertEqual([r["filename"] for r in rows if r["locked"]], [])

    def test_a_locked_table_that_is_hidden_is_locked_and_not_the_default(self) -> None:
        assert self.game.meta_config is not None
        self.game.meta_config["vpinfe"]["default_table"] = "tbl0000002"

        rows = {r["filename"]: r for r in
                table_lens.table_rows(self.game, {"game_dir": str(self.folder)})}

        self.assertTrue(rows[BRAVO]["locked"])
        self.assertFalse(rows[BRAVO]["default"])

    def test_the_library_lens_says_where_a_cleared_choice_goes(self) -> None:
        assert self.game.meta_config is not None
        self.game.meta_config["vpinfe"]["default_table"] = "tbl0000001"

        with mock.patch.object(table_lens.game_repository, "catalog",
                               return_value={"Game00000001": self.game}):
            listed = TableRowList.model_validate(table_lens.library_rows())

        self.assertEqual([r.filename for r in listed.tables if r.automatic], [CHARLIE])

    def test_an_export_bundles_it(self) -> None:
        self.assertEqual(export_bundle.choose_table(self.folder), CHARLIE)


class EntryRowTests(TempTree):
    """Contract 2 describes the table an entry names, not the game's default."""

    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, FOLDER, info=INFO, vpx=False, files={
            ALPHA: b"vpx", BRAVO: b"vpx", CHARLIE: b"vpx",
            "(Wheel) Alpha.png": b"\x89PNG alpha wheel"})
        (self.game,) = GameParser(str(self.root)).get_all_games()

    def _row(self, table_id: str) -> dict:
        assert self.game.meta_config is not None
        table = self.game.meta_config["tables"][table_id]
        return game_state._entry_row(Entry(game=self.game, table=table, siblings=2), {})

    def test_the_path_is_the_entrys_own_file(self) -> None:
        self.assertEqual(Path(self._row("tbl0000001")["table"]["path"]).name, ALPHA)
        self.assertEqual(Path(self._row("tbl0000003")["table"]["path"]).name, CHARLIE)

    def test_the_media_is_what_the_entrys_own_table_resolves(self) -> None:
        self.assertIn("wheel", self._row("tbl0000001")["media"])
        self.assertNotIn("wheel", self._row("tbl0000003")["media"])
