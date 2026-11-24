"""A launcher's command line as text a person reads or pastes into this device's shell."""

from __future__ import annotations

import logging
import shlex
import subprocess
import sys
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from common import apps, tokens
from common.apps.contract import arguments
from common.launcher_path import resolve_launcher_path

if TYPE_CHECKING:
    from common.games import launchers

logger = logging.getLogger("vpinfe.common.host.command_line")


def quoted(argument: str) -> str:
    """One argument, quoted the way this device's shell needs it. A `{name}` alone is
    left bare, so it reads as the name it is."""
    if sys.platform == "win32":
        return subprocess.list2cmdline([argument])
    plain = tokens.TOKEN.sub("x", argument)
    if argument and shlex.quote(plain) == plain:
        return argument
    return shlex.quote(argument)


def readable(argv: Sequence[str]) -> str:
    return " ".join(quoted(str(one)) for one in argv)


def shown(launcher: launchers.Launcher) -> list[dict[str, Any]] | None:
    """What a launcher runs, one piece to an argument, with every name as written and each
    of the launcher's own Arguments marked `yours`. None for an app that takes no
    Arguments, and while a launch would refuse them (`apps.arguments_problem`)."""
    app = apps.get(launcher.app)
    declared = launcher.fields()
    if app is None or app.launch is None or not any(one.key == "args" for one in declared):
        return None
    settings = {one.key: launcher.value(one.key) for one in declared}
    if apps.arguments_problem(launcher.app, str(settings.get("args") or "")):
        return None
    configured = str(settings.get("bin_path") or "").strip()
    settings["bin_path"] = _program(configured) if configured else tokens.written("program")
    entry = apps.Entry(table=tokens.written("table"),
                       names={one.name: tokens.written(one.name)
                              for one in tokens.offered(tokens.TABLE)})
    try:
        cmd = app.launch.command(entry, settings)
        own = arguments(entry, settings)
    except ValueError:
        return None
    except Exception:  # noqa: BLE001 - an extension's command must not stop the listing
        logger.exception("%s could not say what it runs", launcher.display_name)
        return None
    start = next((at for at in range(1, len(cmd) - len(own) + 1)
                  if own and cmd[at:at + len(own)] == own), -1)
    return [{"text": quoted(one),
             "yours": start != -1 and start <= at < start + len(own)}
            for at, one in enumerate(cmd)]


def _program(configured: str) -> str:
    """The program a launcher runs, or as typed where its `~user` is nobody on this device."""
    try:
        return str(resolve_launcher_path(configured))
    except RuntimeError:
        return configured
