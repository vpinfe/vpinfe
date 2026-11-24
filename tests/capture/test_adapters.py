"""Which adapter a session gets, the outputs a wlroots compositor reports, and which output
each window is on."""

from __future__ import annotations

import configparser
import json
import socket
import struct
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from common.capture import adapters, geometry
from common.capture.adapters import ffmpeg, wlr
from common.host import tools

SWAY = {"WAYLAND_DISPLAY": "wayland-1", "SWAYSOCK": "/run/user/1000/sway-ipc.sock",
        "XDG_CURRENT_DESKTOP": "sway"}
HYPRLAND = {"WAYLAND_DISPLAY": "wayland-1", "HYPRLAND_INSTANCE_SIGNATURE": "abc123",
            "XDG_CURRENT_DESKTOP": "Hyprland", "XDG_RUNTIME_DIR": "/run/user/1000"}


class ResolutionTests(unittest.TestCase):
    """Per compositor, from the session's variables alone."""

    CASES = {
        "sway": (SWAY, "wlr", ""),
        "Hyprland": (HYPRLAND, "wlr", ""),
        "sway, with Xwayland's DISPLAY too": ({**SWAY, "DISPLAY": ":0"}, "wlr", ""),
        "KDE Plasma on Wayland": ({"WAYLAND_DISPLAY": "wayland-0",
                                   "XDG_CURRENT_DESKTOP": "KDE"}, "portal", ""),
        "GNOME on Wayland": ({"WAYLAND_DISPLAY": "wayland-0",
                              "XDG_CURRENT_DESKTOP": "ubuntu:GNOME"}, "portal", ""),
        "Weston": ({"WAYLAND_DISPLAY": "wayland-0"}, "wayland", adapters.NO_WAY),
        "X11": ({"DISPLAY": ":0", "XDG_CURRENT_DESKTOP": "KDE"}, "x11grab", ""),
        "a sway socket left in an X11 session": ({"DISPLAY": ":0", "SWAYSOCK": "/x"},
                                                 "x11grab", ""),
        "no session": ({}, "none", adapters.NO_SESSION),
    }

    def test_each_linux_session_resolves_to_its_adapter(self) -> None:
        for name, (env, expected, why) in self.CASES.items():
            with self.subTest(name):
                found = adapters.resolve(env, tools.LINUX)

                self.assertEqual(found.id, expected)
                self.assertEqual(getattr(found, "reason", ""), why)

    def test_windows_and_macos_are_ffmpegs_whatever_the_variables_say(self) -> None:
        for system, expected in ((tools.WINDOWS, ffmpeg.WindowsAdapter),
                                 (tools.DARWIN, ffmpeg.MacAdapter)):
            with self.subTest(system):
                self.assertIsInstance(adapters.resolve(SWAY, system), expected)


def _sway_output(name: str, x: int, width: int, height: int, transform: str) -> dict:
    return {"name": name, "active": True, "transform": transform, "scale": 1.0,
            "rect": {"x": x, "y": 0, "width": width, "height": height},
            "current_mode": {"width": 1920, "height": 1080, "refresh": 60000}}


# Three 1920x1080 60 Hz outputs, the playfield's turned 270 by the compositor.
SWAY_OUTPUTS = [_sway_output("DP-1", 0, 1080, 1920, "270"),
                _sway_output("DP-2", 1080, 1920, 1080, "normal"),
                _sway_output("HDMI-A-1", 3000, 1920, 1080, "normal"),
                {**_sway_output("DP-3", 4920, 1920, 1080, "normal"), "active": False}]
HYPRLAND_MONITORS = [
    {"name": "DP-1", "x": 0, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
     "refreshRate": 60.0, "transform": 3, "disabled": False},
    {"name": "DP-2", "x": 1080, "y": 0, "width": 3840, "height": 2160, "scale": 2.0,
     "refreshRate": 59.94, "transform": 0, "disabled": False},
    {"name": "DP-3", "x": 3000, "y": 0, "width": 1920, "height": 1080, "scale": 1.0,
     "refreshRate": 60.0, "transform": 0, "disabled": True},
]


