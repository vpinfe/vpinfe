"""What core knows about an extension, and where that extension keeps its own settings.

Two homes, because there are two owners. Whether an extension is switched off is a core
setting, `extensions.switched_off`: core has to know before it loads anything, and
reading a file per extension to answer "what am I not loading" is worse than reading one
list. What an extension is configured with is nobody's business but its own, so it gets
a file.

A file each rather than a namespace inside one, and the reason is the same one that keeps
these out of the core settings: a namespace in somebody else's file is a weaker form of
"own" than a file. One bad write must not take every extension's settings with it.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from common.atomic_write import write_atomic
from common.config.config_store import ConfigStore
from common.config.paths import CONFIG_DIR, get_ini_config

logger = logging.getLogger("vpinfe.common.extensions.store")

# One file per extension, named for it. Not beside its code: an extension the build
# ships has no directory here at all, so settings need a home that does not depend on
# where the code came from.
SETTINGS_DIR = CONFIG_DIR / "extension_settings"

SWITCHED_OFF_SECTION = "extensions"
SWITCHED_OFF_KEY = "switched_off"
# In an extension's own file: the accounts it holds, `{player_id: {key: value}}`. Not a
# setting, so it is neither read as one nor writable as one.
ACCOUNTS_KEY = "accounts"
# Also in it: what core last saw the extension declare (`{"settings": true}`), for a page
# that has to say so while the extension is not running. Reserved the same way.
DECLARED_KEY = "declared"
RESERVED = frozenset({ACCOUNTS_KEY, DECLARED_KEY})

# The name is already restricted to this by the manifest. Checked again here because
# this one builds a path out of it, and a name that could contain a separator would be
# a way to write outside the directory.
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,39}$")


def _names(raw: Any) -> list[str]:
    """However the value arrives - a list, or the comma string a parser holds."""
    if isinstance(raw, str):
        raw = raw.split(",")
    return [str(one).strip() for one in (raw or []) if str(one).strip()]


class ExtensionStore:
    def __init__(self, folder: Path | str | None = None,
                 config: ConfigStore | None = None) -> None:
        """Without `folder` this is the install's own. With one, the settings files and
        the core settings both live in it: a caller that relocates one and silently leaves
        the other at this machine's is not isolated at all, which is a thing to find out
        here rather than in whatever it was writing into."""
        self._folder = Path(folder) if folder is not None else None
        self._config = config
        self.settings_dir = (SETTINGS_DIR if self._folder is None
                             else self._folder / SETTINGS_DIR.name)
        self._lock = threading.RLock()

    def _core(self) -> ConfigStore:
        if self._config is not None:
            return self._config
        if self._folder is None:
            return get_ini_config()
        self._config = ConfigStore(str(self._folder / "vpinfe.json"))
        return self._config

    # -- what core knows -----------------------------------------------------

    def enabled(self, name: str) -> bool:
        """An extension nobody has said anything about is on: installing one is the act
        of asking for it."""
        with self._lock:
            return str(name or "").strip() not in self._switched_off(self._core())

    def set_enabled(self, name: str, on: bool) -> None:
        wanted = str(name or "").strip()
        with self._lock:
            config = self._core()
            held = self._switched_off(config)
            if on == (wanted not in held):
                return
            kept = [one for one in held if one != wanted]
            config.set_value(SWITCHED_OFF_SECTION, SWITCHED_OFF_KEY,
                             kept if on else [*kept, wanted])
            config.save()

    @staticmethod
    def _switched_off(config: ConfigStore) -> list[str]:
        return _names(config.value(SWITCHED_OFF_SECTION, SWITCHED_OFF_KEY))

    # -- what the extension knows --------------------------------------------

    def settings(self, name: str) -> dict[str, str]:
        with self._lock:
            raw = self._read_settings(name)
        return {str(key): str(value) for key, value in raw.items() if key not in RESERVED}

    def set_setting(self, name: str, key: str, value: str) -> None:
        wanted = str(key or "").strip()
        if not wanted:
            return
        if wanted in RESERVED:
            logger.error("Refusing a setting named %r for %s: core keeps its own "
                         "records there", wanted, name)
            return
        with self._lock:
            held = self._read_settings(name)
            held[wanted] = str(value)
            self._write_settings(name, held)

    def declared(self, name: str) -> dict[str, Any]:
        """What core last saw this extension declare; empty before it has run."""
        with self._lock:
            raw = self._read_settings(name).get(DECLARED_KEY)
        return dict(raw) if isinstance(raw, dict) else {}

    def set_declared(self, name: str, what: dict[str, Any]) -> None:
        with self._lock:
            held = self._read_settings(name)
            held[DECLARED_KEY] = dict(what)
            self._write_settings(name, held)

    def accounts(self, name: str) -> dict[str, dict[str, str]]:
        """Every account this extension holds for a kept player, by player id."""
        with self._lock:
            return _accounts_in(self._read_settings(name))

    def set_account(self, name: str, player_id: str, values: dict[str, str]) -> None:
        """Replace one player's account. Empty values remove it."""
        wanted = str(player_id or "").strip()
        if not wanted:
            return
        with self._lock:
            held = self._read_settings(name)
            accounts = _accounts_in(held)
            if values:
                accounts[wanted] = {str(key): str(value) for key, value in values.items()}
            elif accounts.pop(wanted, None) is None:
                return
            if accounts:
                held[ACCOUNTS_KEY] = accounts
            else:
                held.pop(ACCOUNTS_KEY, None)
            self._write_settings(name, held)

    def forget_holder(self, player_id: str) -> list[str]:
        """Remove a player's account from every extension's file. Answers the extensions
        that held one."""
        wanted = str(player_id or "").strip()
        held_by = []
        with self._lock:
            for path in sorted(self.settings_dir.glob("*.json")):
                if _NAME.match(path.stem) and wanted in self.accounts(path.stem):
                    self.set_account(path.stem, wanted, {})
                    held_by.append(path.stem)
        return held_by

    def forget(self, name: str) -> None:
        """Drop everything about an extension that has been removed: it is on again if it
        is ever installed, and its settings file is deleted."""
        with self._lock:
            self.set_enabled(name, True)
            path = self._settings_path(name)
            if path is not None:
                path.unlink(missing_ok=True)

    # -- the files -----------------------------------------------------------

    def _settings_path(self, name: str) -> Path | None:
        wanted = str(name or "").strip()
        if not _NAME.match(wanted):
            logger.error("Refusing settings for %r, which is not an extension name",
                         name)
            return None
        return self.settings_dir / f"{wanted}.json"

    def _read_settings(self, name: str) -> dict:
        path = self._settings_path(name)
        if path is None:
            return {}
        try:
            with open(path, encoding="utf-8") as handle:
                held = json.load(handle) or {}
        except FileNotFoundError:
            return {}
        except Exception:
            # One extension's file, so one extension's settings. This is the whole
            # reason they are not in one file together.
            logger.exception("Could not read %s; treating it as empty", path)
            return {}
        return dict(held) if isinstance(held, dict) else {}

    def _write_settings(self, name: str, held: dict) -> None:
        path = self._settings_path(name)
        if path is None:
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, lambda handle: json.dump(held, handle, indent=2))


def _accounts_in(held: dict) -> dict[str, dict[str, str]]:
    raw = held.get(ACCOUNTS_KEY)
    if not isinstance(raw, dict):
        return {}
    return {str(player): {str(key): str(value) for key, value in values.items()}
            for player, values in raw.items() if isinstance(values, dict)}


_store: ExtensionStore | None = None


def get_extension_store() -> ExtensionStore:
    """This install's extension settings. One per process, the way the launchers are."""
    global _store
    if _store is None:
        _store = ExtensionStore()
    return _store
