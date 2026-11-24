"""Whether this install has finished Setup: `device.setup_pending`.

Written true only by a fresh install's own first save (`common/config/config_store.py`); every
other install reads absent the same as false, so a 2.x conversion, a hand-written
`vpinfe.json`, or an install that already ran Setup never sees it again.

`setup_done` on this install's row in the device registry lists the steps already
answered, so Setup reopens where it stopped.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable

from common.config.config_access import ConfigSource, cfg_bool
from common.config.config_store import ConfigStore
from common.devices import device_registry

logger = logging.getLogger("vpinfe.common.devices.install_setup")

SECTION = "device"
KEY = "setup_pending"
DONE_KEY = "setup_done"

_after_finish: list[Callable[[], None]] = []
_after_finish_lock = threading.Lock()


def pending(config: ConfigSource) -> bool:
    """True until Setup finishes. Never writes."""
    return cfg_bool(config, SECTION, KEY, False)


def answered() -> list[str]:
    """The steps answered so far, in the order they were. Never writes."""
    done = device_registry.get_device_registry().self_get(DONE_KEY, [])
    return [str(step) for step in done] if isinstance(done, list) else []


def answer(step: str) -> None:
    """Records a step as answered. Writes only when it was not already."""
    done = answered()
    if step not in done:
        device_registry.get_device_registry().set_self(**{DONE_KEY: [*done, step]})


def after_finish(config: ConfigStore, callback: Callable[[], None]) -> None:
    """Runs `callback` once, when Setup finishes - at once if the file already says so.

    The web server is up before this is called, so Finish can land first. The file is
    read for itself because `config` holds what booted this process.
    """
    with _after_finish_lock:
        _after_finish.append(callback)
    if os.path.exists(config.json_path) and not pending(ConfigStore(config.json_path)):
        _run_waiting()


def _run_waiting() -> None:
    with _after_finish_lock:
        callbacks, _after_finish[:] = list(_after_finish), []
    for callback in callbacks:
        try:
            callback()
        except Exception:
            logger.exception("Something to start once Setup finished did not start")


def finish(config: ConfigStore) -> None:
    """Clears the mark and saves. Idempotent - a second call writes the same value.

    The callbacks run only when this call is the one that cleared the mark, and each runs
    once, so Finish pressed in two windows starts nothing twice.
    """
    was_pending = pending(config)
    config.set_value(SECTION, KEY, False)
    config.save()
    if was_pending:
        _run_waiting()
