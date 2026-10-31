"""What can be pressed on the table that is running, and pressing it.

The keys are the app's and never leave it: a caller names a control by id and the key is
read from the launcher's settings file at the press. A keystroke goes to whichever window
has focus, so `pressed` says the key was sent, not that the table acted on it.
"""

from __future__ import annotations

import atexit
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any

from common import apps, service_errors
from common.apps.contract import PAUSE, Control
from common.host import input_actions, key_simulator, launch_state
from common.i18n import t

logger = logging.getLogger("vpinfe.common.host.table_controls")

# One press at a time on an install: two held keys would be read as one chord.
_pressing = threading.Lock()

TAP = "tap"
PRESS = "press"
RELEASE = "release"



@dataclass
class _Down:
    """A control held down: the timer that lets go of it if the renewals stop, and the key
    to let go of, which is the one that went down even if the settings file has since
    changed. `timer` is compared at the expiry, so a callback that fired just as a renewal
    replaced it cannot let go of the renewed hold."""

    timer: threading.Timer
    code: str
    sender: key_simulator.Sender


_down: dict[str, _Down] = {}
# Where a key cannot be left down, when each control last got its one tap, so the renewals
# that follow are not read as more taps.
_tapped: dict[str, float] = {}


class CannotSendKeysError(service_errors.UnavailableError):
    """This device has no way to press keys; the message says what it needs."""


def _running() -> tuple[launch_state.LaunchState, launch_state.RunningControls | None]:
    """The launch state and the controls of the table being played; no controls where
    nothing plays, a recording owns the table or its app declares none."""
    now, held = launch_state.state_and_controls()
    if not now.launching or now.source == launch_state.SOURCE_CAPTURE:
        return now, None
    return now, held


def _label(control: Control) -> str:
    return t(f"controls.{control.id}")


def _cause(held: launch_state.RunningControls, control: Control) -> str:
    """Why `control` cannot be pressed: the app has no single key for it, or the one it has
    is not a key this device can send."""
    reason = held.why_not(control.id)
    if not reason and not key_simulator.can_press(held.key(control.id)):
        reason = "not_sendable"
    return reason or "unmapped"


def _why(held: launch_state.RunningControls, control: Control) -> str:
    return t(f"controls.reason.{_cause(held, control)}",
             app=apps.app_name(held.app), control=_label(control))


def _shown(held: launch_state.RunningControls) -> list[Control]:
    """What a caller is told about: all of it, except the controls that exist only where
    the app maps them to a key."""
    return [one for one in held.offered if not one.when_mapped or _pressable(held, one)]


def _notes(held: launch_state.RunningControls) -> list[dict[str, Any]]:
    """One sentence per cause the offered controls share, naming them, so a caller says
    it once instead of under every control. Pause has a key of its own and says its own
    reason, so no note names it."""
    shared: dict[str, list[Control]] = {}
    for control in _shown(held):
        if control.id != PAUSE and not _pressable(held, control):
            shared.setdefault(_cause(held, control), []).append(control)
    return [{"controls": [one.id for one in sharing],
             "text": t(f"controls.note.{cause}", app=apps.app_name(held.app),
                       controls=_listed([_label(one) for one in sharing]))}
            for cause, sharing in shared.items()]


def _listed(names: list[str]) -> str:
    """Names read out in a sentence: "A", "A and B", "A, B and C"."""
    if len(names) < 2:
        return "".join(names)
    return t("controls.and_last", most=", ".join(names[:-1]), last=names[-1])


def _pressable(held: launch_state.RunningControls, control: Control) -> str:
    """The key of `control`, or "" where there is none to send."""
    code = held.key(control.id)
    return code if code and key_simulator.can_press(code) else ""


def listing() -> dict[str, Any]:
    """The running table and what it offers, in the install's language."""
    now, held = _running()
    press = key_simulator.sender().as_dict()
    if held is None:
        return {"table": None, "press": press, "controls": [], "notes": []}
    return {
        "table": {"game_id": now.game_id, "game_name": now.game_name, "app": held.app},
        "press": press,
        "controls": [_entry(held, one) for one in _shown(held)],
        "notes": _notes(held),
    }


def _entry(held: launch_state.RunningControls, control: Control) -> dict[str, Any]:
    pressable = bool(_pressable(held, control))
    return {"id": control.id, "group": control.group, "label": _label(control),
            "hold_ms": control.hold_ms, "holdable": control.holdable,
            "side": control.side,
            "available": pressable,
            "reason": "" if pressable else _why(held, control)}


def _refuse_without_controls(now: launch_state.LaunchState,
                             control_id: str) -> service_errors.ServiceError:
    """Why there are none: a recording owns the table, nothing plays, or its app offers
    none, which makes every id one it does not declare."""
    if now.launching and now.source == launch_state.SOURCE_CAPTURE:
        return service_errors.BlockedError(t("controls.blocked.recording"))
    if not now.launching:
        return service_errors.BlockedError(t("controls.blocked.nothing"))
    return _not_declared(control_id)


