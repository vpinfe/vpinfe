"""The USB, serial and display devices attached to this device, as an extension is allowed
to see them: `ctx.hardware`.

Reading only, under `hardware:read`. Opening a device is the extension's own business.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .offered import Offered, Registry

HARDWARE: Registry = {}


def offer_hardware(name: str, scope: str, run: Callable[..., Any]) -> None:
    """Core: let extensions call this on `ctx.hardware`, for anything declaring `scope`."""
    HARDWARE[name] = (scope, run)


class ExtensionHardware(Offered):
    def __init__(self, name: str, scopes: Any) -> None:
        super().__init__(name, scopes, HARDWARE)
