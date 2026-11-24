"""Protects: (b) input actions reach an extension only behind a scope the owner sees.

An extension hears the named actions people press (Take Picture, Back, select) only when its
manifest declares `input:read`; without it the subscription is refused when it is made.
"""

from __future__ import annotations

import unittest

import httpapi.scopes as scopes
from common import events
from common.extensions import context
from common.host import input_actions


def _events(declared_scopes: tuple[str, ...]) -> context.ExtensionEvents:
    return context.ExtensionEvents("sample", (), lambda *a, **k: None, scopes=declared_scopes)


class InputScopeTests(unittest.TestCase):
    def setUp(self) -> None:
        events.clear()
        input_actions.release_all()
        self.addCleanup(events.clear)
        self.addCleanup(input_actions.release_all)

    def test_the_scope_core_checks_is_the_scope_the_owner_is_shown(self) -> None:
        self.assertEqual(context.SCOPED_EVENTS[events.INPUT_ACTION], scopes.INPUT_READ)
        self.assertTrue(scopes.is_known(scopes.INPUT_READ))

    def test_without_the_scope_the_subscription_is_refused(self) -> None:
        with self.assertRaises(context.ContractError) as raised:
            _events(("games:read",)).subscribe(events.INPUT_ACTION, lambda **_: None)

        self.assertIn(scopes.INPUT_READ, str(raised.exception))

    def test_with_the_scope_the_extension_hears_each_press(self) -> None:
        heard: list[dict] = []
        _events((scopes.INPUT_READ,)).subscribe(
            events.INPUT_ACTION, lambda **payload: heard.append(payload))

        input_actions.tap("take_picture", source="remote")

        self.assertEqual([(one["action"], one["phase"]) for one in heard],
                         [("take_picture", "press"), ("take_picture", "release")])

    def test_an_event_that_needs_no_scope_is_still_open_to_every_extension(self) -> None:
        _events(()).subscribe(events.TABLE_PICTURE_TAKEN, lambda **_: None)
