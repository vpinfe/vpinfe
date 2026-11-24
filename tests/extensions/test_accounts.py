"""A player's account with an extension, over core's routes, driven through a sample
extension: read, written, acted on, shared, carded, and never answering with a secret."""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from configparser import ConfigParser
from pathlib import Path
from typing import Any, cast

from fastapi import APIRouter, FastAPI, HTTPException, Response
from PIL import Image
from starlette.testclient import TestClient

import httpapi
from common import extensions, service_errors
from common.extensions import accounts, cards, host, store
from common.extensions.context import ContractError, ExtensionPlayers, ExtensionUI
from common.extensions.contract import Unavailable
from common.games import player_records
from common.i18n import t
from common.players import players
from httpapi import events as event_stream
from tests.support import pictures
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "extensions"
MODULE = "tests.extensions.test_accounts"
TOKEN = "tok-" + "Zq9" * 20

# The one kind of card the sample reads, and the version of it that it writes.
CARD = "sample_card"
CARD_VERSION = 1

# What a player's `picture` value names, and so what its service answers with.
PICTURES = {"red": (200, 30, 30), "blue": (30, 30, 200)}


def sample(ctx: Any) -> None:
    """An account of a handle and a secret token, with an act, a card, a picture and a
    secret setting. Its secrets are answered with their values on purpose: core takes them
    out on the way to a client, and the tests hold it to that."""

    def read(player_id: str) -> dict:
        held = ctx.players.account(player_id)
        return {"fields": [
                    {"key": "handle", "label": "Handle", "type": "string",
                     "value": held.get("handle", "")},
                    {"key": "token", "label": "Token", "type": "secret",
                     "value": held.get("token", "")}],
                "status": "Ready" if held.get("handle") else "No handle",
                "acts": [{"key": "ping", "label": "Ping"}],
                "card": bool(held.get("handle") and held.get("token")),
                "picture": held.get("picture") in PICTURES}

    def write(player_id: str, values: dict) -> None:
        ctx.players.set_account(player_id, {**ctx.players.account(player_id), **values})

    def ping(player_id: str) -> str:
        return f"Pinged {ctx.players.account(player_id).get('handle', '')}"

    def card(player_id: str) -> dict | None:
        held = ctx.players.account(player_id)
        if not (held.get("handle") and held.get("token")):
            return None
        player = ctx.players.get(player_id) or {}
        return {"card": {"type": CARD, "version": CARD_VERSION, "handle": held["handle"],
                         "token": held["token"], "initials": player.get("initials", "")},
                "filename": f"sample-{held['handle']}"}

    def read_card(found: dict) -> dict:
        if found.get("version") != CARD_VERSION:
            raise ValueError("Not a card this reads")
        return {"name": "", "initials": str(found.get("initials") or ""),
                "values": {"handle": str(found.get("handle") or ""),
                           "token": str(found.get("token") or "")}}

    pictured = APIRouter()

    @pictured.get("/accounts/{player_id}/picture")
    def picture(player_id: str) -> Response:
        named = ctx.players.account(player_id).get("picture", "")
        if named == "denied":
            raise HTTPException(403, detail="The service refused")
        if named == "text":
            return Response(b"not an image", media_type="text/plain")
        color = PICTURES.get(named.removesuffix("-jpg"))
        if color is None:
            raise HTTPException(404, detail="No picture yet")
        out = io.BytesIO()
        if named.endswith("-jpg"):
            Image.new("RGB", (40, 20), color).save(out, format="JPEG")
            return Response(out.getvalue(), media_type="image/jpg")
        Image.new("RGB", (40, 20), color).save(out, format="PNG")
        return Response(out.getvalue(), media_type="image/png")

    ctx.ui.settings([{"key": "greeting", "type": "string"},
                     {"key": "token", "type": "secret"}])
    ctx.add_router(pictured)
    ctx.ui.account(read=read, write=write, acts={"ping": ping}, card=card,
                   read_card=read_card, label="Sample", cards=(CARD,))


def read_only(ctx: Any) -> None:
    ctx.ui.account(read=lambda player_id: {"status": "Fixed"})


def failing(ctx: Any) -> None:
    """Every function it supplied raises, the way a service that is down or says no does."""

    def read(player_id: str) -> dict:
        raise Unavailable("The service did not answer")

    def write(player_id: str, values: dict) -> None:
        raise ValueError("That handle is taken")

    def ping(player_id: str) -> None:
        raise RuntimeError("The service is down for good")

    ctx.ui.account(read=read, write=write, acts={"ping": ping})


