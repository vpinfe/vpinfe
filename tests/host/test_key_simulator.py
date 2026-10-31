"""Pressing a key into a running table: which way this device presses, and what each
way is run with. Nothing here types into this machine."""

from __future__ import annotations

import importlib
import sys
import unittest
from pathlib import Path

# pynput is no longer imported at module scope - see `test_imports_without_pynput` below.
# Quartz still is, on macOS only, and PyObjC does not survive being stubbed out, so it is
# imported plainly and the suite skips where the platform will not have it at all.
try:
    from common.host.key_simulator import KeySimulator
except Exception as exc:                    # no display, or no PyObjC
    KeySimulator = None  # type: ignore[assignment,misc]
    IMPORT_ERROR: Exception | None = exc
else:
    IMPORT_ERROR = None


class HeadlessImportTests(unittest.TestCase):
    """The module imports where pynput cannot: a headless runner, a container, a server
    install. Not skipped on macOS, since nothing may touch pynput at import anywhere; the
    import is stubbed rather than the module, so the real module is what is tested.
    """

    def _import_with_pynput_missing(self, platform: str):
        import builtins

        real_import = builtins.__import__

        def refuse(name, *args, **kwargs):
            if name.startswith("pynput"):
                raise ImportError("this platform is not supported")
            return real_import(name, *args, **kwargs)

        saved = {name: module for name, module in sys.modules.items()
                 if name.startswith(("pynput", "common.host.key_simulator"))}
        real_platform = sys.platform
        try:
            for name in saved:
                del sys.modules[name]
            builtins.__import__ = refuse
            sys.platform = platform
            return importlib.import_module("common.host.key_simulator")
        finally:
            builtins.__import__ = real_import
            sys.platform = real_platform
            sys.modules.pop("common.host.key_simulator", None)
            sys.modules.update(saved)

    def test_imports_without_pynput(self) -> None:
        """A headless Linux runner, which is what CI is."""
        module = self._import_with_pynput_missing("linux")

        self.assertTrue(hasattr(module, "KeySimulator"))

    def test_the_key_map_is_not_built_until_it_is_asked_for(self) -> None:
        """A class attribute here would mean the map was built during the import above."""
        module = self._import_with_pynput_missing("linux")

        self.assertFalse(hasattr(module.KeySimulator, "KEY_ID_TO_PYNPUT"))
        self.assertTrue(callable(module.KeySimulator.key_id_to_pynput))


# These build the real map, so they need a machine where pynput loads; a headless runner
# imports the module and raises here.
try:
    import pynput.keyboard  # noqa: F401
except Exception as exc:                    # no display, or no input backend
    PYNPUT_ERROR: Exception | None = exc
else:
    PYNPUT_ERROR = None


@unittest.skipIf(KeySimulator is None, f"key_simulator unavailable here: {IMPORT_ERROR}")
@unittest.skipIf(PYNPUT_ERROR is not None, f"pynput will not load here: {PYNPUT_ERROR}")
class KeyMapTests(unittest.TestCase):
    def test_the_map_carries_every_key_id_the_page_can_send(self) -> None:
        """Derived from names rather than written out, so this checks the derivation
        against the ids the Remote page actually sends."""
        mapping = KeySimulator.key_id_to_pynput()

        for key_id in ("enter", "esc", "space", "f1", "f12", "up", "down",
                       "ctrl_l", "shift_l", "a", "z", "0", "9", "-", "/"):
            with self.subTest(key_id=key_id):
                self.assertIn(key_id, mapping)

    def test_right_command_types_as_command(self) -> None:
        """The one entry that is not `Key.<its own name>`; pynput's cmd_r is unused."""
        mapping = KeySimulator.key_id_to_pynput()

        self.assertEqual(mapping["cmd_r"], mapping["cmd"])


def _found(tool, *, works: bool = True, missing: bool = False):
    from common.host import tools

    if missing:
        return tools.Found(tool, tools.State.MISSING)
    return tools.Found(tool, tools.State.FOUND, Path(f"/usr/bin/{tool.name}"),
                       tools.Probe(works))


