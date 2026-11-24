"""Talking to DOF, the cabinet's lights and solenoids.

DOF runs in a child process (`--dof-helper`) rather than in ours: it loads native
libraries that can take the whole app down with them, and a cabinet losing its lights
should not lose its frontend.
"""

from __future__ import annotations

import json
import logging
import os
import random
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from common import service_errors
from common.config.config_access import cfg_bool
from common.config.config_store import ConfigStore
from common.config.paths import APP_ROOT
from common.host import dof_config, launch_state
from common.host.third_party import (
    find_named_path,
    import_module_from_path,
    third_party_base_candidates,
)
from common.i18n import t

_LOCK = threading.Lock()
_HELPER = None
_CURRENT_EVENT = None
_RUNNER_NAMES = ('dof_runner.py', 'runner_dof.py', 'random_dof_runner.py')
_EVENT_TOKEN_RE = re.compile(r'^([A-Za-z])(\d+)$')
# A controller type that logs none of these when it comes up is not counted as a board.
_BOARD_UP_RE = re.compile(
    r'\binitialized\b'
    r'|\bDetected and added\b'
    r'|\bFound Pinscape device\b'
    r'|\bconnected successfully\b'
    r'|\bPinscape Pico discovery found\b'
    r'|\bDudesCab Controller Informations\b',
    re.IGNORECASE)
# libdof logs "initialized" for a LedWiz it could not connect to, just after saying so.
_LEDWIZ_DOWN_RE = re.compile(r'\bLedWiz\s+(?:LedWiz\s+)?(\d+)\s+could not connect', re.IGNORECASE)
_LEDWIZ_UP_RE = re.compile(r'\bLedWiz\s+Nr\.\s*(\d+)\s+initialized', re.IGNORECASE)
# How long after the runner starts a board may still report.
BOARD_WAIT = 3.0
logger = logging.getLogger("vpinfe.common.host.dof_service")


def _is_enabled(iniconfig: ConfigStore | None) -> bool:
    """Whether the user asked for DOF.

    Through cfg_bool, which resolves the canonical `enable_dof` and the `enabledof` an
    older file still carries. Reading the parser directly for the old spelling looks
    like a fallback and is not: getboolean with fallback=False *returns* False for a key
    that is not there rather than raising, so the branch meant to try the new name never
    runs and DOF stays off for anyone whose config has been migrated.
    """
    return cfg_bool(iniconfig, 'dof', 'enabled', False)


def _find_named_path(base: Path, names: tuple[str, ...]) -> Path | None:
    return find_named_path(base, names)


def _find_runner_path(base: Path) -> Path | None:
    return _find_named_path(base, _RUNNER_NAMES)


def _get_dof_base_candidates() -> list[Path]:
    return third_party_base_candidates('VPINFE_DOF_DIR', 'dof')


class RunnerLoadError(Exception):
    """A DOF runner is installed and cannot be used."""


def _load_library() -> None:
    """Load libdof now. The runner loads it on its own thread after `start()` has already
    answered yes, so a library that will not load would otherwise show as a thread that
    quietly died."""
    loader = getattr(sys.modules.get('dof'), 'load_lib', None)
    if callable(loader):
        loader()


def _load_runner_class() -> tuple[Any, Path]:
    """The runner class and its folder. The class is None when no runner is installed;
    raises RunnerLoadError when one is and will not load."""
    candidates = _get_dof_base_candidates()
    runner_path = None
    for candidate in candidates:
        found = _find_runner_path(candidate)
        if found is not None:
            runner_path = found
            break

    if runner_path is None:
        logger.warning(
            "enabledof=true but runner not found. Checked: %s",
            ", ".join(str(c) for c in candidates),
        )
        return None, candidates[0]

    dof_dir = runner_path.parent

    try:
        module = import_module_from_path(runner_path)
    except Exception as e:
        logger.error("Failed importing dof_runner.py: %s", e)
        raise RunnerLoadError from e

    runner_class = getattr(module, 'SingleEventDofRunner', None)
    if runner_class is None:
        logger.error("SingleEventDofRunner class not found in dof_runner.py")
        raise RunnerLoadError
    try:
        _load_library()
    except Exception as e:  # noqa: BLE001 - the loader raises OSError, ImportError or ctypes errors
        logger.error("The DOF library would not load: %s", e)
        raise RunnerLoadError from e
    return runner_class, dof_dir


