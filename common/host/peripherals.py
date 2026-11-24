"""DOF and real-DMD, driven by game lifecycle events.

Handing the peripherals over for a launch is a hook: VPX drives the same hardware, so
releasing them is part of launching rather than a notification about it. Reacting
to a selection is not - see the subscribers below.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from typing import Any

from common import events
from common.config.config_store import ConfigStore
from common.games.game import Game
from common.games.game_metadata import game_frontend_dof_event
from common.host import launch_state, real_dmd
from common.host.dof_service import (
    restart_dof_service_if_enabled,
    send_frontend_dof_event,
    start_dof_service_if_enabled,
    stop_dof_service,
)
from common.host.libdmdutil_service import (
    restart_libdmdutil_service_if_enabled,
    show_image,
    show_video,
    stop_libdmdutil_service,
)

logger = logging.getLogger("vpinfe.common.host.peripherals")

# Ahead of anything else that hooks a launch: the peripherals come first.
PRIORITY = 10

# The DOF setting its helper reads; its other key is for the config tool.
_DOF_KEYS = frozenset({"dof.enabled"})

_registered = False
_realdmd_updater: real_dmd.RealDmdUpdater | None = None


def release_for_launch(**_payload: Any) -> None:
    """Hand the peripherals over before the table starts. A playing video stops with the
    panel's service, before the panel is released."""
    stop_dof_service()
    stop_libdmdutil_service(clear=False)


def reacquire_after_exit(*, ini_config: ConfigStore | None = None,
                         **_payload: Any) -> None:
    """Take the peripherals back once the table has exited, and put the table's art on the
    panel again."""
    start_dof_service_if_enabled(ini_config)
    if _realdmd_updater is not None:
        _realdmd_updater.show_again()


def play_dof_effect(*, game: Game | None = None, ini_config: ConfigStore | None = None,
                    **_payload: Any) -> None:
    """Fire the table's DOF effect - solenoids and lights."""
    if game is None:
        return
    send_frontend_dof_event(ini_config, game_frontend_dof_event(game))


def show_realdmd_art(*, game: Game | None = None, ini_config: ConfigStore | None = None,
                     **_payload: Any) -> None:
    """Put the table's art on the real DMD panel."""
    if game is None or ini_config is None:
        return
    _updater(ini_config).queue_update(
        game.game_dir_name or "",
        real_dmd.get_realdmd_art_for_game(game, ini_config),
    )


def apply_settings(keys: Sequence[str], ini_config: ConfigStore | None) -> None:
    """Restart the helper of each peripheral whose setting changed.

    While a table plays nothing restarts: VPX holds the hardware, and the next table
    selection starts each again from the saved settings.
    """
    if ini_config is None or launch_state.current().launching:
        return
    if _DOF_KEYS.intersection(keys):
        restart_dof_service_if_enabled(ini_config)
    if any(key.startswith("real_dmd.") for key in keys):
        restart_libdmdutil_service_if_enabled(ini_config)
        # The updater the frontend made, reading the store the frontend holds. One made
        # here would keep this save's store, which is stale by the next save.
        if _realdmd_updater is not None:
            _realdmd_updater.show_again()


def on_settings_changed(*, keys: Sequence[str] = (), ini_config: ConfigStore | None = None,
                        **_payload: Any) -> None:
    """Off the saving request: stopping a helper waits for its process to end."""
    if not (_DOF_KEYS.intersection(keys)
            or any(key.startswith("real_dmd.") for key in keys)):
        return
    threading.Thread(target=apply_settings, args=(keys, ini_config),
                     name="peripherals-settings", daemon=True).start()


def _updater(ini_config: ConfigStore) -> real_dmd.RealDmdUpdater:
    """One updater for the process, not one per frontend window.

    Three windows each hold an API instance, and on the bus every subscriber hears every
    event - so one updater per window would write the panel three times over.
    """
    global _realdmd_updater
    if _realdmd_updater is None:
        _realdmd_updater = real_dmd.RealDmdUpdater(ini_config, "shared", show_image, show_video)
    return _realdmd_updater


def register() -> None:
    """Attach to the bus. Safe to call more than once."""
    global _registered
    if _registered:
        return
    events.hook(events.TABLE_LAUNCHING, release_for_launch, priority=PRIORITY)
    events.hook(events.TABLE_EXITED, reacquire_after_exit, priority=PRIORITY)
    # Two peripherals, two subscribers, one trigger. Neither knows the other exists,
    # so a DOF failure still leaves the art on the panel and vice versa - and a
    # third peripheral is a third subscriber rather than an edit here.
    events.subscribe(events.GAME_SELECTED, play_dof_effect)
    events.subscribe(events.GAME_SELECTED, show_realdmd_art)
    events.subscribe(events.SETTINGS_CHANGED, on_settings_changed)
    _registered = True
    logger.debug("Peripherals attached to table lifecycle events")


def reset_for_tests() -> None:
    global _registered, _realdmd_updater
    _registered = False
    _realdmd_updater = None
