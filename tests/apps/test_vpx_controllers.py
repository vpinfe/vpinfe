"""Protects: (e) the SDL library beside each OS's Visual Pinball is found or refused with a reason.

Presses and names are checked the way the program numbers and names them. SDL itself is not
loaded: a stand-in answers the few calls the listener makes.
"""

from __future__ import annotations

import struct
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from apps.vpx import bindings, controllers

PAD = "SDLJoy_030000007e0500000920000000000000_1"


def _touch(folder: Path, *names: str) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")


def _dll(path: Path, machine: int) -> None:
    head = bytearray(64)
    head[:2] = b"MZ"
    struct.pack_into("<I", head, 60, 64)
    path.write_bytes(bytes(head) + b"PE\0\0" + struct.pack("<H", machine) + b"\0" * 32)


class FindLibraryTests(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def test_on_macos_it_is_in_the_bundle_s_frameworks(self) -> None:
        app = self.root / "VPinballX_BGFX.app"
        _touch(app / "Contents" / "Frameworks", "libSDL3.0.dylib", "libOther.dylib")

        self.assertEqual(controllers.find_library(str(app), "darwin"),
                         app / "Contents" / "Frameworks" / "libSDL3.0.dylib")

    def test_on_macos_a_path_inside_the_bundle_finds_the_same_one(self) -> None:
        app = self.root / "VPinballX_BGFX.app"
        _touch(app / "Contents" / "Frameworks", "libSDL3.dylib")
        _touch(app / "Contents" / "MacOS", "VPinballX_BGFX")

        self.assertEqual(
            controllers.find_library(str(app / "Contents" / "MacOS" / "VPinballX_BGFX"),
                                     "darwin"),
            app / "Contents" / "Frameworks" / "libSDL3.dylib")

    def test_on_macos_a_versioned_name_is_found_by_its_prefix(self) -> None:
        app = self.root / "VPinballX_BGFX.app"
        _touch(app / "Contents" / "Frameworks", "libSDL3.0.5.dylib")

        self.assertEqual(controllers.find_library(str(app), "darwin"),
                         app / "Contents" / "Frameworks" / "libSDL3.0.5.dylib")

    def test_on_windows_it_is_beside_the_program_and_the_64_bit_name_comes_first(self) -> None:
        _touch(self.root, "VPinballX64.exe", "SDL3.dll", "SDL364.dll")

        self.assertEqual(controllers.find_library(str(self.root / "VPinballX64.exe"), "win32"),
                         self.root / "SDL364.dll")

    def test_on_windows_the_32_bit_name_is_found_when_it_is_all_there_is(self) -> None:
        _touch(self.root, "VPinballX.exe", "SDL3.dll")

        self.assertEqual(controllers.find_library(str(self.root / "VPinballX.exe"), "win32"),
                         self.root / "SDL3.dll")

    def test_on_linux_it_is_beside_the_program(self) -> None:
        _touch(self.root, "VPinballX_BGFX", "libSDL3.so.0.4.0")

        self.assertEqual(controllers.find_library(str(self.root / "VPinballX_BGFX"), "linux"),
                         self.root / "libSDL3.so.0.4.0")

    def test_a_folder_with_none_finds_none(self) -> None:
        _touch(self.root, "VPinballX64.exe")

        self.assertIsNone(controllers.find_library(str(self.root / "VPinballX64.exe"), "win32"))
        self.assertIsNone(controllers.find_library(str(self.root / "missing.app"), "darwin"))


class ABitnessMismatchIsRefusedTests(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.root = Path(held.name)

    def test_the_header_says_how_wide_the_library_is(self) -> None:
        _dll(self.root / "a.dll", 0x014C)
        _dll(self.root / "b.dll", 0x8664)
        (self.root / "c.dll").write_bytes(b"not a library")

        self.assertEqual([controllers.pe_bits(self.root / name)
                          for name in ("a.dll", "b.dll", "c.dll", "missing.dll")],
                         [32, 64, 0, 0])

    def test_a_32_bit_library_is_refused_with_the_reason_on_windows(self) -> None:
        _dll(self.root / "SDL3.dll", 0x014C)

        reason = controllers.why_not_loadable(self.root / "SDL3.dll", "win32")

        self.assertIn("32-bit", reason)
        self.assertIn("SDL3.dll", reason)

    def test_a_library_that_cannot_be_read_is_left_to_try(self) -> None:
        self.assertEqual(controllers.why_not_loadable(self.root / "SDL3.dll", "win32"), "")

    def test_other_systems_are_never_refused_on_width(self) -> None:
        self.assertEqual(controllers.why_not_loadable(self.root / "libSDL3.so", "linux"), "")

    def test_with_no_library_the_page_is_told_why(self) -> None:
        tap, reason = controllers.listen(str(self.root / "missing.app"))

        self.assertIsNone(tap)
        self.assertIn("no SDL library", reason)


class _Calls(SimpleNamespace):
    """The SDL calls the listener makes, answering from a table."""

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(name)


def _pad(buttons: int = 2, hats: int = 1, axes: dict[int, tuple[int, int]] | None = None,
         ) -> controllers._Pad:
    return controllers._Pad(1, 1, PAD, bindings.DeviceInfo("Pad #1"), axes or {},
                            [False] * buttons, [0] * hats)


def _sdl(buttons: list[bool], hats: list[int], axes: dict[int, int]) -> Any:
    return SimpleNamespace(cdll=SimpleNamespace(
        SDL_GetJoystickButton=lambda _, index: buttons[index],
        SDL_GetJoystickHat=lambda _, index: hats[index],
        SDL_GetJoystickAxis=lambda _, index: axes[index]))


class PressTests(unittest.TestCase):
    def test_a_button_going_down_and_up_is_two_presses_by_its_raw_number(self) -> None:
        pad, state = _pad(), [False, True]

        down = controllers._changes(_sdl(state, [0], {}), pad)
        again = controllers._changes(_sdl(state, [0], {}), pad)
        state[1] = False
        up = controllers._changes(_sdl(state, [0], {}), pad)

        self.assertEqual(down, [controllers.Press(f"vpx:{PAD};1", True)])
        self.assertEqual(again, [])
        self.assertEqual(up, [controllers.Press(f"vpx:{PAD};1", False)])

    def test_a_hat_direction_is_numbered_the_way_the_program_numbers_it(self) -> None:
        pad = _pad(buttons=0)

        presses = controllers._changes(_sdl([], [0b1001], {}), pad)

        self.assertEqual({one.member for one in presses},
                         {f"vpx:{PAD};256", f"vpx:{PAD};258"})
        self.assertTrue(all(one.down for one in presses))

    def test_a_stick_is_down_past_half_on_the_side_it_is_pushed_and_up_below_four_tenths(
            self) -> None:
        pad = _pad(buttons=0, hats=0, axes={0: (0, 0)})
        axes = {0: 0}

        controllers._changes(_sdl([], [], axes), pad)
        axes[0] = -20000
        left = controllers._changes(_sdl([], [], axes), pad)
        axes[0] = -14000
        held = controllers._changes(_sdl([], [], axes), pad)
        axes[0] = -5000
        let_go = controllers._changes(_sdl([], [], axes), pad)

        self.assertEqual(left, [controllers.Press(f"vpx:{PAD};512;x;-0.5", True)])
        self.assertEqual(held, [])
        self.assertEqual(let_go, [controllers.Press(f"vpx:{PAD};512;x;-0.5", False)])

    def test_a_trigger_resting_at_the_bottom_is_down_past_zero(self) -> None:
        pad = _pad(buttons=0, hats=0, axes={4: (4, -32768)})
        axes = {4: -32768}

        controllers._changes(_sdl([], [], axes), pad)
        axes[4] = 2000
        down = controllers._changes(_sdl([], [], axes), pad)

        self.assertEqual(down, [controllers.Press(f"vpx:{PAD};516", True)])

    def test_a_trigger_resting_at_zero_is_down_past_one_half(self) -> None:
        pad = _pad(buttons=0, hats=0, axes={5: (5, 0)})
        axes = {5: 0}

        controllers._changes(_sdl([], [], axes), pad)
        axes[5] = 8000
        quiet = controllers._changes(_sdl([], [], axes), pad)
        axes[5] = 20000
        down = controllers._changes(_sdl([], [], axes), pad)

        self.assertEqual(quiet, [])
        self.assertEqual(down, [controllers.Press(f"vpx:{PAD};517;o;0.5", True)])

    def test_a_controller_that_goes_away_lets_go_of_what_it_held(self) -> None:
        pad = _pad()
        controllers._changes(_sdl([True, False], [0b0010], {}), pad)

        let_go = controllers._release_all(pad)

        self.assertEqual({one.member for one in let_go},
                         {f"vpx:{PAD};0", f"vpx:{PAD};257"})
        self.assertFalse(any(one.down for one in let_go))

    def test_an_axis_the_library_does_not_name_is_never_reported(self) -> None:
        pad = _pad(buttons=0, hats=0, axes={})

        self.assertEqual(controllers._changes(_sdl([], [], {0: 30000}), pad), [])


class NamingTests(unittest.TestCase):
    DEVICES = {PAD: bindings.DeviceInfo("Pro Controller #1", (
        (0, False, "B"), (256, False, "DPad Left"), (512, True, "Left Stick X"),
        (513, True, "Left Stick Y"), (516, True, "Left Trigger")))}

    def label(self, member: str) -> tuple[str, str]:
        return controllers.label_of(member, self.DEVICES)

    def test_a_button_is_the_program_s_name_with_the_controller_in_the_tooltip(self) -> None:
        self.assertEqual(self.label(f"vpx:{PAD};0"),
                         ("B", "Pro Controller #1 - B"))
        self.assertEqual(self.label(f"vpx:{PAD};256")[0], "DPad Left")

    def test_a_stick_pushed_either_way_says_which_way(self) -> None:
        self.assertEqual(self.label(f"vpx:{PAD};512;x;-0.5")[0], "Left Stick Left")
        self.assertEqual(self.label(f"vpx:{PAD};512;o;0.5")[0], "Left Stick Right")
        self.assertEqual(self.label(f"vpx:{PAD};513;x;-0.5")[0], "Left Stick Up")
        self.assertEqual(self.label(f"vpx:{PAD};513;o;0.5")[0], "Left Stick Down")

    def test_a_trigger_keeps_its_name(self) -> None:
        self.assertEqual(self.label(f"vpx:{PAD};516")[0], "Left Trigger")

    def test_what_the_program_does_not_name_is_numbered_the_way_it_numbers(self) -> None:
        self.assertEqual(self.label(f"vpx:{PAD};7")[0], "Button #7")
        self.assertEqual(self.label(f"vpx:{PAD};519;o;0.5")[0], "Axis #7 +")
        self.assertEqual(self.label(f"vpx:{PAD};260")[0], "Hat #1 Left")
        self.assertEqual(self.label(f"vpx:{PAD};259")[0], "Hat Down")

    def test_a_controller_with_no_record_is_shown_by_its_id(self) -> None:
        other = "SDLJoy_aa_1"

        self.assertEqual(controllers.label_of(f"vpx:{other};3", {}),
                         ("Button #3", f"{other} - Button #3"))


class DeviceIdTests(unittest.TestCase):
    def test_two_of_one_kind_are_told_apart_by_a_count_that_starts_at_one(self) -> None:
        guids = {1: "aa", 2: "aa", 3: "bb"}
        names = {1: "Pad", 2: "Pad", 3: "Pad"}
        sdl = SimpleNamespace(guid_of=guids.get, name_of=names.get)

        found = controllers._device_ids(sdl, [1, 2, 3])  # type: ignore[arg-type]

        self.assertEqual(found[1], ("SDLJoy_aa_1", "Pad #1", "aa"))
        self.assertEqual(found[2], ("SDLJoy_aa_2", "Pad #2", "aa"))
        self.assertEqual(found[3], ("SDLJoy_bb_1", "Pad #3", "bb"))

    def test_the_arcade_unit_is_named_the_way_the_program_names_it(self) -> None:
        guid = controllers._ARCADE_PINBALL[0]
        sdl = SimpleNamespace(guid_of=lambda _: guid, name_of=lambda _: "Xbox 360 Controller")

        found = controllers._device_ids(sdl, [1])  # type: ignore[arg-type]

        self.assertEqual(found[1][1], "Arcade2TV-XR #1")


class ReaderLifeTests(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.library = Path(held.name) / "libSDL3.so"
        self.library.write_bytes(b"")
        self.enterContext(patch.object(controllers, "find_library", return_value=self.library))
        self.enterContext(patch.object(controllers, "why_not_loadable", return_value=""))
        self.enterContext(patch.dict(controllers._readers, clear=True))

    def test_a_reader_whose_thread_died_is_replaced_for_the_next_listener(self) -> None:
        dead = controllers._Reader(self.library)
        dead._thread = threading.Thread(target=lambda: None)
        dead._thread.start()
        dead._thread.join()
        controllers._readers[self.library] = dead
        fresh = Mock(error="", taps=[], library=self.library)
        fresh.start.return_value = ""
        with patch.object(controllers, "_Reader", return_value=fresh):
            tap, reason = controllers.listen("p")

        self.assertIsNotNone(tap)
        self.assertEqual(reason, "")
        self.assertIs(controllers._readers[self.library], fresh)

    def test_a_second_listener_waits_for_the_first_start_and_hears_its_failure(self) -> None:
        reader = controllers._Reader(self.library)
        reader.error = "SDL would not start"
        reader._ready.set()
        controllers._readers[self.library] = reader

        tap, reason = controllers.listen("p")

        self.assertIsNone(tap)
        self.assertEqual(reason, "SDL would not start")
        self.assertEqual(reader.taps, [])

    def test_a_loop_that_raises_says_so_and_lets_go_of_the_library(self) -> None:
        reader = controllers._Reader(self.library)
        sdl = Mock()
        sdl.start.return_value = ""
        with patch.object(controllers, "_Sdl", return_value=sdl), \
                patch.object(reader, "_loop", side_effect=RuntimeError("boom")), \
                self.assertLogs(controllers.log, "WARNING"):
            reader._run()

        self.assertIn("boom", reader.error)
        sdl.unload.assert_called_once()


if __name__ == "__main__":
    unittest.main()
