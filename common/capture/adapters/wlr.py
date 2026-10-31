"""wlroots compositors: sway, and Hyprland, which VPinOS runs. Pictures through `grim`,
video through `wf-recorder`, both over wlr-screencopy."""

from __future__ import annotations

import glob
import json
import os
import socket
import struct
import subprocess
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any

from common.host import tools
from common.i18n import t

from .. import geometry
from . import Output, Screen, Window

# sway's IPC: this magic, then the payload's length and the message type, little-endian.
_MAGIC = b"i3-ipc"
_RUN_COMMAND = 0
_GET_OUTPUTS = 3
_GET_TREE = 4
# Hyprland's `fullscreen` field is 0 none, 1 maximized, 2 full screen.
_FULL_SCREEN = 2
# The output sway keeps its scratchpad under, which shows nothing.
_SCRATCHPAD = "__i3"

Connect = Callable[[str], socket.socket]


def _unix(path: str) -> socket.socket:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(tools.TIMEOUT)
    try:
        sock.connect(path)
    except OSError:
        sock.close()
        raise
    return sock


def _read(sock: socket.socket, count: int) -> bytes:
    held = b""
    while len(held) < count:
        got = sock.recv(count - len(held))
        if not got:
            raise ConnectionResetError
        held += got
    return held


def ask_sway(sock: socket.socket, message: int = _GET_OUTPUTS, payload: bytes = b"") -> Any:
    with sock:
        sock.sendall(_MAGIC + struct.pack("<II", len(payload), message) + payload)
        header = _read(sock, len(_MAGIC) + 8)
        if header[:len(_MAGIC)] != _MAGIC:
            raise ConnectionRefusedError
        length, _ = struct.unpack("<II", header[len(_MAGIC):])
        return json.loads(_read(sock, length))


def ask_hyprland(sock: socket.socket, request: bytes = b"j/monitors") -> Any:
    """Hyprland answers one request per connection and closes it."""
    with sock:
        sock.sendall(request)
        held = b""
        while got := sock.recv(65536):
            held += got
        return json.loads(held)


def reload_config(env: Mapping[str, str], connect: Connect = _unix) -> None:
    """Asks the compositor to read its config again. Raises OSError where it does not
    answer or refuses."""
    if env.get("SWAYSOCK"):
        try:
            said = ask_sway(connect(str(env["SWAYSOCK"])), _RUN_COMMAND, b"reload")
        except ValueError as exc:
            raise OSError(t("capture.reload.refused", compositor="Sway")) from exc
        if not said or not all(isinstance(one, dict) and one.get("success") for one in said):
            raise OSError(t("capture.reload.refused", compositor="Sway"))
        return
    with connect(hyprland_socket(env)) as sock:
        sock.sendall(b"/reload")
        held = b""
        while got := sock.recv(4096):
            held += got
    if held.strip() != b"ok":
        raise OSError(t("capture.reload.refused", compositor="Hyprland"))


def hyprland_socket(env: Mapping[str, str]) -> str:
    """Under the runtime directory since Hyprland 0.40, under /tmp before it."""
    signature = str(env.get("HYPRLAND_INSTANCE_SIGNATURE") or "")
    places = [str(env.get("XDG_RUNTIME_DIR") or ""), "/tmp"]
    paths = [os.path.join(place, "hypr", signature, ".socket.sock")
             for place in places if place]
    return next((path for path in paths if os.path.exists(path)), paths[0])


def sway_outputs(said: Any) -> list[Output]:
    """`rect` is the layout's; `current_mode` is the buffer's, refresh in millihertz."""
    found = []
    for index, one in enumerate(said if isinstance(said, list) else []):
        if not isinstance(one, dict) or not one.get("active", True):
            continue
        rect, mode = one.get("rect") or {}, one.get("current_mode") or {}
        found.append(Output(
            name=str(one.get("name") or ""),
            x=int(rect.get("x") or 0), y=int(rect.get("y") or 0),
            width=int(rect.get("width") or 0), height=int(rect.get("height") or 0),
            mode=(int(mode.get("width") or 0), int(mode.get("height") or 0)),
            refresh=int(mode.get("refresh") or 0) / 1000,
            transform=geometry.sway_transform(one.get("transform")), index=index))
    return found


def hyprland_outputs(said: Any) -> list[Output]:
    """`width` and `height` are the mode's, before the transform and the scale."""
    found = []
    for one in said if isinstance(said, list) else []:
        if not isinstance(one, dict) or one.get("disabled"):
            continue
        transform = geometry.hyprland_transform(one.get("transform"))
        mode = (int(one.get("width") or 0), int(one.get("height") or 0))
        scale = float(one.get("scale") or 1) or 1.0
        width, height = transform.size(*mode)
        found.append(Output(
            name=str(one.get("name") or ""),
            x=int(one.get("x") or 0), y=int(one.get("y") or 0),
            width=round(width / scale), height=round(height / scale),
            mode=mode, refresh=float(one.get("refreshRate") or 0),
            transform=transform, index=int(one.get("id") or 0)))
    return found


