"""VPinFE's tests, grouped by what they exercise.

This file is what keeps the group folders from colliding with the packages they test:
without it, `tests/frontend/` is importable as top-level `frontend` and shadows the real
one. With it, every module is `tests.<group>.<name>` and nothing is ambiguous.

Run it as `python -m unittest discover -t . -s tests`. The top-level directory has
to be the repo root, or discovery imports the group folders as top-level packages
and never imports this file - which is where the power guard below is installed.
tests/invariants/test_bootstrap_runs.py fails when that happens, so a wrong
invocation says so instead of quietly running without a guard.

`python -m tests` runs the same modules across processes; see `tests/__main__.py`.
"""

from __future__ import annotations

import atexit
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from platformdirs import user_cache_dir

# Every test runs against an empty config directory rather than the developer's own.
# common/config/paths.py resolves CONFIG_DIR at import time, so this has to happen before
# anything imports common/ - a setUp is already too late.
#
# Without it the in-process API tests read whatever vpinfe.json the machine happens to
# have: a stale `roles` value failed an assertion about what an unconfigured install
# reports, and vpx_bin_path decides which branch the auth boundary test takes. Both
# pass on CI, which has no config at all, so the suite only lied on a developer's
# machine - where its answer is most likely to be believed.
#
# An explicit VPINFE_CONFIG_DIR still wins, so a caller can hand in a prepared one.
# None when this file made it: `python -m tests` cannot split a caller's between processes.
CALLER_CONFIG_DIR = os.environ.get("VPINFE_CONFIG_DIR", "").strip() or None
TEMP_ROOTS = Path(user_cache_dir("vpinfe")) / "tests-tmp"
# A killed process leaves its temp root, and a failing shard's log is kept on purpose, so
# a kept log is gone after this long. A root with nothing created in it for this long
# counts as dead, even to a process that is still using it.
LEFTOVER_AGE_S = 24 * 3600


def sweep_leftovers(root: Path = TEMP_ROOTS, older_than: float = LEFTOVER_AGE_S) -> int:
    """Remove the entries of `root` last changed more than `older_than` seconds ago, and
    return how many went."""
    cutoff = time.time() - older_than
    removed = 0
    try:
        entries = list(root.iterdir())
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.stat().st_mtime >= cutoff:
                continue
            if entry.is_dir():
                shutil.rmtree(entry, ignore_errors=True)
            else:
                entry.unlink()
        except OSError:
            continue
        if not entry.exists():
            removed += 1
    return removed


if CALLER_CONFIG_DIR is None:
    _TEST_CONFIG_DIR = tempfile.mkdtemp(prefix="vpinfe-tests-")
    os.environ["VPINFE_CONFIG_DIR"] = _TEST_CONFIG_DIR
    atexit.register(shutil.rmtree, _TEST_CONFIG_DIR, ignore_errors=True)

    TEMP_ROOTS.mkdir(parents=True, exist_ok=True)
    sweep_leftovers()
    _TEST_TMP_DIR = tempfile.mkdtemp(prefix="vpinfe-tmp0", dir=str(TEMP_ROOTS))
    os.environ["TMPDIR"] = _TEST_TMP_DIR
    os.environ["TEMP"] = _TEST_TMP_DIR
    os.environ["TMP"] = _TEST_TMP_DIR
    tempfile.tempdir = _TEST_TMP_DIR
    atexit.register(shutil.rmtree, _TEST_TMP_DIR, ignore_errors=True)

# Nor against any host outside this machine: http_client refuses them as an offline
# machine would. A test of the transport stubs it; one that needs the real network is
# a bug in the test.
from common.security.http_client import OFFLINE as _OFFLINE  # noqa: E402

os.environ.setdefault(_OFFLINE, "1")

# Nor does an import work in a library folder of the machine's own: the working folder is
# beside the library, which the suite has none of. Assigned, not a started patch, which any
# test's `patch.stopall()` would lift. Kept for the tests of the folder itself.
from common.uploads import working_folder as _working_folder  # noqa: E402

REAL_WORKING_WHERE = _working_folder._where
# One folder per temp root, named here and made only when an import first needs it: a process
# a test spawns and kills never gets to run its exit handlers, so one made per import of this
# file is left behind by each of them.
_TEST_WORKING_DIR = str(Path(tempfile.gettempdir()) / "vpinfe-library")
atexit.register(shutil.rmtree, _TEST_WORKING_DIR, ignore_errors=True)
_working_folder._where = lambda: (  # type: ignore[assignment]
    Path(_TEST_WORKING_DIR) / _working_folder.NAME, "the test library")

# Nor against the machine's own VPinballX.ini, which an empty Settings File stands for:
# a launcher-scope write would change it. Assigned, not a started patch, which any
# test's `patch.stopall()` would lift.
from apps.vpx import config as _vpx_config  # noqa: E402

# Kept for the tests of the platform's own answer.
REAL_MACHINE_FOLDERS = _vpx_config._machine_folders
_vpx_config._machine_folders = lambda: (None, None)  # type: ignore[attr-defined]

