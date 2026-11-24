import unittest
from unittest import mock

from PIL import Image, ImageDraw

from common.games.formats import dmd_frame

WIDE, HIGH = 960, 540
LIGHT = (255, 255, 255)
BLACK = (0, 0, 0)


def _glass(*boxes: tuple[tuple[int, int, int, int], tuple[int, int, int]],
           mode: str = "RGB") -> Image.Image:
    image = Image.new("RGB", (WIDE, HIGH), LIGHT)
    draw = ImageDraw.Draw(image)
    for (x, y, across, down), color in boxes:
        draw.rectangle((x, y, x + across - 1, y + down - 1), fill=color)
    return image.convert(mode)


class FindTests(unittest.TestCase):
    def test_a_dark_window_is_padded_and_cut_to_the_aspect_ratio(self) -> None:
        # Padding is 1 at this width: (241, 101, 478, 118) is wider than 4:1, so the width
        # becomes 472 and the frame is centered in the 478 it had. Rows are turned over.
        found = dmd_frame.find(_glass(((240, 100, 480, 120), BLACK)), 4.0)
        self.assertEqual(found, (244, HIGH - 1 - 101 - 118, 472, 118))

    def test_a_tall_window_gives_the_first_rows_that_reach_its_width(self) -> None:
        # Later rows score the same and a tie keeps the first, so the frame is at the top.
        found = dmd_frame.find(_glass(((240, 100, 480, 300), BLACK)), 4.0)
        self.assertEqual(found, (241, HIGH - 1 - 101 - 119, 478, 119))

    def test_the_aspect_ratio_asked_for_decides_the_fit(self) -> None:
        found = dmd_frame.find(_glass(((240, 100, 480, 120), BLACK)), 3.0)
        self.assertEqual(found, (303, HIGH - 1 - 101 - 118, 354, 118))

    def test_a_darker_window_inside_a_dark_one_is_the_one_found(self) -> None:
        found = dmd_frame.find(_glass(((100, 60, 760, 400), (100, 100, 100)),
                                      ((240, 200, 480, 120), BLACK)), 4.0)
        self.assertEqual(found, (244, HIGH - 1 - 201 - 118, 472, 118))

    def test_the_more_central_of_two_equal_windows_wins(self) -> None:
        # Boxes are anchored at the right of each row, so inside the window 200 to 679 the
        # box 206 to 679 is the narrowest that still holds a 472 wide frame, and that
        # frame sits nearest the center.
        found = dmd_frame.find(_glass(((20, 40, 480, 120), BLACK),
                                      ((200, 300, 480, 120), BLACK)), 4.0)
        self.assertEqual(found, (207, HIGH - 1 - 301 - 118, 472, 118))

    def test_a_window_narrower_than_a_third_of_the_picture_is_not_found(self) -> None:
        self.assertIsNone(dmd_frame.find(_glass(((300, 100, 300, 75), BLACK)), 4.0))

    def test_a_picture_with_nothing_dark_has_no_frame(self) -> None:
        self.assertIsNone(dmd_frame.find(_glass()))

    def test_a_large_picture_gives_what_the_full_size_passes_give(self) -> None:
        image = Image.new("RGB", (1600, 900), LIGHT)
        draw = ImageDraw.Draw(image)
        draw.rectangle((300, 250, 1259, 449), fill=(90, 90, 90))
        draw.rectangle((440, 270, 1119, 439), fill=BLACK)
        draw.point([(x, 300 + x % 7) for x in range(500, 1000, 37)], fill=(60, 60, 60))

        found = dmd_frame.find(image, 4.0)
        with mock.patch.object(dmd_frame, "LARGE", 10**9):
            self.assertEqual(found, dmd_frame.find(image, 4.0))
        self.assertIsNotNone(found)

    def test_alpha_and_gray_pictures_read_as_their_colors(self) -> None:
        expected = (244, HIGH - 1 - 101 - 118, 472, 118)
        for mode in ("RGBA", "L"):
            with self.subTest(mode):
                self.assertEqual(
                    dmd_frame.find(_glass(((240, 100, 480, 120), BLACK), mode=mode), 4.0),
                    expected)
