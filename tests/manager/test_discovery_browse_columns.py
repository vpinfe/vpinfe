"""All Games' Playable, Matched and kind columns, and the built-in view that waits for a count."""

from __future__ import annotations

import copy
import unittest
from typing import Any
from unittest import mock

from nicegui import ui

from manager.kit import grid, views
from manager.kit.data import Library
from manager.library import discovery, discovery_ask, games

_PAGE = ui.element()

BALLY = {"type": "maker", "name": "Bally"}
SPACE = {"type": "theme", "name": "Space"}


def _release(rid: str, *, fmt: str = "VPX", held: bool = False) -> dict[str, Any]:
    return {"id": rid, "by": [0], "added": "2020-01-01T00:00:00Z", "held": held,
            "mod": False, "version": "", "format": fmt}


def _entry(vps_id: str = "g1", releases: dict[str, list[dict[str, Any]]] | None = None,
           **over: Any) -> dict[str, Any]:
    return {"vps_id": vps_id, "name": "Attack from Mars", "manufacturer": "Bally",
            "year": 1995, "type": "SS", "themes": ["Space"], "url": "", "in_library": True,
            "listed": ["tableFiles", "b2sFiles"], "held": [],
            "releases": releases if releases is not None else {}, **over}


def _row(entry: dict[str, Any], *, counted: bool = True, formats: tuple[str, ...] = (),
         kept: list[str] | None = None) -> dict[str, Any]:
    found = {"counted": "2026-10-01T10:00:00Z" if counted else "", "rows": [entry]}
    return discovery.rows(found, kept, None, formats)[0]


def _browse_columns() -> list[dict[str, Any]]:
    built = [{"manufacturer": f"Maker {n}", "game_type": "SS"} for n in range(2)]
    return grid.with_derived_facets(discovery.COLUMNS, built, discovery.CATALOG_FACET_CEILING)


