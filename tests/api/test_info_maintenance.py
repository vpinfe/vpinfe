"""The library's own metadata files, over the wire.

Two operations that rewrite a file in every game folder, so the things worth pinning are
that they take the scan's job kind, that they refuse rather than overlap, and that the
caller is handed something to watch.
"""

from __future__ import annotations

import asyncio
import json
import time
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from unittest import mock

from nicegui import core, ui

import httpapi
from common.games.game_parser import GameParser
from common.games.info_maintenance import upgrade_library
from common.games.unreadable import UnreadableGame
from common.i18n import t
from common.runtime import jobs as job_registry
from manager.kit.data import Library
from manager.settings import sections
from tests.support import lines
from tests.support.library import game_info, write_game
from tests.support.skips import needs_posix_permissions

try:
    from starlette.testclient import TestClient
except ImportError:  # pragma: no cover
    TestClient = None  # type: ignore[assignment,misc]


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
class InfoRouteTests(unittest.TestCase):
    def setUp(self) -> None:
        job_registry.reset_for_tests()
        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        self.addCleanup(job_registry.reset_for_tests)

    def test_it_reports_the_three_states_apart(self) -> None:
        """`newer_than_us` is the one that has to stay separate: a file written by a
        later build is not one this build can upgrade, and counting it with the pending
        ones would report "I upgraded these" about files it cannot fully read."""
        with mock.patch("common.games.game_repository.info_maintenance_counts",
                        return_value={"pending_upgrade": 3, "restorable": 9,
                                      "newer_than_us": 1}), \
                mock.patch("common.games.game_repository.unreadable_games",
                           return_value=[{"folder": "Bad One", "path": "/games/Bad One",
                                          "error": "Its .info file is empty"}]), \
                mock.patch("common.games.game_service.newest_backup_stamp",
                           return_value="20260909T110917Z"), \
                mock.patch("common.games.game_service.pending_upgrade_game_names",
                           return_value=["A", "B", "C"]), \
                mock.patch("common.games.game_service.restorable_game_names",
                           return_value=["A"]):
            body = self.client.get("/library/info").json()

        self.assertEqual(body["pending_upgrade"], 3)
        self.assertEqual(body["newer_than_us"], 1)
        self.assertEqual(body["restorable"], 9)
        self.assertEqual([one["folder"] for one in body["unreadable"]], ["Bad One"])

    def _start(self, which: str, done: list):
        return mock.patch(f"common.games.game_service.{which}",
                          side_effect=lambda **kw: done.append(kw))

    def test_an_upgrade_hands_back_a_job_to_watch(self) -> None:
        done: list = []
        with self._start("upgrade_info", done):
            answer = self.client.post("/library/info/upgrade")

        self.assertEqual(answer.status_code, 202)
        self.assertTrue(answer.headers["Location"].startswith("/api/v1/jobs/"))
        self.assertEqual(answer.json()["kind"], job_registry.KIND_LIBRARY_SCAN)

    def test_the_route_owns_the_job_rather_than_the_service(self) -> None:
        """Otherwise the service registers a second one of the same kind and refuses
        itself as busy - which is a 500 for work that was about to run fine."""
        done: list = []
        with self._start("upgrade_info", done):
            self.client.post("/library/info/upgrade")

        self.assertEqual(len(done), 1)
        self.assertIn("job", done[0])
        self.assertIsNotNone(done[0]["job"])

    def test_one_at_a_time(self) -> None:
        """A second pass while one is running is refused rather than queued: the two
        would be rewriting the same files."""
        job_registry.submit(job_registry.KIND_LIBRARY_SCAN,
                            lambda job: _never_finishes())

        answer = self.client.post("/library/info/upgrade")

        self.assertEqual(answer.status_code, 409)


