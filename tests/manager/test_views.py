"""The built-in views, and the vocabulary they share with the workbench."""

import unittest
from typing import Any
from unittest.mock import Mock

from common import apps
from common.config.labels import field_label
from common.games import asset_origin, asset_registry
from common.i18n import t
from manager.kit import data, game_tables, media_ownership, play_facts, stars, table_features, views
from manager.library import games, tageditor
from manager.workbench import game_assets, rail


class BuiltinViewTests(unittest.TestCase):
    def test_every_view_names_columns_that_exist(self) -> None:
        """`apply` falls back to every column when a view's fields all miss, so a
        typo in a preset reads as "same as All" rather than as an error."""
        for label, presets, columns in (
            ("games", games.GAME_VIEWS, games.COLUMNS),
            ("tables", games.TABLE_VIEWS, games.TABLE_COLUMNS),
        ):
            known = {definition["field"] for definition in columns}
            for name, preset in presets.items():
                fields = (preset.columns if isinstance(preset, views.Preset)
                          else preset)
                filtered = preset.filters if isinstance(preset, views.Preset) else {}
                # Media and Assets are filled at render time from what the library
                # reports it holds, so an empty preset here is the declaration.
                if not fields:
                    continue
                with self.subTest(grid=label, view=name):
                    self.assertEqual(sorted(set(fields) - known), [])
                    self.assertEqual(sorted(set(filtered) - known), [])

    def test_a_table_view_is_named_for_the_group_it_matches(self) -> None:
        named = {view.name for view in views.builtins(games.TABLE_VIEWS)}
        for group in (game_tables.FILE, game_tables.LAUNCH, game_tables.FEATURES):
            self.assertIn(group, named)


class GameRowTests(unittest.TestCase):
    def test_a_game_row_carries_no_table_level_facts(self) -> None:
        """Both would be the default table's reported as the game's - a game with two
        builds declaring different roms has no single rom."""
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A", "rom": "afm_113b",
                          "version": "1.2", "table_count": 1}]
        library.media = {}

        row = library.game_rows()[0]

        self.assertNotIn("rom", row)
        self.assertNotIn("version", row)

    def test_a_game_with_no_catalog_match_says_so(self) -> None:
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A", "vps_id": "abc"},
                         {"id": "g2", "name": "B", "vps_id": ""},
                         {"id": "g3", "name": "C"}]
        library.media = {}

        rows = library.game_rows()

        self.assertEqual([row["vps_match"] for row in rows],
                         ["matched", "unmatched", "unmatched"])

    def test_a_cleared_match_is_not_the_same_as_none_made(self) -> None:
        library = data.Library.__new__(data.Library)
        library.games = [{"id": "g1", "name": "A", "vps_id": "",
                          "overrides": {"alt_vps_id": None}},
                         {"id": "g2", "name": "B", "vps_id": "",
                          "overrides": {"alt_vps_id": ""}}]
        library.media = {}

        rows = library.game_rows()

        self.assertEqual([row["vps_match"] for row in rows], ["cleared", "unmatched"])


class UnmatchedColumnTests(unittest.TestCase):
    """The column that finds the games the catalog does not know."""

    def _column(self, field: str) -> dict:
        return next(one for one in games.COLUMNS if one["field"] == field)

    def test_the_funnel_offers_every_state_most_in_place_first(self) -> None:
        choices = self._column("vps_match")["filterParams"]["choices"]

        self.assertEqual([(one["value"], one["label"]) for one in choices],
                         [("matched", "Matched"), ("cleared", "Cleared"),
                          ("unmatched", "Unmatched")])

    def test_every_row_draws_its_state(self) -> None:
        """The formatter is JavaScript, so this asserts the shape rather than running it."""
        drawn = self._column("vps_match")[":valueFormatter"]

        for word in ("Matched", "Cleared", "Unmatched"):
            self.assertIn(f'"{word}"', drawn)

    def test_the_column_click_opens_a_section_that_exists(self) -> None:
        """The click is only worth taking if it lands somewhere - a section key that
        no longer exists would quietly do nothing."""
        known = {item.key for item in rail.SECTIONS}

        for field, section in games.COLUMN_SECTIONS.items():
            with self.subTest(column=field):
                self.assertIn(field, {one["field"] for one in games.COLUMNS})
                self.assertIn(section, known)