class PlayableColumn(unittest.TestCase):
    def test_a_game_with_a_table_the_launchers_play_is_playable(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", fmt="VPX")]}),
                   formats=("VPX",))

        self.assertEqual("yes", row[discovery.PLAYABLE])

    def test_a_game_whose_only_tables_are_another_format_is_not(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", fmt="FP")]}),
                   formats=("VPX",))

        self.assertEqual("no", row[discovery.PLAYABLE])

    def test_a_table_filed_under_no_format_counts_as_playable(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", fmt="")]}),
                   formats=("VPX",))

        self.assertEqual("yes", row[discovery.PLAYABLE])

    def test_launchers_that_name_no_format_play_any_table(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", fmt="FP")]}))

        self.assertEqual("yes", row[discovery.PLAYABLE])

    def test_a_game_with_no_table_is_not_playable(self) -> None:
        row = _row(_entry(releases={"b2sFiles": [_release("b1", fmt="")]}))

        self.assertEqual("no", row[discovery.PLAYABLE])

    def test_it_is_read_before_the_library_is_counted(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1")]}), counted=False)

        self.assertEqual("yes", row[discovery.PLAYABLE])

    def test_it_is_blank_where_the_library_collects_no_tables(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1")]}), kept=["b2sFiles"])

        self.assertEqual("", row[discovery.PLAYABLE])


class MatchedColumn(unittest.TestCase):
    def test_a_game_the_library_holds_a_release_of_reads_yes(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1"), _release("t2", held=True)]}))

        self.assertEqual("yes", row[discovery.HELD])

    def test_a_game_with_no_release_held_reads_no(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1")]}))

        self.assertEqual("no", row[discovery.HELD])

    def test_a_release_of_a_kind_the_library_does_not_collect_is_not_counted(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1")],
                                    "b2sFiles": [_release("b1", held=True)]}),
                   kept=["tableFiles"])

        self.assertEqual("no", row[discovery.HELD])

    def test_it_is_not_compared_until_the_library_is_counted(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", held=True)]}), counted=False)

        self.assertEqual("not_compared", row[discovery.HELD])

    def test_rows_of_reads_what_the_launchers_play(self) -> None:
        library = mock.Mock(spec=Library)
        found = {"counted": "x", "rows": [
            _entry("g1", releases={"tableFiles": [_release("t1", fmt="FP")]})]}
        with mock.patch.object(discovery, "kept_listings", return_value=None), \
                mock.patch.object(discovery.discovery_marks, "read", return_value=None), \
                mock.patch.object(discovery.launchers, "get_launcher_store"), \
                mock.patch.object(discovery.launchers, "playable_formats",
                                  return_value=("VPX",)):
            row = discovery.rows_of(library, found)[0]

        self.assertEqual("no", row[discovery.PLAYABLE])


class KindColumns(unittest.TestCase):
    def test_a_file_tied_to_a_release_of_the_kind_is_matched(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1"), _release("t2", held=True)]},
                          held=["tableFiles"]))

        self.assertEqual("matched", row["kind_table"])

    def test_a_file_tied_to_none_is_unmatched(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1")]}, held=["tableFiles"]))

        self.assertEqual("unmatched", row["kind_table"])

    def test_a_file_of_a_kind_the_game_does_not_list_is_unmatched(self) -> None:
        row = _row(_entry(held=["romFiles"]))

        self.assertEqual("unmatched", row["kind_rom"])

    def test_a_kind_listed_and_not_held_is_missing_and_one_not_listed_is_blank(self) -> None:
        row = _row(_entry(held=["tableFiles"]))

        self.assertEqual("missing", row["kind_backglass"])
        self.assertEqual("", row["kind_rom"])

    def test_a_game_not_in_the_library_is_missing_whatever_its_releases_say(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", held=True)]},
                          in_library=False, held=[]))

        self.assertEqual("missing", row["kind_table"])

    def test_a_kind_the_library_does_not_collect_is_blank(self) -> None:
        row = _row(_entry(releases={"b2sFiles": [_release("b1", held=True)]},
                          held=["tableFiles", "b2sFiles"]), kept=["tableFiles"])

        self.assertEqual("", row["kind_backglass"])

    def test_every_kind_is_not_compared_until_the_library_is_counted(self) -> None:
        row = _row(_entry(releases={"tableFiles": [_release("t1", held=True)]},
                          held=["tableFiles"]), counted=False)

        self.assertEqual({"not_compared", ""}, {row[field] for field in discovery._KIND_FIELDS})
        self.assertEqual(("not_compared", ""), (row["kind_table"], row["kind_pov"]))

    def test_each_kind_column_filters_on_its_states_with_a_line_each_in_the_tooltip(
            self) -> None:
        by_field = {one["field"]: one for one in discovery.COLUMNS}

        for _listed, field, _word in discovery.KINDS:
            with self.subTest(field):
                column = by_field[field]
                choices = column["filterParams"]["choices"]
                self.assertEqual(["matched", "unmatched", "missing", "not_compared", ""],
                                 [one["value"] for one in choices])
                self.assertEqual(["Matched", "Unmatched", "Missing", "Not Compared Yet",
                                  "Not Listed"],
                                 [one["label"] for one in choices])
                rows = column["headerTooltip"].split("\n")
                self.assertEqual(["Matched", "Unmatched", "Missing", "Not Compared Yet",
                                  "Not Listed"],
                                 [one.split(" - ")[0] for one in rows[1:]])

    def test_no_kind_has_a_column_of_its_own_for_what_is_held(self) -> None:
        fields = {one["field"] for one in discovery.COLUMNS}

        self.assertEqual(set(), {field for field in fields if field.startswith("held_")})

    def test_a_kind_the_library_does_not_collect_has_no_column(self) -> None:
        library = mock.Mock(spec=Library)
        with mock.patch.object(discovery, "kept",
                               side_effect=lambda _library, listed_as: listed_as != "romFiles"), \
                mock.patch.object(discovery.grid_module, "with_art",
                                  side_effect=lambda columns, _look: columns):
            fields = {one["field"] for one in discovery.grid_columns(library, [])}

        self.assertNotIn("kind_rom", fields)
        self.assertIn("kind_table", fields)


_SHOWN_ON_DEMAND = {discovery.PLAYABLE, discovery.HELD}


class TheColumnsAreNoViewsOwn(unittest.TestCase):
    def test_all_are_columns_of_the_grid(self) -> None:
        fields = {one["field"] for one in discovery.COLUMNS}

        self.assertLessEqual(_SHOWN_ON_DEMAND, fields)

    def test_no_built_in_view_shows_any(self) -> None:
        for name, preset in discovery.VIEWS.items():
            with self.subTest(name):
                assert not isinstance(preset, list)
                self.assertFalse(set(preset.columns) & _SHOWN_ON_DEMAND)

    def test_each_waits_for_a_count_but_playable(self) -> None:
        self.assertEqual(_SHOWN_ON_DEMAND - {discovery.PLAYABLE},
                         discovery.COUNTED_FIELDS & _SHOWN_ON_DEMAND)

    def test_a_view_opened_on_them_shows_what_it_filters(self) -> None:
        fields = [one["field"] for one in discovery.COLUMNS]
        start = views.builtins(discovery.VIEWS)[0]

        view = views.opened(start, "Bally", {discovery.PLAYABLE: {"values": ["yes"]}}, fields)

        self.assertIn(discovery.PLAYABLE, view.columns)
        self.assertNotIn(discovery.HELD, view.columns)

    def test_a_view_opened_on_a_kind_shows_that_kinds_column(self) -> None:
        fields = [one["field"] for one in discovery.COLUMNS]
        start = views.builtins(discovery.VIEWS)[0]

        view = views.opened(start, "Bally", {"kind_backglass": {"values": ["missing"]}}, fields)

        self.assertIn("kind_backglass", view.columns)

    def test_each_filters_on_yes_and_no_then_what_it_cannot_say(self) -> None:
        for field, last in ((discovery.PLAYABLE, [""]), (discovery.HELD, ["not_compared"])):
            with self.subTest(field):
                column = next(one for one in discovery.COLUMNS if one["field"] == field)
                choices = column["filterParams"]["choices"]
                self.assertEqual(["yes", "no", *last], [one["value"] for one in choices])


class OpenInGridCarries(unittest.TestCase):
    def _filters(self, subject: dict[str, str], question: dict[str, Any], *,
                 counted: bool = True) -> dict[str, Any]:
        _tab, opening = discovery.opening_for(subject, question, _browse_columns(),
                                              counted=counted)
        return opening["filters"]

    def test_playable_tables_set_the_playable_column(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "kind": "tableFiles",
                                        "format": ["VPX"]})

        self.assertEqual({"values": ["yes"]}, filters[discovery.PLAYABLE])

    def test_a_theme_page_carries_it_too(self) -> None:
        filters = self._filters(SPACE, {"theme": "Space", "kind": "tableFiles",
                                        "format": ["VPX"]})

        self.assertEqual({"values": ["yes"]}, filters[discovery.PLAYABLE])

    def test_no_format_asked_leaves_the_column_alone(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "kind": "tableFiles"})

        self.assertNotIn(discovery.PLAYABLE, filters)

    def test_playable_does_not_wait_for_a_count(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "kind": "tableFiles",
                                        "format": ["VPX"]}, counted=False)

        self.assertEqual({"manufacturer": {"values": ["Bally"]},
                          discovery.PLAYABLE: {"values": ["yes"]}}, filters)

    def test_without_on_every_kind_is_in_library_with_anything_missing(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "owned": "yes", "held": "missing"})

        self.assertEqual({"manufacturer": {"values": ["Bally"]},
                          "in_library": {"values": ["yes"]},
                          "gap": {"values": ["missing"]}}, filters)

    def test_without_on_a_kind_asks_for_that_kind_to_be_missing(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "kind": "b2sFiles", "owned": "yes",
                                        "held": "missing"})

        self.assertEqual({"values": ["missing"]}, filters["kind_backglass"])
        self.assertNotIn(discovery.HELD, filters)
        self.assertNotIn("gap", filters)

    def test_without_on_tables_asks_the_tables_column(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "kind": "tableFiles",
                                        "owned": "yes", "held": "missing"})

        self.assertEqual({"values": ["missing"]}, filters["kind_table"])
        self.assertNotIn("kind_backglass", filters)

    def test_without_waits_for_a_count(self) -> None:
        filters = self._filters(BALLY, {"maker": "Bally", "owned": "yes", "held": "missing"},
                                counted=False)

        self.assertEqual({"manufacturer": {"values": ["Bally"]}}, filters)


