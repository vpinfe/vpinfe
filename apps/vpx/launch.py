"""Building Visual Pinball's command line, and how one of its sessions is watched."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common.apps.contract import (
    SESSION_CHILD_WITH_READINESS,
    Entry,
    Session,
    arguments,
)

from .config import own_file, possible_logs

# VPX writes this once the table is actually up. Before it, the process exists but the
# player is looking at nothing.
STARTUP_MARKER = "Startup done"


def _is_own(named: str, bin_path: str) -> bool:
    own = own_file(bin_path)
    if own is None:
        return False
    try:
        return Path(named).expanduser().resolve() == own.resolve()
    except OSError:
        return False


class VPXLaunch:
    """`settings` is the launcher's, with `bin_path` already resolved to the executable -
    on macOS what a person picks is a `.app` directory, and VPX is inside it."""

    def command(self, entry: Entry, settings: Mapping[str, Any]) -> list[str]:
        """`-play <table>` is last and the Settings File is the only `-ini`, so long as the
        launcher's Arguments, which go between the two, hold neither: a save and a launch
        refuse both (`Field.taken`). VPX takes the last `-ini` it is given, so
        a second would quietly replace the Settings File.
        """
        bin_path = str(settings.get("bin_path") or "")
        cmd = [bin_path]

        named = str(settings.get("ini_path") or "").strip()
        if named and not _is_own(named, bin_path):
            cmd.extend(["-ini", named])

        cmd.extend(arguments(entry, settings))
        cmd.extend(["-play", str(entry.table)])
        return cmd

    def session(self, settings: Mapping[str, Any]) -> Session:
        return Session(kind=SESSION_CHILD_WITH_READINESS,
                       readiness_marker=STARTUP_MARKER,
                       log_files=_log_files(settings))


def _log_files(settings: Mapping[str, Any]) -> tuple[str, ...]:
    """The logs VPX may be writing for this launcher. Two names for one file count once:
    each line is delivered once per file followed."""
    unique = {os.path.normcase(os.path.realpath(one)): str(one)
              for one in possible_logs(settings)}
    return tuple(unique.values())
