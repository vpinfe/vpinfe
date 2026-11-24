"""Reading a game's combined offers: which part is for the pane's built-in sources, which
for the files an extension's source hands over, and what a failure costs."""

from __future__ import annotations

import unittest
from typing import Any
from unittest import mock

from manager.kit import offload
from manager.library import discovery_ext, discovery_get
from manager.library.discovery_get import Catalogs, Offered, Pick

ROW = {"id": "v1", "name": "Attack from Mars"}
WHEEL = {"source": "db", "name": "w.png", "url": "u", "kind": "wheel", "size": ""}
TOPPER = {"source": "db", "name": "t.png", "url": "u", "kind": "topper", "size": ""}


def _said(kind: str, **over: Any) -> dict[str, Any]:
    return {"kind": kind, "offers": [], "rows": [], "note": "", "link": None, "error": "",
            **over}


def _source(source_id: str, *kinds: dict[str, Any], extension: str = "") -> dict[str, Any]:
    return {"id": source_id, "name": source_id, "extension": extension,
            "key": source_id.split(".")[-1], "kinds": list(kinds)}


def _listed(key: str = "files", kinds: tuple[str, ...] = ("table", "wheel")) -> dict[str, Any]:
    return {"id": f"ext.{key}", "extension": "ext", "key": key, "name": key,
            "kinds": list(kinds)}


def _kept() -> Offered:
    return Offered("ext", "files", "ext.files", "files", "table", {"vps_file_id": "f1"},
                   Pick("ext.files:table:a", "table", "ext.files", row="a"))


async def _inline(callback: Any, *args: Any) -> Any:
    return callback(*args)


class CatalogsAsked(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.object(offload, "io", _inline))
        self.library = mock.Mock()
        self.library.kept_kinds.return_value = {"media": {"wheel", "topper"}}

    async def test_the_kinds_asked_are_those_a_source_holds_and_the_library_keeps(self) -> None:
        self.library.media_sources.return_value = [
            {"id": "db", "enabled": True, "kinds": ["wheel", "pup_pack"]},
            {"id": "off", "enabled": False, "kinds": ["topper"]},
            {"id": "ext.files", "enabled": True, "extension": "ext", "kinds": ["topper"]}]

        plan = await discovery_get.catalogs_asked(self.library)

        self.assertEqual((["wheel"], ["db"]), (plan.kinds, list(plan.asked)))

    async def test_sources_that_cannot_be_read_are_the_reason(self) -> None:
        self.library.media_sources.side_effect = RuntimeError("no list")

        with self.assertLogs("vpinfe.manager.library.discovery_get", "WARNING"):
            plan = await discovery_get.catalogs_asked(self.library)

        self.assertEqual("no list", str(plan.failed))
        self.assertEqual(([], {}), (plan.kinds, plan.asked))


class Reading(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.object(offload, "io", _inline))
        self.library = mock.Mock()
        self.plan = Catalogs({"db": {"id": "db"}}, ["wheel", "topper"])

    async def test_the_offers_come_from_the_combined_answer_by_kind_in_drawing_order(
            self) -> None:
        combined = {"sources": [_source("db", _said("topper", offers=[TOPPER]),
                                        _said("wheel", offers=[WHEEL]))]}

        found, asked, failed = await discovery_get.offers_for(
            self.library, "v1", self.plan, combined)

        self.assertEqual((["wheel", "topper"], ["db"], None),
                         (list(found), list(asked), failed))
        self.library.media_offers.assert_not_called()

    async def test_a_kind_nobody_asked_about_is_left_out(self) -> None:
        combined = {"sources": [_source("db", _said("pup_pack", offers=[WHEEL]))]}

        found, _asked, _failed = await discovery_get.offers_for(
            self.library, "v1", self.plan, combined)

        self.assertEqual({}, found)

    async def test_the_sources_an_extension_gives_are_not_the_built_in_offers(self) -> None:
        combined = {"sources": [_source("ext.files", _said("wheel", offers=[WHEEL]),
                                        extension="ext")]}

        found, _asked, _failed = await discovery_get.offers_for(
            self.library, "v1", self.plan, combined)

        self.assertEqual({}, found)

    async def test_a_kind_that_failed_is_the_reason_and_the_others_arrive(self) -> None:
        combined = {"sources": [_source("db", _said("wheel", error="it is down"),
                                        _said("topper", offers=[TOPPER]))]}

        found, _asked, failed = await discovery_get.offers_for(
            self.library, "v1", self.plan, combined)

        self.assertEqual(["topper"], list(found))
        self.assertEqual("it is down", str(failed))

    async def test_a_call_that_failed_is_the_reason_and_nothing_is_offered(self) -> None:
        down = RuntimeError("no answer")

        found, _asked, failed = await discovery_get.offers_for(
            self.library, "v1", self.plan, down)

        self.assertEqual(({}, down), (found, failed))

    async def test_sources_that_could_not_be_read_are_not_asked_about_kinds(self) -> None:
        plan = Catalogs(failed=RuntimeError("no list"))

        found, asked, failed = await discovery_get.offers_for(
            self.library, "v1", plan, {"sources": []})

        self.assertEqual(({}, {}), (found, asked))
        self.assertEqual("no list", str(failed))

    async def test_with_no_combined_answer_each_kind_is_asked_once(self) -> None:
        self.library.media_offers.side_effect = lambda _vps_id, kind: (
            [WHEEL] if kind == "wheel" else [])

        found, _asked, failed = await discovery_get.offers_for(self.library, "v1", self.plan)

        self.assertEqual(({"wheel": [WHEEL]}, None), (found, failed))
        self.assertEqual(2, self.library.media_offers.call_count)


