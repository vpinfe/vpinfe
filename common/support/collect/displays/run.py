"""The one place a probe starts a program: argv, no shell, nothing on stdin, a limit on the
time and on the output, and the C locale so the words a parser reads do not change."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from common.host.tools import NO_WINDOW

from .model import FAILED, NOT_HERE, OK, TIMED_OUT

# The limit on one program. A probe that asks several has no more than `gather`'s total.
PROBE_SECONDS = 3
MAX_OUTPUT = 1_000_000


@dataclass(frozen=True)
class Ran:
    state: str
    text: str = ""
    code: int = 0

    @property
    def ok(self) -> bool:
        return self.state == OK


Run = Callable[[Sequence[str]], Ran]


def run(argv: Sequence[str]) -> Ran:
    """Runs `argv` and says what happened. Never raises."""
    program = shutil.which(argv[0])
    if program is None:
        return Ran(NOT_HERE)
    env = {**os.environ, "LC_ALL": "C", "LANG": "C"}
    try:
        done = subprocess.run([program, *argv[1:]], stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=PROBE_SECONDS, env=env,
                              creationflags=NO_WINDOW, check=False)
    except subprocess.TimeoutExpired:
        return Ran(TIMED_OUT)
    except OSError:
        return Ran(FAILED)
    text = done.stdout[:MAX_OUTPUT].decode("utf-8", errors="replace")
    return Ran(OK if done.returncode == 0 else FAILED, text, done.returncode)
