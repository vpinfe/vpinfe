"""Setup's Tables step: which answers are complete and which end in Finish, what each
survey says, what the step shows once there is a library, and how the importer's steps
are asked inside it."""

from __future__ import annotations

import asyncio
import pathlib
import tempfile
import unittest
from typing import Any
from unittest import mock

from common.config.path_state import OK as PATH_OK
from common.games import library_folders as model
from manager.setup import setup, setup_tables
from manager.setup.setup_tables import Tables

IMPORT = {"key": "import", "label": "Import a Library", "base": "/wizard"}
FOLDERS = {"game_folders": ["a", "b"], "loose": []}
LOOSE = {"game_folders": [], "loose": ["x.vpx"]}
MIXED = {"game_folders": ["a"], "loose": ["x.vpx", "y.vpx"]}
NOTHING: dict[str, list[str]] = {"game_folders": [], "loose": []}


def _ran(coro: Any) -> Any:
    return asyncio.run(coro)


def _in_folder(found: dict[str, Any] | None, *, state: str = PATH_OK,
               path: str = "/lib") -> dict[str, Any]:
    values: dict[str, Any] = {setup_tables.WHERE: setup_tables.IN_A_FOLDER,
                              setup_tables.FOLDER: path,
                              setup_tables.FOLDER_CHECKED: (path, state)}
    if found is not None:
        values[setup_tables.SURVEYED] = (path, found)
    return values


class AnswersTests(unittest.TestCase):
    def test_a_folder_is_complete_once_checked_and_surveyed_with_something_in_it(self) -> None:
        for found, expected in ((FOLDERS, True), (LOOSE, True), (MIXED, True),
                                (NOTHING, False), (None, False)):
            with self.subTest(found=found):
                self.assertEqual(expected, setup_tables.complete(_in_folder(found)))

    def test_a_folder_that_did_not_check_is_not_complete(self) -> None:
        self.assertFalse(setup_tables.complete(_in_folder(FOLDERS, state="missing")))

    def test_a_survey_of_another_path_is_not_the_folders(self) -> None:
        values = _in_folder(FOLDERS)
        values[setup_tables.FOLDER] = "/elsewhere"

        self.assertFalse(setup_tables.complete(values))
        self.assertFalse(setup_tables.ends_here(values))

    def test_only_a_folder_without_loose_tables_ends_in_finish(self) -> None:
        for found, expected in ((FOLDERS, True), (LOOSE, False), (MIXED, False),
                                (None, False)):
            with self.subTest(found=found):
                self.assertEqual(expected, setup_tables.ends_here(_in_folder(found)))

    def test_none_yet_is_complete_with_a_checked_folder_and_ends_there(self) -> None:
        values: dict[str, Any] = {setup_tables.WHERE: setup_tables.NONE_YET,
                                  setup_tables.NEW_FOLDER: "/new"}

        self.assertFalse(setup_tables.complete(values))
        values[setup_tables.NEW_CHECKED] = ("/new", PATH_OK)
        self.assertTrue(setup_tables.complete(values))
        self.assertTrue(setup_tables.ends_here(values))

    def test_loose_tables_are_not_complete_where_the_importer_is_not_running(self) -> None:
        for found, expected in ((FOLDERS, True), (LOOSE, False), (MIXED, True)):
            with self.subTest(found=found):
                self.assertEqual(
                    expected, setup_tables.complete(_in_folder(found), importer=False))

    def test_another_frontend_is_always_complete_and_never_ends_there(self) -> None:
        values = {setup_tables.WHERE: setup_tables.FROM_A_FRONTEND}

        self.assertTrue(setup_tables.complete(values))
        self.assertFalse(setup_tables.ends_here(values))


