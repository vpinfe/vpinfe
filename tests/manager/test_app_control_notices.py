"""The heads-up every Manager tab gets, which is only for what takes the page away."""

from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from common.i18n import t
from common.runtime import lifecycle
from manager import app_control


def _told(scope: str, action: str) -> list[tuple[str, str]]:
    """What a connected tab is shown when this pair is announced."""
    tab = MagicMock()
    tab.has_socket_connection = True
    with patch.object(app_control.Client, "instances", {"a": tab}), \
            patch.object(app_control.ui, "notify") as toast:
        app_control._notice(scope=scope, action=action)
    return [(call.args[0], call.kwargs["type"]) for call in toast.call_args_list]


class TheHeadsUpTests(unittest.TestCase):
    def test_each_action_that_takes_the_page_away_says_what_is_happening(self) -> None:
        said = {
            (lifecycle.VPINFE, lifecycle.RESTART): "manager.notice.restarting",
            (lifecycle.VPINFE, lifecycle.STOP): "manager.notice.quitting",
            (lifecycle.SYSTEM, lifecycle.RESTART): "manager.notice.rebooting",
            (lifecycle.SYSTEM, lifecycle.STOP): "manager.notice.powering_off",
        }
        for (scope, action), key in said.items():
            with self.subTest(pair=(scope, action)):
                self.assertEqual(_told(scope, action), [(t(key), "warning")])

    def test_the_windows_actions_say_nothing_to_the_tabs(self) -> None:
        for action in (lifecycle.START, lifecycle.STOP, lifecycle.RESTART):
            with self.subTest(action=action):
                self.assertEqual(_told(lifecycle.FRONTEND, action), [])

    def test_a_tab_with_no_socket_is_not_told(self) -> None:
        tab = MagicMock()
        tab.has_socket_connection = False
        with patch.object(app_control.Client, "instances", {"a": tab}), \
                patch.object(app_control.ui, "notify") as toast:
            app_control._notice(scope=lifecycle.VPINFE, action=lifecycle.RESTART)

        toast.assert_not_called()