class _DofHelperProcess:
    """The child process holding DOF, kept alive and restarted if it dies."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen[str] | None = None
        self._reader_thread: threading.Thread | None = None
        self._pending: dict[int, dict[str, Any]] = {}
        self._cv = threading.Condition()
        self._next_request_id = 1
        self._expected_shutdown = False
        self._unexpected_exit_code: int | None = None
        self._runner_started_at: float | None = None
        self._boards_up = 0
        self._ledwiz_down: set[int] = set()

    def _command(self) -> list[str]:
        if getattr(sys, 'frozen', False):
            return [sys.executable, '--dof-helper']
        return [sys.executable, '-m', 'common.host.dof_service_worker']

    def _ensure_started(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True

        cmd = self._command()
        env = os.environ.copy()
        env['PYTHONUNBUFFERED'] = '1'
        project_root = str(APP_ROOT)
        try:
            self._expected_shutdown = False
            self._unexpected_exit_code = None
            self._proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                env=env,
                cwd=project_root,
            )
        except Exception as exc:
            logger.error("Failed to launch DOF helper: %s", exc)
            self._proc = None
            return False

        self._reader_thread = threading.Thread(
            target=self._reader_main,
            name="dof-helper-reader",
            daemon=True,
        )
        self._reader_thread.start()
        return True

    def _reader_main(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return

        try:
            for raw_line in proc.stdout:
                line = raw_line.rstrip('\n')
                if not line:
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    logger.error("[libdof helper] %s", line)
                    continue

                if payload.get("type") == "log":
                    self._handle_log(payload)
                    continue
                if payload.get("type") == "response":
                    with self._cv:
                        request_id = payload.get("id")
                        pending = self._pending.get(request_id)
                        if pending is not None:
                            pending["response"] = payload
                            self._cv.notify_all()
                    continue
                logger.debug("Unhandled DOF helper payload: %s", payload)
        finally:
            return_code = proc.poll()
            with self._cv:
                for pending in self._pending.values():
                    pending["response"] = {
                        "type": "response",
                        "ok": False,
                        "error": f"helper_exited:{return_code}",
                    }
                self._cv.notify_all()

            if not self._expected_shutdown:
                self._unexpected_exit_code = return_code
            if not self._expected_shutdown and return_code not in (None, 0):
                logger.error("DOF helper exited unexpectedly with code %s", return_code)
            elif not self._expected_shutdown:
                logger.warning("DOF helper exited unexpectedly with code %s", return_code)

    def _handle_log(self, payload: dict[str, Any]) -> None:
        level_name = str(payload.get("level", "INFO")).upper()
        message = str(payload.get("message", ""))
        with self._cv:
            if self._reports_board(message):
                self._boards_up += 1
                self._cv.notify_all()
        if level_name == "DEBUG":
            logger.debug("[libdof] %s", message)
        elif level_name == "INFO":
            logger.info("[libdof] %s", message)
        elif level_name in {"WARN", "WARNING"}:
            logger.warning("[libdof] %s", message)
        elif level_name == "ERROR":
            logger.error("[libdof] %s", message)
        else:
            logger.info("[libdof] %s", message)

    def request(self, command: str, timeout: float = 10.0, **payload: Any) -> dict[str, Any] | None:
        if not self._ensure_started():
            return None

        proc = self._proc
        if proc is None or proc.stdin is None:
            return None

        with self._cv:
            request_id = self._next_request_id
            self._next_request_id += 1
            self._pending[request_id] = {}

        message = {"id": request_id, "command": command}
        message.update(payload)
        before = self._boards_started() if command == "start" else None
        encoded = json.dumps(message) + "\n"

        try:
            proc.stdin.write(encoded)
            proc.stdin.flush()
        except Exception as exc:
            logger.error("Failed sending DOF helper command %s: %s", command, exc)
            with self._cv:
                self._pending.pop(request_id, None)
            return None

        with self._cv:
            state = self._pending[request_id]
            got_response = self._cv.wait_for(lambda: "response" in state, timeout=timeout)
            response = state.get("response")
            self._pending.pop(request_id, None)

        if before is not None:
            self._boards_settled(before, bool(response and response.get("started")))
        if not got_response or response is None:
            logger.error("Timed out waiting for DOF helper command: %s", command)
            return None
        return response

    def _reports_board(self, message: str) -> bool:
        """Whether `message` says a board came up. Called with `_cv` held."""
        down = _LEDWIZ_DOWN_RE.search(message)
        if down:
            self._ledwiz_down.add(int(down.group(1)))
            return False
        up = _LEDWIZ_UP_RE.search(message)
        if up and int(up.group(1)) in self._ledwiz_down:
            return False
        return bool(_BOARD_UP_RE.search(message))

    def _boards_started(self) -> tuple[int, float | None, set[int]]:
        """The board count and start time as they were, then begin counting afresh."""
        with self._cv:
            was = (self._boards_up, self._runner_started_at, self._ledwiz_down)
            self._boards_up = 0
            self._ledwiz_down = set()
            self._runner_started_at = time.monotonic()
        return was

    def _boards_settled(self, was: tuple[int, float | None, set[int]], started: bool) -> None:
        if started:
            return
        with self._cv:
            self._boards_up += was[0]
            self._runner_started_at = was[1]
            self._ledwiz_down |= was[2]

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def found_no_boards(self) -> bool:
        """True once the runner has had `BOARD_WAIT` seconds and no board reported in.
        Holds the caller until the first board does, or that time is up."""
        with self._cv:
            if self._runner_started_at is None:
                return False
            left = self._runner_started_at + BOARD_WAIT - time.monotonic()
            if left > 0:
                self._cv.wait_for(lambda: self._boards_up > 0, timeout=left)
            return self._boards_up == 0

    def consume_unexpected_exit_code(self) -> int | None:
        code = self._unexpected_exit_code
        self._unexpected_exit_code = None
        return code

    def close(self, timeout: float = 10.0) -> bool:
        proc = self._proc
        if proc is None:
            return True

        self._expected_shutdown = True
        self.request("shutdown", timeout=timeout, service_timeout=timeout)
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            proc.terminate()
            try:
                proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=2.0)

        self._proc = None
        return True


def is_running() -> bool:
    global _HELPER
    with _LOCK:
        if _HELPER is None:
            return False
        if _HELPER.is_alive():
            return True
        exit_code = _HELPER.consume_unexpected_exit_code()
        if exit_code is not None:
            logger.warning("Detected stopped DOF helper process; last exit code was %s", exit_code)
        _HELPER = None
        return False


def no_boards() -> bool:
    """Whether DOF is up and no board has said it is connected. Waits, once, for boards
    that are still starting."""
    with _LOCK:
        helper = _HELPER
    return helper is not None and helper.is_alive() and helper.found_no_boards()


def find_dof_file(*names: str) -> Path | None:
    candidates = _get_dof_base_candidates()
    valid_names = tuple(name for name in names if name)
    if not valid_names:
        return None

    for candidate in candidates:
        found = _find_named_path(candidate, valid_names)
        if found is not None:
            return found
    return None


def _get_helper() -> _DofHelperProcess:
    global _HELPER
    if _HELPER is None:
        _HELPER = _DofHelperProcess()
    return _HELPER


@dataclass(frozen=True)
class Outcome:
    """What became of a request to DOF. Truthy when it went through; `reason` says why not."""

    ok: bool
    reason: str = ""
    detail: str = ""

    def __bool__(self) -> bool:
        return self.ok

    def words(self) -> str:
        """The sentence a person reads for a refusal."""
        if self.reason == FAILED and self.detail:
            return t("dof.event.failed_because", detail=self.detail)
        return t(_REASON_WORDS.get(self.reason, "dof.event.failed"))


OK = Outcome(True)
OFF = "off"
PLAYING = "playing"
NO_RUNNER = "no_runner"
UNLOADABLE = "unloadable"
NO_CONFIG = "no_config"
NO_HELPER = "no_helper"
FAILED = "failed"
SAME = "same"
_REASON_WORDS = {
    OFF: "dof.event.off",
    PLAYING: "dof.event.playing",
    NO_RUNNER: "dof.event.no_runner",
    UNLOADABLE: "dof.event.unloadable",
    NO_CONFIG: "dof.event.no_config",
    NO_HELPER: "dof.event.no_helper",
    FAILED: "dof.event.failed",
}


def _sentence(text: object) -> str:
    """The runner's own words, or nothing: a code like `runner_not_running` is for the log."""
    said = str(text or "").strip()
    return said if " " in said else ""


