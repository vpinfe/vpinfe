"""A game in a folder VPinFE cannot write, through two refreshes.

Real read-only folders, one a 2.x `.info` and one a schema 2 with no id, each holding a
second `.vpx` its `.info` does not describe, beside a writable 2.x game.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

import httpapi
from common.games import (
    auto_match,
    game_identity,
    game_repository,
    game_service,
    library_refresh,
    unreadable,
    unwritten,
)
from common.games.formats.vpx_parser import VPXParser
from common.games.info_maintenance import upgrade_library
from common.games.library_folders import KIND_ROOT, Location
from common.i18n import t
from common.online.vpsdb import VPSdb
from tests.support.library import TempTree, game_info, write_game
from tests.support.saved import save_record, saved_record
from tests.support.skips import needs_posix_permissions

KEPT = "Sample Game (Original 2024)"
OLD = "Locked Old (Original 2024)"
NEW = "Locked New (Original 2024)"
LOCKED = (OLD, NEW)
SECOND = "Second Build.vpx"
# Writable, and its .vpx is one the real parser cannot read.
BROKEN = "Broken Table (Original 2024)"
MALFORMED = "Malformed Info (Original 2024)"
READ = VPXParser.single_file_extract


def _legacy(name: str) -> dict:
    return {"Info": {"Title": name.split(" (")[0], "Rom": "sample"},
            "VPXFile": {"filename": f"{name}.vpx", "filehash": "abc", "rom": "sample"}}


def _locked_new() -> dict:
    info = game_info("Locked New", tables={f"{NEW}.vpx": {"filename": f"{NEW}.vpx"}})
    del info["Info"]["VPSId"]
    return info


@needs_posix_permissions
class LockedGameRefreshTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        self.reads: list[str] = []
        games = self.root / "games"
        write_game(games, KEPT, info=_legacy(KEPT))
        write_game(games, OLD, info=_legacy(OLD))
        write_game(games, NEW, info=_locked_new())
        for name in LOCKED:
            folder = games / name
            (folder / SECOND).write_bytes(b"not really a vpx")
            folder.chmod(0o555)
            self.addCleanup(folder.chmod, 0o755)
        self.games = games

        catalog = self.root / "vpsdb.json"
        catalog.write_text(json.dumps([
            {"id": f"vps{index}", "name": name.split(" (")[0], "manufacturer": "Original",
             "year": 2024} for index, name in enumerate((KEPT, OLD, NEW))]),
            encoding="utf-8")
        where = Location(location_id="test", path=str(games), kind=KIND_ROOT)
        for patcher in (
                mock.patch.object(game_repository.library_folders, "configured",
                                  return_value=[where]),
                mock.patch.object(game_service, "VPSDB_JSON_PATH", catalog),
                mock.patch.object(game_service, "_vpsdb_cache", None),
                mock.patch.object(VPSdb, "__init__", side_effect=AssertionError("VPSdb")),
                mock.patch("socket.socket.connect", side_effect=OSError("offline")),
                mock.patch.object(VPXParser, "single_file_extract", self._read),
                mock.patch.dict(unwritten._HELD, clear=True),
                mock.patch.dict(unreadable._FAILED, clear=True)):
            patcher.start()
            self.addCleanup(patcher.stop)
        art = mock.patch("common.games.media_fill.request")
        self.art = art.start()
        self.addCleanup(art.stop)
        self.guessed = mock.patch.object(auto_match, "guess", wraps=auto_match.guess).start()
        self.addCleanup(mock.patch.stopall)
        previous = dict(game_repository._PARSERS)
        game_repository._PARSERS.clear()
        self.addCleanup(game_repository._PARSERS.update, previous)
        self.addCleanup(game_repository._PARSERS.clear)

        self.client = TestClient(httpapi.create_api_app(), raise_server_exceptions=False)

    def _read(self, path: str) -> dict[str, str] | None:
        self.reads.append(Path(path).name)
        if Path(path).parent.name == BROKEN:
            return READ(VPXParser(), path)
        return {"filename": Path(path).name, "filehash": "f00d", "rom": "sample"}

    def _refresh_twice(self) -> tuple[dict, dict, list[logging.LogRecord]]:
        with self.assertLogs("vpinfe", level="WARNING") as logged:
            first = library_refresh.refresh()
            second = library_refresh.refresh()
        return first, second, logged.records

    def _by_folder(self) -> dict[str, list[str]]:
        folders = {game_identity.game_id(game): game.game_dir_name
                   for game in game_repository.all_games()}
        answer = self.client.get("/tables")
        self.assertEqual(answer.status_code, 200, answer.text)
        listed: dict[str, list[str]] = {}
        for row in answer.json()["tables"]:
            listed.setdefault(folders[row["game_id"]], []).append(row["filename"])
        return {name: sorted(files) for name, files in listed.items()}

    def test_every_table_in_a_locked_folder_is_listed(self) -> None:
        self._refresh_twice()

        self.assertEqual(self._by_folder(), {
            KEPT: [f"{KEPT}.vpx"],
            NEW: [f"{NEW}.vpx", SECOND],
            OLD: [f"{OLD}.vpx", SECOND]})

    def _ids(self) -> dict[tuple[str, str], str]:
        return {(row["game_id"], row["filename"]): row["id"]
                for row in self.client.get("/tables").json()["tables"]}

    def test_a_locked_table_answers_to_one_id_across_refreshes(self) -> None:
        library_refresh.refresh()
        first = self._ids()

        library_refresh.refresh()

        self.assertEqual(len(first), 5)
        self.assertEqual(self._ids(), first)

    def test_a_second_refresh_reads_no_table_and_logs_nothing(self) -> None:
        write_game(self.games, BROKEN, info=game_info("Broken Table"))
        (write_game(self.games, MALFORMED) / f"{MALFORMED}.info").write_text(
            '{"Info": {"Title": "broken",,,}', encoding="utf-8")
        library_refresh.refresh()
        first = sorted(self.reads)
        self.reads.clear()

        with self.assertNoLogs("vpinfe", level="INFO"):
            library_refresh.refresh()

        self.assertEqual(first, [f"{BROKEN}.vpx", f"{NEW}.vpx", SECOND, SECOND])
        self.assertEqual(self.reads, [])

    def test_a_table_that_could_not_be_read_is_read_again_once_it_changes(self) -> None:
        folder = write_game(self.games, BROKEN, info=game_info("Broken Table"))
        library_refresh.refresh()
        (folder / f"{BROKEN}.vpx").write_bytes(b"still not a vpx, and longer")
        self.reads.clear()

        with self.assertLogs("vpinfe", level="WARNING") as logged:
            library_refresh.refresh()

        self.assertEqual(self.reads, [f"{BROKEN}.vpx"])
        self.assertEqual(len(logged.records), 1)

    def test_an_info_put_right_is_read_at_the_next_refresh(self) -> None:
        info = write_game(self.games, MALFORMED) / f"{MALFORMED}.info"
        info.write_text('{"Info": {"Title": "broken",,,}', encoding="utf-8")
        library_refresh.refresh()
        save_record(info, game_info("Malformed Info"))

        library_refresh.refresh()

        self.assertIn(MALFORMED, [game.game_dir_name for game in game_repository.all_games()])

    def test_a_table_found_there_is_counted_once(self) -> None:
        first, second, _ = self._refresh_twice()

        self.assertEqual((first["discovered_found"], second["discovered_found"]), (2, 0))

    def test_each_folder_it_could_not_write_is_logged_once_with_why(self) -> None:
        _, _, records = self._refresh_twice()

        self.assertEqual(sorted(record.args for record in records), [
            (name, t("said.why.no_permission_at", path=str(self.games / name)))
            for name in sorted(LOCKED)])

    def test_a_locked_game_is_matched_once_per_run(self) -> None:
        first, second, _ = self._refresh_twice()

        guessed = Counter(call.args[1] for call in self.guessed.call_args_list)
        self.assertEqual({name: guessed[name] for name in LOCKED}, {OLD: 1, NEW: 1})
        self.assertEqual((first["new_games"], second["new_games"]), (3, 0))
        self.assertEqual(second["new_unmatched_ids"], [])

    def test_each_game_it_could_not_write_is_named_with_why(self) -> None:
        library_refresh.refresh()

        info = self.client.get("/library/info").json()

        self.assertEqual(info["unwritten"], [
            {"folder": name, "error": t("said.why.no_permission_at",
                                        path=str(self.games / name))}
            for name in sorted(LOCKED)])

    def _on_disk(self, name: str) -> dict:
        return saved_record(self.games / name / f"{name}.info")

    def test_what_it_held_is_written_once_it_can_be(self) -> None:
        library_refresh.refresh()
        held = self._ids()
        for name in LOCKED:
            (self.games / name).chmod(0o755)
        self.reads.clear()

        library_refresh.refresh()

        self.assertEqual(self._ids(), held)
        written = {(info["vpinfe"]["game_id"], entry["filename"]): entry["id"]
                   for info in map(self._on_disk, (KEPT, *LOCKED))
                   for entry in info["tables"].values()}
        self.assertEqual(written, held)
        self.assertEqual(self.reads, [])
        self.assertEqual(self.client.get("/library/info").json()["unwritten"], [])


@needs_posix_permissions
class LockedUpgradeLogTests(TempTree):
    def setUp(self) -> None:
        super().setUp()
        write_game(self.root, KEPT, info=_legacy(KEPT))
        write_game(self.root, OLD, info=_legacy(OLD))
        (self.root / OLD).chmod(0o555)
        self.addCleanup((self.root / OLD).chmod, 0o755)
        save_record(self.root / KEPT / f"{KEPT}.info", game_info("Sample Game"))

    def _said(self) -> list[str]:
        lines: list[str] = []
        upgrade_library(self.root, log_cb=lines.append)
        return lines

    def test_it_says_which_could_not_be_upgraded_and_why(self) -> None:
        lines = self._said()

        reason = t("error.path.read_only", name=OLD)
        self.assertIn(f"Could not upgrade {OLD}: {reason}", lines)

    def test_a_run_that_upgraded_none_says_so(self) -> None:
        lines = self._said()

        self.assertEqual(lines[-1],
                         "Could not upgrade 1 .info file, and left each as it was.")


if __name__ == "__main__":
    import unittest

    unittest.main()
