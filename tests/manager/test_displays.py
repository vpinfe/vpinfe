"""Settings > Hardware > Displays: what the picture reads from the config and writes to it."""

from __future__ import annotations

import asyncio
import unittest
from collections.abc import Mapping
from typing import Any, cast
from unittest.mock import AsyncMock, Mock, patch

from screeninfo import Monitor

from common.i18n import t
from common.settings import config_service
from manager.kit import panel
from manager.settings import displays
from manager.settings import screens_picture as picture
from manager.settings.screens import Screen
from tests.support.clicks import press

SCHEMA = config_service.schema()["sections"]

BIG = Monitor(x=0, y=0, width=2560, height=1440, width_mm=597, height_mm=336,
              name="DP-1", is_primary=True)
TALL = Monitor(x=2560, y=0, width=1080, height=1920, width_mm=336, height_mm=597,
               name="HDMI-1", is_primary=False)
STRIP = Monitor(x=0, y=1440, width=1280, height=390, width_mm=300, height_mm=90,
                name="HDMI-2", is_primary=False)

FIRST = Screen(1, "", 2560, 1440, 0, 0)
SECOND = Screen(2, "", 1080, 1920, 2560, 0)


def _values(**windows: Any) -> dict[str, dict[str, Any]]:
    return {f"windows.{part}": settings for part, settings in windows.items()}


class StoredTests(unittest.TestCase):
    def test_what_is_stored_is_read(self) -> None:
        self.assertEqual(displays.stored(_values(backglass={"screen_id": 2}), SCHEMA,
                                         "windows.backglass", "screen_id"), 2)

    def test_what_is_not_stored_is_the_schema_s_default(self) -> None:
        self.assertEqual(displays.stored({}, SCHEMA, "windows.playfield", "screen_id"), "0")
        self.assertEqual(displays.stored({}, SCHEMA, "windows.backglass", "screen_id"), "")

    def test_a_setting_nobody_declares_is_none(self) -> None:
        self.assertIsNone(displays.stored({}, SCHEMA, "windows.apron", "screen_id"))


class NumberOfTests(unittest.TestCase):
    def test_the_first_stored_screen_is_the_one_a_person_counts_as_1(self) -> None:
        self.assertEqual(displays.number_of(0), 1)
        self.assertEqual(displays.number_of("2"), 3)

    def test_no_screen_is_no_number(self) -> None:
        for stored in ("", None, " ", "-1", "left"):
            with self.subTest(stored=stored):
                self.assertIsNone(displays.number_of(stored))


class PlacedTests(unittest.TestCase):
    def test_each_part_is_where_its_window_is_stored(self) -> None:
        held = displays.placed(_values(playfield={"screen_id": 1}, backglass={"screen_id": 0},
                                       scoreview={"screen_id": ""}, topper={"screen_id": 2}),
                               SCHEMA)

        self.assertEqual(held, {"topper": 3, "backglass": 1, "scoreview": None, "playfield": 2})

    def test_a_fresh_install_has_the_playfield_on_the_first_screen_and_the_rest_on_none(
            self) -> None:
        self.assertEqual(displays.placed({}, SCHEMA),
                         {"topper": None, "backglass": None, "scoreview": None,
                          "playfield": 1})


class StandingTests(unittest.TestCase):
    def test_it_is_where_the_parts_are_and_how_the_playfield_is_turned(self) -> None:
        held = displays.standing(_values(playfield={"screen_id": 1, "orientation": "portrait",
                                                    "rotation": "90"},
                                         backglass={"screen_id": 0}), SCHEMA)

        self.assertEqual(held.on, {"topper": None, "backglass": 1, "scoreview": None,
                                   "playfield": 2})
        self.assertEqual(held.turn, ("portrait", "90"))

    def test_a_fresh_install_is_a_landscape_playfield_turned_nowhere(self) -> None:
        self.assertEqual(displays.standing({}, SCHEMA).turn, ("landscape", "0"))


class CabinetTests(unittest.TestCase):
    def test_cab_mode_decides(self) -> None:
        for stored, expected in (("true", True), (True, True), ("false", False), (False, False),
                                 ("", False), (None, False)):
            with self.subTest(stored=stored):
                self.assertEqual(
                    displays.cabinet({"presentation": {"cab_mode": stored}}, SCHEMA), expected)

    def test_it_is_off_until_it_is_set(self) -> None:
        self.assertFalse(displays.cabinet({}, SCHEMA))


