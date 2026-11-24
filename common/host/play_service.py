"""Starting and stopping a table on this host.

Both halves in one place because they are one question - what is this machine playing -
and every surface that asks it asks the same way.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from common import service_errors
from common.config.paths import get_ini_config
from common.devices import device_client
from common.failures import why
from common.games import game_lens
from common.host import launch, launch_state
from common.runtime import lifecycle

logger = logging.getLogger("vpinfe.common.host.play_service")


def stop_playing(reason: str = "asked over the API") -> dict:
    """Close whatever is running, and say what was closed.

    Through the lifecycle scope rather than reaching for the process, so this takes the
    same confirm-announce-perform path as every other stop and the other surfaces hear
    `lifecycle.acting`. The name is read before the stop because the state clears as
    soon as the process dies.
    """
    state = launch_state.current()
    if not state.launching:
        # Nothing to announce either: a lifecycle request with no table would tell every
        # surface a table was closing when none was.
        return {"stopped": False, "game_name": None}

    went_ahead = device_client.local().request(
        lifecycle.TABLE, lifecycle.STOP,
        origin=lifecycle.Origin(lifecycle.SURFACE_API),
        reason=reason)
    return {"stopped": bool(went_ahead),
            "game_name": state.game_name if went_ahead else None}


def start(game_id: str, table: str | None = None) -> dict:
    """Start a game and answer once it is starting, not once it is over.

    The same path the wheel and the Remote Control page take, so a launch from anywhere
    counts as a play and releases the peripherals like any other.
    """
    game = game_lens.game_or_refuse(game_id)
    ini_config = get_ini_config()
    try:
        resolved = launch.check_launchable(game, ini_config, table)
    except launch.LaunchBusyError as exc:
        raise service_errors.BlockedError(why(exc)) from exc
    except launch.UnknownTableError as exc:
        raise service_errors.RefusedError(why(exc), details={"file": table}) from exc
    except launch.LaunchUnavailableError as exc:
        raise service_errors.UnavailableError(why(exc)) from exc

    def run() -> None:
        try:
            launch.launch_game(game, ini_config, source=launch_state.SOURCE_API,
                               table=table)
        except Exception:
            logger.exception("Launch of %s failed", game_id)

    threading.Thread(target=run, daemon=True,
                     name=f"api-launch-{game_id[:8]}").start()
    return {"launching": True, "game_id": game_id, "file": Path(resolved).name}