def _applied(filters: dict[str, Any], built: list[dict[str, Any]]) -> set[str]:
    """The ids of the rows the grid keeps under `filters`. A filter on a field the row does
    not carry is an error: skipping it would be a filter that excludes nothing."""
    kept = set()
    for row in built:
        for field, model in filters.items():
            if "values" in model:
                said = row[field] if isinstance(row[field], list) else [row[field]]
                if not set(said) & set(model["values"]):
                    break
            elif model.get("filterType") == "text" and model.get("type") == "equals":
                if row[field] != model["filter"]:
                    break
            else:
                raise AssertionError(f"no way to apply {field}: {model}")
        else:
            kept.add(str(row["id"]))
    return kept


def _game(vps_id: str, *, held: tuple[str, ...] = (), unheld: tuple[str, ...] = (),
          in_library: bool = True, maker: str = "Bally", formats: dict[str, str] | None = None,
          listed: tuple[str, ...] | None = None) -> dict[str, Any]:
    """A game with a release of each kind named: `held` ones held, with a file of the kind
    bound to it, `unheld` ones not. `formats` files a kind's release under a table format."""
    formats = formats or {}
    releases = {kind: [_release(f"{vps_id}-{kind}", held=kind in held,
                                fmt=formats.get(kind, "VPX"))]
                for kind in (*held, *unheld)}
    return _entry(vps_id, releases, manufacturer=maker, in_library=in_library,
                  listed=list(listed if listed is not None else releases), held=list(held))


