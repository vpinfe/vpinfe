"""`python -m tests`: the suite across processes, each module whole inside one of them.

    python -m tests                 half the cores
    python -m tests -j 4            four processes
    python -m tests -j 1            in this process, one module after another
    python -m tests tests/theming   only what is under a path

`python -m unittest discover -t . -s tests` stays the reference, and CI's command.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import warnings
from collections.abc import Callable
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any
from unittest.loader import VALID_MODULE_NAME

from platformdirs import user_cache_dir

import tests
from tests import affected, stamp, wholesuite

PACKAGE = Path(tests.__file__).resolve().parent
REPO = PACKAGE.parent
DURATIONS = Path(user_cache_dir("vpinfe")) / "test-durations.json"

# What a module with no recorded time weighs when the modules are shared out.
UNTIMED_BROWSER_MODULE = 40.0
UNTIMED_MODULE = 0.5
_DRIVES_A_BROWSER = re.compile(
    r"^\s*(?:from|import)\s+tests\.support(?:\.(?:live_instance|browser_session)\b"
    r"|\s+import\b.*\b(?:live_instance|browser_session)\b)",
    re.MULTILINE)

# A module that sets this runs when a run names it or passes --release, not in a whole run
# or --affected.
_RELEASE_ONLY = re.compile(r"^RELEASE_ONLY\s*=\s*True\s*$", re.MULTILINE)

_PROGRESS_EVERY = 30.0
_SLOWEST = 10

Record = dict[str, Any]

# What every browser test says when it skips for want of a browser (`chromium_path()` is None).
NO_BROWSER = "no Chromium on this machine"


# -- what runs -------------------------------------------------------------------------


def modules(start: Path, top: Path = REPO) -> list[str]:
    """The modules `unittest discover` loads under `start`, in the order it loads them."""
    if start.is_file():
        return [_name(start, top)]
    found: list[str] = []
    for entry in sorted(os.listdir(start)):
        path = start / entry
        if path.is_file():
            if VALID_MODULE_NAME.match(entry) and fnmatch(entry, "test*.py"):
                found.append(_name(path, top))
        elif (path / "__init__.py").is_file():
            found.extend(modules(path, top))
    return found


def _name(path: Path, top: Path) -> str:
    return ".".join(path.resolve().relative_to(top.resolve()).with_suffix("").parts)


def _path(name: str, top: Path = REPO) -> Path:
    return top.joinpath(*name.split(".")).with_suffix(".py")


def release_only(names: list[str], top: Path = REPO) -> list[str]:
    """The modules among `names` that run only before a release."""
    found = []
    for name in names:
        try:
            source = _path(name, top).read_text(encoding="utf-8")
        except OSError:
            continue
        if _RELEASE_ONLY.search(source):
            found.append(name)
    return found


# -- where it runs ---------------------------------------------------------------------


@dataclass
class Shard:
    number: int
    modules: list[str] = field(default_factory=list)
    planned: float = 0.0
    records: list[Record] = field(default_factory=list)
    log: Path | None = None
    stopped: str = ""


def guess(source: str) -> float:
    return UNTIMED_BROWSER_MODULE if _DRIVES_A_BROWSER.search(source) else UNTIMED_MODULE


def weights(names: list[str], recorded: dict[str, float]) -> dict[str, float]:
    found = {}
    for name in names:
        if name in recorded:
            found[name] = recorded[name]
            continue
        try:
            found[name] = guess(_path(name).read_text(encoding="utf-8"))
        except OSError:
            found[name] = UNTIMED_MODULE
    return found


def plan(names: list[str], weighed: dict[str, float], processes: int) -> list[Shard]:
    """The longest module first, each onto the shard with the least so far.

    A shard runs its modules in discovery order.
    """
    shards = [Shard(number) for number in range(processes)]
    for name in sorted(names, key=lambda name: -weighed[name]):
        lightest = min(shards, key=lambda shard: shard.planned)
        lightest.modules.append(name)
        lightest.planned += weighed[name]
    order = {name: index for index, name in enumerate(names)}
    for shard in shards:
        shard.modules.sort(key=order.__getitem__)
    return shards


def shard_environment(base: dict[str, str], root: Path, number: int) -> dict[str, str]:
    """`base`, with a config dir and a temp dir that belong to this shard alone."""
    temp = str(root / f"tmp{number}")
    # TEMP and TMP are what a Windows program started by a test reads.
    return {**base, "VPINFE_CONFIG_DIR": str(root / f"config{number}"),
            "TMPDIR": temp, "TEMP": temp, "TMP": temp}


def refusal(processes: int, caller_config_dir: str | None) -> str | None:
    """Why the run cannot go ahead as asked, or None."""
    if processes > 1 and caller_config_dir:
        return (f"VPINFE_CONFIG_DIR is set to {caller_config_dir}, and {processes} "
                "processes would all read and write that one directory, so one could "
                "change what another's tests see. Unset it to give each process its own, "
                "or run with -j 1.")
    return None


# -- running ---------------------------------------------------------------------------


class _Lines:
    """The stream a `TextTestResult` writes to."""

    def __init__(self, stream: Any):
        self._stream = stream

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)

    def write(self, text: str) -> None:
        self._stream.write(text)

    def flush(self) -> None:
        self._stream.flush()

    def writeln(self, text: str | None = None, /) -> None:
        self._stream.write(f"{text or ''}\n")


def run_modules(names: list[str], stream: Any, keep: Callable[[Record], None],
                top: Path = REPO) -> None:
    """Every module imported before any runs, as `unittest discover` does; then each as
    a run of its own, timed with its import and its fixtures."""
    loaded = []
    for name in names:
        path = _path(name, top)
        start = time.perf_counter()
        suite = unittest.TestLoader().discover(str(path.parent), pattern=path.name,
                                               top_level_dir=str(top))
        loaded.append((name, suite, time.perf_counter() - start))

    lines = _Lines(stream)
    with warnings.catch_warnings():
        if not sys.warnoptions:
            warnings.simplefilter("default")
        for name, suite, importing in loaded:
            result = unittest.TextTestResult(lines, descriptions=True, verbosity=1)
            start = time.perf_counter()
            result.startTestRun()
            try:
                suite.run(result)
            finally:
                result.stopTestRun()
            keep(_record(name, result, importing + time.perf_counter() - start))


def _record(name: str, result: unittest.TextTestResult[Any], seconds: float) -> Record:
    report = ""
    if not result.wasSuccessful():
        text = io.StringIO()
        shown, result.stream = result.stream, _Lines(text)
        result.printErrors()
        result.stream = shown
        report = text.getvalue().strip("\n")
    return {"module": name, "seconds": round(seconds, 3), "tests": result.testsRun,
            "failures": len(result.failures), "errors": len(result.errors),
            "skipped": len(result.skipped), "expected_failures": len(result.expectedFailures),
            "no_browser": sum(NO_BROWSER in reason for _, reason in result.skipped),
            "unexpected_successes": len(result.unexpectedSuccesses), "report": report}


def run_shard(plan_file: Path) -> int:
    """A shard's own process: its modules, and a record line for each as it ends."""
    assigned = json.loads(plan_file.read_text(encoding="utf-8"))
    records = Path(assigned["records"])

    def keep(record: Record) -> None:
        with records.open("a", encoding="utf-8") as out:
            out.write(json.dumps(record) + "\n")

    run_modules(assigned["modules"], sys.stderr, keep)
    return 0


