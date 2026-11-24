"""What this session is: the one answer every probe is chosen by."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from common.host import tools

from .run import Run
from .run import run as run_program

WINDOWS = "windows"
MACOS = "macos"
LINUX = "linux"

DWM = "dwm"
QUARTZ = "quartz"
X11 = "x11"
WAYLAND = "wayland"
NO_SERVER = "none"

SWAY = "sway"
HYPRLAND = "hyprland"
KDE = "kde"
GNOME = "gnome"
GAMESCOPE = "gamescope"
OTHER = "other"
UNKNOWN = "unknown"

NO_CONTAINER = "none"

# `XDG_CURRENT_DESKTOP` as the desktops write it, by the name this module gives them.
_SHELLS = {"SWAY": SWAY, "HYPRLAND": HYPRLAND, "KDE": KDE, "GNOME": GNOME,
           "X-CINNAMON": "cinnamon", "CINNAMON": "cinnamon", "XFCE": "xfce", "MATE": "mate",
           "LXQT": "lxqt", "LXDE": "lxde", "COSMIC": "cosmic", "BUDGIE": "budgie",
           "I3": "i3", "UNITY": "unity", "DEEPIN": "deepin", "PANTHEON": "pantheon",
           "ENLIGHTENMENT": "enlightenment"}

# What `container` holds when a runtime sets it, by the name this module gives it.
_CONTAINER_VARIABLE = {"podman": "podman", "docker": "docker", "lxc": "lxc",
                       "lxc-libvirt": "lxc", "systemd-nspawn": "nspawn"}


@dataclass(frozen=True)
class Desktop:
    system: str
    server: str
    shell: str
    container: str = NO_CONTAINER
    # The virtual machine monitor, "" where this is not one, None where nothing said.
    vm: str | None = None
    # os-release's ID on Linux.
    distro: str = ""

    @property
    def native_wayland(self) -> bool:
        return self.server == WAYLAND

    @property
    def game_mode(self) -> bool:
        return self.shell == GAMESCOPE


def _exists(path: str) -> bool:
    return os.path.exists(path)


def _read(path: str) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def resolve(env: Mapping[str, str] | None = None, system: str | None = None,
            exists: Callable[[str], bool] = _exists, read: Callable[[str], str] = _read,
            run: Run = run_program) -> Desktop:
    env = os.environ if env is None else env
    here = tools.here() if system is None else system
    if here == tools.WINDOWS:
        return Desktop(WINDOWS, DWM, "explorer")
    if here == tools.DARWIN:
        return Desktop(MACOS, QUARTZ, "aqua")
    return Desktop(LINUX, _server(env), _shell(env), _container(env, exists, read),
                   _vm(run), _distro(read))


def _server(env: Mapping[str, str]) -> str:
    session = str(env.get("XDG_SESSION_TYPE") or "").strip().lower()
    if str(env.get("WAYLAND_DISPLAY") or "").strip() or session == "wayland":
        return WAYLAND
    if str(env.get("DISPLAY") or "").strip() or session == "x11":
        return X11
    return NO_SERVER


def _shell(env: Mapping[str, str]) -> str:
    if env.get("GAMESCOPE_WAYLAND_DISPLAY"):
        return GAMESCOPE
    if env.get("SWAYSOCK"):
        return SWAY
    if env.get("HYPRLAND_INSTANCE_SIGNATURE"):
        return HYPRLAND
    named = [one for one in str(env.get("XDG_CURRENT_DESKTOP") or "").upper().split(":")
             if one.strip()]
    for one in named:
        if one in _SHELLS:
            return _SHELLS[one]
    return OTHER if named else UNKNOWN


def _container(env: Mapping[str, str], exists: Callable[[str], bool],
               read: Callable[[str], str]) -> str:
    if env.get("WSL_DISTRO_NAME") or "microsoft" in read("/proc/version").lower():
        return "wsl"
    if env.get("FLATPAK_ID") or exists("/.flatpak-info"):
        return "flatpak"
    if env.get("SNAP"):
        return "snap"
    if env.get("APPIMAGE"):
        return "appimage"
    if exists("/.dockerenv"):
        return "docker"
    if exists("/run/.containerenv"):
        return "podman"
    return _CONTAINER_VARIABLE.get(str(env.get("container") or ""), NO_CONTAINER)


def _vm(run: Run) -> str | None:
    said = run(["systemd-detect-virt", "--vm"])
    word = said.text.strip().lower()
    if not word or not said.text:
        return None
    return "" if word == "none" else word


def _distro(read: Callable[[str], str]) -> str:
    for line in read("/etc/os-release").splitlines():
        key, _, value = line.partition("=")
        if key == "ID":
            return value.strip().strip('"').strip("'").lower()
    return ""
