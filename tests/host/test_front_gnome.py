"""Protects: (e) GNOME on Wayland is told apart from every other desktop, the state of the
add-on that lets VPinFE bring the table forward is read from GNOME's own settings, and
allowing it changes only what it must in the person's own folder and settings.

GNOME's programs and the bus are fakes; nothing of the real desktop is read or changed.
"""

from __future__ import annotations

import ast
import json
import tempfile
import unittest
from pathlib import Path

from common.host import front_gnome
from common.host.front import Window

GNOME = {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}


class _Desktop:
    """GNOME's settings and extension list, as `gsettings`, `gnome-shell` and
    `gnome-extensions` would answer."""

    def __init__(self, enabled: list[str] | None = None, all_off: bool = False,
                 major: int = 50, state: str | None = None) -> None:
        self.enabled = list(enabled or [])
        self.all_off = all_off
        self.major = major
        self.state = state
        self.refuses = False

    def __call__(self, argv):
        if argv[0] == "gnome-shell":
            return 0, f"GNOME Shell {self.major}.1\n"
        if argv[0] == "gnome-extensions":
            if self.state is None:
                return 2, ""
            return 0, f"{front_gnome.UUID}\n  Enabled: Yes\n  State: {self.state}\n"
        _, verb, _schema, key, *value = argv
        if verb == "get":
            if key == "enabled-extensions":
                return 0, "@as []\n" if not self.enabled else repr(self.enabled) + "\n"
            return 0, "true\n" if self.all_off else "false\n"
        if self.refuses:
            return 1, ""
        if key == "enabled-extensions":
            self.enabled = ast.literal_eval(value[0])
        else:
            self.all_off = value[0] == "true"
        return 0, ""


def _installed(root: Path) -> None:
    folder = root / "gnome-shell" / "extensions" / front_gnome.UUID
    folder.mkdir(parents=True)
    for name in ("metadata.json", "extension.js"):
        (folder / name).write_text("{}", encoding="utf-8")


class StatusTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {**GNOME, "XDG_DATA_HOME": self._home.name}

    def _status(self, desktop: _Desktop, owned: bool = False, env=None) -> str:
        return front_gnome.status(env or self.env, desktop, lambda _env: owned)

    def test_other_desktops_are_not_asked_about(self) -> None:
        for env in ({"XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "GNOME"},
                    {"XDG_SESSION_TYPE": "wayland", "XDG_CURRENT_DESKTOP": "KDE"}):
            self.assertEqual(self._status(_Desktop(), env=env), front_gnome.NOT_HERE)

    def test_an_add_on_that_answers_on_the_bus_is_working(self) -> None:
        self.assertEqual(self._status(_Desktop(), owned=True), front_gnome.WORKING)

    def test_a_release_the_add_on_does_not_list_is_unsupported(self) -> None:
        self.assertEqual(self._status(_Desktop(major=3)), front_gnome.UNSUPPORTED)

    def test_nothing_in_the_folder_is_missing(self) -> None:
        self.assertEqual(self._status(_Desktop()), front_gnome.MISSING)

    def test_in_the_folder_and_not_switched_on_is_off(self) -> None:
        _installed(Path(self._home.name))

        self.assertEqual(self._status(_Desktop()), front_gnome.OFF)

    def test_gnomes_own_switch_for_every_add_on_counts_as_off(self) -> None:
        _installed(Path(self._home.name))

        self.assertEqual(self._status(_Desktop([front_gnome.UUID], all_off=True)),
                         front_gnome.OFF)

    def test_switched_on_and_not_yet_known_to_gnome_waits_for_a_sign_in(self) -> None:
        _installed(Path(self._home.name))

        self.assertEqual(self._status(_Desktop([front_gnome.UUID])), front_gnome.SIGN_IN)

    def test_gnome_reporting_an_error_is_broken(self) -> None:
        _installed(Path(self._home.name))

        self.assertEqual(self._status(_Desktop([front_gnome.UUID], state="ERROR")),
                         front_gnome.BROKEN)

    def test_a_list_that_cannot_be_read_is_broken(self) -> None:
        _installed(Path(self._home.name))

        def unreadable(argv):
            return (0, "GNOME Shell 50.1\n") if argv[0] == "gnome-shell" else (1, "")

        self.assertEqual(front_gnome.status(self.env, unreadable, lambda _env: False),
                         front_gnome.BROKEN)


class AllowTests(unittest.TestCase):
    def setUp(self) -> None:
        self._home = tempfile.TemporaryDirectory()
        self.addCleanup(self._home.cleanup)
        self.env = {**GNOME, "XDG_DATA_HOME": self._home.name}

    def test_it_copies_into_the_persons_folder_and_keeps_what_was_already_on(self) -> None:
        desktop = _Desktop(["dash-to-dock@example"])

        state = front_gnome.allow(self.env, desktop)

        self.assertEqual(desktop.enabled, ["dash-to-dock@example", front_gnome.UUID])
        folder = Path(self._home.name) / "gnome-shell" / "extensions" / front_gnome.UUID
        self.assertTrue((folder / "extension.js").is_file())
        self.assertEqual(state, front_gnome.SIGN_IN)

    def test_it_is_not_listed_twice(self) -> None:
        desktop = _Desktop([front_gnome.UUID])

        front_gnome.allow(self.env, desktop)

        self.assertEqual(desktop.enabled, [front_gnome.UUID])

    def test_it_turns_user_add_ons_back_on_where_gnome_had_them_all_off(self) -> None:
        desktop = _Desktop(all_off=True)

        front_gnome.allow(self.env, desktop)

        self.assertFalse(desktop.all_off)

    def test_gnome_refusing_the_change_says_so(self) -> None:
        desktop = _Desktop()
        desktop.refuses = True

        with self.assertRaises(OSError):
            front_gnome.allow(self.env, desktop)

    def test_it_does_nothing_off_gnome(self) -> None:
        with self.assertRaises(OSError):
            front_gnome.allow({"XDG_SESSION_TYPE": "x11", "XDG_DATA_HOME": self._home.name},
                              _Desktop())
        self.assertEqual(list(Path(self._home.name).iterdir()), [])


class _Bus:
    def __init__(self, listed: object) -> None:
        self.listed = listed
        self.called: list[tuple] = []
        self.found = True

    def call(self, method, signature="", body=()):
        self.called.append((method, body))
        return [json.dumps(self.listed)] if method == "List" else [self.found]

    def close(self) -> None:
        pass


LISTED = {"active": "7", "windows": [{"id": 6, "pid": 4001, "title": "Player"},
                                      {"id": 7, "pid": 4002, "title": "Table"}]}


class BackendTests(unittest.TestCase):
    def test_windows_and_holder_come_from_one_list(self) -> None:
        backend = front_gnome.GnomeBackend(_Bus(LISTED))

        self.assertEqual(backend.windows(), [Window("6", 4001, "Player"),
                                             Window("7", 4002, "Table")])
        self.assertEqual(backend.holder(), Window("7", 4002, "Table"))

    def test_hand_over_asks_for_the_window_by_number(self) -> None:
        bus = _Bus(LISTED)

        self.assertEqual(front_gnome.GnomeBackend(bus).hand_over(Window("6", 4001, "Player")), "")
        self.assertEqual(bus.called[-1], ("Activate", (6,)))

    def test_a_window_gnome_no_longer_has_is_said_not_raised(self) -> None:
        bus = _Bus(LISTED)
        bus.found = False

        self.assertNotEqual(front_gnome.GnomeBackend(bus).hand_over(Window("9", 1, "x")), "")

    def test_an_answer_that_is_not_a_window_list_raises(self) -> None:
        with self.assertRaises(OSError):
            front_gnome.GnomeBackend(_Bus({"active": "1"})).windows()


if __name__ == "__main__":
    unittest.main()
