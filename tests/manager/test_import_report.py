"""The import's report: which groups it draws and in what order, how many rows each names,
what it checks against the library when it is shown, and what the two retry links start."""

from __future__ import annotations

import asyncio
import unittest
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

from nicegui import ui

from manager.kit import offload, report_groups, wizard
from manager.library import import_report
from manager.settings import library_folders

JOB = "7f3a"


def _item(group: str, n: int, **extra: Any) -> dict[str, Any]:
    return {"group": group, "key": f"{group}{n}", "game_id": f"{group}-{n}",
            "name": f"{group} {n}", "reason": "", **extra}


def _job(*, items: list[dict[str, Any]] | None = None, **result: Any) -> dict[str, Any]:
    return {"id": JOB, "state": "done", "result": {
        "rows": [], "already_here": [], "left_behind": [], "items": items or [],
        "answers": {"source": "PinballY"}, **result}}


def _drawn(job: dict[str, Any]) -> list[str]:
    with ui.column() as body:
        wizard.report(body, job, "")
    return [one.text for one in body.descendants() if isinstance(one, ui.label)]


def _below_the_strip(job: dict[str, Any]) -> list[str]:
    """The words of the groups, without the figures above them that point at the groups."""
    with ui.column() as body:
        wizard.report(body, job, "")
    strip = [one for one in body.descendants() if "manager-report-strip" in one.classes]
    inside = {id(each) for one in strip for each in one.descendants()}
    return [one.text for one in body.descendants()
            if isinstance(one, ui.label) and id(one) not in inside]


class TheGroupsTheImporterRecords(unittest.TestCase):
    def test_each_recorded_group_is_a_group_of_its_games(self) -> None:
        found = report_groups.groups(_job(items=[
            _item(import_report.NEED_ROM, 1), _item(import_report.NEED_ROM, 2),
            _item(import_report.NO_WHEEL, 1)]))

        self.assertEqual(("need_rom-1", "need_rom-2"), found[import_report.NEED_ROM].game_ids)
        self.assertEqual(("no_wheel-1",), found[import_report.NO_WHEEL].game_ids)
        self.assertNotIn(import_report.NOT_MATCHED, found)

    def test_a_game_in_two_groups_is_in_each(self) -> None:
        both = {"game_id": "g1"}
        found = report_groups.groups(_job(items=[
            _item(import_report.NOT_MATCHED, 1, **both), _item(import_report.NO_WHEEL, 1, **both)]))

        self.assertEqual(("g1",), found[import_report.NOT_MATCHED].game_ids)
        self.assertEqual(("g1",), found[import_report.NO_WHEEL].game_ids)