class FolderListTests(unittest.TestCase):
    """What the folder list a Needs You row opens says."""

    def _unreadable(self, *names: str) -> list[UnreadableGame]:
        """The rows the scan writes for folders whose `.info` is not valid metadata."""
        with TemporaryDirectory() as tmp:
            for name in names:
                (write_game(tmp, name) / f"{name}.info").write_text(
                    '{"Info": {"Title": "broken",,,}', encoding="utf-8")
            return GameParser(tmp).get_unreadable_games()

    def test_a_folder_it_could_not_read_is_named_with_why_on_screen(self) -> None:
        metadata = {"unreadable": self._unreadable("Malformed Info (Original 2024)")}

        drawn = _folders(metadata, "unreadable")

        self.assertEqual({"Malformed Info (Original 2024)":
                          t("error.games.info_bad_line", line=1)}, drawn)
        self.assertIn(t("manager.sections.unreadable_one"), _said(metadata, "unreadable"))

    def test_two_hundred_are_named_and_the_rest_counted(self) -> None:
        names = [f"Broken {i:03d} (Original 2024)" for i in range(203)]
        metadata = {"unreadable": [{"folder": name, "error": "unreadable"} for name in names]}

        drawn = _folders(metadata, "unreadable")

        self.assertEqual([True] * 200 + [False] * 3, [name in drawn for name in names])
        self.assertIn(t("said.and_more", count=3), drawn)

    def test_one_game_written_by_an_older_build_reads_as_one(self) -> None:
        one, two = (t("manager.sections.written_older", count=n) for n in (1, 2))

        self.assertIn(one, _said({"pending_games": ["A (Original 2024)"]}, "older_format"))
        self.assertIn(two, _said({"pending_games": ["A (Original 2024)", "B (Original 2024)"]},
                                 "older_format"))
        self.assertNotEqual(one, two.replace("2", "1"))

    def test_only_the_older_format_list_offers_the_upgrade(self) -> None:
        metadata = {"unreadable": [{"folder": "A", "error": "x"}],
                    "unwritten": [{"folder": "B", "error": "x"}],
                    "pending_games": ["C"]}

        self.assertEqual(
            {"unreadable": ["Close"], "unwritten": ["Close"],
             "older_format": ["Close", "Upgrade"]},
            {which: _buttons(metadata, which)
             for which in ("unreadable", "unwritten", "older_format")})

    def test_the_upgrade_button_closes_the_list_and_runs_the_upgrade(self) -> None:
        library = mock.Mock()
        library.metadata_state.return_value = {"pending_games": ["Older (Original 2024)"]}
        api = mock.Mock()
        api.upgrade_info.return_value = {"id": "u-1"}
        api.job.return_value = {"state": "done", "result": {"upgraded": 1}}
        ask = mock.AsyncMock(return_value=True)
        with ui.column() as page:
            pass
        layout = page.client.layout
        before = {one for one in layout.descendants() if isinstance(one, ui.dialog)}

        async def press() -> tuple[bool, bool]:
            was = core.loop
            core.loop = asyncio.get_running_loop()
            try:
                with page:
                    await sections.metadata_folders(library, {}, "older_format", mock.Mock())
                [box] = [one for one in layout.descendants()
                         if isinstance(one, ui.dialog) and one not in before]
                self.addCleanup(box.delete)
                [upgrade] = [one for one in box.descendants()
                             if isinstance(one, ui.button) and one.text == t("word.upgrade")]
                opened = box.value
                for listener in upgrade._event_listeners.values():
                    assert listener.handler is not None
                    listener.handler(None)
                for _ in range(50):
                    await asyncio.sleep(0)
                return opened, box.value
            finally:
                core.loop = was

        with mock.patch("manager.kit.confirm.ask", ask), \
                mock.patch("manager.kit.api.ApiClient", return_value=api), \
                mock.patch("nicegui.run.io_bound", new=_now), \
                mock.patch.object(sections, "_POLL_S", 0), \
                mock.patch.object(ui, "notify"):
            opened, still_open = asyncio.run(press())

        self.assertEqual((True, False), (opened, still_open))
        [asked] = ask.await_args_list
        self.assertEqual(t("manager.sections.upgrade_confirm"), asked.args[0])
        api.upgrade_info.assert_called_once_with()
        api.job.assert_called_once_with("u-1")
        for which in ("unreadable", "unwritten"):
            self.assertNotIn(t("word.upgrade"), _buttons(
                {"unreadable": [{"folder": "A", "error": "x"}],
                 "unwritten": [{"folder": "B", "error": "x"}]}, which))

    def test_a_list_that_has_emptied_opens_nothing_and_redraws_the_page(self) -> None:
        redraw = mock.Mock()

        drawn = _read({"pending_games": []}, "older_format", redraw=redraw)

        self.assertEqual(({}, [], []), drawn)
        redraw.assert_called_once_with()

    def test_a_folder_fixed_and_rescanned_is_no_longer_named(self) -> None:
        name = "Malformed Info (Original 2024)"
        with TemporaryDirectory() as tmp:
            info = write_game(tmp, name) / f"{name}.info"
            info.write_text('{"Info": {"Title": "broken",,,}', encoding="utf-8")
            client = mock.Mock()
            client.info_maintenance.side_effect = lambda: {
                "unreadable": GameParser(tmp).get_unreadable_games()}
            library = Library(client)
            library.read_metadata_state()
            named = name in _folders(library.metadata_state(), "unreadable")
            info.write_text(json.dumps(game_info(name)), encoding="utf-8")

            library.refresh_after_import()

        self.assertEqual((True, False), (
            named, name in _folders(library.metadata_state(), "unreadable")))


