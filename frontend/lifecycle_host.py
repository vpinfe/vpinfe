"""Binds the lifecycle vocabulary to the things that actually start and stop.

Every stop and restart of this process ends the same way: `run_frontend_loop` returns and
`main.py` runs `shutdown_services`. Reaching for the browser directly skips it, so those
performers only ever make that loop return.

Two stops are the exception and leave the loop alone. Stopping the frontend closes its
windows and VPinFE keeps running; starting or restarting it opens them again. Stopping a
table is the other: the table is a child of this install rather than a part of it, and
closing one leaves VPinFE and its windows up.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from common import events
from common.config.config_access import cfg_bool
from common.config.config_store import ConfigStore
from common.host import launch_state, system_actions
from common.runtime import lifecycle

if TYPE_CHECKING:
    from frontend.chromium_manager import ChromiumManager
    from frontend.device_channel import DeviceChannel

logger = logging.getLogger("vpinfe.frontend.lifecycle_host")

_config_store = None
_bridge = None
_exit_closes_windows = False


def confirm_scopes() -> tuple[str, ...]:
    """What asks first: quitting VPinFE and powering off the machine, or nothing.

    Stopping the frontend is not in it. The windows reopen from the Manager, so there
    is nothing to lose and it is the scope you would meet most often - a prompt there is
    the one that trains you to dismiss prompts.
    """
    if _config_store is None or not cfg_bool(_config_store, "behavior", "confirm"):
        return ()
    return (lifecycle.VPINFE, lifecycle.SYSTEM)


def wants_confirmation(scope: str) -> bool:
    """Whether a surface should put the question to the user before asking us. The
    bridge to a window is one-way, so the browser asks and reports the answer."""
    return scope in confirm_scopes()


def _announce(request: lifecycle.Request) -> None:
    events.emit(
        events.LIFECYCLE_ACTING,
        scope=request.scope,
        action=request.action,
        surface=request.origin.surface,
        address=request.origin.address,
        reason=request.reason,
        # The card asks a person, so it takes the label rather than the log's
        # English. `describe()` stays what the log lines use.
        description=lifecycle.label(*request.pair),
    )


def _notice(*, scope: str, action: str, surface: str, address: str,
            **_payload: Any) -> None:
    """Tell the windows what is happening, unless they are the ones who asked.

    The rule is a notice on every listening surface that did not start it. The window
    that did already has the answer on screen, so telling it again would talk over its
    own confirmation.

    Only what takes the windows away is announced. The notice is never taken down, so
    one sent for anything that leaves them up would stay on every screen.
    """
    description = lifecycle.announcement(scope, action)
    if _bridge is None or not description:
        return
    message = {"type": "LifecycleActing", "scope": scope, "action": action,
               "description": description, "origin": surface}
    if surface == lifecycle.SURFACE_FRONTEND and address:
        _bridge.send_event_all(message, exclude=address)
    else:
        _bridge.send_event_all(message)


def _why_not(windows_state: Callable[[], str], runs_when: str | None) -> Callable[[], str]:
    """The reason key for a frontend action that cannot run now, or "" when it can."""
    def why() -> str:
        now = lifecycle.once("windows", windows_state)
        if now == lifecycle.WINDOWS_OFF:
            return "error.actions.frontend_off"
        if now == lifecycle.WINDOWS_SETUP:
            return "error.actions.setup_open"
        if runs_when is None or now == runs_when:
            return ""
        return ("error.actions.windows_open" if now == lifecycle.WINDOWS_OPEN
                else "error.actions.windows_closed")

    return why


def install(*, config_store: ConfigStore,
            frontend_browser: ChromiumManager, shutdown_event: threading.Event,
            ws_bridge: DeviceChannel | None = None,
            open_windows: Callable[[], None] | None = None,
            close_windows: Callable[[], None] | None = None,
            windows_state: Callable[[], str] | None = None,
            exit_closes_windows: bool = False) -> None:
    """Wire this process's performers. Called once, from startup. `windows_state` answers
    one of `lifecycle.WINDOWS_*`, which decides which frontend actions can run.
    `exit_closes_windows` is a run that keeps going when the frontend exits."""
    global _config_store, _bridge, _exit_closes_windows
    _config_store = config_store
    _bridge = ws_bridge
    _exit_closes_windows = exit_closes_windows
    # Unsubscribe first: install is called once at startup, but subscribing twice would
    # send every window two copies of the same notice.
    events.unsubscribe(events.LIFECYCLE_ACTING, _notice)
    if ws_bridge is not None:
        events.subscribe(events.LIFECYCLE_ACTING, _notice)

    def stop_app(_request: lifecycle.Request) -> None:
        # Both: the event ends the run, and the windows are taken down with it unless
        # Setup's wait, which blocks on the browser, is the one still running.
        shutdown_event.set()
        frontend_browser.terminate_all()

    def restart_app(_request: lifecycle.Request) -> None:
        # main.py checks for this after the services are down, so the request has to be
        # recorded before anything unblocks the loop.
        system_actions.request_app_restart()
        stop_app(_request)

    def stop_system(request: lifecycle.Request) -> None:
        system_actions.shutdown_system()
        stop_app(request)

    def restart_system(request: lifecycle.Request) -> None:
        system_actions.reboot_system()
        stop_app(request)

    def stop_table(_request: lifecycle.Request) -> None:
        launch_state.stop()

    lifecycle.register_performer(lifecycle.VPINFE, lifecycle.STOP, stop_app)
    lifecycle.register_performer(lifecycle.VPINFE, lifecycle.RESTART, restart_app)
    lifecycle.register_performer(lifecycle.SYSTEM, lifecycle.STOP, stop_system)
    lifecycle.register_performer(lifecycle.SYSTEM, lifecycle.RESTART, restart_system)
    lifecycle.register_performer(lifecycle.TABLE, lifecycle.STOP, stop_table)

    # Only registered when the caller can open and close windows. Opening is also what
    # restarting is - it closes whatever is up first - so one performer serves both.
    # Closing is the caller's, never terminate_all, which would end the run.
    if open_windows is not None and close_windows is not None:
        lifecycle.register_performer(lifecycle.FRONTEND, lifecycle.STOP,
                                     lambda _request: close_windows())
        lifecycle.register_performer(lifecycle.FRONTEND, lifecycle.START,
                                     lambda _request: open_windows())
        lifecycle.register_performer(lifecycle.FRONTEND, lifecycle.RESTART,
                                     lambda _request: open_windows())
        if windows_state is not None:
            for action, runs_when in ((lifecycle.STOP, lifecycle.WINDOWS_OPEN),
                                      (lifecycle.START, lifecycle.WINDOWS_CLOSED),
                                      # Opening is what reopening does, so it waits only on Setup.
                                      (lifecycle.RESTART, None)):
                lifecycle.register_availability(
                    lifecycle.FRONTEND, action, _why_not(windows_state, runs_when))

    lifecycle.register_notifier(_announce)


def effective(scope: str, action: str, surface: str) -> tuple[str, str]:
    """What a surface's request means on this install. The frontend's Exit quits VPinFE,
    except on a run that keeps going, where it closes the windows and nothing else."""
    if (_exit_closes_windows and surface == lifecycle.SURFACE_FRONTEND
            and (scope, action) == (lifecycle.VPINFE, lifecycle.STOP)
            and lifecycle.performable(lifecycle.FRONTEND, lifecycle.STOP)
            # Setup's window is the person quitting, which close_windows leaves alone.
            and lifecycle.unavailable(lifecycle.FRONTEND, lifecycle.STOP)
            != "error.actions.setup_open"):
        return lifecycle.FRONTEND, lifecycle.STOP
    return scope, action


def request(scope: str, action: str, *, origin: lifecycle.Origin, reason: str = "",
            already_confirmed: bool = False) -> bool:
    """Make a request under this process's configured confirm policy.

    `already_confirmed` satisfies the policy rather than bypassing it: a surface that
    does not ask still gets asked.
    """
    scope, action = effective(scope, action, origin.surface)
    scopes = () if already_confirmed else confirm_scopes()
    return lifecycle.request(scope, action, origin=origin,
                             confirm_scopes=scopes, reason=reason)