class WhatTheReportDraws(unittest.TestCase):
    def test_the_headings_come_in_the_order_a_person_works_down(self) -> None:
        drawn = _below_the_strip(_job(
            items=[_item(import_report.NEED_ROM, 1), _item(import_report.DMD_ALONE, 1)],
            rows=[{"key": "bad", "name": "Bad", "game_id": "", "error": "no table"}],
            already_here=[{"key": "old", "name": "Old", "game_id": "o1"}],
            left_behind=[{"what": "ScreenRes.txt", "displays": True}]))

        order = ["Needs Review", "Not Imported", "Need a ROM", "Good to Know",
                 "Already in VPinFE, Left Alone", "DMD Positioned on Its Own", "Files Not Imported",
                 "ScreenRes.txt", "Imported"]
        self.assertEqual(order, [one for one in drawn if one in order])

    def test_a_group_over_three_rows_names_three_and_links_to_all_of_it(self) -> None:
        with ui.column() as body:
            wizard.report(body, _job(items=[_item(import_report.NOT_MATCHED, n)
                                            for n in range(5)]), "")
        words = [one.text for one in body.descendants() if isinstance(one, ui.label)]
        links = [one.text for one in body.descendants() if isinstance(one, ui.link)]

        self.assertEqual(3, len([one for one in words if one.startswith("not_matched ")]))
        self.assertIn("Show All 5 in Tables", links)

    def test_a_group_with_no_games_to_narrow_tables_to_counts_what_is_left(self) -> None:
        job = _job(items=[_item(import_report.NOT_MATCHED, n, game_id="") for n in range(5)])
        words = _drawn(job)

        self.assertIn("2 more", words)

    def test_the_rest_of_a_group_is_a_link_to_it_in_tables(self) -> None:
        job = _job(items=[_item(import_report.NO_WHEEL, n) for n in range(5)])
        with ui.column() as body:
            wizard.report(body, job, "")
        hrefs = [one.props["href"] for one in body.descendants() if isinstance(one, ui.link)]

        self.assertIn(report_groups.tables_address(JOB, import_report.NO_WHEEL), hrefs)

    def test_a_row_the_library_now_satisfies_keeps_a_tick_and_loses_its_link(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1), _item(import_report.NEED_ROM, 2)])
        job["checked"] = {import_report.NEED_ROM: {"need_rom-1"}}
        with ui.column() as body:
            wizard.report(body, job, "")
        words = [one.text for one in body.descendants() if isinstance(one, ui.label)]
        links = [one for one in body.descendants() if isinstance(one, ui.link)]

        self.assertIn("Added", words)
        self.assertEqual(1, len([one for one in links if one.text == "Add the ROM"]))

    def test_a_fixed_row_follows_the_ones_still_to_do(self) -> None:
        job = _job(items=[_item(import_report.NO_WHEEL, 1), _item(import_report.NO_WHEEL, 2)])
        job["checked"] = {import_report.NO_WHEEL: {"no_wheel-1"}}

        named = [one for one in _drawn(job) if one.startswith("no_wheel ")]

        self.assertEqual(["no_wheel 2", "no_wheel 1"], named)

    def test_a_cabinet_file_with_no_place_to_set_it_has_no_displays_link(self) -> None:
        with ui.column() as body:
            wizard.report(body, _job(left_behind=[
                {"what": "DirectOutput", "displays": False},
                {"what": "ScreenRes.txt", "displays": True}]), "")
        links = [one.text for one in body.descendants() if isinstance(one, ui.link)]

        self.assertEqual(["Displays"], links)

    def test_what_could_not_be_read_beside_the_library_names_why_under_left_behind(self) -> None:
        drawn = _below_the_strip(_job(left_behind=[
            {"what": "VPReg.stg could not be read", "detail": "Permission denied"}]))

        self.assertIn("VPReg.stg could not be read", drawn)
        self.assertIn("Permission denied", drawn)

    def test_each_link_opens_the_game_where_the_fix_is(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1), _item(import_report.NOT_MATCHED, 1),
                          _item(import_report.NO_WHEEL, 1)])
        with ui.column() as body:
            wizard.report(body, job, "")
        hrefs = {one.text: one.props["href"] for one in body.descendants()
                 if isinstance(one, ui.link)}

        self.assertEqual("/manager?view=assets&game=need_rom-1&slot=rom", hrefs["Add the ROM"])
        self.assertEqual("/manager?view=tables&game=not_matched-1&section=game_details",
                         hrefs["Match"])
        self.assertEqual("/manager?view=media&game=no_wheel-1&slot=wheel", hrefs["Add a Wheel"])

    def test_files_that_did_not_come_across_are_named_under_needs_a_look(self) -> None:
        job = _job(items=[_item(import_report.NOT_BROUGHT, 1, reason="taxi_l4.zip, Wheel Images")])
        with ui.column() as body:
            wizard.report(body, job, "")
        words = [one.text for one in body.descendants() if isinstance(one, ui.label)]
        hrefs = {one.text: one.props["href"] for one in body.descendants()
                 if isinstance(one, ui.link)}

        self.assertIn("Not Imported", words)
        self.assertIn("taxi_l4.zip, Wheel Images", words)
        self.assertEqual("/manager?view=tables&game=not_brought-1", hrefs["Open"])
        self.assertEqual(1, import_report.waiting(job))

    def test_the_figures_above_the_groups_count_what_is_still_to_do(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1), _item(import_report.NEED_ROM, 2)],
                   against=[{"key": "games", "label": "Games", "expected": 2, "actual": 2}])
        job["checked"] = {import_report.NEED_ROM: {"need_rom-1"}}
        with ui.column() as body:
            wizard.report(body, job, "")
        cells = [one for one in body.descendants() if "manager-report-cell" in one.classes]
        shown = {[each.text for each in one.descendants() if isinstance(each, ui.label)][1]:
                 [each.text for each in one.descendants() if isinstance(each, ui.label)][0]
                 for one in cells}

        self.assertEqual({"Need Review": "1", "Games Imported": "2"}, shown)

    def test_a_figure_for_a_group_that_is_not_drawn_is_not_there(self) -> None:
        with ui.column() as body:
            wizard.report(body, _job(), "")
        cells = [one for one in body.descendants() if "manager-report-cell" in one.classes]

        self.assertEqual(1, len(cells))

    def test_the_imported_link_counts_the_game_files_tables_will_list(self) -> None:
        rows = [{"game_id": f"g{n}", "table": True} for n in range(4)]
        rows += [{"game_id": "g0", "table": True}, {"game_id": "art", "table": False}]
        with ui.column() as body:
            wizard.report(body, _job(rows=rows), "")
        links = [one.text for one in body.descendants() if isinstance(one, ui.link)]

        self.assertIn("Show All 5 in Tables", links)

    def test_the_games_tile_says_how_many_came_as_artwork_alone(self) -> None:
        job = _job(rows=[{"game_id": "g1", "table": True}, {"game_id": "art", "table": False}],
                   against=[{"key": "games", "label": "Games", "expected": 2, "actual": 2}])

        self.assertIn("1 imported as media only", _drawn(job))

    def test_a_tile_that_came_up_short_says_by_how_much(self) -> None:
        job = _job(against=[{"key": "media", "label": "Media", "expected": 10, "actual": 7}])
        drawn = _drawn(job)

        self.assertIn("3 short", drawn)
        self.assertIn("of 10", drawn)

    def test_a_report_without_the_lists_is_drawn_the_way_it_was(self) -> None:
        job = {"id": JOB, "state": "done", "result": {"rows": [], "already_here": []}}

        self.assertFalse(import_report.has_report(job))


