"""What a person calls a theme source: kept apart from the address lists, shown on the
row, and handed back by the address however it is spelled."""

from __future__ import annotations

import unittest
from unittest import mock

from common import service_errors
from common.config.config_store import ConfigStore
from common.online import theme_ops, theme_sources
from tests.support.library import TempTree

LIST = "https://themes.example.net/someone/themes/raw/branch/main/themes.json"
REPO = "https://github.com/someone/pack"


class SourceNameTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.ini = str(self.root / "vpinfe.ini")
        store = ConfigStore(self.ini)
        store.set_value("themes", "registries", LIST)
        store.set_value("themes", "repositories", REPO)
        store.save()
        self.enterContext(mock.patch.object(
            theme_ops, "get_ini_config", side_effect=lambda: ConfigStore(self.ini)))
        self.enterContext(mock.patch.object(theme_ops, "_known", return_value=None))

    def _labels(self) -> dict[str, str]:
        shown = theme_ops.sources()
        return {one["address"]: one["label"] for group in ("registries", "repositories")
                for one in shown[group]}

    def test_a_source_nobody_named_has_no_label(self) -> None:
        self.assertEqual(self._labels(), {LIST: "", REPO: ""})

    def test_a_name_is_shown_on_its_source_and_on_no_other(self) -> None:
        theme_ops.rename_source(LIST, "Dave's picks")

        self.assertEqual(self._labels(), {LIST: "Dave's picks", REPO: ""})

    def test_the_address_lists_are_left_as_they_were(self) -> None:
        theme_ops.rename_source(LIST, "Dave's picks")

        said = theme_sources.from_config(ConfigStore(self.ini))
        self.assertEqual((said.registries, said.repositories), ((LIST,), (REPO,)))

    def test_a_comma_and_a_percent_sign_survive_the_file(self) -> None:
        name = "Dave's picks, 100% of them"

        theme_ops.rename_source(REPO, name)

        self.assertEqual(self._labels()[REPO], name)

    def test_a_blank_name_takes_it_back(self) -> None:
        theme_ops.rename_source(LIST, "Dave's picks")

        answer = theme_ops.rename_source(LIST, "   ")

        self.assertEqual((answer["label"], self._labels()[LIST]), ("", ""))

    def test_the_address_may_be_spelled_the_way_it_is_pasted(self) -> None:
        theme_ops.rename_source("https://www.github.com/someone/pack.git", "Pack")

        self.assertEqual(self._labels()[REPO], "Pack")

    def test_an_address_that_does_not_parse_can_still_be_named(self) -> None:
        store = ConfigStore(self.ini)
        store.set_value("themes", "registries", f"{LIST},not an address")
        store.save()

        theme_ops.rename_source("not an address", "Odd one")

        self.assertEqual(self._labels()["not an address"], "Odd one")

    def test_an_address_that_is_not_added_is_not_found(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            theme_ops.rename_source("https://example.net/other/themes.json", "Other")

    def test_a_name_outlives_its_source_so_adding_it_back_finds_it(self) -> None:
        theme_ops.rename_source(LIST, "Dave's picks")
        store = ConfigStore(self.ini)
        store.set_value("themes", "registries", "")
        store.save()
        self.assertEqual(self._labels(), {REPO: ""})

        store.set_value("themes", "registries", LIST)
        store.save()

        self.assertEqual(self._labels()[LIST], "Dave's picks")


class CleanNameTests(unittest.TestCase):
    def test_a_name_is_one_line_with_single_spaces(self) -> None:
        self.assertEqual(theme_sources.clean_name("  My \n  list\t one "), "My list one")

    def test_a_name_is_cut_at_the_limit(self) -> None:
        self.assertEqual(len(theme_sources.clean_name("x" * 500)), theme_sources.NAME_LIMIT)


if __name__ == "__main__":
    unittest.main()
