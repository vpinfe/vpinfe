"""Protects: (e) the Linux hand-over reads windows and the keyboard holder the way X11, sway and
Hyprland answer them, and says plainly where a desktop has no way to do it.

The display and the compositor are fakes; no window is touched.
"""

from __future__ import annotations

import types
import unittest
from unittest import mock

from common.capture.adapters import wlr
from common.host import front_gnome, front_kde, front_linux
from common.host.front import Window

SWAY = {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/run/user/1000/sway-ipc.sock",
        "XDG_CURRENT_DESKTOP": "sway"}
HYPRLAND = {"WAYLAND_DISPLAY": "wayland-1", "HYPRLAND_INSTANCE_SIGNATURE": "abc123",
            "XDG_CURRENT_DESKTOP": "Hyprland"}
GNOME = {"WAYLAND_DISPLAY": "wayland-0", "XDG_SESSION_TYPE": "wayland",
         "XDG_CURRENT_DESKTOP": "GNOME"}
X11 = {"DISPLAY": ":0", "XDG_SESSION_TYPE": "x11", "XDG_CURRENT_DESKTOP": "XFCE"}


def _view(number: int, pid: int | None, title: str | None, **more) -> dict:
    return {"type": "con", "id": number, "pid": pid, "name": title, "nodes": [],
            "floating_nodes": [], **more}


class WlrViewTests(unittest.TestCase):
    def test_sway_gives_each_view_its_id_program_and_title(self) -> None:
        tree = {"type": "root", "nodes": [{"type": "output", "name": "DP-1", "nodes": [
            {"type": "workspace", "name": "1", "floating_nodes": [], "nodes": [
                _view(11, 4001, "Visual Pinball Player"), _view(12, None, "Unknown"),
                _view(13, 4002, None)]}]}]}

        self.assertEqual(wlr.sway_pid_views(tree), [("11", 4001, "Visual Pinball Player"),
                                                    ("13", 4002, "")])

    def test_hyprland_gives_each_shown_client_its_address_program_and_title(self) -> None:
        clients = [
            {"address": "0xa", "pid": 4001, "title": "Visual Pinball Player", "mapped": True,
             "hidden": False},
            {"address": "0xb", "pid": 4002, "title": "Not shown", "mapped": False},
            {"address": "0xc", "pid": 4003, "title": "Hidden", "mapped": True, "hidden": True},
            {"address": "0xd", "title": "No program", "mapped": True}]

        self.assertEqual(wlr.hyprland_pid_views(clients), [("0xa", 4001,
                                                            "Visual Pinball Player")])

    def test_anything_else_the_compositor_says_is_no_windows(self) -> None:
        self.assertEqual(wlr.sway_pid_views([]), [])
        self.assertEqual(wlr.hyprland_pid_views({"error": "no"}), [])


class _Adapter:
    def __init__(self, views: list[tuple[str, int, str]], focused: str | None,
                 refuses: str | None = None) -> None:
        self._views = views
        self._focused = focused
        self._refuses = refuses
        self.focused_on: list[str] = []

    def pid_views(self) -> list[tuple[str, int, str]]:
        return self._views

    def focused_view(self) -> str | None:
        return self._focused

    def focus_view(self, view: str) -> None:
        if self._refuses is not None:
            raise OSError(self._refuses)
        self.focused_on.append(view)


class WlrBackendTests(unittest.TestCase):
    VIEWS = [("11", 4001, "Player"), ("12", 4002, "Table")]

    def test_windows_are_the_compositors_views(self) -> None:
        backend = front_linux.WlrBackend(_Adapter(self.VIEWS, None))

        self.assertEqual(backend.windows(), [Window("11", 4001, "Player"),
                                             Window("12", 4002, "Table")])

    def test_the_holder_is_the_view_with_focus(self) -> None:
        backend = front_linux.WlrBackend(_Adapter(self.VIEWS, "12"))

        self.assertEqual(backend.holder(), Window("12", 4002, "Table"))

    def test_nothing_focused_or_a_view_not_listed_is_nobody(self) -> None:
        for focused in (None, "99"):
            with self.subTest(focused=focused):
                self.assertIsNone(front_linux.WlrBackend(_Adapter(self.VIEWS, focused)).holder())

    def test_a_hand_over_focuses_the_view_by_its_handle(self) -> None:
        adapter = _Adapter(self.VIEWS, None)

        said = front_linux.WlrBackend(adapter).hand_over(Window("11", 4001, "Player"))

        self.assertEqual((said, adapter.focused_on), ("", ["11"]))

    def test_a_refusal_comes_back_as_words(self) -> None:
        backend = front_linux.WlrBackend(_Adapter(self.VIEWS, None, refuses="no such window"))

        self.assertIn("no such window", backend.hand_over(Window("11", 4001, "Player")))


