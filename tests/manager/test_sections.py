import inspect
import json
import unittest
from unittest import mock
from urllib.parse import parse_qs

from common.i18n import t
from manager.kit import deeplink
from manager.workbench import core, rail
from tests.support.workbench_patch import patch_workbench


def _address(state: dict) -> dict[str, str]:
    return {name: values[0] for name, values in parse_qs(deeplink.query(state)).items()}


class ChosenSectionTests(unittest.TestCase):
    """Which section a panel is on, including none of them."""

    def test_a_fresh_client_lands_on_the_default(self) -> None:
        state: dict = {}

        self.assertEqual(rail.chosen_section(state), core.DEFAULT_SECTION["game"])

    def test_a_section_the_subject_cannot_answer_falls_back(self) -> None:
        """Table details under a game is not a section to land on quietly."""
        state = {"section": "table_details"}

        self.assertEqual(rail.chosen_section(state, "game"),
                         core.DEFAULT_SECTION["game"])

    def test_closed_stays_closed(self) -> None:
        state = {"section": deeplink.COLLAPSED}

        self.assertEqual(rail.chosen_section(state, "game"), deeplink.COLLAPSED)

    def test_closed_survives_a_change_of_subject(self) -> None:
        """Otherwise the fallback reopens a section the user just shut."""
        state = {"section": deeplink.COLLAPSED}
        rail.chosen_section(state, "game")

        self.assertEqual(rail.chosen_section(state, "table"), deeplink.COLLAPSED)


class PickTests(unittest.TestCase):
    """What a click on a rail row does."""

    def _click(self, open_now: str, key: str) -> mock.Mock:
        context = {"state": {"section": open_now}}
        with patch_workbench("_choose") as choose:
            core._pick(context, key)
        return choose

    def test_the_open_row_stays_open(self) -> None:
        """A rail says where you are, so a second click on it must not blank the pane."""
        self._click("assets", "assets").assert_not_called()

    def test_another_row_opens(self) -> None:
        self._click("assets", "media").assert_called_once()

    def test_a_row_opens_from_every_section_closed(self) -> None:
        self._click(deeplink.COLLAPSED, "assets").assert_called_once()


class AddressTests(unittest.TestCase):
    """A place has to survive being written down and read back."""

    def _seed(self, params: dict[str, str]) -> dict:
        state: dict = {}
        deeplink.apply(state, params, views=["games"],
                       sections=[item.key for item in rail.SECTIONS])
        return state

    def test_a_closed_panel_is_named_rather_than_left_out(self) -> None:
        address = _address({"view": "games", "game": "abc",
                            "section": deeplink.COLLAPSED})

        self.assertEqual(address.get("section"), deeplink.NO_SECTION)

    def test_a_closed_panel_survives_the_round_trip(self) -> None:
        """The reason it is named: dropping it reopens a section on reload."""
        state = self._seed({"view": "games", "game": "abc",
                            "section": deeplink.NO_SECTION})

        self.assertEqual(rail.chosen_section(state, "game"), deeplink.COLLAPSED)

    def test_an_open_section_survives_the_round_trip(self) -> None:
        state = self._seed({"view": "games", "game": "abc", "section": "assets"})

        self.assertEqual(rail.chosen_section(state, "game"), "assets")

    def test_a_section_nobody_has_falls_back_rather_than_erroring(self) -> None:
        """Addresses get hand-typed and go stale; neither is an error."""
        state = self._seed({"view": "games", "game": "abc", "section": "nonsense"})

        self.assertEqual(rail.chosen_section(state, "game"),
                         core.DEFAULT_SECTION["game"])

    def test_the_page_reads_every_name_an_address_writes(self) -> None:
        """A name the page takes no parameter for is dropped before `apply` sees it."""
        from manager import page

        taken = set(inspect.signature(page.manager_page).parameters)

        self.assertEqual([name for name, _value in deeplink._FIELDS if name not in taken],
                         [])


