"""The orientation rule, over docs/theme.md's three setups and the three stored
orientations.

The frames here are small labelled grids, and FFmpeg's filters are modelled on them.
"""

from __future__ import annotations

import itertools
import unittest

from common.capture import geometry
from common.capture.geometry import BOTTOM_LEFT, BOTTOM_RIGHT, UPRIGHT, Turn

Grid = tuple[tuple[str, ...], ...]

# A table as the player sees it, top row first: the arch at the top, flippers at the bottom.
UPRIGHT_TABLE: Grid = (("arch-l", "arch-r"),
                       ("mid-l", "mid-r"),
                       ("flip-l", "flip-r"))


def hflip(grid: Grid) -> Grid:
    return tuple(tuple(reversed(row)) for row in grid)


def vflip(grid: Grid) -> Grid:
    return tuple(reversed(grid))


def clockwise(grid: Grid) -> Grid:
    """FFmpeg's `transpose=1`."""
    return tuple(zip(*reversed(grid), strict=True))


def counter_clockwise(grid: Grid) -> Grid:
    """FFmpeg's `transpose=2`."""
    return tuple(reversed(tuple(zip(*grid, strict=True))))


FILTERS = {"hflip": hflip, "vflip": vflip, "transpose=1": clockwise,
           "transpose=2": counter_clockwise}


def run(filters: list[str], grid: Grid) -> Grid:
    for name in filters:
        grid = FILTERS[name](grid)
    return grid


def turned(grid: Grid, quarters_clockwise: int) -> Grid:
    for _ in range(quarters_clockwise % 4):
        grid = clockwise(grid)
    return grid


# Each stored orientation, from the upright table.
STORED = {BOTTOM_RIGHT: turned(UPRIGHT_TABLE, 3), BOTTOM_LEFT: turned(UPRIGHT_TABLE, 1),
          UPRIGHT: UPRIGHT_TABLE}

# Quarter turns clockwise from what the screen shows to the buffer wf-recorder hands over.
BUFFER_FROM_SCREEN = {"normal": 0, "90": 1, "180": 2, "270": 3}

# docs/theme.md "The three setups": the transform the OS applies, and the clockwise turn
# VPinFE's UI makes to face the player. A and B each come in both directions.
SETUPS = {
    "A, portrait, the OS turns it one way": ("270", 0),
    "A, portrait, the OS turns it the other way": ("90", 0),
    "B, portrait, VPinFE turns the UI one way": ("normal", 90),
    "B, portrait, VPinFE turns the UI the other way": ("normal", 270),
    "C, landscape": ("normal", 0),
}


def on_screen(rotation: int) -> Grid:
    """What the screen shows: the table as the UI turned it to face the player."""
    return turned(UPRIGHT_TABLE, rotation // 90)


class OrientationRuleTests(unittest.TestCase):
    def test_every_setup_stores_every_orientation(self) -> None:
        for (setup, (transform, rotation)), orientation in itertools.product(
                SETUPS.items(), geometry.ORIENTATIONS):
            with self.subTest(setup=setup, orientation=orientation):
                buffer = turned(on_screen(rotation), BUFFER_FROM_SCREEN[transform])
                turn = geometry.playfield(geometry.sway_transform(transform), rotation,
                                          orientation)

                self.assertEqual(run(turn.filters, buffer), STORED[orientation])

    def test_the_reference_cabinet_turns_nothing(self) -> None:
        turn = geometry.playfield(geometry.sway_transform("270"), 0, BOTTOM_RIGHT)

        self.assertEqual(turn.filters, [])

    def test_a_sideways_table_on_a_landscape_head_turns_nothing(self) -> None:
        """VPinOS draws the table sideways on a landscape head, captured bottom at the
        right, with VPinFE's UI turned to match."""
        turn = geometry.playfield(geometry.sway_transform("normal"), 270, BOTTOM_RIGHT)

        self.assertEqual(turn.filters, [])

    def test_another_window_is_stored_as_its_screen_shows_it(self) -> None:
        for transform, quarters in BUFFER_FROM_SCREEN.items():
            with self.subTest(transform=transform):
                buffer = turned(UPRIGHT_TABLE, quarters)
                turn = geometry.screen(geometry.sway_transform(transform))

                self.assertEqual(run(turn.filters, buffer), UPRIGHT_TABLE)


ALL = [Turn(flip, ccw) for flip in (False, True) for ccw in (0, 90, 180, 270)]


class TurnTests(unittest.TestCase):
    def test_one_turn_then_another_is_the_two_filters_run_in_order(self) -> None:
        for first, second in itertools.product(ALL, ALL):
            with self.subTest(first=first, second=second):
                self.assertEqual(run(first.then(second).filters, UPRIGHT_TABLE),
                                 run(second.filters, run(first.filters, UPRIGHT_TABLE)))

    def test_every_turn_is_one_of_eight_different_frames(self) -> None:
        self.assertEqual(len({run(turn.filters, UPRIGHT_TABLE) for turn in ALL}), 8)

    def test_a_quarter_turn_swaps_the_size(self) -> None:
        for turn in ALL:
            with self.subTest(turn=turn):
                expected = run(turn.filters, UPRIGHT_TABLE)
                self.assertEqual(turn.size(2, 3), (len(expected[0]), len(expected)))

    def test_hyprland_numbers_the_transforms_sway_names(self) -> None:
        for number, name in enumerate(("normal", "90", "180", "270", "flipped",
                                       "flipped-90", "flipped-180", "flipped-270")):
            with self.subTest(name=name):
                self.assertEqual(geometry.hyprland_transform(number),
                                 geometry.sway_transform(name))

    def test_a_transform_nobody_names_turns_nothing(self) -> None:
        self.assertEqual(geometry.sway_transform("sideways"), geometry.NONE)
        self.assertEqual(geometry.hyprland_transform(9), geometry.NONE)
        self.assertEqual(geometry.hyprland_transform(None), geometry.NONE)


if __name__ == "__main__":
    unittest.main()
