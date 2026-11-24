"""What each player other than the owner has done with each game.

The owner's record is the library's own, a game's `.info` `User` section, and nothing here
touches it. A kept player's is `player_records/<id>.json`, written whole and atomically
like `players.json`; a guest's has the same shape and is held in memory.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from common import events, service_errors
from common.atomic_write import write_atomic
from common.config.paths import CONFIG_DIR
from common.games import high_scores
from common.games.game_metadata import normalize_rating
from common.i18n import t
from common.players import player_pictures
from common.players.players import Player, get_roster

logger = logging.getLogger("vpinfe.common.games.player_records")

RECORDS_DIR = CONFIG_DIR / "player_records"
SCHEMA = 2
WHOLE_STAR_SCHEMA = 1
SCHEMA_KEY = "schema"
PLAYER_KEY = "player"
GAMES_KEY = "games"

PLAY_COUNT = "play_count"
PLAY_TIME = "play_time_seconds"
LAST_PLAYED = "last_played"
BEST_SCORE = "best_score"
RATING = "rating"

_SAFE_ID = re.compile(r"[A-Za-z0-9_-]+")


def _unplayed() -> dict[str, Any]:
    return {PLAY_COUNT: 0, PLAY_TIME: 0, LAST_PLAYED: None, BEST_SCORE: None, RATING: 0}


def _schema_of(payload: dict[str, Any]) -> int:
    try:
        return int(payload.get(SCHEMA_KEY) or 0)
    except (TypeError, ValueError):
        return 0


def _score_of(entry: Any) -> int | None:
    score = entry.get("score") if isinstance(entry, dict) else None
    return score if isinstance(score, int) and not isinstance(score, bool) else None


def best_of(held: dict[str, Any] | None, entries: list[dict[str, Any]]) -> dict | None:
    """The better of what is held and the highest-numbered of `entries`. An entry holding
    no number is kept only while nothing is held; an equal number leaves the held one."""
    numbered = [entry for entry in entries if _score_of(entry) is not None]
    candidate = (max(numbered, key=lambda entry: _score_of(entry) or 0) if numbered
                 else (entries[0] if entries else None))
    if candidate is None:
        return held
    if held is None:
        return candidate
    mine, theirs = _score_of(candidate), _score_of(held)
    return candidate if mine is not None and (theirs is None or mine > theirs) else held


class PlayerRecords:
    """Every non-owner's record on this install. One per process - see get_records()."""

    def __init__(self, root: Path | str | None = None) -> None:
        self.root = Path(root) if root is not None else RECORDS_DIR
        self._lock = threading.RLock()
        self._guests: dict[str, dict[str, Any]] = {}

    # -- reading -------------------------------------------------------------

    def games(self, player: Player) -> dict[str, dict[str, Any]]:
        """Their record per game id, every field present."""
        with self._lock:
            held = self._load(_theirs(player)).get(GAMES_KEY) or {}
        return {str(game_id): {**_unplayed(), **entry} for game_id, entry in held.items()
                if isinstance(entry, dict)}

    def game(self, player: Player, game_id: str) -> dict[str, Any]:
        """Their record of one game; a game they have never touched reads as unplayed."""
        return self.games(player).get(game_id) or _unplayed()

    def shown(self, player: Player, game_id: str) -> dict[str, Any]:
        """Their record of one game as `view` lists it."""
        return _as_shown(self.game(player, game_id))

    def view(self, player: Player) -> dict[str, Any]:
        """The record as `GET /players/{id}/record` answers it: most recently played
        first, then the games only rated."""
        games = [{"game_id": game_id, **_as_shown(entry)}
                 for game_id, entry in self.games(player).items()]
        games.sort(key=lambda one: one["game_id"])
        games.sort(key=lambda one: one[LAST_PLAYED] or "", reverse=True)
        return {PLAYER_KEY: player.player_id, GAMES_KEY: games}

    # -- writing -------------------------------------------------------------

    def count_start(self, player: Player, game_id: str, at: str) -> None:
        """A game started with them up."""
        def started(entry: dict[str, Any]) -> None:
            entry[PLAY_COUNT] = _whole(entry.get(PLAY_COUNT)) + 1
            entry[LAST_PLAYED] = at
        self._change(player, game_id, started)

    def add_time(self, player: Player, game_id: str, seconds: float) -> None:
        """A game they were up for ended after `seconds`."""
        def played(entry: dict[str, Any]) -> None:
            entry[PLAY_TIME] = (_whole(entry.get(PLAY_TIME))
                                + max(0, int(round(float(seconds)))))
        self._change(player, game_id, played)

    def offer_scores(self, player: Player, game_id: str, rom: str,
                     entries: list[dict[str, Any]], at: str) -> None:
        """Entries a game put on `rom`'s table in their name."""
        mine = [{**entry, "rom": rom, "scored_at": at} for entry in entries]

        def scored(entry: dict[str, Any]) -> None:
            entry[BEST_SCORE] = best_of(entry.get(BEST_SCORE), mine)
        self._change(player, game_id, scored)

    def set_rating(self, player: Player, game_id: str, rating: Any) -> int:
        """Their rating of a game, 0-10 in half stars, 0 meaning unrated. Returns what was
        stored."""
        stored = normalize_rating(rating)

        def rated(entry: dict[str, Any]) -> None:
            entry[RATING] = stored
        self._change(player, game_id, rated)
        events.emit(events.GAME_RATED, game_id=str(game_id or "").strip(),
                    player=player.as_payload(), rating=stored)
        return stored

    def forget(self, player_id: str) -> None:
        """A player removed or signed out: their record goes with them."""
        with self._lock:
            self._guests.pop(player_id, None)
            path = self._path(player_id)
            if path is not None:
                path.unlink(missing_ok=True)

    # -- internals -----------------------------------------------------------

    def _change(self, player: Player, game_id: str,
                change: Callable[[dict[str, Any]], None]) -> None:
        """Raises NotFoundError for a player no longer on the roster."""
        wanted = str(game_id or "").strip()
        if not wanted:
            raise service_errors.NotFoundError(t("error.games.unknown_game", game_id=wanted))
        with self._lock:
            get_roster().player(player.player_id)
            record = self._load(_theirs(player))
            games = record.setdefault(GAMES_KEY, {})
            entry = {**_unplayed(), **(games.get(wanted) or {})}
            change(entry)
            if entry == _unplayed():
                games.pop(wanted, None)
            else:
                games[wanted] = entry
            self._save(player, record)

    def _load(self, player: Player) -> dict[str, Any]:
        """A copy of what is held, to change and hand to `_save`."""
        if player.guest:
            return json.loads(json.dumps(self._guests.get(player.player_id) or {}))
        path = self._path(player.player_id)
        if path is None or not path.exists():
            return {}
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("The record at %s is unreadable; treating it as empty", path)
            return {}
        if not isinstance(payload, dict) or _schema_of(payload) < WHOLE_STAR_SCHEMA:
            return {}
        if _schema_of(payload) == WHOLE_STAR_SCHEMA:
            return _in_half_stars(payload)
        return payload

    def _save(self, player: Player, record: dict[str, Any]) -> None:
        # Whatever else a newer build put in the file is kept, and so is its number.
        payload = {**record, SCHEMA_KEY: max(SCHEMA, _schema_of(record)),
                   PLAYER_KEY: player.player_id}
        if player.guest:
            self._guests[player.player_id] = payload
            return
        path = self._path(player.player_id)
        if path is None:
            raise service_errors.RefusedError(
                t("error.players.record_id_unusable", player_id=player.player_id))
        path.parent.mkdir(parents=True, exist_ok=True)
        write_atomic(path, lambda handle: json.dump(payload, handle, indent=2,
                                                    ensure_ascii=False))

    def _path(self, player_id: str) -> Path | None:
        """None for an id that is not a file name."""
        wanted = str(player_id or "").strip()
        return self.root / f"{wanted}.json" if _SAFE_ID.fullmatch(wanted) else None