class Files(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.enterContext(mock.patch.object(offload, "io", _inline))
        self.library = mock.Mock()
        self.library.online_sources.return_value = [_listed()]

    @staticmethod
    def _combined(*kinds: dict[str, Any]) -> dict[str, Any]:
        return {"sources": [_source("ext.files", *kinds, extension="ext")]}

    async def test_the_rows_come_from_the_combined_answer_without_asking_again(self) -> None:
        combined = self._combined(_said("table", rows=[
            {"key": "a", "name": "a", "vps_file_id": "f1", "current": False}]))

        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"],
                                        combined=combined)

        self.assertEqual(["ext.files:table:a"], [one.pick.key for one in found])
        self.library.game_offers.assert_not_called()
        self.library.source_offers.assert_not_called()

    async def test_without_a_combined_answer_the_game_is_asked_once_for_the_slots(self) -> None:
        self.library.game_offers.return_value = self._combined(_said("table"))

        await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"])

        self.library.game_offers.assert_called_once_with("g1", "v1", "Attack from Mars",
                                                         ["table"])
        self.library.source_offers.assert_not_called()

    async def test_a_source_the_call_did_not_ask_has_no_rows(self) -> None:
        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"],
                                        combined={"sources": []})

        self.assertEqual([], found)

    async def test_a_source_that_failed_keeps_what_it_offered(self) -> None:
        kept = _kept()
        combined = self._combined(_said("table", error="it is down"))

        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"],
                                        keep={kept.pick.key: kept}, combined=combined)

        self.assertEqual([kept], found)

    async def test_a_call_that_failed_keeps_what_every_source_offered(self) -> None:
        kept = _kept()

        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"],
                                        keep={kept.pick.key: kept},
                                        combined=RuntimeError("no answer"))

        self.assertEqual([kept], found)


    async def test_a_source_the_answer_does_not_hold_keeps_what_it_offered(self) -> None:
        kept = _kept()

        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"],
                                        keep={kept.pick.key: kept},
                                        combined={"sources": []})

        self.assertEqual([kept], found)

    async def test_nothing_to_ask_makes_no_call(self) -> None:
        self.library.online_sources.return_value = []

        found = await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"])

        self.assertEqual([], found)
        self.library.game_offers.assert_not_called()

    async def test_a_listing_no_source_serves_makes_no_call(self) -> None:
        self.library.online_sources.return_value = [_listed("one", ("wheel",))]

        await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"])

        self.library.game_offers.assert_not_called()


class NoKinds(unittest.IsolatedAsyncioTestCase):
    async def test_no_kinds_asks_nothing_rather_than_every_kind(self) -> None:
        library = mock.Mock()
        with mock.patch.object(offload, "io", _inline):
            found = await discovery_get.game_offers(library, "g1", "v1", "Name", [])

        self.assertEqual({"game_id": "g1", "vps_id": "v1", "sources": []}, found)
        library.game_offers.assert_not_called()

    def test_the_client_sends_nothing_for_no_kinds(self) -> None:
        from manager.kit.api import ApiClient

        client = ApiClient("http://127.0.0.1:1")
        with mock.patch.object(client, "_get") as get:
            found = client.game_offers("g1", "v1", "Name", [])

        self.assertEqual([], found["sources"])
        get.assert_not_called()


class Slots(unittest.TestCase):
    def test_each_slot_a_source_serves_is_named_once(self) -> None:
        sources = [_listed("one", ("table", "wheel")), _listed("two", ("table",))]

        self.assertEqual(["table"], discovery_ext.slots_for(sources, ["tableFiles"]))

    def test_a_listing_no_source_serves_names_no_slot(self) -> None:
        self.assertEqual([], discovery_ext.slots_for([_listed("one", ("wheel",))],
                                                     ["tableFiles"]))


if __name__ == "__main__":
    unittest.main()
