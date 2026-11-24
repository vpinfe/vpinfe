"""Frontend › Players: what each row and the panel say, which acts a player is offered, and
where the page sits and is addressed."""

from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import requests
from nicegui import ui

from common.devices import install_identity
from common.i18n import t
from manager import page
from manager.frontend import players
from manager.kit import deeplink, grid, panel, thumbnails
from manager.kit.api import ApiClient
from manager.workbench import rail
from tests.support.clicks import press

OWNER = {"id": "p-own", "name": "", "initials": "OWN", "owner": True, "guest": False,
         "up": True, "shares_initials_with": []}


def _player(player_id: str, **more: Any) -> dict[str, Any]:
    return {"id": player_id, "name": "", "initials": "", "owner": False, "guest": False,
            "up": False, "shares_initials_with": [], **more}


class TheRows(unittest.TestCase):
    def test_a_household_of_one_shows_no_up(self) -> None:
        self.assertEqual([row["up"] for row in players.rows([OWNER])], [False])

    def test_up_is_marked_once_someone_else_plays_here(self) -> None:
        made = players.rows([OWNER, _player("p-jor", name="Jordan", initials="ABC")])
        self.assertEqual([row["up"] for row in made], [True, False])

    def test_chips_mark_the_owner_and_guests_and_nobody_else(self) -> None:
        made = players.rows([OWNER, _player("p-jor", name="Jordan"),
                             _player("p-vis", initials="XYZ", guest=True)])
        self.assertEqual([row["kind"] for row in made],
                         [players.OWNER, "", players.GUEST])

    def test_a_name_falls_back_to_initials_and_then_says_there_is_none(self) -> None:
        self.assertEqual([players.shown_name(one) for one in (
            _player("a", name=" Jordan "), _player("b", initials="XYZ"), _player("c"))],
            ["Jordan", "XYZ", t("manager.players.no_name")])

    def test_shared_initials_name_the_other_player_on_both_rows(self) -> None:
        kept = _player("p-jor", name="Jordan", initials="XYZ",
                       shares_initials_with=["p-vis"])
        guest = _player("p-vis", initials="XYZ", guest=True, up=True,
                        shares_initials_with=["p-jor"])
        made = players.rows([OWNER, kept, guest])
        self.assertEqual([row["shared"] for row in made], [
            "", t("manager.players.initials_clash", player="XYZ"),
            t("manager.players.initials_clash", player="Jordan")])

    def test_several_sharing_are_all_named(self) -> None:
        one = _player("a", initials="XYZ", shares_initials_with=["b", "c"])
        said = players.same_initials(one, [one, _player("b", name="Sam", initials="XYZ"),
                                           _player("c", initials="XYZ")])
        self.assertIn("Sam", said)
        self.assertIn("XYZ", said)

    def test_a_guest_row_counts_the_games_played_this_session(self) -> None:
        guest = _player("p-vis", initials="XYZ", guest=True, up=True)
        kept = _player("p-jor", name="Jordan")
        made = players.rows([OWNER, kept, guest], {"p-vis": 3, "p-jor": 5})
        self.assertEqual([row["session"] for row in made],
                         ["", "", t("manager.players.session_total", count=3)])

    def test_a_guest_with_no_games_yet_says_nothing(self) -> None:
        guest = _player("p-vis", initials="XYZ", guest=True)
        self.assertEqual(players.rows([OWNER, guest], {"p-vis": 0})[1]["session"], "")

    def test_games_played_adds_every_game_s_plays(self) -> None:
        self.assertEqual(players.games_played([{"play_count": 2}, {"play_count": 1},
                                               {"rating": 4}]), 3)