def _read_records(path: Path) -> list[Record]:
    """What a shard has recorded so far. A line still being written is left for later."""
    found: list[Record] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return found
    for line in text.splitlines():
        try:
            found.append(json.loads(line))
        except ValueError:
            continue
    return found


def in_processes(shards: list[Shard]) -> None:
    """Run each shard in a process of its own, filling in what it recorded."""
    # A digit before mkdtemp's random part, as LiveInstance's prefix has. Not in this
    # process's own temp root, which goes at exit with a failing shard's log in it.
    tests.TEMP_ROOTS.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix="vpinfe-run0", dir=tests.TEMP_ROOTS))
    finished: queue.Queue[tuple[Shard, int]] = queue.Queue()
    running: list[subprocess.Popen] = []
    started = time.perf_counter()
    try:
        for shard in shards:
            number = shard.number
            (root / f"config{number}").mkdir()
            (root / f"tmp{number}").mkdir()
            plan_file = root / f"shard{number}.json"
            plan_file.write_text(json.dumps({"modules": shard.modules,
                                             "records": str(root / f"shard{number}.jsonl")}),
                                 encoding="utf-8")
            shard.log = root / f"shard{number}.log"
            with open(shard.log, "w", encoding="utf-8") as log:
                proc = subprocess.Popen(
                    [sys.executable, "-m", "tests", "--shard", str(plan_file)],
                    cwd=str(REPO), env=shard_environment(dict(os.environ), root, number),
                    stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT)
            running.append(proc)
            threading.Thread(target=lambda s=shard, p=proc: finished.put((s, p.wait())),
                             daemon=True).start()

        for _ in shards:
            while True:
                try:
                    shard, code = finished.get(timeout=_PROGRESS_EVERY)
                    break
                except queue.Empty:
                    _say(_progress(shards, root, time.perf_counter() - started))
            _finished(shard, code, root, len(shards), time.perf_counter() - started)
    finally:
        for proc in running:
            if proc.poll() is None:
                _stop(proc)
        _clean(root, shards)


