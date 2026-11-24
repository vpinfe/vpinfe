"""What VPinFE could not write to a game's .info on its own, held until it restarts.

Every read of that .info through `MetaConfig` reads what is held, so a refresh sees its
own work and does not do it again. What is held goes when a write to the file succeeds,
and when the file changes on disk: the file wins over what was held against it.
"""

from __future__ import annotations

import copy
import logging
import os
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from common.failures import why

logger = logging.getLogger("vpinfe.common.games.unwritten")


@dataclass
class _Held:
    config: dict[str, Any]
    # (mtime_ns, size) of the .info when it was held, or None for a file not there.
    signature: tuple[int, int] | None
    why: str


# .info path -> what could not be written to it.
_HELD: dict[str, _Held] = {}


def _key(path: str | os.PathLike[str]) -> str:
    return os.path.normpath(os.path.abspath(os.fspath(path)))


def _signature(key: str) -> tuple[int, int] | None:
    try:
        stat = os.stat(key)
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _folder_name(key: str) -> str:
    return os.path.basename(os.path.dirname(key))


def hold(info: str | os.PathLike[str], config: dict[str, Any], exc: BaseException) -> None:
    """Keep `config` as what `info` reads as, because writing it raised `exc`."""
    key = _key(info)
    reason = why(exc)
    if key in _HELD:
        logger.debug("Still could not write %s: %s", _folder_name(key), reason)
    else:
        logger.warning("Could not write %s, so what VPinFE found there lasts until it "
                       "restarts: %s", _folder_name(key), reason)
    _HELD[key] = _Held(copy.deepcopy(config), _signature(key), reason)


def held(info: str | os.PathLike[str]) -> dict[str, Any] | None:
    """A copy of what `info` reads as, or None where nothing is held for it."""
    key = _key(info)
    one = _HELD.get(key)
    if one is None:
        return None
    if _signature(key) != one.signature:
        _HELD.pop(key, None)
        logger.info("%s changed on disk, so it is read as it is there now",
                    _folder_name(key))
        return None
    return copy.deepcopy(one.config)


def release(info: str | os.PathLike[str]) -> None:
    """`info` was written, so nothing is held for it."""
    if _HELD.pop(_key(info), None) is not None:
        logger.info("Wrote %s, which VPinFE could not write before",
                    _folder_name(_key(info)))


def infos() -> list[str]:
    """Every .info holding what VPinFE could not write to it."""
    return list(_HELD)


def reasons(folders: Iterable[str]) -> dict[str, str]:
    """{folder: why} for each of `folders` whose .info holds what could not be written."""
    by_folder = {os.path.dirname(key): one.why for key, one in _HELD.items()}
    return {folder: by_folder[_key(folder)] for folder in folders
            if _key(folder) in by_folder}