class WithoutDrawnTests(unittest.TestCase):
    def _blocks(self) -> list[dict]:
        return [block for block in SCHEMA if str(block.get("name")) in (
            "windows.playfield", "windows.backglass", "windows.scoreview", "windows.topper",
            "real_dmd")]

    def test_what_the_picture_draws_is_taken_out(self) -> None:
        kept = {block["name"]: [one["key"] for one in block["options"]]
                for block in displays.without_drawn(self._blocks())}

        self.assertEqual(kept["windows.backglass"], ["override"])
        self.assertEqual(kept["windows.scoreview"], ["override"])

    def test_a_section_with_nothing_left_is_dropped(self) -> None:
        left = [block["name"] for block in displays.without_drawn(self._blocks())]

        self.assertNotIn("windows.playfield", left)
        self.assertNotIn("windows.topper", left)

    def test_a_section_the_picture_does_not_draw_is_whole(self) -> None:
        [dmd] = [block for block in displays.without_drawn(self._blocks())
                 if block["name"] == "real_dmd"]
        [original] = [block for block in self._blocks() if block["name"] == "real_dmd"]

        self.assertEqual(dmd["options"], original["options"])

    def test_the_schema_it_is_given_is_not_changed(self) -> None:
        blocks = self._blocks()
        before = [len(block["options"]) for block in blocks]

        displays.without_drawn(blocks)

        self.assertEqual([len(block["options"]) for block in blocks], before)

    def test_every_setting_the_picture_draws_is_in_the_schema(self) -> None:
        declared = {(block["name"], one["key"]) for block in SCHEMA
                    for one in block.get("options") or []}

        for section, keys in displays.DRAWN.items():
            for key in keys:
                with self.subTest(section=section, key=key):
                    self.assertIn((section, key), declared)


class WritesTests(unittest.TestCase):
    def test_a_part_moved_is_the_screen_id_its_window_stores(self) -> None:
        self.assertEqual(displays.writes({"backglass": 2}, [FIRST, SECOND], "0"),
                         {"windows.backglass": {"screen_id": 1}})

    def test_the_topper_is_stored_in_its_own_window(self) -> None:
        self.assertEqual(displays.writes({"topper": 2}, [FIRST, SECOND], "0"),
                         {"windows.topper": {"screen_id": 1}})
        self.assertEqual(displays.writes({"topper": None}, [FIRST, SECOND], "0"),
                         {"windows.topper": {"screen_id": ""}})

    def test_a_part_cleared_stores_no_screen(self) -> None:
        self.assertEqual(displays.writes({"scoreview": None}, [FIRST], "0"),
                         {"windows.scoreview": {"screen_id": ""}})

    def test_a_swap_is_both_windows(self) -> None:
        self.assertEqual(displays.writes({"backglass": 2, "scoreview": 1}, [FIRST, SECOND], "0"),
                         {"windows.backglass": {"screen_id": 1},
                          "windows.scoreview": {"screen_id": 0}})

    def test_the_playfield_on_another_shape_of_screen_has_its_mounting_worked_out_again(
            self) -> None:
        self.assertEqual(displays.writes({"playfield": 2}, [FIRST, SECOND], "0"),
                         {"windows.playfield": {"screen_id": 1, "orientation": "portrait"}})
        self.assertEqual(displays.writes({"playfield": 1}, [FIRST, SECOND], "0"),
                         {"windows.playfield": {"screen_id": 0, "orientation": "landscape"}})

    def test_the_turn_the_playfield_already_has_is_kept_in_working_it_out(self) -> None:
        self.assertEqual(displays.writes({"playfield": 1}, [FIRST, SECOND], "90"),
                         {"windows.playfield": {"screen_id": 0, "orientation": "portrait"}})

    def test_the_playfield_cleared_has_no_mounting_to_work_out(self) -> None:
        self.assertEqual(displays.writes({"playfield": None}, [FIRST], "0"),
                         {"windows.playfield": {"screen_id": ""}})

    def test_a_turn_stored_as_something_else_is_taken_as_up(self) -> None:
        self.assertEqual(displays.writes({"playfield": 2}, [FIRST, SECOND], "auto"),
                         {"windows.playfield": {"screen_id": 1, "orientation": "portrait"}})

    def test_a_placement_that_is_no_screen_stores_none(self) -> None:
        self.assertEqual(displays.writes({"backglass": picture.INSIDE}, [FIRST], "0"),
                         {"windows.backglass": {"screen_id": ""}})

    def test_the_playfield_on_a_screen_not_connected_is_taken_as_wide(self) -> None:
        self.assertEqual(displays.writes({"playfield": 7}, [FIRST], "0"),
                         {"windows.playfield": {"screen_id": 6, "orientation": "landscape"}})


class RowsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write = AsyncMock(return_value=True)
        self.refill = Mock()
        self.control = self.enterContext(patch.object(displays.picture, "Picture"))

    def _drawn(self, values: dict | None = None, monitors: tuple = (BIG, TALL),
               names: tuple = ("", "")) -> Mapping[str, Any]:
        entries = displays.rows(values or {}, SCHEMA, list(monitors), list(names), self.write,
                                self.refill)
        [(_, draw)] = [entry for entry in entries if entry[0] is panel.FULL]
        draw()
        return self.control.call_args.kwargs

    def test_it_is_a_heading_and_a_row_across_the_page(self) -> None:
        entries = displays.rows({}, SCHEMA, [BIG], [""], self.write, self.refill)

        self.assertEqual([label for label, _ in entries], [panel.HEADING, panel.FULL])
        self.assertEqual(entries[0][1], t("manager.settings.section_screens"))

    def test_it_draws_the_screens_with_their_names_and_numbers(self) -> None:
        self._drawn(names=("Dell P2419H", ""))

        screens, held = self.control.call_args.args
        self.assertEqual([(one.number, one.name) for one in screens],
                         [(1, "Dell P2419H"), (2, "")])
        self.assertEqual(held, {"topper": None, "backglass": None, "scoreview": None,
                                "playfield": 1})

    def test_the_topper_is_on_the_screen_stored_for_it(self) -> None:
        self._drawn(_values(topper={"screen_id": 1}))

        _, held = self.control.call_args.args
        self.assertEqual(held["topper"], 2)

    async def test_a_move_of_the_topper_is_written_to_its_own_window(self) -> None:
        kwargs = self._drawn()

        moved = await kwargs["move"]({"topper": 2})

        self.assertTrue(moved)
        self.write.assert_awaited_once_with({"windows.topper": {"screen_id": 1}})

    def test_the_rotation_check_is_offered_on_a_cabinet_and_on_a_desk(self) -> None:
        on_a_cab = self._drawn({"presentation": {"cab_mode": "true"}})
        on_a_desk = self._drawn({"presentation": {"cab_mode": "false"}})

        self.assertTrue(on_a_cab["cabinet"])
        self.assertFalse(on_a_desk["cabinet"])
        for drawn in (on_a_cab, on_a_desk):
            self.assertIsNotNone(drawn["point"])
            self.assertIsNotNone(drawn["test"])

    def test_the_picture_is_given_the_stored_rotation(self) -> None:
        drawn = self._drawn(_values(playfield={"rotation": "270"}))

        self.assertEqual(drawn["rotation"], "270")

    async def test_a_part_moved_is_written_as_its_screen_id(self) -> None:
        drawn = self._drawn()

        self.assertTrue(await drawn["move"]({"backglass": 2}))

        self.write.assert_awaited_once_with({"windows.backglass": {"screen_id": 1}})
        self.refill.assert_not_called()

    async def test_a_write_that_fails_says_so_and_holds_nothing(self) -> None:
        self.write.return_value = False
        drawn = self._drawn(_values(backglass={"screen_id": 4}))

        self.assertFalse(await drawn["move"]({"backglass": 1}))
        self.write.return_value = True
        await drawn["move"]({"scoreview": 2})

        self.refill.assert_called_once_with()

    async def test_a_part_stored_on_a_screen_that_is_gone_redraws_the_page_once_moved(
            self) -> None:
        drawn = self._drawn(_values(backglass={"screen_id": 4}))

        await drawn["move"]({"backglass": 1})

        self.refill.assert_called_once_with()

    async def test_a_write_that_fails_does_not_redraw_the_page(self) -> None:
        self.write.return_value = False
        drawn = self._drawn(_values(backglass={"screen_id": 4}))

        await drawn["move"]({"backglass": 1})

        self.refill.assert_not_called()

    async def test_a_turn_is_written_as_the_playfield_s_mounting_and_rotation(self) -> None:
        drawn = self._drawn({"presentation": {"cab_mode": "true"}})

        self.assertTrue(await drawn["point"](90, FIRST))

        self.write.assert_awaited_once_with(
            {"windows.playfield": {"orientation": "portrait", "rotation": "90"}})

    async def test_the_turn_just_chosen_is_the_one_a_later_move_keeps(self) -> None:
        drawn = self._drawn({"presentation": {"cab_mode": "true"}})
        await drawn["point"](90, FIRST)
        self.write.reset_mock()

        await drawn["move"]({"playfield": 1})

        self.write.assert_awaited_once_with(
            {"windows.playfield": {"screen_id": 0, "orientation": "portrait"}})

    async def test_a_turn_that_was_not_saved_is_not_the_turn_kept(self) -> None:
        drawn = self._drawn({"presentation": {"cab_mode": "true"}})
        self.write.return_value = False
        await drawn["point"](90, FIRST)
        self.write.reset_mock()
        self.write.return_value = True

        await drawn["move"]({"playfield": 1})

        self.write.assert_awaited_once_with(
            {"windows.playfield": {"screen_id": 0, "orientation": "landscape"}})

    async def test_show_numbers_is_given_the_screens_and_what_each_shows(self) -> None:
        local = self.enterContext(patch.object(displays.device_client, "local"))
        self.enterContext(patch.object(displays.run, "io_bound", new=AsyncMock()))
        drawn = self._drawn({"presentation": {"cab_mode": "true"}})

        await press(drawn["show"], {"playfield": 2})

        cast(Any, displays.run.io_bound).assert_awaited_once()
        args = cast(Any, displays.run.io_bound).await_args.args
        self.assertIs(args[0], local.return_value.show_numbers)
        self.assertEqual([one.number for one in args[1]], [1, 2])
        self.assertEqual(args[2], {"playfield": 2})
        self.assertEqual(len(args), 3)

    async def test_show_numbers_that_cannot_open_a_window_says_why(self) -> None:
        self.enterContext(patch.object(displays.device_client, "local"))
        self.enterContext(patch.object(displays.run, "io_bound",
                                       new=AsyncMock(side_effect=RuntimeError("No browser"))))
        notify = self.enterContext(patch.object(displays.ui, "notify"))
        drawn = self._drawn()

        await press(drawn["show"], {})

        notify.assert_called_once()
        self.assertEqual(notify.call_args.args, (t("manager.screens.show_failed"),))
        self.assertIn("No browser", notify.call_args.kwargs["caption"])


class TestCardTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.local = self.enterContext(patch.object(displays.device_client, "local"))
        self.io = self.enterContext(patch.object(displays.run, "io_bound", new=AsyncMock()))
        self.enterContext(patch("manager.kit.on_page.ui"))

    async def test_opening_is_the_device_s_card_on_that_screen_and_turn(self) -> None:
        device = self.local.return_value
        self.io.side_effect = [None, True]

        opened = await displays.test_card().open(FIRST, 90)

        self.assertTrue(opened)
        self.assertEqual([call.args for call in self.io.await_args_list],
                         [(device.show_test_card, FIRST, 90), (device.test_card_open,)])

    async def test_a_card_that_did_not_stay_open_says_so(self) -> None:
        self.io.side_effect = [None, False]
        notify = self.enterContext(patch.object(displays.ui, "notify"))

        self.assertFalse(await displays.test_card().open(FIRST, 0))

        self.assertEqual(notify.call_args.args, (t("manager.screens.test_card_failed"),))

    async def test_a_card_that_cannot_open_says_why(self) -> None:
        self.io.side_effect = RuntimeError("No browser")
        notify = self.enterContext(patch.object(displays.ui, "notify"))

        opened = await displays.test_card().open(FIRST, 0)

        self.assertFalse(opened)
        self.assertEqual(notify.call_args.args, (t("manager.screens.test_card_failed"),))
        self.assertIn("No browser", notify.call_args.kwargs["caption"])

    async def test_turn_close_and_hold_reach_the_device(self) -> None:
        card = displays.test_card()
        self.io.return_value = True

        await card.turn(180)
        await card.close()
        held = await card.hold()

        device = self.local.return_value
        self.assertEqual([call.args for call in self.io.await_args_list],
                         [(device.turn_test_card, 180), (device.close_test_card,),
                          (device.keep_test_card,)])
        self.assertIs(held, True)


class FollowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.write = AsyncMock(return_value=True)
        self.follow = AsyncMock()
        self.control = self.enterContext(patch.object(displays.picture, "Picture"))
        self.tasks: list[asyncio.Task[Any]] = []
        self.enterContext(patch.object(
            displays.background_tasks, "create",
            side_effect=lambda coro, **_: self.tasks.append(asyncio.ensure_future(coro))))

    async def _settled(self) -> None:
        await asyncio.gather(*self.tasks)

    def _drawn(self, values: dict | None = None) -> Mapping[str, Any]:
        entries = displays.rows(values or {"presentation": {"cab_mode": "true"}}, SCHEMA,
                                [BIG, TALL], ["", ""], self.write, Mock(), self.follow)
        [(_, draw)] = [entry for entry in entries if entry[0] is panel.FULL]
        draw()
        return self.control.call_args.kwargs

    async def test_a_part_moved_tells_what_it_was_and_what_it_is(self) -> None:
        drawn = self._drawn()

        await drawn["move"]({"backglass": 2})
        await self._settled()

        [(before, after, here)] = [call.args for call in self.follow.await_args_list]
        self.assertEqual(before.on["backglass"], None)
        self.assertEqual(after.on["backglass"], 2)
        self.assertEqual([one.number for one in here], [1, 2])

    async def test_the_playfield_moved_to_another_shape_of_screen_tells_its_new_mounting(
            self) -> None:
        drawn = self._drawn()

        await drawn["move"]({"playfield": 2})
        await self._settled()

        [(before, after, _)] = [call.args for call in self.follow.await_args_list]
        self.assertEqual(before.turn, ("landscape", "0"))
        self.assertEqual(after.turn, ("portrait", "0"))

    async def test_a_turn_tells_the_turn_it_made(self) -> None:
        drawn = self._drawn()

        await drawn["point"](90, FIRST)
        await self._settled()

        [(before, after, _)] = [call.args for call in self.follow.await_args_list]
        self.assertEqual((before.turn, after.turn), (("landscape", "0"), ("portrait", "90")))
        self.assertEqual(before.on, after.on)

    async def test_a_change_that_was_not_kept_tells_nothing(self) -> None:
        self.write.return_value = False
        drawn = self._drawn()

        await drawn["move"]({"backglass": 2})
        await drawn["point"](90, FIRST)

        self.follow.assert_not_awaited()

    async def test_each_change_is_told_from_where_the_last_one_left_it(self) -> None:
        drawn = self._drawn()

        await drawn["move"]({"backglass": 2})
        await drawn["move"]({"backglass": 1})
        await self._settled()

        [(_, first, _), (before, after, _)] = [call.args for call in self.follow.await_args_list]
        self.assertEqual(before.on, first.on)
        self.assertEqual(after.on["backglass"], 1)

    async def test_the_picture_does_not_wait_for_the_following(self) -> None:
        release = asyncio.Event()
        async def follow(*_: Any) -> None:
            await release.wait()

        self.follow.side_effect = follow
        drawn = self._drawn()

        kept = await asyncio.wait_for(drawn["move"]({"backglass": 2}), timeout=1)
        await asyncio.sleep(0)

        self.assertTrue(kept)
        self.follow.assert_awaited_once()
        self.assertFalse(self.tasks[0].done())
        release.set()
        await self._settled()

    async def test_one_following_ends_before_the_next_begins(self) -> None:
        seen: list[str] = []
        release = asyncio.Event()

        async def follow(before: Any, after: Any, _here: Any) -> None:
            seen.append(f"begin {after.on['backglass']}")
            await release.wait()
            seen.append(f"end {after.on['backglass']}")

        self.follow.side_effect = follow
        drawn = self._drawn()

        await drawn["move"]({"backglass": 2})
        await drawn["move"]({"backglass": 1})
        await asyncio.sleep(0.01)
        self.assertEqual(seen, ["begin 2"])
        release.set()
        await self._settled()

        self.assertEqual(seen, ["begin 2", "end 2", "begin 1", "end 1"])

    async def test_nothing_is_asked_to_follow_where_nothing_is_listening(self) -> None:
        entries = displays.rows({}, SCHEMA, [BIG], [""], self.write, Mock())
        [(_, draw)] = [entry for entry in entries if entry[0] is panel.FULL]
        draw()

        self.assertTrue(await self.control.call_args.kwargs["move"]({"backglass": 1}))


