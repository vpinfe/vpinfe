"""Discovery with files an extension's source can get: matched by VPS file id, handed over as
one batch, and drawn from the source's own state from then on."""

from __future__ import annotations

import dataclasses
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from common.i18n import t
from manager.library import discovery_ext, discovery_get
from manager.library.discovery_get import Offered, Pick, Tray
from tests.support.game_offers import answer_from

_PAGE = ui.element()

ROW = {"id": "v1", "name": "Attack from Mars"}


def _source(key: str = "files", kinds: tuple[str, ...] = ("table", "wheel")) -> dict[str, Any]:
    return {"id": f"ext.{key}", "extension": "ext", "key": key, "name": "Example Files",
            "kinds": list(kinds)}


def _row(key: str, file_id: str = "f1", **over: Any) -> dict[str, Any]:
    return {"key": key, "name": key, "vps_file_id": file_id, "current": False, **over}


def _offered(key: str = "a", file_id: str = "f1", kind: str = "table", **over: Any) -> Offered:
    row = _row(key, file_id, **over)
    source = _source()
    pick = Pick(discovery_get.key_of(source["id"], kind, key), kind, source["id"], row=key)
    return Offered("ext", "files", source["id"], "Example Files", kind, row, pick)


class Matching(unittest.TestCase):
    def test_a_file_vps_lists_is_the_kind_the_source_knows_it_by(self) -> None:
        self.assertEqual("table", discovery_ext.kind_for(_source(), "tableFiles"))

    def test_a_backglass_file_is_asked_for_by_its_own_name_not_the_picture_s(self) -> None:
        self.assertEqual("backglass_file", discovery_ext.kind_for(
            _source(kinds=("backglass", "backglass_file")), "b2sFiles"))
        self.assertEqual("", discovery_ext.kind_for(_source(kinds=("backglass",)), "b2sFiles"))

    def test_a_source_that_holds_no_such_kind_has_no_name_for_it(self) -> None:
        self.assertEqual("", discovery_ext.kind_for(_source(kinds=("wheel",)), "tableFiles"))

    def test_a_size_is_said_in_the_unit_it_is_read_in(self) -> None:
        self.assertEqual(t("manager.discovery.size.gb", count="1.5"),
                         discovery_ext.size_text(3 << 29))
        self.assertEqual(t("manager.discovery.size.mb", count="4.0"),
                         discovery_ext.size_text(4 << 20))
        self.assertEqual(t("manager.discovery.size.kb", count="0.5"),
                         discovery_ext.size_text(512))

    def test_a_size_the_source_did_not_give_is_not_said(self) -> None:
        self.assertEqual("", discovery_ext.size_text(None))

    def test_a_size_too_large_for_a_float_is_still_said(self) -> None:
        self.assertTrue(discovery_ext.size_text(10 ** 400))


