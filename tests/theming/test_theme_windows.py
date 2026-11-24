"""Which windows a theme gets, and what each one is called.

The window name decides four things at once - the HTML file, the `?window=` value, the
WebSocket identity and the monitor key - so getting the default wrong for a contract
would silently stop twelve published themes from loading.
"""

from __future__ import annotations

import json
import unittest
from configparser import ConfigParser
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.config.config_access import DisplayConfig
from common.config.config_store import ConfigStore
from frontend import theme_api, theme_windows
from tests.support.accepted import owner_accepts_theme
from tests.support.library import TempTree


def _theme(root: Path, manifest: dict | None) -> Path:
    theme = root / "a-theme"
    theme.mkdir(parents=True)
    if manifest is not None:
        (theme / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return theme


class DefaultsTests(TempTree):

    def test_contract_1_keeps_the_names_published_themes_use(self) -> None:
        """index_table.html and ?window=table have to go on working untouched."""
        theme = _theme(self.root, {"name": "a-theme"})

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table", "bg", "dmd"))

    def test_contract_2_uses_the_current_vocabulary(self) -> None:
        theme = _theme(self.root, {"name": "a-theme", "contract": 2})

        self.assertEqual(theme_windows.declared_windows(theme, 2),
                         ("playfield", "backglass", "scoreview"))

    def test_a_theme_with_no_manifest_still_gets_windows(self) -> None:
        theme = _theme(self.root, None)

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table", "bg", "dmd"))

    def test_a_declaration_that_is_not_a_list_is_ignored(self) -> None:
        theme = _theme(self.root, {"windows": "playfield"})

        self.assertEqual(theme_windows.declared_windows(theme, 2),
                         ("playfield", "backglass", "scoreview"))


class PagesDecideTheDefaultTests(TempTree):
    """A theme that declares nothing gets a window per page it ships, not three."""


    def _theme_with(self, pages, manifest=None) -> Path:
        theme = _theme(self.root, manifest if manifest is not None else {"name": "a-theme"})
        for page in pages:
            (theme / f"index_{page}.html").write_text("", encoding="utf-8")
        return theme

    def test_a_one_screen_theme_gets_one_window(self) -> None:
        """carousel2 ships only index_table.html; opening bg and dmd served two 404s."""
        theme = self._theme_with(["table"])

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table",))

    def test_a_three_screen_theme_is_unchanged(self) -> None:
        theme = self._theme_with(["table", "bg", "dmd"])

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table", "bg", "dmd"))

    def test_a_theme_with_no_pages_at_all_still_gets_the_default(self) -> None:
        """Nothing to learn from, so behave as before rather than opening no windows."""
        theme = self._theme_with([])

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table", "bg", "dmd"))

    def test_a_declared_window_is_honored_even_with_no_page(self) -> None:
        """An explicit declaration is intent; silently dropping it would hide the bug."""
        theme = self._theme_with(["playfield"], {"windows": ["playfield", "topper"]})

        self.assertEqual(theme_windows.declared_windows(theme, 2), ("playfield", "topper"))

    def test_the_count_in_the_manifest_is_not_what_decides(self) -> None:
        """`supported_screens` never opened a window; the pages are the real answer."""
        theme = self._theme_with(["table"], {"name": "a-theme", "supported_screens": 3})

        self.assertEqual(theme_windows.declared_windows(theme, 1), ("table",))


class IndexPageTests(TempTree):
    """The window name picks the file, so the contract decides which file a theme ships."""


    def _pages(self, contract: int) -> list[str]:
        theme = _theme(self.root, {"name": "a-theme", "contract": contract})
        config = ConfigParser()
        config.read_dict({"Settings": {"theme": "a-theme"}})
        return [theme_api.get_theme_index_page(config, window)
                for window in theme_windows.declared_windows(theme, contract)]

    def test_contract_1_asks_for_index_table(self) -> None:
        self.assertIn("index_table.html", self._pages(1)[0])

    def test_contract_2_asks_for_index_playfield(self) -> None:
        """A contract 2 theme shipping index_table.html gets a 404, so docs say playfield."""
        self.assertIn("index_playfield.html", self._pages(2)[0])

    def test_a_window_vpinfe_never_had_names_its_own_file(self) -> None:
        theme = _theme(self.root, {"windows": ["playfield", "topper"]})
        config = ConfigParser()
        config.read_dict({"Settings": {"theme": "a-theme"}})

        windows = theme_windows.declared_windows(theme, 2)

        self.assertIn("index_topper.html", theme_api.get_theme_index_page(config, windows[1]))


class CoreTopperTests(TempTree):
    """The topper is the cabinet's: a screen set for it opens a window under any theme."""

    def setUp(self) -> None:
        super().setUp()
        self.themes = self.root / "themes"
        self.enterContext(mock.patch("frontend.theme_api.THEMES_DIR", self.themes))
        owner_accepts_theme(self, "a-theme")

    def _config(self, screen: str = "") -> ConfigParser:
        config = ConfigParser()
        config.read_dict({"Settings": {"theme": "a-theme"}})
        if screen:
            config.read_dict({"windows.topper": {"screen_id": screen}})
        return config

    def _install(self, manifest: dict | None, *, page: bool = False) -> None:
        theme = _theme(self.themes, manifest)
        if page:
            (theme / "index_topper.html").write_text("<html></html>", encoding="utf-8")

    def test_a_screen_for_it_adds_the_window_after_the_theme_s_own(self) -> None:
        self._install({"min_vpinfe": "3.0"})

        self.assertEqual(theme_windows.active(self._config("2")),
                         ("playfield", "backglass", "scoreview", "topper"))

    def test_no_screen_for_it_adds_nothing(self) -> None:
        self._install({"min_vpinfe": "3.0"})

        self.assertNotIn("topper", theme_windows.active(self._config()))

    def test_a_theme_that_declares_it_is_not_given_a_second(self) -> None:
        self._install({"min_vpinfe": "3.0", "windows": ["playfield", "topper"]})

        self.assertEqual(theme_windows.active(self._config("2")), ("playfield", "topper"))

    def test_the_controller_stays_the_theme_s_first_window(self) -> None:
        self._install({"min_vpinfe": "3.0", "windows": ["playfield"]})

        self.assertEqual(theme_windows.controller(theme_windows.active(self._config("2"))),
                         "playfield")

    def test_a_theme_without_a_page_for_it_gets_core_s(self) -> None:
        self._install({"min_vpinfe": "3.0"})

        page = theme_api.get_theme_index_page(self._config("2"), "topper")

        self.assertIn("/core/index_topper.html?window=topper", page)

    def test_a_theme_that_ships_the_page_keeps_it(self) -> None:
        self._install({"min_vpinfe": "3.0", "windows": ["playfield", "topper"]}, page=True)

        page = theme_api.get_theme_index_page(self._config("2"), "topper")

        self.assertIn("/themes/a-theme/index_topper.html?window=topper", page)

    def test_core_has_the_page_it_falls_back_to(self) -> None:
        self.assertTrue((Path(theme_api.__file__).parent / "static" /
                         theme_api.CORE_WINDOW_PAGES["topper"]).is_file())

    def test_core_draws_the_backglass_and_score_view_too(self) -> None:
        self._install({"min_vpinfe": "3.0"})

        for window in ("backglass", "scoreview"):
            with self.subTest(window=window):
                page = theme_api.get_theme_index_page(self._config(), window)
                self.assertIn(f"/core/index_{window}.html?window={window}", page)

    def test_a_theme_that_ships_a_display_page_keeps_it(self) -> None:
        theme = _theme(self.themes, {"min_vpinfe": "3.0"})
        (theme / "index_backglass.html").write_text("<html></html>", encoding="utf-8")

        page = theme_api.get_theme_index_page(self._config(), "backglass")

        self.assertIn("/themes/a-theme/index_backglass.html?window=backglass", page)

    def test_a_window_core_has_no_page_for_goes_to_the_theme(self) -> None:
        self._install({"min_vpinfe": "3.0"})

        page = theme_api.get_theme_index_page(self._config(), "marquee")

        self.assertIn("/themes/a-theme/index_marquee.html", page)

    def test_a_screen_for_a_display_the_theme_left_out_adds_core_s(self) -> None:
        self._install({"min_vpinfe": "3.0", "windows": ["playfield"]})
        config = self._config()
        config.read_dict({"windows.backglass": {"screen_id": "1"}})

        self.assertEqual(theme_windows.active(config), ("playfield", "backglass"))

    def test_a_contract_1_theme_is_not_offered_them(self) -> None:
        self._install({"name": "a-theme"})
        config = self._config()
        config.read_dict({"windows.backglass": {"screen_id": "1"}})

        self.assertNotIn("backglass", theme_windows.active(config))


class DeclaredTests(TempTree):

    def test_a_theme_can_ask_for_a_window_vpinfe_never_had(self) -> None:
        theme = _theme(self.root, {"windows": ["playfield", "bg", "dmd", "topper"]})

        windows = theme_windows.declared_windows(theme, 2)

        self.assertIn("topper", windows)
        self.assertEqual(theme_windows.screen_key("topper"), "topperscreenid")

    def test_the_first_window_is_the_controller(self) -> None:
        theme = _theme(self.root, {"windows": ["playfield", "bg"]})

        self.assertEqual(theme_windows.controller(
            theme_windows.declared_windows(theme, 2)), "playfield")

    def test_the_controller_is_launched_last_so_it_takes_focus(self) -> None:
        order = theme_windows.launch_order(("playfield", "backglass", "scoreview"))

        self.assertEqual(order[-1], "playfield")


class ScreenKeyTests(unittest.TestCase):
    def test_the_playfield_key_is_the_same_under_either_spelling(self) -> None:
        """Contract 1 calls the window `table`; the ini key never moved."""
        self.assertEqual(theme_windows.screen_key("table"), "playfieldscreenid")
        self.assertEqual(theme_windows.screen_key("playfield"), "playfieldscreenid")

    def test_an_unheard_of_window_reads_its_own_key(self) -> None:
        config = ConfigParser()
        config.read_dict({"Displays": {"playfieldscreenid": "0", "topperscreenid": "2"}})

        displays = DisplayConfig.from_config(config)

        self.assertEqual(displays.window_screen_id("topperscreenid"), "2")
        self.assertEqual(displays.window_screen_id("nosuchscreenid"), "",
                         "a window with no monitor set is simply not launched")

    def test_a_2x_file_s_monitors_reach_each_window_under_a_key_screen_key_makes(
            self) -> None:
        ini = Path(self.enterContext(TemporaryDirectory())) / "vpinfe.ini"
        ini.write_text("[Displays]\ntablescreenid = 2\nbgscreenid = 0\ndmdscreenid = 1\n",
                       encoding="utf-8")
        displays = DisplayConfig.from_config(ConfigStore(str(ini)))
        monitors = {"playfield": "2", "backglass": "0", "scoreview": "1"}
        names = [name for names in theme_windows.DEFAULT_WINDOWS.values() for name in names]

        for name in [*names, *(f" {name} " for name in names)]:
            with self.subTest(window=name):
                key = theme_windows.screen_key(name)
                self.assertNotIn(key, {"tablescreenid", "bgscreenid", "dmdscreenid"})
                self.assertEqual(displays.window_screen_id(key),
                                 monitors[theme_windows.canonical(name)])


class ForeignWindowNameTests(TempTree):
    """A theme that declares one contract and names windows from another.

    It still gets the windows it asked for - the name decides the page, the monitor and
    the identity, and canonical() keeps the monitor right. What breaks is anything keyed
    on the media vocabulary, silently, which is why this warns.
    """

    def _theme(self, contract, windows):
        theme = self.root / "Example"
        theme.mkdir(parents=True, exist_ok=True)
        (theme / "manifest.json").write_text(
            json.dumps({"name": "Example", "contract": contract, "windows": windows}),
            encoding="utf-8")
        theme_windows._warned_foreign.clear()
        return theme

    def test_a_contract_2_theme_naming_contract_1_windows_is_warned(self) -> None:
        theme = self._theme(2, ["playfield", "bg", "dmd"])
        with self.assertLogs("vpinfe.frontend.theme_windows", "WARNING") as caught:
            windows = theme_windows.declared_windows(theme, 2)
        self.assertEqual(windows, ("playfield", "bg", "dmd"), "it still gets what it asked for")
        message = caught.output[0]
        self.assertIn("bg, dmd", message)
        self.assertIn("backglass, scoreview", message)

    def test_correct_contract_2_names_say_nothing(self) -> None:
        theme = self._theme(2, ["playfield", "backglass", "scoreview"])
        with self.assertNoLogs("vpinfe.frontend.theme_windows", "WARNING"):
            theme_windows.declared_windows(theme, 2)

    def test_a_contract_1_theme_keeps_its_own_spellings_quietly(self) -> None:
        """Those names are correct there - PAR-32 is explicit that they keep working."""
        theme = self._theme(1, ["table", "bg", "dmd"])
        with self.assertNoLogs("vpinfe.frontend.theme_windows", "WARNING"):
            theme_windows.declared_windows(theme, 1)

    def test_an_invented_window_is_not_a_wrong_one(self) -> None:
        theme = self._theme(2, ["playfield", "topper", "marquee"])
        with self.assertNoLogs("vpinfe.frontend.theme_windows", "WARNING"):
            theme_windows.declared_windows(theme, 2)

    def test_it_is_said_once_per_theme(self) -> None:
        """Windows open repeatedly; the log should not repeat with them."""
        theme = self._theme(2, ["playfield", "bg"])
        with self.assertLogs("vpinfe.frontend.theme_windows", "WARNING") as caught:
            for _ in range(4):
                theme_windows.declared_windows(theme, 2)
        self.assertEqual(len(caught.output), 1)



if __name__ == "__main__":
    unittest.main()
