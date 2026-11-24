"""What is waiting: a comparison against what the run started on and what the windows
opened on, so a value changed back is not waiting any more."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from common import events, extensions
from common.config.config_store import ConfigStore
from common.extensions import host as extension_host
from common.online import theme_service
from common.runtime import lifecycle
from common.settings import config_service, port_checks, settings_waiting

PORT = "network.http_port"
ROTATION = "windows.playfield.rotation"


def _records(*records: extension_host.Record) -> mock._patch:
    return mock.patch.object(extensions, "records", return_value=list(records))


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.windows: frozenset[str] = frozenset({"playfield", "backglass"})
        settings_waiting.forget()
        self.addCleanup(settings_waiting.forget)
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = ConfigStore(str(Path(folder.name) / "vpinfe.ini"))
        patch = mock.patch.object(settings_waiting, "get_ini_config", lambda: self.store)
        patch.start()
        self.addCleanup(patch.stop)
        settings_waiting.install(lambda store: (), lambda store: self.windows)
        settings_waiting.record_start(self.store)

    def _set(self, section: str, key: str, value: object) -> None:
        self.store.set_value(section, key, value)
        self.store.save()

    def _keys(self) -> dict[str, str]:
        return {f"{entry['section']}.{entry['key']}": entry["waits_for"]
                for entry in settings_waiting.answer()["waiting"]}


class WaitingTests(_Base):
    def test_nothing_waits_on_a_start(self) -> None:
        self.assertEqual(settings_waiting.answer()["waiting"], [])

    def test_a_changed_port_waits_for_a_restart_and_clears_when_changed_back(self) -> None:
        self._set("network", "http_port", 9001)

        [entry] = settings_waiting.answer()["waiting"]
        self.assertEqual(
            (entry["kind"], entry["waits_for"], entry["in_effect"], entry["set"]),
            ("setting", "restart", 8001, 9001))
        self.assertEqual(settings_waiting.answer()["http_port"], 9001)

        self._set("network", "http_port", 8001)

        self.assertEqual(settings_waiting.answer()["waiting"], [])

    def test_a_setting_that_applies_at_once_never_waits(self) -> None:
        self._set("behavior", "paging_size", 25)

        self.assertEqual(self._keys(), {})

    def test_a_window_setting_waits_for_a_reopen_and_clears_after_one(self) -> None:
        settings_waiting.windows_opened(self.store)
        self._set("windows.playfield", "rotation", "90")

        self.assertEqual(self._keys(), {ROTATION: "reopen"})

        settings_waiting.windows_opened(self.store)

        self.assertEqual(self._keys(), {})

    def test_a_window_setting_does_not_wait_while_no_windows_are_open(self) -> None:
        self._set("windows.playfield", "rotation", "90")
        self.assertEqual(self._keys(), {})

        settings_waiting.windows_opened(self.store)
        self._set("windows.playfield", "rotation", "180")
        settings_waiting.windows_closed()

        self.assertEqual(self._keys(), {})

    def test_a_window_setting_waits_for_a_restart_once_the_window_set_has_moved(self) -> None:
        settings_waiting.windows_opened(self.store)
        self._set("windows.playfield", "rotation", "90")
        self.windows = self.windows | {"topper"}

        self.assertEqual(self._keys(), {ROTATION: "restart"})

        self.windows = frozenset({"playfield", "backglass"})

        self.assertEqual(self._keys(), {ROTATION: "reopen"})

    def test_a_reopen_does_not_clear_a_change_that_needs_a_restart(self) -> None:
        settings_waiting.windows_opened(self.store)
        self._set("windows.playfield", "rotation", "90")
        self.windows = self.windows | {"topper"}

        settings_waiting.windows_opened(self.store)

        self.assertEqual(self._keys(), {ROTATION: "restart"})

    def test_an_extension_switched_on_since_the_start_waits(self) -> None:
        waiting = extension_host.Record(
            name="vpinplay", directory=Path("."), enabled=True, state=extensions.OFF,
            why=extension_host.STARTS_AT_RESTART)
        off = extension_host.Record(
            name="other", directory=Path("."), enabled=False, state=extensions.OFF,
            why=extension_host.SWITCHED_OFF)
        with _records(waiting, off):
            [entry] = settings_waiting.answer()["waiting"]

        self.assertEqual(
            (entry["kind"], entry["key"], entry["waits_for"], entry["in_effect"], entry["set"]),
            ("extension", "vpinplay", "restart", False, True))

    def test_a_reopen_is_offered_only_where_something_performs_it(self) -> None:
        for performable in (True, False):
            with self.subTest(performable=performable), \
                    mock.patch.object(lifecycle, "performable", return_value=performable):
                self.assertIs(settings_waiting.answer()["reopen"], performable)

    def test_a_reopen_is_not_offered_where_it_cannot_run(self) -> None:
        with mock.patch.object(lifecycle, "performable", return_value=True), \
                mock.patch.object(lifecycle, "unavailable",
                                  return_value="error.actions.frontend_off"):
            self.assertIs(settings_waiting.answer()["reopen"], False)


class ActiveThemeTests(_Base):
    def test_making_a_theme_active_joins_what_is_waiting(self) -> None:
        with mock.patch.object(config_service, "get_ini_config", lambda: self.store), \
                mock.patch.object(port_checks, "get_ini_config", lambda: self.store):
            theme_service.set_active_theme("Another")

        self.assertEqual(self._keys(), {"themes.active": "restart"})


class WaitingEventTests(_Base):
    def setUp(self) -> None:
        super().setUp()
        self.heard: list[dict] = []
        handler = events.subscribe(events.CONFIG_WAITING_CHANGED,
                                   lambda **body: self.heard.append(body))
        self.addCleanup(events.unsubscribe, events.CONFIG_WAITING_CHANGED, handler)

    def test_a_save_that_changes_the_answer_says_so_with_the_whole_answer(self) -> None:
        self._set("network", "http_port", 9001)
        events.emit(events.SETTINGS_CHANGED, keys=[PORT], ini_config=self.store)

        [body] = self.heard
        self.assertEqual(body, settings_waiting.answer())
        self.assertEqual([entry["key"] for entry in body["waiting"]], ["http_port"])

    def test_nothing_is_said_when_the_answer_did_not_move(self) -> None:
        events.emit(events.SETTINGS_CHANGED, keys=["behavior.paging_size"],
                    ini_config=self.store)
        events.emit(events.SETTINGS_CHANGED, keys=["behavior.paging_size"],
                    ini_config=self.store)

        self.assertEqual(self.heard, [])

    def test_changing_a_value_back_says_so(self) -> None:
        self._set("network", "http_port", 9001)
        events.emit(events.SETTINGS_CHANGED, keys=[PORT], ini_config=self.store)
        self._set("network", "http_port", 8001)
        events.emit(events.SETTINGS_CHANGED, keys=[PORT], ini_config=self.store)

        self.assertEqual([len(body["waiting"]) for body in self.heard], [1, 0])

    def test_switching_an_extension_on_says_so(self) -> None:
        waiting = extension_host.Record(
            name="vpinplay", directory=Path("."), enabled=True, state=extensions.OFF,
            why=extension_host.STARTS_AT_RESTART)
        with _records(waiting):
            events.emit(events.EXTENSION_SWITCHED, extension="vpinplay", enabled=True)

        [body] = self.heard
        self.assertEqual([entry["key"] for entry in body["waiting"]], ["vpinplay"])


if __name__ == "__main__":
    unittest.main()