# Nothing in the suite may power the machine off, restart it, or replace the test
# process with a new one.
#
# This is not hypothetical. A test that wired the real lifecycle performers and asked for
# a system stop shut a developer's Mac down, with no prompt and no warning: the confirm
# hook is off by default, so there is nothing between the request and the command. It
# happened twice - the second time from a check meant to prove this guard worked, which
# ran the suite with the guard removed.
#
# A test that means to exercise a power path patches `common.host.system_actions` and
# asserts on the patch. Reaching a real command is a bug in the test, so it raises rather
# than being quietly dropped: a swallowed one leaves the test passing while claiming to
# have done something it did not.
#
# Matched by program plus argument, not by loose keywords, because the same program is
# safe or not depending on what it is told:
#
#   Linux    systemctl poweroff | reboot | halt
#   macOS    osascript ... "shut down" / "restart";  shutdown -h / -r
#   Windows  shutdown /s | /r | /f;  wmic ... shutdown
#
# `shutdown` is the trap. On Windows it needs a flag to do anything, on macOS `-h`/`-r`,
# and a bare `shutdown --help` is harmless - so the program name alone is neither
# sufficient nor safe to match on.

# program -> (exact flags/verbs, substrings)
#
# A flag has to match an argument exactly, because `-h` is a substring of `--help` and
# blocking `shutdown --help` would be wrong. A substring matches anywhere in the line,
# because osascript carries its whole instruction inside one quoted argument.
_POWER_PROGRAMS = {
    "systemctl": (("poweroff", "reboot", "halt", "suspend", "hibernate"), ()),
    "shutdown": (("/s", "/r", "/f", "/sg", "-h", "-r", "-s",
                  "--halt", "--reboot", "--poweroff"), ()),
    "osascript": ((), ("shut down", "restart", "sleep", "log out")),
    "poweroff": ((), ()),          # takes no argument to be dangerous
    "reboot": ((), ()),
    "halt": ((), ()),
    "wmic": (("shutdown",), ()),
    "rundll32": ((), ("exitwindows",)),
}

_real_popen = subprocess.Popen
_real_system = os.system
_real_execv = os.execv
_real_execvp = os.execvp
_real_execve = os.execve


class RefusedPowerCommandError(AssertionError):
    """A test tried to power off, restart or halt the machine running it."""


def _program(path: str) -> str:
    """The command name, however it was spelled: a bare name, a path, or `X.exe`.

    Splits on both separators rather than using os.path.basename, which only knows the
    host's. A Windows command line checked on a POSIX box - which is what CI does - would
    otherwise arrive as one long unsplit string and match nothing.
    """
    name = str(path).replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def _is_power_command(args) -> str | None:
    """The offending command line, or None. Case-insensitive; Windows flags are not."""
    if isinstance(args, (list, tuple)):
        parts = [str(a) for a in args]
    else:
        parts = str(args).split()
    if not parts:
        return None

    entry = _POWER_PROGRAMS.get(_program(parts[0]))
    if entry is None:
        return None
    flags, phrases = entry
    printable = " ".join(parts)
    if not flags and not phrases:
        return printable        # the program name alone is the whole command

    arguments = [part.lower() for part in parts[1:]]
    if any(flag.lower() in arguments for flag in flags):
        return printable
    line = " ".join(arguments)
    if any(phrase.lower() in line for phrase in phrases):
        return printable
    return None


def _refuse(printable: str, how: str) -> None:
    raise RefusedPowerCommandError(
        f"a test tried to {how} a real power command: {printable!r}. "
        "Patch common.host.system_actions and assert on the patch instead."
    )


class _GuardedPopen(_real_popen):
    """Refuses a power command, and stays a Popen in every other respect.

    A subclass rather than a wrapper function: subprocess.Popen is a class, and the
    stdlib subclasses it and reads its attributes. Replacing it with a function made
    `class X(subprocess.Popen)` raise "function() argument 'code' must be code, not str",
    which took the API contract probe down on Windows and nowhere else.
    """

    def __init__(self, args, *rest, **kwargs):
        offending = _is_power_command(args)
        if offending:
            _refuse(offending, "run")
        super().__init__(args, *rest, **kwargs)


def _guarded_system(command):
    """`os.system` takes a string and never reaches Popen, so it needs its own guard."""
    offending = _is_power_command(command)
    if offending:
        _refuse(offending, "run")
    return _real_system(command)


def _guarded_exec(real, name):
    """`restart_if_requested` re-execs the process, which spawns nothing.

    Not a power command, but just as destructive here: it would replace the test runner
    with a fresh VPinFE and the suite would simply stop, mid-run, looking like a crash.
    """
    def guard(path, args, *rest):
        _refuse(f"{path} {' '.join(map(str, args))}", f"{name}")
    return guard


subprocess.Popen = _GuardedPopen  # type: ignore[misc]
os.system = _guarded_system
os.execv = _guarded_exec(_real_execv, "exec")
os.execvp = _guarded_exec(_real_execvp, "exec")
os.execve = _guarded_exec(_real_execve, "exec")
