"""2.x's VPinPlay profiles, answered from core's guests.

2.x shipped `get/set/clear_temporary_vpinplay_profile` to themes and a Multi page to hold
several visitors' VPinPlay accounts for a session, one of them active. Both stay, over the
guests core keeps: a profile is a guest holding a VPinPlay account, and the first of them
up is the active one.
"""

from __future__ import annotations

from typing import Any

import requests

from common import service_errors
from common.config.paths import get_ini_config
from common.devices import serving
from common.failures import why
from common.games import player_records
from common.i18n import t
from common.players.players import get_roster
from common.security import http_client

from . import accounts

EXTENSION = "vpinplay"
USER_ID = "user_id"
TIMEOUT = 15


def state() -> dict[str, Any]:
    """The guests holding a VPinPlay account, in the shape 2.x's profiles had. The key
    their account holds is a secret and is never answered: `machineId` is empty."""
    roster = get_roster()
    up = {player.player_id for player in roster.up()}
    records = player_records.get_records()
    held = []
    for guest in [player for player in roster.players() if player.guest]:
        user_id = accounts.values(EXTENSION, guest.player_id).get(USER_ID, "")
        if user_id:
            held.append((guest.player_id, {
                "profileKey": guest.player_id, "userId": user_id,
                "initials": guest.initials, "machineId": "", "sourceName": "",
                "activatedAt": 0, "trackedTables": len(records.games(guest))}))
    active = next((shown for player_id, shown in held if player_id in up), None)
    return {"active": active is not None, "profile": active,
            "active_games": active["trackedTables"] if active else 0,
            "profiles": sorted((shown for _, shown in held),
                               key=lambda shown: shown["userId"].lower()),
            "activeProfileKey": active["profileKey"] if active else ""}


def join(card: str) -> dict[str, Any]:
    """A guest joins from a card's file or its text, through this install's own
    `POST /players/guests/card`, which asks the extension that reads the card. Raises
    RefusedError with why the card was not taken.

    Never call this on the server's event loop: the request waits on the loop it holds.
    """
    port = serving.own_port(get_ini_config())
    url = f"http://127.0.0.1:{port}/api/v1/players/guests/card"
    try:
        response = http_client.post_response(url, {"card": card}, timeout=TIMEOUT)
    except requests.RequestException as exc:
        raise service_errors.RefusedError(why(exc, at=url)) from exc
    if not response.ok:
        try:
            said = str(((response.json() or {}).get("error") or {}).get("message") or "")
        except ValueError:
            said = ""
        raise service_errors.RefusedError(
            said or t("error.players.card_refused", status_code=response.status_code))
    return state()


def choose(player_id: str) -> dict[str, Any]:
    """This guest up alone."""
    get_roster().set_who_is_up([player_id])
    return state()


def leave(player_id: str) -> dict[str, Any]:
    """This guest signed out."""
    player_records.remove_player(player_id)
    return state()


def clear() -> dict[str, Any]:
    """Every guest signed out, as 2.x's Clear All did."""
    player_records.sign_guests_out()
    return state()
