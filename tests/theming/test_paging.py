import configparser
import unittest
from types import SimpleNamespace

from frontend import input_api
from frontend.api import API
from frontend.game_state import page_jump_index
from tests.support.entries import entries_for


def _game(title):
    # full_path_vpx_file is what every game off a real scan carries, and what the view
    # falls back to for a folder no metadata build has parsed yet.
    return SimpleNamespace(meta_config={"Info": {"Title": title}},
                           full_path_vpx_file=f"/games/{title}/{title}.vpx")


def _games(*titles):
    return [_game(title) for title in titles]


class TestPageJumpIndexAlpha(unittest.TestCase):
    def setUp(self):
        # Alpha-sorted list: groups #(2), A(2), B(1), C(3)
        self.games = _games(
            "24", "4x4", "Attack", "Avalanche", "Bally Hoo", "Cactus", "Comet", "Cyclone"
        )

    def test_next_jumps_to_first_of_next_letter(self):
        self.assertEqual(page_jump_index(self.games, 2, "next"), 4)

    def test_next_from_mid_group_skips_rest_of_group(self):
        self.assertEqual(page_jump_index(self.games, 5, "next"), 0)

    def test_next_wraps_to_number_bucket(self):
        self.assertEqual(page_jump_index(self.games, 7, "next"), 0)

    def test_prev_jumps_to_first_of_previous_letter(self):
        self.assertEqual(page_jump_index(self.games, 4, "prev"), 2)

    def test_prev_from_mid_group_goes_to_previous_group_start(self):
        self.assertEqual(page_jump_index(self.games, 6, "prev"), 4)

    def test_prev_wraps_from_number_bucket_to_last_group(self):
        self.assertEqual(page_jump_index(self.games, 0, "prev"), 5)

    def test_numbers_and_symbols_share_one_bucket(self):
        games = _games("24", "4x4", "(Secret)", "Attack")
        self.assertEqual(page_jump_index(games, 0, "next"), 3)

    def test_descending_alpha_order_still_groups(self):
        games = _games("Cactus", "Bally Hoo", "Attack", "Avalanche")
        self.assertEqual(page_jump_index(games, 0, "next"), 1)
        self.assertEqual(page_jump_index(games, 3, "prev"), 1)

    def test_single_letter_group_falls_back_to_numeric(self):
        games = _games("Attack", "Avalanche", "Aztec", "Airborne")
        # Numeric fallback: step = min(10, 4 // 2) = 2
        self.assertEqual(page_jump_index(games, 0, "next"), 2)

    def test_an_order_with_no_groups_steps_instead(self):
        """Not a fallback any more: last-played gives every game its own timestamp, so
        there are no boundaries to page between and a press steps."""
        result = page_jump_index(self.games, 0, "next", order_by="last_played", page_size=3)
        self.assertEqual(result, 3)

    def test_the_2x_sort_name_still_groups(self):
        """A caller holding `Alpha` means title order, and should not lose its groups."""
        self.assertEqual(page_jump_index(self.games, 0, "next", order_by="Alpha"),
                         page_jump_index(self.games, 0, "next", order_by="title"))


class TestPageJumpIndexNumeric(unittest.TestCase):
    def test_next_steps_by_page_size(self):
        games = _games(*[f"T{i:02d}" for i in range(30)])
        self.assertEqual(page_jump_index(games, 0, "next", paging_group="count", page_size=10), 10)

    def test_prev_steps_back_and_wraps(self):
        games = _games(*[f"T{i:02d}" for i in range(30)])
        self.assertEqual(page_jump_index(games, 5, "prev", paging_group="count", page_size=10), 25)

    def test_step_caps_at_half_the_list(self):
        # 15 games, size 10: uncapped this would land 10 ahead, which reads as
        # moving backward 5 on a circular wheel. Cap keeps it at 7.
        games = _games(*[f"T{i:02d}" for i in range(15)])
        self.assertEqual(page_jump_index(games, 0, "next", paging_group="count", page_size=10), 7)

    def test_two_games_step_one(self):
        games = _games("Alpha", "Bravo")
        self.assertEqual(page_jump_index(games, 0, "next", paging_group="count", page_size=10), 1)

    def test_single_game_is_noop(self):
        games = _games("Alpha")
        self.assertEqual(page_jump_index(games, 0, "next", paging_group="count"), 0)

    def test_empty_list_returns_index(self):
        self.assertEqual(page_jump_index([], 3, "next"), 3)

    def test_out_of_range_index_is_normalized(self):
        games = _games(*[f"T{i:02d}" for i in range(10)])
        self.assertEqual(page_jump_index(games, 12, "next", paging_group="count", page_size=3), 5)


