"""How a user has arranged the UI, so it follows them between devices."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body

from common.config import ui_preferences

from . import scopes
from .auth import requires

logger = logging.getLogger("vpinfe.httpapi.preferences")

router = APIRouter(prefix="/preferences", tags=["preferences"])


@router.get("/{scope}", summary="A stored UI arrangement",
            dependencies=[requires(scopes.PREFERENCES_READ)])
def read(scope: str) -> dict[str, Any]:
    return {"scope": scope, "value": ui_preferences.get(scope)}


@router.put("/{scope}", summary="Store a UI arrangement",
            dependencies=[requires(scopes.PREFERENCES_WRITE)])
def write(scope: str, value: dict[str, Any] = Body(...)) -> dict[str, Any]:
    return {"scope": scope, "value": ui_preferences.put(scope, value)}