class FoundLineTests(unittest.TestCase):
    def test_what_was_found_and_what_happens_to_it(self) -> None:
        for found, icon, helped in ((NOTHING, "error", False), (FOLDERS, "check_circle", True),
                                    (LOOSE, "info", True), (MIXED, "info", True)):
            with self.subTest(found=found):
                shown, line, help_text = setup_tables._found_line(found)
                self.assertEqual(icon, shown)
                self.assertTrue(line)
                self.assertEqual(helped, bool(help_text))

    def test_counts_read_as_counts(self) -> None:
        self.assertIn("2", setup_tables._found_line(FOLDERS)[1])
        mixed = setup_tables._found_line(MIXED)
        self.assertIn("1", mixed[1])
        self.assertIn("2", mixed[1])
        self.assertIn("2", mixed[2])


class TablesBesideTests(unittest.TestCase):
    def test_the_tables_folder_next_to_the_program_whatever_its_case(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            (pathlib.Path(root) / "TABLES").mkdir()
            program = str(pathlib.Path(root) / "VPinballX")

            self.assertEqual(str(pathlib.Path(root) / "TABLES"),
                             setup_tables.tables_beside(program))

    def test_none_beside_the_program_or_no_program_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual("", setup_tables.tables_beside(str(pathlib.Path(root) / "vpx")))
        self.assertEqual("", setup_tables.tables_beside(""))


class AskTests(unittest.TestCase):
    def _ask(self, found: Tables, *, again: bool = False) -> dict[str, Any]:
        with mock.patch.object(setup_tables, "ApiClient", return_value=mock.Mock()):
            return _ran(setup_tables.ask(found, again=again))

    def test_three_answers_where_the_importer_runs_and_two_where_it_does_not(self) -> None:
        for action, keys in ((IMPORT, ["folder", "frontend", "none"]),
                             (None, ["folder", "none"])):
            with self.subTest(importer=action is not None):
                step = self._ask(Tables(action=action))

                (field,) = step["fields"]
                self.assertEqual(keys, [choice[0] for choice in field["choices"]])
                self.assertEqual(action is not None, step["gate"](_in_folder(LOOSE)))
                self.assertIs(setup_tables.ends_here, step["ends"])

    def test_run_again_with_a_library_shows_it_and_offers_to_import_one(self) -> None:
        found = Tables(action=IMPORT, games=12, locations=[{"name": "", "path": "/lib"}])

        step = self._ask(found, again=True)

        self.assertNotIn("fields", step)
        self.assertEqual(["import"], [one["key"] for one in step["acts"]])
        self.assertIn("12", step["help"])

    def test_run_again_without_the_importer_only_shows_what_is_there(self) -> None:
        step = self._ask(Tables(locations=[{"name": "", "path": "/lib"}]), again=True)

        self.assertNotIn("acts", step)

    def test_run_again_with_no_library_asks_the_three_answers_again(self) -> None:
        self.assertIn("fields", self._ask(Tables(action=IMPORT), again=True))

    def test_a_first_run_over_a_library_with_games_shows_it_and_one_without_asks(self) -> None:
        there = [{"name": "", "path": "/lib"}]

        self.assertNotIn("fields", self._ask(Tables(action=IMPORT, locations=there, games=4)))
        self.assertIn("fields", self._ask(Tables(action=IMPORT, locations=there, games=0)))


class AnswerTests(unittest.TestCase):
    def _answered(self, values: dict[str, Any], found: Tables | None = None, *,
                  again: bool = False) -> mock.Mock:
        client = mock.Mock()
        with mock.patch.object(setup_tables, "ApiClient", return_value=client):
            _ran(setup_tables.answer(found or Tables(), values, again=again))
        return client

    def test_a_folder_of_game_folders_is_the_library_folder_itself(self) -> None:
        values = _in_folder(FOLDERS)
        values[setup_tables.FOLDER] = " /lib "
        client = self._answered(values)

        self.assertEqual({"path": "/lib", "kind": model.KIND_ROOT},
                         client.put_location.call_args.args[1])

    def test_none_yet_makes_the_folder_picked(self) -> None:
        client = self._answered({setup_tables.WHERE: setup_tables.NONE_YET,
                                 setup_tables.NEW_FOLDER: "/new"})

        self.assertEqual({"path": "/new", "kind": model.KIND_ROOT},
                         client.put_location.call_args.args[1])

    def test_run_again_with_no_library_makes_the_folder_too(self) -> None:
        client = self._answered(_in_folder(FOLDERS), Tables(), again=True)

        self.assertTrue(client.put_location.called)

    def test_where_the_library_is_shown_nothing_is_made(self) -> None:
        found = Tables(locations=[{"name": "", "path": "/lib"}], games=3)

        self.assertFalse(self._answered({}, found).put_location.called)

    def test_finish_on_an_answer_that_is_the_importers_is_a_defect(self) -> None:
        for values in (_in_folder(LOOSE), _in_folder(MIXED),
                       {setup_tables.WHERE: setup_tables.FROM_A_FRONTEND}):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                self._answered(values)


class InsideTests(unittest.TestCase):
    """The importer's steps, asked as `tables.<step>` over its own calls."""

    def _calls(self, **answers: Any) -> mock.Mock:
        calls = mock.Mock()
        for name, value in answers.items():
            setattr(calls, name, mock.AsyncMock(return_value=value))
        return calls

    def test_keys_belong_to_the_importer_only_under_the_prefix(self) -> None:
        self.assertTrue(setup_tables.inside("tables.sources"))
        self.assertFalse(setup_tables.inside("tables"))
        self.assertFalse(setup_tables.inside(None))

    def test_a_folder_of_loose_tables_goes_to_the_importer_as_folders(self) -> None:
        calls = self._calls(check={"step": "how"})
        values = _in_folder(LOOSE)
        values[setup_tables.FOLDER] = " /lib "

        with mock.patch.object(setup_tables.ext_action, "calls", return_value=calls):
            step = _ran(setup_tables.lead_on(Tables(action=IMPORT, games=0), values))

        self.assertEqual("tables.how", step["step"])
        sent, key = calls.check.call_args.args
        self.assertEqual("sources", key)
        self.assertEqual(("/lib", "folders"), (sent["path"], sent["source_type"]))

    def test_another_frontend_opens_the_importer_at_its_first_step(self) -> None:
        calls = self._calls(first={"step": "source"})

        with mock.patch.object(setup_tables.ext_action, "calls", return_value=calls):
            step = _ran(setup_tables.lead_on(
                Tables(action=IMPORT), {setup_tables.WHERE: setup_tables.FROM_A_FRONTEND}))

        self.assertEqual("tables.source", step["step"])
        calls.first.assert_awaited_once_with(None)

    def test_the_importer_is_not_asked_about_existing_games_where_there_are_none(self) -> None:
        for games, skip in ((0, True), (5, False)):
            with self.subTest(games=games):
                self.assertEqual(
                    skip, setup_tables._put_in(Tables(games=games), {})["skip_existing"])

    def test_checking_asks_the_importer_by_its_own_name_for_the_step(self) -> None:
        calls = self._calls(check={"step": "summary"})

        with mock.patch.object(setup_tables.ext_action, "calls", return_value=calls):
            step = _ran(setup_tables.check(Tables(action=IMPORT), {}, "tables.how"))

        self.assertEqual("tables.summary", step["step"])
        self.assertEqual("how", calls.check.call_args.args[1])

    def test_a_run_that_starts_a_job_is_passed_through_and_a_longer_path_is_named(self) -> None:
        job = {"job_id": "j1", "started": True}
        more = {"step": "how"}

        with mock.patch.object(setup_tables.ext_action, "calls",
                               return_value=self._calls(run=job)):
            self.assertEqual(
                job, _ran(setup_tables.run(Tables(action=IMPORT), {}, "tables.summary")))
        with mock.patch.object(setup_tables.ext_action, "calls",
                               return_value=self._calls(run=more)):
            longer = _ran(setup_tables.run(Tables(action=IMPORT), {}, "tables.summary"))
            self.assertEqual("tables.how", longer["step"])


class SetupCallsTests(unittest.TestCase):
    """Setup's calls hand the nested keys on and finish when the import does."""

    def setUp(self) -> None:
        self.session = setup.Session(again=False, tables=Tables(action=IMPORT))
        self.finished: list[str | None] = []

        async def done(session: setup.Session, answered: str | None = None) -> None:
            self.finished.append(answered)

        patch = mock.patch.object(setup, "_done", done)
        patch.start()
        self.addCleanup(patch.stop)

    def test_a_nested_key_is_checked_by_the_importer_inside_the_tables_segment(self) -> None:
        async def check(found: Tables, values: dict[str, Any], key: str) -> dict[str, Any]:
            return {"step": "tables.summary"}

        with mock.patch.object(setup_tables, "check", check):
            step = _ran(setup.calls(self.session).check({}, "tables.how"))

        row = {entry["key"]: entry for entry in step["steps"]}
        self.assertIn("tables.summary", row[setup.TABLES]["holds"])

    def test_a_job_finishes_setup_when_it_is_done_and_not_before(self) -> None:
        states = iter(({"state": "running"}, {"state": "done"}))

        async def job(found: Tables, job_id: str) -> dict[str, Any]:
            return next(states)

        with mock.patch.object(setup_tables, "job", job):
            calls = setup.calls(self.session)
            _ran(calls.job("j1"))
            self.assertEqual([], self.finished)
            _ran(calls.job("j1"))

        self.assertEqual([setup.TABLES], self.finished)

    def test_a_run_that_starts_a_job_finishes_setup_as_it_starts(self) -> None:
        async def run(found: Tables, values: dict[str, Any], key: str) -> dict[str, Any]:
            return {"job_id": "j1", "started": True}

        with mock.patch.object(setup_tables, "run", run):
            answer = _ran(setup.calls(self.session).run({}, "tables.summary"))

        self.assertEqual({"job_id": "j1", "started": True}, answer)
        self.assertEqual([setup.TABLES], self.finished)

    def test_import_opens_the_importer_from_the_run_again_view(self) -> None:
        async def open_importer(found: Tables) -> dict[str, Any]:
            return {"step": "tables.source"}

        with mock.patch.object(setup_tables, "open_importer", open_importer):
            step = _ran(setup.calls(self.session).act(setup_tables.IMPORT, {}, setup.TABLES))

        self.assertEqual("tables.source", step["step"])


class BrowseStartTests(unittest.TestCase):
    """Both folder fields leave the picker's start to the listing, which answers home."""

    def _opens_at(self, draw_for: Any) -> list[Any]:
        fields = mock.Mock()
        with mock.patch.object(setup_tables, "ui"), \
                mock.patch.object(setup_tables, "panel", fields), \
                mock.patch("pathlib.Path.home", return_value=pathlib.Path("/no/such/home")):
            draw_for({}, lambda: None)
        return [call.kwargs.get("opens_at", "") for call in fields.path_field.call_args_list]

    def test_the_folder_field_opens_at_the_folder_beside_the_program_or_the_listings_start(
            self) -> None:
        self.assertEqual([""], self._opens_at(setup_tables._folder_detail(mock.Mock(), "")))
        self.assertEqual(["/vpx/Tables"],
                         self._opens_at(setup_tables._folder_detail(mock.Mock(), "/vpx/Tables")))

    def test_the_new_folder_field_opens_at_the_listings_start(self) -> None:
        self.assertEqual([""], self._opens_at(setup_tables._new_detail(mock.Mock())))


class DoneTests(unittest.TestCase):
    def test_setup_finishes_once_and_not_when_run_again(self) -> None:
        for again, calls in ((False, 1), (True, 0)):
            with self.subTest(again=again), \
                 mock.patch.object(setup.install_setup, "finish") as finish, \
                 mock.patch.object(setup.install_setup, "answer"):
                session = setup.Session(again=again)
                _ran(setup._done(session, setup.TABLES))
                _ran(setup._done(session, setup.TABLES))

                self.assertEqual(calls, finish.call_count)


if __name__ == "__main__":
    unittest.main()
