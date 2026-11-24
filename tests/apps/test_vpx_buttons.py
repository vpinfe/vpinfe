"""The buttons a cabinet is wired for, as the keys Visual Pinball hears them on.

The file's own `Mapping.*` is the answer where it has one. Visual Pinball writes the entry
blank until somebody binds it, and a blank one still sends the key its built-in table
gives that button, so a machine that never opened the key settings is still a cabinet.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.vpx import VPX, buttons
from apps.vpx.buttons import VPXButtons
from common.apps.contract import Buttons

BUILT_IN = {
    "left_flipper": ("ShiftLeft",), "right_flipper": ("ShiftRight",),
    "left_magna": ("ControlLeft",), "right_magna": ("ControlRight",),
    "start": ("Digit1",), "fire": ("AltLeft",), "extra_ball": ("KeyB",),
    "tilt": ("KeyT",), "exit": ("Escape",),
}


class VPXButtonsTests(unittest.TestCase):
    def setUp(self) -> None:
        buttons._said.clear()

    def _ini(self, body: str | bytes, encoding: str = "utf-8") -> dict:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        path = Path(held.name) / "VPinballX.ini"
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding=encoding)
        return {"ini_path": str(path)}

    def test_the_app_says_what_its_cabinet_sends(self) -> None:
        self.assertIsInstance(VPX.buttons, Buttons)

    def test_a_file_that_maps_nothing_sends_the_built_in_keys(self) -> None:
        blank = "[Input]\nMapping.Start = \nMapping.LeftFlipper = \n"

        with self.assertLogs("vpinfe.apps.vpx.keys", level="WARNING"):
            found = VPXButtons().keys(self._ini(blank))

        self.assertEqual(found, BUILT_IN)

    def test_no_file_at_all_is_the_built_in_table_too(self) -> None:
        self.assertEqual(VPXButtons().keys({}), BUILT_IN)

    def test_the_file_wins_over_the_built_in_key(self) -> None:
        found = VPXButtons().keys(self._ini(
            "[Input]\nMapping.Start = Key;19\nMapping.ExitGame = Key;20\n"))

        self.assertEqual(found["start"], ("KeyP",))
        self.assertEqual(found["exit"], ("KeyQ",))
        self.assertEqual(found["tilt"], ("KeyT",), "what the file leaves out stays built in")

    def test_a_file_that_is_not_utf8_sends_the_built_in_keys(self) -> None:
        latin = b"[Input]\nMapping.Start = Key;19\nMapping.Note = caf\xe9\n"

        with self.assertLogs("vpinfe.apps.vpx.buttons", level="WARNING") as caught:
            found = VPXButtons().keys(self._ini(latin))

        self.assertEqual(found, BUILT_IN)
        self.assertIn("VPinballX.ini", caught.output[0])

    def test_a_utf16_file_sends_the_built_in_keys(self) -> None:
        with self.assertLogs("vpinfe.apps.vpx.buttons", level="WARNING"):
            found = VPXButtons().keys(self._ini(
                "[Input]\nMapping.Start = Key;19\n", encoding="utf-16"))

        self.assertEqual(found, BUILT_IN)

    def test_a_button_the_keyboard_cannot_send_says_what_was_read_and_used(self) -> None:
        pad_only = self._ini("[Input]\nMapping.Start = Joy;0;1\nMapping.Tilt = Key;20\n")

        with self.assertLogs("vpinfe.apps.vpx.buttons", level="INFO") as caught:
            found = VPXButtons().keys(pad_only)
            VPXButtons().keys(pad_only)

        self.assertEqual(found["start"], ("Digit1",))
        self.assertEqual(len(caught.records), 1, "said once for the same file")
        self.assertIn("Mapping.Start = Joy;0;1", caught.output[0])
        self.assertIn("Digit1", caught.output[0])

    def test_a_scancode_with_no_key_here_is_said_too(self) -> None:
        with self.assertLogs("vpinfe.apps.vpx.buttons", level="INFO") as caught:
            found = VPXButtons().keys(self._ini("[Input]\nMapping.Lockbar = Key;600\n"))

        self.assertEqual(found["fire"], ("AltLeft",))
        self.assertIn("Mapping.Lockbar = Key;600", caught.output[0])

    def test_a_blank_entry_is_the_normal_case_and_is_not_said(self) -> None:
        with self.assertNoLogs("vpinfe.apps.vpx.buttons", level="INFO"):
            VPXButtons().keys(self._ini("[Input]\nMapping.Start = \nMapping.Tilt = Key;20\n"))

    def test_tilt_is_t_so_tutorial_cannot_be(self) -> None:
        """The cabinet's tilt bob is the T key in Visual Pinball's own table."""
        self.assertEqual(VPXButtons().keys({})["tilt"], ("KeyT",))


if __name__ == "__main__":
    unittest.main()
