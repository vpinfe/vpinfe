"""Linux: who has the keyboard, and giving it to a window.

X11 through python-xlib, which pynput brings, Hyprland and sway through the sockets the
capture code already speaks, KDE on Wayland through KWin's scripting (`front_kde`) and GNOME
on Wayland through a Shell add-on VPinFE puts in the person's folder (`front_gnome`). Until
that add-on answers GNOME has no backend, and `why_not` says so.
"""

from __future__ import annotations

import os
import types
from collections.abc import Mapping
from typing import Any

from common.capture.adapters.wlr import WlrAdapter
from common.failures import why
from common.host import front_gnome, front_kde
from common.host.front import Window, on_wayland

_SOURCE_PAGER = 2


def backend_for(env: Mapping[str, str] | None = None) -> Any | None:
    """The backend for this session, or None where the desktop has none. Raises where a
    backend should answer and cannot be reached."""
    env = os.environ if env is None else env
    if WlrAdapter.applies(env):
        return WlrBackend(WlrAdapter(env))
    if on_wayland(env) and front_kde.applies(env):
        return front_kde.KWinBackend()
    if on_wayland(env) and front_gnome.applies(env):
        return front_gnome.backend(env)
    if on_wayland(env) or not env.get("DISPLAY"):
        return None
    return X11Backend()


def why_not(env: Mapping[str, str] | None = None) -> str:
    """What to say when `backend_for` has none."""
    env = os.environ if env is None else env
    desktop = env.get("XDG_CURRENT_DESKTOP") or "this desktop"
    if on_wayland(env) and desktop.lower() in ("sway", "hyprland"):
        return (f"{desktop} is running but VPinFE cannot reach it, because its socket is not "
                "in VPinFE's environment, so nothing is watched")
    if on_wayland(env) and front_kde.applies(env):
        return ("KDE Plasma is running but VPinFE cannot reach KWin over the session bus, "
                "so nothing is watched")
    if on_wayland(env) and front_gnome.applies(env):
        return front_gnome.why_not(env)
    if on_wayland(env):
        return (f"{desktop} on Wayland lets no program list another's windows or give it "
                "the keyboard, so nothing is watched; a window rule in the compositor that "
                "gives the table's window focus is the fix")
    if env.get("DISPLAY"):
        return "the display could not be asked, so nothing is watched"
    return "there is no display to ask, so nothing is watched"


class WlrBackend:
    """Hyprland and sway."""

    name = "Wayland (wlroots)"

    def __init__(self, adapter: Any) -> None:
        self._adapter = adapter

    def windows(self) -> list[Window]:
        return [Window(handle, pid, title) for handle, pid, title in self._adapter.pid_views()]

    def holder(self) -> Window | None:
        focused = self._adapter.focused_view()
        if focused is None:
            return None
        return next((one for one in self.windows() if one.handle == focused), None)

    def hand_over(self, window: Window) -> str:
        try:
            self._adapter.focus_view(str(window.handle))
        except OSError as exc:
            return why(exc)
        return ""


class X11Backend:
    """A window manager that follows the EWMH hints, which every desktop does."""

    name = "X11"

    def __init__(self, display: Any = None, xlib: Any = None) -> None:
        if xlib is None:
            from Xlib import X, error
            from Xlib.protocol import event
            xlib = types.SimpleNamespace(X=X, event=event, error=error)
        if display is None:
            from Xlib import display as xdisplay
            display = xdisplay.Display()
        self._x = xlib.X
        self._event = xlib.event
        # A window that closes between being listed and being read raises these.
        self._vanished = (xlib.error.BadWindow, AttributeError)
        self._atoms: dict[str, int] = {}
        self._display = display
        self._root = display.screen().root

    def close(self) -> None:
        self._display.close()

    def _atom(self, name: str) -> int:
        if name not in self._atoms:
            self._atoms[name] = int(self._display.intern_atom(name))
        return self._atoms[name]

    def _property(self, window: Any, name: str) -> Any:
        found = window.get_full_property(self._atom(name), self._x.AnyPropertyType)
        return None if found is None else found.value

    def _window(self, window_id: int) -> Window:
        window = self._display.create_resource_object("window", window_id)
        pid = self._property(window, "_NET_WM_PID")
        title = self._property(window, "_NET_WM_NAME")
        if title is None:
            title = self._property(window, "WM_NAME")
        if isinstance(title, (bytes, bytearray)):
            title = bytes(title).decode("utf-8", errors="replace")
        return Window(handle=int(window_id), pid=int(pid[0]) if pid else 0,
                      title=str(title or ""))

    def windows(self) -> list[Window]:
        listed = self._property(self._root, "_NET_CLIENT_LIST") or ()
        found = []
        for one in listed:
            try:
                found.append(self._window(int(one)))
            except self._vanished:
                continue
        return found

    def holder(self) -> Window | None:
        active = self._property(self._root, "_NET_ACTIVE_WINDOW")
        if not active or not int(active[0]):
            return None
        try:
            if self._is_desktop_or_panel(int(active[0])):
                return None
            return self._window(int(active[0]))
        except self._vanished:
            return None

    def _is_desktop_or_panel(self, window_id: int) -> bool:
        window = self._display.create_resource_object("window", window_id)
        kinds = self._property(window, "_NET_WM_WINDOW_TYPE") or ()
        shell = {self._atom("_NET_WM_WINDOW_TYPE_DESKTOP"), self._atom("_NET_WM_WINDOW_TYPE_DOCK")}
        return any(int(one) in shell for one in kinds)

    def hand_over(self, window: Window) -> str:
        """The request a taskbar or pager makes. A window manager ignores a program that
        asks for itself, and honors one that asks for another."""
        target = self._display.create_resource_object("window", int(window.handle))
        request = self._event.ClientMessage(
            window=target, client_type=self._atom("_NET_ACTIVE_WINDOW"),
            data=(32, [_SOURCE_PAGER, self._x.CurrentTime, 0, 0, 0]))
        self._root.send_event(
            request,
            event_mask=self._x.SubstructureRedirectMask | self._x.SubstructureNotifyMask)
        self._display.sync()
        return ""
