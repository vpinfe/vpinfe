"""The frontend's reaction to a launch, now that it is a subscriber.

The window messages are load-bearing: TableLaunching suppresses frontend input and
TableLaunchComplete restores it, so a launch that announces the first without ever
reaching the second leaves the wheel dead for the life of the process.
"""

from __future__ import annotations

import configparser
import re
import types
import unittest
from pathlib import Path
from unittest import mock

from common import events
from common.host import launch_state
from frontend import play_events
from frontend.device_channel import DeviceChannel


class _Bridge(DeviceChannel):
    def __init__(self):
        self.messages = []

    def send_event_all_with_iframe(self, message):
        self.messages.append(message["type"])


class PlayEventTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        play_events.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(play_events.reset_for_tests)
        self.bridge = _Bridge()

    def _register(self, ini_config=None):
        with mock.patch.object(play_events, "save_last_launched"):
            play_events.register(self.bridge, None, ini_config)

    def _settle(self):
        """Wait out the coalescing window that answers a run of game changes."""
        timer = play_events._change_timer
        if timer is not None:
            timer.join(timeout=5)

    def test_a_launch_leaves_every_window_where_it_is(self) -> None:
        """The hand-over gives the keyboard to the table, so no window is minimized."""
        for system in ("win32", "linux", "darwin"):
            with self.subTest(system=system):
                events.clear()
                play_events.reset_for_tests()
                browser = mock.Mock()
                with mock.patch.object(play_events, "save_last_launched"), \
                        mock.patch.object(play_events.sys, "platform", system):
                    play_events.register(self.bridge, browser, None)
                    events.emit(events.TABLE_LAUNCHING, game=None, ini_config=None)

                self.assertEqual(browser.mock_calls, [])

    def test_windows_gives_the_controller_the_keyboard_when_the_table_has_gone(self) -> None:
        """table.exited is announced on every path out, so a cabinet with no keyboard is
        never left looking at some other program."""
        browser = mock.Mock()
        with mock.patch.object(play_events, "save_last_launched"), \
                mock.patch.object(play_events.sys, "platform", "win32"):
            play_events.register(self.bridge, browser, None)
            events.emit(events.TABLE_EXITED)

        browser.give_controller_keyboard.assert_called_once_with()

    def test_only_windows_hands_the_keyboard_back_itself(self) -> None:
        browser = mock.Mock()
        with mock.patch.object(play_events, "save_last_launched"), \
                mock.patch.object(play_events.sys, "platform", "linux"):
            play_events.register(self.bridge, browser, None)
            events.emit(events.TABLE_EXITED)

        browser.give_controller_keyboard.assert_not_called()

    def test_a_browser_that_cannot_hand_the_keyboard_back_does_not_stop_the_exit(self) -> None:
        browser = mock.Mock()
        browser.give_controller_keyboard.side_effect = OSError("no window manager")
        with mock.patch.object(play_events, "save_last_launched"), \
                mock.patch.object(play_events.sys, "platform", "win32"):
            play_events.register(self.bridge, browser, None)
            with self.assertLogs("vpinfe.frontend.play_events", level="ERROR") as logged:
                events.emit(events.TABLE_EXITED)

        browser.give_controller_keyboard.assert_called_once_with()
        self.assertIn("Could not give the frontend the keyboard back", logged.output[0])

    def test_registering_twice_leaves_one_of_each(self) -> None:
        """Three windows share one bridge; registering per window would treble
        every message."""
        self._register()
        self._register()

        for name in (events.TABLE_LAUNCHING, events.TABLE_LAUNCHED, events.TABLE_EXITED,
                     events.TABLE_PLAY_RECORDED, events.GAME_CHANGED,
                     events.COLLECTIONS_CHANGED):
            with self.subTest(event=name):
                self.assertEqual(events.registered(name), (0, 1))

    def test_the_windows_are_driven_by_the_lifecycle(self) -> None:
        self._register()

        with mock.patch.object(play_events, "save_last_launched"):
            events.emit(events.TABLE_LAUNCHING, game=None, ini_config=None)
            events.emit(events.TABLE_LAUNCHED, game=None, ini_config=None)
            events.emit(events.TABLE_EXITED, game=None, ini_config=None)

        # Once each. PAR-24 was withdrawn, so there is no second spelling to send.
        self.assertEqual(self.bridge.messages,
                         ["TableLaunching", "TableRunning", "TableLaunchComplete"])

    def test_a_launch_from_anywhere_drives_the_windows(self) -> None:
        """The Remote page used to produce no window messages at all."""
        self._register()

        with mock.patch.object(play_events, "save_last_launched"):
            events.emit(events.TABLE_LAUNCHING, game=None, ini_config=None)

        self.assertEqual(self.bridge.messages, ["TableLaunching"])

    def test_the_table_that_launched_is_recorded_not_just_its_game(self) -> None:
        """A game offers several tables, so the game alone comes back to the wrong row
        on an expanded wheel. The launch says which one it started."""
        game = types.SimpleNamespace(game_dir_name="Example")
        ini = types.SimpleNamespace(config={})
        self._register(ini)

        with mock.patch.object(play_events, "save_last_launched") as save:
            events.emit(events.TABLE_LAUNCHING, game=game, ini_config=None,
                        table_id="Tbl1111111")

        save.assert_called_once_with(ini, game, "Tbl1111111")

    def test_a_launch_that_names_no_table_still_records_its_game(self) -> None:
        """An older publisher, or a folder whose tables have no ids yet."""
        game = types.SimpleNamespace(game_dir_name="Example")
        ini = types.SimpleNamespace(config={})
        self._register(ini)

        with mock.patch.object(play_events, "save_last_launched") as save:
            events.emit(events.TABLE_LAUNCHING, game=game, ini_config=None)

        save.assert_called_once_with(ini, game, "")

    def test_a_recording_leaves_the_wheel_where_the_player_left_it(self) -> None:
        """A run of recordings would otherwise leave it on the last game recorded. The
        windows still hear the launch: the table is up and the wheel must not move."""
        game = types.SimpleNamespace(game_dir_name="Example")
        self._register(types.SimpleNamespace(config={}))

        with mock.patch.object(play_events, "save_last_launched") as save:
            events.emit(events.TABLE_LAUNCHING, game=game, ini_config=None,
                        table_id="Tbl1111111", source=launch_state.SOURCE_CAPTURE)

        save.assert_not_called()
        self.assertEqual(self.bridge.messages, ["TableLaunching"])

    def test_a_finished_session_sends_the_windows_back_for_the_payload(self) -> None:
        """Without this the play counts a theme shows are whatever they were at boot."""
        self._register()

        events.emit(events.TABLE_PLAY_RECORDED, game=None, ini_config=None)

        self.assertEqual(self.bridge.messages, ["TableDataChange"])

    def test_the_exit_is_not_what_refreshes_the_payload(self) -> None:
        """The runtime and the score are written after the exit goes out, so a refresh
        there would show the session that just ended as one short."""
        self._register()

        events.emit(events.TABLE_EXITED, game=None, ini_config=None)

        self.assertNotIn("TableDataChange", self.bridge.messages)

    def test_a_game_changed_outside_the_frontend_reaches_the_windows(self) -> None:
        """A Manager edit is the case: same process, but nothing told the wheel."""
        self._register()

        events.emit(events.GAME_CHANGED, game=None, path="/games/Example")
        self._settle()

        self.assertEqual(self.bridge.messages, ["TableDataChange"])

    def test_a_run_of_changes_is_answered_once(self) -> None:
        """An import re-reads one game at a time, and each refresh costs every window a
        rebuild of the whole list."""
        self._register()

        for index in range(20):
            events.emit(events.GAME_CHANGED, game=None, path=f"/games/{index}")
        self._settle()

        self.assertEqual(self.bridge.messages, ["TableDataChange"])

    def test_a_collections_write_reaches_the_windows_too(self) -> None:
        """Membership is not derivable from any game, so a wheel showing a collection
        has nothing else to go on."""
        self._register()

        events.emit(events.COLLECTIONS_CHANGED, path="/config/collections.json")
        self._settle()

        self.assertEqual(self.bridge.messages, ["TableDataChange"])

    def test_a_change_is_not_broadcast_before_the_run_goes_quiet(self) -> None:
        self._register()

        events.emit(events.GAME_CHANGED, game=None, path="/games/Example")

        self.assertEqual(self.bridge.messages, [])

    def test_a_broken_window_message_cannot_stop_a_launch(self) -> None:
        """These are subscribers, so the bus contains them. As hooks they could
        abandon a launch mid-ball."""
        self._register()
        self.bridge.send_event_all_with_iframe = mock.Mock(side_effect=RuntimeError("bridge down"))  # type: ignore[method-assign]

        with self.assertLogs("vpinfe.common.events", level="ERROR"):
            events.emit(events.TABLE_LAUNCHED, game=None, ini_config=None)


