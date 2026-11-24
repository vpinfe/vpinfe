"""A theme VPinFE did not get from the VPinFE Theme List stays stopped until accepted."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import service_errors
from common.config import paths
from common.config.config_store import ConfigStore
from common.online import theme_ops, theme_sources
from common.security import accepted
from frontend import theme_api
from tests.support.accepted import isolated

STOCK = theme_sources.stock_list()
OTHER = "https://git.example.net/someone/themes/raw/branch/main/themes.json"
ELSEWHERE = "https://git.example.net/other/themes/raw/branch/main/themes.json"


class _Registry:
    """What `theme_ops` asks a registry, with the installed themes named by the test."""

    def __init__(self, themes: dict, local: dict | None = None,
                 installed: tuple[str, ...] = ()) -> None:
        self.themes, self._local, self._installed = themes, local or {}, installed
        self.themes_dir = "themes"

    def get_installed_folder(self, key: str) -> None:
        return None

    def get_themes(self) -> dict:
        return self.themes

    def check_for_updates(self, keys: list) -> dict:
        return {}

    def is_installed(self, key: str) -> bool:
        return key in self._installed or key in self._local

    def local_themes(self) -> dict:
        return self._local

    def installs_on_its_own(self, key: str) -> bool:
        return False


def _offered(source: str) -> dict:
    return {"manifest": {"name": "Any"}, "registry_info": {"url": "https://x.net/o/r"},
            "source": source}


class Case(unittest.TestCase):
    def setUp(self) -> None:
        isolated(self)
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        self.root = Path(holder.name)
        self.themes = self.root / "themes"
        self.themes.mkdir()
        patched = mock.patch.object(paths, "THEMES_DIR", self.themes)
        patched.start()
        self.addCleanup(patched.stop)

    def listed(self, registry: _Registry) -> dict:
        with mock.patch.object(theme_ops.theme_service, "load_theme_option_schema",
                               return_value=None), \
                mock.patch.object(theme_ops, "get_ini_config",
                                  return_value=ConfigStore(str(self.root / "vpinfe.ini"))):
            return {one["key"]: one["trust"] for one in theme_ops._described(registry, "")}  # type: ignore[arg-type]

    def through(self, registry: _Registry):
        return mock.patch.object(theme_ops, "_readable", return_value=registry)


class WhatTheListingSays(Case):
    def test_a_theme_from_the_stock_list_is_stock_and_needs_nothing(self) -> None:
        trust = self.listed(_Registry({"Stock": _offered(STOCK)}))["Stock"]

        self.assertEqual(trust["origin"], accepted.STOCK)
        self.assertTrue(trust["accepted"])
        self.assertTrue(trust["install_accepted"])

    def test_an_installed_stock_theme_with_no_entry_is_stopped_until_accepted(self) -> None:
        registry = _Registry({"Stock": _offered(STOCK)}, installed=("Stock",))
        accepted.theme_runs("Stock")

        trust = self.listed(registry)["Stock"]
        self.assertEqual((trust["origin"], trust["accepted"]), (accepted.STOCK, False))

        accepted.accept_theme("Stock", STOCK)

        self.assertTrue(self.listed(registry)["Stock"]["accepted"])

    def test_a_theme_from_another_source_is_not_accepted_before_it_is_installed(self) -> None:
        trust = self.listed(_Registry({"Mine": _offered(OTHER)}))["Mine"]

        self.assertEqual((trust["origin"], trust["accepted"]), (accepted.SOURCE, False))
        self.assertEqual(trust["source"], OTHER)

    def test_one_installed_and_never_accepted_is_stopped(self) -> None:
        registry = _Registry({"Mine": _offered(OTHER)}, installed=("Mine",))
        accepted.theme_runs("Mine")

        self.assertFalse(self.listed(registry)["Mine"]["accepted"])

    def test_one_installed_and_accepted_runs_and_is_still_not_from_the_stock_list(self) -> None:
        accepted.accept_theme("Mine", OTHER)

        trust = self.listed(_Registry({"Mine": _offered(OTHER)}, installed=("Mine",)))["Mine"]

        self.assertEqual((trust["origin"], trust["accepted"]), (accepted.SOURCE, True))

    def test_an_update_from_a_different_address_asks_again(self) -> None:
        accepted.accept_theme("Mine", OTHER)

        trust = self.listed(_Registry({"Mine": _offered(ELSEWHERE)}, installed=("Mine",)))["Mine"]

        self.assertTrue(trust["accepted"], "the copy already here keeps running")
        self.assertFalse(trust["install_accepted"], "the new one has to be accepted")

    def test_a_theme_found_in_the_folder_says_so(self) -> None:
        trust = self.listed(_Registry({}, local={"found": {"name": "Found"}}))["found"]

        self.assertEqual((trust["origin"], trust["source"]), (accepted.IN_FOLDER, ""))

    def test_themes_there_before_the_list_existed_count_as_accepted(self) -> None:
        (self.themes / "Old").mkdir()

        trust = self.listed(_Registry({}, local={"Old": {"name": "Old"}}))["Old"]

        self.assertTrue(trust["accepted"])
        self.assertEqual(trust["origin"], accepted.IN_FOLDER, "and it keeps its mark")

    def test_the_stopped_ones_are_listed_without_reading_any_source(self) -> None:
        (self.themes / "Old").mkdir()
        accepted.theme_runs("Old")
        (self.themes / "Later").mkdir()
        (self.themes / "Later" / "manifest.json").write_text('{"name": "Later Theme"}')

        self.assertEqual(theme_ops.unaccepted(), {"themes": [{"key": "Later",
                                                              "name": "Later Theme"}]})


class WhatInstallingDoes(Case):
    def test_a_theme_that_is_not_accepted_is_refused_and_nothing_is_fetched(self) -> None:
        registry = _Registry({"Mine": _offered(OTHER)})

        with self.through(registry), \
                mock.patch.object(theme_ops.theme_service, "install_theme") as install:
            with self.assertRaises(service_errors.RefusedError):
                theme_ops.install("Mine")

        install.assert_not_called()

    def test_once_accepted_from_that_address_it_installs(self) -> None:
        registry = _Registry({"Mine": _offered(OTHER)})
        with self.through(registry):
            theme_ops.accept("Mine")
            with mock.patch.object(theme_ops.theme_service, "install_theme") as install:
                theme_ops.install("Mine")

        install.assert_called_once()

    def test_a_theme_from_the_stock_list_installs_without_asking(self) -> None:
        registry = _Registry({"Stock": _offered(STOCK)})

        with self.through(registry), \
                mock.patch.object(theme_ops.theme_service, "install_theme") as install:
            theme_ops.install("Stock")

        install.assert_called_once()

    def test_a_removed_theme_has_to_be_accepted_again(self) -> None:
        accepted.accept_theme("Mine", OTHER)
        registry = _Registry({"Mine": _offered(OTHER)}, installed=("Mine",))

        with self.through(registry), \
                mock.patch.object(theme_ops.theme_service, "get_active_theme",
                                  return_value="Other"), \
                mock.patch.object(theme_ops.theme_service, "delete_theme"):
            theme_ops.remove("Mine")

        self.assertFalse(accepted.theme_accepted("Mine", OTHER, stock=False))


class WhatAcceptingDoes(Case):
    def test_the_address_comes_from_the_source_that_offers_it_never_from_the_caller(self) -> None:
        with self.through(_Registry({"Mine": _offered(OTHER)})):
            theme_ops.accept("Mine")

        self.assertTrue(accepted.theme_accepted("Mine", OTHER, stock=False))
        self.assertFalse(accepted.theme_accepted("Mine", ELSEWHERE, stock=False))

    def test_a_theme_in_the_folder_no_source_offers_is_accepted_as_found_there(self) -> None:
        with self.through(_Registry({}, local={"found": {"name": "Found"}})):
            trust = theme_ops.accept("found")

        self.assertEqual(trust["origin"], accepted.IN_FOLDER)
        self.assertTrue(accepted.theme_runs("found"))

    def test_a_theme_nothing_offers_is_not_found(self) -> None:
        with self.through(_Registry({})):
            with self.assertRaises(service_errors.NotFoundError):
                theme_ops.accept("nothing")


class WhatMakingItActiveDoes(Case):
    def test_a_theme_that_is_not_accepted_cannot_be_made_active(self) -> None:
        registry = _Registry({}, installed=("Later",))
        (self.themes / "Old").mkdir()
        accepted.theme_runs("Old")
        (self.themes / "Later").mkdir()

        with self.through(registry), \
                mock.patch.object(theme_ops.theme_service, "set_active_theme") as set_active:
            with self.assertRaises(service_errors.RefusedError):
                theme_ops.activate("Later")

        set_active.assert_not_called()


class WhatThePlayerPlays(Case):
    def config(self, chosen: str) -> ConfigStore:
        store = ConfigStore(str(self.root / "vpinfe.ini"))
        store.set_value("themes", "active", chosen)
        return store

    def test_a_theme_nobody_accepted_gives_way_to_the_default(self) -> None:
        theme_api._warned.clear()

        with self.assertLogs("vpinfe", "WARNING"):
            played = theme_api.get_theme_name(self.config("Later"))

        self.assertEqual(played, accepted.default_theme())

    def test_an_accepted_theme_is_played(self) -> None:
        accepted.accept_theme("Mine", OTHER)

        self.assertEqual(theme_api.get_theme_name(self.config("Mine")), "Mine")

    def test_the_default_is_played_with_nothing_accepted(self) -> None:
        default = accepted.default_theme()

        self.assertEqual(theme_api.get_theme_name(self.config(default)), default)


class AStockTitleBelongsToTheStockList(Case):
    def setUp(self) -> None:
        super().setUp()
        self.ini = str(self.root / "vpinfe.ini")
        store = ConfigStore(self.ini)
        store.set_value("themes", "registries", f"{STOCK}, {OTHER}")
        store.save()
        for target in (theme_ops,):
            patched = mock.patch.object(target, "get_ini_config",
                                        side_effect=lambda: ConfigStore(self.ini))
            patched.start()
            self.addCleanup(patched.stop)
        patched = mock.patch.object(theme_ops, "_known", return_value=None)
        patched.start()
        self.addCleanup(patched.stop)

    def title(self) -> str:
        from common.i18n import t
        return t("manager.theme_sources.stock")

    def test_another_source_cannot_be_given_it(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            theme_ops.rename_source(OTHER, self.title())

    def test_whatever_the_case_or_spacing(self) -> None:
        with self.assertRaises(service_errors.RefusedError):
            theme_ops.rename_source(OTHER, f"  {self.title().upper()} ")

    def test_the_stock_list_may_keep_it(self) -> None:
        theme_ops.rename_source(STOCK, self.title())

    def test_a_name_written_into_the_settings_by_hand_is_ignored_on_another_address(self) -> None:
        store = ConfigStore(self.ini)
        theme_sources.keep_names(store, {STOCK: self.title()})
        store.config.set(theme_sources.NAMES_SECTION, theme_sources._name_key(OTHER),
                         self.title())

        self.assertEqual(theme_sources.name_of_source(store, OTHER), "")
        self.assertEqual(theme_sources.name_of_source(store, STOCK), self.title())


if __name__ == "__main__":
    unittest.main()