class ThePicture(unittest.TestCase):
    def test_a_row_addresses_the_picture_at_list_size_with_its_version(self) -> None:
        kept = _player("p-jor", name="Jordan", picture_version="abc123")
        made = players.rows([OWNER, kept])
        self.assertEqual([row["picture"] for row in made],
                         ["", "/api/v1/players/p-jor/picture?size=256&v=abc123"])

    def test_the_panel_asks_for_the_larger_size(self) -> None:
        kept = _player("p-jor", picture_version="abc123")
        self.assertEqual(players.picture_address(kept, 1024),
                         "/api/v1/players/p-jor/picture?size=1024&v=abc123")

    def test_an_id_that_needs_quoting_is_quoted(self) -> None:
        self.assertIn("/players/a%2Fb/picture",
                      players.picture_address(_player("a/b", picture_version="v1")))


class TheCircleInTheGrid(unittest.TestCase):
    """A row's circle is drawn at the height the lists are set to, as the other grids' art is."""

    def _look(self, **chosen: str) -> thumbnails.Look:
        with patch.object(thumbnails, "cfg_get",
                          side_effect=lambda _config, _section, key, *_: chosen.get(key, "")):
            return thumbnails.person_look()

    def test_it_takes_the_height_the_lists_are_set_to(self) -> None:
        sized = {height: grid.with_art(players.COLUMNS, self._look(thumbnail_height=height))
                 for height in ("small", "medium", "large")}

        self.assertEqual({height: grid.base_row_px(columns) for height, columns in sized.items()},
                         {"small": 56, "medium": 64, "large": 88})

    def test_it_is_a_circle_whatever_shape_the_lists_draw(self) -> None:
        for shape in ("auto", "square", "wide"):
            with self.subTest(shape=shape):
                self.assertEqual(self._look(thumbnail_shape=shape).shape, thumbnails.SQUARE)

    def test_it_keeps_its_ground_when_the_lists_are_unframed(self) -> None:
        with patch.object(thumbnails, "cfg_bool", return_value=False):
            self.assertTrue(thumbnails.person_look().framed)

    def test_a_blank_setting_reads_as_the_default_height(self) -> None:
        self.assertEqual(self._look(thumbnail_height="").height, thumbnails.SMALL)


class TheDetails(unittest.TestCase):
    def test_the_picture_comes_after_the_fields(self) -> None:
        roster = [OWNER, _player("p-jor", name="Jordan", initials="JOR")]
        context = {"library": Mock(), "player": roster[1], "roster": roster}

        holder = ui.card()

        async def inside() -> None:
            with holder:
                await players.details(context)

        with patch.object(panel, "facts") as facts:
            asyncio.run(inside())

        drawn = [label for label, _draw in facts.call_args.args[1] if label is not panel.FULL]
        self.assertEqual(drawn, [t("word.name"), t("manager.players.initials"),
                                 t("manager.players.up"), t("manager.players.picture")])