class TheLibraryIsReadAgain(unittest.TestCase):
    def _checked(self, job: dict[str, Any], **reads: Any) -> dict[str, set[str]]:
        async def inline(call: Any, *args: Any) -> Any:
            return call(*args)

        client = AsyncMock()
        client.all_tables = lambda: reads.get("tables", [])
        client.games = lambda: reads.get("games", [])
        client.all_media = lambda kind: reads.get("media", {}).get(kind, [])
        with patch.object(import_report, "ApiClient", return_value=client), \
                patch.object(offload, "io", new=inline):
            asyncio.run(import_report.check(job))
        return job["checked"]

    def test_a_rom_now_installed_fixes_that_game(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1), _item(import_report.NEED_ROM, 2)])

        fixed = self._checked(job, tables=[
            {"game_id": "need_rom-1", "rom_installed": True},
            {"game_id": "need_rom-2", "rom_installed": None}])

        self.assertEqual({"need_rom-1"}, fixed[import_report.NEED_ROM])

    def test_a_game_now_matched_to_vps_is_fixed(self) -> None:
        job = _job(items=[_item(import_report.NOT_MATCHED, 1)])

        fixed = self._checked(job, games=[{"id": "not_matched-1", "vps_id": "abc"}])

        self.assertEqual({"not_matched-1"}, fixed[import_report.NOT_MATCHED])

    def test_a_wheel_or_a_logo_fills_the_wheel(self) -> None:
        job = _job(items=[_item(import_report.NO_WHEEL, 1), _item(import_report.NO_WHEEL, 2)])

        fixed = self._checked(job, media={
            "wheel": [{"game_id": "no_wheel-1", "present": True}],
            "logo": [{"game_id": "no_wheel-2", "present": False}]})

        self.assertEqual({"no_wheel-1"}, fixed[import_report.NO_WHEEL])

    def test_a_read_that_fails_leaves_the_report_as_it_was_written(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1)])

        async def broken(call: Any, *args: Any) -> Any:
            raise OSError("down")

        with patch.object(offload, "io", new=broken):
            asyncio.run(import_report.check(job))

        self.assertEqual({}, job["checked"])

    def test_a_read_that_fails_later_keeps_the_ticks_already_earned(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1)])
        job["checked"] = {import_report.NEED_ROM: {"need_rom-1"}}

        async def broken(call: Any, *args: Any) -> Any:
            raise OSError("down")

        with patch.object(offload, "io", new=broken):
            asyncio.run(import_report.check(job))

        self.assertEqual({import_report.NEED_ROM: {"need_rom-1"}}, job["checked"])


class TheRetryLinks(unittest.TestCase):
    def _retry(self, keys: list[str], found: list[dict[str, Any]]) -> AsyncMock:
        async def inline(call: Any, *args: Any) -> Any:
            return found

        opened = AsyncMock()
        with patch.object(offload, "io", new=inline), \
                patch.object(library_folders, "open_importer", new=opened):
            asyncio.run(import_report.retry(_job(), keys))
        return opened

    def test_the_second_import_starts_from_the_first_ones_answers_with_these_ticked(self) -> None:
        action = {"key": "import", "label": "Import"}
        found = [{"name": library_folders.IMPORTER, "actions": [action]}]

        opened = self._retry(["a", "b"], found)

        opened.assert_awaited_once_with(
            action, None, values={"source": "PinballY", "only": ["a", "b"]}, until="games")

    def test_an_importer_that_is_not_running_opens_nothing(self) -> None:
        with patch.object(import_report.ui, "notify") as said:
            opened = self._retry(["a"], [])

        opened.assert_not_awaited()
        said.assert_called_once()


