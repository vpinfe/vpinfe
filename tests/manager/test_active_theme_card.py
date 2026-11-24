"""Settings > Themes, Active Theme: the theme as the Themes grid's name cell draws it."""

from __future__ import annotations

import unittest
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

from common.config import config_schema
from manager.frontend import theme_card, themes
from manager.kit import offload, thumbnails
from manager.settings import settings

THEME = {"key": "neon", "name": "Neon", "author": "someone", "installed_version": "1.2",
         "version": "1.2", "preview": "/themes/neon/preview.png", "preview_version": "ab12",
         "installed": True}


class SaidTests(unittest.TestCase):
    def test_the_line_under_the_name_is_the_one_the_grid_draws(self) -> None:
        for theme in (THEME, {**THEME, "update_available": True, "version": "1.3"},
                      {**THEME, "author": ""}, {"key": "bare"}):
            with self.subTest(theme=theme):
                self.assertEqual(themes._row({**theme, "registry": ""})["said"],
                                 theme_card.said(theme))

    def test_a_waiting_update_reads_as_the_version_held_and_the_one_on_offer(self) -> None:
        self.assertEqual("someone · 1.2 → 1.3", theme_card.said(
            {**THEME, "update_available": True, "version": "1.3"}))


class FrameTests(unittest.TestCase):
    def test_the_preview_frame_is_the_grids_and_not_the_lists(self) -> None:
        drawn = thumbnails.frame_html("/p.png", "palette", size=thumbnails.PREVIEW)

        self.assertIn('class="manager-cell-art-box"', drawn)
        self.assertNotIn("--list", drawn)

    def test_a_frame_with_no_picture_holds_the_glyph(self) -> None:
        drawn = thumbnails.frame_html("", "palette", size=thumbnails.PREVIEW)

        self.assertIn("manager-cell-art-box--glyph", drawn)
        self.assertIn("palette", drawn)

    def test_the_lists_frame_is_still_the_default(self) -> None:
        self.assertIn("manager-cell-art-box--list", thumbnails.frame_html("/p.png", "x"))


class DrawnTests(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = self.enterContext(patch.object(theme_card, "ui"))
        self.beside = self.enterContext(patch.object(theme_card.thumbnails, "beside"))
        self.link = self.enterContext(patch.object(theme_card.panel, "link"))

    def lines(self) -> list[str]:
        return [str(one.args[0]) for one in self.ui.label.call_args_list]

    def test_the_theme_is_its_name_then_author_and_version_then_where_to_change_it(self) -> None:
        theme_card.active("neon", THEME)

        self.assertEqual(["Neon", "someone \u00b7 1.2"], self.lines())
        self.link.assert_called_once()
        self.assertEqual("/manager?view=themes", self.link.call_args.kwargs["to"])

    def test_the_picture_is_the_preview_frame(self) -> None:
        theme_card.active("neon", THEME)

        self.assertEqual(call("/api/v1/themes/neon/preview?size=256&v=ab12", theme_card.GLYPH,
                              size=thumbnails.PREVIEW), self.beside.call_args)

    def test_a_theme_with_no_preview_draws_the_glyph(self) -> None:
        theme_card.active("neon", {**THEME, "preview": ""})

        self.assertEqual("", self.beside.call_args.args[0])
        self.assertEqual(theme_card.GLYPH, self.beside.call_args.args[1])

    def test_a_theme_no_source_offers_draws_its_key_and_no_second_line(self) -> None:
        theme_card.active("gone", None)

        self.assertEqual(["gone"], self.lines())
        self.link.assert_called_once()


class EditorTests(unittest.IsolatedAsyncioTestCase):
    def draws(self, value: str, suggestions: dict[str, Any] | None) -> MagicMock:
        with patch.object(theme_card, "active") as active:
            settings._frontend_theme({}, value, lambda _: None,
                                     suggestions=suggestions)()
        return active

    def test_the_active_theme_is_drawn_from_its_entry(self) -> None:
        offered = {config_schema.EDITOR_FRONTEND_THEME: {"neon": THEME}}

        self.draws("neon", offered).assert_called_once_with("neon", THEME)

    def test_a_theme_no_source_offers_is_drawn_by_its_key(self) -> None:
        offered = {config_schema.EDITOR_FRONTEND_THEME: {"neon": THEME}}

        self.draws("gone", offered).assert_called_once_with("gone", None)
        self.draws("gone", None).assert_called_once_with("gone", None)

    async def test_the_page_asks_for_the_themes_only_where_an_editor_wants_them(self) -> None:
        library = MagicMock()
        library.themes.return_value = {"themes": [THEME, {"name": "keyless"}]}
        here = [{"name": "themes", "options": [
            {"key": "active", "editor": config_schema.EDITOR_FRONTEND_THEME}]}]
        elsewhere = [{"name": "manager", "options": [{"key": "x"}]}]
        with patch.object(offload, "io",
                          new=AsyncMock(side_effect=lambda call, *args: call(*args))):
            asked = await settings._suggestions(library, here, ("themes",))
            idle = await settings._suggestions(library, elsewhere, ("manager",))

        self.assertEqual({"neon": THEME}, asked[config_schema.EDITOR_FRONTEND_THEME])
        self.assertNotIn(config_schema.EDITOR_FRONTEND_THEME, idle)
