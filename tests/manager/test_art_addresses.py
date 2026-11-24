"""The address the Manager draws a picture from: the file, its size and its version."""

from __future__ import annotations

import unittest

from manager.kit import art, thumbnails
from manager.kit.data import _thumb
from manager.library import mediapages


class ArtAddressTests(unittest.TestCase):
    def test_a_picture_asks_for_its_size_under_its_version(self) -> None:
        self.assertEqual(art.media("G1", "wheel", version="abc", size=art.CELL),
                         "/api/v1/games/G1/media/wheel?size=256&v=abc")

    def test_a_video_is_never_asked_for_a_size(self) -> None:
        self.assertEqual(art.media("G1", "playfield_video", version="abc", size=art.CELL),
                         "/api/v1/games/G1/media/playfield_video?v=abc")

    def test_nothing_known_about_it_is_the_plain_address(self) -> None:
        self.assertEqual(art.media("G1", "wheel"), "/api/v1/games/G1/media/wheel")

    def test_a_table_s_file_is_its_own_address(self) -> None:
        self.assertEqual(art.media("G1", "wheel", "tbl 1", size=art.PANEL),
                         "/api/v1/games/G1/tables/tbl%201/media/wheel?size=1024")

    def test_a_later_page_is_asked_for_by_number_and_under_no_version(self) -> None:
        self.assertEqual(art.media("G1", "flyer", version="abc", size=art.CELL, page=2),
                         "/api/v1/games/G1/media/flyer?size=256&page=2")

    def test_page_one_is_the_address_without_a_page(self) -> None:
        self.assertEqual(art.media("G1", "flyer", version="abc", page=1),
                         "/api/v1/games/G1/media/flyer?v=abc")

    def test_every_page_of_a_paged_slot_has_an_address_and_a_kind_that_does_not_has_none(
            self) -> None:
        entry = {"pages": 3, "version": "abc"}

        self.assertEqual(mediapages.addresses("G1", "flyer", "", entry),
                         ["/api/v1/games/G1/media/flyer?v=abc",
                          "/api/v1/games/G1/media/flyer?page=2",
                          "/api/v1/games/G1/media/flyer?page=3"])
        self.assertEqual(mediapages.addresses("G1", "wheel", "", {"pages": 1}), [])

    def test_a_page_is_taken_out_at_the_tier_that_holds_page_one(self) -> None:
        self.assertEqual(mediapages.owner_of({"via": "table"}, "t1"), "t1")
        self.assertEqual(mediapages.owner_of({"via": "game"}, "t1"), "")

    def test_a_collection_name_is_one_path_segment(self) -> None:
        self.assertEqual(art.collection("AC/DC & more", version="v1"),
                         "/api/v1/collections/AC%2FDC%20%26%20more/image?v=v1")

    def test_an_address_the_api_gave_keeps_its_own_query(self) -> None:
        self.assertEqual(art.sized("/api/v1/games/G1/media/wheel", art.CELL),
                         "/api/v1/games/G1/media/wheel?size=256")
        self.assertEqual(art.sized("/x?v=1", art.CELL), "/x?v=1&size=256")

    def test_a_file_in_an_upload_is_asked_for_by_the_name_it_was_dropped_under(self) -> None:
        self.assertEqual(art.upload_picture("u 1", "(Rule Sheet) A&B.png", size=art.CELL),
                         "/api/v1/uploads/u%201/picture?name=%28Rule+Sheet%29+A%26B.png&size=256")


class DrawnArtTests(unittest.TestCase):
    def test_a_grid_cell_draws_the_small_copy(self) -> None:
        cell = _thumb("G1", "backglass", {"present": True, "version": "abc"})

        self.assertIn('src="/api/v1/games/G1/media/backglass?size=256&v=abc"', cell)

    def test_a_grid_cell_video_is_the_file_itself(self) -> None:
        cell = _thumb("G1", "backglass_video", {"present": True, "version": "abc"})

        self.assertIn('src="/api/v1/games/G1/media/backglass_video?v=abc#t=0.1"', cell)

    def test_a_collection_s_picture_draws_the_small_copy(self) -> None:
        self.assertEqual(
            thumbnails.collection({"name": "Bally", "image": "b.png", "image_version": "abc"}),
            "/api/v1/collections/Bally/image?size=256&v=abc")

    def test_a_collection_with_no_picture_asks_for_none(self) -> None:
        self.assertEqual(thumbnails.collection({"name": "Bally", "image_version": "abc"}), "")


if __name__ == "__main__":
    unittest.main()
