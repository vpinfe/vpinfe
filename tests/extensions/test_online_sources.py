"""An extension's Online source: what the contract accepts, and what core serves of it."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common import i18n
from common.extensions import contract, host, reach, serving, store
from common.extensions.context import ContractError, ExtensionSources
from common.extensions.contract import Unavailable
from common.i18n import t
from common.online import sources, vpinmediadb
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY
from tests.support.served import client_for, games_client

MODULE = "tests.extensions.test_online_sources"
NAME = "sourced"
KINDS = ["playfield_video", "pup_pack", "table"]
WEB = "https://pictures.example/cover.png"

PUBLIC = {"private": False, "vps_id": "vps-1", "name": "Taxi"}
HIDDEN = {"private": True, "vps_id": "vps-2", "name": "Secret"}

STATE: dict[str, Any] = {}


def fresh_state() -> None:
    STATE.clear()
    STATE.update(offers={"rows": []}, take={"message": "Done"}, asked=[], taken=[], off=[],
                 many={"message": "Getting them"}, act={}, look={}, taken_many=[], acted=[],
                 looked=[], tiers=[])


def sourced(ctx: Any) -> None:
    class Dialog:
        def ask(self, step: str) -> dict:
            return {"fields": []}

    def offers(who: dict, kind: str) -> Any:
        STATE["asked"].append((dict(who), kind))
        STATE["tiers"].append(getattr(reach.current(), "tier", None))
        if isinstance(STATE["offers"], Exception):
            raise STATE["offers"]
        return STATE["offers"]

    def take(who: dict, kind: str, key: str) -> Any:
        STATE["taken"].append((dict(who), kind, key))
        if isinstance(STATE["take"], Exception):
            raise STATE["take"]
        return STATE["take"]

    def take_many(who: dict, picks: list) -> Any:
        STATE["taken_many"].append((dict(who), [dict(one) for one in picks]))
        if isinstance(STATE["many"], Exception):
            raise STATE["many"]
        return STATE["many"]

    def act(who: dict, kind: str, key: str, verb: str) -> Any:
        STATE["acted"].append((dict(who), kind, key, verb))
        if isinstance(STATE["act"], Exception):
            raise STATE["act"]
        return STATE["act"]

    def look(who: dict, kind: str, key: str) -> Any:
        STATE["looked"].append((dict(who), kind, key))
        if isinstance(STATE["look"], Exception):
            raise STATE["look"]
        return STATE["look"]

    ctx.ui.action("get", Dialog())
    ctx.sources.contribute("files", "Example Files", KINDS, offers=offers, take=take,
                           take_many=take_many, act=act, look=look)
    ctx.sources.contribute("plain", "Plain Files", KINDS, offers=offers, take=take)


def _installed(root: Path, capabilities: list[str]) -> Path:
    directory = root / NAME
    (directory / "i18n").mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": NAME, "display_name": "Sourced", "version": "1.0.0",
        "description": "Offers files from a place online, for the tests that ask.",
        "requires_platform": contract.PLATFORM_ABI, **IDENTITY,
        "capabilities": capabilities, "scopes": ["games:read"]}), encoding="utf-8")
    (directory / "i18n" / "en.json").write_text(
        json.dumps({"action.get.label": "Get a file"}), encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import sourced as register\n", encoding="utf-8")
    return directory


def _policy(test: unittest.TestCase) -> None:
    """The sources this install has switched off are whatever STATE says."""
    patch = mock.patch("common.games.library_policy.get_library_policy",
                       lambda: mock.Mock(get=lambda key: list(STATE["off"])))
    patch.start()
    test.addCleanup(patch.stop)


class Registry(unittest.TestCase):
    def setUp(self) -> None:
        fresh_state()
        sources.clear()
        self.addCleanup(sources.clear)
        _policy(self)

    def put(self, extension: str = "ext", key: str = "files",
            kinds: frozenset[str] = frozenset({"wheel"})) -> sources.Source:
        sources.contribute(extension, key, "Files", kinds, lambda who, kind: {},
                         lambda who, kind, row: {})
        held = sources.named(extension, key)
        assert held is not None
        return held

    def test_a_source_is_stored_under_its_extension_and_key(self) -> None:
        self.assertEqual("ext.files", self.put().id)

    def test_two_extensions_may_name_a_source_alike(self) -> None:
        self.put("one")
        self.put("two")

        self.assertEqual(["one.files", "two.files"], [one.id for one in sources.contributed()])

    def test_a_key_is_lower_case_words_joined_by_underscores(self) -> None:
        for key in ("Files", "a b", "a.b", "1up", ""):
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "lower-case words"):
                self.put(key=key)

    def test_a_source_that_offers_nothing_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "offers no kind"):
            self.put(kinds=frozenset())

    def test_a_kind_core_has_no_slot_for_is_refused(self) -> None:
        with self.assertRaisesRegex(ValueError, "no slot for"):
            self.put(kinds=frozenset({"wheel", "bogus"}))

    def test_a_kind_no_dialog_is_opened_for_is_refused(self) -> None:
        for kind in ("game_info", "patch", "media"):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "no slot for"):
                self.put(kinds=frozenset({kind}))

    def test_taking_an_extension_out_takes_its_sources(self) -> None:
        self.put("one")
        self.put("two")

        sources.forget("one")

        self.assertEqual(["two.files"], [one.id for one in sources.contributed()])

    def test_switching_a_source_off_in_settings_is_read_by_its_id(self) -> None:
        held = self.put()
        self.assertTrue(sources.enabled(held))

        STATE["off"] = ["EXT.FILES"]
        self.assertFalse(sources.enabled(held))

        STATE["off"] = ["vpinmediadb"]
        self.assertTrue(sources.enabled(held))


class Names(unittest.TestCase):
    def test_a_kind_is_known_by_every_name_its_slot_may_be_opened_under(self) -> None:
        self.assertEqual({"alt_color", "altcolor_serum", "altcolor_vni"},
                         sources.names_for("alt_color"))
        self.assertEqual({"wheel"}, sources.names_for("wheel"))

    def test_a_table_is_not_the_playfield_picture(self) -> None:
        self.assertEqual({"table"}, sources.names_for("table"))
        self.assertNotIn("table", sources.names_for("playfield"))

    def test_the_backglass_file_is_not_the_backglass_picture(self) -> None:
        self.assertEqual({"backglass_file"}, sources.names_for("backglass_file"))
        self.assertNotIn("backglass_file", sources.names_for("backglass"))
        self.assertEqual("backglass_file", sources.file_kind("backglass"))
        self.assertEqual("pov", sources.file_kind("pov"))
        self.assertIn("backglass_file", sources.slot_kinds())

    def test_an_old_spelling_of_a_media_kind_means_the_kind_it_became(self) -> None:
        self.assertIn("backglass", sources.names_for("bg"))


class TheContract(unittest.TestCase):
    def setUp(self) -> None:
        fresh_state()
        sources.clear()
        self.addCleanup(sources.clear)

    def ctx(self, scopes: tuple[str, ...] = ("games:read",),
            reaches: tuple[str, ...] = (reach.PLAY, reach.LIBRARY)) -> ExtensionSources:
        return ExtensionSources("ext", scopes, reaches)

    def contribute(self, ctx: ExtensionSources, key: str = "files", name: str = "Files",
                   kinds: Any = ("wheel",), offers: Any = lambda who, kind: {},
                   take: Any = lambda who, kind, row: {}) -> None:
        ctx.contribute(key, name, kinds, offers=offers, take=take)

    def test_a_source_is_registered_for_the_kinds_it_names(self) -> None:
        self.contribute(self.ctx(), kinds=["wheel", "table"])

        held = sources.named("ext", "files")
        assert held is not None
        self.assertEqual((frozenset({"wheel", "table"}), "Files"), (held.kinds, held.name))

    def test_it_needs_to_read_games(self) -> None:
        with self.assertRaisesRegex(ContractError, "games:read"):
            self.contribute(self.ctx(scopes=()))

    def test_it_needs_the_library_tier(self) -> None:
        with self.assertRaisesRegex(ContractError, "tiers"):
            self.contribute(self.ctx(reaches=(reach.PLAY,)))

    def test_a_key_and_a_name_are_both_needed(self) -> None:
        for key, name in (("", "Files"), (" ", "Files"), ("files", ""), ("files", " ")):
            with self.subTest(key=key, name=name), self.assertRaisesRegex(
                    ContractError, "no key or name"):
                self.contribute(self.ctx(), key=key, name=name)

    def test_a_key_used_twice_is_refused(self) -> None:
        ctx = self.ctx()
        self.contribute(ctx)

        with self.assertRaisesRegex(ContractError, "twice"):
            self.contribute(ctx)

    def test_kinds_are_a_list_of_names(self) -> None:
        for kinds in ("wheel", 5, None):
            with self.subTest(kinds=kinds), self.assertRaisesRegex(
                    ContractError, "not a list of names"):
                self.contribute(self.ctx(), kinds=kinds)

    def test_offers_and_take_are_functions(self) -> None:
        with self.assertRaisesRegex(ContractError, "not a function"):
            self.contribute(self.ctx(), offers="no")
        with self.assertRaisesRegex(ContractError, "not a function"):
            self.contribute(self.ctx(), take=None)

    def test_a_refusal_from_the_registry_is_the_contracts_own_error(self) -> None:
        for key, kinds in (("Files", ["wheel"]), ("files", []), ("files", ["bogus"])):
            with self.subTest(key=key, kinds=kinds), self.assertRaises(ContractError):
                self.contribute(self.ctx(), key=key, kinds=kinds)

        self.assertEqual([], sources.contributed())


class Served(unittest.TestCase):
    capabilities = ["ui:mount", "net:outbound"]

    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        fresh_state()
        sources.clear()
        self.addCleanup(sources.clear)
        _policy(self)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        self.addCleanup(self.registry.clear)
        self.addCleanup(i18n.disown, f"ext.{NAME}")
        self.record = self.registry.load(_installed(self.root / "installed", self.capabilities))
        self.assertEqual(self.record.state, host.LOADED, self.record.error)
        self.client = client_for(self, self.registry, self.record)
        self.resources = {"g1": PUBLIC, "hidden": HIDDEN}
        patch = mock.patch("httpapi.extensions_served.game_lens.resource_of",
                           lambda game_id: dict(self.resources[game_id]))
        patch.start()
        self.addCleanup(patch.stop)

    def offers(self, kind: str = "playfield_video", game_id: str = "g1", key: str = "files",
               **who: str) -> Any:
        who = who or {"vps_id": "vps-9", "name": "Other"}
        return self.client.get(f"/ext/{NAME}/sources/{key}/offers",
                               params={"kind": kind, "game_id": game_id, **who})

    def take(self, row: str = "a", kind: str = "playfield_video", game_id: str = "g1",
             key: str = "files", table_id: str = "") -> Any:
        return self.client.post(f"/ext/{NAME}/sources/{key}/take", json={
            "kind": kind, "row": row,
            "who": {"game_id": game_id, "vps_id": "vps-9", "name": "Other",
                    "table_id": table_id}})

    def refused(self, response: Any, status: int, message: str = "") -> dict:
        self.assertEqual(status, response.status_code, response.text)
        error = response.json()["error"]
        if message:
            self.assertEqual(message, error["message"])
        return error

    def display(self) -> str:
        return self.record.display_name


class Withdrawn(Served):
    def test_a_loaded_extension_has_its_source_registered(self) -> None:
        held = sources.named(NAME, "files")
        assert held is not None
        self.assertEqual(frozenset(KINDS), held.kinds)

    def test_switching_the_extension_off_takes_its_source_with_it(self) -> None:
        with self.assertLogs(level="ERROR"):
            self.registry.disable(NAME, "switched off")

        self.assertIsNone(sources.named(NAME, "files"))


class Offering(Served):
    def test_it_is_asked_for_the_entry_the_slot_looks_up_and_the_kind_it_is_open_for(
            self) -> None:
        self.assertEqual(200, self.offers().status_code)

        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other", "table_id": ""},
                           "playfield_video")], STATE["asked"])

    def test_what_it_has_comes_back_checked(self) -> None:
        STATE["offers"] = {
            "rows": [{"key": "a", "name": " A table video ", "site": "example.com",
                      "page": "https://example.com/a", "note": "30 MB", "tag": "FSS",
                      "current": True, "picture": WEB},
                     {"key": "b", "name": "Another"}],
            "note": "More on the site",
            "link": {"text": "Open the site", "to": "https://example.com/"}}

        answer = self.offers().json()

        first, second = answer["rows"]
        self.assertRegex(first.pop("picture"), rf"^/api/v1/ext/{NAME}/pictures/.+")
        self.assertEqual({"key": "a", "name": "A table video", "site": "example.com",
                          "page": "https://example.com/a", "note": "30 MB", "tag": "FSS",
                          "current": True, "vps_file_id": "", "state": None, "acts": []},
                         first)
        self.assertEqual({"key": "b", "name": "Another", "site": "", "page": "", "note": "",
                          "tag": "", "picture": "", "current": False, "vps_file_id": "",
                          "state": None, "acts": []}, second)
        self.assertEqual("More on the site", answer["note"])
        self.assertEqual({"text": "Open the site", "to": "https://example.com/"},
                         answer["link"])

    def test_an_answer_with_nothing_in_it_is_no_rows(self) -> None:
        for answer in (None, {}, {"rows": None}):
            STATE["offers"] = answer
            with self.subTest(answer=answer):
                self.assertEqual({"rows": [], "note": "", "link": None},
                                 self.offers().json())

    def test_a_link_to_the_extensions_settings_is_kept(self) -> None:
        STATE["offers"] = {"link": {"text": "Open Settings", "settings": True}}

        self.assertEqual({"text": "Open Settings", "settings": True},
                         self.offers().json()["link"])

    def test_a_private_game_is_not_asked_about(self) -> None:
        answer = self.offers(game_id="hidden")

        self.assertEqual({"rows": [], "note": "", "link": None}, answer.json())
        self.assertEqual([], STATE["asked"])

    def test_a_kind_the_source_does_not_offer_is_not_found(self) -> None:
        self.refused(self.offers(kind="wheel"), 404, t(
            "error.extensions.source_kind", display=self.display(), key="files",
            kind="wheel"))
        self.assertEqual([], STATE["asked"])

    def test_a_source_that_is_not_there_is_not_found(self) -> None:
        self.refused(self.offers(key="nope"), 404, t(
            "error.extensions.no_source", display=self.display(), key="nope"))

    def test_a_source_switched_off_in_settings_is_not_asked(self) -> None:
        STATE["off"] = [f"{NAME}.files"]

        self.refused(self.offers(), 400, t(
            "error.extensions.source_off", display=self.display(), key="files"))
        self.assertEqual([], STATE["asked"])

    def test_an_answer_of_a_shape_that_is_not_this_one_is_refused(self) -> None:
        wrong = {
            "an answer that is not an object": ["x"],
            "a key the answer does not have": {"rowz": []},
            "rows that are not a list": {"rows": "x"},
            "a row that is not an object": {"rows": ["x"]},
            "a row with no key": {"rows": [{"name": "A"}]},
            "a row with no name": {"rows": [{"key": "a"}]},
            "a field a row does not have": {"rows": [{"key": "a", "name": "A", "size": 3}]},
            "a field that is not text": {"rows": [{"key": "a", "name": "A", "tag": 5}]},
            "two rows with one key": {"rows": [{"key": "a", "name": "A"},
                                               {"key": "a", "name": "B"}]},
            "a page that is not a web address": {"rows": [
                {"key": "a", "name": "A", "page": "javascript:alert(1)"}]},
            "a note that is not text": {"note": 5},
            "a link that is not an object": {"link": "https://example.com/"},
            "a link with no text": {"link": {"to": "https://example.com/"}},
            "a link with two places to go": {"link": {
                "text": "x", "to": "https://example.com/", "settings": True}},
            "a link to something that is not a web address": {"link": {
                "text": "x", "to": "ftp://example.com/"}},
            "a link to a page nothing declared": {"link": {"text": "x", "page": "nope"}},
            "a link to a page that is not text": {"link": {"text": "x", "page": 5}},
            "a link to a page that is a list": {"link": {"text": "x", "page": ["a"]}},
            "a current that is not true or false": {"rows": [
                {"key": "a", "name": "A", "current": "yes"}]},
        }
        for what, answer in wrong.items():
            STATE["offers"] = answer
            with self.subTest(what):
                self.assertEqual(400, self.offers().status_code)

    def test_a_source_that_raises_is_said_and_costs_nothing_else(self) -> None:
        STATE["offers"] = ValueError("The host said no")
        said = self.refused(self.offers(), 400)
        self.assertIn("The host said no", said["message"])

        STATE["offers"] = Unavailable("The host did not answer")
        self.assertEqual(502, self.offers().status_code)

        STATE["offers"] = {"rows": []}
        self.assertEqual(200, self.offers().status_code)


class Gathering(Served):
    """The sources asked together for a game, off the core route."""

    def setUp(self) -> None:
        super().setUp()
        self.enterContext(mock.patch.object(vpinmediadb, "_MANIFEST", {}))

    def gathered(self, game_id: str = "g1", allowed: Any = lambda _scope: True,
                 reached: str | None = None, **params: Any) -> Any:
        params.setdefault("kind", "playfield_video")
        return games_client(allowed, reached).get(f"/games/{game_id}/offers", params=params)

    def extensions_in(self, response: Any) -> list[dict[str, Any]]:
        """The sources of extensions in the answer; the one that ships answers beside them."""
        return [one for one in response.json()["sources"] if one["extension"]]

    def test_each_extension_source_answers_with_its_rows_checked(self) -> None:
        STATE["offers"] = {"rows": [{"key": "a", "name": " A table video ", "site": "example.com"}],
                           "note": "More on the site"}

        found = self.extensions_in(self.gathered())

        self.assertEqual(["sourced.files", "sourced.plain"], [one["id"] for one in found])
        files = found[0]
        self.assertEqual((NAME, "files"), (files["extension"], files["key"]))
        said = files["kinds"][0]
        self.assertEqual(("playfield_video", "More on the site", ""),
                         (said["kind"], said["note"], said["error"]))
        self.assertEqual(("a", "A table video", "example.com", False),
                         tuple(said["rows"][0][field]
                               for field in ("key", "name", "site", "current")))

    def test_each_is_told_who_is_asked_about_and_the_kind(self) -> None:
        self.gathered()

        self.assertEqual([({"game_id": "g1", "vps_id": "vps-1", "name": "Taxi", "table_id": ""},
                           "playfield_video")] * 2, STATE["asked"])

    def test_a_source_that_fails_says_why_and_is_still_answered_for(self) -> None:
        STATE["offers"] = Unavailable("the site is down")

        response = self.gathered()

        self.assertEqual(200, response.status_code, response.text)
        said = [one["kinds"][0] for one in self.extensions_in(response)]
        self.assertEqual(2, len(said))
        for each in said:
            self.assertTrue(each["error"])
            self.assertEqual([], each["rows"])

    def test_a_private_game_is_asked_of_no_extension(self) -> None:
        self.assertEqual([], self.extensions_in(self.gathered("hidden")))
        self.assertEqual([], STATE["asked"])

    def test_a_source_of_an_extension_the_caller_may_not_use_is_left_out(self) -> None:
        found = self.extensions_in(self.gathered(
            allowed=lambda scope: scope != f"ext:{NAME}:{reach.LIBRARY}"))

        self.assertEqual([], found)
        self.assertEqual([], STATE["asked"])

    def test_a_source_switched_off_in_settings_is_not_asked(self) -> None:
        STATE["off"] = ["sourced.plain"]

        found = self.extensions_in(self.gathered())

        self.assertEqual(["sourced.files"], [one["id"] for one in found])

    def test_another_entry_is_asked_about_with_its_name(self) -> None:
        self.gathered(vps_id="vps-9", name="Other")

        self.assertEqual({"game_id": "g1", "vps_id": "vps-9", "name": "Other", "table_id": ""},
                         STATE["asked"][0][0])

    def test_the_games_name_is_not_given_to_another_entry(self) -> None:
        self.gathered(vps_id="vps-9")

        self.assertEqual("", STATE["asked"][0][0]["name"])

    def test_an_extension_answers_at_library_for_a_caller_who_reached_higher(self) -> None:
        self.gathered(reached=reach.PROTECTED)

        self.assertEqual([reach.LIBRARY, reach.LIBRARY], STATE["tiers"])

    def test_an_extension_is_never_marked_above_what_the_caller_reached(self) -> None:
        self.gathered(reached=reach.PLAY)

        self.assertEqual([reach.PLAY, reach.PLAY], STATE["tiers"])

    def test_a_source_that_raises_a_lookup_error_says_why_and_is_not_dropped(self) -> None:
        with mock.patch.object(serving, "source_offers", side_effect=KeyError("row")), \
                self.assertLogs("vpinfe.common.online.game_offers", "WARNING"):
            found = self.extensions_in(self.gathered())

        self.assertEqual(["sourced.files", "sourced.plain"], [one["id"] for one in found])
        self.assertTrue(all(one["kinds"][0]["error"] for one in found))

    def test_an_extension_that_has_stopped_is_not_asked(self) -> None:
        with self.assertLogs(level="ERROR"):
            self.registry.disable(NAME, "switched off")

        self.assertEqual([], self.extensions_in(self.gathered()))


class WithoutOutbound(Served):
    capabilities = ["ui:mount"]

    def test_a_picture_from_the_web_needs_the_capability_to_reach_it(self) -> None:
        STATE["offers"] = {"rows": [{"key": "a", "name": "A", "picture": WEB}]}

        self.refused(self.offers(), 400, t(
            "error.extensions.picture_outbound", display=self.display(),
            key="source files offers"))

    def test_a_picture_in_the_extension_is_fine(self) -> None:
        STATE["offers"] = {"rows": [{"key": "a", "name": "A", "picture": "art/a.png"}]}

        self.assertEqual(200, self.offers().status_code)


class Taking(Served):
    def test_a_pick_is_called_with_the_row_the_kind_and_the_game(self) -> None:
        self.assertEqual(200, self.take(row="a").status_code)

        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other", "table_id": ""},
                           "playfield_video", "a")], STATE["taken"])

    def test_a_pick_is_told_the_table_the_file_is_saved_for(self) -> None:
        self.assertEqual(200, self.take(table_id="t1").status_code)

        self.assertEqual("t1", STATE["taken"][0][0]["table_id"])

    def test_it_answers_a_job_a_dialog_or_a_message(self) -> None:
        for answer in ({"job": "j1", "message": "Started"}, {"job": "j1"}, {"action": "get"},
                       {"message": "Nothing to do"}):
            STATE["take"] = answer
            with self.subTest(answer=answer):
                response = self.take()
                self.assertEqual(200, response.status_code, response.text)
                self.assertEqual(answer, response.json())

    def test_a_dialog_the_extension_never_declared_is_refused(self) -> None:
        STATE["take"] = {"action": "nope"}

        self.assertEqual(400, self.take().status_code)

    def test_an_answer_that_is_not_one_of_the_three_is_refused(self) -> None:
        wrong = {
            "nothing": {},
            "no answer": None,
            "a job and a dialog": {"job": "j1", "action": "get"},
            "a dialog and a message": {"action": "get", "message": "x"},
            "a key it does not have": {"link": "x"},
            "an empty job": {"job": " "},
            "a job that is not text": {"job": ["x"]},
            "a dialog that is not text": {"action": ["get"]},
            "a message that is not text": {"message": 5},
            "a message of nothing but space": {"message": "  "},
            "a list": ["x"],
        }
        for what, answer in wrong.items():
            STATE["take"] = answer
            with self.subTest(what):
                self.assertEqual(400, self.take().status_code)

    def test_a_blank_message_beside_a_job_is_no_message(self) -> None:
        STATE["take"] = {"job": "j1", "message": "  "}

        response = self.take()

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"job": "j1"}, response.json())

    def test_a_source_that_raises_is_said(self) -> None:
        STATE["take"] = ValueError("The host said no")

        self.assertIn("The host said no", self.refused(self.take(), 400)["message"])

    def test_a_private_game_is_never_taken_into(self) -> None:
        self.refused(self.take(game_id="hidden"), 404, t(
            "error.extensions.verb_none", display=NAME, key="files"))
        self.assertEqual([], STATE["taken"])

    def test_a_kind_the_source_does_not_offer_is_not_found(self) -> None:
        self.assertEqual(404, self.take(kind="wheel").status_code)
        self.assertEqual([], STATE["taken"])

    def test_a_source_switched_off_in_settings_is_not_taken_from(self) -> None:
        STATE["off"] = [f"{NAME}.files"]

        self.assertEqual(400, self.take().status_code)
        self.assertEqual([], STATE["taken"])


class DrawingARow(Served):
    def test_a_row_says_which_vps_file_it_is_and_how_far_it_has_got(self) -> None:
        STATE["offers"] = {"rows": [{
            "key": "a", "name": "A", "vps_file_id": " f-1 ",
            "state": {"text": "Downloading", "level": "unknown", "hint": "42 percent"},
            "acts": [{"key": "retry", "label": "Try again"}]}]}

        row = self.offers().json()["rows"][0]

        self.assertEqual("f-1", row["vps_file_id"])
        self.assertEqual({"text": "Downloading", "level": "unknown", "hint": "42 percent"},
                         row["state"])
        self.assertEqual([{"key": "retry", "label": "Try again"}], row["acts"])

    def test_a_state_with_no_level_is_unknown(self) -> None:
        STATE["offers"] = {"rows": [{"key": "a", "name": "A", "state": {"text": "Queued"}}]}

        self.assertEqual({"text": "Queued", "level": "unknown", "hint": ""},
                         self.offers().json()["rows"][0]["state"])

    def test_a_row_that_says_it_wrongly_is_refused(self) -> None:
        wrong: dict[str, dict[str, Any]] = {
            "a state that is not an object": {"state": "Queued"},
            "a state with no text": {"state": {"level": "on"}},
            "a state field it does not have": {"state": {"text": "x", "color": "red"}},
            "a level it does not have": {"state": {"text": "x", "level": "purple"}},
            "acts that are not a list": {"acts": "retry"},
            "an act that is not an object": {"acts": ["retry"]},
            "an act with no label": {"acts": [{"key": "retry"}]},
            "two acts with one key": {"acts": [{"key": "a", "label": "A"},
                                               {"key": "a", "label": "B"}]},
            "a file id that is not text": {"vps_file_id": 5},
        }
        for what, extra in wrong.items():
            STATE["offers"] = {"rows": [{"key": "a", "name": "A", **extra}]}
            with self.subTest(what):
                self.assertEqual(400, self.offers().status_code)


class Discovering(Served):
    """Discovery asks by VPS id alone: a game that is not in the library has offers too."""

    def test_a_game_that_is_not_in_the_library_is_asked_about_by_its_vps_id(self) -> None:
        response = self.offers(game_id="", vps_id="vps-9", name="Taxi")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([({"game_id": "", "vps_id": "vps-9", "name": "Taxi", "table_id": ""},
                           "playfield_video")], STATE["asked"])

    def test_nothing_identified_is_nothing_to_ask_about(self) -> None:
        response = self.client.get(f"/ext/{NAME}/sources/files/offers",
                                   params={"kind": "playfield_video"})

        self.assertEqual({"rows": [], "note": "", "link": None}, response.json())
        self.assertEqual([], STATE["asked"])


class TakingMany(Served):
    def many(self, picks: list, key: str = "files", game_id: str = "g1") -> Any:
        return self.client.post(f"/ext/{NAME}/sources/{key}/take-many", json={
            "picks": picks,
            "who": {"game_id": game_id, "vps_id": "vps-9", "name": "Other"}})

    def test_every_ticked_file_goes_in_one_call(self) -> None:
        response = self.many([{"kind": "table", "key": "t"}, {"kind": "pup_pack", "key": "p"}])

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual({"message": "Getting them"}, response.json())
        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other", "table_id": ""},
                           [{"kind": "table", "key": "t"}, {"kind": "pup_pack", "key": "p"}])],
                         STATE["taken_many"])

    def test_it_answers_a_message_or_nothing(self) -> None:
        STATE["many"] = None
        self.assertEqual({}, self.many([{"kind": "table", "key": "t"}]).json())

        STATE["many"] = {"message": "  "}
        self.assertEqual({}, self.many([{"kind": "table", "key": "t"}]).json())

    def test_an_answer_with_anything_else_is_refused(self) -> None:
        for answer in ({"job": "j1"}, {"action": "get"}, ["x"], {"message": 5}):
            STATE["many"] = answer
            with self.subTest(answer=answer):
                self.assertEqual(400, self.many([{"kind": "table", "key": "t"}]).status_code)

    def test_a_kind_the_source_does_not_offer_is_not_found(self) -> None:
        self.assertEqual(404, self.many([{"kind": "wheel", "key": "w"}]).status_code)
        self.assertEqual([], STATE["taken_many"])

    def test_a_pick_with_no_key_is_refused(self) -> None:
        self.assertEqual(400, self.many([{"kind": "table", "key": " "}]).status_code)

    def test_no_picks_is_no_call(self) -> None:
        self.assertEqual({}, self.many([]).json())
        self.assertEqual([], STATE["taken_many"])

    def test_a_source_that_cannot_take_many_says_so(self) -> None:
        self.refused(self.many([{"kind": "table", "key": "t"}], key="plain"), 404, t(
            "error.extensions.source_verb", display=self.display(), key="plain",
            verb="take_many"))

    def test_a_private_game_is_never_taken_into(self) -> None:
        self.assertEqual(404, self.many([{"kind": "table", "key": "t"}],
                                        game_id="hidden").status_code)
        self.assertEqual([], STATE["taken_many"])

    def test_a_source_switched_off_in_settings_is_not_taken_from(self) -> None:
        STATE["off"] = [f"{NAME}.files"]

        self.assertEqual(400, self.many([{"kind": "table", "key": "t"}]).status_code)
        self.assertEqual([], STATE["taken_many"])


class Acting(Served):
    def act(self, verb: str = "retry", kind: str = "table", row: str = "t",
            key: str = "files") -> Any:
        return self.client.post(f"/ext/{NAME}/sources/{key}/act", json={
            "act": verb, "kind": kind, "row": row,
            "who": {"game_id": "g1", "vps_id": "vps-9", "name": "Other"}})

    def test_a_button_is_pressed_on_its_row(self) -> None:
        STATE["act"] = {"message": "Trying again"}

        response = self.act()

        self.assertEqual({"message": "Trying again"}, response.json())
        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other",
                            "table_id": ""}, "table", "t", "retry")], STATE["acted"])

    def test_a_button_may_open_a_dialog_or_do_nothing_visible(self) -> None:
        STATE["act"] = {"action": "get"}
        self.assertEqual({"action": "get"}, self.act().json())

        STATE["act"] = None
        self.assertEqual({}, self.act().json())

    def test_a_pane_button_has_no_kind_and_no_row(self) -> None:
        response = self.act(verb="stop", kind="", row="")

        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other",
                            "table_id": ""}, "", "", "stop")], STATE["acted"])

    def test_a_kind_the_source_does_not_offer_is_not_found(self) -> None:
        self.assertEqual(404, self.act(kind="wheel").status_code)

    def test_a_source_that_cannot_act_says_so(self) -> None:
        self.refused(self.act(key="plain"), 404, t(
            "error.extensions.source_verb", display=self.display(), key="plain", verb="act"))

    def test_an_answer_that_is_not_one_of_the_three_is_refused(self) -> None:
        for answer in ({"job": "j1", "action": "get"}, {"link": "x"}, ["x"], {"action": "nope"}):
            STATE["act"] = answer
            with self.subTest(answer=answer):
                self.assertEqual(400, self.act().status_code)


class Looking(Served):
    def look(self, kind: str = "table", row: str = "t", key: str = "files") -> Any:
        return self.client.get(f"/ext/{NAME}/sources/{key}/look", params={
            "kind": kind, "row": row, "game_id": "g1", "vps_id": "vps-9", "name": "Other"})

    def test_what_the_page_says_comes_back_checked(self) -> None:
        STATE["look"] = {
            "pictures": [WEB, " "], "description": " A long description ",
            "files": [{"name": "taxi.vpx", "size": 1024}, {"name": "readme"}],
            "note": "From the page"}

        answer = self.look().json()

        self.assertRegex(answer["pictures"][0], rf"^/api/v1/ext/{NAME}/pictures/.+")
        self.assertEqual(1, len(answer["pictures"]))
        self.assertEqual("A long description", answer["description"])
        self.assertEqual([{"name": "taxi.vpx", "size": 1024}, {"name": "readme", "size": None}],
                         answer["files"])
        self.assertEqual("From the page", answer["note"])
        self.assertEqual([({"game_id": "g1", "vps_id": "vps-9", "name": "Other",
                            "table_id": ""}, "table", "t")], STATE["looked"])

    def test_nothing_to_say_is_an_empty_look(self) -> None:
        STATE["look"] = None

        self.assertEqual({"pictures": [], "description": "", "files": [], "note": ""},
                         self.look().json())

    def test_what_is_shown_is_limited(self) -> None:
        STATE["look"] = {
            "pictures": [WEB] * (serving.LOOK_PICTURES + 3),
            "files": [{"name": "f"}] * (serving.LOOK_FILES + 3),
            "description": "x" * (serving.LOOK_DESCRIPTION + 10)}

        answer = self.look().json()

        self.assertEqual(serving.LOOK_PICTURES, len(answer["pictures"]))
        self.assertEqual(serving.LOOK_FILES, len(answer["files"]))
        self.assertEqual(serving.LOOK_DESCRIPTION, len(answer["description"]))

    def test_a_look_of_a_shape_that_is_not_this_one_is_refused(self) -> None:
        wrong = {
            "a key it does not have": {"title": "x"},
            "pictures that are not a list": {"pictures": "x"},
            "a file that is not an object": {"files": ["x"]},
            "a file field it does not have": {"files": [{"name": "f", "kind": "x"}]},
            "a size that is not a number": {"files": [{"name": "f", "size": "big"}]},
            "a size below nothing": {"files": [{"name": "f", "size": -1}]},
            "a size that is true": {"files": [{"name": "f", "size": True}]},
            "a size no file has": {"files": [{"name": "f", "size": 10 ** 400}]},
            "a file with no name": {"files": [{"size": 3}]},
        }
        for what, answer in wrong.items():
            STATE["look"] = answer
            with self.subTest(what):
                self.assertEqual(400, self.look().status_code)

    def test_a_source_that_cannot_look_says_so(self) -> None:
        self.refused(self.look(key="plain"), 404, t(
            "error.extensions.source_verb", display=self.display(), key="plain", verb="look"))

    def test_a_kind_the_source_does_not_offer_is_not_found(self) -> None:
        self.assertEqual(404, self.look(kind="wheel").status_code)


class Moving(unittest.TestCase):
    """The registry's version moves each time a source says an offer did."""

    def setUp(self) -> None:
        sources.clear()
        self.addCleanup(sources.clear)

    def test_nothing_has_moved_at_the_start(self) -> None:
        self.assertEqual((0, []), sources.changes_since(0))

    def test_an_offer_that_moved_is_named_once_however_often(self) -> None:
        sources.changed("v1")
        sources.changed("v1")
        sources.changed("v2")

        self.assertEqual((3, ["v1", "v2"]), sources.changes_since(0))

    def test_only_what_moved_after_a_version_is_named(self) -> None:
        sources.changed("v1")
        seen, _ = sources.changes_since(0)
        sources.changed("v2")

        self.assertEqual((2, ["v2"]), sources.changes_since(seen))
        self.assertEqual((2, []), sources.changes_since(2))

    def test_a_reader_too_far_behind_is_told_everything_moved(self) -> None:
        for number in range(sources.CHANGES_KEPT + 5):
            sources.changed(f"v{number}")

        self.assertEqual((sources.CHANGES_KEPT + 5, [""]), sources.changes_since(1))

    def test_a_version_ahead_of_the_registry_reads_as_everything_moved(self) -> None:
        sources.changed("v1")

        self.assertEqual((1, [""]), sources.changes_since(50))