class WhatCanBeDone(unittest.TestCase):
    def acts(self, player: dict[str, Any]) -> list:
        return players.acts(object(), {}, [player], lambda: None)  # type: ignore[arg-type]

    def test_the_owner_s_remove_is_offered_and_refused_with_why(self) -> None:
        (verb,) = self.acts(OWNER)
        self.assertEqual((verb.label, verb.run, verb.danger, verb.hint),
                         (t("word.remove"), None, True,
                          t("error.players.owner_not_removable")))

    def test_a_kept_player_can_be_removed(self) -> None:
        (verb,) = self.acts(_player("p-jor", name="Jordan"))
        self.assertEqual(verb.label, t("word.remove"))
        self.assertIsNotNone(verb.run)

    def test_a_guest_signs_out(self) -> None:
        (verb,) = self.acts(_player("p-vis", guest=True))
        self.assertEqual((verb.label, verb.danger), (t("manager.players.sign_out"), True))

    def test_an_account_is_offered_the_verb_its_state_allows(self) -> None:
        async def after() -> None:
            return None

        accounts: list[dict[str, Any]] = [
            {"extension": "vpinplay", "label": "VPinPlay", "user_id": "u-1", "claimed": True},
            {"extension": "other", "label": "Other", "user_id": "u-2", "claimed": False},
            {"extension": "bare", "label": "Bare", "user_id": ""},
            {"extension": "broken", "label": "Broken", "user_id": "u-3", "error": "down"},
        ]
        made = players.acts(object(), {}, [_player("p-jor")], lambda: None,  # type: ignore[arg-type]
                            accounts={"p-jor": accounts}, after=after)
        self.assertEqual([(one.label, one.danger) for one in made], [
            (t("manager.players.menu_disconnect", service="VPinPlay"), True),
            (t("manager.players.menu_remove_id", service="Other"), True),
            (t("word.remove"), True)])

    def test_without_a_way_to_redraw_only_the_player_is_offered(self) -> None:
        made = players.acts(object(), {}, [_player("p-jor")], lambda: None,  # type: ignore[arg-type]
                            accounts={"p-jor": [{"extension": "vpinplay", "user_id": "u-1",
                                            "claimed": True}]})
        self.assertEqual([one.label for one in made], [t("word.remove")])

    def test_removing_says_what_goes_and_offers_the_card_where_there_is_one(self) -> None:
        self.assertEqual(players.removal_detail(None), [t("manager.players.remove_detail")])
        self.assertEqual(players.removal_detail({"extension": "vpinplay",
                                                 "label": "VPinPlay"}),
                         [t("manager.players.remove_detail"),
                          t("manager.players.remove_card_detail", service="VPinPlay")])


# Made at import, outside any task, where NiceGUI still hands out the script's own page.
_PAGE = ui.element()


class TheUpSwitch(unittest.IsolatedAsyncioTestCase):
    """Putting a player up writes it, then draws the switch where it stands."""

    def setUp(self) -> None:
        self.changes: list[Any] = []
        real = players.panel.switch

        def spy(value: Any, on_change: Any, **more: Any) -> Any:
            self.changes.append(on_change)
            return real(value, on_change, **more)

        self.enterContext(patch.object(players.panel, "switch", spy))

    @staticmethod
    def _library(roster: list[dict[str, Any]]) -> Mock:
        library = Mock()
        library.set_player_up.side_effect = lambda who, on: [
            {**one, "up": on} if one["id"] == who else one for one in roster]
        return library

    async def _drawn(self, library: Mock, player: dict[str, Any],
                     roster: list[dict[str, Any]]) -> tuple[dict[str, Any], ui.element]:
        context = {"library": library, "player": player, "roster": roster, "state": {},
                   "retitle": Mock(), "rebuild": AsyncMock()}
        with _PAGE:
            box = ui.column()
            with box:
                await players.details(context)
        return context, box

    @staticmethod
    def _switch(box: ui.element) -> ui.switch:
        return next(one for one in box.descendants() if isinstance(one, ui.switch))

    async def _flip(self, on: bool) -> None:
        await press(self.changes[-1], SimpleNamespace(value=on))

    async def test_a_flip_writes_it_and_does_not_rebuild_the_panel(self) -> None:
        guest = _player("p-jor", name="Jordan")
        roster = [OWNER, guest]
        library = self._library(roster)
        context, box = await self._drawn(library, guest, roster)
        before = self._switch(box)

        await self._flip(True)

        library.set_player_up.assert_called_once_with("p-jor", True)
        context["rebuild"].assert_not_awaited()
        self.assertTrue(guest["up"])
        self.assertIsNot(before, self._switch(box))
        self.assertTrue(self._switch(box).value)

    async def test_the_owner_alone_up_has_nowhere_to_go(self) -> None:
        owner = {**OWNER, "up": False}
        guest = _player("p-jor", name="Jordan", up=True)
        roster = [owner, guest]
        library = Mock()
        library.set_player_up.return_value = [{**owner, "up": True}, {**guest, "up": False}]
        _context, box = await self._drawn(library, owner, roster)
        self.assertTrue(self._switch(box).enabled)

        await self._flip(True)

        self.assertFalse(self._switch(box).enabled)

    async def test_a_flip_that_fails_puts_the_switch_back(self) -> None:
        guest = _player("p-jor", name="Jordan")
        roster = [OWNER, guest]
        library = self._library(roster)
        library.set_player_up.side_effect = RuntimeError("gone")
        _context, box = await self._drawn(library, guest, roster)
        clicked = self._switch(box)
        clicked.value = True

        with patch.object(players.ui, "notify") as notice:
            await self._flip(True)

        notice.assert_called_once()
        self.assertTrue(clicked.is_deleted)
        self.assertFalse(self._switch(box).value)