class TestGetPagingConfig(unittest.TestCase):
    def _config(self, **input_values):
        parser = configparser.ConfigParser()
        parser.add_section("Input")
        for key, value in input_values.items():
            parser.set("Input", key, value)
        return parser

    def test_defaults_when_unset(self):
        self.assertEqual(input_api.get_paging_config(self._config()), ("sort", 10))

    def test_reads_configured_values(self):
        config = self._config(pagingtype="count", pagingsize="25")
        self.assertEqual(input_api.get_paging_config(config), ("count", 25))

    def test_the_2x_spellings_still_resolve(self):
        """`alpha` and `numeric` are in configs users already have."""
        alpha = input_api.get_paging_config(self._config(pagingtype="alpha"))
        numeric = input_api.get_paging_config(self._config(pagingtype="numeric"))
        self.assertEqual(alpha[0], "sort")
        self.assertEqual(numeric[0], "count")

    def test_invalid_values_fall_back_to_defaults(self):
        config = self._config(pagingtype="bogus", pagingsize="zero")
        self.assertEqual(input_api.get_paging_config(config), ("sort", 10))

    def test_nonpositive_size_falls_back(self):
        config = self._config(pagingsize="0")
        self.assertEqual(input_api.get_paging_config(config), ("sort", 10))


class TestApiGetPageIndex(unittest.TestCase):
    def _api(self, games, order_by="title", **input_values):
        parser = configparser.ConfigParser()
        parser.add_section("Input")
        for key, value in input_values.items():
            parser.set("Input", key, value)
        api = API.__new__(API)
        api._ini_config = SimpleNamespace(config=parser)
        # The view holds entries, which is what an index from a theme addresses.
        api.filtered_games = entries_for(games)
        api.current_sort = order_by
        return api

    def test_alpha_paging_over_current_view(self):
        api = self._api(_games("Attack", "Avalanche", "Bally Hoo", "Cactus"))
        self.assertEqual(api.get_page_index(0, "next"), 2)
        self.assertEqual(api.get_page_index(0, "prev"), 3)

    def test_numeric_config_is_honored(self):
        api = self._api(
            _games(*[f"T{i:02d}" for i in range(20)]),
            pagingtype="numeric",
            pagingsize="5",
        )
        self.assertEqual(api.get_page_index(0, "next"), 5)

    def test_non_alpha_sort_uses_numeric_fallback(self):
        api = self._api(
            _games(*[f"T{i:02d}" for i in range(20)]),
            order_by="last_played",
            pagingsize="4",
        )
        self.assertEqual(api.get_page_index(0, "next"), 4)

    def test_bad_index_from_theme_is_coerced(self):
        api = self._api(_games("Attack", "Bally Hoo", "Cactus"))
        self.assertEqual(api.get_page_index("not-a-number", "next"), 1)


class TestGroupPagingFollowsTheOrder(unittest.TestCase):
    """A group press moves to the next boundary in whatever the list is ordered by.

    It used to be letters or nothing: `alpha` paging applied only when the sort was
    alphabetical and silently stepped otherwise, so a collection ordered by year was
    paged by letter through a list where the letters were scattered.
    """

    def test_ordered_by_title_pages_to_the_next_letter(self):
        games = _games("Attack", "Avalanche", "Bally Hoo", "Cactus")

        self.assertEqual(page_jump_index(games, 0, "next", order_by="title"), 2)

    def test_ordered_by_year_pages_to_the_next_year(self):
        games = _games("A", "B", "C")
        for game, year in zip(games, ("1975", "1975", "1980"), strict=True):
            game.meta_config["Info"]["Year"] = year

        self.assertEqual(page_jump_index(games, 0, "next", order_by="year"), 2,
                         "the boundary is the year, not the letter")

    def test_a_digit_title_shares_one_group(self):
        """'300' and '24' are both `#`, so paging crosses them in a single press."""
        games = _games("300", "24", "Attack")

        self.assertEqual(page_jump_index(games, 0, "next", order_by="title"), 2)


if __name__ == "__main__":
    unittest.main()
