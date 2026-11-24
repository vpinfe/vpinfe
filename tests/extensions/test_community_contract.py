"""A list an extension declares for Community, and the ones it may not."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

import httpapi
from common import extensions
from common.extensions import host, store
from common.extensions.context import ContractError, ExtensionUI, check_references
from tests.support.accepted import owner_accepts
from tests.support.identity import IDENTITY

ROOT = Path(__file__).resolve().parents[1] / "fixtures" / "community"
WEEK = "https://challenge.example/week"

COLUMNS = [{"field": "name", "header": "Table"},
           {"field": "plays", "header": "Plays", "kind": "number"},
           {"field": "vpsId", "header": "VPS"}]
GAMES = [{"field": "vpsId", "header": "VPS", "kind": "game"}, *COLUMNS[:2]]
TABLES = [{"field": "vpsId", "header": "VPS", "kind": "table"}, *COLUMNS[:2]]


def _rows() -> list[dict]:
    return []


def _mark(rows: list) -> None:
    return None


class _Dialog:
    def ask(self, step: str) -> dict:
        return {"fields": []}


class TheDeclaration(unittest.TestCase):
    def setUp(self) -> None:
        self.ui = ExtensionUI("site", allowed=True)

    def test_a_list_is_recorded_as_data(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=GAMES,
                     views=[{"key": "plays", "name": "Most played",
                             "columns": ["name", "plays"],
                             "sort": [{"field": "plays", "desc": True}]}], area="community")

        (said,) = self.ui.declared_lists
        self.assertEqual(("tables", ["game", "text", "number"],
                          [{"field": "plays", "desc": True}], "vps_entry"),
                         (said["key"], [one["kind"] for one in said["columns"]],
                          said["views"][0]["sort"], said["relation"]["keys"]))

    def test_a_kind_core_does_not_draw_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site",
                         columns=[{"field": "art", "header": "Art", "kind": "html"}],
                         area="community")

    def test_a_view_on_a_column_it_does_not_have_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=COLUMNS,
                         views=[{"key": "top", "sort": [{"field": "rating"}]}], area="community")

    def test_a_view_with_no_key_is_refused(self) -> None:
        """The key is what its words are found by, in the extension's catalog."""
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, columns=COLUMNS,
                         views=[{"name": "Top", "sort": [{"field": "plays"}]}], area="community")

    def test_a_line_under_the_first_column_is_kept(self) -> None:
        self.ui.list("tables", _rows, title="Site",
                     columns=[{**COLUMNS[0], "under": ["maker", "year"]}, *COLUMNS[1:]],
                     area="community")

        self.assertEqual(["maker", "year"],
                         self.ui.declared_lists[0]["columns"][0]["under"])

    def test_a_line_under_any_other_column_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site",
                         columns=[COLUMNS[0], {**COLUMNS[1], "under": ["year"]}], area="community")

    def _drawn(self, *more: dict) -> list[dict]:
        self.ui.list("tables", _rows, title="Site", columns=[COLUMNS[0], *more], area="community")
        return self.ui.declared_lists[0]["columns"][1:]

    def test_a_state_keeps_its_looks_in_the_order_given(self) -> None:
        (column,) = self._drawn({"field": "status", "kind": "state",
                                 "states": {"live": {"tier": "on"}, "late": {"icon": "schedule"},
                                            "gone": {}}})

        self.assertEqual({"live": {"tier": "on"}, "late": {"icon": "schedule"}, "gone": {}},
                         column["states"])
        self.assertEqual(["live", "late", "gone"], list(column["states"]))

    def test_a_link_names_which_row_field_it_reads_as(self) -> None:
        (column,) = self._drawn({"field": "page", "kind": "link", "name_from": "title"})

        self.assertEqual("title", column["name_from"])

    def test_chips_and_pictures_take_nothing_more(self) -> None:
        columns = self._drawn({"field": "tags", "kind": "chips"},
                              {"field": "art", "kind": "picture"})

        self.assertEqual(["chips", "picture"], [one["kind"] for one in columns])

    def test_a_state_with_no_states_is_refused(self) -> None:
        for column in ({"field": "s", "kind": "state"},
                       {"field": "s", "kind": "state", "states": {}}):
            with self.subTest(column=column), self.assertRaises(ContractError):
                self._drawn(column)

    def test_a_state_look_is_a_tier_or_an_icon_and_not_both(self) -> None:
        looks: tuple[Any, ...] = ({"tier": "loud"}, {"tier": "on", "icon": "check"},
                                  {"color": "red"}, {"tier": ["on"]}, {"tier": {}},
                                  {"tier": None}, {"icon": ""}, {"icon": 3}, "on")
        for look in looks:
            with self.subTest(look=look), self.assertRaises(ContractError):
                self._drawn({"field": "s", "kind": "state", "states": {"x": look}})

    def test_a_state_value_is_a_non_empty_string_with_no_dot(self) -> None:
        wrong_states: tuple[Any, ...] = ({"": {}}, {3: {}}, {"open.why": {}})
        for states in wrong_states:
            with self.subTest(states=states), self.assertRaises(ContractError):
                self._drawn({"field": "s", "kind": "state", "states": states})

    def test_states_and_name_from_belong_to_their_own_kind(self) -> None:
        for column in ({"field": "s", "states": {"x": {}}},
                       {"field": "s", "kind": "link", "states": {"x": {}}},
                       {"field": "s", "name_from": "title"},
                       {"field": "s", "kind": "state", "states": {"x": {}}, "name_from": "t"},
                       {"field": "s", "kind": "link", "name_from": 3},
                       {"field": "s", "kind": "link", "says": "title"}):
            with self.subTest(column=column), self.assertRaises(ContractError):
                self._drawn(column)

    def test_a_column_key_core_does_not_read_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self._drawn({"field": "s", "color": "red"})

    def test_a_cell_kind_is_never_the_identifier(self) -> None:
        for kind in ("state", "chips", "link", "picture"):
            column: dict[str, Any] = {"field": "name", "kind": kind, "identifier": True,
                      **({"states": {"x": {}}} if kind == "state" else {})}
            with self.subTest(kind=kind), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, title="Site", columns=[column, *COLUMNS[1:]],
                             area="community")

    def test_the_identifier_is_the_first_column_that_draws_its_own_text(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=[
            {"field": "status", "kind": "state", "states": {"x": {}}}, *COLUMNS], area="community")

        self.assertEqual(["status", "name"],
                         [one["field"] for one in self.ui.declared_lists[0]["columns"]][:2])
        self.assertEqual([False, True, False, False],
                         [one["identifier"] for one in self.ui.declared_lists[0]["columns"]])

    def test_a_list_names_its_identifier_where_it_sits(self) -> None:
        self.ui.list("tables", _rows, title="Site",
                     columns=[*COLUMNS[:2], {**GAMES[0], "identifier": True, "under": ["plays"]}],
                     area="community")

        (said,) = self.ui.declared_lists
        self.assertEqual([False, False, True], [one["identifier"] for one in said["columns"]])
        self.assertEqual({"field": "vpsId", "keys": "vps_entry"}, said["relation"])

    def test_two_identifiers_or_a_line_elsewhere_are_refused(self) -> None:
        wrong_columns: tuple[list[dict[str, Any]], ...] = (
            [{**COLUMNS[0], "identifier": True}, {**COLUMNS[1], "identifier": True}],
            [COLUMNS[0], {**COLUMNS[1], "under": ["plays"]}],
            [COLUMNS[0], {**COLUMNS[1], "picture": "art"}],
            [{**COLUMNS[0], "identifier": True}, {**COLUMNS[1], "icon": "star"}])
        for columns in wrong_columns:
            with self.subTest(columns=columns), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, title="Site", columns=columns, area="community")

    def test_a_list_of_only_cells_has_no_name_to_draw(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=[
                {"field": "s", "kind": "state", "states": {"x": {}}},
                {"field": "c", "kind": "chips"}], area="community")

    def test_ids_other_than_vps_and_vpinfe_are_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site",
                         columns=[{**GAMES[0], "keys": "rom"}, *GAMES[1:]], area="community")

    def test_the_identifier_is_what_the_list_relates_to(self) -> None:
        for column, keys in ((GAMES[0], "vps_entry"), (TABLES[0], "vps_release"),
                             ({**GAMES[0], "keys": "vpinfe"}, "vpinfe_game"),
                             ({**TABLES[0], "keys": "vpinfe"}, "vpinfe_table")):
            with self.subTest(keys=keys):
                ui = ExtensionUI("site", allowed=True)
                ui.list("tables", _rows, title="Site", columns=[column, *COLUMNS[:2]],
                        area="community")
                self.assertEqual({"field": "vpsId", "keys": keys},
                                 ui.declared_lists[0]["relation"])

    def test_a_game_column_that_is_not_the_identifier_relates_to_nothing(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=[*COLUMNS[:2], GAMES[0]],
                     area="community")

        self.assertIsNone(self.ui.declared_lists[0]["relation"])

    def test_only_the_identifier_has_a_frame(self) -> None:
        for named in ("picture", "icon"):
            with self.subTest(named=named), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, title="Site",
                             columns=[*COLUMNS[:2], {"field": "x", named: "image"}],
                             area="community")

    def test_keys_and_name_from_belong_to_the_columns_that_read_them(self) -> None:
        for column in ({"field": "x", "keys": "vps"}, {"field": "x", "name_from": "name"}):
            with self.subTest(column=column), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, title="Site", columns=[*COLUMNS[:2], column],
                             area="community")

    def test_a_list_takes_an_icon_of_the_rail_and_refuses_any_other(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=TABLES, icon="leaderboard",
                     area="community")
        self.ui.list("plain", _rows, title="Plain", columns=TABLES, area="community")

        self.assertEqual(["leaderboard", ""], [one["icon"] for one in self.ui.declared_lists])
        with self.assertRaisesRegex(ContractError, "'nope'"):
            self.ui.list("other", _rows, title="Other", columns=TABLES, icon="nope",
                         area="community")

    def test_a_tag_is_recorded_as_a_tag_would_be_stored(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=TABLES,
                     tag=" Weekly   Challenge ", area="community")

        self.assertEqual("Weekly Challenge", self.ui.declared_lists[0]["tag"])

    def test_a_tag_on_a_list_that_relates_to_nothing_is_refused(self) -> None:
        """The tag lands on what the list relates to, so without a relation it would
        land nowhere and say nothing about why."""
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=COLUMNS, tag="Challenge",
                         area="community")

    def test_a_view_ranks_by_numbers_and_dates(self) -> None:
        self.ui.list("scores", _rows, title="Site",
                     columns=[*TABLES, {"field": "set", "kind": "date"}],
                     views=[{"key": "top", "ranks": True,
                             "sort": [{"field": "plays", "desc": True},
                                      {"field": "set"}]},
                            {"key": "all", "sort": [{"field": "name"}]}], area="community")

        self.assertEqual([True, False],
                         [one["ranks"] for one in self.ui.declared_lists[0]["views"]])

    def test_a_ranked_view_sorting_on_text_is_refused(self) -> None:
        """A ranking is a number a game has; a name sorts but ranks nothing."""
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=GAMES,
                         views=[{"key": "top", "ranks": True,
                                 "sort": [{"field": "plays", "desc": True},
                                          {"field": "name"}]}], area="community")

    def test_a_ranked_view_with_no_sort_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=GAMES,
                         views=[{"key": "top", "ranks": True}], area="community")

    def test_a_ranked_view_of_a_list_that_relates_to_nothing_is_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, title="Site", columns=COLUMNS,
                         views=[{"key": "top", "ranks": True,
                                 "sort": [{"field": "plays", "desc": True}]}], area="community")

    def test_it_needs_the_capability_to_draw(self) -> None:
        with self.assertRaises(ContractError):
            ExtensionUI("site", allowed=False).list("tables", _rows, columns=COLUMNS)

    def test_the_routes_core_serves_it_on_are_recorded(self) -> None:
        self.ui.list("tables", _rows, title="Site", columns=COLUMNS, area="community")

        (said,) = self.ui.declared_lists
        self.assertEqual(("/lists/tables", "/lists/tables/about"),
                         (said["base"], said["about"]))

    def test_a_list_goes_where_it_says(self) -> None:
        self.ui.list("tables", _rows, columns=COLUMNS, area="community")
        self.ui.list("scores", _rows, columns=COLUMNS, area="library")

        self.assertEqual(["community", "library"],
                         [one["area"] for one in self.ui.declared_lists])

    def test_a_list_with_no_area_is_refused_in_words(self) -> None:
        for area in ("", None):
            with self.subTest(area=area), self.assertRaisesRegex(
                    ContractError, "the list 'tables' no area"):
                self.ui.list("tables", _rows, columns=COLUMNS, area=area)  # type: ignore[arg-type]
        self.assertEqual([], self.ui.declared_lists)

    def test_an_area_core_does_not_draw_is_refused(self) -> None:
        for area in ("Community", "sidebar", "provides"):
            with self.subTest(area=area), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, columns=COLUMNS, area=area)
        self.assertEqual([], self.ui.declared_lists)

    def test_rows_that_are_not_a_function_are_refused(self) -> None:
        wrong_rows: tuple[Any, ...] = ("/community/tables", None, [{"name": "AFM"}])
        for rows in wrong_rows:
            with self.subTest(rows=rows), self.assertRaises(ContractError):
                self.ui.list("tables", rows, columns=COLUMNS, area="community")

    def test_a_status_or_acts_that_are_not_what_core_calls_are_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, columns=COLUMNS, status="/tables/about", area="community")  # type: ignore[arg-type]
        with self.assertRaises(ContractError):
            self.ui.list("scores", _rows, columns=COLUMNS, acts=["post"], area="community")

    def test_a_key_declared_twice_is_refused(self) -> None:
        self.ui.list("tables", _rows, columns=COLUMNS, area="community")

        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, columns=COLUMNS, area="community")

    def test_verbs_are_recorded_as_data_without_their_functions(self) -> None:
        self.ui.list("tables", _rows, columns=COLUMNS, verbs=[
            {"key": "mark", "run": _mark, "danger": True, "needs": "ok", "one": True,
             "label": "Mark"},
            {"key": "open", "link": "page"}], area="community")

        (said,) = self.ui.declared_lists
        self.assertEqual(
            [{"key": "mark", "label": "Mark", "link": "", "action": "", "each": False,
              "danger": True, "needs": "ok", "one": True},
             {"key": "open", "label": "", "link": "page", "action": "", "each": False,
              "danger": False, "needs": "", "one": True}], said["verbs"])
        self.assertIs(self.ui.list_functions["tables"].verbs["mark"]["run"], _mark)

    def test_a_verb_core_cannot_read_is_refused_with_what_it_reads(self) -> None:
        with self.assertRaises(ContractError) as raised:
            self.ui.list("tables", _rows, columns=COLUMNS,
                         verbs=[{"key": "mark", "run": _mark, "confirm": "Sure?"}],
                         area="community")

        self.assertIn("confirm", str(raised.exception))
        self.assertIn("danger", str(raised.exception))
        self.assertEqual([], self.ui.declared_lists)

    def test_a_verb_wrongly_given_is_refused(self) -> None:
        wrong: dict[str, Any] = {
            "not a dict": ["mark"],
            "no key": {"run": _mark},
            "neither run nor link": {"key": "mark"},
            "both run and link": {"key": "mark", "run": _mark, "link": "page"},
            "run that is not a function": {"key": "mark", "run": "mark"},
            "danger on a link": {"key": "open", "link": "page", "danger": True},
            "needs on a link": {"key": "open", "link": "page", "needs": "ok"},
        }
        for what, verb in wrong.items():
            with self.subTest(what), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, columns=COLUMNS, verbs=[verb], area="community")
        self.assertEqual([], self.ui.declared_lists)

    def test_a_verb_that_opens_a_dialog_is_recorded_with_whether_it_walks_the_rows(self) -> None:
        self.ui.action("note", _Dialog())
        self.ui.list("tables", _rows, columns=COLUMNS, verbs=[
            {"key": "note", "action": "note", "each": True},
            {"key": "tag", "action": "note"}], add=["note"], area="community")

        (said,) = self.ui.declared_lists
        self.assertEqual([("note", "note", True), ("tag", "note", False)],
                         [(one["key"], one["action"], one["each"]) for one in said["verbs"]])
        self.assertEqual(["note"], said["add"])
        self.assertIsNone(self.ui.list_functions["tables"].verbs["note"]["run"])
        check_references(self.ui)

    def test_a_verb_that_opens_a_dialog_wrongly_is_refused(self) -> None:
        wrong: dict[str, Any] = {
            "action and run": {"key": "n", "action": "note", "run": _mark},
            "action and link": {"key": "n", "action": "note", "link": "page"},
            "action that is not a name": {"key": "n", "action": 3},
            "danger on a dialog": {"key": "n", "action": "note", "danger": True},
            "each without a dialog": {"key": "n", "run": _mark, "each": True},
        }
        for what, verb in wrong.items():
            with self.subTest(what), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, columns=COLUMNS, verbs=[verb], area="community")
        self.assertEqual([], self.ui.declared_lists)

    def test_add_is_a_dialog_name_or_several_and_nothing_else(self) -> None:
        wrong_adds: tuple[Any, ...] = (3, [3], ["a", "a"], ["a", " a"], [""], ["  "])
        for add in wrong_adds:
            with self.subTest(add=add), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, columns=COLUMNS, add=add, area="community")
        self.assertEqual([], self.ui.declared_lists)

    def test_a_pane_is_a_function_and_saving_it_needs_one_to_save_into(self) -> None:
        wrong_panes: tuple[dict[str, Any], ...] = (
            {"detail": "pane"}, {"detail": _rows, "save": "write"}, {"save": _mark})
        for what in wrong_panes:
            with self.subTest(what=what), self.assertRaises(ContractError):
                self.ui.list("tables", _rows, columns=COLUMNS, **what, area="community")
        self.assertEqual([], self.ui.declared_lists)

        self.ui.list("tables", _rows, columns=COLUMNS, detail=_rows, save=_mark, area="community")  # type: ignore[arg-type]
        self.ui.list("plain", _rows, columns=COLUMNS, area="community")

        self.assertEqual(["/lists/tables/detail", ""],
                         [one["detail"] for one in self.ui.declared_lists])

    def test_a_dialog_a_list_names_but_the_extension_never_declared_is_refused(self) -> None:
        for what in ({"add": "missing"},
                     {"verbs": [{"key": "n", "action": "missing"}]},
                     {"acts": {"open": {"action": "missing"}}}):
            with self.subTest(what=what):
                ui = ExtensionUI("site", allowed=True)
                ui.list("tables", _rows, columns=COLUMNS, **what, area="community")
                with self.assertRaises(ContractError) as raised:
                    check_references(ui)
                self.assertIn("missing", str(raised.exception))

    def test_an_act_whose_action_is_not_a_key_is_refused_at_load(self) -> None:
        for action in (3, ["x"], None, ""):
            with self.subTest(action=action):
                ui = ExtensionUI("site", allowed=True)
                ui.list("tables", _rows, columns=COLUMNS, acts={"open": {"action": action}},
                        area="community")
                with self.assertRaises(ContractError):
                    check_references(ui)

    def test_two_verbs_with_one_key_are_refused(self) -> None:
        with self.assertRaises(ContractError):
            self.ui.list("tables", _rows, columns=COLUMNS,
                         verbs=[{"key": "mark", "run": _mark}, {"key": "mark", "run": _mark}],
                         area="community")