class OutputTests(unittest.TestCase):
    def test_sway_reports_the_layout_the_buffer_and_the_transform(self) -> None:
        playfield, backglass, dmd = wlr.sway_outputs(SWAY_OUTPUTS)

        self.assertEqual((playfield.name, playfield.width, playfield.height),
                         ("DP-1", 1080, 1920))
        self.assertEqual(playfield.mode, (1920, 1080))
        self.assertEqual(playfield.refresh, 60.0)
        self.assertEqual(playfield.transform, geometry.sway_transform("270"))
        self.assertEqual(playfield.surface, "portrait")
        self.assertEqual((backglass.surface, dmd.name), ("landscape", "HDMI-A-1"))

    def test_hyprland_lays_out_the_mode_after_its_transform_and_scale(self) -> None:
        playfield, backglass = wlr.hyprland_outputs(HYPRLAND_MONITORS)

        self.assertEqual((playfield.width, playfield.height), (1080, 1920))
        self.assertEqual(playfield.mode, (1920, 1080))
        self.assertEqual((backglass.width, backglass.height), (1920, 1080))
        self.assertEqual(backglass.refresh, 59.94)

    def test_anything_else_the_compositor_says_is_no_outputs(self) -> None:
        self.assertEqual(wlr.sway_outputs({"error": "no"}), [])
        self.assertEqual(wlr.hyprland_outputs("ok"), [])


def _view(title: str, app_id: str | None = "VPinballX_BGFX", **more) -> dict:
    return {"type": "con", "name": title, "app_id": app_id, "nodes": [],
            "floating_nodes": [], **more}


def _workspace(*views: dict, floating: tuple = ()) -> dict:
    return {"type": "workspace", "name": "1", "nodes": list(views),
            "floating_nodes": list(floating)}


# sway's tree as VPX's windows sat in it: the playfield and the backglass each on an
# output of their own, nothing on the third, and a split holding two of VPinFE's windows.
SWAY_TREE = {"type": "root", "nodes": [
    {"type": "output", "name": "__i3", "nodes": [_workspace(_view("Scratch"))]},
    {"type": "output", "name": "DP-1", "nodes": [_workspace(
        {"type": "con", "name": None, "nodes": [
            _view("VPinFE Table", "chromium"), _view("Visual Pinball Player")]})]},
    {"type": "output", "name": "HDMI-A-1", "nodes": [_workspace(
        _view("VPinFE BG", None, window_properties={"class": "Chromium"}),
        floating=(_view("Visual Pinball Backglass", type="floating_con"),))]},
    {"type": "output", "name": "DVI-D-1", "nodes": [_workspace()]},
]}

HYPRLAND_CLIENTS = [
    {"class": "VPinballX_BGFX", "title": "Visual Pinball Player", "monitor": 0,
     "mapped": True, "hidden": False},
    {"class": "VPinballX_BGFX", "title": "Visual Pinball Backglass", "monitor": 1,
     "mapped": True, "hidden": False},
    {"class": "VPinballX_BGFX", "title": "Visual Pinball Score View", "monitor": 1,
     "mapped": False, "hidden": False},
    {"class": "chromium", "title": "VPinFE DMD", "monitor": 1, "mapped": True,
     "hidden": True},
    {"class": "foot", "title": "somewhere", "monitor": 7, "mapped": True},
]


class WindowTests(unittest.TestCase):
    def test_sway_says_each_view_and_the_output_holding_it(self) -> None:
        found = wlr.sway_windows(SWAY_TREE)

        self.assertEqual(found, [
            adapters.Window("chromium", "VPinFE Table", "DP-1"),
            adapters.Window("VPinballX_BGFX", "Visual Pinball Player", "DP-1"),
            adapters.Window("Chromium", "VPinFE BG", "HDMI-A-1"),
            adapters.Window("VPinballX_BGFX", "Visual Pinball Backglass", "HDMI-A-1")])

    def test_hyprland_names_each_clients_monitor_and_skips_what_is_not_shown(self) -> None:
        monitors = [{"id": 0, "name": "DP-1"}, {"id": 1, "name": "HDMI-A-1"}]

        found = wlr.hyprland_windows(HYPRLAND_CLIENTS, monitors)

        self.assertEqual(found, [
            adapters.Window("VPinballX_BGFX", "Visual Pinball Player", "DP-1"),
            adapters.Window("VPinballX_BGFX", "Visual Pinball Backglass", "HDMI-A-1")])

    def test_anything_else_the_compositor_says_is_no_windows(self) -> None:
        self.assertEqual(wlr.sway_windows([]), [])
        self.assertEqual(wlr.hyprland_windows({"error": "no"}, []), [])


def _serve(answer):
    """One end of a socket pair, with `answer(conversation)` on the other end."""
    ours, theirs = socket.socketpair()
    threading.Thread(target=answer, args=(theirs,), daemon=True).start()
    return ours