LEGACY = {"Info": {"Title": "Sample Game", "Rom": "sample"},
          "VPXFile": {"filename": "Sample Game.vpx", "filehash": "abc", "rom": "sample"}}
KEPT = "Sample Game (Original 2024)"
LOCKED = "Locked Game (Original 2024)"


def _read(metadata: dict, which: str, left: dict | None = None, redraw: Any = None
          ) -> tuple[dict[str, str], list[str], list[str]]:
    """The folder list Needs You opens for `which` over `metadata`: each folder it names
    with the line under it, every label it draws and every button. All are empty where it
    opens nothing, and `redraw` is called instead."""
    library = mock.Mock()
    library.metadata_state.return_value = metadata
    state: dict[str, Any] = {} if left is None else {sections._LEFT: left}
    with ui.column() as page:
        pass
    layout = page.client.layout
    before = {one for one in layout.descendants() if isinstance(one, ui.dialog)}

    async def open_it() -> None:
        with page:
            await sections.metadata_folders(library, state, which, redraw or mock.Mock())

    asyncio.run(open_it())
    drawn: tuple[dict[str, str], list[str], list[str]] = ({}, [], [])
    for box in [one for one in layout.descendants()
                if isinstance(one, ui.dialog) and one not in before]:
        drawn = (lines.folders(box),
                 [one.text for one in box.descendants() if isinstance(one, ui.label)],
                 [one.text for one in box.descendants() if isinstance(one, ui.button)])
        box.delete()
    return drawn


def _folders(metadata: dict, which: str, left: dict | None = None) -> dict[str, str]:
    return _read(metadata, which, left)[0]


def _said(metadata: dict, which: str) -> list[str]:
    return _read(metadata, which)[1]


def _buttons(metadata: dict, which: str) -> list[str]:
    return _read(metadata, which)[2]


async def _now(callback, *args, **kwargs):
    return callback(*args, **kwargs)


