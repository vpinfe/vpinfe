"""A kept report's games as groups, the addresses that lead to them, and the links drawn
for a group that holds more than a few."""

from __future__ import annotations

import unittest
from typing import Any
from urllib.parse import parse_qs, parse_qsl, urlparse

from nicegui import ui

from manager.kit import deeplink, panel, report_groups, wizard
from manager.system import job_report

JOB = "7f3a"


def _result(*, made: int = 0, held: int = 0, failed: int = 0) -> dict[str, Any]:
    return {"rows": [{"name": f"New {n}", "game_id": f"n{n}", "existing": False}
                     for n in range(made)]
            + [{"name": f"Bad {n}", "game_id": "", "error": "no table"} for n in range(failed)],
            "already_here": [{"key": f"k{n}", "name": f"Old {n}", "game_id": f"o{n}"}
                             for n in range(held)]}


def _job(**counts: int) -> dict[str, Any]:
    return {"id": JOB, "state": "done", "result": _result(**counts)}


class WhichGamesAreInAGroup(unittest.TestCase):
    def test_imported_is_the_games_this_run_made(self) -> None:
        found = report_groups.groups(_job(made=4, failed=2))

        self.assertEqual(("n0", "n1", "n2", "n3"), found[report_groups.IMPORTED].game_ids)

    def test_a_game_this_run_only_filled_is_not_imported(self) -> None:
        job = _job(made=1)
        job["result"]["rows"].append({"name": "Filled", "game_id": "f1", "existing": True})

        self.assertEqual(("n0",), report_groups.groups(job)[report_groups.IMPORTED].game_ids)

    def test_a_game_that_came_without_a_game_file_is_not_imported_for_tables(self) -> None:
        job = _job(made=1)
        job["result"]["rows"].append(
            {"name": "Art only", "game_id": "a1", "existing": False, "table": False})

        self.assertEqual(("n0",), report_groups.groups(job)[report_groups.IMPORTED].game_ids)

    def test_already_here_is_the_games_left_alone(self) -> None:
        found = report_groups.groups(_job(held=2))

        self.assertEqual(("o0", "o1"), found[report_groups.ALREADY_HERE].game_ids)

    def test_a_group_with_no_game_is_absent(self) -> None:
        self.assertEqual({}, report_groups.groups(_job(failed=3)))
        self.assertEqual({}, report_groups.groups({"state": "failed"}))

    def test_an_already_here_row_with_no_game_id_is_not_counted(self) -> None:
        job = _job()
        job["result"]["already_here"] = [{"key": "k", "name": "Old"}]

        self.assertEqual({}, report_groups.groups(job))


class HowManyThingsTablesWillList(unittest.TestCase):
    def test_a_game_with_two_game_files_counts_for_each_in_tables_but_once_as_a_game(self) -> None:
        job = _job(made=2)
        job["result"]["rows"].append({"name": "Twin", "game_id": "n0", "existing": False})

        self.assertEqual(3, report_groups.tables_made(job))
        self.assertEqual(2, len(report_groups.groups(job)[report_groups.IMPORTED].game_ids))

    def test_a_game_that_came_as_artwork_alone_is_counted_apart(self) -> None:
        job = _job(made=1)
        job["result"]["rows"] += [
            {"name": "Art", "game_id": "a1", "existing": False, "table": False},
            {"name": "Art again", "game_id": "a1", "existing": False, "table": False}]

        self.assertEqual(1, report_groups.artwork_alone(job))
        self.assertEqual(1, report_groups.tables_made(job))

    def test_artwork_for_a_game_that_also_has_a_file_is_not_alone(self) -> None:
        job = _job(made=1)
        job["result"]["rows"].append(
            {"name": "Art", "game_id": "n0", "existing": False, "table": False})

        self.assertEqual(0, report_groups.artwork_alone(job))

    def test_a_game_left_alone_or_failed_is_in_neither(self) -> None:
        job = _job(failed=1)
        job["result"]["rows"].append({"name": "Old", "game_id": "o1", "existing": True})

        self.assertEqual(0, report_groups.tables_made(job))
        self.assertEqual(0, report_groups.artwork_alone(job))