class CollectionAddressTests(unittest.TestCase):
    def test_a_collection_survives_the_round_trip(self) -> None:
        state: dict = {}
        deeplink.apply(state, {"view": "collections", "collection": "Friday Night"},
                       views=["games", "collections"], sections=[])

        self.assertEqual(_address(state),
                         {"view": "collections", "collection": "Friday Night"})

    def test_the_games_grid_carries_the_collection_it_is_narrowed_to(self) -> None:
        self.assertEqual({"view": "games", "collection": "Friday Night"},
                         _address({"view": "games", "collection": "Friday Night"}))

    def test_a_collection_is_noise_anywhere_else(self) -> None:
        self.assertNotIn("collection",
                         _address({"view": "tables", "collection": "Friday Night"}))

    def test_leaving_a_page_drops_the_collection_it_had_open(self) -> None:
        from manager import page

        state = {"view": "collections", "collection": "Friday Night"}
        page.leave_for(state, "games")

        self.assertEqual({"view": "games"}, _address(state))

    def test_leaving_a_file_for_another_kind_of_file_drops_it_from_the_address(self) -> None:
        from manager import page

        state = {"view": "media", "game": "g", "table": "t", "section": "media_file",
                 "slot": {"kind": "backglass"}}
        page.leave_for(state, "assets")

        self.assertEqual({"view": "assets"}, _address(state))

    def test_leaving_a_game_keeps_the_section_a_person_was_reading(self) -> None:
        from manager import page

        state = {"view": "games", "game": "g", "section": "play"}
        page.leave_for(state, "tables")

        self.assertEqual({"view": "tables", "section": "play"}, _address(state))


class ExtensionAddressTests(unittest.TestCase):
    """An extension's own page is a page of Settings, so it has an address that reloads
    onto it."""

    def _seed(self, query: dict, views: tuple[str, ...] = ("games", "settings")) -> dict:
        state: dict = {}
        deeplink.apply(state, query, views=views, sections=[])
        return state

    def test_an_extension_s_page_survives_the_round_trip(self) -> None:
        state = self._seed({"view": "settings", "page": "extensions.vpinplay"})

        self.assertEqual((state["view"], state["settings_page"]),
                         ("settings", "extensions.vpinplay"))
        self.assertEqual("view=settings&page=extensions.vpinplay", deeplink.query(state))

    def test_the_address_is_the_one_its_page_links_to(self) -> None:
        from manager.extensions import ext_page

        self.assertEqual("/manager?view=settings&page=extensions.vpinplay",
                         ext_page.address("vpinplay"))

    def test_the_old_view_is_not_one(self) -> None:
        """Nothing redirects: a place that was never shipped is not kept alive."""
        self.assertEqual({}, self._seed({"view": "extensions:vpinplay"},
                                        views=("games", "extensions")))

    def test_a_page_is_noise_on_any_other_view(self) -> None:
        self.assertEqual({"view": "games"},
                         _address({"view": "games", "settings_page": "extensions.x"}))


