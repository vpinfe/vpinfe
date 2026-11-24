"""Everything the enabled sources would give for one game, asked together.

Which sources exist, and which are switched on, is the registry's; this only asks. A kind
is asked for by the name of its slot, and each source is asked in the name it knows that
slot by. A source that serves none of the kinds asked is not asked and is not in the
answer. One that fails costs only its own kind, with the reason beside it.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor, wait
from contextvars import copy_context
from dataclasses import dataclass, field
from typing import Any

from common.failures import why
from common.i18n import t
from common.online import sources
from common.online.sources import Offer, Source

logger = logging.getLogger("vpinfe.common.online.game_offers")

# Sources wait on other people's servers, so what more costs is a thread each.
ASKED_AT_ONCE = 8

# Seconds from the call that sources have to answer; one that has not is reported.
ASKED_WITHIN = 10.0


class SourceGoneError(Exception):
    """The extension a source came from is no longer running, or no longer lists it."""


@dataclass
class KindOffers:
    """What one source said for one kind. A source core fetches from fills `offers`; one that
    hands the file over itself fills `rows`, `note` and `link`. `error` is why it could not
    answer, and then the rest are empty."""

    kind: str
    offers: list[Offer] = field(default_factory=list)
    rows: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""
    link: dict[str, Any] | None = None
    error: str = ""


@dataclass
class SourceOffers:
    source: Source
    kinds: list[KindOffers] = field(default_factory=list)


def kind_in(source: Source, slot: str) -> str:
    """The name `source` knows the slot `slot` by; empty where it serves none."""
    found = sorted(sources.names_for(slot) & source.kinds)
    return found[0] if found else ""


def _extension_answer(source: Source, kind: str, who: Mapping[str, Any]) -> KindOffers:
    """What an extension's source says, checked the way its own route checks it."""
    from common import extensions
    from common.extensions import serving

    record = extensions.registry().get(source.extension)
    if record is None or not record.running:
        raise SourceGoneError(source.extension)
    answer = serving.source_offers(record, source.key, kind, who)
    return KindOffers(kind, rows=list(answer["rows"]), note=answer["note"], link=answer["link"])


def _ask_one(source: Source, kind: str, who: Mapping[str, Any]) -> KindOffers | None:
    """One source's answer for one kind, or None where it has gone since it was listed.
    Any other failure is that source's answer: its reason, and the rest are unaffected."""
    try:
        if source.extension:
            return _extension_answer(source, kind, who)
        assert source.offers is not None
        return KindOffers(kind, offers=list(source.offers({"vps_id": who.get("vps_id", "")},
                                                          kind)))
    except SourceGoneError:
        return None
    except Exception as exc:  # noqa: BLE001 - a source that is down costs its own answer
        logger.warning("Source %s could not be asked for %s", source.id, kind, exc_info=True)
        return KindOffers(kind, error=why(exc))


def ask(who: Mapping[str, Any], slots: Iterable[str] = (), *, extensions: bool = True,
        permits: Callable[[Source], bool] | None = None) -> list[SourceOffers]:
    """What every enabled source has for `who` (`game_id`, `vps_id`, `name`), in registry
    order. `slots` are the kinds to ask about; none asks every kind each source serves.

    `extensions` False leaves out the sources extensions contribute, which are told who is
    asked about. `permits` leaves out those the caller may not use. A source core fetches
    from needs a VPS entry to look up, so with none it is not asked.
    """
    wanted = list(dict.fromkeys(slots))
    vps_id = str(who.get("vps_id") or "")
    asked: list[tuple[SourceOffers, str]] = []
    answers: list[SourceOffers] = []
    for source in sources.all_sources():
        if source.offers is None or not sources.enabled(source):
            continue
        if source.extension and (not extensions or (permits is not None
                                                    and not permits(source))):
            continue
        if not source.extension and not vps_id:
            continue
        names = (list(dict.fromkeys(name for slot in wanted if (name := kind_in(source, slot))))
                 if wanted else sorted(source.kinds))
        if not names:
            continue
        held = SourceOffers(source)
        answers.append(held)
        asked.extend((held, name) for name in names)

    def run(held: SourceOffers, name: str) -> KindOffers | None:
        return _ask_one(held.source, name, who)

    pool = ThreadPoolExecutor(max_workers=ASKED_AT_ONCE)
    try:
        # A copy of this context each: what an extension is told about the request is in it.
        futures = [pool.submit(copy_context().run, run, held, name) for held, name in asked]
        finished, _late = wait(futures, timeout=ASKED_WITHIN)
        for (held, name), future in zip(asked, futures, strict=True):
            if future in finished:
                one = future.result()
            else:
                logger.warning("Source %s did not answer for %s in %s seconds",
                               held.source.id, name, ASKED_WITHIN)
                one = KindOffers(name, error=t("said.why.timed_out"))
            if one is not None:
                held.kinds.append(one)
    finally:
        # Not waited for: a source still running is left to finish on its own thread.
        pool.shutdown(wait=False, cancel_futures=True)
    return [one for one in answers if one.kinds]


def for_game(game_id: str, slots: Iterable[str] = (), *, vps_id: str = "", name: str = "",
             permits: Callable[[Source], bool] | None = None) -> tuple[str, list[SourceOffers]]:
    """`ask` for a game of the library: the VPS entry asked about, and what the sources say.

    The game's own entry is asked about unless `vps_id` names another. A Private game is
    asked of no extension. Raises `game_lens`'s refusal for a game the library lacks.
    """
    from common.games import game_lens

    game = game_lens.resource_of(game_id)
    entry = vps_id or str(game["vps_id"] or "")
    who = {"game_id": game_id, "vps_id": entry,
           "name": name or (str(game["name"]) if entry == game["vps_id"] else "")}
    return entry, ask(who, slots, extensions=not game["private"], permits=permits)


def _wire(found: list[SourceOffers]) -> list[dict[str, Any]]:
    return [{
        "id": one.source.id, "name": one.source.name, "extension": one.source.extension,
        "key": one.source.key,
        "kinds": [{"kind": each.kind, "rows": each.rows, "note": each.note, "link": each.link,
                   "error": each.error,
                   "offers": [{"source": offer.source, "name": offer.name, "url": offer.url,
                               "kind": offer.kind, "size": offer.size,
                               "md5": offer.md5}
                              for offer in each.offers]}
                  for each in one.kinds]} for one in found]


def wire_for_game(game_id: str, slots: Iterable[str] = (), *, vps_id: str = "", name: str = "",
                  permits: Callable[[Source], bool] | None = None) -> dict[str, Any]:
    """`for_game` as the plain structure the API answers with."""
    entry, found = for_game(game_id, slots, vps_id=vps_id, name=name, permits=permits)
    return {"game_id": game_id, "vps_id": entry, "sources": _wire(found)}


def wire_for_entry(vps_id: str, slots: Iterable[str] = (), *, name: str = "",
                   permits: Callable[[Source], bool] | None = None) -> dict[str, Any]:
    """`ask` for a catalog entry the library may not hold, the way a game with no `game_id`
    is asked about: by its VPS id alone, so nothing of the library is read or told."""
    found = ask({"game_id": "", "vps_id": vps_id, "name": name}, slots, permits=permits)
    return {"vps_id": vps_id, "sources": _wire(found)}