def _start(iniconfig: ConfigStore | None) -> tuple[bool, Outcome]:
    """Start the helper. The bool is whether this call started it; the outcome is why not."""
    global _HELPER, _CURRENT_EVENT
    if not _is_enabled(iniconfig):
        return False, Outcome(False, OFF)

    with _LOCK:
        if launch_state.current().launching:
            return False, Outcome(False, PLAYING)
        restarting_after_crash = False
        if _HELPER is not None and not _HELPER.is_alive():
            exit_code = _HELPER.consume_unexpected_exit_code()
            if exit_code is not None:
                restarting_after_crash = True
                logger.warning(
                    "Restarting DOF helper after unexpected exit (code %s)",
                    exit_code,
                )
            _HELPER = None
        helper = _get_helper()
        base = dof_config.base_path()
        response = helper.request("start", timeout=10.0,
                                  base_path=str(base) if base is not None else "")

    if response is None:
        return False, Outcome(False, NO_HELPER)
    if not response.get("ok", False):
        error = str(response.get("error", "unknown"))
        logger.error("Failed to start DOF helper: %s", error)
        return False, Outcome(False, FAILED)

    started = bool(response.get("started"))
    if started:
        _CURRENT_EVENT = None
        logger.info("Service started using dof_runner.py from %s", response.get("detail"))
        if restarting_after_crash:
            logger.info("DOF helper recovered and is running again.")
        return True, OK

    detail = str(response.get("detail", ""))
    logger.info("Service start requested but runner was not started: %s", detail)
    if detail == "runner_unavailable":
        return False, Outcome(False, NO_RUNNER)
    if detail == "runner_unloadable":
        return False, Outcome(False, UNLOADABLE)
    if detail.startswith("start_failed"):
        return False, Outcome(False, FAILED, _sentence(detail.partition(":")[2]))
    return False, OK