def _installed(root: Path, name: str, register: str, display: str) -> Path:
    """An extension named `name` in a folder under `root`, whose `register` is the function
    of that name in this module."""
    directory = root / name
    directory.mkdir(parents=True)
    (directory / "extension.json").write_text(json.dumps({
        "name": name, "display_name": display, "version": "1.0.0",
        "description": "Holds accounts for the tests that drive them.",
        "requires_platform": 1, **IDENTITY, "scopes": ["players:read"],
        "capabilities": ["config:own", "ui:mount"]}), encoding="utf-8")
    (directory / "__init__.py").write_text(
        f"from {MODULE} import {register} as register\n", encoding="utf-8")
    return directory


def _owner_config() -> ConfigParser:
    parser = ConfigParser()
    parser.add_section("vpinplay")
    parser.set("vpinplay", "initials", "OWN")
    return parser


class AccountsCase(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = store.ExtensionStore(self.root)
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        players.reset_for_tests(self.root / "players.json")
        self.addCleanup(players.reset_for_tests)
        accounts.reset_for_tests()
        self.addCleanup(accounts.reset_for_tests)
        owner = players.get_roster().ensure_owner(_owner_config())
        assert owner is not None
        self.owner = owner.player_id
        self.registry.load(_installed(self.root / "installed", "sample", "sample", "Sample"))
        self.registry.load(FIXTURES / "bystander")
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.addCleanup(event_stream.reset)

    def ok(self, response, status: int = 200) -> dict:
        self.assertEqual(response.status_code, status, response.text)
        return response.json()

    def account(self, player_id: str) -> dict:
        return self.ok(self.client.get(f"/players/{player_id}/accounts/sample"))

    def fill(self, player_id: str, **values: str) -> dict:
        return self.ok(self.client.put(f"/players/{player_id}/accounts/sample",
                                       json={"values": values}))

    def file(self) -> dict:
        path = self.root / "extension_settings" / "sample.json"
        return json.loads(path.read_text("utf-8")) if path.exists() else {}

    def secrets_answered(self, answer: object) -> list[str]:
        """Every place the token appears in an answer, as text."""
        return [TOKEN] * json.dumps(answer).count(TOKEN)


class ReadingAccounts(AccountsCase):
    def test_a_player_is_offered_an_account_with_each_extension_that_holds_them(self) -> None:
        found = self.ok(self.client.get(f"/players/{self.owner}/accounts"))["accounts"]

        self.assertEqual([one["extension"] for one in found], ["sample"])
        self.assertEqual((found[0]["label"], found[0]["status"], found[0]["acts"]),
                         ("Sample", "No handle",
                          [{"key": "ping", "label": "Ping", "description": ""}]))

    def test_an_empty_account_says_its_secret_is_not_set(self) -> None:
        token = next(one for one in self.account(self.owner)["fields"]
                     if one["key"] == "token")

        self.assertEqual(token, {"key": "token", "label": "Token", "type": "secret",
                                 "set": False})

    def test_the_listing_names_what_an_extension_offers(self) -> None:
        listed = self.ok(self.client.get("/extensions"))["extensions"]

        self.assertEqual(next(one for one in listed if one["name"] == "sample")["account"],
                         {"base": "/accounts", "label": "Sample", "cards": [CARD],
                          "marker": "sample", "check": "", "consent": [],
                          "sends_picture": False, "share_help": ""})

    def test_an_extension_with_no_words_for_share_leaves_them_to_core(self) -> None:
        self.assertEqual(self.account(self.owner)["share_help"], "")

    def test_an_extension_that_holds_no_accounts_is_a_404(self) -> None:
        response = self.client.get(f"/players/{self.owner}/accounts/bystander")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.players.no_accounts_in", extension="bystander"))

    def test_a_player_nobody_has_is_a_404(self) -> None:
        self.assertEqual(self.client.get("/players/Nobody0000/accounts").status_code, 404)

    def test_an_extension_switched_off_offers_no_account(self) -> None:
        self.registry.switch("sample", False)

        found = self.ok(self.client.get(f"/players/{self.owner}/accounts"))["accounts"]

        self.assertEqual(found, [])


class WritingAccounts(AccountsCase):
    def test_values_written_are_read_back_and_kept_in_the_extensions_own_file(self) -> None:
        answered = self.fill(self.owner, handle="own-handle", token=TOKEN)

        self.assertEqual(answered["status"], "Ready")
        self.assertEqual(self.file()["accounts"][self.owner],
                         {"handle": "own-handle", "token": TOKEN})

    def test_accounts_are_not_read_as_settings(self) -> None:
        self.fill(self.owner, handle="own-handle")

        self.assertNotIn("accounts", self.store.settings("sample"))

    def test_a_guests_account_is_held_in_memory_and_never_written(self) -> None:
        guest = players.get_roster().add_guest("ABC").player_id

        self.fill(guest, handle="their-handle", token=TOKEN)

        self.assertEqual(self.account(guest)["status"], "Ready")
        self.assertNotIn(TOKEN, json.dumps(self.file()))

    def test_an_act_answers_what_came_of_it(self) -> None:
        self.fill(self.owner, handle="own-handle")

        said = self.ok(self.client.post(f"/players/{self.owner}/accounts/sample/acts/ping"))

        self.assertEqual(said, {"message": "Pinged own-handle"})

    def test_an_act_the_extension_does_not_have_is_a_404(self) -> None:
        response = self.client.post(f"/players/{self.owner}/accounts/sample/acts/dance")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.extensions.no_act", display="Sample", key="dance"))

    def test_removing_a_player_forgets_their_accounts(self) -> None:
        kept = players.get_roster().add_player("Jordan", "JOR").player_id
        self.fill(kept, handle="their-handle", token=TOKEN)

        self.assertEqual(self.client.delete(f"/players/{kept}").status_code, 204)

        self.assertNotIn(kept, self.file().get("accounts", {}))

    def test_a_guest_signing_out_takes_their_account_with_them(self) -> None:
        guest = players.get_roster().add_guest("ABC").player_id
        self.fill(guest, handle="their-handle", token=TOKEN)

        self.client.delete(f"/players/{guest}")

        self.assertEqual(accounts.holders("sample", self.store), [])


class UnderTheMount(AccountsCase):
    """Served the way the app serves it, under /api/v1, where core's routes for the
    extension are asked for at a path of their own under that prefix."""

    def test_an_account_is_asked_for_under_the_prefix(self) -> None:
        outer = FastAPI()
        outer.mount(httpapi.API_PREFIX, httpapi.create_api_app())
        client = TestClient(outer, raise_server_exceptions=False)
        where = f"{httpapi.API_PREFIX}/players/{self.owner}/accounts/sample"

        written = client.put(where, json={"values": {"handle": "h", "token": TOKEN}})
        read = client.get(where)

        self.assertEqual((written.status_code, read.status_code), (200, 200), read.text)
        self.assertEqual((read.json()["status"], self.secrets_answered(read.json())),
                         ("Ready", []))


class WhatCoreSaysForAnExtension(AccountsCase):
    """The functions an extension supplies refuse by raising; the words and the status are
    core's."""

    def setUp(self) -> None:
        super().setUp()
        for name, register, display in (("failing", "failing", "Failing"),
                                        ("readonly", "read_only", "Read Only")):
            self.registry.load(_installed(self.root / "installed", name, register, display))
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def test_an_extension_that_holds_no_accounts_has_no_account_routes_to_answer(self) -> None:
        response = self.client.get(f"/ext/bystander/accounts/{self.owner}")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.extensions.no_account", display="Bystander"))

    def test_a_service_that_does_not_answer_is_a_502_in_the_extensions_words(self) -> None:
        response = self.client.get(f"/ext/failing/accounts/{self.owner}")

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.json()["error"]["message"], "The service did not answer")

    def test_a_refusal_is_a_400_in_the_extensions_words(self) -> None:
        response = self.client.put(f"/ext/failing/accounts/{self.owner}",
                                   json={"values": {"handle": "h"}})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"], "That handle is taken")

    def test_an_act_that_fails_is_a_400_in_the_extensions_words(self) -> None:
        response = self.client.post(f"/players/{self.owner}/accounts/failing/acts/ping")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"], "The service is down for good")

    def test_an_account_with_no_write_function_cannot_be_changed(self) -> None:
        response = self.client.put(f"/players/{self.owner}/accounts/readonly",
                                   json={"values": {"handle": "h"}})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.extensions.account_read_only", display="Read Only"))

    def test_an_account_with_no_card_function_has_no_card(self) -> None:
        response = self.client.get(f"/players/{self.owner}/accounts/readonly/card")

        self.assertEqual(response.status_code, 404)

    def test_an_extension_that_fails_to_answer_is_listed_with_its_error(self) -> None:
        found = self.ok(self.client.get(f"/players/{self.owner}/accounts"))["accounts"]

        failed = next(one for one in found if one["extension"] == "failing")
        self.assertEqual(failed["error"], "The service did not answer")

    def test_a_read_that_fails_does_not_switch_the_extension_off(self) -> None:
        self.client.get(f"/ext/failing/accounts/{self.owner}")

        failing = self.registry.get("failing")
        assert failing is not None
        self.assertTrue(failing.running)


