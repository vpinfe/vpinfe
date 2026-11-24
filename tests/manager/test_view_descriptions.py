"""Every built-in view says what it is for, including the ones built at render time."""

from __future__ import annotations

import ast
import pathlib
import unittest

from common import i18n
from manager.devices import devices
from manager.frontend import launchers, themes
from manager.kit import views as views_module
from manager.library import assets, collections, games, media, tageditor
from tests.support import trees

REPO = pathlib.Path(__file__).resolve().parent.parent.parent

DECLARED = {
    "games": games.GAME_VIEWS,
    "tables": games.TABLE_VIEWS,
    "media": media.VIEWS,
    "assets": assets.VIEWS,
    "devices": devices.VIEWS,
    "collections": collections.COLLECTION_VIEWS,
    "launchers": launchers.LAUNCHER_VIEWS,
}


class BuiltinViewsAreDescribed(unittest.TestCase):

    def test_every_declared_view_has_one_unless_its_name_says_it(self):
        named_enough = views_module.builtin_id("manager.view.everything")
        bare = [f"{grid}: {view.name!r} has no description"
                for grid, presets in DECLARED.items()
                for view in views_module.builtins(presets)
                if not view.help.strip() and view.id != named_enough]
        self.assertEqual(bare, [], "\n" + "\n".join(bare))

    def test_it_found_the_views(self):
        """An empty sweep passes and measures nothing, which reads the same as clean."""
        seen = sum(len(views_module.builtins(p)) for p in DECLARED.values())
        self.assertGreater(seen, 15)

    def test_a_view_built_at_render_time_is_a_preset(self):
        """A view built from what the library holds is a `Preset`, not a plain list."""
        tree = trees.tree_for(REPO / "manager/library/games.py")
        plain = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Assign) and len(node.targets) == 1
                    and getattr(node.targets[0], "id", "") == "presets"):
                continue
            if not isinstance(node.value, ast.Dict):
                continue
            for key, value in zip(node.value.keys, node.value.values, strict=True):
                if key is None:
                    continue
                if not (isinstance(value, ast.Call)
                        and getattr(value.func, "attr", "") == "Preset"):
                    plain.append(f"manager/library/games.py:{node.lineno}: a view built here "
                                 "is a plain list, so it has no description")
        self.assertEqual(plain, [], "\n" + "\n".join(plain))


class BuiltinViewsAreKeptByKey(unittest.TestCase):
    """The view last showing is remembered by id, so an id holding the words on screen
    is forgotten when the language changes."""

    GRIDS = {**DECLARED, "themes": themes.VIEWS, "tags": tageditor.VIEWS}

    def test_every_id_is_a_catalog_key(self):
        worded = [f"{grid}: {view.id}"
                  for grid, presets in self.GRIDS.items()
                  for view in views_module.builtins(presets)
                  if not i18n.first_key(view.id.removeprefix(views_module.builtin_id("")))]
        self.assertEqual(worded, [], "\n" + "\n".join(worded))

    def test_the_name_is_read_in_the_language_set(self):
        self.addCleanup(i18n.set_language, i18n.language())
        english = [view.name for view in views_module.builtins(media.VIEWS)]
        i18n.set_language("qps")

        self.assertNotEqual(english, [view.name for view in views_module.builtins(media.VIEWS)])

    def test_a_view_named_for_its_panel_section_is_one_the_grid_declares(self):
        declared = {view.id for view in views_module.builtins(games.GAME_VIEWS)}
        self.assertLessEqual(set(games.VIEW_SECTIONS), declared)


if __name__ == "__main__":
    unittest.main()