class Asking(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(discovery_ext.offload, "io", inline))
        self.library = mock.Mock()
        self.library.online_sources.return_value = [_source("one"), _source("two")]
        self.answers: dict[str, Any] = {}
        self.library.source_offers.side_effect = self._offers
        answer_from(self.library, self.library.online_sources,
                    lambda source, *_: self._offers("ext", source["key"], *_))

    def _offers(self, _extension, key, *_asked) -> Any:
        answer = self.answers.get(key, {"rows": []})
        if isinstance(answer, Exception):
            raise answer
        return answer

    async def test_each_file_vps_lists_is_matched_by_its_file_id(self) -> None:
        self.answers = {"one": {"rows": [_row("a", "f1"), _row("b", "f2")]}}

        found = await discovery_ext.ask(self.library, ROW, "", ["tableFiles"])

        self.assertEqual(["f1", "f2"], [one.row["vps_file_id"] for one in found])
        self.assertEqual(["ext.one:table:a", "ext.one:table:b"], [one.pick.key for one in found])

    async def test_the_first_source_owns_a_file_two_of_them_offer(self) -> None:
        self.answers = {"one": {"rows": [_row("a", "f1")]}, "two": {"rows": [_row("z", "f1")]}}

        found = await discovery_ext.ask(self.library, ROW, "", ["tableFiles"])

        self.assertEqual(["ext.one:table:a"], [one.pick.key for one in found])

    async def test_a_row_with_no_file_id_is_not_discoverys_to_draw(self) -> None:
        self.answers = {"one": {"rows": [_row("a", "")]}}

        self.assertEqual([], await discovery_ext.ask(self.library, ROW, "", ["tableFiles"]))

    async def test_a_source_that_cannot_answer_leaves_the_rest_to_draw(self) -> None:
        self.answers = {"one": RuntimeError("down"), "two": {"rows": [_row("z", "f9")]}}

        found = await discovery_ext.ask(self.library, ROW, "", ["tableFiles"])

        self.assertEqual(["ext.two:table:z"], [one.pick.key for one in found])

    async def test_a_source_that_cannot_answer_keeps_what_it_offered(self) -> None:
        before = _offered("a", "f1")
        self.answers = {"one": RuntimeError("down"), "two": {"rows": [_row("z", "f9")]}}
        kept = dataclasses.replace(before, source_id="ext.one")

        found = await discovery_ext.ask(self.library, ROW, "", ["tableFiles"],
                                        keep={kept.pick.key: kept})

        self.assertEqual([kept, "ext.two:table:z"],
                         [one if one is kept else one.pick.key for one in found])

    async def test_a_game_outside_the_library_is_asked_by_vps_id_alone(self) -> None:
        self.answers = {"one": {"rows": []}}

        await discovery_ext.ask(self.library, ROW, "", ["tableFiles"])

        self.assertEqual(("ext", "one", "table", "", "v1", "Attack from Mars"),
                         self.library.source_offers.call_args_list[0].args)


class Adopting(unittest.TestCase):
    def test_the_offers_become_what_the_pane_draws_from(self) -> None:
        context: dict[str, Any] = {}
        tray = Tray()
        one = _offered("a", "f1")

        discovery_ext.adopt(context, tray, [one])

        self.assertIs(one, context["hosted"]["f1"])
        self.assertIs(one, tray.offered[one.pick.key])

    def test_a_media_move_is_left_alone_by_a_new_set_of_offers(self) -> None:
        tray = Tray()
        tray.moves["vpinmediadb:wheel:4K"] = discovery_get.Move(discovery_get.FAILED, "down")

        discovery_ext.adopt({}, tray, [_offered("a", "f1")])

        self.assertIn("vpinmediadb:wheel:4K", tray.moves)


class Handing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(discovery_get.offload, "io", inline))
        self.library = mock.Mock()
        self.library.source_take_many.return_value = {"message": "Getting them"}
        self.context: dict[str, Any] = {"library": self.library, "discovery": ROW, "state": {}}
        self.tray = Tray()
        self.one, self.two = _offered("a", "f1"), _offered("b", "f2", kind="wheel")
        self.tray.offered = {each.pick.key: each for each in (self.one, self.two)}

    async def test_what_a_source_was_asked_for_goes_in_one_call(self) -> None:
        other = _offered("c", "f3")
        self.tray.offered[other.pick.key] = other
        for each in (self.one, self.two, other):
            self.tray.move(each.pick.key, discovery_get.QUEUED)

        with _PAGE:
            await discovery_get.hand_over(self.context, self.tray, "g1",
                                          [self.one.pick, self.two.pick, other.pick])

        self.library.source_take_many.assert_called_once_with(
            "ext", "files",
            [{"kind": "table", "key": "a"}, {"kind": "wheel", "key": "b"},
             {"kind": "table", "key": "c"}], "g1", "v1", "Attack from Mars")
        self.assertEqual({each.pick.key for each in (self.one, self.two, other)},
                         set(self.tray.moves))

    async def test_a_source_that_refuses_fails_its_files(self) -> None:
        self.library.source_take_many.side_effect = RuntimeError("no room")
        self.tray.move(self.one.pick.key, discovery_get.QUEUED)

        with _PAGE:
            await discovery_get.hand_over(self.context, self.tray, "g1", [self.one.pick])

        self.assertEqual(discovery_get.FAILED, self.tray.moves[self.one.pick.key].state)
        self.assertEqual("no room", self.tray.moves[self.one.pick.key].reason)

    async def test_a_file_the_source_no_longer_offers_fails_without_a_call(self) -> None:
        del self.tray.offered[self.one.pick.key]

        with _PAGE:
            await discovery_get.hand_over(self.context, self.tray, "g1", [self.one.pick])

        self.assertEqual(discovery_get.FAILED, self.tray.moves[self.one.pick.key].state)
        self.library.source_take_many.assert_not_called()

    async def test_the_pane_asks_again_once_the_files_are_handed_over(self) -> None:
        refreshed = mock.AsyncMock()
        self.context["refresh_hosted"] = refreshed

        with _PAGE:
            await discovery_get.hand_over(self.context, self.tray, "g1", [self.one.pick])

        refreshed.assert_awaited_once()