class AssetSectionTests(unittest.TestCase):
    """The Assets section: what it counts, and the one kind that takes no tier."""

    def _context(self, resolved, folder):
        return {"library": data.Library(Mock()), "game": {"assets": folder}, "lens": "t1",
                "tables": [{"id": "t1", "assets": resolved}]}

    def test_the_count_matches_the_rows_drawn(self) -> None:
        """The folder reports a backglass the table resolves too, and the body skips
        what the table already answered - so counting both said four over two rows."""
        label = game_assets._assets_label(self._context(
            {"backglass": {"resolution": "dedicated"},
             "ini": {"resolution": "shared"},
             "script": {"resolution": "none"}},
            {"backglass": {"present": True}, "ini": {"present": True},
             "music": {"present": True}, "pup_pack": {"present": False}}))

        self.assertEqual(label, "Assets (3)")

    def test_nothing_here_says_so_without_a_number(self) -> None:
        label = game_assets._assets_label(self._context(
            {"backglass": {"resolution": "none"}}, {"music": {"present": False}}))

        self.assertEqual(label, "Assets")

    def test_a_script_inside_the_vpx_is_not_missing(self) -> None:
        """The ordinary table runs the script in its own .vpx. Reading the resolver's
        `none` through the media tiers would render that as Missing and call every one
        of them broken, which is why the script keeps SCRIPT_WORDS."""
        internal = game_tables.word_for(game_tables.SCRIPT_WORDS, False)

        self.assertEqual(internal, "Internal")
        self.assertNotEqual(internal, media_ownership.for_resolution("none").noun)

    def test_a_resolution_maps_onto_the_tier_that_means_it(self) -> None:
        for resolution, noun in (("dedicated", "This table"), ("shared", "This game"),
                                 ("none", "Missing"), (None, "Missing")):
            with self.subTest(resolution=resolution):
                self.assertEqual(t(media_ownership.for_resolution(resolution).noun),
                                 noun)


class LaunchRollupTests(unittest.TestCase):
    """One answer to "will this run", and the trap it has to avoid."""

    def test_an_em_table_declaring_no_rom_is_ready(self) -> None:
        """The trap: reading REQUIRED_KINDS as a checklist every table must satisfy
        calls every EM table broken. Required-ness is the kind's; whether it applies
        is the table's."""
        self.assertIs(asset_registry.launchable(True, False, None), True)

    def test_a_declared_rom_that_is_not_installed_blocks(self) -> None:
        self.assertIs(asset_registry.launchable(True, True, False), False)

    def test_an_unparsed_table_is_unknown_rather_than_broken(self) -> None:
        self.assertIsNone(asset_registry.launchable(True, True, None))

    def test_a_file_that_is_gone_blocks_whatever_the_rom_says(self) -> None:
        self.assertIs(asset_registry.launchable(False, True, True), False)

    def test_the_ordinary_state_is_the_quiet_one(self) -> None:
        """Notable first, like every other pair: a word on every row saying the table
        works tells a reader nothing."""
        self.assertEqual(game_tables.word_for(game_tables.LAUNCH_WORDS, True), "Blocked")
        self.assertEqual(game_tables.word_for(game_tables.LAUNCH_WORDS, False), "Ready")