class HistoryTests(unittest.TestCase):
    """Back and Forward: a place pushes an entry, a pick rewrites the current one."""

    def _written(self, state: dict, **kwargs) -> str:
        with mock.patch.object(deeplink.ui, "run_javascript") as js:
            deeplink.sync(state, **kwargs)
        return js.call_args.args[0]

    def _arguments(self, state: dict, **kwargs) -> list:
        """What the script was called with: address, push, over, title."""
        return json.loads("[" + self._written(state, **kwargs).rsplit("; })(", 1)[1][:-1] + "]")

    def test_a_pick_rewrites_the_entry_it_is_on(self) -> None:
        self.assertFalse(self._arguments({"view": "games"})[1])

    def test_a_place_adds_an_entry(self) -> None:
        self.assertTrue(self._arguments({"view": "games"}, push=True)[1])

    def test_an_entry_is_marked_over_the_list_only_when_asked(self) -> None:
        self.assertFalse(self._arguments({"view": "games"}, push=True)[2])
        self.assertTrue(self._arguments({"view": "games"}, push=True, over=True)[2])

    def test_a_dialogs_entry_on_top_is_rewritten_rather_than_buried(self) -> None:
        written = self._written({"view": "games"}, push=True)

        self.assertLess(written.index("vpinTakeSentinel"), written.index("pushState"))

    def test_an_address_equal_to_the_one_showing_writes_nothing(self) -> None:
        self.assertIn("address !== location.pathname + location.search",
                      self._written({"view": "games"}, push=True))

    def test_the_title_is_set_after_the_entry_is_written(self) -> None:
        written = self._written({"view": "games", "place_title": lambda: "Games - X"},
                                push=True)

        self.assertLess(written.index("history["), written.index("document.title"))
        self.assertIn("Games - X", written)

    def test_nothing_is_written_while_the_browser_has_moved_and_the_page_has_not(self) -> None:
        written = self._written({"view": "games"}, push=True)

        self.assertLess(written.index("window.vpinArrivals"), written.index("history["))

    def test_each_move_is_settled_once_the_state_matches_it(self) -> None:
        with mock.patch.object(deeplink.ui, "run_javascript") as js:
            deeplink.settled()

        self.assertIn("vpinArrivals", js.call_args.args[0])
        self.assertIn("- 1", js.call_args.args[0])

    def test_naming_a_place_leaves_the_address_alone(self) -> None:
        with mock.patch.object(deeplink.ui, "run_javascript") as js:
            deeplink.name_place({"view": "games", "place_title": lambda: "Games - X"})

        self.assertIn("Games - X", js.call_args.args[0])
        self.assertNotIn("history", js.call_args.args[0])

    def test_a_discovery_tab_survives_the_round_trip(self) -> None:
        state: dict = {"view": "discovery"}
        deeplink.apply(state, {"view": "discovery", "tab": "tables"},
                       views=["discovery"], sections=[])

        self.assertEqual("view=discovery&tab=tables", deeplink.query(state))

    def test_a_tab_is_dropped_where_a_subject_has_the_page(self) -> None:
        state = {"view": "discovery", "discovery_tab": "tables",
                 "subject": {"type": "author", "name": "Ann"}}

        self.assertNotIn("tab", _address(state))

    def test_forget_leaves_nothing_for_the_next_address_to_inherit(self) -> None:
        state: dict = {"view": "games", "game": "g", "section": "play", "slot": {"kind": "bg"},
                       "collection": "c", "subject": {"type": "author", "name": "Ann"},
                       "settings_page": "extensions.x", "discovery_tab": "tables"}

        deeplink.forget(state)
        deeplink.apply(state, {"view": "games"}, views=["games"], sections=[])

        self.assertEqual({"view": "games"}, _address(state))

    def test_the_half_showing_is_named_only_where_the_two_do_not_fit(self) -> None:
        picked = {"view": "games", "game": "g"}

        self.assertEqual("", deeplink.showing(picked))
        self.assertEqual("list", deeplink.showing({**picked, "stacked": True}))
        self.assertEqual("open", deeplink.showing({**picked, "stacked": True, "opened": True}))
        self.assertEqual("", deeplink.showing({"view": "games", "stacked": True}))

    def test_a_row_picked_on_any_page_with_a_panel_is_a_subject(self) -> None:
        for view, key in (("collections", "collection"), ("launchers", "launcher"),
                          ("players", "player"), ("backups", "backup"), ("discovery", "entry")):
            with self.subTest(view=view):
                state = {"view": view, "stacked": True}
                self.assertEqual("", deeplink.showing(state))
                self.assertEqual("list", deeplink.showing({**state, key: "x"}))

    def test_a_filter_that_is_not_the_pages_subject_does_not_name_a_half(self) -> None:
        self.assertEqual("", deeplink.showing(
            {"view": "tables", "stacked": True, "launcher": "x"}))

    def test_the_half_showing_is_in_the_address(self) -> None:
        state = {"view": "games", "game": "g", "stacked": True, "opened": True}

        self.assertEqual({"view": "games", "game": "g", "pane": "open"}, _address(state))

    def test_an_address_names_the_half_it_lands_on(self) -> None:
        state: dict = {"view": "games"}
        deeplink.apply(state, {"view": "games", "game": "g", "pane": "list"},
                       views=["games"], sections=[])

        self.assertEqual("list", state["pane"])

    def test_an_unknown_half_is_ignored(self) -> None:
        state: dict = {"view": "games"}
        deeplink.apply(state, {"view": "games", "pane": "both"}, views=["games"], sections=[])

        self.assertNotIn("pane", state)

    def test_two_states_differing_only_in_the_half_showing_are_one_place(self) -> None:
        listed = {"view": "games", "game": "g", "stacked": True}
        opened = {**listed, "opened": True}

        self.assertTrue(deeplink.same_place(listed, opened))
        self.assertFalse(deeplink.same_place(listed, {**listed, "game": "h"}))