class Sharing(AccountsCase):
    def test_an_account_does_not_share_until_somebody_says_so(self) -> None:
        self.assertFalse(self.account(self.owner)["share"])

    def test_share_is_kept_per_player_in_the_roster(self) -> None:
        said = self.ok(self.client.put(f"/players/{self.owner}/accounts/sample/share",
                                       json={"share": True}))

        self.assertTrue(said["share"])
        saved = json.loads((self.root / "players.json").read_text("utf-8"))
        self.assertEqual(saved["players"][0]["share"], {"sample": True})

    def test_share_goes_off_again(self) -> None:
        where = f"/players/{self.owner}/accounts/sample/share"
        self.client.put(where, json={"share": True})

        self.assertFalse(self.ok(self.client.put(where, json={"share": False}))["share"])


class Cards(AccountsCase):
    def card_of(self, player_id: str) -> str:
        response = self.client.get(f"/players/{player_id}/accounts/sample/card")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "image/svg+xml")
        return response.text

    def join(self, card: str, status: int = 201) -> dict:
        return self.ok(self.client.post("/players/guests/card", json={"card": card}), status)

    def test_a_card_is_the_accounts_text_drawn_under_the_extensions_name(self) -> None:
        self.fill(self.owner, handle="own-handle", token=TOKEN)

        drawn = self.card_of(self.owner)

        self.assertIn("<!--SAMPLE_PAYLOAD:", drawn)
        self.assertEqual(cards.read(drawn), {"type": "sample_card", "version": 1,
                                             "handle": "own-handle", "token": TOKEN,
                                             "initials": "OWN"})

    def test_a_card_is_saved_under_the_name_the_extension_gives(self) -> None:
        self.fill(self.owner, handle="own handle", token=TOKEN)

        response = self.client.get(f"/players/{self.owner}/accounts/sample/card")

        self.assertEqual(response.headers["content-disposition"],
                         'attachment; filename="sample-own_handle.svg"')

    def test_an_account_with_nothing_to_card_says_so(self) -> None:
        response = self.client.get(f"/players/{self.owner}/accounts/sample/card")

        self.assertEqual(response.status_code, 404)

    def test_a_card_made_here_joins_as_a_guest_holding_that_account(self) -> None:
        self.fill(self.owner, handle="own-handle", token=TOKEN)
        drawn = self.card_of(self.owner)

        guest = self.join(drawn)

        self.assertEqual((guest["guest"], guest["up"], guest["initials"]),
                         (True, True, "OWN"))
        self.assertEqual(accounts.values("sample", guest["id"]),
                         {"handle": "own-handle", "token": TOKEN})
        self.assertEqual(cards.read(self.card_of(guest["id"])), cards.read(drawn))

    def test_a_guest_who_joins_with_a_card_shares(self) -> None:
        guest = self.join(json.dumps({"type": "sample_card", "version": 1,
                                      "handle": "h", "token": TOKEN, "initials": "ABC"}))

        self.assertTrue(self.account(guest["id"])["share"])

    def test_a_cards_initials_are_taken_as_they_are(self) -> None:
        guest = self.join(json.dumps({"type": "sample_card", "version": 1, "handle": "h",
                                      "token": TOKEN, "initials": "ab"}))

        self.assertEqual(guest["initials"], "AB")

    def test_initials_longer_than_three_are_refused(self) -> None:
        response = self.client.post("/players/guests/card", json={"card": json.dumps(
            {"type": "sample_card", "version": 1, "handle": "h", "token": TOKEN,
             "initials": "ABCD"})})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.players.card_initials"))

    def test_the_same_card_again_puts_that_guest_up_rather_than_adding_another(self) -> None:
        card = json.dumps({"type": "sample_card", "version": 1, "handle": "h",
                           "token": TOKEN, "initials": "ABC"})
        first = self.join(card)["id"]
        players.get_roster().set_who_is_up([self.owner])

        again = self.join(card)

        self.assertEqual(again["id"], first)
        self.assertEqual([one.player_id for one in players.get_roster().up()], [first])
        self.assertEqual(len([one for one in players.get_roster().players() if one.guest]), 1)

    def test_a_card_nothing_here_reads_is_refused(self) -> None:
        response = self.client.post("/players/guests/card",
                                    json={"card": '{"type": "elsewhere_card"}'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.players.card_unclaimed"))

    def test_a_card_the_extension_will_not_read_is_its_refusal(self) -> None:
        response = self.client.post("/players/guests/card", json={
            "card": '{"type": "sample_card", "version": 9}'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"], "Not a card this reads")

    def test_a_file_that_is_not_a_card_is_refused(self) -> None:
        response = self.client.post("/players/guests/card", json={"card": "<svg></svg>"})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.players.card_unreadable"))


class UsingACard(AccountsCase):
    """A card made elsewhere, taken into an account a player already has here."""

    def use(self, player_id: str, card: str, status: int = 200) -> dict:
        return self.ok(self.client.post(f"/players/{player_id}/accounts/sample/card",
                                        json={"card": card}), status)

    def made_elsewhere(self, **more: str) -> str:
        return cards.drawn({"type": "sample_card", "version": 1, "handle": "far-handle",
                            "token": TOKEN, "initials": "FAR", **more}, "sample")

    def test_the_card_s_values_become_the_account_s(self) -> None:
        answered = self.use(self.owner, self.made_elsewhere())

        self.assertEqual(accounts.values("sample", self.owner),
                         {"handle": "far-handle", "token": TOKEN})
        self.assertEqual(next(one for one in answered["fields"]
                              if one["key"] == "token")["set"], True)
        self.assertEqual(self.secrets_answered(answered), [])

    def test_they_replace_the_values_held(self) -> None:
        self.fill(self.owner, handle="own-handle", token="tok-old")

        self.use(self.owner, self.made_elsewhere())

        self.assertEqual(accounts.values("sample", self.owner),
                         {"handle": "far-handle", "token": TOKEN})

    def test_the_player_keeps_their_initials_and_share(self) -> None:
        self.use(self.owner, self.made_elsewhere())

        owner = players.get_roster().get(self.owner)
        assert owner is not None
        self.assertEqual((owner.initials, self.account(self.owner)["share"]), ("OWN", False))

    def test_the_card_s_text_on_its_own_is_taken_too(self) -> None:
        self.use(self.owner, json.dumps({"type": "sample_card", "version": 1,
                                         "handle": "h", "token": TOKEN, "initials": "AB"}))

        self.assertEqual(accounts.values("sample", self.owner)["handle"], "h")

    def test_a_card_another_extension_reads_is_refused(self) -> None:
        response = self.client.post(f"/players/{self.owner}/accounts/sample/card",
                                    json={"card": '{"type": "elsewhere_card"}'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["message"],
                         t("error.players.card_not_for", extension="Sample"))
        self.assertEqual(accounts.values("sample", self.owner), {})

    def test_a_card_the_extension_will_not_read_writes_nothing(self) -> None:
        response = self.client.post(f"/players/{self.owner}/accounts/sample/card",
                                    json={"card": '{"type": "sample_card", "version": 9}'})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(accounts.values("sample", self.owner), {})

    def test_a_player_nobody_has_is_a_404(self) -> None:
        response = self.client.post("/players/nobody/accounts/sample/card",
                                    json={"card": self.made_elsewhere()})

        self.assertEqual(response.status_code, 404)

    def test_an_account_says_whether_a_card_can_be_used_for_it(self) -> None:
        self.assertTrue(self.account(self.owner)["reads_cards"])


class PictureFromAnAccount(AccountsCase):
    """The service's picture, copied once into the player's own."""

    def use(self, player_id: str, status: int = 200) -> dict:
        return self.ok(self.client.post(f"/players/{player_id}/accounts/sample/picture"),
                       status)

    def kept(self, player_id: str) -> tuple[int, int, int] | None:
        found = players.get_roster().pictures_dir / f"{player_id}.webp"
        if not found.exists():
            return None
        with Image.open(found) as opened:
            pixel = opened.convert("RGB").getpixel((opened.width // 2, opened.height // 2))
            return cast(tuple[int, int, int], pixel)

    def test_an_account_offers_a_picture_only_where_its_service_answers_one(self) -> None:
        self.assertFalse(self.account(self.owner)["picture"])

        self.fill(self.owner, picture="red")

        self.assertTrue(self.account(self.owner)["picture"])

    def test_using_it_keeps_it_as_an_upload_is_kept(self) -> None:
        self.fill(self.owner, picture="red")

        answered = self.use(self.owner)

        red, green, blue = self.kept(self.owner) or (0, 0, 0)
        self.assertGreater(red, green + 100)
        self.assertGreater(red, blue + 100)
        self.assertIsNotNone(answered["picture_version"])

    def test_it_is_copied_once_and_never_followed(self) -> None:
        self.fill(self.owner, picture="red")
        self.use(self.owner)

        self.fill(self.owner, picture="blue")

        red, _green, blue = self.kept(self.owner) or (0, 0, 0)
        self.assertGreater(red, blue)

        self.use(self.owner)

        red, _green, blue = self.kept(self.owner) or (0, 0, 0)
        self.assertGreater(blue, red)

    def test_a_service_with_none_is_a_404_and_changes_nothing(self) -> None:
        self.fill(self.owner, picture="red")
        self.use(self.owner)
        self.fill(self.owner, picture="")

        self.use(self.owner, 404)

        self.assertIsNotNone(self.kept(self.owner))

    def test_a_guest_has_none_to_set(self) -> None:
        guest = players.get_roster().add_guest("GST")

        self.fill(guest.player_id, picture="red")
        response = self.client.post(f"/players/{guest.player_id}/accounts/sample/picture")

        self.assertEqual(response.status_code, 400)
        self.assertIsNone(self.kept(guest.player_id))

    def test_a_service_that_refuses_is_an_error_and_not_a_missing_picture(self) -> None:
        self.fill(self.owner, picture="denied")

        response = self.client.post(f"/players/{self.owner}/accounts/sample/picture")

        self.assertNotIn(response.status_code, (200, 404))
        self.assertIsNone(self.kept(self.owner))

    def test_what_is_not_an_image_is_refused_and_changes_nothing(self) -> None:
        self.fill(self.owner, picture="text")

        self.use(self.owner, 400)

        self.assertIsNone(self.kept(self.owner))

    def test_a_jpeg_served_as_image_jpg_is_kept(self) -> None:
        self.fill(self.owner, picture="blue-jpg")

        self.use(self.owner)

        red, _green, blue = self.kept(self.owner) or (0, 0, 0)
        self.assertGreater(blue, red)

    def test_a_player_nobody_has_is_a_404(self) -> None:
        self.assertEqual(self.client.post(
            "/players/Nobody0000/accounts/sample/picture").status_code, 404)

    def test_an_extension_that_is_not_running_has_none_to_copy(self) -> None:
        self.assertEqual(self.client.post(
            f"/players/{self.owner}/accounts/bystander/picture").status_code, 404)


class SendingAPicture(AccountsCase):
    """`ctx.players.picture`, and the consent a service that sends one owes."""

    def sender(self, *, declared: bool = True) -> ExtensionPlayers:
        ui = ExtensionUI("sample", allowed=True)
        ui.account(read=lambda player_id: {}, sends_picture=declared)
        return ExtensionPlayers("sample", (), self.store, ui)

    def given(self, player_id: str) -> None:
        players.get_roster().set_picture(player_id, "p.png", pictures.png(40, 40))

    def test_it_answers_the_file_while_share_is_on(self) -> None:
        self.given(self.owner)
        players.get_roster().set_sharing(self.owner, "sample", True)

        found = self.sender().picture(self.owner)

        self.assertEqual(found, players.get_roster().pictures_dir / f"{self.owner}.webp")

    def test_share_off_answers_none(self) -> None:
        self.given(self.owner)

        self.assertIsNone(self.sender().picture(self.owner))

    def test_an_extension_that_did_not_declare_it_answers_none(self) -> None:
        self.given(self.owner)
        players.get_roster().set_sharing(self.owner, "sample", True)

        self.assertIsNone(self.sender(declared=False).picture(self.owner))
        self.assertIsNone(ExtensionPlayers("sample", (), self.store).picture(self.owner))

    def test_a_guest_answers_none(self) -> None:
        guest = players.get_roster().add_guest("GST")
        players.get_roster().set_sharing(guest.player_id, "sample", True)

        self.assertIsNone(self.sender().picture(guest.player_id))

    def test_a_player_with_no_picture_answers_none(self) -> None:
        players.get_roster().set_sharing(self.owner, "sample", True)

        self.assertIsNone(self.sender().picture(self.owner))

    def test_the_consent_names_the_picture_where_it_is_sent(self) -> None:
        self.assertEqual(self.account(self.owner)["consent"], [])

        sample = self.registry.get("sample")
        assert sample is not None
        sample.account["sends_picture"] = True

        self.assertEqual(self.account(self.owner)["consent"],
                         [t("extension.account.sends_picture")])


class SecretsNeverLeave(AccountsCase):
    """Accepted on a write, answered as set or not set, by every route."""

    def test_no_account_route_answers_with_a_secret(self) -> None:
        answers = [
            self.fill(self.owner, handle="own-handle", token=TOKEN),
            self.account(self.owner),
            self.ok(self.client.get(f"/players/{self.owner}/accounts")),
            self.ok(self.client.put(f"/players/{self.owner}/accounts/sample/share",
                                    json={"share": True})),
            self.ok(self.client.post(f"/players/{self.owner}/accounts/sample/acts/ping")),
            self.ok(self.client.get(f"/ext/sample/accounts/{self.owner}")),
            self.ok(self.client.put(f"/ext/sample/accounts/{self.owner}",
                                    json={"values": {}})),
        ]

        self.assertEqual([one for answer in answers for one in self.secrets_answered(answer)],
                         [])

    def test_a_secret_reads_back_as_set(self) -> None:
        self.fill(self.owner, token=TOKEN)

        token = next(one for one in self.account(self.owner)["fields"]
                     if one["key"] == "token")

        self.assertEqual((token["set"], "value" in token), (True, False))

    def test_a_secret_setting_is_never_answered_either(self) -> None:
        written = self.ok(self.client.put("/ext/sample/settings",
                                          json={"values": {"token": TOKEN}}))
        read = self.ok(self.client.get("/ext/sample/settings"))

        self.assertEqual(self.secrets_answered([written, read]), [])
        self.assertEqual(self.store.settings("sample")["token"], TOKEN)
        self.assertEqual(next(one for one in read["fields"] if one["key"] == "token")["set"],
                         True)


def _read(player_id: str) -> dict:
    return {}


def _read_card(card: Any) -> dict:
    return {}


class TheDeclaration(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = ExtensionUI("site", allowed=True)

    def test_an_account_is_recorded_as_data(self) -> None:
        self.ui.account(read=_read, read_card=_read_card, cards=["site_card"])

        self.assertEqual(self.ui.account_offered, {"base": "/accounts", "label": "",
                                                   "cards": ["site_card"], "marker": "site",
                                                   "check": "", "consent": [],
                                                   "sends_picture": False})

    def test_the_functions_are_kept_for_core_to_call(self) -> None:
        def ping(player_id: str) -> None:
            return None

        self.ui.account(read=_read, acts={"ping": ping})

        kept = self.ui.account_functions
        assert kept is not None
        self.assertEqual((kept.read, kept.write, kept.acts), (_read, None, {"ping": ping}))

    def test_sending_a_picture_is_declared_not_assumed(self) -> None:
        self.ui.account(read=_read, sends_picture=True)

        self.assertIs(self.ui.account_offered["sends_picture"], True)

    def test_a_function_that_says_whether_a_name_is_free_gives_it_a_check_route(self) -> None:
        self.ui.account(read=_read, available=lambda candidate: True,
                        consent=["their id", "what they play"])

        self.assertEqual((self.ui.account_offered["check"],
                          self.ui.account_offered["consent"]),
                         ("/available", ["their id", "what they play"]))

    def test_an_account_needs_ui_mount(self) -> None:
        with self.assertRaises(ContractError):
            ExtensionUI("site", allowed=False).account(read=_read)

    def test_a_player_holds_one_account_with_an_extension(self) -> None:
        self.ui.account(read=_read)

        with self.assertRaises(ContractError):
            self.ui.account(read=_read)

    def test_an_account_needs_a_function_that_reads_it(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.account(read="/accounts")  # type: ignore[arg-type]

    def test_a_marker_that_is_not_a_plain_name_is_refused(self) -> None:
        """It becomes part of a comment and two element ids in the card."""
        with self.assertRaises(ContractError):
            self.ui.account(read=_read, marker="x--><script")

    def test_a_card_with_no_kind_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.account(read=_read, read_card=_read_card, cards=[""])

    def test_cards_are_read_only_where_a_function_reads_them(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.account(read=_read, cards=["site_card"])

    def test_an_act_that_is_not_a_function_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.account(read=_read, acts={"ping": "/ping"})  # type: ignore[dict-item]


class WhatAnExtensionSees(AccountsCase):
    def players_of(self, *scopes: str) -> ExtensionPlayers:
        return ExtensionPlayers("sample", scopes, self.store)

    def test_the_roster_needs_players_read(self) -> None:
        with self.assertRaises(ContractError):
            self.players_of().roster()

    def test_the_roster_is_the_rows_players_changed_carries(self) -> None:
        guest = players.get_roster().add_guest("ABC").player_id
        seen = self.players_of("players:read")

        self.assertEqual(seen.roster(), players.get_roster().state()["players"])
        self.assertEqual([one["id"] for one in seen.up()], [guest])
        got = seen.get(guest)
        assert got is not None
        self.assertEqual(got["initials"], "ABC")

    def test_a_player_s_record_of_a_game_needs_players_read(self) -> None:
        with self.assertRaises(ContractError):
            self.players_of().record(self.owner, "g1")

    def test_a_player_s_record_of_a_game_is_their_own(self) -> None:
        records = self.records()
        kept = players.get_roster().add_player("Jordan", "ABC")
        records.count_start(kept, "g1", "2026-09-28T20:00:00Z")
        records.set_rating(kept, "g1", 4)

        found = self.players_of("players:read").record(kept.player_id, "g1")

        assert found is not None
        self.assertEqual((found["play_count"], found["rating"], found["last_played"]),
                         (1, 4, "2026-09-28T20:00:00Z"))
        self.assertEqual(found, records.shown(kept, "g1"))

    def test_the_owner_s_record_is_the_library_s_and_nobody_s_is_nothing(self) -> None:
        self.records()
        seen = self.players_of("players:read")

        self.assertIsNone(seen.record(self.owner, "g1"))
        self.assertIsNone(seen.record("Nobody0000", "g1"))

    def records(self) -> player_records.PlayerRecords:
        player_records.reset_for_tests(self.root / "player_records")
        self.addCleanup(player_records.reset_for_tests)
        return player_records.get_records()

    def test_its_own_accounts_need_nothing_declared(self) -> None:
        seen = self.players_of()
        seen.set_account(self.owner, {"handle": "h"})

        self.assertEqual((seen.account(self.owner), seen.holders(), seen.sharing(self.owner)),
                         ({"handle": "h"}, [self.owner], False))

    def test_an_account_for_somebody_nobody_has_is_refused(self) -> None:
        with self.assertRaises(service_errors.NotFoundError):
            self.players_of().set_account("Nobody0000", {"handle": "h"})


class TheStore(AccountsCase):
    def test_accounts_is_not_a_name_a_setting_can_take(self) -> None:
        self.store.set_account("sample", self.owner, {"handle": "h"})
        with self.assertLogs("vpinfe.common.extensions.store", level="ERROR"):
            self.store.set_setting("sample", "accounts", "flattened")

        self.assertEqual(self.store.accounts("sample"), {self.owner: {"handle": "h"}})

    def test_forgetting_a_player_reaches_every_extension_holding_them(self) -> None:
        for name in ("sample", "bystander"):
            self.store.set_account(name, self.owner, {"handle": "h"})
        self.store.set_account("sample", "Other00000", {"handle": "o"})

        self.assertEqual(self.store.forget_holder(self.owner), ["bystander", "sample"])
        self.assertEqual((self.store.accounts("sample"), self.store.accounts("bystander")),
                         ({"Other00000": {"handle": "o"}}, {}))


if __name__ == "__main__":
    unittest.main()
