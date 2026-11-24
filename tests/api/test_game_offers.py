"""What every enabled source has for a game, in the one call that asks them together."""

from __future__ import annotations

import threading
import time
import unittest
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.config.config_store import ConfigStore
from common.games import library_policy
from common.online import game_offers, sources
from common.online.sources import Offer, Source
from common.service_errors import NotFoundError
from tests.support.library import TempTree
from tests.support.sources import only

GAMES: dict[str, dict[str, Any]] = {
    "g1": {"private": False, "vps_id": "vps-1", "name": "Taxi"},
    "g2": {"private": False, "vps_id": "", "name": "Unmatched"},
}
ASKED: list[tuple[str, str, str]] = []


def _catalog(source_id: str, kinds: frozenset[str] = frozenset({"wheel", "topper"}),
             fails: bool = False) -> Source:
    def offers(who: dict, kind: str) -> list[Offer]:
        ASKED.append((source_id, kind, who["vps_id"]))
        if fails:
            raise RuntimeError("that catalog is down")
        return [Offer(source=source_id, name=f"{kind}.png",
                      url=f"https://example.invalid/{who['vps_id']}/{kind}.png", kind=kind,
                      size="4k")]

    return Source(id=source_id, name=source_id.title(), kinds=kinds, offers=offers)


def _raising(source_id: str, exc: Exception) -> Source:
    def offers(who: dict, kind: str) -> list[Offer]:
        raise exc

    return Source(id=source_id, name=source_id.title(), kinds=frozenset({"wheel"}), offers=offers)


def _resource(game_id: str) -> dict[str, Any]:
    if game_id not in GAMES:
        raise NotFoundError(f"no game {game_id}")
    return dict(GAMES[game_id])


