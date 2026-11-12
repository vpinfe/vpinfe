from __future__ import annotations

import ast
import functools
import re
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from tests.support import trees
from tests.support.catalogs import served

CATALOG = served()
REPO = Path(__file__).resolve().parents[2]

CABINET = {
    "app.vpx.field.Player.BGSet.choice.1.label": "Visual Pinball's view for a cabinet's screen",
    "app.vpx.field.TableOverride.ViewCabMode.choice.2.help": "the physical cabinet",
    "app.vpx.field.TableOverride.ViewDTMode.choice.2.help": "the physical cabinet",
    "app.vpx.field.TableOverride.ViewFSSMode.choice.2.help": "the physical cabinet",
    "app.vpx.group.displays.heading.cabinet.label": "the physical cabinet the screen sits in",
    "app.vpx.group.point_of_view.heading.cabinet.label": "VPX's view for a cabinet's screen",
    "config.dof.config_tool_api_key.description": "the DOF Config Tool's own Cabinets page",
    "config.presentation.cab_mode.description": "playing standing at the physical cabinet",
    "config.presentation.cab_mode.label": "Cabinet Mode is named for the physical cabinet",
    "config.windows.playfield.orientation.description": "how a screen is mounted in it",
    "manager.screens.hint_cabinet": "where a screen sits in the physical cabinet",
    "manager.setup.glass.typical": "the physical cabinet's usual playfield tilt",
    "manager.presets.button.help": "Cabinet Mode, and a cabinet as the physical one",
    "manager.presets.cabinet": "the physical cabinet, the preset's own name",
    "manager.presets.cabinet_help": "the physical cabinet the preset is for",
    "manager.themes.both": "a theme made for a cabinet or a desktop",
    "manager.themes.cabinet": "a theme made for a cabinet rather than a desktop",
}

MACHINE: dict[str, str] = {
    "ext.vpinplay.error.card.no_machine_id": "VPinPlay's machineId field on a card",
}

BUILD = {
    "about.fact.build": "this VPinFE build",
    "app.vpx.no_plugins": "the VPX build beside the plugins",
    "manager.devices.newer_build": "a VPinFE build",
    "manager.devices.published_build": "a VPinFE build",
    "manager.settings.browser_slim_build": "this VPinFE build",
    "manager.needs.newer_build.tip": "this VPinFE build",
    "manager.needs.older_format.tip": "an older VPinFE build",
    "manager.sections.written_older": "an older VPinFE build",
    "ext.library_importer.reason.nothing_readable": "this VPinFE build",
    "extension.reason.platform_mismatch": "this VPinFE build",
    "frontend.buildmeta.started": "the verb",
    "frontend.mainmenu.build_metadata": "the verb",
    "frontend.mainmenu.metadata_menu": "the verb",
    "frontend.mainmenu.building_metadata": "the verb",
    "frontend.mainmenu.start_build": "the verb",
}

LOADS = "the files a table loads"
ASSETS_PAGE = "the Assets page, of the files a table loads"

ASSET = {
    "manager.discovery.gap.assets": LOADS,
    "manager.discovery.gap.backglass_asset": LOADS,
    "manager.discovery.gap.backglasses_asset": LOADS,
    "config.library.hidden_asset_kinds.label": LOADS,
    "manager.drop_sheet.section_with": LOADS,
    "manager.assets.assets": LOADS,
    "manager.assets.assets_missing": LOADS,
    "manager.assets.search_assets": LOADS,
    "manager.add.assets": ASSETS_PAGE,
    "manager.backups.kind.asset": "the copy of a file of the Assets page",
    "manager.page.assets": ASSETS_PAGE,
    "manager.section.assets": ASSETS_PAGE,
    "manager.settings.page_assets": ASSETS_PAGE,
    "manager.view.assets": ASSETS_PAGE,
    "manager.view.backups_assets": "the copies of the files the Assets page lists",
    "manager.workbench.assets": ASSETS_PAGE,
    "manager.workbench.assets_2": ASSETS_PAGE,
}

CATALOG_WORD: dict[str, str] = {}

COMPUTER: dict[str, str] = {}

WORDS = {"cabinet": CABINET, "machine": MACHINE, "build": BUILD, "asset": ASSET,
         "catalog": CATALOG_WORD, "computer": COMPUTER}

MEANT = {"cabinet": "the frontend", "machine": "a game, this device or another device",
         "build": "a table", "asset": "file",
         "catalog": "VPS for the database, sources for the places media comes from",
         "computer": "this device, or another device"}