@unittest.skipIf(TestClient is None, "starlette test client unavailable")
@needs_posix_permissions
class MetadataOutcomeTests(unittest.TestCase):
    """What Upgrade and Restore say once their job ends, from the job run over a library
    with one folder it may not write, read back as the resource the Manager is handed."""

    def setUp(self) -> None:
        job_registry.reset_for_tests()
        self.addCleanup(job_registry.reset_for_tests)
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for name in (KEPT, LOCKED):
            write_game(self.root, name, info=LEGACY)
        self.addCleanup(self._lock, self.root / LOCKED, False)
        self.addCleanup(self._lock, self.root, False)

    def _lock(self, folder: Path, locked: bool = True) -> None:
        folder.chmod(0o555 if locked else 0o755)

    def _job(self, work) -> dict:
        job = job_registry.submit(job_registry.KIND_LIBRARY_SCAN, lambda _job: work(self.root))
        for _ in range(500):
            if job.state != job_registry.RUNNING:
                break
            time.sleep(0.01)
        api = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)
        return api.get(f"/jobs/{job.id}").json()

    def _metadata(self) -> dict:
        parser = GameParser(self.root)
        games = parser.get_all_games()
        pending = [one.game_dir_name for one in games if one.info_pending_upgrade]
        return {"pending_upgrade": len(pending), "pending_games": pending,
                "restorable": sum(1 for one in games if one.info_restorable),
                "unreadable": parser.get_unreadable_games()}

    def _press(self, which: str, work, *, leave: bool = False
               ) -> tuple[list[tuple[str, str, str]], dict[str, str]]:
        """Press Upgrade over what `work` did, from inside a row of the page;
        `leave` deletes that row while the job runs. Hand back each thing the Manager
        said, as lead, caption and type, and the older-format list as it is drawn
        afterwards."""
        job = self._job(work)
        with ui.column() as card:
            row = ui.row().default_slot

        def asked(_job_id: str) -> dict:
            if leave:
                card.clear()
            return job

        client = mock.Mock()
        client.upgrade_info.return_value = {"id": which}
        client.job.side_effect = asked
        client.info_maintenance.side_effect = self._metadata
        library = Library(client)
        page: dict[str, Any] = {"view": "overview"}

        async def pressed() -> None:
            with row:
                await sections._metadata_action(library, page, mock.Mock())(which)

        with mock.patch("manager.kit.confirm.ask", mock.AsyncMock(return_value=True)), \
                mock.patch("manager.kit.api.ApiClient", return_value=client), \
                mock.patch("nicegui.run.io_bound", new=_now), \
                mock.patch.object(sections, "_POLL_S", 0), \
                mock.patch.object(sections.ui, "notify", wraps=ui.notify) as notify:
            asyncio.run(pressed())
        said = [(one.args[0], one.kwargs.get("caption", ""), one.kwargs.get("type", ""))
                for one in notify.call_args_list]
        return said, _folders(library.metadata_state(), "older_format", page.get(sections._LEFT))

    def _upgrade(self):
        self._lock(self.root / LOCKED)
        return self._press("upgrade", upgrade_library)

    def test_it_says_how_many_came_through(self) -> None:
        said, _ = self._upgrade()

        self.assertIn((t("manager.sections.upgraded_some", done=1, total=2), "", "warning"),
                      said)

    def test_the_folder_it_could_not_write_is_named_with_why_on_screen(self) -> None:
        _, drawn = self._upgrade()

        self.assertEqual(drawn.get(LOCKED), t("error.path.read_only", name=LOCKED))

    def test_a_folder_it_may_not_write_is_not_tried_or_counted_as_matched(self) -> None:
        self._lock(self.root / LOCKED)
        routed: list[str] = []

        def route(record: dict) -> str:
            routed.append(record["Info"]["Title"])
            return "bound"

        found = upgrade_library(self.root, route_match=route)

        self.assertEqual(found["matches_bound"], len(routed))
        self.assertEqual(len(routed), 1)
        self.assertEqual(found["failures"], [(LOCKED, t("error.path.read_only", name=LOCKED))])

    def test_a_job_that_fails_leads_with_words_and_its_reason(self) -> None:
        self.root.chmod(0o000)

        said, _ = self._press("upgrade", upgrade_library)

        lead, caption, kind = said[-1]
        self.assertEqual((lead, kind), (t("manager.sections.upgrade_failed"), "negative"))
        self.assertTrue(caption.startswith(t("said.why.no_permission")), caption)

    def test_how_it_went_is_said_after_the_page_is_left(self) -> None:
        said, _ = self._press("upgrade", upgrade_library, leave=True)

        self.assertIn((t("manager.sections.upgraded", count=2), "", "positive"), said)


def _never_finishes() -> None:
    import threading

    threading.Event().wait(30)