class TheInitialsClash(unittest.IsolatedAsyncioTestCase):
    """Changing initials draws the clash line where it stands, not the whole panel."""

    def setUp(self) -> None:
        self.fields: list[Any] = []
        real = players.panel.field

        def spy(value: Any, save: Any, **more: Any) -> Any:
            self.fields.append(save)
            return real(value, save, **more)

        self.enterContext(patch.object(players.panel, "field", spy))
        self.enterContext(patch.object(players.offload, "io", AsyncMock(
            side_effect=lambda call, *a, **kw: call(*a, **kw))))

    @staticmethod
    def _clashes(box: ui.element) -> list[str]:
        return [one.text for one in box.descendants()
                if isinstance(one, ui.label) and "manager-attention-line" in one.classes]

    async def test_a_new_clash_appears_and_a_cleared_one_goes_without_a_rebuild(self) -> None:
        guest = _player("p-jor", name="Jordan", initials="AAA")
        other = _player("p-vis", name="Sam", initials="BBB")
        roster = [OWNER, guest, other]
        library = Mock()
        library.change_player.return_value = {"initials": "BBB",
                                              "shares_initials_with": ["p-vis"]}
        context: dict[str, Any] = {"library": library, "player": guest, "roster": roster,
                                   "state": {}, "retitle": Mock(), "rebuild": AsyncMock()}
        with _PAGE:
            box = ui.column()
            with box:
                await players.details(context)
        self.assertEqual([], self._clashes(box))

        await press(self.fields[1], "BBB")

        self.assertEqual([t("manager.players.initials_clash", player="Sam")],
                         self._clashes(box))
        context["rebuild"].assert_not_awaited()

        library.change_player.return_value = {"initials": "CCC", "shares_initials_with": []}
        await press(self.fields[1], "CCC")

        self.assertEqual([], self._clashes(box))
        context["rebuild"].assert_not_awaited()