TABLE, GLASS = "tableFiles", "b2sFiles"
PLAYS = ("VPX",)


class TheGridHoldsWhatThePageLists(unittest.TestCase):
    """Without on a maker's page against the grid it opens: both read the same library, the
    page through `games_found` and `in_show`, the grid through the filters the page hands
    it applied to All Games' own rows."""

    LIBRARY = [
        # A bound table and no backglass: the page counts it Without Backglasses.
        _game("table_only", held=(TABLE,), unheld=(GLASS,)),
        _game("holds_both", held=(TABLE, GLASS)),
        _game("holds_neither", unheld=(TABLE, GLASS)),
        _game("glass_only", held=(GLASS,), unheld=(TABLE,)),
        _game("tables_only_listed", unheld=(TABLE,)),
        _game("elsewhere", in_library=False, unheld=(TABLE, GLASS)),
        _game("other_maker", maker="Williams", unheld=(TABLE, GLASS)),
        # The library has a backglass for it that no VPS release is bound to.
        _entry("unbound_glass", {TABLE: [_release("t")], GLASS: [_release("b")]},
               manufacturer="Bally", listed=[TABLE, GLASS], held=[GLASS]),
        _entry("other_theme", {TABLE: [_release("o")]}, manufacturer="Bally",
               listed=[TABLE], themes=["Western"]),
        # Holds a table the launchers do not play, and has one they do that it lacks.
        _entry("holds_another_format", {
            TABLE: [_release("fp", fmt="FP", held=True), _release("vpx", fmt="VPX")],
            GLASS: [_release("glass")]}, manufacturer="Bally", listed=[TABLE, GLASS],
            held=[TABLE]),
    ]

    def _library(self, kept: list[str] | None = None
                 ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        found = {"counted": "x", "rows": copy.deepcopy(self.LIBRARY)}
        return (discovery.rows(found, kept, None, PLAYS),
                discovery.releases_of(found, [GLASS, TABLE] if kept is None else kept))

    def _page(self, kind: str, show: str, *, playing: bool = False, subject: dict[str, str]
              = BALLY, kept: list[str] | None = None) -> set[str]:
        built, releases = self._library(kept)
        question = discovery_ask.vps_question.clean({
            **discovery_ask.subject_question(subject), "kind": kind,
            "format": list(PLAYS) if kind == TABLE and playing else []})
        people = [{"name": "Someone", "aliases": []}]
        by_id = {str(row["id"]): row for row in built}
        fields = {listed_as: field for listed_as, field, _word in discovery.KINDS}
        found = discovery_ask.vps_question.games_found(question, people, built, releases)
        return {game for game, fitting in found.items()
                if discovery_ask.in_show(show, by_id[game], fitting, fields, of_author=False)}

    def _grid(self, kind: str, *, playing: bool = False, subject: dict[str, str] = BALLY,
              kept: list[str] | None = None) -> set[str]:
        built, _releases = self._library(kept)
        question = discovery_ask.vps_question.clean({
            **discovery_ask.subject_question(subject), "kind": kind, "owned": "yes",
            "held": "missing", "format": list(PLAYS) if kind == TABLE and playing else []})
        off = {field for listed_as, field, _word in discovery.KINDS
               if kept is not None and listed_as not in kept}
        columns = [one for one in _browse_columns() if one["field"] not in off]
        _tab, opening = discovery.opening_for(subject, question, columns, counted=True)
        return _applied(opening["filters"], built)

    def test_the_page_lists_the_bound_table_with_no_backglass(self) -> None:
        self.assertIn("table_only", self._page(GLASS, discovery_ask.WITHOUT))

    def test_without_backglasses_is_the_same_games_on_the_grid(self) -> None:
        self.assertEqual(self._page(GLASS, discovery_ask.WITHOUT), self._grid(GLASS))
        self.assertIn("table_only", self._grid(GLASS))

    def test_without_tables_is_the_same_games_on_the_grid(self) -> None:
        self.assertEqual(self._page(TABLE, discovery_ask.WITHOUT), self._grid(TABLE))
        self.assertNotIn("table_only", self._grid(TABLE))

    def test_without_on_every_kind_is_the_same_games_on_the_grid(self) -> None:
        self.assertEqual(self._page("", discovery_ask.WITHOUT), self._grid(""))
        self.assertIn("table_only", self._grid(""))

    def test_without_playable_tables_is_the_same_games_on_the_grid(self) -> None:
        page = self._page(TABLE, discovery_ask.WITHOUT, playing=True)

        self.assertEqual(page, self._grid(TABLE, playing=True))
        self.assertNotIn("holds_another_format", page)

    def test_a_game_the_library_lacks_is_not_without(self) -> None:
        for kind in ("", TABLE, GLASS):
            with self.subTest(kind):
                self.assertNotIn("elsewhere", self._grid(kind))

    def test_a_file_bound_to_no_release_reads_unmatched_and_is_not_without(self) -> None:
        built, _releases = self._library()
        row = next(one for one in built if one["id"] == "unbound_glass")

        self.assertEqual("unmatched", row["kind_backglass"])
        self.assertNotIn("unbound_glass", self._page(GLASS, discovery_ask.WITHOUT))
        self.assertNotIn("unbound_glass", self._grid(GLASS))

    def test_a_narrower_kept_set_reads_the_same_on_page_and_grid(self) -> None:
        wide = self._grid("")
        narrow = self._grid("", kept=[TABLE])

        self.assertEqual(self._page("", discovery_ask.WITHOUT, kept=[TABLE]), narrow)
        self.assertIn("table_only", wide)
        self.assertNotIn("table_only", narrow)

    def test_without_on_a_theme_page_is_the_same_games_on_the_grid(self) -> None:
        for kind in ("", TABLE, GLASS):
            with self.subTest(kind):
                page = self._page(kind, discovery_ask.WITHOUT, subject=SPACE)
                self.assertEqual(page, self._grid(kind, subject=SPACE))
        self.assertIn("other_maker", self._grid("", subject=SPACE))
        self.assertNotIn("other_theme", self._grid("", subject=SPACE))


class _StopError(Exception):
    pass


class _Answer:
    def __init__(self, value: Any = None) -> None:
        self.value = value

    def __await__(self):
        return self.value
        yield


class _Table:
    """A grid that keeps the columns shown and the filter model it was given, and answers
    with them as the real one does."""

    parent_slot = None
    id = 1

    def __init__(self, fields: list[str]) -> None:
        self.fields = fields
        self.shown: set[str] = set()
        self.model: Any = None

    def run_grid_method(self, name: str, *args: Any) -> _Answer:
        if name == "setColumnsVisible":
            self.shown = (self.shown | set(args[0])) if args[1] else (self.shown - set(args[0]))
        elif name == "setFilterModel":
            self.model = args[0]
        elif name == "getFilterModel":
            return _Answer(dict(self.model or {}))
        elif name == "getColumnState":
            return _Answer([{"colId": field, "hide": field not in self.shown}
                            for field in self.fields])
        return _Answer()

    def on(self, *_args: Any, **_kwargs: Any) -> None:
        pass


SAVED = views.View(id="mine", name="Mine", builtin=False, columns=("name", "held"),
                   filters={"held": {"values": ["no"]}, "manufacturer": {"values": ["Bally"]}})


class _Driven:
    def __init__(self, test: unittest.IsolatedAsyncioTestCase, *, counted: bool,
                 saved: views.View, active: str) -> None:
        self.record: dict[str, Any] = {"views": [views.to_record(saved)], "active": active}
        self.read = copy.deepcopy(self.record)
        self.library = mock.Mock()
        self.library.preferences.return_value = self.record
        self.fields = [one["field"] for one in discovery.COLUMNS]
        self.table = _Table(self.fields)
        self.counted = counted
        self.work: list[Any] = []
        self.asked: list[Any] = []
        self.ui = test.enterContext(mock.patch.object(games, "ui"))
        self.panel = test.enterContext(mock.patch.object(games, "panel"))

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        test.enterContext(mock.patch.object(games.busy, "fill",
                                            lambda _region, run: self.work.append(run)))
        test.enterContext(mock.patch.object(games.renderers, "draw_as"))
        test.enterContext(mock.patch.object(games.run, "io_bound", inline))
        test.enterContext(mock.patch.object(games.grid, "apply_layout", mock.AsyncMock()))
        test.enterContext(mock.patch.object(
            games, "_ask_name", lambda save, **_kw: self.asked.append(save)))
        test.enterContext(mock.patch("manager.kit.on_page.ui"))

    async def start(self) -> None:
        wire, *_ = games.view_control(
            self.library, discovery.SCOPE, discovery.VIEWS, self.fields, discovery.COLUMNS,
            bar=mock.MagicMock(),
            unfiltered=frozenset() if self.counted else discovery.COUNTED_FIELDS)
        wire(self.table)
        await self.work[0]()
        opener = self.ui.button.return_value.props.return_value.classes.return_value \
            .tooltip.return_value.on_click.call_args.args[0]
        self.opener = opener

    async def menu(self) -> list[Any]:
        """The save and the rename the view menu offers, in the order it lists them."""
        self.asked.clear()
        self.ui.menu_item.reset_mock()
        await self.opener()
        for call in self.ui.menu_item.call_args_list:
            action = call.args[1]
            if getattr(action, "__name__", "") == "<lambda>":
                action()
        return list(self.asked)

    def stored(self) -> list[dict[str, Any]]:
        return self.library.put_preferences.call_args.args[1]["views"]

    def marked_modified(self) -> bool:
        picker = self.panel.DescribedSelect.return_value.classes.return_value
        return any(call.kwargs.get("add") == "suffix=modified"
                   for call in picker.props.call_args_list)


class ViewsWaitForACount(unittest.IsolatedAsyncioTestCase):
    """What the view control puts on the grid, which is where an empty first screen came
    from. A saved view is the user's, so it keeps every filter it was saved with."""

    async def _driven(self, *, counted: bool, saved: views.View = SAVED,
                      active: str | None = None) -> _Driven:
        driven = _Driven(self, counted=counted, saved=saved, active=active or saved.id)
        await driven.start()
        return driven

    async def test_a_saved_view_on_held_opens_with_its_other_filters_before_a_count(self) -> None:
        driven = await self._driven(counted=False)

        self.assertEqual({"manufacturer": {"values": ["Bally"]}}, driven.table.model)

    async def test_after_a_count_it_filters_on_held_too(self) -> None:
        driven = await self._driven(counted=True)

        self.assertEqual(SAVED.filters, driven.table.model)

    async def test_applying_it_leaves_the_stored_views_as_they_were_read(self) -> None:
        driven = await self._driven(counted=False)

        self.assertEqual(driven.read, driven.record)
        driven.library.put_preferences.assert_not_called()

    async def test_it_does_not_read_as_modified_when_nothing_was_changed(self) -> None:
        driven = await self._driven(counted=False)

        self.assertFalse(driven.marked_modified())

    async def test_missing_games_opens_unfiltered_before_a_count(self) -> None:
        driven = await self._driven(
            counted=False, active=views.builtin_id("manager.view.missing_games"))

        self.assertFalse(driven.table.model)

    async def test_missing_games_filters_once_counted(self) -> None:
        driven = await self._driven(
            counted=True, active=views.builtin_id("manager.view.missing_games"))

        self.assertEqual({"in_library": {"values": ["yes"]}, "gap": {"values": ["missing"]}},
                         driven.table.model)


class SavingKeepsWhatWasHeldBack(unittest.IsolatedAsyncioTestCase):
    async def _driven(self, *, counted: bool) -> _Driven:
        driven = _Driven(self, counted=counted, saved=SAVED, active=SAVED.id)
        await driven.start()
        return driven

    async def test_saving_over_a_view_before_a_count_keeps_its_held_filter(self) -> None:
        driven = await self._driven(counted=False)
        save, _rename = await driven.menu()

        await save("Mine")

        (kept,) = driven.stored()
        self.assertEqual((SAVED.id, SAVED.filters), (kept["id"], kept["filters"]))

    async def test_saving_over_a_view_after_a_count_keeps_both_too(self) -> None:
        driven = await self._driven(counted=True)
        save, _rename = await driven.menu()

        await save("Mine")

        (kept,) = driven.stored()
        self.assertEqual(SAVED.filters, kept["filters"])

    async def test_saving_under_a_new_name_starts_from_the_views_held_filters(self) -> None:
        driven = await self._driven(counted=False)
        save, _rename = await driven.menu()

        await save("Another")

        old, new = driven.stored()
        self.assertEqual(SAVED.filters, old["filters"])
        self.assertEqual(SAVED.filters, new["filters"])
        self.assertNotEqual(old["id"], new["id"])

    async def test_a_filter_set_on_the_grid_for_a_held_back_column_wins(self) -> None:
        driven = await self._driven(counted=False)
        driven.table.model = {"manufacturer": {"values": ["Bally"]},
                              "held": {"values": ["yes"]}}
        save, _rename = await driven.menu()

        await save("Mine")

        self.assertEqual({"values": ["yes"]}, driven.stored()[0]["filters"]["held"])

    async def test_what_the_grid_changed_is_saved(self) -> None:
        driven = await self._driven(counted=False)
        driven.table.model = {"manufacturer": {"values": ["Stern"]}}
        save, _rename = await driven.menu()

        await save("Mine")

        self.assertEqual({"held": {"values": ["no"]}, "manufacturer": {"values": ["Stern"]}},
                         driven.stored()[0]["filters"])

    async def test_a_view_just_saved_does_not_read_as_modified(self) -> None:
        driven = await self._driven(counted=False)
        save, _rename = await driven.menu()

        await save("Mine")

        self.assertFalse(driven.marked_modified())

    async def test_renaming_before_a_count_keeps_the_held_filter(self) -> None:
        driven = await self._driven(counted=False)
        _save, rename = await driven.menu()

        await rename("Renamed", "")

        (kept,) = driven.stored()
        self.assertEqual(("Renamed", SAVED.filters), (kept["name"], kept["filters"]))


class WithoutFilters(unittest.TestCase):
    VIEW = views.View(id="v", name="V", builtin=False,
                      filters={"held": {"values": ["no"]}, "gap": {"values": ["missing"]},
                               "manufacturer": {"values": ["Bally"]}})

    def test_a_filter_on_another_column_stays(self) -> None:
        shown = views.without_filters(self.VIEW, {"held", "gap"})

        self.assertEqual({"manufacturer": {"values": ["Bally"]}}, shown.filters)

    def test_the_view_it_was_given_is_not_changed(self) -> None:
        views.without_filters(self.VIEW, {"held", "gap"})

        self.assertEqual({"held", "gap", "manufacturer"}, set(self.VIEW.filters))

    def test_a_view_with_nothing_to_drop_is_returned_as_it_is(self) -> None:
        self.assertIs(self.VIEW, views.without_filters(self.VIEW, {"year"}))
        self.assertIs(self.VIEW, views.without_filters(self.VIEW, ()))

    def test_the_counted_columns_are_the_ones_a_count_fills(self) -> None:
        listed = [listed_as for listed_as, _field, _word in discovery.KINDS]
        entry = _entry(releases={"tableFiles": [_release("t1", held=True)]},
                       listed=listed, held=listed[:1])
        before = discovery.rows({"counted": "", "rows": [entry]})[0]
        after = discovery.rows({"counted": "x", "rows": [entry]})[0]
        columns = {one["field"] for one in discovery.COLUMNS}

        filled = {field for field in columns if before[field] != after[field]}

        self.assertEqual(filled, discovery.COUNTED_FIELDS)


class BrowseHoldsBackTheCountedFilters(unittest.IsolatedAsyncioTestCase):
    """What `_fill` hands the view control."""

    async def _handed(self, counted: bool) -> Any:
        client = mock.Mock()
        client.library_policy.return_value = {"hidden_asset_kinds": [], "hidden_media_kinds": []}
        client.discovery_marks.return_value = []
        client.preferences.return_value = {}
        library = Library(client)
        found = {"counted": "x" if counted else "", "people": [], "rows": [_entry()]}
        seen: dict[str, Any] = {}

        def view_control(_library: Any, _scope: str, _presets: Any, *_more: Any,
                         **kwargs: Any) -> Any:
            seen.update(kwargs)
            raise _StopError

        async def inline(call: Any, *args: Any, **more: Any) -> Any:
            return call(*args, **more)

        with _PAGE:
            body = ui.column()
        with mock.patch("manager.library.games.view_control", view_control), \
                mock.patch.object(discovery.run, "io_bound", inline), \
                mock.patch.object(discovery, "_read", mock.AsyncMock(return_value=found)), \
                mock.patch.object(discovery.discovery_marks, "refresh", mock.AsyncMock()), \
                mock.patch.object(discovery.launchers, "get_launcher_store"), \
                mock.patch.object(discovery.deeplink, "sync"):
            with self.assertRaises(_StopError):
                await discovery._fill(library, body, {}, mock.AsyncMock(), mock.Mock())
        return seen["unfiltered"]

    async def test_before_a_count_the_counted_columns_are_held_back(self) -> None:
        self.assertEqual(discovery.COUNTED_FIELDS, await self._handed(counted=False))

    async def test_after_a_count_nothing_is(self) -> None:
        self.assertFalse(await self._handed(counted=True))


if __name__ == "__main__":
    unittest.main()
