"""Commands a person asks to run around a table, or around VPinFE itself.

Outermost, and that is the whole point. Anything defined inside VPinFE's own sequence -
"after the hardware handover" - means something that shifts as that sequence changes.
Outermost is defined relative to nothing, so it is the one pair that can be promised to
keep working, and it covers everything external to VPinFE's own work.

**argv, never a shell.** One command per line, split the way a shell would split it but
with nothing else a shell does - no pipes, no redirection, no expansion. Anything that
wants those goes in a script the user points at, where they can be read.

**A timeout is the feature working at all.** A pre-start command that hangs means no
table ever launches again, and the launch path suppresses the frontend's input around
that window, so a hang there can leave a cabinet with no game and no way to leave it.
"""

from __future__ import annotations

import logging
import shlex
import subprocess
from dataclasses import dataclass, field

from common import tokens

logger = logging.getLogger("vpinfe.common.host.commands")

# Long enough for a mount or a service to settle, short enough that a mistake is a pause
# rather than a cabinet nobody can use.
DEFAULT_TIMEOUT = 15

# What a failure costs. The same distinction peripherals draw: a share that did not
# mount should stop the launch, and an audio route that did not switch should not.
REQUIRED = "required"
BEST_EFFORT = "best_effort"


@dataclass(frozen=True)
class Result:
    command: str
    ok: bool
    said: str = ""
    exit_code: int | None = None


@dataclass
class Outcome:
    results: list[Result] = field(default_factory=list)
    # Whether anything ran at all, which is what decides if the other half must.
    ran: bool = False

    @property
    def failed(self) -> list[Result]:
        return [one for one in self.results if not one.ok]


class CommandRefusedError(RuntimeError):
    """A required command failed, so whatever it was preparing for must not go ahead."""


def planned(text: str, values: dict[str, str], *, context: str,
            after: bool = False) -> list[list[str]]:
    """The lines of a setting, as argv.

    Split first and resolved per argument, so a path with a space in it stays one
    argument. Resolving into the line and splitting after is how it becomes two.
    """
    plans: list[list[str]] = []
    for line in str(text or "").splitlines():
        said = line.strip()
        if not said or said.startswith("#"):
            continue
        try:
            argv = shlex.split(said, comments=True, posix=True)
        except ValueError as exc:
            raise ValueError(f"{said}: {exc}") from exc
        if argv:
            plans.append([tokens.resolve(one, values, context=context, after=after)
                          for one in argv])
    return plans


def run(text: str, values: dict[str, str], *, context: str, after: bool = False,
        timeout: int = DEFAULT_TIMEOUT, on_failure: str = BEST_EFFORT,
        env: dict[str, str] | None = None) -> Outcome:
    """Every line in a setting, in the order written.

    A required set stops at its first failure and raises, because the rest of it was
    written expecting the first to have worked.
    """
    outcome = Outcome()
    try:
        plans = planned(text, values, context=context, after=after)
    except (ValueError, tokens.UnknownTokenError) as exc:
        said = (f"{exc} is not something this can stand for"
                if isinstance(exc, tokens.UnknownTokenError) else str(exc))
        outcome.results.append(Result(command=str(text or "").strip(), ok=False,
                                      said=said))
        if on_failure == REQUIRED:
            raise CommandRefusedError(said) from exc
        logger.warning("Ignoring a command that could not be read: %s", said)
        return outcome

    for argv in plans:
        outcome.ran = True
        one = _one(argv, timeout=timeout, env=env)
        outcome.results.append(one)
        if one.ok:
            continue
        if on_failure == REQUIRED:
            raise CommandRefusedError(one.said)
        logger.warning("Command failed and was allowed to: %s", one.said)
    return outcome


def _one(argv: list[str], *, timeout: int, env: dict[str, str] | None) -> Result:
    said = shlex.join(argv)
    try:
        done = subprocess.run(argv, capture_output=True, text=True,  # noqa: S603
                              timeout=max(1, int(timeout or DEFAULT_TIMEOUT)),
                              check=False, env=env)
    except subprocess.TimeoutExpired:
        return Result(said, False, f"{said}: still running after {timeout}s")
    except OSError as exc:
        return Result(said, False, f"{said}: {exc.strerror or exc}")
    if done.returncode != 0:
        tail = (done.stderr or done.stdout or "").strip().splitlines()
        return Result(said, False,
                      f"{said}: exited {done.returncode}"
                      + (f" - {tail[-1]}" if tail else ""),
                      exit_code=done.returncode)
    return Result(said, True, exit_code=0)
