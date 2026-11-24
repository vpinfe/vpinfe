"""An icon's size is a token or sized to its own text, never a pixel count in place."""

from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SHEET = ROOT / "manager" / "static" / "manager-base.css"

RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
SIZE = re.compile(r"font-size:\s*([^;!}]+)")
# A selector naming one of these is drawing a glyph rather than words.
DRAWS_A_GLYPH = ("icon", "mark", "glyph")
RELATIVE = ("em", "%")


def _icon_sizes() -> list[tuple[str, str]]:
    """Every font-size on a rule that draws a glyph, with the selector it sits on."""
    found = []
    for rule in RULE.finditer(SHEET.read_text(encoding="utf-8")):
        selector = rule.group(1).strip().splitlines()[-1].strip()
        if not any(word in selector.lower() for word in DRAWS_A_GLYPH):
            continue
        said = SIZE.search(rule.group(2))
        if said:
            found.append((said.group(1).strip(), selector))
    return found


class AnIconSizeIsNamed(unittest.TestCase):
    def test_it_found_the_rules(self) -> None:
        """Or the check below passes by reading nothing."""
        self.assertGreater(len(_icon_sizes()), 10)

    def test_no_glyph_carries_a_pixel_count(self) -> None:
        loose = [f"{selector} -> {size}" for size, selector in _icon_sizes()
                 if "var(" not in size and not size.endswith(RELATIVE)]

        self.assertEqual(loose, [], "name the size in appearance.py and use the token")

    def test_every_token_a_glyph_names_is_declared(self) -> None:
        from manager.kit import appearance

        wanted = {name for size, _ in _icon_sizes()
                  for name in re.findall(r"var\((--[a-z0-9-]+)\)", size)}
        for mode in appearance.PALETTES:
            css = appearance.palette_css(mode)
            missing = sorted(name for name in wanted if f"{name}:" not in css)
            with self.subTest(mode=mode):
                self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
