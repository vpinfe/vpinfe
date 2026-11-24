"""A page whose tab has gone stops its polling timers, and resumes them if the tab returns."""

from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from nicegui import background_tasks, core, ui

from manager.kit import ghost_timers

_PAGE = ui.element()


class APageWhoseTabHasGone(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(patch.object(core, "loop", asyncio.get_event_loop()))
        self.enterContext(patch.object(background_tasks, "create_or_defer"))
        self.client = _PAGE.client
        with _PAGE:
            self.poll = ui.timer(3.0, lambda: None)
            self.opening = ui.timer(0.1, lambda: None, once=True)
            self.off = ui.timer(3.0, lambda: None, active=False)

    def tearDown(self) -> None:
        ghost_timers._paused.clear()

    def test_pauses_a_poll_and_leaves_the_rest(self) -> None:
        ghost_timers._pause(self.client)
        self.assertFalse(self.poll.active)
        self.assertTrue(self.opening.active)
        self.assertFalse(self.off.active)

    def test_takes_up_only_the_poll_it_paused(self) -> None:
        ghost_timers._pause(self.client)
        ghost_timers._resume(self.client)
        self.assertTrue(self.poll.active)
        self.assertFalse(self.off.active)

    def test_a_poll_deleted_meanwhile_is_not_woken(self) -> None:
        ghost_timers._pause(self.client)
        self.poll.cancel()
        ghost_timers._resume(self.client)
        self.assertFalse(self.poll.active)

    def test_a_timer_at_the_limit_is_left_running(self) -> None:
        with _PAGE:
            edge = ui.timer(ghost_timers.MIN_POLL_S, lambda: None, once=True)
        ghost_timers._pause(self.client)
        self.assertTrue(edge.active)

    def test_a_page_with_a_live_tab_is_left_alone(self) -> None:
        with patch.object(type(self.client), "has_socket_connection", True):
            ghost_timers._pause(self.client)
        self.assertTrue(self.poll.active)
        self.assertEqual(ghost_timers._paused, {})

    def test_install_hooks_disconnect_connect_and_delete(self) -> None:
        with patch.object(ghost_timers.app, "on_disconnect") as disconnect, \
                patch.object(ghost_timers.app, "on_connect") as connect, \
                patch.object(ghost_timers.app, "on_delete") as delete:
            ghost_timers.install()
        disconnect.assert_called_once_with(ghost_timers._pause)
        connect.assert_called_once_with(ghost_timers._resume)
        delete.assert_called_once_with(ghost_timers._forget)

    def test_forgets_a_deleted_page(self) -> None:
        ghost_timers._pause(self.client)
        ghost_timers._forget(self.client)
        self.assertEqual(ghost_timers._paused, {})
