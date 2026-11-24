"""VPX's key mappings, read as the keys a binding names, and said so when there are none.

Visual Pinball writes `Mapping.LeftFlipper = ` with no value until the user binds that
key in its own UI, so an entry is not a mapping until it names a key.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from apps.vpx import keys as vpx_keys

# What VPX writes before the user has bound anything, and after.
UNBOUND = "\n".join(["[Input]"] + [f"Mapping.{name} = " for name in
                                   ("LeftFlipper", "RightFlipper", "Start", "Plunger")])
BOUND = "\n".join([
    "[Input]",
    "Mapping.LeftFlipper = Key;225",
    "Mapping.RightFlipper = Key;229",
    "Mapping.Pause = Key;19",
    "Mapping.Start = ",
])

LOGGER = "vpinfe.apps.vpx.keys"


class _WithIni(unittest.TestCase):
    def _ini(self, body: str) -> str:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        path = Path(held.name) / "VPinballX.ini"
        path.write_text(body, encoding="utf-8")
        return str(path)


class MappingTests(_WithIni):
    def test_bound_entries_are_kept_as_codes_and_unbound_ones_dropped(self) -> None:
        self.assertEqual(vpx_keys.mappings(self._ini(BOUND)),
                         {"LeftFlipper": "ShiftLeft", "RightFlipper": "ShiftRight",
                          "Pause": "KeyP"})

    def test_an_unbound_ini_yields_no_mappings_and_says_so(self) -> None:
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(vpx_keys.mappings(self._ini(UNBOUND)), {})
        self.assertIn("none of the 4 vpx key mappings", logs.output[0].lower())

    def test_an_input_section_with_no_mappings_says_that_instead(self) -> None:
        with self.assertLogs(LOGGER, level="WARNING") as logs:
            self.assertEqual(vpx_keys.mappings(self._ini("[Input]\n")), {})
        self.assertIn("no mapping.* entries", logs.output[0].lower())

    def test_a_missing_file_is_no_mappings(self) -> None:
        with self.assertLogs(LOGGER, level="WARNING"):
            self.assertEqual(vpx_keys.mappings("/nowhere/VPinballX.ini"), {})

    def test_a_scancode_with_no_key_is_reported(self) -> None:
        with self.assertLogs(LOGGER, level="DEBUG") as logs:
            found = vpx_keys.mappings(self._ini("[Input]\nMapping.VRCenter = Key;600\n"
                                                "Mapping.Start = Key;30\n"))
        self.assertEqual(found, {"Start": "Digit1"})
        self.assertIn("VRCenter", "\n".join(logs.output))


class CodeTests(unittest.TestCase):
    def test_scancodes_name_the_keys_a_browser_does(self) -> None:
        for scancode, code in {4: "KeyA", 19: "KeyP", 29: "KeyZ", 30: "Digit1",
                               39: "Digit0", 40: "Enter", 41: "Escape", 58: "F1",
                               69: "F12", 72: "Pause", 75: "PageUp", 82: "ArrowUp",
                               89: "Numpad1", 98: "Numpad0", 104: "F13",
                               225: "ShiftLeft", 231: "MetaRight"}.items():
            with self.subTest(scancode=scancode):
                self.assertEqual(vpx_keys.code_of(scancode), code)

    def test_every_code_it_gives_can_be_pressed(self) -> None:
        from common.host import keys

        given = {vpx_keys.code_of(n) for n in range(0, 256)} - {""}
        self.assertEqual(sorted(given - set(keys.BY_CODE)), [])


if __name__ == "__main__":
    unittest.main()