class IpcTests(unittest.TestCase):
    def test_sway_is_asked_for_its_outputs_in_its_own_framing(self) -> None:
        heard = []

        def sway(sock):
            with sock:
                heard.append(sock.recv(14))
                payload = json.dumps(SWAY_OUTPUTS).encode()
                sock.sendall(b"i3-ipc" + struct.pack("<II", len(payload), 3) + payload)

        connect = MagicMock(side_effect=lambda path: _serve(sway))
        found = wlr.WlrAdapter(SWAY, connect).outputs()

        connect.assert_called_once_with(SWAY["SWAYSOCK"])
        self.assertEqual(heard, [b"i3-ipc" + struct.pack("<II", 0, 3)])
        self.assertEqual([one.name for one in found], ["DP-1", "DP-2", "HDMI-A-1"])

    def test_hyprland_is_asked_on_its_socket_under_the_runtime_directory(self) -> None:
        heard = []

        def hyprland(sock):
            with sock:
                heard.append(sock.recv(64))
                sock.sendall(json.dumps(HYPRLAND_MONITORS).encode())

        connect = MagicMock(side_effect=lambda path: _serve(hyprland))
        found = wlr.WlrAdapter(HYPRLAND, connect).outputs()

        self.assertEqual(Path(connect.call_args.args[0]),
                         Path("/run/user/1000/hypr/abc123/.socket.sock"))
        self.assertEqual(heard, [b"j/monitors"])
        self.assertEqual(len(found), 2)

    def test_sway_is_asked_for_its_tree_for_the_windows(self) -> None:
        heard = []

        def sway(sock):
            with sock:
                heard.append(sock.recv(14))
                payload = json.dumps(SWAY_TREE).encode()
                sock.sendall(b"i3-ipc" + struct.pack("<II", len(payload), 4) + payload)

        found = wlr.WlrAdapter(SWAY, lambda path: _serve(sway)).windows()

        self.assertEqual(heard, [b"i3-ipc" + struct.pack("<II", 0, 4)])
        self.assertEqual(len(found), 4)

    def test_hyprland_is_asked_for_its_clients_and_monitors_one_to_a_connection(self) -> None:
        heard = []
        answers = {b"j/clients": HYPRLAND_CLIENTS,
                   b"j/monitors": [{**one, "id": index}
                                   for index, one in enumerate(HYPRLAND_MONITORS)]}

        def hyprland(sock):
            with sock:
                asked = sock.recv(64)
                heard.append(asked)
                sock.sendall(json.dumps(answers[asked]).encode())

        found = wlr.WlrAdapter(HYPRLAND, lambda path: _serve(hyprland)).windows()

        self.assertEqual(heard, [b"j/clients", b"j/monitors"])
        self.assertEqual([(one.title, one.output) for one in found],
                         [("Visual Pinball Player", "DP-1"),
                          ("Visual Pinball Backglass", "DP-2")])

    def test_a_reply_cut_short_is_an_error_not_a_partial_list(self) -> None:
        def sway(sock):
            with sock:
                sock.recv(14)
                sock.sendall(b"i3-ipc" + struct.pack("<II", 500, 3) + b"[")

        with self.assertRaises(OSError):
            wlr.WlrAdapter(SWAY, lambda path: _serve(sway)).outputs()


class ReloadTests(unittest.TestCase):
    def _sway(self, said: list, heard: list):
        def sway(sock):
            with sock:
                head = sock.recv(14)
                (length, kind) = struct.unpack("<II", head[6:])
                heard.append((kind, sock.recv(length) if length else b""))
                payload = json.dumps(said).encode()
                sock.sendall(b"i3-ipc" + struct.pack("<II", len(payload), 0) + payload)

        return lambda path: _serve(sway)

    def test_sway_is_sent_reload_as_a_command(self) -> None:
        heard: list = []

        wlr.reload_config(SWAY, self._sway([{"success": True}], heard))

        self.assertEqual(heard, [(0, b"reload")])

    def test_sway_answering_with_nothing_readable_is_an_error_not_a_crash(self) -> None:
        with patch.object(wlr, "ask_sway", side_effect=ValueError("empty")), \
                self.assertRaises(OSError):
            wlr.reload_config(SWAY, lambda path: MagicMock())

    def test_sway_refusing_the_reload_is_an_error(self) -> None:
        said: Any
        for said in ([], [{"success": False, "error": "no"}],
                     [{"success": True}, {"success": False}], {"success": True}):
            with self.subTest(said=said), self.assertRaises(OSError):
                wlr.reload_config(SWAY, self._sway(said, []))

    def test_hyprland_is_sent_reload_and_must_answer_ok(self) -> None:
        for answer, ok in ((b"ok", True), (b"ok\n", True), (b"unknown request", False)):
            heard: list = []

            def hyprland(sock, answer=answer, heard=heard):
                with sock:
                    heard.append(sock.recv(64))
                    sock.sendall(answer)

            with self.subTest(answer=answer):
                if ok:
                    wlr.reload_config(HYPRLAND, lambda path: _serve(hyprland))
                else:
                    with self.assertRaises(OSError):
                        wlr.reload_config(HYPRLAND, lambda path: _serve(hyprland))
                self.assertEqual(heard, [b"/reload"])