class SectionTests(unittest.TestCase):
    """What the rows offer, and which of them brings its own work area."""

    def test_both_rails_offer_collections(self) -> None:
        for subject in ("game", "table"):
            with self.subTest(subject=subject):
                self.assertIn("collections",
                              {item.key for item in rail.sections_for(subject)})

    def test_a_table_section_is_absent_under_a_game(self) -> None:
        keys = [item.key for item in rail.sections_for("game")]

        self.assertNotIn("table_details", keys)
        self.assertIn("game_details", keys)

    def test_a_table_shows_the_game_first(self) -> None:
        """Parent before the thing it contains, which is the reading order."""
        keys = [item.key for item in rail.sections_for("table")]

        self.assertLess(keys.index("game_details"), keys.index("table_details"))

    def test_only_a_section_with_two_regions_reserves_a_dock(self) -> None:
        """The room is half the panel; a section with nothing to put in it left a void.
        Media and Assets put the picked file beside the map; a file's panel puts its
        picture there when it has one."""
        docked = {item.key for item in rail.SECTIONS if item.dock}

        self.assertEqual(docked, {"media", "assets", "media_file", "asset_file"})

    def test_a_key_means_one_section_across_every_rail(self) -> None:
        """The rail is per subject; the key namespace is not.

        `section=` in an address is resolved before a subject is settled - `deeplink`
        is handed every rail's keys, not one rail's - so two sections sharing a key
        would make a link mean whichever one the reader happened to be on. The
        `subjects` field is what answers it: a section says which rails it appears in
        rather than a rail owning a namespace.

        Asserted rather than left to the docstring, because the pressure arrives with
        the next rail: Assets, the Tag Editor and the VPS section each want a short
        noun somebody has already used.
        """
        keys = [item.key for item in rail.SECTIONS]

        self.assertEqual(sorted(keys), sorted(set(keys)))


class RailDefaultTests(unittest.TestCase):
    """Each rail lands where it declares, not on whatever is first."""

    def test_a_game_lands_on_the_game(self) -> None:
        self.assertEqual(rail.chosen_section({}, "game"), "game_details")

    def test_a_table_lands_on_the_table(self) -> None:
        """Selecting a file on purpose should not open on the machine holding it."""
        self.assertEqual(rail.chosen_section({}, "table"), "table_details")

    def test_every_rail_declares_a_section_it_actually_has(self) -> None:
        for subject, wanted in core.DEFAULT_SECTION.items():
            keys = {item.key for item in rail.sections_for(subject)}
            self.assertIn(wanted, keys, f"{subject} lands nowhere")


class TablesBlockTests(unittest.TestCase):
    """A game's tables are a block inside Game Details, not a place of their own."""

    def test_no_rail_offers_tables_as_a_section(self) -> None:
        """Most games hold one, so "Tables (1)" would be a rail entry you went to in
        order to read a single row - and the rail holds places. It takes the other shape
        instead: a sub-table, a related collection with its own columns."""
        for subject in ("game", "table"):
            with self.subTest(subject=subject):
                keys = {item.key for item in rail.sections_for(subject)}
                self.assertNotIn("tables", keys)

    def test_a_game_still_lands_on_details_where_they_now_live(self) -> None:
        self.assertEqual(rail.chosen_section({}, "game"), "game_details")


class MatchTests(unittest.TestCase):
    """The match heads Game details, on both subjects."""

    def test_both_rails_offer_it(self) -> None:
        """A game's match identifies the machine, which a table belongs to - so it is
        answerable whichever of the two is selected."""
        for subject in ("game", "table"):
            with self.subTest(subject=subject):
                keys = {item.key for item in rail.sections_for(subject)}
                self.assertIn("game_details", keys)
                self.assertNotIn("vps", keys)

    def test_the_rail_says_game_whether_or_not_it_is_matched(self) -> None:
        """The Match row says Not matched; the rail names the section, not its state."""
        section = next(one for one in rail.SECTIONS if one.key == "game_details")
        for game in ({}, {"vps_id": "abc"}, {"overrides": {"alt_vps_id": None}}):
            with self.subTest(game=game):
                self.assertEqual(section.label({"game": game}),
                                 t("manager.workbench.game_section"))
