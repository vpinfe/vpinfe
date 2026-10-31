"""Runs the probes a session answers to side by side, so one slow desktop cannot hold the
report."""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping, Sequence

from . import desktop as desk
from .model import TIMED_OUT, Reading
from .probes import Probe, chosen
from .run import PROBE_SECONDS, Ran, Run
from .run import run as run_program

logger = logging.getLogger("vpinfe.common.support.collect.displays.gather")

# All the probes together. Each program has its own limit in `run`.
TOTAL_SECONDS = 12


def _guarded(name: str, probe: Probe, env: Mapping[str, str], run: Run) -> Reading:
    try:
        return probe(env, run)
    except Exception as failure:  # noqa: BLE001 - a probe that fails is a gap, not an error
        logger.warning("Display probe %s failed: %s", name, type(failure).__name__)
        logger.debug("Display probe %s failed", name, exc_info=True)
        return Reading.failed(name, type(failure).__name__)


def _until(run: Run, deadline: float) -> Run:
    """`run`, except that no program starts once the deadline has passed, so a probe that
    asks several in turn hands back what it has read instead of being cut off."""
    def bounded(argv: Sequence[str]) -> Ran:
        if time.monotonic() >= deadline:
            return Ran(TIMED_OUT)
        return run(argv)
    return bounded


def readings(here: desk.Desktop, env: Mapping[str, str], run: Run = run_program,
             probes: Sequence[tuple[str, Probe]] | None = None,
             seconds: float = TOTAL_SECONDS) -> list[Reading]:
    """One reading for each probe, in the order they are chosen. A probe still running when
    the time is up is `timed_out`, and the rest are kept. The probes' threads are daemons,
    so one stuck in a call never holds the program open."""
    asked = list(chosen(here) if probes is None else probes)
    if not asked:
        return []
    start = time.monotonic()
    bounded = _until(run, start + max(seconds - PROBE_SECONDS, 0))
    found: list[Reading | None] = [None] * len(asked)

    def work(index: int, name: str, probe: Probe) -> None:
        found[index] = _guarded(name, probe, env, bounded)

    threads = [threading.Thread(target=work, args=(index, name, probe), daemon=True,
                                name=f"display-probe-{name}")
               for index, (name, probe) in enumerate(asked)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(max(start + seconds - time.monotonic(), 0))
    return [one if one is not None else Reading.timed_out(name)
            for (name, _), one in zip(asked, found, strict=True)]
