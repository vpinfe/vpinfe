"""The commands that run around a table, and what they are told about it.

Two sets, nested: the install's run outside the launcher's. That is execution order and
not evidence they are one thing - the install's are about the machine, and run whichever
launcher plays the table; the launcher's are about one way of playing.

**If VPinFE stops between the two halves, the second never runs.** So the fact that the
first ran is written down, and the next start finishes what it began before doing
anything else. That turns a machine left muted, or with a service stopped, from something
somebody has to work out into something that fixes itself.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from common import apps, tokens
from common.config.config_access import cfg_bool, cfg_get, cfg_int
from common.config.config_store import ConfigStore
from common.devices import device_registry
from common.games import tables
from common.games.game import Game
from common.games.launchers import Launcher
from common.host import bundled_libraries, commands
from common.i18n import t
from common.players import players

logger = logging.getLogger("vpinfe.common.host.table_commands")

# What ran and has not been finished. One note: a machine plays one table at a time.
PENDING_KEY = "unfinished_commands"


@dataclass
class Around:
    """One table's two halves, and what the second of them will be told."""

    values: dict[str, str] = field(default_factory=dict)
    timeout: int = commands.DEFAULT_TIMEOUT
    install_after: str = ""
    launcher_after: str = ""
    ran: bool = False


def _values(game: Game, playing: apps.Entry, launcher: Launcher | None,
            up: list[players.Player] | None = None) -> dict[str, str]:
    """What a command about this table may say. Strings, all of them, because they are
    going into an argument list.

    Every name the context offers resolves to something, empty included: a command
    mentioning one that happens not to apply to this entry should run with a blank, not
    fail. An entry with no file has no `{table}`, and that is normal.
    """
    entries = tables.table_entries(getattr(game, "meta_config", {}))
    entry = entries.get(playing.entry_id) or {}
    settings = ({one.key: launcher.value(one.key) for one in launcher.fields()}
                if launcher is not None else {})
    up = players.get_roster().up() if up is None else up
    built = {
        "game_dir": str(playing.game_dir or ""),
        "table": str(playing.table or ""),
        "table_stem": Path(playing.table).stem if playing.table else "",
        "game_name": str(game.game_dir_name or ""),
        "id": str(playing.entry_id or ""),
        # The app's own name for the entry. Something in the folder has none.
        "key": str(playing.key or ""),
        "rom": str(entry.get("rom") or ""),
        "program": str(settings.get("bin_path") or ""),
        "settings_file": str((launcher.in_effect("ini_path") if launcher else "") or ""),
        "location": str(getattr(game, "location_id", "") or ""),
        "player": up[0].initials if len(up) == 1 else "",
    }
    return tokens.filled(tokens.TABLE, built)


def before(game: Game, playing: apps.Entry, launcher: Launcher | None,
           ini_config: ConfigStore, up: list[players.Player] | None = None) -> Around:
    """The install's commands and then this launcher's, in that order.

    A failure stops the launch only where somebody said it should. What that means is a
    share that has to be mounted against an audio route that would be nice to switch,
    and only they know which they wrote.
    """
    around = Around(values=_values(game, playing, launcher, up))
    around.timeout = cfg_int(ini_config, "commands", "timeout",
                             commands.DEFAULT_TIMEOUT)
    around.install_after = cfg_get(ini_config, "table_commands", "on_exit", "")
    settings = ({one.key: launcher.value(one.key) for one in launcher.fields()}
                if launcher is not None else {})
    around.launcher_after = str(settings.get("on_table_exit") or "")

    _run(cfg_get(ini_config, "table_commands", "on_start", ""), around,
         required=cfg_bool(ini_config, "table_commands", "start_required", False))
    _run(str(settings.get("on_table_start") or ""), around,
         required=_as_bool(settings.get("on_start_required")), launcher=launcher)

    if around.ran:
        _remember(around)
    return around


def after(around: Around, *, started_at: float | None = None) -> None:
    """The other half, whenever the first ran. The launcher's first, then the install's:
    it nests, so what was put on last comes off first."""
    if not around.ran:
        return
    values = dict(around.values)
    values["duration"] = str(int(time.time() - started_at)) if started_at else "0"
    values.setdefault("exit_code", "")
    values = tokens.filled(tokens.TABLE, values, after=True)
    for text in (around.launcher_after, around.install_after):
        if not str(text or "").strip():
            continue
        commands.run(text, values, context=tokens.TABLE, after=True,
                     timeout=around.timeout, on_failure=commands.BEST_EFFORT,
                     env=bundled_libraries.for_child())
    _forget()


def _run(text: str, around: Around, *, required: bool,
         launcher: Launcher | None = None) -> None:
    """One setting's commands: the install's own, or `launcher`'s."""
    if not str(text or "").strip():
        return
    try:
        outcome = commands.run(
            text, around.values, context=tokens.TABLE, timeout=around.timeout,
            on_failure=commands.REQUIRED if required else commands.BEST_EFFORT,
            env=bundled_libraries.for_child())
    except commands.CommandRefusedError as exc:
        # Whatever did run still has to be undone, so this counts as having run.
        around.ran = True
        _remember(around)
        logger.warning("A command before the table failed, so it will not start: %s", exc)
        raise commands.CommandRefusedError(
            t("error.table_commands.launcher_command_failed",
              launcher_name=launcher.display_name)
            if launcher is not None
            else t("error.table_commands.global_failed")) from exc
    around.ran = around.ran or outcome.ran


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on")


def _remember(around: Around) -> None:
    """Write down that the first half ran, so a VPinFE that dies here is a restart
    rather than a machine somebody has to put back by hand."""
    try:
        device_registry.get_device_registry().set_self(**{PENDING_KEY: {
            "values": around.values, "timeout": around.timeout,
            "install_after": around.install_after,
            "launcher_after": around.launcher_after,
        }})
    except Exception:  # noqa: BLE001 - never the thing that stops a launch
        logger.exception("Could not note that the pre-table commands ran")


def _forget() -> None:
    try:
        device_registry.get_device_registry().set_self(**{PENDING_KEY: None})
    except Exception:  # noqa: BLE001
        logger.exception("Could not clear the note")


def finish_unfinished() -> bool:
    """Run the half that never ran, before anything else on the next start.

    A machine left muted, or with a service stopped, is otherwise something somebody has
    to work out. Returns whether there was anything to finish.
    """
    payload = device_registry.get_device_registry().self_get(PENDING_KEY)
    if payload is None:
        return False
    if not isinstance(payload, dict):
        logger.error("What was left unfinished is not readable; clearing it")
        _forget()
        return False

    logger.info("Finishing the commands a previous run left")
    after(Around(values=dict(payload.get("values") or {}),
                 timeout=int(payload.get("timeout") or commands.DEFAULT_TIMEOUT),
                 install_after=str(payload.get("install_after") or ""),
                 launcher_after=str(payload.get("launcher_after") or ""),
                 ran=True))
    return True