def _config(**screens: str) -> configparser.ConfigParser:
    config = configparser.ConfigParser()
    for window, value in screens.items():
        config[f"windows.{window}"] = {"screen_id": value}
    return config


def _monitor(x: int, width: int, height: int, name: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=0, width=width, height=height, name=name)


class ScreenMatchTests(unittest.TestCase):
    outputs = wlr.sway_outputs(SWAY_OUTPUTS)

    def test_a_window_finds_its_output_by_connector_name(self) -> None:
        monitors = [_monitor(3000, 1920, 1080, "HDMI-A-1"), _monitor(0, 1080, 1920, "DP-1")]

        found = adapters.screen_of("playfield", self.outputs, _config(playfield="1"),
                                   monitors)

        assert found.output is not None
        self.assertEqual(found.output.name, "DP-1")

    def test_a_monitor_with_no_name_is_found_where_it_is(self) -> None:
        monitors = [_monitor(1080, 1920, 1080, "XWAYLAND1")]

        found = adapters.screen_of("backglass", self.outputs, _config(backglass="0"),
                                   monitors)

        assert found.output is not None
        self.assertEqual(found.output.name, "DP-2")

    def test_the_index_is_the_display_models_never_the_compositors(self) -> None:
        """Monitor 0 here is the DMD, while the compositor lists the playfield first."""
        monitors = [_monitor(3000, 1920, 1080)]

        found = adapters.screen_of("scoreview", self.outputs,
                                   _config(scoreview="0"), monitors)

        assert found.output is not None
        self.assertEqual(found.output.name, "HDMI-A-1")

    def test_the_topper_finds_the_output_of_its_own_screen(self) -> None:
        monitors = [_monitor(3000, 1920, 1080, "HDMI-A-1"), _monitor(0, 1080, 1920, "DP-1")]

        found = adapters.screen_of("topper", self.outputs, _config(topper="1"), monitors)

        assert found.output is not None
        self.assertEqual(found.output.name, "DP-1")

    def test_a_window_with_no_screen_set_says_so(self) -> None:
        for window, config in (("backglass", _config()), ("topper", _config())):
            with self.subTest(window):
                found = adapters.screen_of(window, self.outputs, config, [])

                self.assertEqual((found.output, found.reason), (None, adapters.NO_SCREEN))

    def test_a_screen_no_output_matches_is_not_found(self) -> None:
        for monitors in ([], [_monitor(9000, 800, 600)]):
            with self.subTest(monitors=monitors):
                found = adapters.screen_of("playfield", self.outputs,
                                           _config(playfield="0"), monitors)

                self.assertEqual(found.reason, adapters.NOT_FOUND)


def _ffmpeg(*encoders: str) -> tools.Found:
    return tools.Found(tools.FFMPEG, tools.State.FOUND, Path("/usr/bin/ffmpeg"),
                       tools.Probe(True, "7.1", {tools.ENCODERS: frozenset(encoders)}))


class HardwareTests(unittest.TestCase):
    def setUp(self) -> None:
        wlr.reset_for_tests()
        self.addCleanup(wlr.reset_for_tests)

    def test_a_frame_that_encodes_on_the_render_node_is_hardware(self) -> None:
        run = MagicMock(return_value=SimpleNamespace(returncode=0))

        node = wlr.vaapi_node(_ffmpeg("h264_vaapi"), "/dev/dri/renderD128", run)

        self.assertEqual(node, "/dev/dri/renderD128")
        argv = run.call_args.args[0]
        self.assertEqual(argv[argv.index("-vaapi_device") + 1], "/dev/dri/renderD128")
        self.assertEqual(argv[argv.index("-c:v") + 1], "h264_vaapi")

    def test_one_that_fails_is_not_and_is_asked_once(self) -> None:
        run = MagicMock(return_value=SimpleNamespace(returncode=1))

        for _ in range(2):
            self.assertEqual(wlr.vaapi_node(_ffmpeg("h264_vaapi"), "/dev/dri/renderD128",
                                            run), "")
        run.assert_called_once()

    def test_nothing_is_run_without_the_encoder_or_a_node(self) -> None:
        run = MagicMock()

        self.assertEqual(wlr.vaapi_node(_ffmpeg("libx264"), "/dev/dri/renderD128", run), "")
        self.assertEqual(wlr.vaapi_node(_ffmpeg("h264_vaapi"), "", run), "")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
