"""On screen, the files a game shows are media; art is only the picture inside a backglass."""

from __future__ import annotations

import re
import unittest
from pathlib import Path
from typing import Any

from tests.support.catalogs import served

CATALOG = served()

ART = re.compile(r"\bart(?:works?)?\b", re.IGNORECASE)

# A backglass file carries a picture of its own, and a credit for whoever drew it. Neither
# is a media file a game has or lacks, so neither is the Media view's word.
THE_BACKGLASS_PICTURE = "the picture inside a .directb2s, as the file and the B2S plugin call it"
ALLOWED = {
    "manager.backglass.artwork": "the artist's credit, the file's own Artwork field",
    "manager.backglass.dmd_fit": THE_BACKGLASS_PICTURE,
    "manager.backglass.dmd_fit.none": THE_BACKGLASS_PICTURE,
    "manager.backglass.preview.fit": THE_BACKGLASS_PICTURE,
    "manager.backglass.score_art": THE_BACKGLASS_PICTURE,
    "error.backglass.scoreview_absent": THE_BACKGLASS_PICTURE,
    **{f"app.vpx.field.Plugin.{plugin}.{field}.help": THE_BACKGLASS_PICTURE
       for plugin in ("B2S", "B2SLegacy")
       for field in ("BackglassDMDAutoPos", "BackglassDMDOverlay", "ScoreViewDMDAutoPos",
                     "ScoreViewDMDOverlay")},
    "app.vpx.field.Plugin.B2S.ShowGrill.help": THE_BACKGLASS_PICTURE,
    "app.vpx.group.plugins.pair.backglass_dmd_position.note": THE_BACKGLASS_PICTURE,
    "app.vpx.group.plugins.pair.scoreview_dmd_position.note": THE_BACKGLASS_PICTURE,
}


def says_art(value: Any) -> bool:
    forms = value.values() if isinstance(value, dict) else [value]
    return any(ART.search(str(form)) for form in forms)


def unexplained(catalog: dict[str, Any], allowed: dict[str, str]) -> list[str]:
    return sorted(key for key, value in catalog.items()
                  if says_art(value) and not allowed.get(key))


ART_WORD = re.compile(r"\bart\b", re.IGNORECASE)
BACKGLASS_PICTURE = re.compile(r"backglass|directb2s|\bDMD\b", re.IGNORECASE)
SUMMARY = re.compile(r'summary="([^"]*)"')


def api_lines() -> list[str]:
    """Every OpenAPI summary in `httpapi/` and every line of the API reference."""
    root = Path(__file__).resolve().parents[2]
    lines = [one for path in sorted((root / "httpapi").glob("*.py"))
             for one in SUMMARY.findall(path.read_text())]
    return lines + (root / "docs" / "http-api.md").read_text().splitlines()


def api_says_art(lines: list[str]) -> list[str]:
    return [one for one in lines if ART_WORD.search(one) and not BACKGLASS_PICTURE.search(one)]


class TheApiAndItsReference(unittest.TestCase):
    def test_the_files_a_game_has_are_media(self) -> None:
        self.assertEqual(
            [one[:80] for one in api_says_art(api_lines())], [],
            "say media for the files a game has, gets or lacks; art is only the picture "
            "inside a backglass file")

    def test_it_reads_both_sources(self) -> None:
        lines = api_lines()
        self.assertTrue(any("Remove the file every table shares" in one for one in lines))
        self.assertTrue(any("/api/v1/tables" in one for one in lines))

    def test_it_can_fail(self) -> None:
        lines = ["Kinds where a table has art of its own",
                 "The art a backglass file draws on the score view",
                 "Media you added", "A smart pick", "Start a party"]

        self.assertEqual(api_says_art(lines), ["Kinds where a table has art of its own"])


class EveryCatalog(unittest.TestCase):
    def test_media_files_are_media_on_screen(self) -> None:
        self.assertEqual(
            unexplained(CATALOG, ALLOWED), [],
            "say media for the files a game has, gets or lacks (Get media, Media you "
            "added); if the word is the picture inside a backglass file, list the key in "
            "ALLOWED with why")

    def test_every_listed_key_still_says_it(self) -> None:
        self.assertEqual(sorted(key for key in ALLOWED
                                if not says_art(CATALOG.get(key, ""))), [])

    def test_the_sweep_reads_every_owner(self) -> None:
        owners = {key.split(".", 2)[0] for key in CATALOG}
        self.assertLessEqual({"app", "ext", "manager", "frontend", "config"}, owners)

    def test_it_can_fail(self) -> None:
        catalog = {
            "label": "Get Art for New Games",
            "plural": {"one": "Get art for 1 game?", "other": "Get artwork for {count} games?"},
            "kept": "Media you added stays",
            "word": "Start a party",
            "partial": "Get smart media",
        }

        self.assertEqual(unexplained(catalog, {}), ["label", "plural"])
        self.assertEqual(unexplained(catalog, {"label": "x"}), ["plural"])


if __name__ == "__main__":
    unittest.main()