class TableRowShapeTests(unittest.TestCase):
    """A grid row has to answer every column that reads it."""

    def test_a_row_carries_every_field_a_column_names(self) -> None:
        """A column whose field the row lacks renders blank and says nothing - there
        is no error to see. It happened: patching a row-menu write from the game's own
        sub-resource, which describes a table rather than where it sits in a library,
        blanked Game, Manufacturer, Year and ROM on the rows just acted on.
        """
        wire = {
            "id": "t1", "game_id": "g1", "game": "Addams Family, The",
            "manufacturer": "Bally", "year": "1992",
            "filename": "taf.vpx", "version": "2.1", "authors": ["g5k"],
            "rating": 0, "game_rating": 0, "features": {}, "assets": {},
            "rom": "TAF_L7", "rom_installed": True, "launchable": True,
            "default": True, "default_kind": "user", "hidden": False,
            "available": True, "absent_since": None, "app": "vpx",
        }

        row = games.table_rows([wire])[0]

        named = {definition["field"] for definition in games.TABLE_COLUMNS}
        self.assertEqual(sorted(named - set(row)), [])


class PlayGroupTests(unittest.TestCase):
    """The play record, in the units somebody reads it in."""

    def test_play_time_uses_the_largest_unit_that_is_still_true(self) -> None:
        """41,400 seconds is not a length anybody pictures."""
        cases = [(0, "None"), (45, "45 sec"), (1020, "17 min"), (5340, "89 min"),
                 (5400, "1 hr 30 min"), (7200, "2 hr")]
        for seconds, said in cases:
            with self.subTest(seconds=seconds):
                self.assertEqual(play_facts.played_for(seconds), said)

    def test_never_played_says_so_rather_than_showing_nothing(self) -> None:
        """An empty cell is not a state: a value is a name or a state."""
        self.assertEqual(play_facts.played_when(None), "Never")
        self.assertEqual(play_facts.played_when(""), "Never")
        self.assertEqual(play_facts.played_when("not a date"), "Never")


class LabelCasingTests(unittest.TestCase):
    """One casing rule for every label the Manager shows."""

    def test_acronyms_stay_acronyms(self) -> None:
        for text, said in (("vps_id", "VPS ID"), ("rom", "ROM"),
                           ("dof_event", "DOF Event"),
                           ("Clear NVRAM on exit", "Clear NVRAM on Exit")):
            with self.subTest(text=text):
                self.assertEqual(field_label(text), said)

    def test_a_small_word_stays_down_unless_it_leads_or_closes(self) -> None:
        """`AssetSpec` writes "Point of View" by hand, so a rule producing "Point Of
        View" would disagree with the registry it is the fallback for."""
        self.assertEqual(field_label("point_of_view"), "Point of View")
        self.assertEqual(field_label("Made by"), "Made By")
        self.assertEqual(field_label("of the year"), "Of the Year")

    def test_a_small_word_inside_a_name_keeps_its_capital(self) -> None:
        for text, said in (("Pin Up Player", "Pin Up Player"),
                           ("Legacy Fly Over Mode", "Legacy Fly Over Mode"),
                           ("Field Of View (overall scale)", "Field of View (Overall Scale)"),
                           ("Copy Into This Game", "Copy into This Game"),
                           ("Clear NVRAM on exit", "Clear NVRAM on Exit")):
            with self.subTest(text=text):
                self.assertEqual(field_label(text), said)

    def test_a_key_nobody_named_splits_a_run_of_acronyms(self) -> None:
        for key, said in (("Plugin.B2SLegacy.B2SDMDWidth", "B2S DMD Width"),
                          ("Plugin.B2SLegacy.B2SHideB2SDMD", "B2S Hide B2S DMD"),
                          ("B2SHideGrill", "B2S Hide Grill"),
                          ("PIN2DMD", "PIN2DMD"),
                          ("UseNVRAM", "Use NVRAM")):
            with self.subTest(key=key):
                self.assertEqual(apps.humanized(key), said)

    def test_it_takes_a_key_or_a_phrase(self) -> None:
        self.assertEqual(field_label("last_played"), "Last Played")
        self.assertEqual(field_label("Last played"), "Last Played")

    def test_a_name_with_capitals_of_its_own_keeps_them(self) -> None:
        self.assertEqual(["VPinPlay", "PinMAME Settings", "Table Script"],
                         [field_label(text) for text in
                          ("VPinPlay", "PinMAME settings", "TABLE SCRIPT")])

    def test_a_word_its_source_writes_in_capitals_keeps_them(self) -> None:
        for text, said in (("Disable tonemapping on HDR display",
                            "Disable Tonemapping on HDR Display"),
                           ("HDR Display Global Exposure", "HDR Display Global Exposure"),
                           ("MSAA level", "MSAA Level"),
                           ("RAR Tool Path", "RAR Tool Path")):
            with self.subTest(text=text):
                self.assertEqual(field_label(text), said)

    def test_an_apostrophe_does_not_start_a_word(self) -> None:
        """`str.title` would give "Author'S"."""
        self.assertEqual(field_label("author's notes"), "Author's Notes")