def sway_windows(tree: Any) -> list[Window]:
    """Every view in sway's tree - a container with nothing inside it - under the output
    that holds it."""
    found: list[Window] = []

    def walk(node: Any, output: str) -> None:
        if not isinstance(node, dict):
            return
        if node.get("type") == "output":
            output = str(node.get("name") or "")
        inside = [*(node.get("nodes") or []), *(node.get("floating_nodes") or [])]
        if node.get("type") in ("con", "floating_con") and not inside \
                and output and output != _SCRATCHPAD:
            properties = node.get("window_properties") or {}
            found.append(Window(str(node.get("app_id") or properties.get("class") or ""),
                                str(node.get("name") or ""), output))
        for child in inside:
            walk(child, output)

    walk(tree, "")
    return found


def hyprland_windows(clients: Any, monitors: Any) -> list[Window]:
    """A client's `monitor` is a monitor's id."""
    names = {one.get("id"): str(one.get("name") or "")
             for one in (monitors if isinstance(monitors, list) else [])
             if isinstance(one, dict)}
    found = []
    for one in clients if isinstance(clients, list) else []:
        if not isinstance(one, dict) or one.get("mapped") is False or one.get("hidden"):
            continue
        output = names.get(one.get("monitor"), "")
        if output:
            found.append(Window(str(one.get("class") or ""), str(one.get("title") or ""),
                                output))
    return found


def _sway_views(node: Any) -> Iterator[dict[str, Any]]:
    inside = [*(node.get("nodes") or []), *(node.get("floating_nodes") or [])] \
        if isinstance(node, dict) else []
    if isinstance(node, dict) and node.get("type") in ("con", "floating_con") and not inside:
        yield node
    for child in inside:
        yield from _sway_views(child)


def sway_view(tree: Any, pid: int) -> str | None:
    """The id of the view program `pid` has open, once sway has one."""
    return next((str(one["id"]) for one in _sway_views(tree)
                 if one.get("pid") == pid and "id" in one), None)


def sway_focused(tree: Any) -> str | None:
    """The id of the view that has focus, or None where focus is on an empty workspace."""
    return next((str(one["id"]) for one in _sway_views(tree)
                 if one.get("focused") and "id" in one), None)


def hyprland_view(clients: Any, pid: int) -> dict[str, Any] | None:
    """The client program `pid` has mapped, once Hyprland has one."""
    return next((one for one in clients if isinstance(one, dict) and one.get("pid") == pid
                 and one.get("mapped") is not False and not one.get("hidden")), None) \
        if isinstance(clients, list) else None


def hyprland_send(sock: socket.socket, request: bytes) -> str:
    """What Hyprland says to a command: "ok", or why not."""
    with sock:
        sock.sendall(request)
        held = b""
        while got := sock.recv(4096):
            held += got
    return held.decode(errors="replace").strip()


def render_node() -> str:
    found = sorted(glob.glob("/dev/dri/renderD*"))
    return found[0] if found else ""


# By FFmpeg and render node, for the life of the process.
_encodes: dict[tuple[str, str], bool] = {}


def vaapi_probe(ffmpeg: Path, node: str) -> list[str]:
    """One frame through `h264_vaapi`, to nowhere."""
    return [str(ffmpeg), "-hide_banner", "-loglevel", "error", "-vaapi_device", node,
            "-f", "lavfi", "-i", "color=c=black:s=256x256:d=0.1",
            "-vf", "format=nv12,hwupload", "-frames:v", "1", "-c:v", "h264_vaapi",
            "-f", "null", "-"]


def vaapi_node(ffmpeg: tools.Found, node: str | None = None,
               run: Callable[..., Any] = subprocess.run) -> str:
    """The render node that encodes H.264 in hardware, or "" where none does."""
    if ffmpeg.path is None or ffmpeg.probe is None or not ffmpeg.probe.has(
            tools.ENCODERS, "h264_vaapi"):
        return ""
    node = render_node() if node is None else node
    if not node:
        return ""
    key = (str(ffmpeg.path), node)
    if key not in _encodes:
        try:
            done = run(vaapi_probe(ffmpeg.path, node), capture_output=True,
                       stdin=subprocess.DEVNULL, timeout=tools.TIMEOUT, check=False,
                       creationflags=tools.NO_WINDOW)
            _encodes[key] = done.returncode == 0
        except (OSError, subprocess.TimeoutExpired):
            _encodes[key] = False
    return node if _encodes[key] else ""


