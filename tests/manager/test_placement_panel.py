"""The window placement rows: what they offer per session, and what each answer writes."""

from __future__ import annotations

import configparser
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from common.host import placement
from common.i18n import t
from common.settings import config_service
from manager.kit import panel
from manager.settings import placement_panel

SCHEMA = config_service.schema()["sections"]
WAYLAND = {"WAYLAND_DISPLAY": "wayland-1"}
SWAY = {**WAYLAND, "SWAYSOCK": "/run/user/1000/sway.sock"}


def _labels(entries: list) -> list:
    return [label for label, _ in entries if isinstance(label, str)]


class ScreenRowTests(unittest.TestCase):
    def _config(self, screen_id: str) -> configparser.ConfigParser:
        config = configparser.ConfigParser()
        config.read_dict({"windows.playfield": {"screen_id": screen_id}})
        return config

    def _rows(self, config: configparser.ConfigParser, monitors: list) -> tuple:
        self.enterContext(patch.object(placement_panel, "get_ini_config", return_value=config))
        self.enterContext(patch.object(placement_panel.theme_windows, "active",
                                       return_value=("playfield",)))
        self.enterContext(patch.object(placement_panel.display_service, "get_display_monitors",
                                       return_value=monitors))
        return placement_panel._rows([SimpleNamespace(name="DP-1")])

    def test_a_set_screen_with_no_monitors_known_is_missing(self) -> None:
        rows, missed = self._rows(self._config("0"), [])

        self.assertEqual([one.output for one in rows], [""])
        self.assertTrue(missed)

    def test_no_screen_set_is_no_screen_and_not_a_missing_one(self) -> None:
        rows, missed = self._rows(self._config(""), [])

        self.assertEqual([one.output for one in rows], [""])
        self.assertFalse(missed)

    def test_a_set_screen_the_desktop_reports_is_placed(self) -> None:
        rows, missed = self._rows(self._config("0"), [SimpleNamespace(name="DP-1")])

        self.assertEqual([one.output for one in rows], ["DP-1"])
        self.assertFalse(missed)


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.enterContext(patch.object(placement, "CONFIG_DIR", Path(self.dir.name)))
        self.enterContext(patch.object(placement.tools, "here", return_value="linux"))
        self.enterContext(patch("common.host.vpinos.detected", return_value=False))
        self.rows = self.enterContext(patch.object(placement_panel, "_rows", return_value=(
            [placement.Row("playfield", "playfield", "DP-1")], False)))
        self.adapter = self.enterContext(patch.object(placement_panel, "WlrAdapter"))
        self.adapter.return_value.outputs.return_value = [SimpleNamespace(name="DP-1")]

    def test_nothing_is_said_where_the_session_has_no_offer(self) -> None:
        self.assertIsNone(placement_panel.read({}))

    def test_vpinos_has_no_offer_because_its_config_carries_the_line(self) -> None:
        with patch("common.host.vpinos.detected", return_value=True):
            self.assertIsNone(placement_panel.read(SWAY))

    def test_sway_gets_the_rules_for_the_screens_as_they_stand(self) -> None:
        reading = placement_panel.read(SWAY)

        assert reading is not None
        self.assertEqual(reading.offer.kind, placement.SWAY)
        self.assertIn("output DP-1", reading.text)
        self.assertEqual(reading.problem, "")

    def test_screens_that_cannot_be_read_still_give_rules_and_say_why(self) -> None:
        self.adapter.return_value.outputs.side_effect = OSError("no socket")

        reading = placement_panel.read(SWAY)

        assert reading is not None
        self.assertIn("no socket", reading.problem)
        self.assertIn("border none", reading.text)

    def test_a_screen_the_desktop_does_not_report_is_flagged_not_written_over(self) -> None:
        self.rows.return_value = ([placement.Row("playfield", "playfield", "")], True)

        reading = placement_panel.read(SWAY)

        assert reading is not None
        self.assertEqual(reading.problem, t("manager.placement.screen_missing"))

    def test_gnome_has_no_rules_to_write(self) -> None:
        reading = placement_panel.read({**WAYLAND, "XDG_CURRENT_DESKTOP": "GNOME"})

        assert reading is not None
        self.assertEqual(reading.offer.kind, placement.GNOME)
        self.assertEqual(reading.text, "")
        self.adapter.assert_not_called()