class FeatureLegendTests(unittest.TestCase):
    """A legend names what a reader can actually see."""

    def test_a_settled_library_does_not_explain_a_mark_it_never_shows(self) -> None:
        """Measured on 166 real tables: every one of the seven displayed features is
        true or false, and every null belongs to pinmame - which this vocabulary
        leaves out on purpose. So the third line explained nothing, on every visit."""
        rows = [{"feature_ssf": True, "feature_lut": False}]

        self.assertEqual(table_features.states_in(rows), [table_features.IN_SCRIPT])

    def test_a_state_that_draws_nothing_is_not_in_the_legend(self) -> None:
        """It read as blank space with a name beside it. `media_ownership` leaves
        Missing out for the same reason - a blank cell is not something anybody looks
        up - and the binary views should not differ from the matrix they sit beside."""
        rows = [{"feature_ssf": True, "feature_lut": False}]

        self.assertNotIn(table_features.UNUSED, table_features.states_in(rows))

    def test_it_comes_back_the_moment_a_table_is_unparsed(self) -> None:
        """Reachable, not impossible: discovery leaves a table with a filename and an
        id, and parsing is a separate job because reading one is a full file read. In
        that window the flags are null, and calling that "Not used" would give an
        unparsed table a clean bill of health it has not earned."""
        rows: list[dict[str, Any]] = [{"feature_ssf": True}, {"feature_ssf": None}]

        self.assertIn(table_features.UNKNOWN, table_features.states_in(rows))

    def test_it_keeps_the_vocabulary_s_own_order(self) -> None:
        """Whatever order the rows arrive in, the legend reads the way the vocabulary
        is declared - so it does not shuffle when a library changes."""
        rows: list[dict[str, Any]] = [{"feature_ssf": None}, {"feature_lut": False},
                                      {"feature_nfozzy": True}]

        self.assertEqual(table_features.states_in(rows),
                         [table_features.IN_SCRIPT, table_features.UNKNOWN])


class FunnelChoiceTests(unittest.TestCase):
    """A funnel offers the marks the grid actually draws."""

    def _by_label(self, choices):
        return {choice["label"]: choice for choice in choices}

    def test_missing_offers_no_mark_because_the_cell_draws_none(self) -> None:
        """The media cell holds "" for Missing and the renderer returns nothing, so a
        glyph in the funnel would promise a mark that is never on screen."""
        choices = self._by_label(games._STATE_CHOICES)

        missing = t(media_ownership.tier_for(media_ownership.MISSING).noun)
        table = t(media_ownership.tier_for(media_ownership.TABLE).noun)
        self.assertEqual(choices[missing]["mark"], "")
        self.assertTrue(choices[table]["mark"])

    def test_every_state_is_still_offered(self) -> None:
        """Dropping the mark is not dropping the choice - Missing is the one people
        filter to most, and it stays pickable."""
        self.assertEqual({choice["label"] for choice in games._STATE_CHOICES},
                         {t(media_ownership.tier_for(key).noun)
                          for key in media_ownership.STATES})


