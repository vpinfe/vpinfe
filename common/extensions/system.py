"""Restarting VPinFE, rebooting and powering off, as an extension asks for them: `ctx.system`.

The same lifecycle requests every other surface makes, so the person's own confirm
settings and the per-OS commands in `common/host/system_actions.py` apply unchanged and an
extension never sees the differences between operating systems.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from typing import Any

from common.devices import device_client
from common.i18n import t
from common.runtime import lifecycle

from . import reach
from .contract import ContractError

SYSTEM_ADMIN = "system:admin"

# Long enough for the answer to the request that asked to leave the process.
DELAY = 1.0


def _perform(log: logging.Logger, scope: str, action: str, origin: lifecycle.Origin,
             reason: str) -> None:
    """The request made after the delay. It runs where nobody awaits an answer, so a refusal
    or a failure is logged here or it is lost."""
    what = lifecycle.describe(scope, action)
    try:
        done = device_client.local().request(scope, action, origin=origin, reason=reason)
    except Exception:
        log.exception("could not %s", what)
        return
    if not done:
        log.warning("did not %s: nothing performed it or it was declined", what)


class ExtensionSystem:
    def __init__(self, name: str, scopes: Iterable[str]) -> None:
        self._name = name
        self._scopes = frozenset(scopes)

    def restart_vpinfe(self, reason: str = "") -> dict[str, Any]:
        """Restart VPinFE on this device."""
        return self._ask(lifecycle.VPINFE, lifecycle.RESTART, reason)

    def reboot(self, reason: str = "") -> dict[str, Any]:
        """Reboot this device."""
        return self._ask(lifecycle.SYSTEM, lifecycle.RESTART, reason)

    def shut_down(self, reason: str = "") -> dict[str, Any]:
        """Power this device off."""
        return self._ask(lifecycle.SYSTEM, lifecycle.STOP, reason)

    def _ask(self, scope: str, action: str, reason: str) -> dict[str, Any]:
        """Refused unless `system:admin` is declared and the request being answered
        reached Protected."""
        if SYSTEM_ADMIN not in self._scopes:
            raise ContractError(
                f"{self._name} asks core to {lifecycle.describe(scope, action)}, which needs "
                f"{SYSTEM_ADMIN} and its manifest does not declare it")
        mark = reach.current()
        if mark is None:
            raise PermissionError(t("error.os.no_request"))
        if mark.tier != reach.PROTECTED:
            raise PermissionError(t("error.os.not_from_here"))
        if not lifecycle.performable(scope, action):
            raise PermissionError(t("error.actions.unsupported"))

        who = f"{self._name}: {reason.strip()}" if reason.strip() else self._name
        origin = lifecycle.Origin(lifecycle.SURFACE_EXTENSION, self._name)
        log = logging.getLogger(f"vpinfe.ext.{self._name}")
        log.info("asked to %s from %s%s", lifecycle.describe(scope, action),
                 mark.origin or "a request", f": {reason.strip()}" if reason.strip() else "")
        # Off the request, so the answer goes out before the process or the machine does.
        timer = threading.Timer(DELAY, _perform, (log, scope, action, origin, who))
        timer.daemon = True
        timer.start()
        return {"scope": scope, "action": action,
                "what": lifecycle.label(scope, action), "scheduled": True}