def _waiting(*keys: str, waits_for: str = "reopen", reopen: bool = True) -> dict[str, Any]:
    return {"reopen": reopen,
            "waiting": [{"section": "windows.playfield", "key": key, "waits_for": waits_for}
                        for key in keys]}


class WaitsForReopenTests(unittest.TestCase):
    def test_a_turn_waiting_on_the_windows_is_applied_by_reopening_them(self) -> None:
        for keys in (("rotation",), ("orientation",), ("orientation", "rotation")):
            with self.subTest(keys=keys):
                self.assertTrue(displays.waits_for_reopen(_waiting(*keys)))

    def test_nothing_waiting_is_nothing_to_reopen_for(self) -> None:
        self.assertFalse(displays.waits_for_reopen(_waiting()))
        self.assertFalse(displays.waits_for_reopen({}))

    def test_a_turn_waiting_on_a_restart_is_left_to_the_bar(self) -> None:
        self.assertFalse(displays.waits_for_reopen(_waiting("rotation", waits_for="restart")))

    def test_a_reopen_that_cannot_be_done_here_is_not_asked_for(self) -> None:
        self.assertFalse(displays.waits_for_reopen(_waiting("rotation", reopen=False)))

    def test_another_setting_waiting_is_not_the_turn(self) -> None:
        answer = {"reopen": True, "waiting": [
            {"section": "windows.backglass", "key": "screen_id", "waits_for": "reopen"}]}

        self.assertFalse(displays.waits_for_reopen(answer))


async def _apply() -> None:
    apply = displays.test_card().apply
    assert apply is not None
    await apply()


class ApplyTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.client = self.enterContext(patch.object(displays, "ApiClient")).return_value
        self.io = self.enterContext(patch.object(displays.run, "io_bound", new=AsyncMock()))
        self.notify = self.enterContext(patch.object(displays.ui, "notify"))
        self.enterContext(patch("manager.kit.on_page.ui"))

    async def test_a_turn_waiting_on_the_windows_reopens_them(self) -> None:
        self.io.side_effect = [_waiting("rotation"), {"performed": True}]

        await _apply()

        self.assertEqual([call.args for call in self.io.await_args_list],
                         [(self.client.config_waiting,),
                          (self.client.perform_action, "frontend", "restart")])
        self.assertEqual(self.notify.call_args.args, (t("manager.devices.windows_reopened"),))

    async def test_nothing_waiting_on_the_turn_reopens_nothing(self) -> None:
        self.io.side_effect = [_waiting()]

        await _apply()

        self.assertEqual([call.args for call in self.io.await_args_list],
                         [(self.client.config_waiting,)])
        self.notify.assert_not_called()

    async def test_a_reopen_that_did_not_happen_says_so(self) -> None:
        self.io.side_effect = [_waiting("rotation"), {"performed": False}]

        await _apply()

        self.assertEqual(self.notify.call_args.args[0], t(
            "manager.devices.not_happen", label=t("manager.waiting.reopen_windows")))

    async def test_a_reopen_that_failed_says_why(self) -> None:
        self.io.side_effect = RuntimeError("No frontend")

        await _apply()

        self.assertEqual(self.notify.call_args.args, (t("said.action_failed"),))
        self.assertIn("No frontend", self.notify.call_args.kwargs["caption"])


if __name__ == "__main__":
    unittest.main()
