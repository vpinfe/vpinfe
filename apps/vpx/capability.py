"""What a given Visual Pinball install can do, established by looking at it.

Never by comparing version numbers. 10.8.0 is the only release in the 10.8 line and
every 10.8.1 tag is a prerelease, so builds eighteen months apart both answer "10.8.1"
while differing by everything we would be gating on.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from common.apps.contract import Availability

from . import plugins
from .config import settings_file

PLUGINS = "plugins"
PER_TABLE_SETTINGS = "per_table_settings"


class VPXCapability:
    def probe(self, settings: Mapping[str, Any]) -> Mapping[str, Availability]:
        bin_path = str(settings.get("bin_path") or "").strip()
        return {
            PLUGINS: self._plugins(bin_path, settings),
            # Read by 10.8.0 and by master alike, so there is nothing to gate on.
            PER_TABLE_SETTINGS: Availability(True),
        }

    def _plugins(self, bin_path: str, settings: Mapping[str, Any]) -> Availability:
        """The plugin architecture postdates 10.8.0, where B2S is built in and there are
        no plugin sections at all. Evidence is the folder the program loads plugins from,
        or the ini already carrying one."""
        directory = plugins.folder(bin_path)
        if directory is not None and directory.is_dir():
            return Availability(True)

        ini_path = settings_file(settings)
        if ini_path is not None and _declares_a_plugin(ini_path):
            return Availability(True)

        return Availability(False, "no_plugins")


def _declares_a_plugin(ini_path: Path) -> bool:
    try:
        with ini_path.open(encoding="utf-8", errors="replace") as handle:
            return any(line.lstrip().startswith("[Plugin.") for line in handle)
    except OSError:
        return False