class SelectionTests(unittest.TestCase):
    def test_sway_and_hyprland_get_the_compositor_backend(self) -> None:
        for env in (SWAY, HYPRLAND):
            with self.subTest(desktop=env["XDG_CURRENT_DESKTOP"]):
                self.assertIsInstance(front_linux.backend_for(env), front_linux.WlrBackend)

    def test_gnome_on_wayland_has_no_backend_and_says_why(self) -> None:
        with mock.patch.object(front_gnome, "backend", return_value=None), \
                mock.patch.object(front_gnome, "status", return_value=front_gnome.MISSING):
            self.assertIsNone(front_linux.backend_for(GNOME))
            said = front_linux.why_not(GNOME)

        self.assertIn("GNOME", said)
        self.assertIn("Settings", said)

    def test_kde_on_wayland_gets_the_kwin_backend(self) -> None:
        env = {**GNOME, "XDG_CURRENT_DESKTOP": "KDE"}

        with mock.patch.object(front_kde, "KWinBus", return_value=object()):
            self.assertIsInstance(front_linux.backend_for(env), front_kde.KWinBackend)

    def test_kde_whose_bus_cannot_be_reached_says_that_not_the_gnome_reason(self) -> None:
        env = {**GNOME, "XDG_CURRENT_DESKTOP": "KDE"}

        said = front_linux.why_not(env)

        self.assertIn("KWin", said)
        self.assertNotIn("lets no program", said)

    def test_a_wlroots_desktop_missing_its_socket_says_that(self) -> None:
        env = {**GNOME, "XDG_CURRENT_DESKTOP": "sway"}

        self.assertIn("cannot reach it", front_linux.why_not(env))

    def test_no_display_at_all_has_no_backend(self) -> None:
        self.assertIsNone(front_linux.backend_for({}))
        self.assertIn("no display", front_linux.why_not({}))

    def test_an_x11_display_that_cannot_be_asked_says_so(self) -> None:
        self.assertIn("could not be asked", front_linux.why_not(X11))


class _GoneError(Exception):
    pass


class _Window:
    def __init__(self, display: _Display, number: int) -> None:
        self._display = display
        self._number = number

    def get_full_property(self, atom: int, _kind: int):
        if self._number in self._display.gone:
            raise _GoneError()
        found = self._display.properties.get((self._number, self._display.names[atom]))
        return None if found is None else types.SimpleNamespace(value=found)


class _Root(_Window):
    def __init__(self, display: _Display) -> None:
        super().__init__(display, 0)
        self.sent: list[tuple[object, int]] = []

    def send_event(self, event: object, event_mask: int) -> None:
        self.sent.append((event, event_mask))


class _Display:
    def __init__(self, properties: dict[tuple[int, str], object]) -> None:
        self.properties = properties
        self.names: dict[int, str] = {}
        self.gone: set[int] = set()
        self.interned = 0
        self.synced = False
        self.closed = False
        self.root = _Root(self)

    def intern_atom(self, name: str) -> int:
        self.interned += 1
        for number, known in self.names.items():
            if known == name:
                return number
        number = len(self.names) + 1
        self.names[number] = name
        return number

    def close(self) -> None:
        self.closed = True

    def screen(self) -> types.SimpleNamespace:
        return types.SimpleNamespace(root=self.root)

    def create_resource_object(self, _kind: str, number: int) -> _Window:
        return _Window(self, number)

    def sync(self) -> None:
        self.synced = True