class AnAccountThatIsOnlyFields(unittest.TestCase):
    """An extension that keeps a few values per player and no id: each is drawn as a
    setting is, and saved to that player's account under its own key."""

    def _rows(self, library: Mock, form: Mock, fields: list[dict[str, Any]]):
        drawn: list[tuple[dict[str, Any], Any]] = []

        def control(_client: Any, field: dict[str, Any], save: Any) -> Any:
            drawn.append((field, save))
            return lambda: None

        self.account: dict[str, Any] = {"extension": "scores", "fields": fields}
        context = {"library": library, "player": _player("p-jor"), "state": {},
                   "rebuild": AsyncMock()}
        for target, name, replacement in (
                (players.ext_page, "control", control),
                (players, "on_page", lambda handler: handler),
                (players.offload, "io", AsyncMock(side_effect=lambda fn, *a: fn(*a)))):
            patcher = patch.object(target, name, replacement)
            patcher.start()
            self.addCleanup(patcher.stop)
        rows = players._field_rows(context, self.account, fields, form)
        return rows, drawn

    def test_each_field_is_a_row_under_its_label_with_its_help_after_it(self) -> None:
        rows, drawn = self._rows(Mock(), Mock(), [
            {"key": "handle", "label": "Handle", "help": "Shown on the board"},
            {"key": "key"}])

        self.assertEqual([one[0] for one in rows if isinstance(one, tuple) and len(one) == 2
                          and isinstance(one[0], str)], ["Handle", "key"])
        self.assertEqual([field["key"] for field, _ in drawn], ["handle", "key"])

    def test_saving_a_field_writes_it_to_that_players_account(self) -> None:
        library, form = Mock(), Mock()
        library.player_accounts.return_value = []
        _, drawn = self._rows(library, form, [{"key": "handle"}, {"key": "token"}])

        asyncio.run(drawn[1][1]("abc"))

        library.put_account.assert_called_once_with("p-jor", "scores", {"token": "abc"})

    def test_a_field_that_reads_back_as_written_leaves_the_panel_standing(self) -> None:
        library, form = Mock(), Mock()
        library.player_accounts.return_value = [{"extension": "scores", "fields": [
            {"key": "handle", "value": "old"}, {"key": "token", "value": "abc"}]}]
        _, drawn = self._rows(library, form, [{"key": "handle", "value": "old"},
                                              {"key": "token", "value": ""}])

        asyncio.run(drawn[1][1]("abc"))

        form.refill.assert_not_called()
        self.assertEqual("abc", self.account["fields"][1]["value"])

    def test_something_else_that_changed_draws_the_panel_again(self) -> None:
        library, form = Mock(), Mock()
        library.player_accounts.return_value = [{"extension": "scores", "waiting_count": 2,
                                                 "fields": [{"key": "token", "value": "abc"}]}]
        _, drawn = self._rows(library, form, [{"key": "token", "value": ""}])

        asyncio.run(drawn[0][1]("abc"))

        form.refill.assert_called_once()
        self.assertEqual(2, self.account["waiting_count"])

    def test_a_save_that_fails_puts_the_field_back_as_it_was(self) -> None:
        library, form = Mock(), Mock()
        library.put_account.side_effect = RuntimeError("gone")
        _, drawn = self._rows(library, form, [{"key": "token", "value": "old"}])

        with patch.object(players.ui, "notify") as notice:
            asyncio.run(drawn[0][1]("abc"))

        notice.assert_called_once()
        form.refill.assert_called_once()

    def test_a_secret_is_drawn_again_even_when_it_was_already_set(self) -> None:
        library, form = Mock(), Mock()
        library.player_accounts.return_value = [{"extension": "scores", "fields": [
            {"key": "token", "type": "secret", "set": True}]}]
        _, drawn = self._rows(library, form, [{"key": "token", "type": "secret",
                                               "set": True}])

        asyncio.run(drawn[0][1]("abc", under=True))

        form.refill.assert_called_once()

    def test_an_account_that_cannot_be_read_back_keeps_what_was_typed(self) -> None:
        library, form = Mock(), Mock()
        library.player_accounts.side_effect = RuntimeError("down")
        _, drawn = self._rows(library, form, [{"key": "handle", "value": "old"}])

        asyncio.run(drawn[0][1]("new", under=True))

        library.put_account.assert_called_once()
        form.refill.assert_not_called()

    def test_a_refusal_comes_back_for_the_box_to_show(self) -> None:
        library, form = Mock(), Mock()
        library.put_account.side_effect = RuntimeError("That handle is taken")
        _, drawn = self._rows(library, form, [{"key": "handle"}])

        said = asyncio.run(drawn[0][1]("x", under=True))

        self.assertIn("That handle is taken", said)
        form.refill.assert_not_called()


