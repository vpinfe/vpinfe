"""Drawings by name: what a column declares, what a view records, what the grid is sent."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from manager.kit import grid, renderers, views
from manager.library import games

CATALOG = json.loads((Path(__file__).resolve().parents[2]
                      / "common/i18n/catalogs/en.json").read_text(encoding="utf-8"))


class TheVocabulary(unittest.TestCase):
    def test_every_drawing_has_words_a_person_reads(self) -> None:
        for one in renderers.REGISTRY.values():
            with self.subTest(one.name):
                self.assertIn(one.label, CATALOG)

    def test_a_column_drawn_by_name_is_drawn_through_the_dispatcher(self) -> None:
        column = renderers.drawable("mark", "picture")
        self.assertEqual(renderers.DISPATCH, column[":cellRenderer"])
        self.assertEqual({"drawn": "mark"}, column["cellRendererParams"])
        self.assertEqual(("mark", "picture"), renderers.choices(column))

    def test_the_choices_never_reach_the_grid(self) -> None:
        sent = grid.for_grid([{"field": "media_wheel", **renderers.drawable("mark")}])
        self.assertNotIn(renderers.CHOICES_KEY, sent[0])

    def test_games_media_can_be_drawn_as_pictures(self) -> None:
        column = games.media_columns(["wheel"])[0]
        self.assertEqual(("mark", "picture"), renderers.choices(column))


class TheRowHeight(unittest.TestCase):
    def test_it_is_what_the_tallest_drawing_on_screen_needs(self) -> None:
        drawing = {"media_wheel": "picture"}
        self.assertEqual(74, renderers.row_px(drawing, ["media_wheel"], 56))

    def test_a_drawing_on_a_hidden_column_asks_for_nothing(self) -> None:
        self.assertEqual(56, renderers.row_px({"media_wheel": "picture"}, ["name"], 56))


class AView(unittest.TestCase):
    def test_it_keeps_how_it_draws_across_a_save(self) -> None:
        view = views.View(id="view:x", name="Art", drawn={"media_wheel": "picture"})
        self.assertEqual(view.drawn, views.from_record(views.to_record(view)).drawn)

    def test_a_view_from_an_older_build_draws_as_its_columns_say(self) -> None:
        self.assertEqual({}, views.from_record({"id": "view:x", "name": "Old"}).drawn)

    def test_drawing_differently_is_drift(self) -> None:
        view = views.View(id=views.builtin_id("manager.view.media"), name="Media",
                          columns=("name",))
        self.assertFalse(views.differs(view, ("name",), (), {}, {}))
        self.assertTrue(views.differs(view, ("name",), (), {}, {"media_wheel": "picture"}))

    def test_a_built_in_can_declare_one(self) -> None:
        built = views.builtins({"Art": views.Preset(drawn={"media_wheel": "picture"})})
        self.assertEqual({"media_wheel": "picture"}, built[0].drawn)


if __name__ == "__main__":
    unittest.main()
