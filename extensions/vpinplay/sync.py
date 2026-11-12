"""A game played here, in the shape VPinPlay files it, and the requests that carry it.

Built from what core hands over rather than read off disk.

Every key and every bound below is the service's, read from a value that is ours, and none
of them is renamed to match our vocabulary. A key their models require that goes missing
refuses the whole request; one they do not know is dropped in silence.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC
from typing import Any
from urllib.parse import quote

import requests

from common.extensions.contract import get_response, post_response, words

logger = logging.getLogger("vpinfe.ext.vpinplay.sync")
t = words("vpinplay")

GAME_TIMEOUT = 30

# Their bound, in whole stars, and one game outside it fails the whole request rather than
# that game. A game's own rating is in half stars, 0-10, and goes as whole stars, a half
# star rounding down, except a lone half star, which goes as one: 0 would clear the rating.
RATING_MIN, RATING_MAX = 0, 5

# What the service calls each thing a script was seen to use. Ours are in `features`
# under shorter names; theirs spell Scorbit's product "Scorebit".
FEATURES = {
    "detectNfozzy": "nfozzy",
    "detectFleep": "fleep",
    "detectSSF": "ssf",
    "detectLUT": "lut",
    "detectScorebit": "scorbit",
    "detectFastflips": "fastflips",
    "detectFlex": "flexdmd",
}


def payload_for(game: dict, table: dict | None) -> dict | None:
    """One game in the shape the service accepts, or None where it has nothing to say.

    A game no catalog has matched is skipped: the service keys on the catalog id, so a
    game without one describes nothing it can file.
    """
    vps_id = str(game.get("vps_id") or "").strip()
    if not vps_id:
        return None

    table = table or {}
    user = game.get("user") or {}
    overrides = game.get("overrides") or {}
    features = table.get("features") or {}

    return {
        "info": {"vpsId": vps_id, "rom": _text(game.get("rom"))},
        "user": {
            "rating": _rating(user.get("rating")),
            "lastRun": _epoch(user.get("last_played")),
            "startCount": _number(user.get("play_count")),
            "runTime": _number(user.get("play_time_seconds")) // 60,
            "score": _score(user.get("score")),
        },
        "vpxFile": {
            "filename": _text(table.get("filename")),
            "filehash": _text(table.get("file_hash")),
            "version": _text(table.get("version")),
            "releaseDate": _text(table.get("release_date")),
            "saveDate": _text(table.get("save_date")),
            "saveRev": _text(table.get("save_rev")),
            # The file's own words, not the game's resolved answer: this section
            # describes the file, and a catalog match would be a different claim.
            "manufacturer": _text(table.get("manufacturer")),
            "year": _text(table.get("year")),
            "type": _text(table.get("type")),
            "vbsHash": _text(table.get("vbs_hash")),
            "rom": _text(game.get("rom")),
            **{theirs: bool(features.get(ours))
               for theirs, ours in FEATURES.items()},
        },
        "vpinfe": {
            "alttitle": _text(overrides.get("alt_title")),
            "altvpsid": _text(overrides.get("alt_vps_id")),
        },
    }


def payload_for_player(game: dict, table: dict | None, mine: dict, held: dict,
                       reading: Any, initials: str, *, credited: bool = False,
                       rated: bool = False) -> dict | None:
    """One game, for a player other than the owner, in the shape `payload_for` builds.

    `mine` is their record of the game here. A send replaces their whole record for the
    table, so what this install does not hold for them comes from `held`, the service's
    record for them: the rating, unless they rated it here, the alternate title and id,
    and the score, unless `reading` carries theirs - and only their own entries in it,
    never a housemate's. `credited` says this game's new entries were theirs, which is
    the only way a reading of one number is. `rated` says the send is for a rating just
    given, where 0 is them clearing it and goes as it is.
    """
    return payload_for({
        **game,
        "user": {"rating": (mine.get("rating") if rated
                            else _rating(mine.get("rating")) * 2
                            or _halves(held.get("rating"))),
                 "last_played": mine.get("last_played"),
                 "play_count": mine.get("play_count"),
                 "play_time_seconds": mine.get("play_time_seconds"),
                 "score": theirs_of(reading, initials, credited) or held.get("score")},
        "overrides": {"alt_title": held.get("alttitle"),
                      "alt_vps_id": held.get("altvpsid")},
    }, table)


def payload_for_owner(game: dict, table: dict | None, held: dict, reading: Any,
                      initials: str, *, credited: bool = False, rated: bool = False
                      ) -> dict | None:
    """One game, for the owner, in the shape `payload_for` builds.

    `game` is the library's record of it, which is the owner's own. A send replaces the
    whole record for the table, and the service stores a missing score as nothing and a
    rating of 0 as nothing, so where this install has neither to send they come from
    `held`, the service's record - as they do for another player. `rated` says the send
    is for a rating just given, where 0 is the owner clearing it and goes as it is.
    """
    user = game.get("user") or {}
    return payload_for({
        **game,
        "user": {**user,
                 "rating": (user.get("rating") if rated
                            else _rating(user.get("rating")) * 2
                            or _halves(held.get("rating"))),
                 "score": theirs_of(reading, initials, credited) or held.get("score")},
    }, table)


def from_library(game: dict) -> dict:
    """A game as core's library lens publishes it, its high score table in the shape a
    reading off the hardware has - which is the shape the service files."""
    user = dict(game.get("user") or {})
    user["score"] = reading_of(user.pop("high_scores", None))
    return {**game, "user": user}


def reading_of(high_scores: Any) -> dict | None:
    """The machine's table in the shape the score parser gives a reading."""
    if not isinstance(high_scores, dict) or not high_scores.get("sections"):
        return None
    entries = []
    for section in high_scores["sections"]:
        for entry in section.get("entries") or []:
            score = entry.get("score")
            text = str(entry.get("text") or "")
            entries.append({
                "section": str(section.get("name") or ""),
                "rank": entry.get("rank"),
                "initials": str(entry.get("initials") or ""),
                "score": score,
                "value_prefix": str(entry.get("prefix") or "") or None,
                "value_suffix": str(entry.get("suffix") or "").lstrip() or None,
                "extra_lines": text.split("\n") if score is None and text else [],
            })
    return {"rom": str(high_scores.get("rom") or ""), "entries": entries}


