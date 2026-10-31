"""Which selected media rows a turn applies to, and what it says when it ends."""

from __future__ import annotations

import unittest

from common.games import media_rotate
from common.i18n import t
from manager.library import media_rotate as page


def row(path: str, *, present: bool = True, game_id: str = "g1") -> dict:
    return {"game_id": game_id, "path": path, "present": present}


class TurnableTests(unittest.TestCase):
    def test_only_pictures_and_videos_that_are_there_are_turned(self) -> None:
        picked = [row("medias/a.png"), row("medias/b.JPG"), row("medias/c.mp4"),
                  row("medias/d.mp3"), row("medias/e.png", present=False), row("")]

        self.assertEqual(page.turnable(picked),
                         [("g1", "medias/a.png"), ("g1", "medias/b.JPG"),
                          ("g1", "medias/c.mp4")])

    def test_a_file_shown_on_two_rows_is_turned_once(self) -> None:
        picked = [row("medias/a.png"), row("medias/a.png"), row("medias/a.png", game_id="g2")]

        self.assertEqual(page.turnable(picked), [("g1", "medias/a.png"), ("g2", "medias/a.png")])


class OutcomeTests(unittest.TestCase):
    def test_a_clean_turn_is_said_plainly(self) -> None:
        self.assertEqual(page.outcome({"turned": 3, "skipped": []}),
                         (t("manager.media_rotate.rotated", count=3), "positive"))

    def test_skipped_files_make_it_a_warning_with_the_count(self) -> None:
        said, level = page.outcome({"turned": 1, "skipped": [{"reason": "x"}, {"reason": "y"}]})

        self.assertEqual(level, "warning")
        self.assertIn("2", said)


class MenuTests(unittest.TestCase):
    def test_every_way_the_menu_offers_is_one_the_service_knows_and_has_words(self) -> None:
        self.assertEqual([way for way, _ in page.MENU], list(media_rotate.TURNS))
        for _, word in page.MENU:
            self.assertNotEqual(t(word), word)


if __name__ == "__main__":
    unittest.main()