def _progress(shards: list[Shard], root: Path, seconds: float) -> str:
    so_far = [record for shard in shards
              for record in _read_records(root / f"shard{shard.number}.jsonl")]
    failing = len(_bad(so_far))
    return (f"  {seconds:.0f}s: {len(so_far)} of {sum(len(s.modules) for s in shards)} "
            f"modules, {sum(record['tests'] for record in so_far)} tests"
            + (f", {_modules(failing)} failing" if failing else ""))


def _finished(shard: Shard, code: int, root: Path, count: int, seconds: float) -> None:
    shard.records = _read_records(root / f"shard{shard.number}.jsonl")
    done = {record["module"] for record in shard.records}
    unfinished = [name for name in shard.modules if name not in done]
    label = f"Process {shard.number + 1} of {count}"
    if unfinished:
        after = len(unfinished) - 1
        shard.stopped = (f"{label} stopped with exit code {code} in {unfinished[0]}"
                         + (f"; {_modules(after)} after it did not run." if after else "."))
        _say(f"{label} stopped after {seconds:.0f}s, in {unfinished[0]}")
        return
    if code:
        shard.stopped = f"{label} exited with code {code} after its last module."
        _say(shard.stopped)
        return
    failing = len(_bad(shard.records))
    _say(f"{label} finished in {seconds:.0f}s (planned {shard.planned:.0f}s): "
         f"{sum(record['tests'] for record in shard.records)} tests, "
         + (f"{_modules(failing)} failing" if failing else "OK"))


def _stop(proc: subprocess.Popen) -> None:
    """Ctrl-C reaches a shard with the rest of the terminal; it gets time to close up."""
    for escalate in (None, proc.terminate, proc.kill):
        if escalate is not None:
            escalate()
        try:
            proc.wait(timeout=10)
            return
        except subprocess.TimeoutExpired:
            continue


def _leaked(path: Path) -> list[str]:
    try:
        return sorted(os.listdir(path))
    except OSError:
        return []


def _entries(count: int) -> str:
    return f"{count} entr{'y' if count == 1 else 'ies'}"


def _clean(root: Path, shards: list[Shard]) -> None:
    """Everything the run made, except the output of a shard that did not pass."""
    for shard in shards:
        number = shard.number
        shutil.rmtree(root / f"config{number}", ignore_errors=True)
        left = _leaked(root / f"tmp{number}")
        if left:
            note = f"left {_entries(len(left))} in its temp root: " + ", ".join(left[:5])
            if len(left) > 5:
                note += ", ..."
            shard.stopped = f"{shard.stopped} {note}".strip() if shard.stopped else note
            _say(f"Process {shard.number + 1} of {len(shards)} {note}")
        shutil.rmtree(root / f"tmp{number}", ignore_errors=True)
        for made in (f"shard{number}.json", f"shard{number}.jsonl"):
            (root / made).unlink(missing_ok=True)
        if shard.log is not None and _passed(shard):
            shard.log.unlink(missing_ok=True)
            shard.log = None
    try:
        root.rmdir()
    except OSError:
        pass


# -- the report ------------------------------------------------------------------------


def _modules(count: int) -> str:
    return f"{count} module{'' if count == 1 else 's'}"


def _bad(records: list[Record]) -> list[Record]:
    return [record for record in records if record["report"]]


def _passed(shard: Shard) -> bool:
    return (len(shard.records) == len(shard.modules) and not shard.stopped
            and not _bad(shard.records))