def theirs_of(reading: Any, initials: str, credited: bool) -> dict | None:
    """This player's own share of a reading off the hardware, kept in the same shape,
    before it goes on the wire: entries with their initials, plus one credited to them
    with no initials on it. None where nothing of theirs is here, which a caller reads
    as "use what is already held for them instead."

    A reading with no entries to sort by initials - one number, not a table - is theirs
    whole where `credited` says so, and nobody's otherwise.
    """
    if not isinstance(reading, dict):
        return None
    entries = reading.get("entries")
    if not isinstance(entries, list):
        return reading if credited else None
    wanted = str(initials or "").strip().upper()
    mine = [one for one in entries if isinstance(one, dict)
            and _is_theirs(one, wanted, credited)]
    return {**reading, "entries": mine} if mine else None


def _is_theirs(entry: dict, wanted: str, credited: bool) -> bool:
    said = str(entry.get("initials") or "").strip().upper()
    return said == wanted if said else credited


def their_record(sync_endpoint: str, user_id: str, vps_id: str,
                 timeout_seconds: int) -> dict | None:
    """What the service holds for one user and one table.

    `{}` where it holds nothing, and None where it could not be asked - which a caller
    must not read as nothing.
    """
    root = sync_endpoint.removesuffix("/sync")
    url = f"{root}/users/{quote(user_id, safe='')}/tables/{quote(vps_id, safe='')}"
    try:
        response = get_response(url, timeout=timeout_seconds)
    except requests.RequestException:
        return None
    if response.status_code == 404:
        return {}
    if not response.ok:
        return None
    try:
        found = response.json()
    except ValueError:
        return None
    return found if isinstance(found, dict) else None


