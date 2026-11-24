"""Where a dropped file goes is decided in Python from what the page reports, and every place
that means something by a drop says so by declaring it."""

from __future__ import annotations

import inspect
import re
import unittest

from manager.library import mediamap, uploads
from manager.workbench import game_media
from tests.support.project import REPO, project_files


class ThePageScriptDecidesNothing(unittest.TestCase):
    def test_it_names_no_slot_or_asset_column(self) -> None:
        for prefix in ("media_", "thumb_", "asset_"):
            with self.subTest(prefix=prefix):
                self.assertNotIn(prefix, uploads._DND_SCRIPT)

    def test_it_finds_a_declaration_by_the_nearest_element_that_has_one(self) -> None:
        self.assertIn("closest('[data-drop]')", uploads._DND_SCRIPT)

    def test_everything_the_handler_reads_off_the_event_is_sent_by_the_script(self) -> None:
        keys = set(re.findall(r'payload\.get\("(\w+)"\)', inspect.getsource(uploads._handle)))

        self.assertTrue(keys)
        for key in keys:
            with self.subTest(key=key):
                self.assertRegex(uploads._DND_SCRIPT, rf"\b{key}:")


class APlaceThatMeansSomethingByADropDeclaresIt(unittest.TestCase):
    def test_a_picture_tile_declares_its_slot(self) -> None:
        self.assertIn("uploads.declare(", inspect.getsource(mediamap._tile))

    def test_a_slot_in_the_workbench_declares_its_slot(self) -> None:
        self.assertIn("uploads.declare(", inspect.getsource(game_media._slot_art))


class NothingReadsTheAttributeItReplaced(unittest.TestCase):
    def test_no_file_in_the_project_names_it(self) -> None:
        this = REPO / "tests" / "invariants" / "test_drop_targets.py"
        named = [path.relative_to(REPO) for path in project_files("*.py", "*.js", "*.css")
                 if path != this and "data-drop-own" in path.read_text(errors="replace")]

        self.assertEqual(named, [])


if __name__ == "__main__":
    unittest.main()
