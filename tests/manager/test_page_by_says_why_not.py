"""A collection's Page By lists the group choice on every order, and says why where it is
greyed."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import patch

from nicegui import ui

from common.i18n import t
from manager.kit import panel
from manager.workbench import collection

_PAGE = ui.element()


def _asked(row: dict[str, Any], *, draft: dict[str, Any] | None = None,
           settings: dict[str, Any] | None = None
           ) -> tuple[dict[str, str], str, dict[str, str]]:
    """The options, the value shown and the reasons that `_page_by` hands the select."""
    context = {"collection": row, "state": {}, "draft": draft or {},
               "settings": settings or {}}
    taken: list[tuple[dict[str, str], str, dict[str, str]]] = []

    def select(options: dict[str, str], value: str, _changed: Any, *,
               unavailable: dict[str, str]) -> Any:
        taken.append((options, value, unavailable))
        return lambda: None

    with patch.object(panel, "select", new=select):
        collection._page_by(context, row)
    return taken[0]


class PageBy(unittest.TestCase):
    def test_an_order_with_groups_offers_every_choice(self) -> None:
        for order in ("title", "year", "rating"):
            with self.subTest(order):
                options, _, unavailable = _asked({"name": "Mine", "order_by": order})

                self.assertEqual(["", "sort", "count"], list(options))
                self.assertEqual({}, unavailable)

    def test_an_order_without_groups_lists_the_group_choice_and_names_the_order(self) -> None:
        for order, name in (("last_played", "order.by.last_played"),
                            ("play_count", "order.by.play_count"),
                            ("manual", "order.by.manual")):
            with self.subTest(order):
                options, _, unavailable = _asked({"name": "Mine", "order_by": order})

                self.assertEqual(["", "sort", "count"], list(options))
                self.assertEqual(
                    {"sort": t("manager.workbench.page_by.no_groups", order=t(name))},
                    unavailable)

    def test_a_stored_group_on_an_order_without_groups_is_shown_as_stored(self) -> None:
        _, shown, unavailable = _asked(
            {"name": "Mine", "order_by": "last_played", "paging_group": "sort"})

        self.assertEqual("sort", shown)
        self.assertIn("sort", unavailable)

    def test_nothing_stored_shows_what_the_setting_says(self) -> None:
        options, shown, _ = _asked({"name": "Mine", "order_by": "title"},
                                   settings={"behavior": {"paging_group": "count"}})

        self.assertEqual("", shown)
        self.assertEqual(t("manager.workbench.page_by.same",
                           choice=t("config.behavior.paging_group.choice.count")), options[""])

    def test_a_stored_order_the_menu_dropped_is_judged_as_the_order_bar_shows_it(self) -> None:
        _, _, unavailable = _asked({"name": "Mine", "order_by": "manual", "type": "filter"})

        self.assertEqual({}, unavailable)

    def test_a_drafted_order_is_the_one_judged(self) -> None:
        _, _, unavailable = _asked({"name": "Mine", "order_by": "title"},
                                   draft={"order": {"order_by": "last_played"}})

        self.assertEqual(
            {"sort": t("manager.workbench.page_by.no_groups",
                       order=t("order.by.last_played"))}, unavailable)


class AReasonedSelect(unittest.TestCase):
    def _options(self) -> dict[str, dict[str, Any]]:
        with _PAGE:
            one = panel.ReasonedSelect({"a": "A", "b": "B"}, value="a",
                                       reasons={"b": "Because"})
        return {str(o["label"]): o for o in one._props["options"]}

    def test_an_option_with_a_reason_is_greyed_and_carries_it(self) -> None:
        held = self._options()

        self.assertTrue(held["B"]["disable"])
        self.assertEqual("Because", held["B"]["reason"])

    def test_an_option_without_one_can_be_picked(self) -> None:
        held = self._options()

        self.assertFalse(held["A"]["disable"])
        self.assertEqual("", held["A"]["reason"])


class TheSelect(unittest.TestCase):
    def test_unavailable_greys_the_option_in_the_control_it_draws(self) -> None:
        made: list[Any] = []

        with _PAGE:
            panel.select({"a": "A", "b": "B"}, "a", lambda _event: None,
                         unavailable={"b": "Because"}, on_control=made.append)()

        held = {str(o["label"]): o for o in made[0]._props["options"]}
        self.assertTrue(held["B"]["disable"])
        self.assertFalse(held["A"]["disable"])

    def test_describing_and_greying_cannot_be_asked_together(self) -> None:
        with self.assertRaises(ValueError):
            panel.select({"a": "A"}, "a", lambda _event: None,
                         describes={"A": "The first"}, unavailable={"a": "Because"})


if __name__ == "__main__":
    unittest.main()
