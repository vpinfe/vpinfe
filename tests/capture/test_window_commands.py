"""Telling a wlroots compositor where a window goes: finding a program's window, the one that
has focus, and sending a window to an output full screen."""

from __future__ import annotations

import json
import socket
import struct
import threading
import unittest
from types import SimpleNamespace
from typing import Any

from common.capture import adapters, geometry
from common.capture.adapters import wlr

SWAY = {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/run/user/1000/sway-ipc.sock"}
HYPRLAND = {"WAYLAND_DISPLAY": "wayland-1", "HYPRLAND_INSTANCE_SIGNATURE": "abc123",
            "XDG_RUNTIME_DIR": "/run/user/1000"}

GET_TREE = 4
RUN_COMMAND = 0


def _view(view_id: int, pid: int, *, focused: bool = False, **more: Any) -> dict[str, Any]:
    return {"type": "con", "id": view_id, "pid": pid, "focused": focused, "nodes": [],
            "floating_nodes": [], **more}


# A split holding the frontend's table window and a number window, a floating window, and a
# workspace with nothing on it.
SWAY_TREE = {"type": "root", "nodes": [
    {"type": "output", "name": "DP-1", "nodes": [
        {"type": "workspace", "nodes": [
            {"type": "con", "id": 2, "nodes": [_view(10, 500, focused=True), _view(11, 501)],
             "floating_nodes": []}],
         "floating_nodes": [_view(12, 502, type="floating_con")]}]},
    {"type": "output", "name": "HDMI-A-1", "nodes": [
        {"type": "workspace", "nodes": [], "floating_nodes": []}]},
]}

HYPRLAND_CLIENTS = [
    {"address": "0xa1", "pid": 600, "mapped": True, "hidden": False, "fullscreen": 0},
    {"address": "0xa2", "pid": 601, "mapped": False, "hidden": False, "fullscreen": 0},
    {"address": "0xa3", "pid": 602, "mapped": True, "hidden": True, "fullscreen": 0},
    {"address": "0xa4", "pid": 603, "mapped": True, "hidden": False, "fullscreen": 2},
    {"address": "0xa5", "pid": 604, "mapped": True, "hidden": False, "fullscreen": 1},
]
HYPRLAND_MONITORS = [{"name": "DP-1", "activeWorkspace": {"id": 1}},
                     {"name": "HDMI-A-1", "activeWorkspace": {"id": 3}}]


class FakeSway:
    """sway's IPC socket: remembers each message, and answers by its kind."""

    def __init__(self, run: Any = None) -> None:
        self.answers = {GET_TREE: SWAY_TREE,
                        RUN_COMMAND: [{"success": True}] if run is None else run}
        self.heard: list[tuple[int, bytes]] = []

    def connect(self, path: str) -> socket.socket:
        ours, theirs = socket.socketpair()
        threading.Thread(target=self._answer, args=(theirs,), daemon=True).start()
        return ours

    def _answer(self, sock: socket.socket) -> None:
        with sock:
            head = sock.recv(14)
            length, kind = struct.unpack("<II", head[6:])
            self.heard.append((kind, sock.recv(length) if length else b""))
            payload = json.dumps(self.answers[kind]).encode()
            sock.sendall(b"i3-ipc" + struct.pack("<II", len(payload), kind) + payload)

    def commands(self) -> list[str]:
        return [payload.decode() for kind, payload in self.heard if kind == RUN_COMMAND]


class FakeHyprland:
    """Hyprland's socket: one request to a connection, each remembered, answered by what the
    request starts with."""

    def __init__(self, dispatch: bytes = b"ok", **answers: Any) -> None:
        self.answers = {b"j/clients": HYPRLAND_CLIENTS, b"j/monitors": HYPRLAND_MONITORS,
                        b"j/activewindow": {"address": "0xa4"}}
        self.answers.update({b"j/" + kind.encode(): said for kind, said in answers.items()})
        self.dispatch = dispatch
        self.heard: list[bytes] = []

    def connect(self, path: str) -> socket.socket:
        ours, theirs = socket.socketpair()
        threading.Thread(target=self._answer, args=(theirs,), daemon=True).start()
        return ours

    def _answer(self, sock: socket.socket) -> None:
        with sock:
            asked = sock.recv(256)
            self.heard.append(asked)
            if asked.startswith(b"/dispatch "):
                sock.sendall(self.dispatch)
            else:
                sock.sendall(json.dumps(self.answers[asked]).encode())

    def dispatched(self) -> list[str]:
        return [asked.removeprefix(b"/dispatch ").decode() for asked in self.heard
                if asked.startswith(b"/dispatch ")]


def _output(name: str, x: int = 0, y: int = 0, width: int = 1920,
            height: int = 1080) -> adapters.Output:
    return adapters.Output(name, x, y, width, height, (width, height), 60.0,
                           geometry.sway_transform("normal"))


class FindingTests(unittest.TestCase):
    def test_sway_finds_the_view_a_program_has_wherever_it_sits_in_the_tree(self) -> None:
        for pid, view in ((500, "10"), (501, "11"), (502, "12")):
            with self.subTest(pid=pid):
                self.assertEqual(wlr.sway_view(SWAY_TREE, pid), view)

    def test_sway_has_no_view_for_a_program_whose_window_is_not_up_yet(self) -> None:
        self.assertIsNone(wlr.sway_view(SWAY_TREE, 999))
        self.assertIsNone(wlr.sway_view([], 500))

    def test_sway_says_which_view_has_focus(self) -> None:
        self.assertEqual(wlr.sway_focused(SWAY_TREE), "10")

    def test_sway_says_none_when_focus_is_on_an_empty_workspace(self) -> None:
        self.assertIsNone(wlr.sway_focused({"type": "root", "nodes": [
            {"type": "workspace", "nodes": [], "floating_nodes": []}]}))

    def test_hyprland_finds_the_client_a_program_has_mapped(self) -> None:
        found = wlr.hyprland_view(HYPRLAND_CLIENTS, 600)

        assert found is not None
        self.assertEqual(found["address"], "0xa1")

    def test_hyprland_has_no_client_for_one_not_mapped_or_hidden(self) -> None:
        for pid in (601, 602, 999):
            with self.subTest(pid=pid):
                self.assertIsNone(wlr.hyprland_view(HYPRLAND_CLIENTS, pid))

    def test_anything_else_hyprland_says_is_no_client(self) -> None:
        self.assertIsNone(wlr.hyprland_view({"error": "no"}, 600))

    def test_the_adapter_asks_sway_for_its_tree(self) -> None:
        sway = FakeSway()

        found = wlr.WlrAdapter(SWAY, sway.connect).view_of(501)

        self.assertEqual(found, "11")
        self.assertEqual(sway.heard, [(GET_TREE, b"")])

    def test_the_adapter_asks_hyprland_for_its_clients(self) -> None:
        hyprland = FakeHyprland()

        found = wlr.WlrAdapter(HYPRLAND, hyprland.connect).view_of(600)

        self.assertEqual(found, "0xa1")
        self.assertEqual(hyprland.heard, [b"j/clients"])

    def test_the_adapter_says_which_window_has_focus(self) -> None:
        self.assertEqual(wlr.WlrAdapter(SWAY, FakeSway().connect).focused_view(), "10")
        self.assertEqual(wlr.WlrAdapter(HYPRLAND, FakeHyprland().connect).focused_view(),
                         "0xa4")

    def test_hyprland_with_no_active_window_says_none(self) -> None:
        hyprland = FakeHyprland(activewindow={})

        self.assertIsNone(wlr.WlrAdapter(HYPRLAND, hyprland.connect).focused_view())


class FocusTests(unittest.TestCase):
    def test_sway_is_told_to_focus_the_view(self) -> None:
        sway = FakeSway()

        wlr.WlrAdapter(SWAY, sway.connect).focus_view("10")

        self.assertEqual(sway.commands(), ["[con_id=10] focus"])

    def test_hyprland_is_dispatched_to_focus_the_client(self) -> None:
        hyprland = FakeHyprland()

        wlr.WlrAdapter(HYPRLAND, hyprland.connect).focus_view("0xa1")

        self.assertEqual(hyprland.dispatched(), ["focuswindow address:0xa1"])

    def test_a_compositor_that_refuses_is_an_error(self) -> None:
        with self.assertRaises(OSError):
            wlr.WlrAdapter(SWAY, FakeSway(run=[{"success": False, "error": "no"}]
                                          ).connect).focus_view("10")
        with self.assertRaises(OSError):
            wlr.WlrAdapter(HYPRLAND, FakeHyprland(dispatch=b"Window not found").connect
                           ).focus_view("0xa1")


class FillTests(unittest.TestCase):
    def test_sway_sends_the_view_to_the_output_borderless_and_full_screen(self) -> None:
        sway = FakeSway()

        wlr.WlrAdapter(SWAY, sway.connect).fill_output("11", _output("HDMI-A-1"))

        self.assertEqual(sway.commands(), [
            '[con_id=11] move to output "HDMI-A-1", border none, fullscreen enable'])

    def test_an_output_name_cannot_end_the_command_early(self) -> None:
        sway = FakeSway()

        wlr.WlrAdapter(SWAY, sway.connect).fill_output("11", _output('A" ; exec x'))

        [command] = sway.commands()
        self.assertIn('move to output "A\\" ; exec x"', command)

    def test_sway_refusing_any_part_of_it_is_an_error(self) -> None:
        for said in ([{"success": True}, {"success": False, "error": "no such output"}],
                     [{"success": False, "error": "no such output"}], []):
            with self.subTest(said=said), self.assertRaises(OSError):
                wlr.WlrAdapter(SWAY, FakeSway(run=said).connect).fill_output(
                    "11", _output("DP-9"))

    def test_hyprland_moves_the_client_to_the_outputs_workspace_then_fills_it(self) -> None:
        hyprland = FakeHyprland()

        wlr.WlrAdapter(HYPRLAND, hyprland.connect).fill_output("0xa1", _output("HDMI-A-1"))

        self.assertEqual(hyprland.dispatched(), [
            "movetoworkspacesilent 3,address:0xa1", "focuswindow address:0xa1", "fullscreen 0"])

    def test_hyprland_leaves_a_client_that_is_already_full_screen_so(self) -> None:
        hyprland = FakeHyprland()

        wlr.WlrAdapter(HYPRLAND, hyprland.connect).fill_output("0xa4", _output("DP-1"))

        self.assertEqual(hyprland.dispatched(), ["movetoworkspacesilent 1,address:0xa4"])

    def test_hyprland_fills_a_client_that_is_only_maximized(self) -> None:
        hyprland = FakeHyprland()

        wlr.WlrAdapter(HYPRLAND, hyprland.connect).fill_output("0xa5", _output("HDMI-A-1"))

        self.assertEqual(hyprland.dispatched()[-1], "fullscreen 0")

    def test_hyprland_with_no_such_monitor_is_an_error_and_moves_nothing(self) -> None:
        hyprland = FakeHyprland()

        with self.assertRaises(OSError):
            wlr.WlrAdapter(HYPRLAND, hyprland.connect).fill_output("0xa1", _output("DP-9"))

        self.assertEqual(hyprland.dispatched(), [])

    def test_hyprland_answering_anything_but_ok_is_an_error(self) -> None:
        hyprland = FakeHyprland(dispatch=b"Invalid dispatcher")

        with self.assertRaises(OSError):
            wlr.WlrAdapter(HYPRLAND, hyprland.connect).fill_output("0xa1", _output("DP-1"))

        self.assertEqual(len(hyprland.dispatched()), 1)


class OutputOfTests(unittest.TestCase):
    outputs = [_output("DP-1", 0, 0, 1080, 1920), _output("DP-2", 1080, 0),
               _output("HDMI-A-1", 3000, 0)]

    @staticmethod
    def _monitor(x: int, y: int, width: int, height: int) -> SimpleNamespace:
        return SimpleNamespace(x=x, y=y, width=width, height=height)

    def test_a_monitor_is_the_output_with_its_place_and_size(self) -> None:
        found = adapters.output_of(self._monitor(1080, 0, 1920, 1080), self.outputs)

        assert found is not None
        self.assertEqual(found.name, "DP-2")

    def test_a_monitor_the_compositor_scaled_is_still_found_by_where_it_starts(self) -> None:
        found = adapters.output_of(self._monitor(3000, 0, 3840, 2160), self.outputs)

        assert found is not None
        self.assertEqual(found.name, "HDMI-A-1")

    def test_a_monitor_that_starts_nowhere_an_output_does_is_not_found(self) -> None:
        self.assertIsNone(adapters.output_of(self._monitor(9000, 0, 1920, 1080), self.outputs))
        self.assertIsNone(adapters.output_of(self._monitor(0, 0, 1920, 1080), []))


if __name__ == "__main__":
    unittest.main()
