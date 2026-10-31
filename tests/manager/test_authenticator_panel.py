"""The Authenticator section of Security."""

from __future__ import annotations

import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common.config import paths
from common.release import upgrade_needs
from common.security import authenticator
from manager.kit import appearance, panel
from manager.security import authenticator_panel


def _shown(state: dict) -> list[tuple]:
    return authenticator_panel.entries(state, "Game Room", mock.Mock(), mock.Mock(), mock.Mock())


def _labels(entries: list[tuple]) -> list[str]:
    return [str(entry[1]) for entry in entries if entry[0] is panel.HEADING] + \
        [str(entry[0]) for entry in entries
         if entry[0] not in (panel.HEADING, panel.FULL, panel.LEDE)]


class WhatTheSectionShows(unittest.TestCase):
    def test_before_one_is_set_up_it_offers_to_set_one_up_and_nothing_else(self) -> None:
        found = _shown({"enabled": False, "screen_codes": True})

        self.assertEqual(found[0], (panel.HEADING, "Authenticator"))
        self.assertEqual(len(found), 3)

    def test_the_screen_code_switch_is_offered_only_once_one_is_set_up(self) -> None:
        before = _labels(_shown({"enabled": False, "screen_codes": True}))
        after = _labels(_shown({"enabled": True, "screen_codes": True}))

        self.assertNotIn("Show Sign-In Codes on Game Room's Screen", before)
        self.assertIn("Show Sign-In Codes on Game Room's Screen", after)

    def test_once_set_up_it_offers_setting_up_again_and_removing(self) -> None:
        found = _shown({"enabled": True, "screen_codes": True})

        self.assertEqual(len(found), 5)


class TheQrDrawing(unittest.TestCase):
    def test_it_is_centered_in_whatever_dialog_draws_it(self) -> None:
        rule = re.search(r"\.manager-qr\s*\{([^}]*)\}", appearance.base_css())

        assert rule is not None
        self.assertIn("margin: 0 auto", rule.group(1))


class WithAnAuthenticator(unittest.TestCase):
    def setUp(self) -> None:
        holder = tempfile.TemporaryDirectory()
        self.addCleanup(holder.cleanup)
        root = Path(holder.name)
        for name, value in (("CONFIG_DIR", root), ("SECRETS_PATH", root / "secrets.json")):
            patched = mock.patch.object(paths, name, value)
            patched.start()
            self.addCleanup(patched.stop)
        self.addCleanup(authenticator.use, authenticator.instance())
        self.held = authenticator.Authenticator()
        authenticator.use(self.held)


class TheUpgradeStep(WithAnAuthenticator):
    def test_no_update_asks_for_one(self) -> None:
        for since in ("", "v2.6.6", "v3.0.0"):
            keys = [need.key for need in upgrade_needs.open_for(mock.Mock(), since)]

            self.assertNotIn("authenticator", keys)

    def test_nothing_it_asks_for_stops_play(self) -> None:
        self.assertEqual(upgrade_needs.blocking(mock.Mock()), [])


if __name__ == "__main__":
    unittest.main()