class TheAddresses(unittest.TestCase):
    def test_the_report_has_an_address_of_its_own(self) -> None:
        address = urlparse(report_groups.report_address(JOB))

        self.assertEqual("/manager/report", address.path)
        self.assertEqual({"job": [JOB]}, parse_qs(address.query))

    def test_tables_on_a_group_names_the_report_and_the_group(self) -> None:
        address = urlparse(report_groups.tables_address(JOB, report_groups.IMPORTED))

        self.assertEqual("/manager", address.path)
        self.assertEqual({"view": ["tables"], "report": [JOB], "group": ["imported"]},
                         parse_qs(address.query))

    def test_the_address_is_read_back_into_the_place_it_names(self) -> None:
        state: dict[str, Any] = {"view": "games"}
        params = {"view": "tables", "report": JOB, "group": "imported"}

        deeplink.apply(state, params, views=["games", "tables"], sections=[])

        self.assertEqual((JOB, "imported"), (state["report"], state["group"]))

    def test_a_report_with_no_group_is_not_a_place(self) -> None:
        state: dict[str, Any] = {"view": "games"}

        deeplink.apply(state, {"view": "tables", "report": JOB}, views=["tables"], sections=[])

        self.assertNotIn("report", state)

    def test_the_media_address_is_read_back_as_the_group_it_names(self) -> None:
        state: dict[str, Any] = {"view": "games"}
        params = dict(parse_qsl(urlparse(
            report_groups.media_address(report_groups.UNSUPPORTED)).query))

        deeplink.apply(state, params, views=["games", "media"], sections=[])

        self.assertEqual(("media", report_groups.UNSUPPORTED), (state["view"], state["group"]))
        self.assertNotIn("report", state)

    def test_a_group_on_a_view_that_takes_none_is_left_out(self) -> None:
        state: dict[str, Any] = {"view": "games"}

        deeplink.apply(state, {"view": "games", "group": "unsupported"}, views=["games"],
                       sections=[])

        self.assertNotIn("group", state)

    def test_another_view_leaves_the_report_out_of_its_address(self) -> None:
        state = {"view": "games", "report": JOB, "group": "imported"}

        self.assertEqual("view=games", deeplink.query(state))


def _links(job: dict[str, Any]) -> list[Any]:
    with ui.column() as body:
        wizard.report(body, job, "")
    return [one for one in body.descendants() if isinstance(one, ui.link)]


class TheLinkToTables(unittest.TestCase):
    def test_a_group_over_three_games_links_to_all_of_them_in_tables(self) -> None:
        links = _links(_job(made=4))

        self.assertEqual(1, len(links))
        self.assertEqual(report_groups.tables_address(JOB, report_groups.IMPORTED),
                         links[0].props["href"])

    def test_a_group_of_three_has_no_link(self) -> None:
        self.assertEqual([], _links(_job(made=3, held=3)))

    def test_each_group_over_three_has_its_own(self) -> None:
        links = _links(_job(made=5, held=4))

        self.assertEqual(
            [report_groups.tables_address(JOB, report_groups.IMPORTED),
             report_groups.tables_address(JOB, report_groups.ALREADY_HERE)],
            [one.props["href"] for one in links])

    def test_the_link_opens_in_the_managers_window(self) -> None:
        links = _links(_job(made=4))

        self.assertEqual(report_groups.MANAGER_WINDOW, links[0].props["target"])


class TheOwnTab(unittest.TestCase):
    def test_the_report_opens_in_a_new_tab_that_keeps_its_opener(self) -> None:
        with ui.column() as body:
            job_report.in_its_own_tab(JOB)
        link = next(one for one in body.descendants() if isinstance(one, ui.link))

        self.assertEqual(report_groups.report_address(JOB), link.props["href"])
        self.assertEqual("_blank", link.props["target"])
        self.assertEqual("opener", link.props["rel"])

    def test_a_link_out_has_no_opener_unless_it_asks(self) -> None:
        with ui.column() as body:
            panel.link_out("Elsewhere", to="https://example.org")()
        link = next(one for one in body.descendants() if isinstance(one, ui.link))

        self.assertNotIn("rel", link.props)


if __name__ == "__main__":
    unittest.main()
