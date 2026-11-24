"""Whether core's attract runs under the current theme, and what the Manager says if not."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from common.online import theme_service
from manager.settings import attract


def _theme(root: Path, name: str, *, min_vpinfe: str | None, config: dict | None = None) -> None:
    folder = root / name
    folder.mkdir()
    manifest = {} if min_vpinfe is None else {"min_vpinfe": min_vpinfe}
    (folder / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    if config is not None:
        (folder / "config.json").write_text(json.dumps(config), encoding="utf-8")


class WhyAttractIsLeftToTheTheme(unittest.TestCase):
    def setUp(self) -> None:
        self._dir = tempfile.TemporaryDirectory()
        self.addCleanup(self._dir.cleanup)
        self.root = Path(self._dir.name)
        patcher = patch.object(theme_service, "THEMES_DIR", self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _why(self, name: str) -> str:
        return theme_service.attract_left_to_the_theme(name)

    def test_a_theme_that_says_nothing_is_contract_1_and_runs_its_own(self) -> None:
        _theme(self.root, "old", min_vpinfe=None)
        self.assertEqual(self._why("old"), theme_service.ATTRACT_TWO_X)

    def test_a_contract_2_theme_gets_core_attract(self) -> None:
        _theme(self.root, "new", min_vpinfe="3.0.0")
        self.assertEqual(self._why("new"), "")

    def test_a_contract_2_theme_can_give_it_up(self) -> None:
        _theme(self.root, "own", min_vpinfe="3.0.0", config={"attract": {"enabled": False}})
        self.assertEqual(self._why("own"), theme_service.ATTRACT_OWN)

    def test_a_contract_2_theme_that_enables_it_keeps_it(self) -> None:
        _theme(self.root, "on", min_vpinfe="3.0.0", config={"attract": {"enabled": True}})
        self.assertEqual(self._why("on"), "")

    def test_a_contract_1_theme_that_enables_it_gets_it(self) -> None:
        _theme(self.root, "stated", min_vpinfe=None, config={"attract": {"enabled": True}})
        self.assertEqual(self._why("stated"), "")

    def test_a_theme_not_on_disk_leaves_nothing_to_say(self) -> None:
        self.assertEqual(self._why("missing"), "")


class TheLineTheManagerDraws(unittest.TestCase):
    def _line(self, why: str):
        async def write(_changes: dict) -> bool:
            return True

        return attract.Attract(write, {}, [], writable=True, theme="Some Theme",
                               left_to_theme=why).theme_line()

    def test_a_theme_core_attract_runs_under_draws_no_line(self) -> None:
        self.assertIsNone(self._line(""))

    def test_each_reason_draws_a_line(self) -> None:
        for why in (theme_service.ATTRACT_TWO_X, theme_service.ATTRACT_OWN):
            with self.subTest(why=why):
                self.assertIsNotNone(self._line(why))

    def test_the_listing_names_the_active_theme_and_its_reason(self) -> None:
        listing = {"themes": [
            {"name": "A", "active": False, "attract_left_to_theme": "own"},
            {"name": "B", "active": True, "attract_left_to_theme": "contract_1"}]}
        self.assertEqual(attract.left_to_theme(listing), ("B", "contract_1"))

    def test_no_active_theme_is_blank(self) -> None:
        self.assertEqual(attract.left_to_theme({"themes": [{"name": "A", "active": False}]}),
                         ("", ""))


if __name__ == "__main__":
    unittest.main()
