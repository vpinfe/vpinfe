"""A stand-in for the call that asks every source about a game, for Manager tests.

Manager tests hold a mock library whose per-source and per-kind answers are set one at a
time. `answer_from` makes `game_offers` combine them as `GET /games/{id}/offers` does, so a
test says what a source holds and reads what the pane drew.
"""

from __future__ import annotations

from typing import Any
from unittest import mock

from common.online import sources as source_registry


def answer_from(library: Any, listed: Any, extension_says: Any, catalog_has: Any = None) -> None:
    """Make `library.game_offers` answer from `listed()` (the sources, as `online_sources`
    or `media_sources` give them), `extension_says(source, kind, game_id, vps_id, name)` and
    `catalog_has(vps_id, kind)`.

    No kinds asks every kind each source serves, as the route does. A source switched off,
    or serving none of the kinds asked, is left out. An extension source that raises has its
    reason in its answer, as the route gives it.
    """

    def combined(game_id: str, vps_id: str, name: str, kinds: list[str]) -> dict[str, Any]:
        out: list[dict[str, Any]] = []
        for source in listed():
            if not source.get("enabled", True):
                continue
            held = set(source.get("kinds") or ())
            names = (list(dict.fromkeys(
                sorted(source_registry.names_for(slot) & held)[0] for slot in kinds
                if source_registry.names_for(slot) & held)) if kinds else sorted(held))
            said = []
            for kind in names:
                if source.get("extension"):
                    try:
                        one = dict(extension_says(source, kind, game_id, vps_id, name))
                    except Exception as exc:  # noqa: BLE001 - what the route does too
                        one = {"error": str(exc)}
                    said.append({"kind": kind, "offers": [], "rows": one.get("rows", []),
                                 "note": one.get("note", ""), "link": one.get("link"),
                                 "error": one.get("error", "")})
                else:
                    said.append({"kind": kind, "offers": list(catalog_has(vps_id, kind)),
                                 "rows": [], "note": "", "link": None, "error": ""})
            if said:
                out.append({"id": source["id"], "name": source.get("name", source["id"]),
                            "extension": source.get("extension", ""),
                            "key": source.get("key", ""), "kinds": said})
        return {"game_id": game_id, "vps_id": vps_id, "sources": out}

    library.game_offers = mock.Mock(side_effect=combined)