class Listed(Served):
    def setUp(self) -> None:
        super().setUp()
        self.api = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def listed(self) -> dict:
        response = self.api.get("/media-sources")
        self.assertEqual(200, response.status_code, response.text)
        return next(one for one in response.json()["sources"] if one.get("extension") == NAME)

    def test_settings_lists_it_with_the_kinds_it_offers_and_who_offers_it(self) -> None:
        self.assertEqual({"id": f"{NAME}.files", "name": "Example Files", "url": "",
                          "enabled": True, "kinds": sorted(KINDS), "extension": NAME,
                          "key": "files", "always_on": False, "versions": False},
                         self.listed())

    def test_its_switch_follows_what_settings_has_switched_off(self) -> None:
        STATE["off"] = [f"{NAME}.files"]

        self.assertFalse(self.listed()["enabled"])

    def test_what_moved_is_asked_by_version(self) -> None:
        sources.changed("v1")
        sources.changed("v2")

        everything = self.api.get("/media-sources/changes").json()
        after_first = self.api.get("/media-sources/changes", params={"since": 1}).json()

        self.assertEqual({"version": 2, "vps_ids": ["v1", "v2"]}, everything)
        self.assertEqual({"version": 2, "vps_ids": ["v2"]}, after_first)


if __name__ == "__main__":
    unittest.main()