class SourceWordTests(unittest.TestCase):
    def test_a_recording_is_named_and_offered_as_recorded(self) -> None:
        said = media_ownership.source_name(asset_origin.RECORDED)

        self.assertEqual(said, t("manager.media_ownership.recorded"))
        self.assertIn(said, media_ownership.source_names())


class StarControlTests(unittest.TestCase):
    """One control, drawn twice - so the two drawings have to agree."""

    def test_both_renderers_draw_the_same_control(self) -> None:
        """A grid cell is rendered by AG Grid in the browser and a panel row from
        elements on the server, so they cannot be one call. Everything they draw comes
        from the module's constants, which is what stops them drifting again - the clear
        had a body in one and not the other, and cleared on a different gesture."""
        js = stars.renderer("game")

        for token in (stars.BOX, stars.STAR, stars.LIT, stars.CLEAR_CLASS, stars.CLEAR):
            with self.subTest(token=token):
                self.assertIn(token, js)
        self.assertIn(f"n <= {stars.MOST}", js)

    def test_the_clear_carries_the_character(self) -> None:
        """In a tooltip it measures zero and cannot be clicked - and a scripted click
        passes on it anyway, so only an eye catches it."""
        self.assertEqual(stars.CLEAR, "×")
        self.assertIn(f">{stars.CLEAR}</span>", stars.renderer("game"))

    def test_only_a_rated_row_offers_the_clear(self) -> None:
        """Guarded in the renderer, or an unrated library is a column of dismissals."""
        self.assertIn("if (held)", stars.renderer("game"))

    def test_a_game_s_rating_on_a_table_s_row_writes_the_game(self) -> None:
        """No table named, or the click rates the file instead."""
        js = stars.renderer("game", on_table=True)

        self.assertIn("' + (row.game_id || '') + '", js)
        self.assertIn("data-table=\"' + '' + '", js)

    def test_the_listener_reads_the_box_class(self) -> None:
        """One delegated listener for every star in the app; if the class it looks for
        drifts from the one drawn, every click stops arriving and nothing errors."""
        self.assertIn(f".{stars.BOX} [data-value]", stars.CLICK_JS)


class TagEditorTests(unittest.TestCase):
    """What the editor leads with: the spellings that are one word."""

    def _rows(self, pairs):
        return [{"tag": tag, "games": count, "same": " ".join(tag.split()).casefold()}
                for tag, count in pairs]

    def test_only_words_spelled_more_than_one_way_are_grouped(self) -> None:
        """A tag nobody has spelled twice is not something to act on, and listing it
        would bury the ones that are."""
        groups = tageditor.rows_by_key(
            self._rows([("Sci-Fi", 2), ("sci-fi", 1), ("Wide Body", 4)]))

        self.assertEqual([[r["tag"] for r in g] for g in groups], [["Sci-Fi", "sci-fi"]])

    def test_the_most_used_spelling_leads_because_it_is_the_survivor(self) -> None:
        groups = tageditor.rows_by_key(self._rows([("sci-fi", 1), ("Sci-Fi", 9)]))

        self.assertEqual(groups[0][0]["tag"], "Sci-Fi")

    def test_whitespace_alone_makes_two_spellings_one_word(self) -> None:
        groups = tageditor.rows_by_key(self._rows([("Wide Body", 1), ("wide  body", 1)]))

        self.assertEqual(len(groups), 1)