class HowTheListStands(unittest.TestCase):
    """The fixture's Ratings list answers a line and its acts from its settings, through
    the routes core writes."""

    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.store = store.ExtensionStore(Path(tmp.name))
        self.registry = host.Registry(self.store)
        extensions.set_registry(self.registry)
        self.addCleanup(extensions.set_registry, host.Registry())
        self.addCleanup(self.registry.clear)
        self.registry.load_from(ROOT)
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def about(self) -> dict:
        answer = self.client.get("/ext/challenge/lists/ratings/about")
        answer.raise_for_status()
        return answer.json()

    def test_the_declaration_names_the_route_for_core(self) -> None:
        (found,) = [one for one in self.client.get("/extensions").json()["extensions"]
                    if one["name"] == "challenge"]

        self.assertEqual({"machines": "/lists/machines/about",
                          "releases": "/lists/releases/about",
                          "ratings": "/lists/ratings/about",
                          "builds": "/lists/builds/about"},
                         {one["key"]: one["about"] for one in found["lists"]})
        self.assertEqual({"community"}, {one["area"] for one in found["lists"]})

    def test_a_line_and_the_acts_that_can_work_now(self) -> None:
        self.store.set_setting("challenge", "status", "Ends Sunday")
        self.store.set_setting("challenge", "status_to", "settings")
        self.store.set_setting("challenge", "waiting", "afm,mm")

        self.assertEqual({"status": {"text": "Ends Sunday", "to": "settings"},
                          "acts": [{"key": "post", "label": "Post Now"},
                                   {"key": "week", "label": "This Week", "url": WEEK}]},
                         self.about())

    def test_an_act_is_pressed_on_the_route_under_it(self) -> None:
        self.store.set_setting("challenge", "waiting", "afm,mm")

        said = self.client.post("/ext/challenge/lists/ratings/about/acts/post")

        self.assertEqual((200, {"message": "Posted 2"}), (said.status_code, said.json()))
        self.assertEqual(["week"], [one["key"] for one in self.about()["acts"]])

    def test_an_act_the_list_does_not_offer_now_is_not_found(self) -> None:
        said = self.client.post("/ext/challenge/lists/ratings/about/acts/post")

        self.assertEqual(404, said.status_code)

    def test_a_stopped_extension_answers_neither(self) -> None:
        self.registry.disable("challenge", "switched off")

        self.assertEqual(
            (501, 501),
            (self.client.get("/ext/challenge/lists/ratings/about").status_code,
             self.client.post("/ext/challenge/lists/ratings/about/acts/post").status_code))


