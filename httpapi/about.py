"""What this install and this machine are, as one answer somebody can paste.

Grouped and labelled server-side, and served with the plain-text rendering beside the
groups. A client that laid the rows out itself would be a second place for the order to
be decided, and the copied text and the visible text would drift apart on the day one of
them gained a field.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from common.host import about

from . import scopes
from .auth import requires

router = APIRouter(prefix="/about", tags=["about"])


@router.get("", summary="What this install and this device are",
            dependencies=[requires(scopes.SYSTEM_READ)])
def read(refresh: bool = False) -> dict[str, Any]:
    """Read once per process and kept, except the displays, which are held for 30 seconds.

    Every field costs a subprocess, a config read or a walk of the install, and none of
    them changes while VPinFE runs - so `refresh` is there for the case somebody has
    changed a setting and wants to see it, not for a caller on a timer.
    """
    return {"groups": about.described(refresh), "text": about.as_text()}