class TheLineOnLocations(unittest.TestCase):
    def setUp(self) -> None:
        import_report._drawn.clear()

    def test_what_is_waiting_is_the_rows_the_report_draws_under_needs_a_look(self) -> None:
        job = _job(items=[_item(import_report.NEED_ROM, 1), _item(import_report.NEED_ROM, 2),
                          _item(import_report.NO_WHEEL, 1), _item(import_report.DMD_ALONE, 1)],
                   rows=[{"key": "bad", "name": "Bad", "error": "no table"}])
        job["checked"] = {import_report.NEED_ROM: {"need_rom-1"}}

        self.assertEqual(3, import_report.waiting(job))

    def _latest(self, jobs: list[dict[str, Any]], kept: dict[str, Any]) -> Any:
        async def inline(call: Any, *args: Any) -> Any:
            return call(*args)

        library = Mock()
        library.jobs.return_value = jobs
        library.job.return_value = kept
        with patch.object(offload, "io", new=inline), \
                patch.object(import_report, "check", new=AsyncMock()):
            return asyncio.run(import_report.latest(library)), library

    def _kept(self, **result: Any) -> dict[str, Any]:
        return {**_job(**result), "finished_at": 1760000000.0}

    def test_an_import_with_rows_left_says_how_many(self) -> None:
        kept = self._kept(items=[_item(import_report.NOT_MATCHED, n) for n in range(12)],
                          program="PinballY")
        line, library = self._latest(
            [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}],
            kept)

        self.assertEqual(("PinballY, 12 to review", report_groups.report_address(JOB)), line)
        library.job.assert_called_once_with(JOB)

    def test_an_import_with_nothing_left_says_when_it_was(self) -> None:
        line, _ = self._latest(
            [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}],
            self._kept(program="PinballY"))

        self.assertRegex(line[0], r"^PinballY, imported \S+")
        self.assertEqual(report_groups.report_address(JOB), line[1])

    def test_the_newest_import_that_kept_a_report_is_the_one(self) -> None:
        _, library = self._latest([
            {"id": "new", "kind": import_report.IMPORT_KIND, "report": True, "started_at": 9},
            {"id": "old", "kind": import_report.IMPORT_KIND, "report": True, "started_at": 1},
            {"id": "mid", "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5},
            {"id": "newer", "kind": import_report.IMPORT_KIND, "report": False, "started_at": 12},
            {"id": "scan", "kind": "library.scan", "report": True, "started_at": 20}],
            self._kept())

        library.job.assert_called_once_with("new")

    def test_one_row_left_is_counted_as_one(self) -> None:
        line, _ = self._latest(
            [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}],
            self._kept(items=[_item(import_report.NOT_MATCHED, 1)], program="PinballY"))

        self.assertEqual("PinballY, 1 to review", line[0])

    def test_a_result_that_cannot_be_read_is_no_line(self) -> None:
        line, _ = self._latest(
            [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}],
            self._kept(items=["not a row"]))

        self.assertIsNone(line)

    def test_a_second_draw_soon_after_does_not_read_the_library_again(self) -> None:
        jobs = [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}]
        kept = self._kept(program="PinballY")
        first, library = self._latest(jobs, kept)

        library.job.reset_mock()
        with patch.object(import_report, "check", new=AsyncMock()) as checked:
            asyncio.run(import_report.latest(library))

        self.assertIsNotNone(first)
        library.job.assert_not_called()
        checked.assert_not_called()

    def test_no_import_kept_is_no_line(self) -> None:
        line, library = self._latest(
            [{"id": "scan", "kind": "library.scan", "report": True, "started_at": 20}], {})

        self.assertIsNone(line)
        library.job.assert_not_called()

    def test_an_older_report_without_the_program_is_named_for_the_import(self) -> None:
        line, _ = self._latest(
            [{"id": JOB, "kind": import_report.IMPORT_KIND, "report": True, "started_at": 5}],
            self._kept())

        self.assertTrue(line[0].startswith("Import Tables and Settings, imported "))

    def test_jobs_that_cannot_be_read_are_no_line(self) -> None:
        async def broken(call: Any, *args: Any) -> Any:
            raise OSError("down")

        with patch.object(offload, "io", new=broken):
            self.assertIsNone(asyncio.run(import_report.latest(Mock())))


if __name__ == "__main__":
    unittest.main()
