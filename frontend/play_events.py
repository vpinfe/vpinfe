"""What the frontend does about a launch, wherever the launch came from.

The window messages are subscribers rather than part of the launch, so a launch from
the Remote Control page or the API drives the windows the same way the wheel does.

Registered once per process against the shared bridge - every API instance sends
through the same one, so registering per window would send each message three
times.
"""

from __future__ import annotations

import logging
import sys
import threading
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from common import events
from common.config.config_access import cfg_bool
from common.config.config_store import ConfigStore
from common.games.game import GameRecord
from common.host import front, launch_state
from common.players import player_pictures
from frontend import config_api
from frontend.last_game import save_last_launched

if TYPE_CHECKING:
    from frontend.chromium_manager import ChromiumManager
    from frontend.device_channel import DeviceChannel

logger = logging.getLogger("vpinfe.frontend.play_events")

# How long a run of game changes has to go quiet before the windows are sent back for the
# payload. An import touches one game at a time, and each refresh costs every window a
# rebuild of the whole list, so the burst is answered once.
CHANGE_COALESCE_SECONDS = 0.5

_registered = False
_bridge = None
_browser = None
_ini_config = None
_change_timer = None
_change_lock = threading.Lock()


# The same map vpinfe-core.js keeps as MESSAGE_TYPE_ALIASES, and it has to stay the same:
# a theme matches on whichever spelling it was written against, so both have to arrive.
# Empty now that the backend messages carry the Table* names 2.x published, and it has to
# be filled again the next time one of them is renamed.
_LEGACY_MESSAGE_TYPES: dict[str, str] = {}


def _mark_views_stale() -> None:
    """Tell the shared view the library moved, before the windows are sent for it.

    Set here rather than in each window's handler: the broadcast is one event and the
    refresh behind it should be one refresh, however many windows answer it.
    """
    if _bridge is None:
        return
    for api in list(getattr(_bridge, "_api_instances", {}).values()):
        library = getattr(api, "library", None)
        if library is not None:
            library.mark_stale()


def _broadcast(message: dict) -> None:
    if _bridge is None:
        return
    if message.get("type") == "TableDataChange":
        _mark_views_stale()
    _bridge.send_event_all_with_iframe(message)
    legacy = _LEGACY_MESSAGE_TYPES.get(message.get("type"))
    if legacy is not None:
        _bridge.send_event_all_with_iframe({**message, "type": legacy})


def on_launching(*, game: GameRecord | None = None, table_id: str = "",
                 source: str = "", **_payload: Any) -> None:
    """Suppress frontend input and record where the player was, which a recording
    does not change.

    Runs after every hook, so a peripheral that refused the launch has already
    stopped this - input is never suppressed for a launch that is not happening.
    """
    if (game is not None and _ini_config is not None
            and source != launch_state.SOURCE_CAPTURE):
        save_last_launched(_ini_config, game, table_id)
    launching: dict[str, Any] = {"type": "TableLaunching", "table_id": table_id}
    if _ini_config is not None:
        launching["loading_screen"] = config_api.get_loading_screen_config(_ini_config)
    _broadcast(launching)
    if (sys.platform == "win32" and _browser is not None
            and not front.enabled(_ini_config)):
        # VPX pauses whenever its player window loses focus, and Windows will not let a
        # process hand the foreground to one it spawned - so a table came up paused until
        # the user alt-tabbed. Out of the way, and VPX takes the foreground itself. With
        # Experimental Table Focus on, the hand-over does this instead.
        try:
            _browser.minimize_all_windows()
        except Exception:
            logger.exception("Could not get the frontend windows out of VPX's way")


def on_launched(**_payload: Any) -> None:
    """The table is actually up, not merely started."""
    _broadcast({"type": "TableRunning"})


