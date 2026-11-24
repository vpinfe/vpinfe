"""The notice that offers to move what an upgrade left behind: which rows it acts on and when
it shrinks to a line."""

from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from common.i18n import t
from manager.kit import leftover_ids
from manager.kit.data import Library
from manager.overview import needs
from manager.workbench import leftovers


def item(key, action="move", files=0):
    return {"key": key, "action": action, "files": files}


class WhichRowsItActsOn(unittest.TestCase):
    def test_a_move_and_a_turn_on_are_acted_on(self):
        self.assertTrue(leftover_ids.acts(item("a")))
        self.assertTrue(leftover_ids.acts(item("a", "turn_on")))

    def test_a_box_is_acted_on_only_where_a_file_would_take_it(self):
        self.assertFalse(leftover_ids.acts(item("a", "place", 0)))
        self.assertTrue(leftover_ids.acts(item("a", "place", 3)))

    def test_a_name_alone_is_never_acted_on(self):
        self.assertFalse(leftover_ids.acts(item("a", "name")))

    def test_the_ids_are_the_launcher_and_the_key(self):
        self.assertEqual(("vpx:a", "vpx:c"),
                         leftover_ids.of("vpx", [item("a"), item("b", "name"), item("c")]))


class WhenItShrinksToALine(unittest.TestCase):
    def test_every_id_in_the_record_sets_it_aside(self):
        self.assertTrue(leftovers.is_set_aside(("vpx:a", "vpx:b"),
                                               {leftovers.NOTICE: ["vpx:b", "vpx:a", "x:z"]}))

    def test_one_id_the_record_lacks_draws_it_whole(self):
        self.assertFalse(leftovers.is_set_aside(("vpx:a", "vpx:b"),
                                                {leftovers.NOTICE: ["vpx:a"]}))

    def test_nothing_listed_is_never_set_aside(self):
        self.assertFalse(leftovers.is_set_aside((), {leftovers.NOTICE: ["vpx:a"]}))

    def test_the_row_overview_draws_is_the_one_not_now_sets_aside(self):
        self.assertIn(leftovers.ROW, needs.ORDER)

    def test_overview_keeps_the_notice_s_record_it_does_not_count(self):
        arranged = needs.arrange([], {leftovers.NOTICE: ["vpx:a"], "unmatched": ["g"]}, set())

        self.assertEqual({leftovers.NOTICE: ["vpx:a"]}, arranged.record)