class _Recorded:
    """A runner that notes each argv and environment it is given."""

    def __init__(self) -> None:
        self.calls: list[tuple[list[str], dict | None]] = []

    def __call__(self, argv, **kwargs):
        self.calls.append((list(argv), kwargs.get("env")))


@unittest.skipIf(KeySimulator is None, f"key_simulator unavailable here: {IMPORT_ERROR}")
class SenderTests(unittest.TestCase):
    """Which way a device presses keys, read off its session."""

    SWAY = {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/run/sway.sock"}
    PLASMA = {"WAYLAND_DISPLAY": "wayland-0", "XDG_CURRENT_DESKTOP": "KDE"}

    def _sender(self, env, have=("wtype", "ydotool"), socket=True, system="linux",
                post_access=True, macos_major=26):
        from common.host import key_simulator

        def resolve(tool):
            return _found(tool, missing=tool.id not in have)

        return key_simulator.sender(env, system, resolve,
                                    exists=lambda _path: socket,
                                    post_access=lambda: post_access,
                                    macos_major=lambda: macos_major)

    def test_a_wlroots_session_presses_through_wtype(self) -> None:
        found = self._sender(self.SWAY)
        self.assertEqual((found.via, str(found.path)), ("wtype", str(Path("/usr/bin/wtype"))))

    def test_without_wtype_it_is_ydotool_through_its_socket(self) -> None:
        found = self._sender({**self.SWAY, "XDG_RUNTIME_DIR": "/run/user/1000"},
                             have=("ydotool",))
        self.assertEqual((found.via, found.socket),
                         ("ydotool", "/run/user/1000/.ydotool_socket"))

    def test_another_compositor_tries_ydotool_first(self) -> None:
        self.assertEqual(self._sender(self.PLASMA).via, "ydotool")
        self.assertEqual(self._sender(self.PLASMA, socket=False).via, "wtype")

    def test_ydotool_with_its_service_stopped_says_so(self) -> None:
        found = self._sender(self.SWAY, have=("ydotool",), socket=False)

        self.assertFalse(found.available)
        self.assertEqual(found.reason["key"], "keys.send.no_ydotool_service")
        self.assertEqual(found.reason["fix"], "user")
        self.assertTrue(found.reason["remedy"])

    def test_neither_tool_names_the_one_to_install(self) -> None:
        found = self._sender(self.SWAY, have=())

        self.assertEqual(found.reason["key"], "keys.send.needs_tool")
        self.assertEqual(found.reason["params"], {"tool": "wtype"})
        self.assertEqual(found.reason["remedy"]["setting"], "tools.wtype_path")

    def test_x11_windows_and_macos_use_pynput(self) -> None:
        self.assertEqual(self._sender({"DISPLAY": ":0"}).via, "pynput")
        for system in ("windows", "darwin"):
            with self.subTest(system=system):
                self.assertEqual(self._sender({}, system=system).via, "pynput")

    def test_macos_without_permission_to_post_keys_cannot_press(self) -> None:
        for system, available in (("darwin", False), ("windows", True), ("linux", True)):
            with self.subTest(system=system):
                found = self._sender({"DISPLAY": ":0"}, system=system, post_access=False)
                self.assertEqual(found.available, available)
        denied = self._sender({}, system="darwin", post_access=False)
        self.assertEqual(denied.reason["key"], "keys.send.no_post_access")
        self.assertEqual(denied.reason["fix"], "user")
        self.assertEqual(denied.reason["remedy"]["key"], "keys.send.allow_post_access")

    def test_macos_27_names_the_pane_it_calls_device_control_and_data_access(self) -> None:
        from common.host import key_simulator

        denied = self._sender({}, system="darwin", post_access=False, macos_major=27)
        self.assertEqual(denied.reason["remedy"]["key"],
                         "keys.send.device_control_pane")
        said = key_simulator.words(denied.reason)
        self.assertIn("Device Control and Data Access", said)
        self.assertTrue(said.endswith("."))

    def test_macos_without_the_permission_asks_for_it(self) -> None:
        from common.host import key_simulator

        asked: list[str] = []
        allowed = key_simulator.ask_for_post_access(
            "darwin", post_access=lambda: False,
            request=lambda: asked.append("dialog") or False)

        self.assertEqual(asked, ["dialog"])
        self.assertFalse(allowed)

    def test_macos_with_the_permission_does_not_ask(self) -> None:
        from common.host import key_simulator

        def refuse() -> bool:
            raise AssertionError("asked although the permission was held")

        self.assertTrue(key_simulator.ask_for_post_access(
            "darwin", post_access=lambda: True, request=refuse))

    def test_other_systems_have_nothing_to_ask_for(self) -> None:
        from common.host import key_simulator

        def refuse() -> bool:
            raise AssertionError("asked on a system with no such permission")

        for system in ("linux", "windows"):
            self.assertTrue(key_simulator.ask_for_post_access(
                system, post_access=lambda: False, request=refuse))

    def test_no_session_is_nothing_to_fix(self) -> None:
        found = self._sender({})

        self.assertEqual(found.as_dict()["reason"]["key"], "keys.send.no_session")
        self.assertEqual(found.reason["fix"], "none")

    def test_every_reason_is_in_the_catalog(self) -> None:
        from common.host import key_simulator
        from tests.support.catalogs import served

        catalog = served()
        for key in (key_simulator.NEEDS_TOOL, key_simulator.NO_YDOTOOL_SERVICE,
                    key_simulator.NO_SESSION, key_simulator.START_YDOTOOL_SERVICE,
                    key_simulator.NO_POST_ACCESS, key_simulator.ALLOW_POST_ACCESS,
                    key_simulator.DEVICE_CONTROL_PANE,
                    "keys.send.with_remedy"):
            with self.subTest(key=key):
                self.assertIn(key, catalog)


@unittest.skipIf(KeySimulator is None, f"key_simulator unavailable here: {IMPORT_ERROR}")
class BackendTests(unittest.TestCase):
    """What each Tool is run with. Nothing here types into this machine."""

    def _simulator(self, via: str, **found):
        from common.host import key_simulator

        runner = _Recorded()
        chosen = key_simulator.Sender(via, Path(f"/usr/bin/{via}"), **found)
        return key_simulator.KeySimulator(sender_found=chosen,
                                          runner=runner), runner

    def test_wtype_types_a_key_by_its_keysym(self) -> None:
        simulator, runner = self._simulator("wtype")

        self.assertTrue(simulator.press_code("KeyP"))
        self.assertTrue(simulator.press_code("Escape"))
        self.assertTrue(simulator.hold("page_up", 0.1))
        self.assertTrue(simulator.combo("ctrl_l", "a"))

        wtype = str(Path("/usr/bin/wtype"))
        self.assertEqual([argv for argv, _ in runner.calls], [
            [wtype, "-k", "p"],
            [wtype, "-k", "Escape"],
            [wtype, "-P", "Prior", "-s", "100", "-p", "Prior"],
            [wtype, "-P", "Control_L", "-P", "a", "-p", "a", "-p", "Control_L"],
        ])

    def test_ydotool_presses_the_evdev_number_through_its_socket(self) -> None:
        simulator, runner = self._simulator("ydotool", socket="/tmp/.ydotool_socket")

        self.assertTrue(simulator.press_code("KeyP"))

        argv, env = runner.calls[0]
        self.assertEqual(argv, [str(Path("/usr/bin/ydotool")), "key", "25:1", "25:0"])
        self.assertEqual(env["YDOTOOL_SOCKET"], "/tmp/.ydotool_socket")

    def test_ydotool_leaves_a_key_down_and_lets_it_go_in_two_calls(self) -> None:
        simulator, runner = self._simulator("ydotool", socket="/tmp/.ydotool_socket")

        self.assertTrue(simulator.backend.holds)
        self.assertTrue(simulator.down_code("KeyP"))
        self.assertTrue(simulator.up_code("KeyP"))

        self.assertEqual([argv[-1] for argv, _ in runner.calls], ["25:1", "25:0"])

    def test_wtype_cannot_leave_a_key_down_across_two_commands(self) -> None:
        simulator, runner = self._simulator("wtype")

        self.assertFalse(simulator.backend.holds)
        self.assertFalse(simulator.down_code("KeyP"))
        self.assertFalse(simulator.up_code("KeyP"))
        self.assertEqual(runner.calls, [])

    def test_a_device_that_cannot_press_cannot_hold(self) -> None:
        from common.host import key_simulator

        chosen = key_simulator.Sender("", reason={"key": key_simulator.NO_SESSION,
                                                  "params": {}})

        self.assertFalse(key_simulator.can_hold(chosen))

    def test_a_key_with_no_name_here_presses_nothing(self) -> None:
        simulator, runner = self._simulator("wtype")

        with self.assertLogs("vpinfe.common.host.key_simulator", level="WARNING"):
            self.assertFalse(simulator.press_code("Lang3"))
        self.assertEqual(runner.calls, [])

    def test_a_device_that_cannot_press_says_why_once(self) -> None:
        from common.host import key_simulator

        chosen = key_simulator.Sender("", reason={"key": key_simulator.NO_SESSION,
                                                  "params": {}})
        simulator = key_simulator.KeySimulator(sender_found=chosen)

        with self.assertLogs("vpinfe.common.host.key_simulator", level="WARNING") as logs:
            self.assertFalse(simulator.press_code("KeyP"))
            self.assertFalse(simulator.press_code("KeyP"))
        self.assertEqual(len(logs.output), 1)

    def test_a_tool_that_fails_is_a_key_not_pressed(self) -> None:
        import subprocess

        from common.host import key_simulator

        def failing(argv, **_kwargs):
            raise subprocess.CalledProcessError(1, argv, stderr="compositor said no")

        chosen = key_simulator.Sender("wtype", Path("/usr/bin/wtype"))
        simulator = key_simulator.KeySimulator(sender_found=chosen,
                                               runner=failing)
        with self.assertLogs("vpinfe.common.host.key_simulator", level="WARNING") as logs:
            self.assertFalse(simulator.press_code("KeyP"))
        self.assertIn("compositor said no", logs.output[0])


class KeyTableTests(unittest.TestCase):
    """One row per key, so pressing and hearing read the same table."""

    def test_every_code_and_number_is_one_key(self) -> None:
        from common.host import keys

        self.assertEqual(len(keys.BY_CODE), len(keys.KEYS))
        self.assertEqual(len(keys.BY_EVDEV), len(keys.KEYS))

    def test_codes_are_the_ones_a_binding_names(self) -> None:
        """A code the settings page has no name for is a code no binding holds: a
        letter, a digit, a function key or one of the named keys."""
        import re

        from common.config import input_registry
        from common.host import keys

        named = set(input_registry.key_names())
        spelled = re.compile(r"^(Key[A-Z]|Digit\d|F\d{1,2})$")
        self.assertEqual([key.code for key in keys.KEYS
                          if key.code not in named and not spelled.match(key.code)], [])

    def test_the_old_ydotool_numbers_are_kept(self) -> None:
        from common.host import keys

        for key_id, number in {"esc": 1, "p": 25, "enter": 28, "pause": 119,
                               "page_down": 109, "cmd_r": 126, "f12": 88}.items():
            with self.subTest(key_id=key_id):
                self.assertEqual(keys.BY_KEY_ID[key_id].evdev, number)


@unittest.skipUnless(sys.platform == "darwin", "the macOS key table is only built on macOS")
class MacOSKeycodeTests(unittest.TestCase):
    def test_each_side_of_a_modifier_posts_its_own_key(self) -> None:
        from unittest import mock

        import Quartz
        from pynput.keyboard import Key

        from common.host import key_simulator

        posted: list[int] = []

        def create(_source, keycode, _down):
            posted.append(keycode)
            return object()

        with mock.patch.object(Quartz, "CGEventCreateKeyboardEvent", create), \
                mock.patch.object(Quartz, "CGEventPost", lambda *_: None):
            controller = key_simulator._quartz_controller()
            for key in (Key.shift_l, Key.shift_r, Key.ctrl_l, Key.ctrl_r, Key.alt_l,
                        Key.alt_r):
                controller.press(key)

        self.assertEqual(posted, [56, 60, 59, 62, 58, 61])


if __name__ == "__main__":
    unittest.main()