class ThePanel(unittest.TestCase):
    def test_the_header_says_what_the_player_is(self) -> None:
        self.assertEqual([players.kind_word(one) for one in (
            OWNER, _player("a"), _player("b", guest=True))],
            [t("manager.players.owner"), t("manager.players.player"),
             t("manager.players.guest")])

    def test_a_player_s_rail_lands_on_details(self) -> None:
        self.assertEqual(rail.chosen_section({}, "player"), "player_details")

    def test_the_owner_has_no_plays_section(self) -> None:
        plays = next(one for one in rail.sections_for("player")
                     if one.key == "player_plays")
        assert plays.shown is not None
        self.assertEqual([plays.shown({"player": one}) for one in (
            OWNER, _player("a"), _player("b", guest=True))], [False, True, True])

    def test_an_account_is_a_section_named_for_its_service(self) -> None:
        key, label, _build = players.account_section(
            {"extension": "vpinplay", "label": "VPinPlay"})
        self.assertEqual((key, label({})), ("player_account_vpinplay", "VPinPlay"))

    def test_share_says_what_the_extension_says_it_does(self) -> None:
        self.assertEqual(players.share_help({"extension": "vpinplay", "label": "VPinPlay",
                                             "share_help": "Makes it public"}),
                         "Makes it public")

    def test_share_says_core_s_words_where_the_extension_says_none(self) -> None:
        self.assertEqual(players.share_help({"extension": "site", "label": "Site"}),
                         t("manager.players.share.help", service="Site"))

    def test_a_card_is_saved_as_an_svg_named_for_the_service_and_the_user_id(self) -> None:
        self.assertEqual([players.card_filename(account) for account in (
            {"extension": "vpinplay", "user_id": "player-one"},
            {"extension": "vpinplay", "user_id": ""})],
            ["vpinplay-player-one.svg", "vpinplay-card.svg"])


class ThePlays(unittest.TestCase):
    def test_count_time_and_when_then_the_best_score(self) -> None:
        said = players.played_line({"play_count": 3, "play_time_seconds": 1500,
                                    "last_played": "2026-09-01T20:00:00+00:00",
                                    "best_score": {"score": 1234560, "text": "1,234,560",
                                                   "prefix": "", "suffix": ""}})
        self.assertTrue(said.startswith("3 plays · 25 min · "))
        self.assertTrue(said.endswith(" · Best 1,234,560"))

    def test_a_score_made_without_being_up_stands_alone(self) -> None:
        self.assertEqual(players.played_line({"play_count": 0, "best_score": {
            "score": 500, "text": "500", "prefix": "", "suffix": ""}}), "Best 500")

    def test_a_game_only_rated_is_not_a_play(self) -> None:
        self.assertEqual(players.played_line({"play_count": 0, "rating": 4,
                                              "best_score": None}), "")


class WhereItSits(unittest.TestCase):
    def test_players_leads_the_frontend(self) -> None:
        groups = dict(page.nav_for(install_identity.FEATURES))
        self.assertEqual([key for key, *_rest in groups[page.NAV_FRONTEND]][0], "players")

    def test_an_install_without_the_frontend_has_no_players(self) -> None:
        keys = [key for _parent, items in page.nav_for([install_identity.LIBRARY])
                for key, *_rest in items]
        self.assertNotIn("players", keys)

    def test_the_address_names_the_player_on_players_only(self) -> None:
        state = {"view": "players", "player": "p-jor", "launcher": "l-1"}
        self.assertEqual(deeplink.query(state), "view=players&player=p-jor")
        self.assertEqual(deeplink.query({**state, "view": "games"}), "view=games")

    def test_an_address_names_the_player_to_open(self) -> None:
        state: dict[str, Any] = {"view": "games"}
        deeplink.apply(state, {"view": "players", "player": " p-jor "},
                       views=["games", "players"], sections=[])
        self.assertEqual((state["view"], state["player"]), ("players", "p-jor"))

    def test_leaving_the_page_drops_the_player(self) -> None:
        state = {"view": "players", "player": "p-jor"}
        with patch.object(page.remembered, "put"):
            page.leave_for(state, "games")
        self.assertIsNone(state["player"])