# Setting names and served paths, which a person reads in a config file or a URL.
NAMED_ASSET = {
    "general.hidden_asset_kinds": LOADS,
    "library.hidden_asset_kinds": LOADS,
    "/assets": LOADS,
    "/games/{game_id}/asset_source": LOADS,
    "/games/{game_id}/assets": LOADS,
    "/games/{game_id}/assets/detail": LOADS,
    "/games/{game_id}/assets/text": LOADS,
    "/games/{game_id}/assets/{kind}": LOADS,
    "/games/{game_id}/assets/{kind}/displaced": LOADS,
    "/games/{game_id}/assets/{kind}/import": LOADS,
    "/games/{game_id}/assets/{kind}/placements": LOADS,
    "/games/{game_id}/assets/backglass/displays": LOADS,
    "/games/{game_id}/assets/backglass/displays/undo": LOADS,
    "/games/{game_id}/assets/backglass/picture": LOADS,
    "/games/{game_id}/tables/{table_id}/assets/backglass/picture": LOADS,
    "/games/{game_id}/assets/backglass/scoreview": LOADS,
    "/games/{game_id}/tables/{table_id}/assets/backglass/scoreview": LOADS,
    "/games/{game_id}/tables/{table_id}/assets/{kind}": LOADS,
}
PATH = re.compile(r"^/[A-Za-z_{][\w/{}.-]*$")
FRONTEND_SERVERS = ("frontend/theme_routes.py", "frontend/runtime.py")


# What /api/v1/docs and discovery show, keyed by where the string sits: in the OpenAPI
# document, or under `discovery.` in what GET /api/v1 answers.
API_CABINET: dict[str, str] = {}

API_MACHINE = {
    "components.schemas.GameResource.description": "defines a game as the pinball machine",
}

API_BUILD = {
    "components.schemas.Action.description": "this VPinFE build",
    "components.schemas.EntryOverrides.description": "the verb",
    "components.schemas.InfoMaintenance.description": "a VPinFE build",
    "components.schemas.UpdateCheck.description": "a VPinFE build",
    "paths./collections/{name}/members/from_filters.post.description": "the verb",
    "paths./library/scan.post.summary": "the verb",
    "paths./update.get.summary": "a VPinFE build",
    "paths./update.post.description": "a VPinFE build",
    "paths./update.post.summary": "a VPinFE build",
    "paths./uploads/{upload_id}/plan.post.summary": "the verb",
}

API_COMPUTER: dict[str, str] = {}

API_WORDS = {"cabinet": API_CABINET, "machine": API_MACHINE, "build": API_BUILD,
             "computer": API_COMPUTER}


def _says(value: Any, word: str) -> bool:
    forms = value.values() if isinstance(value, dict) else [value]
    return any(word in str(form).lower() for form in forms)


def unexplained(catalog: dict[str, Any], word: str, allowed: dict[str, str]) -> list[str]:
    return sorted(key for key, value in catalog.items()
                  if _says(value, word) and not allowed.get(key))


class EveryCatalog(unittest.TestCase):
    def test_each_use_of_the_words_is_on_its_list(self) -> None:
        for word, allowed in WORDS.items():
            with self.subTest(word=word):
                self.assertEqual(
                    unexplained(CATALOG, word, allowed), [],
                    f"say {MEANT[word]} if that is what it means; if not, add the key to "
                    f"{word.upper()} with what the word names there")

    def test_every_listed_key_still_says_its_word(self) -> None:
        for word, allowed in WORDS.items():
            with self.subTest(word=word):
                self.assertEqual(sorted(key for key in allowed
                                        if not _says(CATALOG.get(key, ""), word)), [])

    def test_the_sweep_reads_every_owner(self) -> None:
        owners = {key.split(".", 2)[0] for key in CATALOG}
        self.assertLessEqual({"app", "ext", "manager", "frontend", "config"}, owners)

    def test_it_can_fail(self) -> None:
        catalog = {"a": "Shown on the Cabinet", "b": {"one": "{count} build", "other": "x"},
                   "c": "Kept", "d": "Cabinet Mode"}

        self.assertEqual(unexplained(catalog, "cabinet", {"d": "the physical cabinet"}), ["a"])
        self.assertEqual(unexplained(catalog, "build", {}), ["b"])
        self.assertEqual(unexplained(catalog, "cabinet", {"a": "", "d": "x"}), ["a"])


def served_paths(tree: ast.AST) -> set[str]:
    return {node.value for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
            and PATH.match(node.value)}


def _names() -> set[str]:
    import httpapi
    from common.config import config_schema

    found = set()
    for option in config_schema.CONFIG_OPTIONS:
        found.add(f"{option.section}.{option.key}")
        found.update(str(alias) for alias in option.aliases or ())
        found.update(".".join(pair) for pair in option.legacy or ())
    found.update(httpapi.create_api_app().openapi()["paths"])
    for path in FRONTEND_SERVERS:
        found |= served_paths(trees.tree_for(REPO / path))
    return found