class ResyncTests(unittest.TestCase):
    def _offer(self, added: bool) -> placement_panel.Reading:
        found = placement.Offer(placement.SWAY, Path("/r.conf"), "include /r.conf",
                                Path("/c"), added)
        return placement_panel.Reading(found, "rules\n")

    def test_a_line_not_added_is_left_alone(self) -> None:
        with patch.object(placement_panel, "read", return_value=self._offer(False)), \
                patch.object(placement_panel.placement, "write_rules") as write, \
                patch.object(placement_panel, "reload_config") as reload:
            self.assertFalse(placement_panel.resync(SWAY))

        write.assert_not_called()
        reload.assert_not_called()

    def test_a_line_added_has_its_rules_rewritten_and_the_compositor_told(self) -> None:
        with patch.object(placement_panel, "read", return_value=self._offer(True)), \
                patch.object(placement_panel, "_held", return_value="old\n"), \
                patch.object(placement_panel.placement, "write_rules") as write, \
                patch.object(placement_panel, "reload_config") as reload:
            self.assertTrue(placement_panel.resync(SWAY))

        write.assert_called_once_with(placement.SWAY, "rules\n")
        reload.assert_called_once_with(SWAY)

    def test_screens_that_could_not_be_read_write_nothing_and_say_so(self) -> None:
        reading = placement_panel.Reading(self._offer(True).offer, "rules\n", "no socket")
        with patch.object(placement_panel, "read", return_value=reading), \
                patch.object(placement_panel.placement, "write_rules") as write, \
                patch.object(placement_panel, "reload_config") as reload:
            with self.assertRaises(OSError):
                placement_panel.resync(SWAY)

        write.assert_not_called()
        reload.assert_not_called()

    def test_rules_that_have_not_changed_do_not_reload_the_desktop(self) -> None:
        with patch.object(placement_panel, "read", return_value=self._offer(True)), \
                patch.object(placement_panel, "_held", return_value="rules\n"), \
                patch.object(placement_panel.placement, "write_rules") as write, \
                patch.object(placement_panel, "reload_config") as reload:
            self.assertFalse(placement_panel.resync(SWAY))

        write.assert_not_called()
        reload.assert_not_called()

    def test_no_offer_is_nothing_to_do(self) -> None:
        with patch.object(placement_panel, "read", return_value=None):
            self.assertFalse(placement_panel.resync({}))


class RowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write = AsyncMock(return_value=True)
        self.refill = Mock()
        self.action = self.enterContext(patch.object(panel, "action"))
        self.notify = self.enterContext(patch.object(placement_panel.ui, "notify"))
        self.add = self.enterContext(patch.object(placement_panel.placement, "add"))
        self.reload = self.enterContext(patch.object(placement_panel, "reload_config"))
        self.show = self.enterContext(patch.object(placement_panel, "_show_line"))
        self.enterContext(patch.object(placement_panel.ui, "row"))
        self.enterContext(patch.object(placement_panel.ui, "context"))
        self.enterContext(patch.object(placement_panel.placement, "write_rules"))

    def _rows(self, reading: Any, values: dict | None = None) -> list:
        return placement_panel.rows(reading, values or {}, SCHEMA, self.write, self.refill,
                                    SWAY)

    def _sway(self, config: Path | None = Path("/cfg/sway/config"),
              added: bool = False, problem: str = "") -> placement_panel.Reading:
        found = placement.Offer(placement.SWAY, Path("/v/window-rules-sway.conf"),
                                "include /v/window-rules-sway.conf", config, added)
        return placement_panel.Reading(found, "rules\n", problem)

    def _clicked(self, label: str) -> Any:
        return next(call.args[1] for call in self.action.call_args_list
                    if call.args[0] == label)

    def test_no_offer_draws_nothing(self) -> None:
        self.assertEqual(self._rows(None), [])

    def test_sway_with_a_config_offers_both_ways(self) -> None:
        self._rows(self._sway())[-1][1]()

        self.assertEqual([call.args[0] for call in self.action.call_args_list],
                         [t("manager.placement.add"), t("manager.placement.add_myself")])

    def test_the_rules_files_path_is_shown(self) -> None:
        entries = dict(self._rows(self._sway())[1:])

        self.assertEqual(entries[t("manager.placement.rules_file")],
                         str(Path("/v/window-rules-sway.conf")))

    def test_without_a_config_only_the_copy_is_offered_and_it_says_why(self) -> None:
        entries = self._rows(self._sway(config=None))
        entries[-1][1]()

        self.assertEqual([call.args[0] for call in self.action.call_args_list],
                         [t("manager.placement.add_myself")])
        self.assertIn(t("manager.placement.no_config"),
                      [value for _, value in entries if isinstance(value, str)])

    def test_once_added_there_is_nothing_left_to_press(self) -> None:
        entries = self._rows(self._sway(added=True))

        self.assertNotIn("", _labels(entries))
        self.assertEqual(entries[-1][0], t("manager.placement.config_file"))
        self.action.assert_not_called()

    def test_screens_that_could_not_be_read_are_flagged(self) -> None:
        with patch.object(panel, "state") as state:
            self._rows(self._sway(problem="no socket"))

        self.assertEqual(state.call_args.args[:2], (t("manager.placement.read_failed"), "warn"))

    async def test_add_it_writes_reloads_and_draws_again(self) -> None:
        with patch.object(placement_panel.run, "io_bound", new=AsyncMock(
                side_effect=lambda fn, *a: fn(*a))):
            entries = self._rows(self._sway())
            entries[-1][1]()
            await self._clicked(t("manager.placement.add"))()

        self.add.assert_called_once()
        self.reload.assert_called_once_with(SWAY)
        self.refill.assert_called_once_with()
        self.assertEqual(self.notify.call_args.kwargs["type"], "positive")

    async def test_a_line_that_could_not_be_written_is_said_and_nothing_reloads(self) -> None:
        self.add.side_effect = OSError("read-only")
        with patch.object(placement_panel.run, "io_bound", new=AsyncMock(
                side_effect=lambda fn, *a: fn(*a))):
            self._rows(self._sway())[-1][1]()
            await self._clicked(t("manager.placement.add"))()

        self.reload.assert_not_called()
        self.assertEqual(self.notify.call_args.kwargs["type"], "negative")

    async def test_a_reload_that_failed_says_the_line_is_in_all_the_same(self) -> None:
        self.reload.side_effect = OSError("refused")
        with patch.object(placement_panel.run, "io_bound", new=AsyncMock(
                side_effect=lambda fn, *a: fn(*a))):
            self._rows(self._sway())[-1][1]()
            await self._clicked(t("manager.placement.add"))()

        self.add.assert_called_once()
        self.assertEqual(self.notify.call_args.kwargs["type"], "warning")
        self.refill.assert_called_once_with()

    async def test_the_copy_writes_the_rules_then_shows_the_line(self) -> None:
        with patch.object(placement_panel.run, "io_bound", new=AsyncMock(
                side_effect=lambda fn, *a: fn(*a))):
            self._rows(self._sway())[-1][1]()
            await self._clicked(t("manager.placement.add_myself"))()

        placement_panel.placement.write_rules.assert_called_once_with(  # type: ignore[attr-defined]
            placement.SWAY, "rules\n")
        self.show.assert_called_once()
        self.add.assert_not_called()


class XwaylandTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write = AsyncMock(return_value=True)
        self.refill = Mock()
        self.action = self.enterContext(patch.object(panel, "action"))

    def _rows(self, kind: str = placement.GNOME, held: str = "") -> list:
        reading = placement_panel.Reading(placement.Offer(kind))
        values = {"chromium": {"options": held}}
        return placement_panel.rows(reading, values, SCHEMA, self.write, self.refill, SWAY)

    def test_it_says_it_cannot_place_windows_and_names_the_desktop(self) -> None:
        for kind, name in ((placement.GNOME, "GNOME"), (placement.KDE, "KDE Plasma")):
            self.action.reset_mock()
            with self.subTest(kind=kind), patch.object(panel, "intro") as intro:
                self._rows(kind)

                self.assertIn(name, intro.call_args.args[0])

    def test_it_offers_xwayland_and_no_rules_or_config(self) -> None:
        entries = self._rows()

        self.assertEqual(_labels(entries), [t("manager.placement.xwayland")])
        self.assertEqual(self.action.call_args.args[0], t("manager.placement.use_xwayland"))

    async def test_use_xwayland_appends_the_flag_to_what_chromium_options_hold(self) -> None:
        self._rows(held="--disable-gpu")

        await self.action.call_args.args[1]()

        self.write.assert_awaited_once_with(
            {"chromium": {"options": "--disable-gpu\n--ozone-platform=x11"}})
        self.refill.assert_called_once_with()

    async def test_use_xwayland_on_empty_options_is_the_flag_alone(self) -> None:
        self._rows()

        await self.action.call_args.args[1]()

        self.write.assert_awaited_once_with({"chromium": {"options": "--ozone-platform=x11"}})

    async def test_a_write_that_was_not_kept_draws_nothing_again(self) -> None:
        self.write.return_value = False
        self._rows()

        await self.action.call_args.args[1]()

        self.refill.assert_not_called()

    def test_with_the_flag_already_there_it_shows_on_and_offers_nothing(self) -> None:
        with patch.object(panel, "state") as state:
            self._rows(held="--ozone-platform=x11")

        self.action.assert_not_called()
        self.assertEqual(state.call_args.args[:2], (t("manager.placement.xwayland_on"), "on"))


class RowsFromThemeTests(unittest.TestCase):
    def test_a_window_without_a_page_or_a_screen_role_is_left_out(self) -> None:
        outputs = [SimpleNamespace(name="DP-1")]
        screen = SimpleNamespace(output=outputs[0], reason="")
        with patch.object(placement_panel, "get_ini_config"), \
                patch.object(placement_panel.display_service, "get_display_monitors",
                             return_value=[]), \
                patch.object(placement_panel.theme_windows, "active",
                             return_value=("playfield", "table", "bg")), \
                patch.object(placement_panel.theme_windows, "canonical",
                             side_effect=lambda name: {"bg": "backglass"}.get(name, name)), \
                patch.object(placement_panel.theme_windows, "window_title",
                             side_effect=lambda name: None if name == "table" else name), \
                patch.object(placement_panel, "screen_of", return_value=screen):
            found, _missed = placement_panel._rows(outputs)

        self.assertEqual([(one.window, one.role) for one in found],
                         [("playfield", "playfield"), ("bg", "backglass")])
        self.assertEqual({one.output for one in found}, {"DP-1"})

    def test_the_topper_is_placed_on_the_screen_set_for_it(self) -> None:
        config = configparser.ConfigParser()
        config.read_dict({"windows.topper": {"screen_id": "0"}})
        with patch.object(placement_panel, "get_ini_config", return_value=config), \
                patch.object(placement_panel.display_service, "get_display_monitors",
                             return_value=[SimpleNamespace(name="DP-3")]), \
                patch.object(placement_panel.theme_windows, "active",
                             return_value=("playfield", "topper")):
            found, missed = placement_panel._rows([SimpleNamespace(name="DP-3")])

        self.assertEqual([(one.window, one.role, one.output) for one in found],
                         [("playfield", "playfield", ""), ("topper", "topper", "DP-3")])
        self.assertFalse(missed)


if __name__ == "__main__":
    unittest.main()
