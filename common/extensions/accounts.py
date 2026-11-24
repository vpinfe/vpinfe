"""The accounts extensions hold for players: where their values live, and what never
leaves.

A kept player's values are in the extension's own file, keyed by player id. A guest's
are held here in memory and never written. Whether an account shares is the roster's.
"""

from __future__ import annotations

import json
import threading
from typing import Any

from common import service_errors
from common.i18n import t
from common.players import players

from .store import ExtensionStore

SECRET = "secret"

_lock = threading.Lock()
# extension -> guest id -> values
_guests: dict[str, dict[str, dict[str, str]]] = {}


def values(extension: str, player_id: str,
           store: ExtensionStore | None = None) -> dict[str, str]:
    """One player's account with `extension`. Empty for a player nobody has."""
    player = players.get_roster().get(player_id)
    if player is None:
        return {}
    if player.guest:
        with _lock:
            return dict(_guests.get(extension, {}).get(player.player_id, {}))
    return _in(store).accounts(extension).get(player.player_id, {})


def keep(extension: str, player_id: str, held: dict[str, Any],
         store: ExtensionStore | None = None) -> None:
    """Replace one player's account with `extension`. Empty values remove it."""
    player = players.get_roster().get(player_id)
    if player is None:
        raise service_errors.NotFoundError(t("error.players.no_player", player_id=player_id))
    wanted = {str(key): str(value) for key, value in held.items() if value is not None}
    if not player.guest:
        _in(store).set_account(extension, player.player_id, wanted)
        return
    with _lock:
        mine = _guests.setdefault(extension, {})
        if wanted:
            mine[player.player_id] = wanted
        else:
            mine.pop(player.player_id, None)


def holders(extension: str, store: ExtensionStore | None = None) -> list[str]:
    """Every player holding an account with `extension`, in roster order."""
    kept = _in(store).accounts(extension)
    with _lock:
        guests = dict(_guests.get(extension, {}))
    return [player.player_id for player in players.get_roster().players()
            if (guests if player.guest else kept).get(player.player_id)]


def guest_holding(extension: str, held: dict[str, str]) -> players.Player | None:
    """The guest already holding this account, as a card joining twice finds: every value
    the card gave is theirs. An extension may keep more beside them."""
    with _lock:
        mine = dict(_guests.get(extension, {}))

    def holds(player: players.Player) -> bool:
        theirs = mine.get(player.player_id) or {}
        return all(theirs.get(key) == value for key, value in held.items())

    return next((player for player in players.get_roster().players()
                 if player.guest and held and holds(player)), None)


def join(extension: str, read: dict[str, Any],
         store: ExtensionStore | None = None) -> players.Player:
    """A guest joining with `extension`'s card, as the extension read it: `name`,
    `initials` and the account's `values`. The guest already holding that account is put
    up alone instead of joining twice."""
    held = {str(key): str(value) for key, value in dict(read.get("values") or {}).items()
            if value is not None}
    roster = players.get_roster()
    again = guest_holding(extension, held)
    if again is not None:
        roster.set_who_is_up([again.player_id])
        return again
    guest = roster.add_guest_with_card(extension, str(read.get("initials") or ""),
                                       str(read.get("name") or ""))
    keep(extension, guest.player_id, held, store)
    return guest


def forget(player_id: str, store: ExtensionStore | None = None) -> None:
    """A player removed or signed out: their account in every extension goes."""
    with _lock:
        for mine in _guests.values():
            mine.pop(player_id, None)
    _in(store).forget_holder(player_id)


def reset_for_tests() -> None:
    with _lock:
        _guests.clear()


def _in(store: ExtensionStore | None) -> ExtensionStore:
    """The store given, else the one the running extensions were loaded with."""
    from common import extensions

    return store or extensions.registry().store


# -- which extension ----------------------------------------------------------

def declared() -> list[dict[str, Any]]:
    """Every running extension's account, as the host records it, by extension name."""
    from common import extensions

    return [{"extension": record.name, **record.account_declared()}
            for record in extensions.records() if record.running and record.account]


def declared_by(extension: str) -> dict[str, Any]:
    """One extension's account. Refuses one that is not running or holds none."""
    found = next((one for one in declared() if one["extension"] == extension), None)
    if found is None:
        raise service_errors.NotFoundError(
            t("error.players.no_accounts_in", extension=extension))
    return found


def claiming(card: dict[str, Any]) -> dict[str, Any]:
    """The account whose extension reads this card. Refuses a card nothing here reads."""
    kind = str(card.get("type") or "").strip()
    found = next((one for one in declared() if kind in one["cards"]), None)
    if found is None:
        raise service_errors.RefusedError(t("error.players.card_unclaimed"))
    return found


def reading(extension: str, card: dict[str, Any]) -> dict[str, Any]:
    """`extension`'s account, where it reads this kind of card. Refuses one it does not."""
    found = declared_by(extension)
    if str(card.get("type") or "").strip() not in found["cards"]:
        raise service_errors.RefusedError(
            t("error.players.card_not_for", extension=found["label"]))
    return found


# -- what never leaves --------------------------------------------------------

def scrubbed(answer: Any) -> Any:
    """`answer` with the value taken out of every secret field, and `set` in its place."""
    if isinstance(answer, list):
        return [scrubbed(one) for one in answer]
    if not isinstance(answer, dict):
        return answer
    if answer.get("type") == SECRET:
        kept = {key: value for key, value in answer.items() if key != "value"}
        said = (str(answer.get("value") or "").strip() if "value" in answer
                else answer.get("set"))
        return {**kept, "set": bool(said)}
    return {key: scrubbed(value) for key, value in answer.items()}


def scrubbed_json(body: bytes) -> bytes:
    """A JSON body with every secret field's value taken out. Anything else unchanged."""
    if f'"{SECRET}"'.encode() not in body:
        return body
    try:
        answer = json.loads(body)
    except ValueError:
        return body
    return json.dumps(scrubbed(answer), ensure_ascii=False, allow_nan=False,
                      separators=(",", ":")).encode("utf-8")
