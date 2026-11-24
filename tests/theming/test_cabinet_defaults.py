"""A cabinet's own buttons become the defaults for the actions nobody has changed.

What the keys are is the app's to say - Visual Pinball reads its `Mapping.*` - and which
actions take them is the registry's. This is the write that joins them: the part Setup
calls once it knows the install is a cabinet.
"""

from __future__ import annotations

import configparser
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from common.config import input_registry
from common.games.launchers import Launcher
from frontend import input_api


class _Config:
    def __init__(self, **chosen: str) -> None:
        self.config = configparser.ConfigParser()
        if chosen:
            self.config.add_section(input_registry.SECTION)
            for name, value in chosen.items():
                self.config.set(input_registry.SECTION, name, value)
        self.saved = 0

    def save(self) -> None:
        self.saved += 1


class ApplyCabinetDefaultsTests(unittest.TestCase):
    def _launcher(self, mapping: str = "") -> Launcher:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        ini = Path(held.name) / "VPinballX.ini"
        ini.write_text("[Input]\nMapping.LeftFlipper = Key;225\n" + mapping,
                       encoding="utf-8")
        return Launcher(launcher_id="l1", app="vpx", display_name="Visual Pinball X",
                        settings={"ini_path": str(ini)})

    def _apply(self, config: _Config, launcher: Launcher | None) -> dict:
        with mock.patch.object(input_api.launchers, "default_launcher",
                               return_value=launcher):
            return input_api.apply_cabinet_defaults(config)

    def test_the_machines_own_mapping_is_what_lands(self) -> None:
        config = _Config()

        changed = self._apply(config, self._launcher("Mapping.Start = Key;19\n"))

        self.assertEqual(changed["select"][0], "key:KeyP")
        self.assertEqual(config.config.get("input", "select").split(",")[0], "key:KeyP")
        self.assertEqual(config.saved, 1)

    def test_a_file_that_maps_nothing_falls_back_to_visual_pinballs_own_keys(self) -> None:
        config = _Config()

        changed = self._apply(config, self._launcher("Mapping.Start = \n"))

        self.assertEqual(changed["select"][0], "key:Digit1")
        self.assertEqual(changed["tutorial"], [], "T is the tilt bob there")
        self.assertEqual(config.config.get("input", "tutorial"), "")

    def test_what_somebody_set_is_not_touched(self) -> None:
        config = _Config(select="key:F9")

        changed = self._apply(config, self._launcher())

        self.assertNotIn("select", changed)
        self.assertEqual(config.config.get("input", "select"), "key:F9")

    def test_no_launcher_changes_nothing_and_saves_nothing(self) -> None:
        config = _Config()

        self.assertEqual(self._apply(config, None), {})
        self.assertEqual(config.saved, 0)

    def test_a_second_call_changes_nothing(self) -> None:
        config = _Config()
        launcher = self._launcher()
        self._apply(config, launcher)
        saved = config.saved

        self.assertEqual(self._apply(config, launcher), {})
        self.assertEqual(config.saved, saved)


if __name__ == "__main__":
    unittest.main()