def _in_half_stars(payload: dict[str, Any]) -> dict[str, Any]:
    """A schema 1 record rated in whole stars, 0-5; the same record in half stars, 0-10,
    stamped so a later read does not double it again."""
    games = payload.get(GAMES_KEY)
    doubled = {game_id: {**entry, RATING: min(10, _whole(entry.get(RATING)) * 2)}
               if isinstance(entry, dict) and RATING in entry else entry
               for game_id, entry in (games.items() if isinstance(games, dict) else ())}
    return {**payload, SCHEMA_KEY: SCHEMA, GAMES_KEY: doubled}


def _theirs(player: Player) -> Player:
    if player.owner:
        raise service_errors.RefusedError(t("error.players.owner_record"))
    return player


def _whole(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _as_shown(entry: dict[str, Any]) -> dict[str, Any]:
    return {**entry, BEST_SCORE: _shown(entry[BEST_SCORE])}


def _shown(best: Any) -> dict[str, Any] | None:
    if not isinstance(best, dict):
        return None
    shown = high_scores.shown_entry(best)
    del shown["new"]
    return {"rom": str(best.get("rom") or ""), "section": str(best.get("section") or ""),
            "scored_at": best.get("scored_at") or None, **shown}


_records: PlayerRecords | None = None
_records_lock = threading.Lock()


def get_records() -> PlayerRecords:
    """This install's records. One per process, because the guests' live in it."""
    global _records
    with _records_lock:
        if _records is None:
            _records = PlayerRecords()
        return _records


def remove_player(player_id: str) -> Player:
    """Remove a kept player or sign a guest out, forgetting their accounts with every
    extension and their record. The owner cannot be removed. Raises NotFoundError for an
    id nobody has."""
    from common.extensions import accounts

    gone = get_roster().remove(player_id)
    accounts.forget(gone.player_id)
    get_records().forget(gone.player_id)
    player_pictures.remove(get_roster().pictures_dir, gone.player_id)
    return gone


def sign_guests_out() -> list[Player]:
    """Every guest signed out. Returns who left."""
    gone = []
    for guest in [player for player in get_roster().players() if player.guest]:
        try:
            gone.append(remove_player(guest.player_id))
        except service_errors.NotFoundError:
            continue
    return gone


def reset_for_tests(root: Path | None = None) -> None:
    global _records
    with _records_lock:
        _records = PlayerRecords(root) if root is not None else None