class SettingNamesAndServedPaths(unittest.TestCase):
    def test_each_that_says_asset_is_on_its_list(self) -> None:
        self.assertEqual(sorted(name for name in _names()
                                if "asset" in name.lower() and not NAMED_ASSET.get(name)), [],
                         "say file if that is what it means; if not, add it to NAMED_ASSET "
                         "with what the word names there")

    def test_every_listed_name_is_still_served(self) -> None:
        self.assertEqual(sorted(set(NAMED_ASSET) - _names()), [])

    def test_the_sweep_reads_the_frontend_s_routes_and_mounts(self) -> None:
        found = set()
        for path in FRONTEND_SERVERS:
            found |= served_paths(trees.tree_for(REPO / path))
        self.assertLessEqual({"/media/{table_id}/{kind}", "/manufacturers/{slug}/logo", "/themes/"},
                             found)

    def test_it_would_see_a_served_path(self) -> None:
        planted = served_paths(trees.parse_snippet(
            'mount_points["/assets/"] = folder\n'
            'if request_path.startswith("/assets/"): pass\n'))

        self.assertEqual(planted, {"/assets/"})


def api_text(document: dict[str, Any]) -> dict[str, str]:
    """Every `summary` and `description` string in an OpenAPI document, at any depth, by
    where it sits: `paths./games/{game_id}.get.summary`, a parameter by its name."""
    found: dict[str, str] = {}

    def walk(node: Any, where: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                here = f"{where}.{key}" if where else key
                # A model field called `description` is a schema, not a string: walk into it.
                if key in ("summary", "description") and isinstance(value, str):
                    found[here] = value
                else:
                    walk(value, here)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                name = item.get("name") if isinstance(item, dict) else None
                walk(item, f"{where}[{name if isinstance(name, str) else index}]")

    walk(document, "")
    return found


@functools.cache
def _served() -> tuple[dict[str, str], frozenset[str]]:
    """The words, and the capabilities declared when they were read."""
    import httpapi
    from httpapi import capabilities, instance

    document = api_text(httpapi.create_api_app().openapi())
    declared = frozenset(capabilities._CAPABILITIES)
    # Every capability, whichever features this config switches on.
    with patch.object(capabilities, "_enabled_features", return_value=capabilities.FEATURES):
        discovery = instance.discovery_payload(httpapi.API_PREFIX, httpapi.API_VERSION)
    return {**document, **api_text({"discovery": discovery})}, declared


def _served_api_text() -> dict[str, str]:
    return _served()[0]


class TheApiDocument(unittest.TestCase):
    maxDiff = None

    def test_each_use_of_the_words_is_on_its_list(self) -> None:
        for word, allowed in API_WORDS.items():
            with self.subTest(word=word):
                self.assertEqual(
                    unexplained(_served_api_text(), word, allowed), [],
                    f"say {MEANT[word]} if that is what it means; if not, add the site to "
                    f"API_{word.upper()} with what the word names there")

    def test_every_listed_site_still_says_its_word(self) -> None:
        text = _served_api_text()
        for word, allowed in API_WORDS.items():
            with self.subTest(word=word):
                self.assertEqual(sorted(site for site in allowed
                                        if not _says(text.get(site, ""), word)), [])

    def test_the_sweep_reads_operations_parameters_and_models(self) -> None:
        sites = _served_api_text()
        self.assertTrue(any(".get.summary" in site for site in sites))
        self.assertTrue(any(".parameters[" in site for site in sites))
        self.assertTrue(any(site.startswith("components.schemas.") for site in sites))

    def test_the_sweep_reads_every_capability_discovery_can_serve(self) -> None:
        text, declared = _served()
        served = {site for site in text if site.startswith("discovery.")}
        self.assertIn("discovery.capabilities[launch].description", served)
        self.assertEqual({f"discovery.capabilities[{name}].description"
                          for name in declared}, served)

    def test_it_reads_every_depth(self) -> None:
        document = {
            "info": {"description": "I"},
            "paths": {"/a": {"get": {"summary": "S", "parameters": [
                {"name": "t", "description": "P", "schema": {"description": "Q"}}]}}},
            "components": {"schemas": {"M": {"description": "M", "properties": {
                "description": {"type": "string", "description": "F"}}}}},
        }
        self.assertEqual(api_text(document), {
            "info.description": "I",
            "paths./a.get.summary": "S",
            "paths./a.get.parameters[t].description": "P",
            "paths./a.get.parameters[t].schema.description": "Q",
            "components.schemas.M.description": "M",
            "components.schemas.M.properties.description.description": "F",
        })


if __name__ == "__main__":
    unittest.main()
