"""What AG Grid reads out to a screen reader comes from the catalog, all of it."""

import re
import unittest
from pathlib import Path

import nicegui

from common import i18n

BUNDLE = Path(nicegui.__file__).parent / "elements" / "aggrid" / "dist" / "index.js"


class TheGridsSpokenWords(unittest.TestCase):
    def test_every_aria_key_the_grid_asks_for_is_in_the_catalog(self) -> None:
        source = BUNDLE.read_text(encoding="utf-8")
        asked = set(re.findall(r'"(aria[A-Z]\w+)"', source)) \
            - set(re.findall(r'beanName="(\w+)"', source))
        self.assertTrue(asked, f"no aria keys found in {BUNDLE}")
        self.assertEqual([], sorted(asked - set(i18n.under("grid"))))


if __name__ == "__main__":
    unittest.main()