def start_dof_service_if_enabled(iniconfig: ConfigStore | None) -> bool:
    return _start(iniconfig)[0]


def stop_dof_service(timeout: float = 10.0) -> bool:
    global _HELPER, _CURRENT_EVENT
    with _LOCK:
        helper = _HELPER
        if helper is None:
            return True
        _HELPER = None

    logger.info("Stopping service...")
    stopped = helper.close(timeout=timeout)
    _CURRENT_EVENT = None
    return stopped


def restart_dof_service_if_enabled(iniconfig: ConfigStore | None) -> bool:
    stop_dof_service()
    return start_dof_service_if_enabled(iniconfig)


def reload_config(iniconfig: ConfigStore | None) -> bool:
    """Start the helper over so it reads config just written. Left alone when it is not
    running, or while a table plays: VPX reads the files at each table start, and the
    helper starts again after every table."""
    if not is_running() or launch_state.current().launching:
        return False
    return restart_dof_service_if_enabled(iniconfig)


def normalize_event_token(event_token: str | None) -> str | None:
    token = str(event_token or '').strip().upper()
    if not token:
        return None

    match = _EVENT_TOKEN_RE.fullmatch(token)
    if not match:
        raise ValueError(f"Invalid DOF event token: {event_token!r}")

    type_char, number_text = match.groups()
    return f"{type_char}{int(number_text)}"


def _resolve_frontend_event_token(event_token: str | None) -> str:
    normalized = normalize_event_token(event_token)
    if normalized:
        return normalized
    return f"E{random.randint(900, 990)}"