def failures(shards: list[Shard]) -> list[str]:
    """Every failure and error in full, as unittest prints them, under the shard's name."""
    lines = []
    for shard in shards:
        if _passed(shard):
            continue
        if len(shards) > 1:
            lines += ["#" * 70, f"Process {shard.number + 1} of {len(shards)}. "
                      f"Everything it printed: {shard.log}"]
        lines += [record["report"] for record in _bad(shard.records)]
        if shard.stopped:
            lines += ["=" * 70, shard.stopped]
    return lines


def failed_modules(shards: list[Shard]) -> list[str]:
    """Modules that failed or errored, and those a stopped process never got to finish."""
    names = []
    for shard in shards:
        names += [record["module"] for record in shard.records
                  if record["failures"] or record["errors"]]
        if shard.stopped:
            done = {record["module"] for record in shard.records}
            names += [name for name in shard.modules if name not in done]
    return sorted(names)


def sandboxed_modules(records: list[Record]) -> list[str]:
    """Modules with a test that skipped for want of a browser: that test has not passed.

    A drive that skips a test for another reason (a device that cannot send keys) is not here.
    """
    return sorted(record["module"] for record in records if record["no_browser"])


def summary(records: list[Record], seconds: float, stopped: int) -> tuple[list[str], int]:
    """unittest's closing lines for the whole run, and its exit code."""
    total = {key: sum(record[key] for record in records)
             for key in ("tests", "failures", "errors", "skipped", "expected_failures",
                         "unexpected_successes")}
    tests_run = total["tests"]
    infos = []
    if total["failures"] or total["errors"] or total["unexpected_successes"] or stopped:
        verdict, code = "FAILED", 1
        if total["failures"]:
            infos.append(f"failures={total['failures']}")
        if total["errors"]:
            infos.append(f"errors={total['errors']}")
        if stopped:
            infos.append(f"processes stopped={stopped}")
    elif tests_run == 0 and not total["skipped"]:
        verdict, code = "NO TESTS RAN", 5
    else:
        verdict, code = "OK", 0
    if total["skipped"]:
        infos.append(f"skipped={total['skipped']}")
    if total["expected_failures"]:
        infos.append(f"expected failures={total['expected_failures']}")
    if total["unexpected_successes"]:
        infos.append(f"unexpected successes={total['unexpected_successes']}")
    return ([f"Ran {tests_run} test{'' if tests_run == 1 else 's'} in {seconds:.3f}s", "",
             verdict + (f" ({', '.join(infos)})" if infos else "")], code)


def slowest(records: list[Record], recorded: dict[str, float]) -> list[str]:
    """A report, never a gate."""
    rows = sorted(records, key=lambda record: -record["seconds"])[:_SLOWEST]
    if not rows:
        return []
    lines = ["Slowest modules, this run and the time recorded before it:"]
    for record in rows:
        before = recorded.get(record["module"])
        was = "-" if before is None else f"{before:.1f}s"
        lines.append(f"  {record['seconds']:7.1f}s {was:>8}  {record['module']}")
    return lines


def recorded_after(recorded: dict[str, float], measured: dict[str, float]) -> dict[str, float]:
    """The latest time of every module still in the tree."""
    merged = {**recorded, **measured}
    return {name: seconds for name, seconds in sorted(merged.items()) if _path(name).is_file()}


def read_durations(path: Path = DURATIONS) -> dict[str, float]:
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(stored, dict):
        return {}
    return {name: float(seconds) for name, seconds in stored.items()
            if isinstance(seconds, (int, float))}


def write_durations(durations: dict[str, float], path: Path = DURATIONS) -> None:
    partial = path.with_name(f"{path.name}.{os.getpid()}")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        partial.write_text(json.dumps(durations, indent=1), encoding="utf-8")
        os.replace(partial, path)
    except OSError as exc:
        _say(f"The module times were not kept in {path}: {exc}")


def _say(text: str = "") -> None:
    print(text, file=sys.stderr, flush=True)


# -- the command -----------------------------------------------------------------------


def _processes(text: str) -> int:
    count = int(text)
    if count < 1:
        raise argparse.ArgumentTypeError("needs at least one process")
    return count


