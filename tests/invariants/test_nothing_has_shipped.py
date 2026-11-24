"""Machinery kept for nobody, and values a reader cannot use.

Two shapes that outlive the reasoning that produced them: a second name for a key no
released version ever wrote, and a value published to a consumer that refuses it.
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from common.config import config_schema

ROOT = Path(__file__).resolve().parents[2]
LEGACY = ROOT / "tests" / "fixtures" / "config_legacy_names.json"


def _names_2x() -> set[tuple[str, str, str]]:
    """Every 2.x key, against the option it became: (old_key, new_section, new_key)."""
    rows = json.loads(LEGACY.read_text(encoding="utf-8"))
    return {(str(old_key).lower(), str(section).lower(), str(key).lower())
            for _old_section, old_key, section, key in rows}


class CompatNamesAnswerForSomethingReal(unittest.TestCase):
    def test_every_alias_names_a_key_2x_wrote(self) -> None:
        known = _names_2x()
        stray = []
        for option in config_schema.options():
            for alias in option.aliases:
                if (alias.lower(), option.section.lower(), option.key.lower()) not in known:
                    stray.append(f"{option.section}.{option.key} <- {alias}")
        self.assertEqual(
            [], stray,
            "these keep a second name for a key no 2.x file carries, so nothing on any "
            f"disk resolves through them: {stray}")

    def test_every_legacy_pair_names_a_key_2x_wrote(self) -> None:
        rows = json.loads(LEGACY.read_text(encoding="utf-8"))
        known = {(str(a).lower(), str(b).lower()) for a, b, _c, _d in rows}
        stray = []
        for option in config_schema.options():
            for section, key in option.legacy:
                if (str(section).lower(), str(key).lower()) not in known:
                    stray.append(f"{option.section}.{option.key} <- [{section}] {key}")
        self.assertEqual(
            [], stray,
            f"these read a 2.x name the recorded mapping does not have: {stray}")


class APublishedValueIsOneItsReaderAccepts(unittest.TestCase):
    """`entry.tutorial` is fetched through the frontend's proxy, and that proxy allows
    one host. A url it refuses reaches the player as a 403."""

    def test_the_contract_1_tutorial_is_always_proxyable(self) -> None:
        from common.games.game_metadata import contract_1_tutorial
        from common.games.info_file import GUIDES_KEY, PINBALL_PRIMER_PREFIX
        from frontend import theme_routes

        guides = [
            {"kind": "tutorial", "url": "https://www.youtube.com/watch?v=a"},
            {"kind": "tutorial", "url": "https://vpuniverse.com/files/file/1"},
            {"kind": "tutorial", "url": "https://tiltforums.com/t/x"},
            {"kind": "tutorial", "url": "", "youtube_id": "abc"},
            {"kind": "tutorial", "url": PINBALL_PRIMER_PREFIX + "x.html"},
        ]
        for take in range(len(guides) + 1):
            answered = contract_1_tutorial({GUIDES_KEY: guides[:take]})
            if not answered:
                continue
            with self.subTest(guides=take):
                self.assertTrue(
                    theme_routes._is_allowed_pinball_primer_url(answered),
                    f"contract 1 published {answered!r}, which the proxy answers 403 to")


if __name__ == "__main__":
    unittest.main()