def envelope(user_id: str, initials: str, machine_id: str,
             games: list[dict], program_version: str, sent_at: str) -> dict:
    """What wraps the games. The program and its version are theirs to record."""
    return {
        "source": {"program": "VPinFE", "programVersion": program_version},
        "client": {"userId": user_id, "initials": initials, "machineId": machine_id},
        "sentAt": sent_at,
        "tables": games,
    }


def _epoch(value: Any) -> int | None:
    """When it was last played, in the seconds the service takes.

    Records keep an epoch and the API hands out ISO, because those are the right answers
    for a file and for a reader. This is neither: it is what the service accepts, so the
    conversion belongs here with the rest of their vocabulary.
    """
    if value in ("", None):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    from datetime import datetime

    said = str(value).strip().replace("Z", "+00:00")
    try:
        return int(datetime.fromisoformat(said).timestamp())
    except ValueError:
        return None


def _score(value: Any) -> dict | None:
    """A score is a reading off the hardware, which is a mapping of fields.

    Anything else is not one. A string here has been seen - a machine that writes its
    display rather than its values - and sending it describes nothing their models can
    file, so it is left out rather than passed along.
    """
    return value if isinstance(value, dict) else None


def now() -> str:
    """When this was sent, as the service records it."""
    from datetime import datetime

    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _text(value: Any) -> str:
    return str(value or "")


def _number(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _rating(value: Any) -> int:
    """A rating in half stars as the whole stars the service takes. Clamped rather than
    sent as it came: one game outside their bound fails the whole request, so a rating
    nobody meant is not worth losing a sync over."""
    halves = _number(value)
    return max(RATING_MIN, min(RATING_MAX, max(1, halves // 2) if halves > 0 else 0))


def _halves(stars: Any) -> int:
    """What the service holds, whole stars, in the half stars a game's rating is in."""
    return max(RATING_MIN, min(RATING_MAX, _number(stars))) * 2


def send(endpoint: str, payload: dict, timeout_seconds: int) -> dict:
    """Post games to the service and describe what came back.

    The body is kept whether it parsed or not: a failure is usually explained in text
    their models did not produce, and dropping it leaves somebody with a status code.
    Not ok when the body says `"status": "error"`, which the service answers with a 200.
    """
    response = post_response(endpoint, payload, timeout=timeout_seconds)
    body = response.text
    try:
        parsed = response.json()
        body = json.dumps(parsed, indent=2)
    except Exception:
        parsed = None
    refused = isinstance(parsed, dict) and parsed.get("status") == "error"
    return {
        "endpoint": endpoint,
        "status_code": response.status_code,
        "ok": response.ok and not refused,
        "response_body": body,
        "response_json": parsed,
        "payload": payload,
    }


def api_root(said: str) -> str:
    """Their API root, from whatever somebody put in the setting.

    A person types a host, a host and port, the API root, or the whole sync path - all
    four have been seen in a config file, and the one that is already complete must not
    have the path added twice. A scheme is assumed rather than demanded because nobody
    types one for a machine on their own network.
    """
    from urllib.parse import urlsplit

    raw = str(said or "").strip()
    if not raw:
        raise ValueError(t("error.no_address"))
    if "://" not in raw:
        raw = f"http://{raw}"
    try:
        parts = urlsplit(raw)
        parts.port  # noqa: B018 - a port that is not a number raises here
    except ValueError:
        parts = None
    if parts is None or not parts.netloc:
        raise ValueError(t("error.bad_address", address=str(said).strip()))

    base = parts.path.rstrip("/")
    for tail in (SYNC_PATH, API_ROOT):
        if base.lower().endswith(tail):
            base = base[:-len(tail)]
            break
    return f"{parts.scheme}://{parts.netloc}{base}{API_ROOT}"


def endpoint_for(said: str) -> str:
    """Their sync path."""
    return f"{api_root(said)}/sync"


API_ROOT = "/api/v1"
SYNC_PATH = f"{API_ROOT}/sync"
