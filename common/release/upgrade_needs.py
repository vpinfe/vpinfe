"""Steps an update asks a person to take, each declaring the version that introduced it.

A step is open until its own check says it is done, and a done step never shows. Only a
step with `blocks_play` set is ever drawn at the cabinet.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from common.config.config_access import ConfigSource
from common.release.release_notes import newer

logger = logging.getLogger("vpinfe.common.release.upgrade_needs")


@dataclass(frozen=True)
class Need:
    # Names `needs.<key>.label` and `needs.<key>.help` in the catalog.
    key: str
    # The release that first asked for this.
    version: str
    done: Callable[[ConfigSource], bool]
    blocks_play: bool = False


# Empty on purpose: the authenticator is offered once by the page and is never a step an
# update asks for.
NEEDS: tuple[Need, ...] = ()


def _open(config: ConfigSource, need: Need) -> bool:
    try:
        return not need.done(config)
    except Exception:
        logger.exception("Could not tell whether %s is done", need.key)
        return True


def open_for(config: ConfigSource, since: str,
             needs: Sequence[Need] | None = None) -> list[Need]:
    """Steps introduced after `since` that are not done. With nothing for `since`, every
    step that is not done."""
    return [n for n in (NEEDS if needs is None else needs)
            if (not since or newer(n.version, since)) and _open(config, n)]


def blocking(config: ConfigSource, needs: Sequence[Need] | None = None) -> list[Need]:
    """Open steps that stop play, whichever version asked for them."""
    return [n for n in (NEEDS if needs is None else needs) if n.blocks_play and _open(config, n)]