def _xlib() -> types.SimpleNamespace:
    def client_message(**fields):
        return types.SimpleNamespace(**fields)

    return types.SimpleNamespace(
        X=types.SimpleNamespace(AnyPropertyType=0, CurrentTime=0, SubstructureRedirectMask=1,
                                SubstructureNotifyMask=2),
        event=types.SimpleNamespace(ClientMessage=client_message),
        error=types.SimpleNamespace(BadWindow=_GoneError))


class X11BackendTests(unittest.TestCase):
    PROPERTIES: dict[tuple[int, str], object] = {
        (0, "_NET_CLIENT_LIST"): [101, 102],
        (0, "_NET_ACTIVE_WINDOW"): [102],
        (101, "_NET_WM_PID"): [4001], (101, "_NET_WM_NAME"): b"Visual Pinball Player",
        (102, "_NET_WM_PID"): [4002], (102, "WM_NAME"): "Table",
    }

    def _backend(self, properties: dict[tuple[int, str], object] | None = None):
        display = _Display(self.PROPERTIES if properties is None else properties)
        return front_linux.X11Backend(display, _xlib()), display

    def test_windows_are_the_window_managers_client_list_with_program_and_title(self) -> None:
        backend, _ = self._backend()

        self.assertEqual(backend.windows(), [Window(101, 4001, "Visual Pinball Player"),
                                             Window(102, 4002, "Table")])

    def test_the_holder_is_the_active_window(self) -> None:
        backend, _ = self._backend()

        self.assertEqual(backend.holder(), Window(102, 4002, "Table"))

    def test_no_active_window_is_nobody(self) -> None:
        for active in (None, [0]):
            with self.subTest(active=active):
                backend, _ = self._backend({**self.PROPERTIES, (0, "_NET_ACTIVE_WINDOW"): active})
                self.assertIsNone(backend.holder())

    def test_the_desktop_and_a_panel_never_hold_the_keyboard(self) -> None:
        for kind in ("_NET_WM_WINDOW_TYPE_DESKTOP", "_NET_WM_WINDOW_TYPE_DOCK"):
            with self.subTest(kind=kind):
                display = _Display({})
                backend = front_linux.X11Backend(display, _xlib())
                display.properties.update(self.PROPERTIES)
                display.properties[(102, "_NET_WM_WINDOW_TYPE")] = [display.intern_atom(kind)]

                self.assertIsNone(backend.holder())

    def test_a_normal_window_type_still_holds_the_keyboard(self) -> None:
        display = _Display({})
        backend = front_linux.X11Backend(display, _xlib())
        display.properties.update(self.PROPERTIES)
        display.properties[(102, "_NET_WM_WINDOW_TYPE")] = [
            display.intern_atom("_NET_WM_WINDOW_TYPE_NORMAL")]

        self.assertEqual(backend.holder(), Window(102, 4002, "Table"))

    def test_a_window_without_a_program_has_pid_zero(self) -> None:
        backend, _ = self._backend({(0, "_NET_CLIENT_LIST"): [101]})

        self.assertEqual(backend.windows(), [Window(101, 0, "")])

    def test_a_window_that_closes_while_being_read_is_left_out(self) -> None:
        backend, display = self._backend()
        display.gone.add(101)

        self.assertEqual(backend.windows(), [Window(102, 4002, "Table")])
        display.gone.add(102)
        self.assertIsNone(backend.holder())

    def test_each_atom_is_asked_for_once(self) -> None:
        backend, display = self._backend()

        backend.windows()
        asked = display.interned
        backend.windows()

        self.assertEqual(display.interned, asked)

    def test_close_closes_the_connection(self) -> None:
        backend, display = self._backend()

        backend.close()

        self.assertTrue(display.closed)

    def test_a_hand_over_asks_the_way_a_taskbar_does(self) -> None:
        backend, display = self._backend()

        said = backend.hand_over(Window(101, 4001, "Visual Pinball Player"))

        (event, mask), = display.root.sent
        self.assertEqual(said, "")
        self.assertEqual(mask, 1 | 2)
        self.assertEqual(display.names[event.client_type], "_NET_ACTIVE_WINDOW")
        self.assertEqual(event.data, (32, [2, 0, 0, 0, 0]))
        self.assertTrue(display.synced)


if __name__ == "__main__":
    unittest.main()
