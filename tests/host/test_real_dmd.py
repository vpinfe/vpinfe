from __future__ import annotations

import configparser
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import cast

from common.config.config_store import ConfigStore
from common.games.game import Game
from common.host import real_dmd


def _game(**paths: str) -> Game:
    found = {"real_dmd_video_path": "", "real_dmd_color_video_path": "",
             "real_dmd_image_path": "", "real_dmd_color_image_path": ""}
    found.update(paths)
    return cast(Game, SimpleNamespace(**found))


def _config(priority: str) -> ConfigStore:
    parser = configparser.ConfigParser()
    parser.read_dict({"Media": {"realdmdmediapriority": priority}})
    return cast(ConfigStore, parser)


class ArtTests(unittest.TestCase):
    def test_color_comes_first_unless_standard_is_chosen(self) -> None:
        game = _game(real_dmd_video_path="/art/s.mp4", real_dmd_color_video_path="/art/c.mp4")

        self.assertEqual(real_dmd.get_realdmd_video_for_game(game), Path("/art/c.mp4").resolve())
        self.assertEqual(real_dmd.get_realdmd_video_for_game(game, _config("color")),
                         Path("/art/c.mp4").resolve())

    def test_with_standard_first_the_standard_video_is_picked(self) -> None:
        game = _game(real_dmd_video_path="/art/s.mp4", real_dmd_color_video_path="/art/c.mp4")

        self.assertEqual(real_dmd.get_realdmd_video_for_game(game, _config("standard")),
                         Path("/art/s.mp4").resolve())

    def test_the_other_video_is_used_when_the_preferred_one_is_missing(self) -> None:
        game = _game(real_dmd_color_video_path="/art/c.mp4")

        self.assertEqual(real_dmd.get_realdmd_video_for_game(game, _config("standard")),
                         Path("/art/c.mp4").resolve())

    def test_a_table_with_no_video_has_none(self) -> None:
        self.assertIsNone(real_dmd.get_realdmd_video_for_game(_game()))

    def test_a_table_has_the_video_and_the_picture_to_fall_back_on(self) -> None:
        game = _game(real_dmd_color_video_path="/art/c.mp4", real_dmd_image_path="/art/s.png")

        art = real_dmd.get_realdmd_art_for_game(game)

        self.assertEqual(art.video, Path("/art/c.mp4").resolve())
        self.assertEqual(art.image, Path("/art/s.png").resolve())


class UpdaterTests(unittest.TestCase):
    def _updater(self, with_video: bool = True):
        seen: list[tuple] = []
        done = threading.Event()

        def image(_config, path) -> bool:
            seen.append(("image", path))
            done.set()
            return True

        def video(_config, path, fallback) -> bool:
            seen.append(("video", path, fallback))
            done.set()
            return True

        updater = real_dmd.RealDmdUpdater(object(), "t", image,  # type: ignore[arg-type]
                                          video if with_video else None)
        return updater, seen, done

    def test_a_video_goes_to_the_player_with_its_picture(self) -> None:
        updater, seen, done = self._updater()

        updater.queue_update("G", real_dmd.RealDmdArt(video=Path("/a.mp4"), image=Path("/a.png")))
        self.assertTrue(done.wait(5))

        self.assertEqual(seen, [("video", Path("/a.mp4"), Path("/a.png"))])

    def test_a_picture_alone_goes_to_show_image(self) -> None:
        updater, seen, done = self._updater()

        updater.queue_update("G", real_dmd.RealDmdArt(image=Path("/a.png")))
        self.assertTrue(done.wait(5))

        self.assertEqual(seen, [("image", Path("/a.png"))])

    def test_it_is_played_again_when_asked(self) -> None:
        updater, seen, done = self._updater()
        updater.queue_update("G", real_dmd.RealDmdArt(video=Path("/a.mp4")))
        self.assertTrue(done.wait(5))
        done.clear()

        updater.show_again()
        self.assertTrue(done.wait(5))

        self.assertEqual([one[0] for one in seen], ["video", "video"])

    def test_without_a_player_the_picture_shows(self) -> None:
        updater, seen, done = self._updater(with_video=False)

        updater.queue_update("G", real_dmd.RealDmdArt(video=Path("/a.mp4"), image=Path("/a.png")))
        self.assertTrue(done.wait(5))

        self.assertEqual(seen, [("image", Path("/a.png"))])


if __name__ == "__main__":
    unittest.main()