class TheClient(unittest.TestCase):
    """Each call goes to the route that does it, with the id made safe for a path."""

    def asked(self, call: Any) -> tuple[str, str, Any]:
        seen: list[tuple[str, str, Any]] = []

        def answer(_session: Any, method: str, url: str, **kwargs: Any) -> Any:
            seen.append((method, url.split("/api/v1", 1)[1], kwargs.get("json")))
            response = requests.Response()
            response.status_code = 200
            response._content = json.dumps({"players": [], "accounts": [], "games": []}) \
                .encode()
            return response

        with patch.object(requests.Session, "request", answer):
            call(ApiClient("http://127.0.0.1:1"))
        return seen[0]

    def test_each_route(self) -> None:
        cases = [
            (lambda c: c.add_player("Jordan", "ABC"),
             ("POST", "/players", {"name": "Jordan", "initials": "ABC"})),
            (lambda c: c.add_guest("XYZ"), ("POST", "/players/guests", {"initials": "XYZ"})),
            (lambda c: c.add_guest_from_card("{}"),
             ("POST", "/players/guests/card", {"card": "{}"})),
            (lambda c: c.change_player("a/b", initials="XYZ"),
             ("PATCH", "/players/a%2Fb", {"initials": "XYZ"})),
            (lambda c: c.remove_player("p-1"), ("DELETE", "/players/p-1", None)),
            (lambda c: c.set_player_up("p-1", True), ("PUT", "/players/p-1/up", {"up": True})),
            (lambda c: c.put_account("p-1", "vpinplay", {"user_id": "u"}),
             ("PUT", "/players/p-1/accounts/vpinplay", {"values": {"user_id": "u"}})),
            (lambda c: c.put_share("p-1", "vpinplay", False),
             ("PUT", "/players/p-1/accounts/vpinplay/share", {"share": False})),
            (lambda c: c.use_card("p-1", "vpinplay", "{}"),
             ("POST", "/players/p-1/accounts/vpinplay/card", {"card": "{}"})),
            (lambda c: c.account_act("p-1", "vpinplay", "claim"),
             ("POST", "/players/p-1/accounts/vpinplay/acts/claim", {})),
            (lambda c: c.player_card("p-1", "vpinplay"),
             ("GET", "/players/p-1/accounts/vpinplay/card", None)),
            (lambda c: c.use_account_picture("p-1", "vpinplay"),
             ("POST", "/players/p-1/accounts/vpinplay/picture", {})),
            (lambda c: c.set_player_picture("a/b", "me.png", b"x"),
             ("PUT", "/players/a%2Fb/picture", None)),
            (lambda c: c.clear_player_picture("p-1"), ("DELETE", "/players/p-1/picture", None)),
            (lambda c: c.player_picture_bytes("p-1"), ("GET", "/players/p-1/picture", None)),
            (lambda c: c.account_available("vpinplay", "/available", "jordan"),
             ("GET", "/ext/vpinplay/available?candidate=jordan", None)),
        ]
        for call, wanted in cases:
            with self.subTest(wanted[1]):
                self.assertEqual(self.asked(call), wanted)


class ChoosingAUserId(unittest.TestCase):
    def test_the_id_field_is_left_as_typed(self) -> None:
        """The id is a handle, so a mobile keyboard may not capitalize or correct it."""
        holder = ui.card()
        fields: list[ui.input] = []
        real_field = players.frame.field

        def kept(*args: Any, **kwargs: Any) -> ui.input:
            fields.append(real_field(*args, **kwargs))
            return fields[-1]

        async def asked() -> None:
            account = {"extension": "vpinplay", "label": "VPinPlay", "check": "", "user_id": ""}
            context = {"library": Mock(), "rebuild": AsyncMock()}
            with holder:
                task = asyncio.ensure_future(players.choose_user_id(context, account))
                await asyncio.sleep(0)
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)

        with patch.object(players, "_of", return_value=_player("p-1")), \
                patch.object(players.frame, "field", kept), \
                patch.object(players.frame, "enter_presses"):
            asyncio.run(asked())

        (field,) = fields
        self.assertEqual({key: field.props.get(key) for key in (
            "spellcheck", "autocorrect", "autocapitalize", "autocomplete")},
            {"spellcheck": "false", "autocorrect": "off", "autocapitalize": "off",
             "autocomplete": "off"})


if __name__ == "__main__":
    unittest.main()
