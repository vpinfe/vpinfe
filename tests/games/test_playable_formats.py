"""The formats a library's enabled launchers play."""

from __future__ import annotations

import unittest
from unittest import mock

from common.games import launchers


def _launcher(app: str = "vpx", **over: object) -> launchers.Launcher:
    return launchers.Launcher(launcher_id="l1", app=app, **over)  # type: ignore[arg-type]


class PlayableFormats(unittest.TestCase):
    def test_they_are_what_the_enabled_launchers_apps_play(self) -> None:
        self.assertEqual(("VPX",), launchers.playable_formats([_launcher()]))

    def test_a_disabled_launcher_adds_nothing(self) -> None:
        self.assertEqual((), launchers.playable_formats([_launcher(enabled=False)]))

    def test_an_app_declaring_none_plays_everything_so_nothing_narrows(self) -> None:
        with mock.patch.object(launchers.apps, "vps_formats", return_value=()):
            self.assertEqual((), launchers.playable_formats([_launcher()]))

    def test_two_launchers_add_their_formats_once_in_the_order_found(self) -> None:
        declared = {"a": ("VPX", "FP"), "b": ("FP", "BGFX")}
        with mock.patch.object(launchers.apps, "vps_formats", side_effect=declared.get):
            found = launchers.playable_formats([_launcher("a"), _launcher("b")])

        self.assertEqual(("VPX", "FP", "BGFX"), found)

    def test_one_app_declaring_none_ends_the_narrowing_wherever_it_comes(self) -> None:
        declared = {"a": ("VPX",), "none": ()}
        with mock.patch.object(launchers.apps, "vps_formats", side_effect=declared.get):
            first = launchers.playable_formats([_launcher("none"), _launcher("a")])
            last = launchers.playable_formats([_launcher("a"), _launcher("none")])

        self.assertEqual(((), ()), (first, last))

    def test_a_disabled_launcher_declaring_none_does_not_end_it(self) -> None:
        declared = {"a": ("VPX",), "none": ()}
        with mock.patch.object(launchers.apps, "vps_formats", side_effect=declared.get):
            found = launchers.playable_formats(
                [_launcher("a"), _launcher("none", enabled=False)])

        self.assertEqual(("VPX",), found)


if __name__ == "__main__":
    unittest.main()
