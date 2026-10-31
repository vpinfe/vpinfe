"""The screens picture: where a screen goes, which way the playfield faces, and what it draws."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from nicegui import ui

from common.i18n import t
from manager.settings import screens_picture as picture
from manager.settings.screens import Screen
from tests.support.clicks import press

BIG = Screen(1, "Dell P2419H", 2560, 1440, 0, 0)
TALL = Screen(2, "", 1080, 1920, 2560, 0)
STRIP = Screen(3, "", 1280, 390, 0, 1440)
BARE = Screen(4, "", 1920, 1080, 3640, 0)


class PlacedTests(unittest.TestCase):
    def test_a_screen_put_on_a_part_is_on_it(self) -> None:
        after = picture.placed({"backglass": None, "playfield": 1}, "backglass", 2)

        self.assertEqual(after, {"backglass": 2, "playfield": 1})

    def test_a_screen_put_where_another_is_sends_that_one_to_where_it_came_from(self) -> None:
        after = picture.placed({"backglass": 1, "playfield": 2}, "backglass", 2)

        self.assertEqual(after, {"backglass": 2, "playfield": 1})

    def test_a_swap_with_an_empty_part_leaves_that_part_empty(self) -> None:
        after = picture.placed({"backglass": None, "playfield": 2}, "backglass", 2)

        self.assertEqual(after, {"backglass": 2, "playfield": None})

    def test_no_screen_leaves_the_part_empty_and_touches_no_other(self) -> None:
        after = picture.placed({"backglass": 1, "playfield": 2}, "backglass", None)

        self.assertEqual(after, {"backglass": None, "playfield": 2})

    def test_what_it_is_given_is_not_changed(self) -> None:
        on = {"backglass": 1, "playfield": 2}

        picture.placed(on, "backglass", 2)

        self.assertEqual(on, {"backglass": 1, "playfield": 2})

    def test_only_the_parts_that_differ_are_changes(self) -> None:
        before = {"backglass": 1, "playfield": 2, "scoreview": None}

        self.assertEqual(picture.changes(before, picture.placed(before, "backglass", 2)),
                         {"backglass": 2, "playfield": 1})
        self.assertEqual(picture.changes(before, dict(before)), {})


class InsideTests(unittest.TestCase):
    def test_visual_pinball_has_a_topper_above_the_backglass_and_the_playfield_last(self) -> None:
        self.assertEqual([part.key for part in picture.vpx_parts()],
                         ["topper", "backglass", "scoreview", "playfield"])

    def test_every_part_but_the_playfield_can_be_drawn_inside_it(self) -> None:
        self.assertEqual({part.key: part.inside for part in picture.vpx_parts()},
                         {"topper": True, "backglass": True, "scoreview": True,
                          "playfield": False})

    def test_vpinfe_has_a_topper_above_the_backglass_and_the_playfield_last(self) -> None:
        self.assertEqual([part.key for part in picture.vpinfe_parts()],
                         ["topper", "backglass", "scoreview", "playfield"])

    def test_vpinfe_s_parts_are_never_offered_inside(self) -> None:
        self.assertFalse([part for part in picture.vpinfe_parts() if part.inside])

    def test_inside_is_no_screen_and_displaces_nothing(self) -> None:
        self.assertEqual(picture.placed({"backglass": 2, "playfield": 1}, "scoreview",
                                        picture.INSIDE),
                         {"backglass": 2, "playfield": 1, "scoreview": picture.INSIDE})

    def test_a_screen_put_where_a_part_is_inside_sends_nothing_back(self) -> None:
        self.assertEqual(picture.placed({"backglass": picture.INSIDE, "playfield": 1},
                                        "backglass", 1),
                         {"backglass": 1, "playfield": picture.INSIDE})

    def test_the_playfield_cannot_be_drawn_inside_itself(self) -> None:
        self.assertFalse(next(part for part in picture.vpx_parts()
                              if part.key == picture.PLAYFIELD).inside)


class TurnTests(unittest.TestCase):
    """The setups in docs/theme.md: a rotated portrait monitor, one the OS leaves alone, and a
    landscape one."""

    def test_a_tall_window_not_turned_is_portrait(self) -> None:
        self.assertEqual(picture.turn(0, TALL), {"orientation": "portrait", "rotation": "0"})

    def test_a_wide_window_turned_a_quarter_is_portrait(self) -> None:
        self.assertEqual(picture.turn(90, BIG), {"orientation": "portrait", "rotation": "90"})
        self.assertEqual(picture.turn(270, BIG), {"orientation": "portrait", "rotation": "270"})

    def test_a_wide_window_not_turned_is_landscape(self) -> None:
        self.assertEqual(picture.turn(0, BIG), {"orientation": "landscape", "rotation": "0"})

    def test_a_tall_window_turned_a_quarter_is_landscape(self) -> None:
        self.assertEqual(picture.turn(90, TALL), {"orientation": "landscape", "rotation": "90"})

    def test_a_half_turn_keeps_the_shape_of_the_screen(self) -> None:
        self.assertEqual(picture.turn(180, BIG), {"orientation": "landscape", "rotation": "180"})
        self.assertEqual(picture.turn(180, TALL), {"orientation": "portrait", "rotation": "180"})

    def test_a_screen_not_connected_is_taken_as_wide(self) -> None:
        self.assertEqual(picture.turn(0, None)["orientation"], "landscape")


class GuessTests(unittest.TestCase):
    def test_no_screens_place_nothing(self) -> None:
        self.assertEqual(picture.guess([]), {})

    def test_a_lone_screen_is_the_playfield(self) -> None:
        self.assertEqual(picture.guess([STRIP]), {"playfield": 3})

    def test_a_strip_is_the_scoreview_and_the_biggest_of_the_rest_the_playfield(
            self) -> None:
        self.assertEqual(picture.guess([STRIP, BARE, BIG]),
                         {"scoreview": 3, "playfield": 1, "backglass": 4})

    def test_the_fourth_screen_is_the_topper(self) -> None:
        self.assertEqual(picture.guess([STRIP, BARE, BIG, TALL]),
                         {"scoreview": 3, "playfield": 1, "backglass": 4, "topper": 2})

    def test_three_screens_leave_the_topper_unplaced(self) -> None:
        self.assertNotIn("topper", picture.guess([STRIP, BARE, BIG]))

    def test_two_screens_of_one_size_are_not_told_apart(self) -> None:
        twin = Screen(5, "", 1920, 1080, 0, 0)

        self.assertEqual(picture.guess([BARE, twin]), {})

    def test_a_size_shared_leaves_the_others_to_be_placed(self) -> None:
        twin = Screen(5, "", 1920, 1080, 0, 0)

        self.assertEqual(picture.guess([BARE, twin, STRIP, BIG]),
                         {"scoreview": 3, "playfield": 1})

    def test_two_strips_are_not_told_apart(self) -> None:
        other = Screen(5, "", 1920, 600, 0, 0)

        self.assertEqual(picture.guess([STRIP, other, BIG]), {"playfield": 1})

    def test_an_ordinary_widescreen_is_not_a_strip(self) -> None:
        self.assertNotIn("scoreview", picture.guess([BIG, BARE]))

    def test_each_screen_is_given_to_one_part(self) -> None:
        found = picture.guess([STRIP, BARE, BIG, TALL])

        self.assertEqual(len(set(found.values())), len(found))


async def _no_move(_moved: dict[str, int | None]) -> bool:
    return True


def _drawn(on: dict[str, picture.Placement], screens: tuple[Screen, ...] = (BIG, TALL, STRIP),
           *, into: ui.element | None = None,
           **kwargs: Any) -> tuple[picture.Picture, ui.element]:
    with into or ui.column() as body:
        control = picture.Picture(screens, on, move=kwargs.pop("move", _no_move), **kwargs)
    return control, body


def _labels(body: ui.element) -> list[str]:
    return [one.text for one in body.descendants() if isinstance(one, ui.label)]


def _classes(body: ui.element) -> list[str]:
    return [name for one in body.descendants() for name in one.classes]


def _buttons(body: ui.element) -> list[Any]:
    return [one.props.get("label") for one in body.descendants() if isinstance(one, ui.button)]


def _button(body: ui.element, label: str) -> ui.button:
    [button] = [one for one in body.descendants()
                if isinstance(one, ui.button) and one.props.get("label") == label]
    return button


def _test(**kwargs: Any) -> Any:
    return picture.Test(open=kwargs.get("open", AsyncMock(return_value=True)),
                        turn=kwargs.get("turn", AsyncMock()),
                        close=kwargs.get("close", AsyncMock()),
                        hold=kwargs.get("hold", AsyncMock(return_value=True)),
                        apply=kwargs.get("apply"))


# What a cabinet gives the picture for the rotation check.
_ASKS: dict[str, Any] = {"point": AsyncMock(return_value=True), "test": _test()}


class DrawnTests(unittest.TestCase):
    def test_the_picture_is_the_container_its_own_layout_queries(self) -> None:
        control, _ = _drawn({})

        self.assertIn("manager-screens-host", control.container.classes)

    def test_every_part_is_drawn_with_its_name(self) -> None:
        _, body = _drawn({})

        labels = _labels(body)
        for word in ("manager.screens.backglass", "manager.screens.scoreview",
                     "manager.screens.playfield"):
            self.assertIn(t(word), labels)

    def test_an_empty_part_reads_no_screen(self) -> None:
        _, body = _drawn({"backglass": None})

        self.assertIn(t("manager.screens.no_screen"), _labels(body))

    def test_a_filled_part_is_its_number_and_the_screen_s_name(self) -> None:
        _, body = _drawn({"playfield": 1})

        self.assertIn("1", _labels(body))
        self.assertIn("Dell P2419H", _labels(body))

    def test_a_screen_with_no_name_is_said_by_its_size(self) -> None:
        _, body = _drawn({"playfield": 2})

        self.assertIn(t("manager.screens.size", width=1080, height=1920), _labels(body))

    def test_a_part_whose_screen_is_not_connected_says_so_and_is_marked(self) -> None:
        _, body = _drawn({"backglass": 9})

        self.assertIn(t("manager.screens.not_connected"), _labels(body))
        self.assertIn("manager-part-slot--missing", _classes(body))

    def test_every_connected_screen_is_a_card_and_says_what_it_is_used_for(self) -> None:
        _, body = _drawn({"playfield": 1, "backglass": 2})

        labels = _labels(body)
        self.assertEqual(_classes(body).count("manager-screen-card"), 3)
        self.assertIn(t("manager.screens.playfield"), labels)
        self.assertIn(t("manager.screens.not_used"), labels)

    def test_a_screen_that_is_not_on_a_part_is_a_card_all_the_same(self) -> None:
        _, body = _drawn({})

        self.assertEqual(_classes(body).count("manager-screen-card"), 3)

    def test_no_screens_says_so_where_the_cards_would_be(self) -> None:
        _, body = _drawn({}, screens=())

        self.assertIn(t("manager.screens.none_found"), _labels(body))
        self.assertNotIn("manager-screen-card", _classes(body))

    def test_the_playfield_carries_a_small_card_turned_to_the_stored_answer(self) -> None:
        _, body = _drawn({"playfield": 1}, rotation="90")

        [card] = [one for one in body.descendants() if "manager-pf-card" in one.classes]
        self.assertIn("rotate(90deg)", card._style.get("transform", ""))
        labels = [one.text for one in card.descendants() if isinstance(one, ui.label)]
        self.assertEqual(labels, [t("manager.screens.card_top"), t("manager.screens.card_bottom")])

    def test_a_rotation_that_is_no_quarter_turn_draws_the_card_upright(self) -> None:
        _, body = _drawn({"playfield": 1}, rotation="auto")

        [card] = [one for one in body.descendants() if "manager-pf-card" in one.classes]
        self.assertIn("rotate(0deg)", card._style.get("transform", ""))

    def test_a_turn_that_is_not_known_draws_no_card(self) -> None:
        _, body = _drawn({"playfield": 1}, rotation=None)

        self.assertNotIn("manager-pf-card", _classes(body))

    def test_a_desktop_page_asks_the_rotation_question_and_words_its_hint_for_a_desk(self) -> None:
        _, body = _drawn({"playfield": 1}, cabinet=False, **_ASKS)

        self.assertIn("manager-pf-card", _classes(body))
        self.assertIn("manager-rotation", _classes(body))
        self.assertIn(t("manager.screens.hint_desktop"), _labels(body))

    def test_a_playfield_with_no_screen_has_no_card_and_asks_nothing(self) -> None:
        _, body = _drawn({"playfield": None}, **_ASKS)

        self.assertNotIn("manager-pf-card", _classes(body))
        self.assertNotIn("manager-rotation", _classes(body))

    def test_the_question_is_asked_only_where_something_hears_the_answer(self) -> None:
        _, none = _drawn({"playfield": 1})
        _, no_test = _drawn({"playfield": 1}, point=AsyncMock())
        _, no_point = _drawn({"playfield": 1}, test=_test())

        for body in (none, no_test, no_point):
            self.assertNotIn("manager-rotation", _classes(body))

    def test_the_question_is_asked_beside_the_screens_with_a_card_to_open(self) -> None:
        _, body = _drawn({"playfield": 1}, **_ASKS)

        labels = _labels(body)
        self.assertIn(t("manager.screens.rotation"), labels)
        self.assertIn(t("manager.screens.rotation_look"), labels)
        self.assertIn(t("manager.screens.rotation_top"), labels)
        self.assertEqual(_buttons(body), [t("manager.screens.test_card")])
        self.assertNotIn(t("manager.screens.rotation_press"), labels)

    def test_an_answer_that_is_not_turned_wears_no_chip(self) -> None:
        _, body = _drawn({"playfield": 1}, rotation="0", **_ASKS)

        self.assertNotIn("manager-tier", _classes(body))

    def test_an_answer_that_is_turned_wears_a_chip_that_says_which_way(self) -> None:
        for rotation, word in (("90", "turned_right"), ("180", "turned_around"),
                               ("270", "turned_left")):
            with self.subTest(rotation=rotation):
                _, body = _drawn({"playfield": 1}, rotation=rotation, **_ASKS)

                self.assertIn(t(f"manager.screens.{word}"), _labels(body))

    def test_show_numbers_is_offered_only_where_something_runs_it(self) -> None:
        _, without = _drawn({})
        _, with_it = _drawn({}, show=AsyncMock())

        self.assertNotIn(t("manager.screens.show_numbers"),
                         [one.text for one in without.descendants()
                          if isinstance(one, ui.button)])
        self.assertIn(t("manager.screens.show_numbers"),
                      [one.props.get("label") for one in with_it.descendants()
                       if isinstance(one, ui.button)])

    def test_the_part_a_hint_belongs_to_carries_it(self) -> None:
        [score] = [part for part in picture.vpinfe_parts() if part.key == picture.SCOREVIEW]

        self.assertEqual(score.hint, t("manager.screens.scoreview.help"))
        self.assertEqual(score.label, t("manager.screens.scoreview"))


class VpxDrawnTests(unittest.TestCase):
    def _vpx(self, on: dict, **kwargs: Any) -> tuple[picture.Picture, ui.element]:
        return _drawn(on, parts=picture.vpx_parts(), **kwargs)

    def test_a_topper_is_drawn_above_the_backglass(self) -> None:
        _, body = self._vpx({"topper": 2})

        labels = _labels(body)
        self.assertLess(labels.index(t("manager.screens.topper")),
                        labels.index(t("manager.screens.backglass")))
        self.assertIn("manager-part--topper", _classes(body))

    def test_a_part_inside_the_playfield_says_so_and_is_not_a_screen(self) -> None:
        _, body = self._vpx({"backglass": picture.INSIDE, "playfield": 1})

        self.assertIn(t("manager.screens.inside"), _labels(body))
        self.assertIn("manager-part-slot--inside", _classes(body))
        self.assertNotIn("manager-part-slot--missing", _classes(body))

    def test_a_part_inside_the_playfield_cannot_be_dragged_out(self) -> None:
        _, body = self._vpx({"backglass": picture.INSIDE})

        [slot] = [one for one in body.descendants() if "manager-part-slot--inside" in one.classes]
        self.assertNotIn("draggable", slot.props)

    def test_a_part_inside_can_be_put_back_on_no_screen(self) -> None:
        _, body = self._vpx({"backglass": picture.INSIDE})

        [slot] = [one for one in body.descendants() if "manager-part-slot--inside" in one.classes]
        self.assertIn("manager-part-slot-clear",
                      [name for one in slot.descendants() for name in one.classes])

    def test_the_menu_offers_inside_to_the_parts_that_can_be(self) -> None:
        _, body = self._vpx({})
        _, plain = _drawn({})

        said = [one.text for one in body.descendants() if isinstance(one, ui.label)
                and one.text == t("manager.screens.inside")]
        self.assertEqual(len(said), 3)
        self.assertNotIn(t("manager.screens.inside"), _labels(plain))

    def test_nothing_that_differs_draws_no_line_about_it(self) -> None:
        _, body = self._vpx({"playfield": 1})

        self.assertNotIn(t("manager.screens.different"), _labels(body))

    def test_a_part_that_differs_is_named_and_can_be_matched(self) -> None:
        _, body = self._vpx({"playfield": 1, "backglass": 2}, differs=["backglass"],
                            match=AsyncMock())

        self.assertIn(t("manager.screens.different"), _labels(body))
        self.assertIn(t("manager.screens.backglass"), _labels(body))
        self.assertIn("manager-part-name--differs", _classes(body))
        self.assertIn(t("manager.screens.match_vpinfe"),
                      [one.props.get("label") for one in body.descendants()
                       if isinstance(one, ui.button)])

    def test_the_vpx_page_draws_the_card_and_never_asks_the_question(self) -> None:
        _, body = self._vpx({"playfield": 1}, rotation="270")

        self.assertIn("manager-pf-card", _classes(body))
        self.assertNotIn("manager-rotation", _classes(body))

    def test_with_nothing_to_match_with_the_line_still_says_it_differs(self) -> None:
        _, body = self._vpx({"playfield": 1, "backglass": 2}, differs=["backglass"])

        self.assertIn(t("manager.screens.different"), _labels(body))
        self.assertNotIn(t("manager.screens.match_vpinfe"),
                         [one.props.get("label") for one in body.descendants()
                          if isinstance(one, ui.button)])


class ActedOnTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        # Outside the test's task, which has no page to draw into.
        self.body = ui.column()

    def _drawn(self, on: dict[str, picture.Placement],
               **kwargs: Any) -> tuple[picture.Picture, Any]:
        return _drawn(on, into=self.body, **kwargs)

    async def test_a_screen_put_on_a_part_is_reported_and_then_drawn_there(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": None, "playfield": 1}, move=move)

        await press(control._put, "backglass", 2)

        move.assert_awaited_once_with({"backglass": 2})
        self.assertEqual(control.on, {"backglass": 2, "playfield": 1})

    async def test_a_swap_is_reported_as_both_parts(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": 1, "playfield": 2}, move=move)

        await press(control._put, "backglass", 2)

        move.assert_awaited_once_with({"backglass": 2, "playfield": 1})

    async def test_a_move_that_was_not_kept_leaves_the_picture_as_it_was(self) -> None:
        control, _ = self._drawn({"backglass": None}, move=AsyncMock(return_value=False))

        await press(control._put, "backglass", 2)

        self.assertEqual(control.on, {"backglass": None})

    async def test_putting_a_screen_where_it_already_is_reports_nothing(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": 2}, move=move)

        await press(control._put, "backglass", 2)

        move.assert_not_awaited()

    async def test_a_part_cleared_is_reported_as_no_screen(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": 2}, move=move)

        await press(control._put, "backglass", None)

        move.assert_awaited_once_with({"backglass": None})
        self.assertEqual(control.on, {"backglass": None})

    async def test_a_drop_of_a_connected_screen_puts_it_there(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": None}, move=move)

        await press(control._dropped, "backglass", SimpleNamespace(args=2))

        move.assert_awaited_once_with({"backglass": 2})

    async def test_a_drop_of_anything_else_changes_nothing(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": None}, move=move)

        for args in (9, None, "2", {"file": 1}, 0.5):
            with self.subTest(args=args):
                await press(control._dropped, "backglass", SimpleNamespace(args=args))

        move.assert_not_awaited()

    async def test_the_test_card_is_opened_on_the_playfield_s_screen_turned_as_stored(
            self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test, rotation="90")

        await press(control._open_card)

        test.open.assert_awaited_once_with(TALL, 90)
        self.assertEqual(control.phase, "checking")
        self.assertFalse(control.opening)

    async def test_a_card_that_did_not_come_up_leaves_the_question_to_be_asked_again(
            self) -> None:
        test = _test(open=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)

        await press(control._open_card)

        self.assertEqual(control.phase, "idle")
        self.assertFalse(control.opening)

    async def test_a_card_that_raises_still_lets_it_be_pressed_again(self) -> None:
        test = _test(open=AsyncMock(side_effect=RuntimeError("no")))
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)

        with self.assertRaises(RuntimeError):
            await press(control._open_card)

        button = _button(body, t("manager.screens.test_card"))
        self.assertTrue(button.enabled)

    async def test_a_card_that_comes_up_after_the_playfield_moved_is_taken_down(self) -> None:
        async def open_then_move(_screen: Screen, _rotation: int) -> bool:
            control.on["playfield"] = 1
            return True

        test = _test(open=AsyncMock(side_effect=open_then_move))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)

        await press(control._open_card)

        test.close.assert_awaited_once_with()
        self.assertEqual(control.phase, "idle")

    async def test_while_checking_it_asks_for_the_turns_and_says_to_stop_when_it_is_right(
            self) -> None:
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test(),
                                    show=AsyncMock())

        await press(control._open_card)

        self.assertEqual(_buttons(body)[1:], [t("manager.screens.turn_left"),
                                          t("manager.screens.turn_around"),
                                          t("manager.screens.turn_right"),
                                          t("manager.screens.looks_right"),
                                          t("manager.screens.test_card_close")])
        self.assertIn(t("manager.screens.rotation_press"), _labels(body))
        self.assertIn(t("manager.screens.rotation_stop"), _labels(body))
        self.assertFalse(_button(body, t("manager.screens.show_numbers")).enabled)

    async def test_each_turn_adds_to_the_stored_rotation_and_turns_the_card(self) -> None:
        for start, degrees, total in ((0, 90, 90), (0, 180, 180), (0, 270, 270),
                                      (90, 90, 180), (270, 90, 0), (180, 270, 90)):
            with self.subTest(start=start, degrees=degrees):
                point = AsyncMock(return_value=True)
                test = _test()
                control, _ = self._drawn({"playfield": 2}, point=point, test=test,
                                         rotation=str(start))
                await press(control._open_card)

                await press(control._turn_by, degrees)

                point.assert_awaited_once_with(total, TALL)
                test.turn.assert_awaited_once_with(total)
                self.assertEqual(control.rotation, total)

    async def test_two_turns_pressed_together_both_count(self) -> None:
        async def slow(_total: int, _screen: Screen) -> bool:
            await asyncio.sleep(0.01)
            return True

        point = AsyncMock(side_effect=slow)
        control, _ = self._drawn({"playfield": 2}, point=point, test=_test())
        await press(control._open_card)

        await asyncio.gather(press(control._turn_by, 90), press(control._turn_by, 90))

        self.assertEqual([call.args[0] for call in point.await_args_list], [90, 180])
        self.assertEqual(control.rotation, 180)

    async def test_a_turn_after_the_card_has_gone_is_not_made(self) -> None:
        point = AsyncMock(return_value=True)
        control, _ = self._drawn({"playfield": 2}, point=point, test=_test())

        await press(control._turn_by, 90)

        point.assert_not_awaited()

    async def test_a_turn_that_was_not_kept_leaves_the_card_and_the_answer_as_they_were(
            self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(return_value=False),
                                 test=test, rotation="90")
        await press(control._open_card)

        await press(control._turn_by, 90)

        test.turn.assert_not_awaited()
        self.assertEqual(control.rotation, 90)

    async def test_looks_right_takes_the_card_down_and_says_the_screen_is_rotated_correctly(
            self) -> None:
        test = _test()
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)

        await press(control._looks_right)

        test.close.assert_awaited_once_with()
        self.assertEqual(control.phase, "done")
        self.assertIn(t("manager.screens.rotated_correctly"), _labels(body))
        self.assertEqual(_buttons(body), [t("manager.screens.check_again")])

    async def test_looks_right_is_what_puts_the_turn_into_effect(self) -> None:
        apply = AsyncMock()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test(apply=apply))
        await press(control._open_card)

        await press(control._looks_right)

        apply.assert_awaited_once_with()

    async def test_closing_the_card_does_not_put_the_turn_into_effect(self) -> None:
        apply = AsyncMock()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test(apply=apply))
        await press(control._open_card)

        await press(control._close_card)

        apply.assert_not_awaited()

    async def test_looks_right_after_the_card_is_gone_puts_the_turn_into_effect(self) -> None:
        apply = AsyncMock()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test(apply=apply))
        control.phase = "asking"

        await press(control._looks_right)

        apply.assert_awaited_once_with()

    async def test_a_second_press_of_looks_right_does_not_put_the_turn_into_effect_again(
            self) -> None:
        apply = AsyncMock()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test(apply=apply))
        await press(control._open_card)

        await asyncio.gather(press(control._looks_right), press(control._looks_right))

        apply.assert_awaited_once_with()

    async def test_check_again_asks_for_the_card_again_without_opening_it(self) -> None:
        test = _test()
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)
        await press(control._looks_right)
        test.open.reset_mock()

        await press(control._check_again)

        test.open.assert_not_awaited()
        self.assertEqual(control.phase, "idle")
        self.assertIn(t("manager.screens.test_card"), _buttons(body))

    async def test_closing_the_card_goes_back_to_the_start_and_keeps_the_answer(self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test, rotation="180")
        await press(control._open_card)

        await press(control._close_card)

        test.close.assert_awaited_once_with()
        self.assertEqual((control.phase, control.rotation), ("idle", 180))

    async def test_the_page_says_the_card_is_still_wanted(self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)

        await control._hold()

        test.hold.assert_awaited_once_with()
        self.assertEqual(control.phase, "checking")

    async def test_a_card_that_has_gone_asks_whether_it_faced_the_right_way_up(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)

        await control._hold()

        self.assertEqual(control.phase, "asking")
        self.assertIn(t("manager.screens.rotation_asked"), _labels(body))
        self.assertEqual(_buttons(body), [t("manager.screens.turn_left"),
                                          t("manager.screens.turn_around"),
                                          t("manager.screens.turn_right"),
                                          t("manager.screens.looks_right"),
                                          t("manager.screens.reopen_card")])

    async def test_closing_the_card_from_the_panel_does_not_ask(self) -> None:
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=_test())
        await press(control._open_card)

        await press(control._close_card)

        self.assertNotIn(t("manager.screens.rotation_asked"), _labels(body))

    async def test_a_turn_with_the_card_gone_turns_the_answer_and_opens_the_card_turned(
            self) -> None:
        for start, degrees, total in ((0, 90, 90), (90, 90, 180), (270, 90, 0)):
            with self.subTest(start=start, degrees=degrees):
                point = AsyncMock(return_value=True)
                test = _test(hold=AsyncMock(return_value=False))
                control, _ = self._drawn({"playfield": 2}, point=point, test=test,
                                         rotation=str(start))
                await press(control._open_card)
                await control._hold()
                test.open.reset_mock()

                await press(control._turn_by, degrees)

                point.assert_awaited_once_with(total, TALL)
                test.open.assert_awaited_once_with(TALL, total)
                test.turn.assert_not_awaited()
                self.assertEqual((control.phase, control.rotation), ("checking", total))

    async def test_a_turn_with_the_card_gone_that_was_not_kept_opens_nothing(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(return_value=False),
                                 test=test, rotation="90")
        await press(control._open_card)
        await control._hold()
        test.open.reset_mock()

        await press(control._turn_by, 90)

        test.open.assert_not_awaited()
        self.assertEqual((control.phase, control.rotation), ("asking", 90))

    async def test_a_turn_that_cannot_reopen_the_card_leaves_the_question_asked(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(return_value=True),
                                    test=test)
        await press(control._open_card)
        await control._hold()
        test.open.return_value = False

        await press(control._turn_by, 90)

        self.assertEqual((control.phase, control.rotation), ("asking", 90))
        self.assertIn(t("manager.screens.reopen_card"), _buttons(body))

    async def test_show_test_card_again_opens_it_turned_as_it_was_left(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test, rotation="180")
        await press(control._open_card)
        await control._hold()
        test.open.reset_mock()
        test.hold.return_value = True

        await press(control._open_card)

        test.open.assert_awaited_once_with(TALL, 180)
        self.assertEqual(control.phase, "checking")

    async def test_while_the_card_is_opening_again_the_turns_and_looks_right_are_off(
            self) -> None:
        release = asyncio.Event()

        async def slow(_screen: Screen, _rotation: int) -> bool:
            await release.wait()
            return True

        test = _test(hold=AsyncMock(return_value=False))
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(return_value=True),
                                    test=test)
        await press(control._open_card)
        await control._hold()
        test.open.side_effect = slow

        opening = asyncio.ensure_future(press(control._open_card))
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        self.assertEqual(_buttons(body)[-1], t("manager.screens.test_card_opening"))
        for words in ("manager.screens.turn_left", "manager.screens.turn_around",
                      "manager.screens.turn_right", "manager.screens.looks_right",
                      "manager.screens.test_card_opening"):
            with self.subTest(words=words):
                self.assertFalse(_button(body, t(words)).enabled)
        release.set()
        await opening

    async def test_an_answer_given_while_a_turn_is_being_kept_is_not_undone_by_the_card(
            self) -> None:
        release = asyncio.Event()

        async def slow(_total: int, _screen: Screen) -> bool:
            await release.wait()
            return True

        test = _test(hold=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(side_effect=slow), test=test)
        await press(control._open_card)
        await control._hold()
        test.open.reset_mock()

        turning = asyncio.ensure_future(press(control._turn_by, 90))
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        await press(control._looks_right)
        release.set()
        await turning

        test.open.assert_not_awaited()
        self.assertEqual(control.phase, "done")

    async def test_looks_right_with_the_card_gone_is_the_answer(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, body = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)
        await control._hold()

        await press(control._looks_right)

        self.assertEqual(control.phase, "done")
        self.assertEqual(_buttons(body), [t("manager.screens.check_again")])

    async def test_moving_the_playfield_with_the_card_gone_asks_again_from_the_start(
            self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2, "backglass": None}, point=AsyncMock(),
                                 test=test)
        await press(control._open_card)
        await control._hold()

        await press(control._put, "playfield", 1)

        self.assertEqual(control.phase, "idle")

    async def test_a_card_that_has_gone_after_the_answer_leaves_the_answer(self) -> None:
        test = _test(hold=AsyncMock(return_value=False))
        control, _ = self._drawn({"playfield": 2}, point=AsyncMock(), test=test)
        await press(control._open_card)
        await press(control._looks_right)

        await control._hold()

        self.assertEqual(control.phase, "done")

    async def test_moving_the_playfield_while_checking_takes_the_card_down(self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2, "backglass": None}, point=AsyncMock(),
                                 test=test)
        await press(control._open_card)

        await press(control._put, "playfield", 1)

        test.close.assert_awaited_once_with()
        self.assertEqual(control.phase, "idle")

    async def test_moving_the_playfield_after_the_answer_asks_again(self) -> None:
        control, _ = self._drawn({"playfield": 2, "backglass": None}, point=AsyncMock(),
                                 test=_test())
        await press(control._open_card)
        await press(control._looks_right)

        await press(control._put, "playfield", 1)

        self.assertEqual(control.phase, "idle")

    async def test_moving_another_part_while_checking_leaves_the_card_up(self) -> None:
        test = _test()
        control, _ = self._drawn({"playfield": 2, "backglass": None}, point=AsyncMock(),
                                 test=test)
        await press(control._open_card)

        await press(control._put, "backglass", 1)

        test.close.assert_not_awaited()
        self.assertEqual(control.phase, "checking")

    async def test_show_numbers_is_given_what_is_where_and_can_be_pressed_again(self) -> None:
        show = AsyncMock()
        control, _ = self._drawn({"playfield": 1}, show=show)

        await press(control._show)

        show.assert_awaited_once_with({"playfield": 1})
        self.assertFalse(control.showing)

    async def test_show_numbers_that_fails_still_lets_it_be_pressed_again(self) -> None:
        control, _ = self._drawn({}, show=AsyncMock(side_effect=RuntimeError("no")))

        with self.assertRaises(RuntimeError):
            await press(control._show)

        self.assertFalse(control.showing)

    async def test_matching_asks_the_page_to_put_the_parts_right(self) -> None:
        match = AsyncMock()
        control, _ = self._drawn({"playfield": 1, "backglass": 2}, parts=picture.vpx_parts(),
                                 differs=["backglass"], match=match)

        await press(control._match)

        match.assert_awaited_once_with()

    async def test_a_part_put_inside_is_reported_as_inside_and_drawn_so(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": 2, "playfield": 1}, parts=picture.vpx_parts(),
                                 move=move)

        await press(control._put, "backglass", picture.INSIDE)

        move.assert_awaited_once_with({"backglass": picture.INSIDE})
        self.assertEqual(control.on["backglass"], picture.INSIDE)

    async def test_a_swap_does_not_leave_the_playfield_inside_itself(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"backglass": picture.INSIDE, "playfield": 1},
                                 parts=picture.vpx_parts(), move=move)

        await press(control._put, "backglass", 1)

        move.assert_awaited_once_with({"backglass": 1, "playfield": None})

    async def test_a_swap_leaves_a_part_that_can_be_inside_where_it_is(self) -> None:
        move = AsyncMock(return_value=True)
        control, _ = self._drawn({"scoreview": picture.INSIDE, "backglass": 1},
                                 parts=picture.vpx_parts(), move=move)

        await press(control._put, "scoreview", 1)

        move.assert_awaited_once_with({"scoreview": 1, "backglass": picture.INSIDE})


if __name__ == "__main__":
    unittest.main()
