"""Who and what is private on this install, read from the install's own stores.

Every reader answers with what it found and an empty tuple when it could not read: a
store that will not open must not stop a package from being built, and the pattern pass
still covers what a missing literal would have.
"""

from __future__ import annotations

import getpass
import logging
import os
import socket
import subprocess
import sys
from collections.abc import Callable, Iterable
from pathlib import Path

from common.support.redact import Identity

logger = logging.getLogger("vpinfe.common.support.identity")

# A value shorter than this matches inside ordinary words, so it is not hidden as a name.
MIN_ACCOUNT_VALUE = 3
MAX_ACCOUNT_VALUE = 64


def build_identity() -> Identity:
    """This install's private names and paths. Never raises."""
    homes, logins = _account()
    return Identity(
        homes=homes,
        logins=logins,
        hosts=_hosts(),
        devices=_devices(),
        domains=_domains(),
        players=_players(),
        identifiers=_identifiers(),
        libraries=_libraries(),
        tables=_tables())


def _guard(read: Callable[[], Iterable[str]]) -> tuple[str, ...]:
    try:
        found = [str(item).strip() for item in read()]
    except Exception:  # noqa: BLE001 - one unreadable store is not a failed package
        logger.debug("Could not read %s", getattr(read, "__name__", "a store"), exc_info=True)
        return ()
    return tuple(dict.fromkeys(item for item in found if item))


def _account() -> tuple[tuple[str, ...], tuple[str, ...]]:
    homes = _guard(lambda: [str(Path.home()), os.environ.get("USERPROFILE", ""),
                            os.environ.get("HOME", "")])
    logins = _guard(lambda: [getpass.getuser(), os.environ.get("USERNAME", ""),
                             os.environ.get("USER", ""), os.environ.get("LOGNAME", "")])
    return homes, logins


def _hosts() -> tuple[str, ...]:
    def names() -> list[str]:
        found = [socket.gethostname(), socket.getfqdn(), os.environ.get("COMPUTERNAME", ""),
                 os.environ.get("USERDOMAIN", ""), _computer_name()]
        for name in list(found):
            short = name.split(".")[0]
            if short:
                found += [short, f"{short}.local", f"{short}.lan"]
        found.append(_install_name())
        return found

    return _guard(names)


def _computer_name() -> str:
    """The name a Mac is shared under, which can differ from its host name."""
    if sys.platform != "darwin":
        return ""
    done = subprocess.run(["scutil", "--get", "ComputerName"], capture_output=True, text=True,
                          timeout=3, check=False)
    return done.stdout.strip()


def _install_name() -> str:
    from common.config.paths import get_ini_config
    from common.devices import install_identity

    return install_identity.display_name(get_ini_config())


def _registry_devices() -> list:
    from common.devices.device_registry import get_device_registry

    return get_device_registry().devices()


def _devices() -> tuple[str, ...]:
    return _guard(lambda: [device.display_name for device in _registry_devices()])


def _domains() -> tuple[str, ...]:
    def addresses() -> list[str]:
        return [device.address for device in _registry_devices() if device.address]

    return _guard(addresses)


def _players() -> tuple[str, ...]:
    def names() -> list[str]:
        from common.players.players import get_roster

        found: list[str] = []
        for player in get_roster().players():
            found += [player.name, player.initials]
        return found + _account_values()

    return _guard(names)


def _account_values() -> list[str]:
    """What a person typed into an extension account: a handle, an email, a name."""
    from common import extensions

    store = extensions.get_extension_store()
    found: list[str] = []
    for record in extensions.registry().records():
        for fields in store.accounts(record.name).values():
            found += [value for value in fields.values()
                      if MIN_ACCOUNT_VALUE <= len(str(value)) <= MAX_ACCOUNT_VALUE]
    return found


def _identifiers() -> tuple[str, ...]:
    def ids() -> list[str]:
        from common.devices import install_identity

        return [install_identity.install_id(),
                *(device.device_id for device in _registry_devices())]

    return _guard(ids)


def _libraries() -> tuple[str, ...]:
    def paths() -> list[str]:
        from common.games import library_folders

        return [location.path for location in library_folders.configured()]

    return _guard(paths)


def _tables() -> tuple[str, ...]:
    """Read from the rows already loaded: building a package must not trigger a scan."""
    def names() -> list[str]:
        from common.games import game_index_service

        found: list[str] = []
        for row in game_index_service.get_rows() or []:
            filename = str(row.get("filename", ""))
            found += [row.get("name", ""), Path(str(row.get("game_dir", ""))).name,
                      filename, Path(filename).stem]
        return found

    return _guard(names)