class TagRowTests(unittest.TestCase):
    def _library(self, tags: list[dict]) -> data.Library:
        library = data.Library.__new__(data.Library)
        library._tags = tags
        return library

    def test_a_tag_only_written_down_is_a_row(self) -> None:
        rows = self._library([{"name": "Someday", "games": 0, "color": "amber"},
                              {"name": "Wide Body", "games": 2,
                               "description": "Wide ones"}]).tag_rows()

        self.assertEqual([("Someday", 0, ["Someday"], ""), ("Wide Body", 2, ["Wide Body"],
                                                            "Wide ones")],
                         [(r["tag"], r["games"], r["tag_list"], r["description"])
                          for r in rows])

    def test_a_tag_on_nothing_is_unused(self) -> None:
        rows = self._library([{"name": "Someday", "games": 0, "tables": 0},
                              {"name": "VR", "games": 0, "tables": 2},
                              {"name": "Wide Body", "games": 2, "tables": 0}]).tag_rows()

        self.assertEqual({"Someday": data.TAG_UNUSED, "VR": data.TAG_IN_USE,
                          "Wide Body": data.TAG_IN_USE},
                         {r["tag"]: r["use"] for r in rows})

    def test_a_tag_spelled_two_ways_says_so_and_the_others_say_one(self) -> None:
        rows = self._library([{"name": "Sci-Fi", "games": 1}, {"name": "sci-fi", "games": 1},
                              {"name": "VR", "games": 1}]).tag_rows()

        self.assertEqual({"Sci-Fi": data.TAG_TWO, "sci-fi": data.TAG_TWO, "VR": data.TAG_ONE},
                         {r["tag"]: r["spelling"] for r in rows})

    def test_every_tag_view_filters_on_a_column_the_grid_has(self) -> None:
        from manager.library import tageditor

        fields = {one["field"] for one in tageditor.COLUMNS}
        for name, preset in tageditor.VIEWS.items():
            with self.subTest(view=name):
                assert not isinstance(preset, list)
                self.assertLessEqual(set(preset.filters) | set(preset.columns), fields)

    def test_a_tag_panel_is_details_and_games(self) -> None:
        self.assertEqual(["tag_details", "tag_games"],
                         [item.key for item in rail.sections_for("tag")])


class StaleViewTests(unittest.TestCase):
    """A saved view outliving the fields it named."""

    FIELDS = ["name", "year", "manufacturer"]

    def _view(self, columns):
        return views.View(id="v", name="Mine", columns=tuple(columns))

    def test_a_view_naming_only_gone_fields_is_stale_rather_than_everything(self) -> None:
        """The old fallback showed every column here, which reads as the view
        misbehaving rather than as a view saved against fields that have since gone."""
        self.assertIsNone(views.visible_columns(self._view(["rom", "vbs_hash"]),
                                                self.FIELDS))

    def test_naming_no_columns_still_means_all_of_them(self) -> None:
        """A built-in carrying only a sort or a filter. Not the stale case."""
        self.assertEqual(views.visible_columns(self._view([]), self.FIELDS), self.FIELDS)

    def test_the_fields_that_survive_are_what_shows(self) -> None:
        self.assertEqual(
            views.visible_columns(self._view(["year", "rom", "name"]), self.FIELDS),
            ["year", "name"])


