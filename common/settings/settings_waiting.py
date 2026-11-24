"""What has been changed and is not yet in effect.

A setting is read by something at a particular moment - as it is used, when the windows
open, when VPinFE starts - and `ConfigOption.applies` says which. This remembers what
each of the last two was read as, and answers which settings now differ from that.

Changing a value back clears it, because the answer is a comparison and not a list of
edits. Window settings wait for a reopen, or for a restart when the set of windows has
changed: a window's API is built once at startup, so a reopen cannot add or remove one.
An install with no windows open has nothing for a window setting to wait on.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from typing import Any

from common import events, extensions
from common.config import config_schema
from common.config.config_store import ConfigStore
from common.config.paths import get_ini_config
from common.extensions import host as extension_host
from common.runtime import lifecycle
from common.settings import config_service

WAITS_RESTART = "restart"
WAITS_REOPEN = "reopen"

KIND_SETTING = "setting"
KIND_EXTENSION = "extension"

# The two things the frontend knows and common/ may not import: the windows the theme
# adds to the three it always has, and the windows that get an API under these settings.
OwnWindows = Callable[[ConfigStore], tuple[str, ...]]
WindowSet = Callable[[ConfigStore], frozenset[str]]

_lock = threading.RLock()


def _no_windows(store: ConfigStore) -> tuple[str, ...]:
    return ()


def _no_window_set(store: ConfigStore) -> frozenset[str]:
    return frozenset()


_own_windows: OwnWindows = _no_windows
_window_set: WindowSet = _no_window_set
_started: dict[str, Any] | None = None
_started_windows: frozenset[str] = frozenset()
_opened: dict[str, Any] | None = None
_last: dict[str, Any] | None = None


def install(own_windows: OwnWindows, window_set: WindowSet) -> None:
    """Say how this process names its windows. Once, before `record_start`."""
    global _own_windows, _window_set
    with _lock:
        _own_windows, _window_set = own_windows, window_set
    for name in (events.SETTINGS_CHANGED, events.EXTENSION_SWITCHED):
        events.unsubscribe(name, _on_change)
        events.subscribe(name, _on_change)


def _on_change(**_: Any) -> None:
    refresh()


def record_start(store: ConfigStore) -> None:
    """The values VPinFE starts on, and the windows it builds an API for."""
    global _started, _started_windows, _last
    with _lock:
        _started = _read(store, {config_schema.APPLIES_RESTART, config_schema.APPLIES_WINDOWS})
        _started_windows = _window_set(store)
        _last = answer()


def windows_opened(store: ConfigStore) -> None:
    """The windows are opening on these values."""
    global _opened
    with _lock:
        _opened = _read(store, {config_schema.APPLIES_WINDOWS})
    refresh()


def windows_closed() -> None:
    global _opened
    with _lock:
        _opened = None
    refresh()


def forget() -> None:
    """Nothing has started and nothing is open. For a test."""
    global _own_windows, _window_set, _started, _started_windows, _opened, _last
    with _lock:
        _own_windows, _window_set = _no_windows, _no_window_set
        _started, _started_windows, _opened, _last = None, frozenset(), None, None


def answer() -> dict[str, Any]:
    """Every waiting setting, whether a reopen can be done here, and the HTTP port the next
    start listens on - the one the Manager has to move to when that is what changed."""
    store = get_ini_config()
    with _lock:
        started, opened, started_windows = _started, _opened, _started_windows
    waiting: list[dict[str, Any]] = []
    if started is not None:
        # A reopen builds no new API, so it cannot serve a window that was not there.
        window_set_moved = _window_set(store) != started_windows
        for option in config_service.settable(_own_windows(store)):
            if option.applies == config_schema.APPLIES_RESTART:
                was, waits = started, WAITS_RESTART
            elif option.applies == config_schema.APPLIES_WINDOWS and opened is not None:
                # Against the start while the window set differs from it: a reopen does
                # not serve a window that was not there, so it must not clear the entry.
                was, waits = ((started, WAITS_RESTART) if window_set_moved
                              else (opened, WAITS_REOPEN))
            else:
                continue
            key = f"{option.section}.{option.key}"
            now = store.value(option.section, option.key)
            if key in was and was[key] != now:
                waiting.append({
                    "kind": KIND_SETTING, "section": option.section, "key": option.key,
                    "label": option.label or option.key, "label_key": f"{option.keys}.label",
                    "waits_for": waits, "in_effect": was[key], "set": now})
        waiting += _extensions()
    return {"waiting": waiting,
            "reopen": (lifecycle.performable(lifecycle.FRONTEND, lifecycle.RESTART)
                       and not lifecycle.unavailable(lifecycle.FRONTEND, lifecycle.RESTART)),
            "http_port": store.value("network", "http_port")}


def refresh() -> None:
    """Tell the stream when the answer is not the one it last heard."""
    global _last
    with _lock:
        current = answer()
        if current == _last:
            return
        _last = current
        events.emit(events.CONFIG_WAITING_CHANGED, **current)


def _read(store: ConfigStore, applies: set[str]) -> dict[str, Any]:
    return {f"{option.section}.{option.key}": store.value(option.section, option.key)
            for option in config_service.settable(_own_windows(store))
            if option.applies in applies}


def _extensions() -> list[dict[str, Any]]:
    return [{"kind": KIND_EXTENSION, "section": "extensions", "key": record.name,
             "label": record.display_name, "label_key": "",
             "waits_for": WAITS_RESTART, "in_effect": False, "set": True}
            for record in extensions.records()
            if record.enabled and (record.files_changed or (
                record.state == extensions.OFF
                and record.why == extension_host.STARTS_AT_RESTART))]