class PlayersForThemesTests(unittest.TestCase):
    def test_a_theme_gets_an_address_for_a_picture_and_never_the_version(self) -> None:
        state = {"players": [
            {"id": "Kid0000001", "name": "Kid", "picture_version": "abc123"},
            {"id": "Guest00001", "name": "", "picture_version": None}]}

        seen = play_events.players_for_themes(state)

        self.assertEqual(seen["players"], [
            {"id": "Kid0000001", "name": "Kid",
             "picture": "/player_pictures/Kid0000001.webp?v=abc123"},
            {"id": "Guest00001", "name": "", "picture": None}])

    def test_an_id_that_cannot_name_a_file_has_no_address(self) -> None:
        seen = play_events.players_for_themes(
            {"players": [{"id": "../x", "picture_version": "abc123"}]})

        self.assertIsNone(seen["players"][0]["picture"])


class LifecycleMessageSpellingTests(unittest.TestCase):
    """Both spellings of a renamed message have to reach the windows.

    The dual send landed in vpinfe-core.js only, which covers messages a theme
    originates. The launch lifecycle comes from here instead, so it kept sending the 2.x
    names alone: every installed theme carried on working and a theme written against
    3.0's documented names got no launch events at all. PAR-24.
    """

    REPO = Path(__file__).resolve().parent.parent.parent
    JS = REPO / "frontend" / "static" / "common" / "vpinfe-core.js"

    def test_python_and_javascript_agree_on_the_aliases(self) -> None:
        """One contract, written down twice - so assert the two copies match."""
        block = self.JS.read_text(encoding="utf-8").split("MESSAGE_TYPE_ALIASES = {")[1]
        js_map = dict(re.findall(r'(\w+): "(\w+)"', block[:block.index("}")]))

        self.assertEqual(js_map, play_events._LEGACY_MESSAGE_TYPES)

    def test_a_launch_sends_each_message_exactly_once(self) -> None:
        """The risk that replaced the dual send: dropping a send along with its copy."""
        bridge = _Bridge()
        events.clear()
        play_events.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(play_events.reset_for_tests)
        with mock.patch.object(play_events, "save_last_launched"):
            play_events.register(bridge, None, None)

        events.emit(events.TABLE_LAUNCHING, game=None)
        events.emit(events.TABLE_LAUNCHED)
        events.emit(events.TABLE_EXITED)

        for name in ("TableLaunching", "TableRunning", "TableLaunchComplete"):
            self.assertEqual(bridge.messages.count(name), 1, name)


