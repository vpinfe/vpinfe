"""A device with no launcher is one state, and every surface says it in the same words."""

from __future__ import annotations

import json
import unittest

from tests.support.catalogs import served

CATALOG = served()

SAID = "This device has no launcher"

SAYS_IT = (
    "manager.workbench.no_launcher",
    "manager.workbench.launcher_hint",
    "error.capabilities.no_launcher",
    "error.features.no_launcher",
    "error.launch.no_launcher_configured",
    "error.pinmame.no_launcher",
)

# The Launchers list's own empty state, "No launchers yet", is a list with nothing in it
# rather than a sentence about the device, and is not one of these.
RETIRED = ("launcher configured", "no launcher yet", "has no launchers")


def retired_spellings(catalog: dict) -> list[str]:
    return sorted(key for key, value in catalog.items()
                  if any(old in json.dumps(value).lower() for old in RETIRED))


class NoLauncher(unittest.TestCase):
    def test_each_leads_with_the_same_words(self) -> None:
        self.assertEqual([key for key in SAYS_IT
                          if not str(CATALOG.get(key, "")).startswith(SAID)], [])

    def test_no_string_says_it_another_way(self) -> None:
        self.assertEqual(retired_spellings(CATALOG), [])

    def test_it_can_fail(self) -> None:
        self.assertEqual(retired_spellings({"a": "No launcher configured.", "b": SAID}), ["a"])


if __name__ == "__main__":
    unittest.main()
