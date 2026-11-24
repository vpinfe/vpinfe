"""The offer on a beta's last page: when it is made, what the switch shows, and that it and
Settings are one setting."""

from __future__ import annotations

import asyncio
import configparser
import unittest
from collections.abc import Callable
from unittest import mock

from nicegui import ui

from common.config.config_access import cfg_get, cfg_set
from common.release import migration_ledger, release_notes, upgrade_needs, whats_new
from manager.setup import setup
from manager.system import early_access
from manager.system import whats_new as page_whats_new
from tests.support.library import TempTree

_PAGE = ui.element()
_BETA = "v3.0.0-beta.2"


def _drawn(draw: Callable[[], None]) -> tuple[list[str], list[ui.switch]]:
    with _PAGE:
        holder = ui.element("div")
        with holder:
            draw()
    labels = [one.text for one in holder.descendants() if isinstance(one, ui.label)]
    switches = [one for one in holder.descendants() if isinstance(one, ui.switch)]
    return labels, switches


class _Install:
    """A config in memory, and the API call that writes it."""

    def __init__(self, channel: str | None = None) -> None:
        self.config = configparser.ConfigParser()
        if channel is not None:
            cfg_set(self.config, *early_access.app_updater.CHANNEL_SETTING, channel)
        self.writes: list[dict] = []

    def put_config(self, changes: dict) -> dict:
        self.writes.append(changes)
        for section, entries in changes.items():
            for key, value in entries.items():
                cfg_set(self.config, section, key, value)
        return changes

    def channel(self) -> str:
        return cfg_get(self.config, *early_access.app_updater.CHANNEL_SETTING, "stable")

    def __enter__(self) -> _Install:
        client = mock.Mock()
        client.put_config.side_effect = self.put_config
        self._patches = [
            mock.patch.object(early_access.app_updater, "get_ini_config",
                              return_value=self.config),
            mock.patch.object(early_access, "ApiClient", return_value=client),
        ]
        for patch in self._patches:
            patch.start()
        return self

    def __exit__(self, *_: object) -> None:
        for patch in self._patches:
            patch.stop()


def _offer(version: str, **kwargs: bool) -> Callable[[], None] | None:
    return asyncio.run(early_access.offer(version, **kwargs))


class OfferTests(TempTree):
    def test_a_release_that_is_not_a_beta_is_not_offered(self) -> None:
        with _Install() as install:
            self.assertIsNone(_offer("v3.0.0"))
        self.assertEqual(install.writes, [])

    def test_a_page_asked_for_again_is_not_offered(self) -> None:
        with _Install() as install:
            self.assertIsNone(_offer(_BETA, again=True))
        self.assertEqual(install.writes, [])

    def test_a_setting_that_cannot_be_read_leaves_the_page_without_the_offer(self) -> None:
        with _Install(), mock.patch.object(early_access, "_read", side_effect=OSError("gone")):
            self.assertIsNone(_offer(_BETA))

    def test_making_the_offer_writes_nothing_until_it_is_drawn(self) -> None:
        with _Install("stable") as install:
            self.assertIsNotNone(_offer(_BETA))
        self.assertEqual(install.writes, [])
        self.assertEqual(install.channel(), "stable")

    def test_the_first_offer_shows_on_and_sets_early_access_over_the_stored_default(self) -> None:
        with _Install("stable") as install:
            state = early_access._read()
            self.assertEqual((state.on, state.first), (True, True))
            early_access._first()

            after = early_access._read()
        self.assertEqual(install.channel(), "beta")
        self.assertEqual((after.on, after.first), (True, False))

    def test_a_channel_turned_off_stays_off_at_the_next_offer(self) -> None:
        with _Install():
            early_access._first()
            early_access._set(False)
            state = early_access._read()
        self.assertEqual((state.on, state.first), (False, False))

    def test_the_switch_writes_the_channel_settings_edits(self) -> None:
        with _Install() as install:
            early_access._set(False)
            self.assertEqual(install.channel(), "stable")
            early_access._set(True)
            self.assertEqual(install.channel(), "beta")

    def test_the_first_write_is_scheduled_once_however_often_the_page_is_drawn(self) -> None:
        with _Install(), mock.patch.object(early_access.background_tasks, "create") as create:
            create.side_effect = lambda coroutine, **_: coroutine.close()
            draw = early_access._draw(early_access._Offer(on=True, first=True))
            _drawn(draw)
            _drawn(draw)
        create.assert_called_once()

    def test_a_redraw_shows_what_was_last_set(self) -> None:
        state = early_access._Offer(on=True, first=False)
        with _Install():
            draw = early_access._draw(state)
            labels, (switch,) = _drawn(draw)
            self.assertTrue(switch.value)
            self.assertIn("You're on a beta release", labels)

            state.on = False
            _, (again,) = _drawn(draw)
        self.assertFalse(again.value)



class SetupTests(TempTree):
    def ask(self, key: str, offer: Callable[[], None] | None) -> dict:
        async def ask(session: setup.Session) -> dict:
            return {"title": "A step"}

        step = setup.Step(key, "manager.setup.step.tables", ask, mock.AsyncMock())
        session = setup.Session(again=False, offer=offer)
        with mock.patch.object(setup, "STEPS", (step,)):
            return asyncio.run(setup._ask(session, step))

    def test_the_tables_step_offers_it_after_its_own_content(self) -> None:
        offer = mock.Mock()

        found = self.ask(setup.TABLES, offer)
        found["after"]({})

        offer.assert_called_once_with()

    def test_no_other_step_offers_it(self) -> None:
        self.assertNotIn("after", self.ask(setup.CONTROLS, mock.Mock()))

    def test_setup_with_nothing_to_offer_asks_for_nothing_after(self) -> None:
        self.assertNotIn("after", self.ask(setup.TABLES, None))


class LastPageTests(unittest.TestCase):
    def test_whats_new_offers_it_on_its_last_part_only(self) -> None:
        section = release_notes.Section("Summary", [release_notes.Entry("Faster scans.")])
        found = whats_new.Review(
            since="v3.0.0",
            releases=[release_notes.Release("v3.1.0", [section])],
            files=[migration_ledger.Entry("info_upgrade", "v3.1.0", 2)],
            needs=[upgrade_needs.Need("pick", "v3.1.0", lambda config: False)])
        offer = mock.Mock()
        calls = page_whats_new.calls(found, page_whats_new.parts(found), offer)

        steps = [asyncio.run(calls.first(key)) for key in ("changed", "files", "needs")]

        self.assertEqual(["after" in step for step in steps], [False, False, True])
        steps[-1]["after"]({})
        offer.assert_called_once_with()

    def test_whats_new_without_an_offer_asks_for_nothing_after(self) -> None:
        section = release_notes.Section("Summary", [release_notes.Entry("Faster scans.")])
        found = whats_new.Review(since="v3.0.0",
                                 releases=[release_notes.Release("v3.1.0", [section])])

        step = asyncio.run(page_whats_new.calls(found, page_whats_new.parts(found)).first(None))

        self.assertNotIn("after", step)


if __name__ == "__main__":
    unittest.main()