class Refreshing(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(discovery_ext.offload, "io", inline))
        self.redraw = mock.AsyncMock()
        self.enterContext(mock.patch.object(discovery_get, "redraw", self.redraw))
        self.library = mock.Mock()
        self.library.online_sources.return_value = [_source()]
        self.context: dict[str, Any] = {"library": self.library, "discovery": ROW, "state": {}}
        self.tray = Tray()
        self.answer: dict[str, Any] = {}
        self.library.source_offers.side_effect = lambda *_args: self.answer
        answer_from(self.library, self.library.online_sources,
                    lambda *_asked: self.library.source_offers(*_asked))

    async def _begin(self, *rows: dict[str, Any]) -> None:
        self.answer = {"rows": list(rows)}
        discovery_ext.adopt(self.context, self.tray,
                            await discovery_ext.ask(self.library, ROW, "g1", ["tableFiles"]))

    async def test_a_row_whose_state_moved_is_repainted_where_it_stands(self) -> None:
        await self._begin(_row("a", "f1", state={"text": "Queued", "level": "unknown"}))
        key = next(iter(self.tray.offered))
        painted: list[str] = []
        with _PAGE:
            holder = ui.element()
            self.tray.paint(key, holder, lambda: painted.append("row"))
        painted.clear()
        self.answer = {"rows": [_row("a", "f1", state={"text": "In", "level": "on"})]}

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"])

        self.assertEqual(["row"], painted)
        self.redraw.assert_not_awaited()
        self.assertEqual("on", self.tray.offered[key].level)

    async def test_a_file_that_appeared_redraws_the_pane(self) -> None:
        await self._begin(_row("a", "f1"))
        self.answer = {"rows": [_row("a", "f1"), _row("b", "f2")]}

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"])

        self.redraw.assert_awaited_once()

    async def test_a_file_that_came_in_redraws_the_pane(self) -> None:
        await self._begin(_row("a", "f1"))
        self.answer = {"rows": [_row("a", "f1", current=True)]}

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"])

        self.redraw.assert_awaited_once()

    async def test_a_source_that_fails_a_poll_keeps_its_rows(self) -> None:
        await self._begin(_row("a", "f1"))
        key = next(iter(self.tray.offered))
        self.library.source_offers.side_effect = RuntimeError("slow")

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"])

        self.redraw.assert_not_awaited()
        self.assertIn(key, self.tray.offered)

    async def test_a_pane_that_draws_no_rows_is_not_redrawn_for_one_that_appeared(self) -> None:
        await self._begin(_row("a", "f1"))
        self.answer = {"rows": [_row("a", "f1"), _row("b", "f2")]}

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"],
                                        draws_rows=False)

        self.redraw.assert_not_awaited()
        self.assertEqual(2, len(self.tray.offered))

    async def test_nothing_moved_draws_nothing(self) -> None:
        await self._begin(_row("a", "f1"))

        with _PAGE:
            await discovery_ext.refresh(self.context, self.tray, "g1", ["tableFiles"])

        self.redraw.assert_not_awaited()


