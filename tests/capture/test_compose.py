"""Every screen in one picture: backglass and DMD side by side at one height, above the
upright playfield, the row scaled to the playfield's width."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PIL import Image

from common.capture import compose
from common.capture.geometry import Turn


class LayoutTests(unittest.TestCase):
    def test_the_row_is_scaled_to_the_playfields_width(self) -> None:
        size, boxes = compose.layout({"playfield": (1080, 1920), "backglass": (1920, 1080),
                                      "scoreview": (1280, 320)})
        placed = {box.window: box for box in boxes}

        # At the backglass's height the DMD is 4320 wide, so the row is 6240 by 1080,
        # and at 1080 wide it is 187 high.
        self.assertEqual(size, (1080, 187 + 1920))
        self.assertEqual((placed["backglass"].x, placed["backglass"].width), (0, 332))
        self.assertEqual((placed["scoreview"].x, placed["scoreview"].width), (332, 748))
        self.assertEqual({placed["backglass"].height, placed["scoreview"].height}, {187})
        self.assertEqual((placed["playfield"].y, placed["playfield"].width), (187, 1080))

    def test_the_row_fills_the_width_exactly(self) -> None:
        _, boxes = compose.layout({"playfield": (1081, 1920), "backglass": (1024, 768),
                                   "scoreview": (1280, 390)})
        row = [box for box in boxes if box.window != "playfield"]
        self.assertEqual(row[-1].x + row[-1].width, 1081)

    def test_a_cabinet_with_no_dmd_screen_has_the_backglass_alone(self) -> None:
        size, boxes = compose.layout({"playfield": (1080, 1920), "backglass": (1920, 1080)})
        self.assertEqual(size, (1080, 608 + 1920))
        self.assertEqual([box.window for box in boxes], ["backglass", "playfield"])

    def test_no_playfield_is_the_row_at_its_own_size(self) -> None:
        size, boxes = compose.layout({"backglass": (1920, 1080), "scoreview": (1280, 320)})
        self.assertEqual(size, (6240, 1080))
        self.assertNotIn("playfield", [box.window for box in boxes])

    def test_the_playfield_alone(self) -> None:
        size, boxes = compose.layout({"playfield": (1080, 1920)})
        self.assertEqual(size, (1080, 1920))
        self.assertEqual(boxes[0].y, 0)


class ComposeTests(unittest.TestCase):
    def _still(self, folder: Path, name: str, size: tuple[int, int],
               color: tuple[int, int, int]) -> Path:
        path = folder / f"{name}.png"
        Image.new("RGB", size, color).save(path)
        return path

    def test_each_screen_lands_where_the_layout_puts_it(self) -> None:
        with tempfile.TemporaryDirectory() as held:
            folder = Path(held)
            # The playfield as the screen shows it, bottom at the right: upright is a
            # quarter turn clockwise, which is 270 counter-clockwise.
            playfield = Image.new("RGB", (200, 100), (0, 0, 255))
            playfield.paste((255, 255, 255), (190, 0, 200, 100))
            playfield.save(folder / "playfield.png")
            stills = {"playfield": folder / "playfield.png",
                      "backglass": self._still(folder, "backglass", (160, 90), (255, 0, 0)),
                      "scoreview": self._still(folder, "scoreview", (128, 32), (0, 255, 0))}

            picture = compose.compose(stills, {"playfield": Turn(ccw=270)})

        self.assertEqual(picture.width, 100)
        top = round(picture.height - 200)
        self.assertEqual(picture.getpixel((5, top // 2)), (255, 0, 0))
        self.assertEqual(picture.getpixel((95, top // 2)), (0, 255, 0))
        self.assertEqual(picture.getpixel((50, top + 20)), (0, 0, 255))
        # The playfield's bottom edge, at the right as shown, is at the bottom now.
        self.assertEqual(picture.getpixel((50, picture.height - 2)), (255, 255, 255))

    def test_a_flip_mirrors_before_it_turns(self) -> None:
        image = Image.new("RGB", (2, 1))
        image.putpixel((0, 0), (255, 0, 0))

        self.assertEqual(compose.turned(image, Turn(flip=True)).getpixel((1, 0)), (255, 0, 0))
        self.assertEqual(compose.turned(image, Turn(ccw=90)).size, (1, 2))


if __name__ == "__main__":
    unittest.main()
