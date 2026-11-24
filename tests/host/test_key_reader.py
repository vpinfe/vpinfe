"""Hearing a key during play: whether this device can, which keys it listens for, and
what a key going down presses. Nothing here listens to this machine's keyboard."""

from __future__ import annotations

import os
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from common import events
from common.host import key_reader, keys, launch_state
from tests.support.catalogs import served
from tests.support.skips import needs_selectable_files

DEVICES = """I: Bus=0003 Vendor=1209 Product=eaea Version=0111
N: Name="Pinscape Controller"
H: Handlers=sysrq kbd leds event3
B: EV=120013

I: Bus=0003 Vendor=1209 Product=eaeb Version=0111
N: Name="Pinscape Controller Joystick"
H: Handlers=event4 js0
B: EV=1b

I: Bus=0011 Vendor=0001 Product=0001 Version=ab41
N: Name="AT Translated Set 2 keyboard"
H: Handlers=sysrq kbd event0 leds
B: EV=120013
"""


class HearingTests(unittest.TestCase):
    WAYLAND = {"WAYLAND_DISPLAY": "wayland-1"}

    def test_windows_and_x11_hear_through_pynput(self) -> None:
        self.assertEqual(key_reader.hearing({}, "windows").via, "pynput")
        self.assertEqual(key_reader.hearing({"DISPLAY": ":0"}, "linux").via, "pynput")

    def test_macos_hears_only_with_input_monitoring(self) -> None:
        self.assertEqual(key_reader.hearing({}, "darwin", listen_access=lambda: True).via,
                         "pynput")
        refused = key_reader.hearing({}, "darwin", listen_access=lambda: False)
        self.assertFalse(refused.available)
        assert refused.reason is not None
        self.assertEqual(refused.reason["key"], key_reader.NO_INPUT_MONITORING)
        self.assertEqual(refused.reason["fix"], "user")

    def test_wayland_hears_from_the_devices_it_can_read(self) -> None:
        self.assertEqual(key_reader.hearing(self.WAYLAND, "linux",
                                            devices=lambda: ["/dev/input/event3"]).via,
                         "evdev")
        refused = key_reader.hearing(self.WAYLAND, "linux", devices=list)
        assert refused.reason is not None
        self.assertEqual(refused.as_dict()["reason"]["key"], key_reader.NO_INPUT_GROUP)
        self.assertEqual(refused.reason["remedy"]["key"], key_reader.JOIN_INPUT_GROUP)

    def test_no_session_is_nothing_to_fix(self) -> None:
        reason = key_reader.hearing({}, "linux").reason
        assert reason is not None
        self.assertEqual(reason["fix"], "none")

    def test_every_reason_is_in_the_catalog(self) -> None:
        catalog = served()
        for key in (key_reader.NOT_HEARD, key_reader.NO_INPUT_GROUP,
                    key_reader.JOIN_INPUT_GROUP, key_reader.NO_INPUT_MONITORING,
                    key_reader.ALLOW_INPUT_MONITORING, key_reader.NO_SESSION):
            with self.subTest(key=key):
                self.assertIn(key, catalog)

    def test_the_keyboards_are_the_readable_kbd_devices(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            listing = Path(held) / "devices"
            listing.write_text(DEVICES, encoding="utf-8")

            found = key_reader.keyboards(str(listing), lambda path: path.endswith("3"))
            everything = key_reader.keyboards(str(listing), lambda _path: True)

        self.assertEqual(found, ["/dev/input/event3"])
        self.assertEqual(everything, ["/dev/input/event3", "/dev/input/event0"])

    def test_no_device_listing_is_no_keyboards(self) -> None:
        self.assertEqual(key_reader.keyboards("/nowhere/devices"), [])


class WantedTests(unittest.TestCase):
    def test_nothing_is_listened_for_until_take_picture_has_a_key(self) -> None:
        self.assertEqual(key_reader.wanted({"take_picture": [], "back": ["key:KeyB"]}), {})
        self.assertEqual(key_reader.wanted({"take_picture": ["pad:0/button:7"],
                                            "back": ["key:KeyB"]}), {})

    def test_the_play_actions_keys_and_no_others(self) -> None:
        found = key_reader.wanted({"take_picture": ["key:p", "pad:0/button:7"],
                                   "back": ["key:KeyB", "key:Escape@hold:800"],
                                   "select": ["key:Enter"]})

        self.assertEqual(found, {"KeyP": ["take_picture"], "KeyB": ["back"]})

    def test_a_key_held_by_both_presses_both(self) -> None:
        found = key_reader.wanted({"take_picture": ["key:F9"], "back": ["key:F9"]})
        self.assertEqual(found, {"F9": ["take_picture", "back"]})


class ReaderTests(unittest.TestCase):
    def _reader(self, via: str = key_reader.EVDEV, **kwargs):
        pressed: list[tuple[str, str]] = []
        reader = key_reader.Reader(
            key_reader.Hearing(via), {"KeyP": ["take_picture"], "KeyB": ["back"]},
            press=lambda action, source: pressed.append((action, source)), **kwargs)
        return reader, pressed

    @needs_selectable_files
    def test_a_key_going_down_presses_its_action(self) -> None:
        reader, pressed = self._reader()
        read_end, write_end = os.pipe()
        p = keys.BY_CODE["KeyP"].evdev
        os.write(write_end, key_reader.event(p) + key_reader.event(p, value=0)
                 + key_reader.event(p, value=2) + key_reader.event(0, kind=4))
        whole = key_reader.event(keys.BY_CODE["KeyB"].evdev)
        # An event split across two reads is still one event.
        os.write(write_end, whole[:10])

        def finish() -> None:
            os.write(write_end, whole[10:])
            os.close(write_end)

        writer = threading.Timer(0.05, finish)
        writer.start()
        try:
            reader.read([read_end])
        finally:
            writer.join()
            os.close(read_end)

        self.assertEqual(pressed, [("take_picture", "keyboard"), ("back", "keyboard")])

    def test_a_key_nobody_bound_presses_nothing(self) -> None:
        reader, pressed = self._reader()
        reader.hear("KeyQ")
        reader.hear("")
        self.assertEqual(pressed, [])

    @needs_selectable_files
    def test_stopping_ends_a_read(self) -> None:
        reader, _ = self._reader()
        read_end, write_end = os.pipe()
        self.addCleanup(os.close, read_end)
        self.addCleanup(os.close, write_end)
        thread = threading.Thread(target=reader.read, args=([read_end],))
        thread.start()

        reader.stop()
        thread.join(2)

        self.assertFalse(thread.is_alive())

    def test_pynput_hears_through_a_listener_it_starts_and_stops(self) -> None:
        made: dict = {}

        class Listener:
            def __init__(self, on_press):
                made["on_press"] = on_press
                made["state"] = "made"

            def start(self):
                made["state"] = "started"

            def stop(self):
                made["state"] = "stopped"

        reader, pressed = self._reader(key_reader.PYNPUT, listener=Listener)
        reader.start()
        made["on_press"](SimpleNamespace(char="p"))
        made["on_press"](SimpleNamespace(name="esc"))
        reader.stop()

        self.assertEqual(pressed, [("take_picture", "keyboard")])
        self.assertEqual(made["state"], "stopped")

    def test_pynput_keys_name_the_codes_a_binding_does(self) -> None:
        for key, code in ((SimpleNamespace(char="P"), "KeyP"),
                          (SimpleNamespace(char="7"), "Digit7"),
                          (SimpleNamespace(char="/"), "Slash"),
                          (SimpleNamespace(name="f9"), "F9"),
                          (SimpleNamespace(name="f20"), "F20"),
                          (SimpleNamespace(name="shift"), "ShiftLeft"),
                          (SimpleNamespace(name="page_up"), "PageUp"),
                          (SimpleNamespace(name="print_screen"), "PrintScreen"),
                          (SimpleNamespace(char=None), "")):
            with self.subTest(key=key):
                self.assertEqual(key_reader.code_of_pynput(key), code)


class WhileATableRunsTests(unittest.TestCase):
    def setUp(self) -> None:
        key_reader.reset_for_tests()
        self.addCleanup(key_reader.reset_for_tests)
        self.made: list = []

        test = self

        class Reader:
            def __init__(self, heard, listening, **_kwargs):
                self.listening = listening
                self.state = "made"
                test.made.append(self)

            def start(self):
                self.state = "started"

            def stop(self):
                self.state = "stopped"

        for patch in (mock.patch.object(key_reader, "Reader", Reader),
                      mock.patch.object(key_reader, "_bound", return_value={
                          "take_picture": ["key:KeyP"], "back": ["key:KeyB"]})):
            patch.start()
            self.addCleanup(patch.stop)
        key_reader.register()

    def _hearing(self, via: str):
        return mock.patch.object(key_reader, "hearing", return_value=key_reader.Hearing(
            via, None if via else {"key": key_reader.NO_INPUT_GROUP, "params": {}}))

    def test_it_listens_from_launch_to_exit(self) -> None:
        with self._hearing("evdev"):
            events.emit(events.TABLE_LAUNCHED, source=launch_state.SOURCE_FRONTEND)
        self.assertEqual([one.state for one in self.made], ["started"])
        self.assertEqual(self.made[0].listening, {"KeyP": ["take_picture"],
                                                  "KeyB": ["back"]})

        events.emit(events.TABLE_EXITED, source=launch_state.SOURCE_FRONTEND)

        self.assertEqual(self.made[0].state, "stopped")

    def test_a_recording_is_not_listened_to(self) -> None:
        with self._hearing("evdev"):
            events.emit(events.TABLE_LAUNCHED, source=launch_state.SOURCE_CAPTURE)
        self.assertEqual(self.made, [])

    def test_a_device_that_cannot_hear_says_so_once(self) -> None:
        with self._hearing(""), \
                self.assertLogs("vpinfe.common.host.key_reader", "WARNING") as logs:
            events.emit(events.TABLE_LAUNCHED, source=launch_state.SOURCE_FRONTEND)
            events.emit(events.TABLE_EXITED, source=launch_state.SOURCE_FRONTEND)
            events.emit(events.TABLE_LAUNCHED, source=launch_state.SOURCE_FRONTEND)

        self.assertEqual(self.made, [])
        self.assertEqual(len(logs.output), 1)
        self.assertIn("Take Picture", logs.output[0])


if __name__ == "__main__":
    unittest.main()
