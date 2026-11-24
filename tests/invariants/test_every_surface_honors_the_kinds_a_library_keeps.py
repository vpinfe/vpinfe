"""A kind switched off in Settings > Library > Assets is absent from every surface that
reports one.

Each surface is listed here, so a new one is added to the list or it is not known to
honor the setting. The API serves every kind either way; the Manager decides what it shows.
"""

from __future__ import annotations

import unittest
from collections.abc import Callable
from unittest.mock import Mock

from common.i18n import t
from manager.kit.data import Library
from manager.library import discovery, games
from manager.settings import sections
from manager.workbench import core, game_assets

HIDDEN_ASSETS = ("ini", "rom", "readme")
HIDDEN_MEDIA = ("topper",)
KEPT_ASSETS = ("backglass", "script", "pup_pack")
KEPT_MEDIA = ("wheel",)
TABLE = "t-1"


def _library() -> Library:
    client = Mock()
    client.library_policy.return_value = {"hidden_asset_kinds": list(HIDDEN_ASSETS),
                                          "hidden_media_kinds": list(HIDDEN_MEDIA)}
    library = Library(client)
    library.load_kept_kinds()
    library._table_rows = []
    library.games = [{"id": "g-1", "assets": {kind: {"present": True}
                                              for kind in (*HIDDEN_ASSETS, *KEPT_ASSETS)}}]
    library.media = {"g-1": {kind: {"present": True}
                             for kind in (*HIDDEN_MEDIA, *KEPT_MEDIA)}}
    return library


def _asset_files() -> list[dict]:
    return [{"id": f"g-1:{kind}:", "game_id": "g-1", "kind": kind, "present": True}
            for kind in (*HIDDEN_ASSETS, *KEPT_ASSETS)]


def _media_files() -> list[dict]:
    return [{"id": f"g-1:{kind}:", "game_id": "g-1", "kind": kind, "present": True}
            for kind in (*HIDDEN_MEDIA, *KEPT_MEDIA)]


def _panel_context(library: Library) -> dict:
    states = {kind: {"resolution": "game", "file": f"{kind}.x"}
              for kind in (*HIDDEN_ASSETS, *KEPT_ASSETS)}
    return {"library": library, "lens": TABLE, "rebuild": object(),
            "game": {"assets": {kind: {"present": True}
                                for kind in (*HIDDEN_ASSETS, *KEPT_ASSETS)}},
            "tables": [{"id": TABLE, "assets": states,
                        "dependencies": {"pinmame": {"declared": "rom_1", "installed": True}}}]}


def _asset_kinds_on_the_assets_grid(library: Library) -> set[str]:
    return {row["kind"] for row in library._kept_assets(_asset_files())}


def _media_kinds_on_the_media_grid(library: Library) -> set[str]:
    return {row["kind"] for row in library._kept_media(_media_files())}


def _media_kinds_on_the_library_card(library: Library) -> set[str]:
    return {kind for kind, _held in sections.kind_holdings(library)}


def _asset_kinds_in_the_games_grid(library: Library) -> set[str]:
    return {column["field"].removeprefix("asset_")
            for column in games.asset_columns(games.folder_asset_keys(library))}


def _asset_kinds_in_the_tables_grid(library: Library) -> set[str]:
    return {column["field"].removeprefix("asset_")
            for column in games.table_asset_columns(games.table_asset_keys(library))}


def _asset_kinds_in_the_table_panel(library: Library) -> set[str]:
    labels = {core._asset_name(kind): kind for kind in (*HIDDEN_ASSETS, *KEPT_ASSETS)}
    named = (core._asset_name(kind)
             for kind, _draw in game_assets._asset_sections(_panel_context(library), []))
    return {labels[name] for name in named if name in labels}


def _kinds_in_the_discovery_pane(library: Library) -> set[str]:
    return {kind for listed_as in discovery.kept_listings(library)
            for kind in discovery.kept_ours(library, listed_as)}


def _kinds_in_the_discovery_grid(library: Library) -> set[str]:
    drawn = {one["field"] for one in discovery.grid_columns(library, [])}
    return {kind for listed_as, field, _word in discovery.KINDS if field in drawn
            for kind in discovery.kept_ours(library, listed_as)}


def _counted(library: Library) -> int:
    """How many files the panel header says are here."""
    said = game_assets._assets_label(_panel_context(library))
    for have in range(len(HIDDEN_ASSETS) + len(KEPT_ASSETS) + 1):
        if said == t("manager.workbench.assets_2", have=have):
            return have
    return 0


SURFACES: dict[str, tuple[Callable[[Library], set[str]], set[str]]] = {
    "the Assets grid": (_asset_kinds_on_the_assets_grid, set(HIDDEN_ASSETS)),
    "the Media grid": (_media_kinds_on_the_media_grid, set(HIDDEN_MEDIA)),
    "the Library card's bars": (_media_kinds_on_the_library_card, set(HIDDEN_MEDIA)),
    "the Games grid's asset columns": (_asset_kinds_in_the_games_grid, set(HIDDEN_ASSETS)),
    "the Tables grid's asset columns": (_asset_kinds_in_the_tables_grid,
                                        set(HIDDEN_ASSETS)),
    "the table panel's Assets block": (_asset_kinds_in_the_table_panel,
                                       set(HIDDEN_ASSETS)),
    "the Discovery pane's files": (_kinds_in_the_discovery_pane,
                                   {*HIDDEN_ASSETS, *HIDDEN_MEDIA}),
    "the Discovery grid's kind columns": (_kinds_in_the_discovery_grid,
                                          {*HIDDEN_ASSETS, *HIDDEN_MEDIA}),
}


class EverySurfaceHonorsTheKinds(unittest.TestCase):
    def test_a_kind_switched_off_is_on_none_of_them(self) -> None:
        library = _library()

        shown = {name: reports(library) & hidden
                 for name, (reports, hidden) in SURFACES.items()}

        self.assertEqual({name: left for name, left in shown.items() if left}, {})

    def test_a_kind_switched_on_is_on_every_one(self) -> None:
        library = _library()
        kept = {"the Assets grid": set(KEPT_ASSETS), "the Media grid": set(KEPT_MEDIA),
                "the Library card's bars": set(KEPT_MEDIA),
                "the Games grid's asset columns": set(KEPT_ASSETS) - set(games.TABLE_ASSET_KEYS),
                "the Tables grid's asset columns": set(KEPT_ASSETS) & set(games.TABLE_ASSET_KEYS),
                "the table panel's Assets block": set(KEPT_ASSETS),
                "the Discovery pane's files": {"backglass", "pup_pack", *KEPT_MEDIA},
                "the Discovery grid's kind columns": {"backglass", "pup_pack", *KEPT_MEDIA}}

        absent = {name: kept[name] - reports(library)
                  for name, (reports, _hidden) in SURFACES.items()}

        self.assertEqual({name: left for name, left in absent.items() if left}, {})


class WhatTheCountsAdd(unittest.TestCase):
    def test_the_panel_header_counts_only_the_kinds_collected(self) -> None:
        self.assertEqual(_counted(_library()), len(KEPT_ASSETS))

    def test_the_table_has_no_switch(self) -> None:
        self.assertTrue(game_assets._collects("table", set()))


if __name__ == "__main__":
    unittest.main()