class Asking(TempTree):
    def setUp(self) -> None:
        super().setUp()
        ASKED.clear()
        self.policy = library_policy.reset_for_tests(ConfigStore(str(self.root / "vpinfe.ini")))
        self.addCleanup(library_policy.reset_for_tests)
        self.enterContext(mock.patch("common.games.game_lens.resource_of", _resource))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def get(self, game_id: str = "g1", **params: Any) -> Any:
        return self.client.get(f"/games/{game_id}/offers", params=params)

    def by_source(self, answer: dict[str, Any]) -> dict[str, dict[str, Any]]:
        return {one["id"]: {kind["kind"]: kind for kind in one["kinds"]}
                for one in answer["sources"]}

    def test_every_source_answers_for_every_kind_it_serves_in_one_response(self) -> None:
        with only(_catalog("one"), _catalog("two", frozenset({"wheel"}))):
            response = self.get()

        self.assertEqual(200, response.status_code, response.text)
        answer = response.json()
        self.assertEqual(("g1", "vps-1"), (answer["game_id"], answer["vps_id"]))
        self.assertEqual({"one": {"wheel", "topper"}, "two": {"wheel"}},
                         {key: set(kinds) for key, kinds in self.by_source(answer).items()})
        self.assertEqual({"source": "one", "name": "wheel.png", "kind": "wheel", "size": "4k",
                          "md5": "", "url": "https://example.invalid/vps-1/wheel.png"},
                         self.by_source(answer)["one"]["wheel"]["offers"][0])
        self.assertEqual(["one", "two"], [one["id"] for one in answer["sources"]])

    def test_the_kinds_asked_narrow_it_and_a_source_serving_none_is_left_out(self) -> None:
        with only(_catalog("one"), _catalog("two", frozenset({"wheel"}))):
            answer = self.get(kind=["topper"]).json()

        self.assertEqual({"one": {"topper"}},
                         {key: set(kinds) for key, kinds in self.by_source(answer).items()})
        self.assertEqual([("one", "topper", "vps-1")], ASKED)

    def test_a_kind_asked_twice_is_asked_of_a_source_once(self) -> None:
        with only(_catalog("one")):
            self.get(kind=["wheel", "wheel"])

        self.assertEqual([("one", "wheel", "vps-1")], ASKED)

    def test_a_kind_is_asked_in_the_name_the_source_knows_it_by(self) -> None:
        with only(_catalog("one", frozenset({"altcolor_serum"}))):
            answer = self.get(kind=["alt_color"]).json()

        self.assertEqual({"one": {"altcolor_serum"}},
                         {key: set(kinds) for key, kinds in self.by_source(answer).items()})

    def test_a_source_that_is_down_says_why_and_the_rest_arrive(self) -> None:
        with only(_catalog("broken", fails=True), _catalog("works")), \
                self.assertLogs("vpinfe.common.online.game_offers", "WARNING"):
            answer = self.get(kind=["wheel"]).json()

        found = self.by_source(answer)
        self.assertEqual("that catalog is down", found["broken"]["wheel"]["error"])
        self.assertEqual([], found["broken"]["wheel"]["offers"])
        self.assertEqual("", found["works"]["wheel"]["error"])
        self.assertEqual(1, len(found["works"]["wheel"]["offers"]))

    def test_a_source_switched_off_is_not_asked(self) -> None:
        self.policy.set("hidden_sources", ["one"])

        with only(_catalog("one"), _catalog("two")):
            answer = self.get(kind=["wheel"]).json()

        self.assertEqual(["two"], [one["id"] for one in answer["sources"]])
        self.assertEqual([("two", "wheel", "vps-1")], ASKED)

    def test_another_entry_is_asked_about_when_one_is_named(self) -> None:
        with only(_catalog("one")):
            answer = self.get(kind=["wheel"], vps_id="vps-9", name="Other").json()

        self.assertEqual("vps-9", answer["vps_id"])
        self.assertEqual([("one", "wheel", "vps-9")], ASKED)

    def test_a_game_matched_to_no_entry_asks_no_catalog(self) -> None:
        with only(_catalog("one")):
            answer = self.get("g2", kind=["wheel"]).json()

        self.assertEqual({"game_id": "g2", "vps_id": "", "sources": []}, answer)
        self.assertEqual([], ASKED)

    def test_a_game_the_library_does_not_hold_is_refused(self) -> None:
        with only(_catalog("one")):
            response = self.get("nope", kind=["wheel"])

        self.assertEqual(404, response.status_code)
        self.assertEqual([], ASKED)

    def test_a_kind_no_source_serves_is_answered_with_no_sources(self) -> None:
        with only(_catalog("one")):
            response = self.get(kind=["no_such_kind"])

        self.assertEqual(200, response.status_code)
        self.assertEqual([], response.json()["sources"])
        self.assertEqual([], ASKED)

    def test_a_source_that_raises_a_lookup_error_says_why_and_is_not_dropped(self) -> None:
        for exc in (KeyError("row"), IndexError("none"), LookupError("gone")):
            with self.subTest(exc=type(exc).__name__), \
                    only(_raising("odd", exc), _catalog("works")), \
                    self.assertLogs("vpinfe.common.online.game_offers", "WARNING"):
                found = self.by_source(self.get(kind=["wheel"]).json())

            self.assertTrue(found["odd"]["wheel"]["error"])
            self.assertEqual(1, len(found["works"]["wheel"]["offers"]))

    def test_a_source_that_does_not_answer_in_time_says_so_and_the_rest_arrive(self) -> None:
        release = threading.Event()
        self.addCleanup(release.set)

        def hangs(who: dict, kind: str) -> list[Offer]:
            release.wait(5)
            return []

        slow = Source(id="slow", name="Slow", kinds=frozenset({"wheel"}), offers=hangs)
        began = time.monotonic()
        with only(slow, _catalog("works")), \
                mock.patch.object(game_offers, "ASKED_WITHIN", 0.2), \
                self.assertLogs("vpinfe.common.online.game_offers", "WARNING"):
            response = self.get(kind=["wheel"])
        took = time.monotonic() - began

        self.assertEqual(200, response.status_code, response.text)
        found = self.by_source(response.json())
        self.assertTrue(found["slow"]["wheel"]["error"])
        self.assertEqual([], found["slow"]["wheel"]["offers"])
        self.assertEqual(1, len(found["works"]["wheel"]["offers"]))
        self.assertLess(took, 2)


ENTRIES = {"vps-1": "Taxi", "vps-9": "Other"}


def _entry(vps_id: str) -> dict[str, Any]:
    if vps_id not in ENTRIES:
        raise NotFoundError(f"no entry {vps_id}")
    return {"vps_id": vps_id, "name": ENTRIES[vps_id]}


