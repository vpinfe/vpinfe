"""A picture a source has for a game: taken from a tile, or set aside in the pane."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any, cast
from unittest import mock

from nicegui import background_tasks, core, events, run, ui

from common.i18n import t
from common.online import game_offers, sources, vps_marks
from httpapi import models
from manager.kit.data import Library
from manager.library import (
    discovery_get,
    discovery_list,
    discovery_marks,
    discovery_pictures,
)
from manager.library.discovery_kinds import Kind, Scene

_PAGE = ui.element()

CHANGE = {"id": "E1:wheel", "game_id": "g1", "vps_id": "E1", "name": "Alpha",
          "kind": "wheel", "source": "vpinmediadb", "size": "", "md5": "new-md5"}
GAME = {"game_id": "g1", "vps_id": "E1", "name": "Alpha",
        "take": {"kind": "wheel", "source": "vpinmediadb", "size": ""}}


def _kind(change: dict[str, Any] | None = None) -> Kind:
    return Kind(key="wheel", word="Wheel", media="wheel", change=change or {})


class NewerTests(unittest.TestCase):
    def test_the_offer_with_the_hash_of_the_change_is_the_newer_picture(self) -> None:
        self.assertTrue(discovery_pictures.newer(_kind(CHANGE), {"md5": "new-md5"}))

    def test_another_offer_is_not(self) -> None:
        self.assertFalse(discovery_pictures.newer(_kind(CHANGE), {"md5": "other"}))
        self.assertFalse(discovery_pictures.newer(_kind(CHANGE), {}))

    def test_a_slot_nothing_replaced_has_none(self) -> None:
        self.assertFalse(discovery_pictures.newer(_kind(), {"md5": "new-md5"}))
        self.assertFalse(discovery_pictures.newer(_kind(), {"md5": ""}))


class TakeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        patcher = mock.patch.object(run, "io_bound", inline)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.client = mock.Mock()
        self.library = Library(self.client)
        self.after = mock.AsyncMock()

    def test_a_game_with_nothing_to_take_has_no_verb(self) -> None:
        self.assertEqual(discovery_pictures.take_verb(self.library, {**GAME, "take": None},
                                                      self.after), [])

    def test_a_game_with_a_file_to_take_has_the_verb(self) -> None:
        [verb] = discovery_pictures.take_verb(self.library, GAME, self.after)

        self.assertEqual(verb.label, "Take")

    async def test_the_take_button_keeps_its_press_from_opening_the_game(self) -> None:
        with _PAGE:
            body = ui.column()
            with body:
                discovery_pictures.take_button(self.library, GAME, self.after)
        [button] = [one for one in body.descendants() if isinstance(one, ui.button)]
        wrapper = cast(Any, button.parent_slot).parent

        types = {one.type for one in wrapper._event_listeners.values()}

        self.assertEqual(types, {"click.stop", "keydown.enter.stop"})

    async def test_a_game_with_nothing_to_take_draws_no_button(self) -> None:
        with _PAGE:
            body = ui.column()
            with body:
                discovery_pictures.take_button(self.library, {**GAME, "take": None}, self.after)

        self.assertEqual([one for one in body.descendants() if isinstance(one, ui.button)], [])

    async def test_taking_fetches_the_file_the_tile_names_and_draws_again(self) -> None:
        with _PAGE, mock.patch.object(ui, "notify") as notified:
            await discovery_pictures.take(self.library, GAME, self.after)

        self.client.fetch_media.assert_called_once_with("g1", "", "wheel", "vpinmediadb",
                                                        "E1", "")
        self.after.assert_awaited_once()
        self.assertEqual(notified.call_args.kwargs["type"], "positive")

    async def test_a_take_that_fails_says_why_and_draws_nothing_again(self) -> None:
        self.client.fetch_media.side_effect = RuntimeError("no connection")

        with _PAGE, mock.patch.object(ui, "notify") as notified:
            await discovery_pictures.take(self.library, GAME, self.after)

        self.after.assert_not_awaited()
        self.assertEqual(notified.call_args.kwargs["type"], "negative")


class SetAsideTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    async def _drawn(self) -> tuple[ui.element, mock.AsyncMock]:
        applied = mock.AsyncMock()
        context = {"library": mock.Mock(), "state": {}}
        scene = cast(Scene, SimpleNamespace(title="Alpha"))
        with _PAGE:
            body = ui.column()
            with body, mock.patch.object(discovery_marks, "apply", applied), \
                    mock.patch.object(discovery_get, "redraw", mock.AsyncMock()):
                discovery_pictures.set_aside(context, scene, _kind(CHANGE))
        self.applied = applied
        return body, applied

    @staticmethod
    async def _press(button: ui.button) -> None:
        started: list[Any] = []
        with mock.patch.object(background_tasks, "create_or_defer",
                               lambda awaitable, **_: started.append(awaitable)):
            for listener in button._event_listeners.values():
                if listener.type == "click" and listener.handler is not None:
                    listener.handler(events.ClickEventArguments(sender=button,
                                                                client=button.client))
        await asyncio.gather(*started)

    def _button(self, body: ui.element, label: str) -> ui.button:
        return next(one for one in body.descendants()
                    if isinstance(one, ui.button) and one.text == label)

    async def test_both_ways_to_leave_it_alone_are_offered(self) -> None:
        body, _ = await self._drawn()

        labels = [one.text for one in body.descendants() if isinstance(one, ui.button)]

        self.assertEqual(labels, ["Keep Mine", "Ignore Updates"])

    async def test_each_hint_is_one_sentence_without_a_full_stop(self) -> None:
        for key in ("manager.discovery.picture.keep.help", "manager.discovery.picture.ignore.help"):
            said = t(key, kind="wheel")

            self.assertFalse(said.endswith("."), said)
            self.assertNotIn(". ", said)

    async def test_keep_mine_leaves_this_version_of_the_picture_alone(self) -> None:
        body, applied = await self._drawn()

        await self._press(self._button(body, "Keep Mine"))

        [call] = applied.await_args_list
        [subject] = call.args[2]
        self.assertEqual((call.args[1], subject.scope, subject.id, subject.detail),
                         (discovery_marks.IGNORED, vps_marks.VERSION, "E1:wheel", "new-md5"))

    async def test_ignore_updates_leaves_every_version_of_the_picture_alone(self) -> None:
        body, applied = await self._drawn()

        await self._press(self._button(body, "Ignore Updates"))

        [call] = applied.await_args_list
        [subject] = call.args[2]
        self.assertEqual((call.args[1], subject.scope, subject.id, subject.detail),
                         (discovery_marks.IGNORED, vps_marks.UPDATES, "E1:wheel", ""))


class NewerTileOnThePaneTests(unittest.IsolatedAsyncioTestCase):
    """The offers reach the pane the way the API sends them, so a hash the wire drops would
    leave the tile without its buttons."""

    async def asyncSetUp(self) -> None:
        was = core.loop
        core.loop = asyncio.get_running_loop()
        self.addCleanup(setattr, core, "loop", was)

    async def _offers(self) -> list[dict[str, Any]]:
        source = sources.Source(id="vpinmediadb", name="VPinMediaDB",
                                kinds=frozenset({"wheel"}))
        found = [game_offers.SourceOffers(source, [game_offers.KindOffers("wheel", offers=[
            sources.Offer("vpinmediadb", "Alpha", "https://example.test/old.png", "wheel",
                          md5="old-md5"),
            sources.Offer("vpinmediadb", "Alpha", "https://example.test/new.png", "wheel",
                          md5="new-md5")])])]
        with mock.patch.object(game_offers, "for_game", return_value=("E1", found)):
            wire = models.GameOffers.model_validate(
                game_offers.wire_for_game("g1", ["wheel"])).model_dump()
        plan = cast(Any, SimpleNamespace(failed=None, asked={"vpinmediadb": {}},
                                         kinds=["wheel"]))
        by_kind, _, _ = await discovery_get.offers_for(mock.Mock(), "E1", plan, wire)
        return by_kind["wheel"]

    async def test_only_the_offer_that_replaced_the_picture_has_keep_mine_and_ignore(self) -> None:
        kind = _kind(CHANGE)
        kind.offers = await self._offers()
        scene = cast(Scene, SimpleNamespace(
            game_id="g1", title="Alpha", tray=discovery_get.Tray(),
            sources={"vpinmediadb": {"name": "VPinMediaDB"}}))
        with _PAGE:
            body = ui.column()
            with body:
                discovery_list._offers({"library": mock.Mock(), "state": {}}, scene, kind)

        labels = [one.text for one in body.descendants() if isinstance(one, ui.button)]

        self.assertEqual(labels.count("Keep Mine"), 1)
        self.assertEqual(labels.count("Ignore Updates"), 1)

    async def test_the_route_for_one_kind_sends_the_hash_too(self) -> None:
        from starlette.testclient import TestClient

        import httpapi

        found = [sources.Offer("vpinmediadb", "Alpha", "https://example.test/new.png", "wheel",
                               md5="new-md5")]
        with mock.patch.object(sources, "offers", return_value=found), \
                mock.patch.object(sources, "enabled_ids", return_value=["vpinmediadb"]):
            body = TestClient(httpapi.create_api_app(), raise_server_exceptions=False) \
                .get("/media-sources/offers", params={"vps_id": "E1", "kind": "wheel"}).json()

        self.assertEqual(body["offers"][0]["md5"], "new-md5")


if __name__ == "__main__":
    unittest.main()