class Settling(unittest.IsolatedAsyncioTestCase):
    """A file that landed repaints the row and the open section where they stand."""

    def setUp(self) -> None:
        self.redraw = mock.AsyncMock()
        self.enterContext(mock.patch.object(discovery_get, "redraw", self.redraw))

    async def test_a_page_that_settles_in_place_is_not_redrawn(self) -> None:
        inplace = mock.AsyncMock(return_value=True)

        await discovery_get.settle({"settle_discovery": inplace}, "e1")

        inplace.assert_awaited_once_with("e1")
        self.redraw.assert_not_awaited()

    async def test_a_page_that_cannot_settle_in_place_is_redrawn(self) -> None:
        inplace = mock.AsyncMock(return_value=False)
        state = {"settle_discovery": inplace}

        await discovery_get.settle(state, "e1")

        self.redraw.assert_awaited_once_with(state)

    async def test_a_page_with_no_settler_is_redrawn(self) -> None:
        state: dict[str, Any] = {}

        await discovery_get.settle(state, "e1")

        self.redraw.assert_awaited_once_with(state)

    async def test_a_page_that_was_left_is_not_redrawn(self) -> None:
        inplace = mock.AsyncMock(side_effect=RuntimeError("deleted"))

        await discovery_get.settle({"settle_discovery": inplace}, "e1")

        self.redraw.assert_not_awaited()


class Painting(unittest.TestCase):
    def test_a_key_is_painted_while_a_holder_under_it_is_alive(self) -> None:
        tray = Tray()
        self.assertFalse(tray.painted(discovery_get.BODY))
        with _PAGE:
            holder = ui.element()
            tray.paint(discovery_get.BODY, holder, lambda: None)

            self.assertTrue(tray.painted(discovery_get.BODY))
            self.assertFalse(tray.painted(discovery_get.RAIL))

            holder.delete()

            self.assertFalse(tray.painted(discovery_get.BODY))


class Stopping(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        async def inline(callback, *args):
            return callback(*args)

        self.enterContext(mock.patch.object(discovery_ext.offload, "io", inline))
        self.enterContext(mock.patch.object(discovery_ext, "refresh", mock.AsyncMock()))
        self.library = mock.Mock()
        self.context: dict[str, Any] = {"library": self.library, "discovery": ROW, "state": {}}

    async def test_each_source_with_a_row_under_way_is_told_to_stop_once(self) -> None:
        tray = Tray()
        one = _offered("a", "f1", state={"text": "Downloading", "level": "unknown"})
        two = _offered("b", "f2", state={"text": "Downloading", "level": "unknown"})
        idle = _offered("c", "f3", state={"text": "In", "level": "on"})
        tray.offered = {each.pick.key: each for each in (one, two, idle)}
        tray.stopping = True

        with _PAGE:
            await discovery_ext.stopper(self.context, tray, lambda: "g1", ["tableFiles"])()

        self.library.source_act.assert_called_once_with(
            "ext", "files", "", "", "stop", "g1", "v1", "Attack from Mars")
        self.assertFalse(tray.stopping)

    async def test_a_batch_still_running_is_ended_by_the_flag_the_stop_set(self) -> None:
        tray = Tray()
        tray.move("k", discovery_get.DOWNLOADING)
        tray.stopping = True

        with _PAGE:
            await discovery_ext.stopper(self.context, tray, lambda: "g1", ["tableFiles"])()

        self.assertTrue(tray.stopping)

    async def test_a_button_on_a_row_presses_it_as_the_row_is_now(self) -> None:
        tray = Tray()
        one = _offered("a", "f1", acts=[{"key": "retry", "label": "Try again"}])
        tray.offered = {one.pick.key: one}
        self.library.source_act.return_value = {}

        with (mock.patch.object(discovery_ext, "answered", mock.AsyncMock()),
              _PAGE):
            handler = discovery_ext.acts_for(self.context, tray, lambda: "g1", ["tableFiles"],
                                             one.pick.key)
            await handler("retry")()

        self.library.source_act.assert_called_once_with(
            "ext", "files", "table", "a", "retry", "g1", "v1", "Attack from Mars")


class Counting(unittest.TestCase):
    def test_downloading_counts_our_files_and_the_rows_a_source_is_getting(self) -> None:
        tray = Tray()
        tray.moves = {"a": discovery_get.Move(discovery_get.QUEUED),
                      "b": discovery_get.Move(discovery_get.FAILED, "down")}
        going = _offered("c", "f3", state={"text": "x", "level": "unknown"})
        needs = _offered("d", "f4", state={"text": "x", "level": "warn"})
        tray.offered = {each.pick.key: each for each in (going, needs)}

        self.assertEqual(2, tray.downloading())


if __name__ == "__main__":
    unittest.main()