class Page:
    """A launcher's page drawn against a library with a real record behind it."""

    def __init__(self, items, stored=None):
        self.stored = {row: list(ids) for row, ids in (stored or {}).items()}
        self.items = items
        self.rebuilt = 0
        self.answer: dict[str, Any] = {}
        self.said: list[Any] = []
        self.labels: list[str] = []
        self.buttons: dict[str, Any] = {}
        client = SimpleNamespace(
            launcher_leftovers=lambda _id: {"items": self.items},
            move_launcher_leftovers=lambda _id: self.answer,
            dismissed=lambda: {row: list(ids) for row, ids in self.stored.items()},
            put_dismissed=self._put)
        self.library = Library.__new__(Library)
        self.library._client = client
        self.library._dismissed = {}
        self.library._dismissed_read = False
        self.state: dict[str, Any] = {}

    def _put(self, row, ids):
        if ids:
            self.stored[row] = list(ids)
        else:
            self.stored.pop(row, None)

    async def rebuild(self):
        self.rebuilt += 1

    def draw(self, launcher_id="vpx"):
        self.labels, self.buttons = [], {}
        ui = MagicMock()
        ui.label.side_effect = lambda text="", *a, **k: self.labels.append(text) or MagicMock()

        def action(label, fn=None, **_kw):
            self.buttons[label] = fn
            return lambda: None

        async def io_bound(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        context = {"library": self.library, "state": self.state, "rebuild": self.rebuild,
                   "launcher": {"launcher_id": launcher_id, "has_config": True}}
        with patch.object(leftovers, "ui", ui), \
                patch.object(leftovers.panel, "action", action), \
                patch.object(leftovers, "on_page", lambda handler: handler), \
                patch.object(leftovers.run, "io_bound", io_bound), \
                patch.object(leftovers.offload.run, "io_bound", io_bound), \
                patch.object(leftovers.undo, "offer"):
            asyncio.run(leftovers.notice(context))

    def press(self, label):
        async def io_bound(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        ui = MagicMock()
        ui.notify.side_effect = lambda *a, **k: self.said.append((a, k))
        with patch.object(leftovers, "ui", ui), \
                patch.object(leftovers.undo, "offer"), \
                patch.object(leftovers, "on_page", lambda handler: handler), \
                patch.object(leftovers.run, "io_bound", io_bound):
            asyncio.run(self.buttons[label]())


MOVES = [item("Plugin.ScoreView.Enable"), item("Plugin.B2SLegacy.B2SDualMode")]
NAMED = [item("Plugin.B2SLegacy.B2SHideDMD", "name")]
ONE_LINE = t("manager.leftovers.line", count=2)


class WhatTheNoticeDraws(unittest.TestCase):
    def test_a_launcher_with_nothing_left_behind_draws_nothing(self):
        page = Page([])
        page.draw()

        self.assertEqual((page.labels, page.buttons), ([], {}))

    def test_the_full_form_offers_move_and_not_now(self):
        page = Page(MOVES)
        page.draw()

        self.assertEqual({t("manager.leftovers.move"), t("manager.leftovers.not_now")},
                         set(page.buttons))
        self.assertIn(t("manager.leftovers.heading"), page.labels)

    def test_a_box_left_in_the_settings_for_the_undo_journal_says_so(self):
        page = Page(MOVES)
        page.answer = {"moved": ["a"], "undo": "",
                       "held_back": [{"key": "k", "files": [], "limit": 32}]}
        page.draw()
        page.press(t("manager.leftovers.move"))

        self.assertEqual([(a[0], k.get("caption")) for a, k in page.said],
                         [(t("manager.leftovers.held_over", limit=32), None)])

    def test_not_now_leaves_one_line_with_review_and_the_overview_row_set_aside(self):
        page = Page(MOVES)
        page.draw()
        page.press(t("manager.leftovers.not_now"))
        page.draw()

        self.assertEqual(page.labels, [ONE_LINE])
        self.assertEqual(set(page.buttons), {t("manager.leftovers.review")})
        self.assertEqual(page.stored[leftovers.ROW], ["vpx:Plugin.B2SLegacy.B2SDualMode",
                                                      "vpx:Plugin.ScoreView.Enable"])
        self.assertEqual(page.rebuilt, 1)

    def test_the_line_reads_as_the_design_words_it(self):
        self.assertEqual(ONE_LINE, "2 settings this version no longer reads")

    def test_review_opens_it_again_and_leaves_the_dismissal_alone(self):
        page = Page(MOVES)
        page.draw()
        page.press(t("manager.leftovers.not_now"))
        page.draw()
        page.press(t("manager.leftovers.review"))
        page.draw()

        self.assertIn(t("manager.leftovers.move"), page.buttons)
        self.assertEqual(len(page.stored[leftovers.NOTICE]), 2)

    def test_a_later_upgrade_that_leaves_more_draws_it_whole_again(self):
        page = Page(MOVES)
        page.draw()
        page.press(t("manager.leftovers.not_now"))
        page.items = [*MOVES, item("Plugin.B2SLegacy.B2SHideGrill")]
        page.draw()

        self.assertIn(t("manager.leftovers.move"), page.buttons)

    def test_a_notice_that_only_names_can_still_be_set_aside(self):
        page = Page(NAMED)
        page.draw()

        self.assertEqual(set(page.buttons), {t("manager.leftovers.not_now")})
        page.press(t("manager.leftovers.not_now"))
        page.draw()

        self.assertEqual(page.labels, [t("manager.leftovers.line", count=1)])
        self.assertNotIn(leftovers.ROW, page.stored)

    def test_a_page_opened_directly_reads_the_record_before_it_draws(self):
        page = Page(MOVES, {leftovers.NOTICE: ["vpx:Plugin.ScoreView.Enable",
                                               "vpx:Plugin.B2SLegacy.B2SDualMode"]})
        self.assertEqual(page.library.dismissed(), {})
        page.draw()

        self.assertEqual(page.labels, [ONE_LINE])

    def test_not_now_keeps_what_another_launcher_was_dismissed_over(self):
        page = Page(MOVES, {leftovers.ROW: ["other:k"], leftovers.NOTICE: ["other:k"]})
        page.draw()
        page.press(t("manager.leftovers.not_now"))

        self.assertIn("other:k", page.stored[leftovers.ROW])
        self.assertIn("other:k", page.stored[leftovers.NOTICE])
        self.assertIn("vpx:Plugin.ScoreView.Enable", page.stored[leftovers.ROW])

    def test_ids_of_this_launcher_that_name_nothing_listed_are_dropped(self):
        page = Page(MOVES, {leftovers.NOTICE: ["vpx:gone", "other:k",
                                               "vpx:Plugin.ScoreView.Enable"]})
        page.draw()

        self.assertEqual(sorted(page.stored[leftovers.NOTICE]),
                         ["other:k", "vpx:Plugin.ScoreView.Enable"])


if __name__ == "__main__":
    unittest.main()
