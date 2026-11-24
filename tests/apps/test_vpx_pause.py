"""Visual Pinball's Pause: its own key, and the lines it writes on either side."""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import mock

from apps.vpx import VPX, pause
from common.apps.contract import Pause


class VPXPauseTests(unittest.TestCase):
    def _ini(self, body: str | bytes) -> str:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        path = Path(held.name) / "VPinballX.ini"
        if isinstance(body, bytes):
            path.write_bytes(body)
        else:
            path.write_text(body, encoding="utf-8")
        return str(path)

    def test_the_app_says_how_it_pauses(self) -> None:
        self.assertIsInstance(VPX.pause, Pause)
        assert VPX.pause is not None
        self.assertEqual((VPX.pause.paused_marker, VPX.pause.resumed_marker,
                          VPX.pause.focus_lost_marker),
                         ("Pausing Game", "Unpausing Game", "Playfield window lost focus"))

    def test_the_key_is_the_one_its_settings_file_maps_to_pause(self) -> None:
        ini = self._ini("[Input]\nMapping.Pause = Key;72\n")
        self.assertEqual(pause.VPXPause().key({"ini_path": ini}), "Pause")

    def test_p_where_nothing_is_mapped(self) -> None:
        ini = self._ini("[Input]\nMapping.Pause = \nMapping.Start = Key;30\n")
        self.assertEqual(pause.VPXPause().key({"ini_path": ini}), "KeyP")

    def test_a_file_that_is_not_utf8_pauses_on_p(self) -> None:
        latin = b"[Input]\nMapping.Pause = Key;72\nMapping.Note = caf\xe9\n"

        with self.assertLogs("vpinfe.apps.vpx.pause", level="WARNING") as caught:
            key = pause.VPXPause().key({"ini_path": self._ini(latin)})

        self.assertEqual(key, "KeyP")
        self.assertIn("VPinballX.ini", caught.output[0])

    def test_its_own_file_where_the_launcher_names_none(self) -> None:
        ini = self._ini("[Input]\nMapping.Pause = Key;72\n")
        with mock.patch.object(pause, "own_file", return_value=Path(ini)) as own:
            self.assertEqual(pause.VPXPause().key({"ini_path": "", "bin_path": "/opt/vpx"}),
                             "Pause")
        own.assert_called_once_with("/opt/vpx")


if __name__ == "__main__":
    unittest.main()