def _not_declared(control_id: str) -> service_errors.NotFoundError:
    return service_errors.NotFoundError(t("controls.not_found"),
                                        details={"control_id": control_id})


def press(control_id: str, *, source: str, phase: str = TAP,
          ttl_ms: int | None = None) -> dict[str, Any]:
    """Send the key of one control of the running table.

    A tap sends the control's key held for its `hold_ms`. On a `holdable` control, a
    `press` leaves the key down and a `release` lets it go; a press is renewed by sending
    it again, and the key is let go of on its own when `ttl_ms` passes without one, so a
    release that never arrives does not leave a flipper up. Where this device cannot leave
    a key down, a press is one tap and a release does nothing.

    Raises `BlockedError` where nothing is playing, a recording owns the table, the
    control has no key, or it cannot be held; `NotFoundError` for an id the app does not
    declare; and `CannotSendKeysError` where this device cannot send keys.
    """
    if phase == RELEASE:
        # Whatever is down is let go of, even where the table has since ended.
        return {"control_id": control_id, "pressed": _let_go(control_id, source)}
    now, held = _running()
    if held is None:
        raise _refuse_without_controls(now, control_id)
    control = next((one for one in held.offered if one.id == control_id), None)
    if control is None:
        raise _not_declared(control_id)
    code = _pressable(held, control)
    if not code:
        raise service_errors.BlockedError(_why(held, control),
                                          details={"control_id": control.id})
    if phase == PRESS and not control.holdable:
        raise service_errors.BlockedError(t("controls.not_holdable"),
                                          details={"control_id": control.id})
    sender = key_simulator.sender()
    if not sender.available:
        raise CannotSendKeysError(key_simulator.words(sender.reason))
    ttl = input_actions.clamp_ttl(ttl_ms) / 1000
    with _pressing:
        if phase == PRESS and key_simulator.can_hold(sender):
            return {"control_id": control.id,
                    "pressed": _keep_down(control.id, code, sender, source, ttl)}
        if phase == PRESS:
            moment = time.monotonic()
            if moment - _tapped.get(control.id, -ttl) < ttl:
                return {"control_id": control.id, "pressed": True}
            _tapped[control.id] = moment
        logger.info("Pressing the table's %s control, from %s", control.id, source)
        sent = (key_simulator.hold_code(code, control.hold_ms / 1000, via=sender)
                if control.hold_ms else key_simulator.press_code(code, via=sender))
    return {"control_id": control.id, "pressed": bool(sent)}


def _keep_down(control_id: str, code: str, sender: key_simulator.Sender, source: str,
               ttl: float) -> bool:
    """Caller holds `_pressing`. Down once, and only the expiry renewed after that."""
    standing = _down.pop(control_id, None)
    if standing is not None:
        standing.timer.cancel()
        code, sender = standing.code, standing.sender
        sent = True
    else:
        logger.info("Holding the table's %s control down, from %s", control_id, source)
        sent = key_simulator.down_code(code, via=sender)
    if sent:
        timer = threading.Timer(ttl, lambda: _expired(control_id, source, timer))
        timer.daemon = True
        _down[control_id] = _Down(timer, code, sender)
        timer.start()
    return sent


def _let_go(control_id: str, source: str) -> bool:
    """False where nothing was down, which a release arriving twice makes ordinary."""
    with _pressing:
        _tapped.pop(control_id, None)
        standing = _down.pop(control_id, None)
        if standing is None:
            return False
        standing.timer.cancel()
        logger.info("Letting go of the table's %s control, from %s", control_id, source)
        return key_simulator.up_code(standing.code, via=standing.sender)


def _expired(control_id: str, source: str, timer: threading.Timer) -> None:
    """The renewals stopped, so let go on the caller's behalf. Logged above debug: each one
    is a release that was lost or a caller that stopped without saying so."""
    with _pressing:
        standing = _down.get(control_id)
        if standing is None or standing.timer is not timer:
            return
        del _down[control_id]
        logger.info("The table's %s control held by %s expired without a release",
                    control_id, source)
        key_simulator.up_code(standing.code, via=standing.sender)


def release_all() -> None:
    """Let go of every key still down, for a process going away with one held."""
    with _pressing:
        for control_id in list(_down):
            standing = _down.pop(control_id)
            standing.timer.cancel()
            try:
                key_simulator.up_code(standing.code, via=standing.sender)
            except Exception:
                logger.warning("Could not let go of the table's %s control", control_id,
                               exc_info=True)
        _tapped.clear()


atexit.register(release_all)
