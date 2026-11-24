"""A requirement finding sends someone to a page, and that page is in their rail."""

from __future__ import annotations

import itertools
import unittest
from configparser import ConfigParser

from common.devices import install_identity
from common.settings import feature_checks
from manager import page
from manager.settings import settings


def _findings(on: list[str]) -> list[feature_checks.Unmet]:
    return feature_checks.unmet(
        ConfigParser(),  # type: ignore[arg-type]
        features=on, launcher=None, locations=[])


class EveryFindingHasAPageTests(unittest.TestCase):
    def test_every_page_a_finding_names_is_in_that_device_s_rail(self) -> None:
        switchable = list(install_identity.FEATURES)
        for size in range(len(switchable) + 1):
            for chosen in itertools.combinations(switchable, size):
                on = [install_identity.CORE, *chosen]
                rail = {key for _parent, items in page.nav_for(on) for key, *_rest in items}
                with self.subTest(features=chosen):
                    self.assertEqual({one.where for one in _findings(on)} - rail, set())
                    held = {item[0] for _group, item in settings.system_pages(on)}
                    self.assertEqual(
                        set(settings.pages_in_trouble(_findings(on))) - held, set())

    def test_every_page_a_finding_names_is_one_the_rail_marks(self) -> None:
        wheres = {one.where for one in _findings(list(install_identity.FEATURES))}

        self.assertLessEqual(wheres, set(feature_checks.WHERES))

    def test_a_frontend_only_device_is_among_the_cases_it_checks(self) -> None:
        """Nothing configured gives it a finding about its library folders, and the
        Folders page is where that one is fixed."""
        found = _findings([install_identity.FRONTEND])

        self.assertIn(feature_checks.WHERE_SETTINGS, {one.where for one in found})
        self.assertIn("library.folders", settings.pages_in_trouble(found))

    def test_the_folders_page_is_in_the_rail_of_every_device_that_has_the_finding(
            self) -> None:
        for chosen in ([install_identity.LIBRARY], [install_identity.FRONTEND]):
            with self.subTest(features=chosen):
                pages = {item[0] for _group, item in settings.system_pages(chosen)}
                self.assertIn("library.folders", pages)


if __name__ == "__main__":
    unittest.main()
