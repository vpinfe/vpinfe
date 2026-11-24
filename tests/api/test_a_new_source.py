"""A source that exists only in this file reaches everything a source reaches.

Nothing here is changed for it but its own registration: the list the API serves, the
switch Settings draws, the offers a game is asked for, the watch that refreshes it and
the arrivals it is dated by, and what it says is later. The spreadsheet is the first
source, and the only one with no switch.
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from starlette.testclient import TestClient

import httpapi
from common.config.config_store import ConfigStore
from common.games import library_policy
from common.online import (
    arrivals,
    sources,
    sources_watch,
    vpinmediadb,
    vps_releases,
    vpsdb_sync,
)
from common.online.sources import Listed, Offer, Source
from manager.settings import settings
from tests.support.library import TempTree

ID = "made_up"

HOLDS = [Listed("m1", "vps-1", "wheel", "1.0", "2026-01-01"),
         Listed("m2", "vps-2", "wheel", "1.0")]


def _made_up() -> Source:
    def offers(who: dict, kind: str) -> list[Offer]:
        return [Offer(source=ID, name="made-up.png", url=f"https://example.invalid/{who['vps_id']}",
                      kind=kind)]

    return Source(
        id=ID, name="Made Up", url="https://example.invalid/", kinds=frozenset({"wheel"}),
        versions=True, refresh=lambda config: True, listing=lambda: iter(HOLDS),
        offers=offers, upgrades=lambda who, kind: {"later": "2.0", "who": who["vps_id"]})


class _Registered(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.policy = library_policy.reset_for_tests(ConfigStore(str(self.root / "vpinfe.ini")))
        self.addCleanup(library_policy.reset_for_tests)
        sources.register(_made_up())
        self.addCleanup(sources.unregister, ID)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def listed(self) -> dict[str, Any]:
        found = self.client.get("/media-sources").json()["sources"]
        return next(one for one in found if one["id"] == ID)


class TheApi(_Registered):
    def test_it_is_listed_with_what_it_declares(self) -> None:
        self.assertEqual(
            {"id": ID, "name": "Made Up", "url": "https://example.invalid/", "enabled": True,
             "kinds": ["wheel"], "extension": "", "key": "", "always_on": False,
             "versions": True},
            self.listed())

    def test_the_spreadsheet_is_listed_first_and_has_no_switch(self) -> None:
        found = self.client.get("/media-sources").json()["sources"]

        self.assertEqual("vps", found[0]["id"])
        self.assertTrue(found[0]["always_on"])
        self.assertEqual([], found[0]["kinds"])

    def test_it_is_asked_for_a_game(self) -> None:
        found = self.client.get("/media-sources/offers",
                                params={"vps_id": "vps-9", "kind": "wheel"}).json()["offers"]

        self.assertIn({"source": ID, "name": "made-up.png", "kind": "wheel", "size": "",
                       "md5": "", "url": "https://example.invalid/vps-9"}, found)

    def test_it_is_asked_for_a_game_with_every_other_source_in_the_one_answer(self) -> None:
        game = {"private": False, "vps_id": "vps-3", "name": "Taxi"}
        with patch("common.games.game_lens.resource_of", lambda _id: dict(game)), \
                patch.object(vpinmediadb, "_MANIFEST", {}):
            answer = self.client.get("/games/g1/offers", params={"kind": "wheel"}).json()

        asked = {one["id"]: one for one in answer["sources"]}
        self.assertEqual({ID, "vpinmediadb"}, set(asked))
        self.assertEqual([{"source": ID, "name": "made-up.png", "kind": "wheel", "size": "",
                           "md5": "", "url": "https://example.invalid/vps-3"}],
                         asked[ID]["kinds"][0]["offers"])

    def test_switched_off_it_is_listed_and_not_asked(self) -> None:
        self.policy.set("hidden_sources", [ID])

        self.assertFalse(self.listed()["enabled"])
        found = self.client.get("/media-sources/offers",
                                params={"vps_id": "vps-9", "kind": "wheel"}).json()["offers"]
        self.assertNotIn(ID, [offer["source"] for offer in found])


class Settings(_Registered):
    def test_settings_draws_a_switch_for_it_and_none_for_the_spreadsheet(self) -> None:
        library = MagicMock()
        library.media_sources.return_value = self.client.get("/media-sources").json()["sources"]
        _note, _section, key, switches = settings.KIND_PAGES["media_sources"]

        named = switches(library)

        self.assertEqual("hidden_sources", key)
        self.assertEqual("Made Up", named[ID])
        self.assertNotIn("vps", named)
        self.assertIn("vpinmediadb", named)


class TheWatch(_Registered):
    def setUp(self) -> None:
        super().setUp()
        held = tempfile.TemporaryDirectory()
        self.addCleanup(held.cleanup)
        for one in (patch.object(arrivals, "PATH", Path(held.name) / "arrivals.json"),
                    patch.object(sources_watch, "compare", return_value=True),
                    patch.object(vpsdb_sync, "adopt_due", return_value=False),
                    patch.object(vpsdb_sync, "art_due", return_value=False),
                    patch.object(vpsdb_sync, "sync", return_value={"changed": False}),
                    patch.object(vps_releases, "index", return_value={"entries": {}})):
            one.start()
            self.addCleanup(one.stop)

    def test_it_is_refreshed_and_what_it_lists_is_taken(self) -> None:
        sources_watch.watch_once(MagicMock())

        self.assertTrue(arrivals.seeded(ID))

    def test_an_item_with_no_date_is_dated_when_it_arrives(self) -> None:
        sources_watch.watch_once(MagicMock())
        later = [*HOLDS, Listed("m3", "vps-3", "wheel", "1.0")]
        sources.register(replace(_made_up(), listing=lambda: iter(later)))

        sources_watch.watch_once(MagicMock())

        self.assertNotEqual("", arrivals.dated(ID, later[2]))
        self.assertEqual("2026-01-01", arrivals.dated(ID, HOLDS[0]))
        self.assertEqual("", arrivals.dated(ID, HOLDS[1]))

    def test_switched_off_it_is_left_alone(self) -> None:
        self.policy.set("hidden_sources", [ID])

        sources_watch.watch_once(MagicMock())

        self.assertFalse(arrivals.seeded(ID))


class WhatIsLater(_Registered):
    def test_it_says_what_is_later_for_a_game(self) -> None:
        self.assertEqual({ID: {"later": "2.0", "who": "vps-1"}},
                         sources.upgrades({"vps_id": "vps-1"}, "wheel"))

    def test_a_kind_it_does_not_serve_is_not_asked(self) -> None:
        self.assertEqual({}, sources.upgrades({"vps_id": "vps-1"}, "topper"))

    def test_switched_off_it_says_nothing(self) -> None:
        self.policy.set("hidden_sources", [ID])

        self.assertEqual({}, sources.upgrades({"vps_id": "vps-1"}, "wheel"))


if __name__ == "__main__":
    unittest.main()