class AskingAboutAnEntry(TempTree):
    """`GET /vps/entry/{id}/offers`: the same sources, asked about an entry by its id alone."""

    def setUp(self) -> None:
        super().setUp()
        ASKED.clear()
        self.policy = library_policy.reset_for_tests(ConfigStore(str(self.root / "vpinfe.ini")))
        self.addCleanup(library_policy.reset_for_tests)
        self.resource = self.enterContext(mock.patch("common.games.game_lens.resource_of"))
        self.enterContext(mock.patch("common.online.vps_lens.entry", _entry))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def get(self, vps_id: str = "vps-9", **params: Any) -> Any:
        return self.client.get(f"/vps/entry/{vps_id}/offers", params=params)

    def test_every_source_answers_for_the_entry_and_no_game_is_read(self) -> None:
        with only(_catalog("one"), _catalog("two", frozenset({"wheel"}))):
            response = self.get()

        self.assertEqual(200, response.status_code, response.text)
        answer = response.json()
        self.assertEqual("vps-9", answer["vps_id"])
        self.assertNotIn("game_id", answer)
        self.assertEqual({"one": {"wheel", "topper"}, "two": {"wheel"}},
                         {one["id"]: {kind["kind"] for kind in one["kinds"]}
                          for one in answer["sources"]})
        self.assertEqual({("one", "wheel", "vps-9"), ("one", "topper", "vps-9"),
                          ("two", "wheel", "vps-9")}, set(ASKED))
        self.resource.assert_not_called()

    def test_the_answer_for_a_kind_has_the_shape_a_game_has(self) -> None:
        with only(_catalog("one")):
            [source] = self.get(kind=["wheel"]).json()["sources"]

        self.assertEqual({"source": "one", "name": "wheel.png", "kind": "wheel", "size": "4k",
                          "md5": "", "url": "https://example.invalid/vps-9/wheel.png"},
                         source["kinds"][0]["offers"][0])

    def test_the_kinds_asked_narrow_it(self) -> None:
        with only(_catalog("one"), _catalog("two", frozenset({"wheel"}))):
            answer = self.get(kind=["topper"]).json()

        self.assertEqual(["one"], [one["id"] for one in answer["sources"]])
        self.assertEqual([("one", "topper", "vps-9")], ASKED)

    def test_a_source_switched_off_is_not_asked(self) -> None:
        self.policy.set("hidden_sources", ["one"])

        with only(_catalog("one"), _catalog("two")):
            answer = self.get(kind=["wheel"]).json()

        self.assertEqual(["two"], [one["id"] for one in answer["sources"]])

    def test_a_source_that_is_down_says_why_and_the_rest_arrive(self) -> None:
        with only(_catalog("broken", fails=True), _catalog("works")), \
                self.assertLogs("vpinfe.common.online.game_offers", "WARNING"):
            found = {one["id"]: one["kinds"][0] for one in
                     self.get(kind=["wheel"]).json()["sources"]}

        self.assertEqual("that catalog is down", found["broken"]["error"])
        self.assertEqual(1, len(found["works"]["offers"]))

    def test_an_entry_the_catalog_does_not_list_is_refused(self) -> None:
        with only(_catalog("one")):
            response = self.get("nope", kind=["wheel"])

        self.assertEqual(404, response.status_code)
        self.assertEqual([], ASKED)

    def test_an_extensions_source_is_asked_by_id_and_name_and_left_out_when_not_permitted(
            self) -> None:
        told: list[dict[str, Any]] = []

        def answer(source: Source, kind: str, who: Any) -> game_offers.KindOffers:
            told.append(dict(who))
            return game_offers.KindOffers(kind, note="from an extension")

        held = Source(id="ext", name="Ext", kinds=frozenset({"wheel"}), extension="wovp",
                      key="ext", offers=lambda who, kind: [])
        with only(held), mock.patch.object(game_offers, "_extension_answer", answer):
            kept = game_offers.wire_for_entry("vps-9", ["wheel"], name="Other")
            left = game_offers.wire_for_entry("vps-9", ["wheel"], name="Other",
                                              permits=lambda source: False)

        self.assertEqual([{"game_id": "", "vps_id": "vps-9", "name": "Other"}], told)
        self.assertEqual(["ext"], [one["id"] for one in kept["sources"]])
        self.assertEqual([], left["sources"])


class Concurrency(unittest.TestCase):
    def test_sources_are_asked_at_once_not_one_after_another(self) -> None:
        """A source waits on somebody else's server; a game with several costs the slowest."""
        import threading

        meeting = threading.Barrier(3, timeout=5)

        def waits(who: dict, kind: str) -> list[Offer]:
            meeting.wait()
            return []

        held = [Source(id=f"s{n}", name=f"S{n}", kinds=frozenset({"wheel"}), offers=waits)
                for n in range(3)]
        with only(*held), mock.patch.object(sources, "enabled", lambda _source: True):
            found = game_offers.ask({"game_id": "g1", "vps_id": "vps-1", "name": "Taxi"},
                                    ["wheel"])

        self.assertEqual(["s0", "s1", "s2"], [one.source.id for one in found])


if __name__ == "__main__":
    unittest.main()