def _extension(root: Path, name: str, *titles: tuple[str, str]) -> Path:
    """A folder holding an extension whose lists are `(area, title)`, titled as written."""
    folder = root / name
    folder.mkdir()
    (folder / "extension.json").write_text(json.dumps({
        "name": name, "display_name": name.title(), "version": "1.0.0",
        "requires_platform": 1, **IDENTITY, "capabilities": ["ui:mount"]}))
    declared = "".join(
        f"    ctx.ui.list('l{n}', lambda: [], area={area!r}, title={title!r},"
        " columns=[{'field': 'name', 'header': 'Name'}])\n"
        for n, (area, title) in enumerate(titles))
    (folder / "__init__.py").write_text(f"def register(ctx):\n{declared}")
    return folder


class WhatAListMayBeTitled(unittest.TestCase):
    def setUp(self) -> None:
        owner_accepts(self)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        state = self.root / "state"
        state.mkdir()
        self.registry = host.Registry(store.ExtensionStore(state))
        self.addCleanup(self.registry.clear)

    def load(self, name: str, *titles: tuple[str, str]) -> host.Record:
        return self.registry.load(_extension(self.root, name, *titles))

    def test_a_list_titled_as_a_core_page_does_not_load(self) -> None:
        record = self.load("site", ("community", "Games"))

        self.assertEqual(host.FAILED, record.state)
        self.assertIn("Games", record.error)

    def test_the_comparison_ignores_case(self) -> None:
        self.assertEqual(host.FAILED, self.load("site", ("system", "ABOUT")).state)

    def test_two_lists_with_one_title_in_one_area_do_not_both_load(self) -> None:
        self.assertEqual(host.LOADED, self.load("one", ("community", "Scores")).state)
        self.assertEqual(host.FAILED, self.load("two", ("community", "scores")).state)

    def test_one_title_in_two_areas_is_two_lists(self) -> None:
        self.assertEqual(host.LOADED, self.load("one", ("community", "Scores")).state)
        self.assertEqual(host.LOADED, self.load("two", ("library", "Scores")).state)

    def test_the_failed_one_leaves_nothing_declared(self) -> None:
        record = self.load("site", ("community", "Games"))

        self.assertEqual([], record.declared_lists)


if __name__ == "__main__":
    unittest.main()