def send_dof_event_token(iniconfig: ConfigStore | None, event_token: str) -> Outcome:
    """Turn an event on. Falsy, with a reason, when it did not go out.

    Refuses while a table plays: the helper must never start under Visual Pinball, which
    holds the same boards. Sending the event already on is a no-op.
    """
    global _CURRENT_EVENT
    if not _is_enabled(iniconfig):
        return Outcome(False, OFF)

    resolved_event = normalize_event_token(event_token)
    if not resolved_event:
        raise ValueError("DOF event token is required.")

    if launch_state.current().launching:
        return Outcome(False, PLAYING)

    with _LOCK:
        current_event = _CURRENT_EVENT

    if current_event == resolved_event:
        return Outcome(False, SAME)

    if not is_running():
        started, why = _start(iniconfig)
        if not started and not is_running():
            return why if not why.ok else Outcome(False, FAILED)

    if not dof_config.present():
        # A live helper does not mean a live runner: starting again says whether the runner
        # or the library is what is missing before the config is blamed.
        _, why = _start(iniconfig)
        return why if not why.ok else Outcome(False, NO_CONFIG)

    response = _ask_to_send(resolved_event)
    if response is not None and response.get("error") == "runner_not_running":
        # The helper is up and its runner is not: starting again says why.
        started, why = _start(iniconfig)
        if not started:
            return why if not why.ok else Outcome(False, FAILED)
        response = _ask_to_send(resolved_event)

    if response is None or not response.get("ok", False):
        error = None if response is None else response.get("error", "unknown")
        logger.error("Failed to send frontend DOF event %s: %s", resolved_event, error)
        return Outcome(False, NO_HELPER if response is None else FAILED)

    _CURRENT_EVENT = resolved_event
    logger.debug("Sent frontend DOF event: %s", resolved_event)
    return OK


def _ask_to_send(event_token: str) -> dict[str, Any] | None:
    with _LOCK:
        return _get_helper().request("send_event_token", timeout=5.0, event_token=event_token)


def current_event() -> str | None:
    """The event on now, from this process's own record of it."""
    return _CURRENT_EVENT


def blocked(iniconfig: ConfigStore | None) -> Outcome:
    """Falsy, with a reason, while an event could not be turned on right now."""
    if not _is_enabled(iniconfig):
        return Outcome(False, OFF)
    if launch_state.current().launching:
        return Outcome(False, PLAYING)
    return OK


def test_event(iniconfig: ConfigStore | None, event_token: str) -> Outcome:
    """Fire an event for a person to see, even one that is already on."""
    if normalize_event_token(event_token) == _CURRENT_EVENT:
        clear_active_dof_event(iniconfig)
    return send_dof_event_token(iniconfig, event_token)


def clear_active_dof_event(iniconfig: ConfigStore | None) -> bool:
    global _CURRENT_EVENT
    if not _is_enabled(iniconfig):
        return False

    if not is_running():
        return False

    with _LOCK:
        helper = _get_helper()
        response = helper.request("clear_event", timeout=5.0)

    if response is None or not response.get("ok", False):
        logger.error(
            "Failed to clear active DOF event: %s",
            None if response is None else response.get("error", "unknown"),
        )
        return False

    _CURRENT_EVENT = None
    logger.debug("Cleared active frontend DOF event")
    return bool(response.get("cleared", False))


_REFUSALS: dict[str, type[service_errors.ServiceError]] = {
    OFF: service_errors.UnavailableError,
    NO_RUNNER: service_errors.UnavailableError,
    UNLOADABLE: service_errors.UnavailableError,
}


def turn_on(iniconfig: ConfigStore | None, said: str) -> str:
    """Fire the event a person typed, and the token it comes to. Raises a refusal with the
    reason in words when it does not go out."""
    try:
        event = normalize_event_token(said)
    except ValueError:
        event = None
    if not event:
        raise service_errors.RefusedError(t("dof.event.malformed"))

    outcome = test_event(iniconfig, event)
    if not outcome and outcome.reason != SAME:
        raise _REFUSALS.get(outcome.reason, service_errors.BlockedError)(outcome.words())
    return event


def send_frontend_dof_event(iniconfig: ConfigStore | None,
                            event_token: str | None = None) -> Outcome:
    resolved_event = _resolve_frontend_event_token(event_token)
    return send_dof_event_token(iniconfig, resolved_event)