class WlrAdapter:
    id = "wlr"
    picture_tool = tools.GRIM
    video_tool = tools.WF_RECORDER

    def __init__(self, env: Mapping[str, str], connect: Connect = _unix) -> None:
        self.env = env
        self._connect = connect

    @staticmethod
    def applies(env: Mapping[str, str]) -> bool:
        return bool(env.get("WAYLAND_DISPLAY")) and bool(
            env.get("SWAYSOCK") or env.get("HYPRLAND_INSTANCE_SIGNATURE"))

    def requirements(self) -> tuple[tools.Tool, ...]:
        return (tools.FFMPEG, tools.GRIM, tools.WF_RECORDER)

    def grabs(self, ffmpeg: tools.Found) -> bool:
        """FFmpeg only encodes here."""
        return True

    def lacks(self, found: Mapping[str, tools.Found]) -> tuple[tools.Tool, str] | None:
        return None

    def refused(self) -> tuple[str, Mapping[str, str]] | None:
        return None

    def begin(self, screens: Mapping[str, Output]) -> dict[str, Screen]:
        return {window: Screen(window, output) for window, output in screens.items()}

    def lend(self, output: Output) -> Output:
        return output

    def end(self) -> None:
        pass

    def no_sound(self) -> tuple[str, Mapping[str, str]] | None:
        return None

    def outputs(self) -> list[Output]:
        """Raises OSError or ValueError where the compositor does not answer."""
        if self.env.get("SWAYSOCK"):
            return sway_outputs(ask_sway(self._connect(str(self.env["SWAYSOCK"]))))
        return hyprland_outputs(ask_hyprland(self._connect(hyprland_socket(self.env))))

    def windows(self) -> list[Window]:
        """Raises OSError or ValueError where the compositor does not answer."""
        if self.env.get("SWAYSOCK"):
            return sway_windows(ask_sway(self._connect(str(self.env["SWAYSOCK"])), _GET_TREE))
        path = hyprland_socket(self.env)
        return hyprland_windows(ask_hyprland(self._connect(path), b"j/clients"),
                                ask_hyprland(self._connect(path)))

    def _sway(self, message: int, payload: bytes = b"") -> Any:
        return ask_sway(self._connect(str(self.env["SWAYSOCK"])), message, payload)

    def _sway_run(self, command: str) -> None:
        said = self._sway(_RUN_COMMAND, command.encode())
        if not said or not all(isinstance(one, dict) and one.get("success") for one in said):
            raise OSError(next((str(one.get("error")) for one in said
                                if isinstance(one, dict) and one.get("error")), command))

    def _hyprland(self, request: bytes) -> Any:
        return ask_hyprland(self._connect(hyprland_socket(self.env)), request)

    def _hyprland_run(self, command: str) -> None:
        said = hyprland_send(self._connect(hyprland_socket(self.env)),
                             b"/dispatch " + command.encode())
        if said != "ok":
            raise OSError(said or command)

    def view_of(self, pid: int) -> str | None:
        """The compositor's handle for the window program `pid` has open, or None while it
        has none. Raises OSError or ValueError where the compositor does not answer."""
        if self.env.get("SWAYSOCK"):
            return sway_view(self._sway(_GET_TREE), pid)
        found = hyprland_view(self._hyprland(b"j/clients"), pid)
        return str(found["address"]) if found and found.get("address") else None

    def focused_view(self) -> str | None:
        """The handle of the window that has focus. Raises as `view_of` does."""
        if self.env.get("SWAYSOCK"):
            return sway_focused(self._sway(_GET_TREE))
        found = self._hyprland(b"j/activewindow")
        return str(found["address"]) if isinstance(found, dict) and found.get("address") \
            else None

    def focus_view(self, view: str) -> None:
        """Raises OSError where the compositor refuses."""
        if self.env.get("SWAYSOCK"):
            self._sway_run(f"[con_id={view}] focus")
        else:
            self._hyprland_run(f"focuswindow address:{view}")

    def fill_output(self, view: str, output: Output) -> None:
        """Sends the window to `output`, borderless and full screen. Raises OSError where
        the compositor refuses and ValueError where it answers nothing readable."""
        if self.env.get("SWAYSOCK"):
            name = output.name.replace('"', '\\"')
            self._sway_run(f'[con_id={view}] move to output "{name}", border none, '
                           "fullscreen enable")
            return
        monitors = self._hyprland(b"j/monitors")
        workspace = next((one["activeWorkspace"].get("id")
                          for one in monitors if isinstance(monitors, list)
                          and isinstance(one, dict) and one.get("name") == output.name
                          and isinstance(one.get("activeWorkspace"), dict)), None)
        if workspace is None:
            raise OSError(output.name)
        clients = self._hyprland(b"j/clients")
        already = next((one.get("fullscreen") == _FULL_SCREEN for one in clients
                        if isinstance(clients, list) and isinstance(one, dict)
                        and one.get("address") == view), False)
        self._hyprland_run(f"movetoworkspacesilent {workspace},address:{view}")
        if not already:
            self._hyprland_run(f"focuswindow address:{view}")
            self._hyprland_run("fullscreen 0")

    def at_once(self, ffmpeg: tools.Found) -> bool:
        return bool(self.hardware(ffmpeg))

    def hardware(self, ffmpeg: tools.Found) -> str:
        return vaapi_node(ffmpeg)

    def still(self, found: Mapping[str, tools.Found], output: Output,
              dest: Path) -> list[str]:
        return [str(found[tools.GRIM.id].path), "-o", output.name, str(dest)]

    def still_turn(self, output: Output) -> geometry.Turn:
        """grim draws the output as the screen shows it."""
        return geometry.NONE

    def recording_turn(self, output: Output) -> geometry.Turn:
        """wf-recorder hands over the buffer before the output's transform."""
        return output.transform


def reset_for_tests() -> None:
    _encodes.clear()
