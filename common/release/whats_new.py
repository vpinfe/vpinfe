"""Whether this start is the first after an update, and what there is to tell about it.

`last_version` on this install's row in the device registry is the release it last
started as. A start under a later release marks the review pending, once, and reading it
clears the mark. `whats_new_since` is where that review begins: the oldest version not yet
read.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from common.config.config_access import ConfigSource
from common.config.config_store import ConfigStore
from common.devices import device_registry, install_setup
from common.release import migration_ledger, release_notes, upgrade_needs

LAST_KEY = "last_version"
PENDING_KEY = "whats_new_pending"
SINCE_KEY = "whats_new_since"


@dataclass(frozen=True)
class Review:
    since: str
    releases: list[release_notes.Release] = field(default_factory=list)
    files: list[migration_ledger.Entry] = field(default_factory=list)
    needs: list[upgrade_needs.Need] = field(default_factory=list)


def _row() -> device_registry.DeviceRegistry:
    return device_registry.get_device_registry()


def last_version() -> str:
    return str(_row().self_get(LAST_KEY, "") or "")


def since() -> str:
    return str(_row().self_get(SINCE_KEY, "") or "")


def _is_pending() -> bool:
    return bool(_row().self_get(PENDING_KEY, False))


def due(config: ConfigSource) -> bool:
    """Whether an update is waiting to be read. Never while Setup is, because a new
    install has nothing to catch up on."""
    return _is_pending() and not install_setup.pending(config)


def note_start(config: ConfigStore, running: str) -> None:
    """Record this start. A build that is not a tagged release is not an update and
    writes nothing, so a person running from source or a CI build is never interrupted.
    """
    if not release_notes.is_release(running):
        return
    last = last_version()
    if last == running:
        return
    fields: dict[str, object] = {LAST_KEY: running}
    if not install_setup.pending(config) and (not last or release_notes.newer(running, last)):
        if not _is_pending():
            fields[SINCE_KEY] = last or None
        fields[PENDING_KEY] = True
    _row().set_self(**fields)


def mark_seen() -> None:
    if _is_pending():
        _row().set_self(**{PENDING_KEY: None, SINCE_KEY: None})


def review(config: ConfigSource, running: str, *, again: bool = False,
           folder: Path | None = None, current: Path | None = None,
           needs: tuple[upgrade_needs.Need, ...] | None = None) -> Review:
    """Everything since the version this install last read about. Asked again, it is the
    running version alone: its notes, what it did to the files, and what is still open."""
    began = "" if again else since()
    files = migration_ledger.entries(began)
    if again:
        files = [one for one in files if one.version == running]
    return Review(
        since=began,
        releases=release_notes.crossed(began, running, folder=folder, current=current),
        files=files,
        needs=upgrade_needs.open_for(config, began, needs),
    )