def _tree_hash() -> str | None:
    try:
        return stamp.tree_hash(REPO)
    except (subprocess.CalledProcessError, OSError):
        return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tests",
        description="Run the suite across processes, each test module whole in one.")
    parser.add_argument("-j", "--jobs", type=_processes,
                        default=max(1, (os.cpu_count() or 2) * 3 // 5),
                        help="how many processes; 1 runs in this one (default: three "
                             "fifths of the cores)")
    parser.add_argument("paths", nargs="*", type=Path, default=[PACKAGE],
                        help="a directory or a test file under tests/ (default: all of it)")
    parser.add_argument("--affected", nargs="?", const="HEAD", metavar="REV",
                        help="only what the changes since REV reach (default: HEAD, so the "
                             "working tree), plus the invariants; see tests/affected.py")
    parser.add_argument("--release", action="store_true",
                        help="also run the modules that are left out of a whole run and "
                             "--affected because they are only needed before a release")
    parser.add_argument("--overlap", action="store_true",
                        help="start a whole-suite run even though another is going")
    parser.add_argument("--shard", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.shard:
        return run_shard(args.shard)

    if args.affected:
        if args.paths != [PACKAGE]:
            parser.error("--affected picks the paths itself; drop the paths, or drop --affected")
        try:
            chosen = affected.select(REPO, affected.changed_files(REPO, args.affected))
        except ValueError as error:
            parser.error(str(error))
        for note in chosen.notes:
            _say(note)
        if chosen.whole_suite:
            _say("Running the whole suite.")
        else:
            args.paths = [REPO / path for path in chosen.paths]
            _say(f"Affected: {' '.join(chosen.paths)}")

    names: list[str] = []
    for path in args.paths:
        where = path.resolve()
        if not where.exists() or not where.is_relative_to(PACKAGE):
            parser.error(f"{path} is not a directory or a file under {PACKAGE}")
        names.extend(name for name in modules(where) if name not in names)
    left_out: list[str] = []
    if (args.affected or args.paths == [PACKAGE]) and not args.release:
        left_out = release_only(names)
        names = [name for name in names if name not in left_out]
    processes = min(args.jobs, max(1, len(names)))
    refused = refusal(processes, tests.CALLER_CONFIG_DIR)
    if refused:
        _say(refused)
        return 2

    whole = [path.resolve() for path in args.paths] == [PACKAGE]
    if whole and not args.overlap:
        busy = wholesuite.take()
        if busy:
            _say(busy)
            return 2

    covers_invariants = set(modules(PACKAGE / "invariants")) <= set(names)
    tree_before = _tree_hash() if covers_invariants else None
    recorded = read_durations()
    started = time.perf_counter()
    try:
        if processes == 1:
            shards = [Shard(0, names)]
            run_modules(names, sys.stderr, shards[0].records.append)
            _say()
        else:
            shards = plan(names, weights(names, recorded), processes)
            untimed = sum(name not in recorded for name in names)
            _say(f"{len(names)} modules in {processes} processes, planned at about "
                 f"{max(shard.planned for shard in shards):.0f}s"
                 + (f"; {untimed} of them never timed" if untimed else ""))
            in_processes(shards)
    except KeyboardInterrupt:
        _say("\nInterrupted.")
        return 130
    finally:
        if whole:
            wholesuite.release()
    seconds = time.perf_counter() - started

    records = [record for shard in shards for record in shard.records]
    if records:
        write_durations(recorded_after(recorded, {r["module"]: r["seconds"] for r in records}))
    closing, code = summary(records, seconds, sum(bool(shard.stopped) for shard in shards))
    count: list[str] = []
    if whole:
        sandboxed = sandboxed_modules(records)
        wholesuite.record(seconds, sum(r["tests"] for r in records), code == 0,
                          failed=failed_modules(shards), sandboxed=sandboxed)
        runs, spent = wholesuite.today()
        count = [f"Whole-suite runs today: {runs}, {spent / 60:.0f} minutes."]
        if sandboxed:
            count.append(f"{len(sandboxed)} browser modules skipped: they ran in the sandbox "
                         f"and have not passed.")
    if left_out:
        count.append(f"Left out, needed only before a release (--release runs them): "
                     f"{', '.join(left_out)}")
    if tree_before and code == 0 and _tree_hash() == tree_before:
        stamp.record(REPO, tree_before)
    for line in [*failures(shards), "-" * 70, *slowest(records, recorded), *count, "-" * 70,
                 *closing]:
        _say(line)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