class _FullBridge(DeviceChannel):
    def __init__(self):
        self.messages = []

    def send_event_all_with_iframe(self, message):
        self.messages.append(message)


class SettingsChangedTests(unittest.TestCase):
    """The settings a window acts on while it is open reach it when they are saved."""

    def setUp(self) -> None:
        events.clear()
        play_events.reset_for_tests()
        self.addCleanup(events.clear)
        self.addCleanup(play_events.reset_for_tests)
        self.bridge = _FullBridge()
        self.store = configparser.ConfigParser()
        self.store["behavior"] = {"mute_audio": "true"}
        play_events.register(self.bridge, None, self.store)  # type: ignore[arg-type]

    def _saved(self, *keys: str) -> list[dict]:
        events.emit(events.SETTINGS_CHANGED, keys=list(keys), ini_config=self.store)
        return self.bridge.messages

    def test_the_mute_switch_carries_the_value_it_was_saved_with(self) -> None:
        self.assertEqual(self._saved("behavior.mute_audio"),
                         [{"type": "AudioMuteChanged", "muted": True}])

    def test_any_attract_setting_asks_the_windows_to_read_them_again(self) -> None:
        for key in ("attract_enabled", "attract_idle", "attract_step_time", "attract_order",
                    "attract_on_return", "attract_audio"):
            self.bridge.messages.clear()
            self.assertEqual(self._saved(f"behavior.{key}"), [{"type": "AttractSettingsChanged"}],
                             key)

    def test_a_save_of_several_settings_is_answered_once_for_each_kind(self) -> None:
        self.assertEqual(
            [m["type"] for m in self._saved("behavior.attract_idle", "behavior.attract_enabled",
                                            "behavior.mute_audio")],
            ["AudioMuteChanged", "AttractSettingsChanged"])

    def test_a_setting_no_window_acts_on_sends_nothing(self) -> None:
        self.assertEqual(self._saved("behavior.confirm", "network.http_port"), [])


if __name__ == "__main__":
    unittest.main()