class ArrivalTests(unittest.TestCase):
    FIELDS = [definition["field"] for definition in games.COLUMNS]
    SORT = ({"colId": "name", "sort": "asc", "sortIndex": 0},)
    VIEW = views.View(id="v", name="Mine", columns=("name", "year"), sort=SORT)
    ARRIVED = ("name", "year", "vps_match")

    def _baseline(self):
        return views.arrived(self.VIEW, games.UNMATCHED, self.FIELDS)

    def test_the_screen_a_link_opened_is_not_modified(self) -> None:
        self.assertFalse(views.differs(self._baseline(), self.ARRIVED, self.SORT,
                                       games.UNMATCHED))

    def test_what_changes_after_it_is(self) -> None:
        baseline = self._baseline()
        resorted = ({"colId": "year", "sort": "desc", "sortIndex": 0},)
        for columns, sort, filters in ((self.ARRIVED, resorted, games.UNMATCHED),
                                       (self.ARRIVED, self.SORT, {}),
                                       (("name", "year"), self.SORT, games.UNMATCHED)):
            with self.subTest(columns=columns, sort=sort, filters=filters):
                self.assertTrue(views.differs(baseline, columns, sort, filters))

    def test_the_saved_view_is_left_as_it_was(self) -> None:
        self._baseline()
        self.assertEqual((self.VIEW.columns, self.VIEW.filters), (("name", "year"), {}))

    def test_nothing_this_grid_has_is_no_arrival(self) -> None:
        for over in (None, {}, {"gone": {"values": [True]}}):
            with self.subTest(over=over):
                self.assertIs(views.arrived(self.VIEW, over, self.FIELDS), self.VIEW)

    def test_a_stale_view_arrives_as_itself(self) -> None:
        stale = views.View(id="s", name="Old", columns=("gone",))
        self.assertIs(views.arrived(stale, games.UNMATCHED, self.FIELDS), stale)

    def test_a_view_saved_with_the_script_fix_column_loses_only_that_column(self) -> None:
        saved = views.View(id="s", name="Old", columns=("name", "script_fix", "year"),
                           filters={"script_fix": {"values": ["Fix available"]}})
        self.assertNotIn("script_fix", self.FIELDS)
        self.assertEqual(views.visible_columns(saved, self.FIELDS), ["name", "year"])
        self.assertIs(views.arrived(saved, {"script_fix": {"values": ["x"]}}, self.FIELDS),
                      saved)
        self.assertEqual(views.arrived(saved, games.UNMATCHED, self.FIELDS).filters,
                         games.UNMATCHED)

    def test_a_view_saved_with_only_the_script_fix_column_is_stale(self) -> None:
        saved = views.View(id="s", name="Old", columns=("script_fix",))
        self.assertIsNone(views.visible_columns(saved, self.FIELDS))


class OpenedViewTests(unittest.TestCase):
    FIELDS = ["name", "authors", "kind", "held"]
    START = views.View(id="builtin:everything", name="Everything", builtin=True,
                       columns=("name", "kind"), sort=({"colId": "name", "sort": "asc"},),
                       help="Everything")

    def test_it_is_the_starting_view_named_and_filtered_with_each_filtered_column_shown(
            self) -> None:
        over = {"authors": {"values": ["Wildman"]}, "kind": {"values": ["b2sFiles"]}}

        opened = views.opened(self.START, "Wildman Backglass", over, self.FIELDS)

        self.assertEqual((views.OPENED, "Wildman Backglass", over),
                         (opened.id, opened.name, opened.filters))
        self.assertEqual(("name", "kind", "authors"), opened.columns)
        self.assertEqual(self.START.sort, opened.sort)

    def test_it_is_read_only_so_it_is_never_among_the_views_that_are_kept(self) -> None:
        opened = views.opened(self.START, "Wildman", {}, self.FIELDS)

        self.assertTrue(opened.builtin)

    def test_a_filter_on_a_column_the_grid_does_not_have_is_left_off(self) -> None:
        opened = views.opened(self.START, "Wildman", {"gone": {"values": ["x"]}}, self.FIELDS)

        self.assertEqual({}, opened.filters)
        self.assertEqual(("name", "kind"), opened.columns)

    def test_it_reads_as_unmodified_until_the_screen_moves_off_it(self) -> None:
        over = {"authors": {"values": ["Wildman"]}}
        opened = views.opened(self.START, "Wildman", over, self.FIELDS)
        shown = ("name", "kind", "authors")

        self.assertFalse(views.differs(opened, shown, opened.sort, over))
        self.assertTrue(views.differs(opened, shown, opened.sort, {}))

    def test_a_record_saved_by_an_older_build_with_a_question_still_reads(self) -> None:
        record = views.to_record(views.View(id="v", name="Mine", columns=("name",)))
        record["asked"] = {"by": ["VPW"]}

        self.assertEqual("Mine", views.from_record(record).name)
