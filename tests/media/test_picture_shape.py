"""What an unnamed picture's shape says about where it goes.

The table is the calibration of the library import's own output: backglass and playfield
are 1920x1080, a DMD 1080x542, and only a wheel has transparent pixels. A change to a
range is a change to one row here and the rule beside it.
"""

from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image

from common.uploads import picture_shape
from common.uploads.picture_shape import Shape
from tests.support import drops

# (width, height, transparent) -> the slots the shape fits, best first
FITS = (
    (1920, 1080, False, ("backglass", "playfield")),
    (1280, 720, False, ("backglass", "playfield")),
    (1900, 1080, False, ("backglass", "playfield")),
    (1080, 542, False, ("scoreview",)),
    (1024, 512, False, ("scoreview",)),
    (700, 331, True, ("wheel",)),
    (373, 128, True, ("wheel",)),
    (1600, 1600, True, ("wheel",)),
    (1920, 1080, True, ("wheel",)),
    (800, 800, False, ()),
    (2560, 1080, False, ()),
    (1000, 530, False, ()),
    (1080, 1920, False, ()),
)


class FitTests(unittest.TestCase):
    def test_each_calibrated_shape_fits_the_slots_the_import_output_shows(self) -> None:
        for width, height, transparent, slots in FITS:
            with self.subTest(width=width, height=height, transparent=transparent):
                self.assertEqual(slots, picture_shape.fits(Shape(width, height, transparent)))

    def test_only_a_shape_that_fits_one_slot_is_a_suggestion(self) -> None:
        self.assertEqual("wheel", picture_shape.suggestion(Shape(700, 331, True)))
        self.assertEqual("scoreview", picture_shape.suggestion(Shape(1080, 542, False)))
        self.assertEqual("", picture_shape.suggestion(Shape(1920, 1080, False)))
        self.assertEqual("", picture_shape.suggestion(Shape(800, 800, False)))

    def test_the_ranges_do_not_overlap(self) -> None:
        top_of_wide = picture_shape._WIDE * (1 + picture_shape.TOLERANCE)
        bottom_of_dmd = picture_shape._DMD * (1 - picture_shape.TOLERANCE)
        self.assertLess(top_of_wide, bottom_of_dmd)


class ReadTests(unittest.TestCase):
    def setUp(self) -> None:
        held = TemporaryDirectory()
        self.addCleanup(held.cleanup)
        self.tmp = Path(held.name)

    def test_it_reads_the_pixel_size(self) -> None:
        shape = picture_shape.read(drops.png(self.tmp / "a.png", 300, 120))
        self.assertEqual((300, 120), (shape.width, shape.height))

    def test_a_picture_with_see_through_pixels_is_transparent(self) -> None:
        path = drops.png(self.tmp / "a.png", 30, 12, alpha=True)
        self.assertTrue(picture_shape.read(path).transparent)

    def test_an_alpha_channel_with_nothing_see_through_is_not_transparent(self) -> None:
        path = self.tmp / "opaque.png"
        Image.new("RGBA", (30, 12), (200, 30, 30, 255)).save(path)
        self.assertFalse(picture_shape.read(path).transparent)

    def test_a_file_that_is_not_a_picture_has_no_shape(self) -> None:
        path = self.tmp / "IMG_0001.png"
        path.write_bytes(b"x" * 16)
        self.assertIsNone(picture_shape.read(path))

    def test_a_file_that_is_missing_has_no_shape(self) -> None:
        self.assertIsNone(picture_shape.read(self.tmp / "gone.png"))


if __name__ == "__main__":
    unittest.main()