def on_exited(**_payload: Any) -> None:
    """Always reached once a launch was announced, so input always comes back."""
    _broadcast({"type": "TableLaunchComplete"})
    if sys.platform == "darwin" and _browser is not None:
        try:
            _browser.activate_all_mac()
        except Exception:
            logger.exception("Could not bring the frontend windows back to the front")
    if sys.platform == "win32" and _browser is not None:
        # Reached on every path out, including a launch that failed, so a cabinet with
        # no keyboard is never left staring at minimized windows.
        try:
            _browser.restore_all_windows()
        except Exception:
            logger.exception("Could not bring the frontend windows back")


def on_play_recorded(**_payload: Any) -> None:
    """Send the windows back for the payload, now that the session is in it.

    Not on_exited: the exit is announced before the runtime and the score are written.
    """
    _broadcast({"type": "TableDataChange"})


def library_arrived() -> None:
    """A library that was waiting on its folders has been read: every window reads it."""
    on_game_changed()


def on_game_changed(**_payload: Any) -> None:
    """A game or the collections file changed. Coalesced, never immediate."""
    global _change_timer
    with _change_lock:
        if _change_timer is not None:
            _change_timer.cancel()
        _change_timer = threading.Timer(CHANGE_COALESCE_SECONDS, _flush_game_changes)
        _change_timer.daemon = True
        _change_timer.start()


def players_for_themes(state: dict | None) -> dict[str, Any]:
    """The roster as a theme reads it: each player's `picture_version` becomes the
    address of the picture, or None where they have none."""
    return {"players": [
        {**{key: value for key, value in one.items() if key != "picture_version"},
         "picture": player_pictures.address(one["id"], one.get("picture_version"))}
        for one in (state or {}).get("players", [])]}


def on_players_changed(*, state: dict | None = None, **_payload: Any) -> None:
    """Who is kept, who is a guest, and who is up. Forwarded into every overlay iframe
    as well as the theme windows, which is how the main menu's Player list keeps up."""
    _broadcast({"type": "PlayersChanged", "state": players_for_themes(state)})


def on_settings_changed(*, keys: Sequence[str] = (), ini_config: ConfigStore | None = None,
                        **_payload: Any) -> None:
    """Tell the windows about the settings they act on while they are open.

    The mute switch carries its value, because the window has nothing to ask. Attract mode
    sends only that something changed: the window asks for the whole set, so a change to
    one of its keys and a change to all of them are one case.
    """
    store = ini_config or _ini_config
    if "behavior.mute_audio" in keys and store is not None:
        _broadcast({"type": "AudioMuteChanged",
                    "muted": cfg_bool(store, "behavior", "mute_audio", False)})
    if any(key.startswith("behavior.attract_") for key in keys):
        _broadcast({"type": "AttractSettingsChanged"})


def _flush_game_changes() -> None:
    global _change_timer
    with _change_lock:
        _change_timer = None
    _broadcast({"type": "TableDataChange"})


def register(ws_bridge: DeviceChannel, frontend_browser: ChromiumManager | None = None,
             ini_config: ConfigStore | None = None) -> None:
    """Attach the frontend's reaction to the game lifecycle. Idempotent."""
    global _registered, _bridge, _browser, _ini_config
    _bridge = ws_bridge
    _browser = frontend_browser
    _ini_config = ini_config
    if _registered:
        return

    events.subscribe(events.TABLE_LAUNCHING, on_launching)
    events.subscribe(events.TABLE_LAUNCHED, on_launched)
    events.subscribe(events.TABLE_EXITED, on_exited)
    events.subscribe(events.TABLE_PLAY_RECORDED, on_play_recorded)
    events.subscribe(events.GAME_CHANGED, on_game_changed)
    events.subscribe(events.COLLECTIONS_CHANGED, on_game_changed)
    events.subscribe(events.PLAYERS_CHANGED, on_players_changed)
    events.subscribe(events.SETTINGS_CHANGED, on_settings_changed)
    _registered = True


def reset_for_tests() -> None:
    global _registered, _bridge, _browser, _ini_config, _change_timer
    _registered = False
    _bridge = _browser = _ini_config = None
    with _change_lock:
        if _change_timer is not None:
            _change_timer.cancel()
        _change_timer = None
